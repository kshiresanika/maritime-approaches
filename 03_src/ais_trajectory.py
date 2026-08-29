"""
ais_trajectory.py — what the CLAIMED track DID over time. Lane A owns this file.

WHY THIS FILE IS SEPARATE FROM ais_ingest.py
`ais_ingest` answers "what is being claimed, right now, in this report". This module
answers a different question: "what did the sequence of claims DO" — did it stop, did
it go quiet, how fast was it actually moving according to its own positions. Those
need movingpandas, geopandas and shapely. Keeping them here means a teammate without
the geo stack can still read AIS; folding this into ais_ingest would make the geo
stack a hard dependency of reading a CSV.

LIBRARY-FIRST (03_src/LIBRARIES.md) — NOTHING HERE IS HAND-ROLLED
  * `movingpandas.TrajectoryCollection`  per-vessel trajectories from a flat table
  * `Trajectory.add_speed` / `.add_direction` / `.add_timedelta`  speed profile
  * `movingpandas.TrajectoryStopDetector`  loiter detection
  * `movingpandas.ObservationGapSplitter`  AIS silence periods
  * `geopandas` reprojection + `shapely.distance`  distance to the slice boundary
There is no haversine, no resampling loop and no bearing formula in this file, and
there must never be. Verified against movingpandas 0.23.0 / geopandas 1.1.4 /
shapely 2.1.2 by reading their source before writing against them.

UNITS — the library defaults already match the project conventions, which is why no
conversion appears below. Verified in movingpandas/trajectory.py:
  * `add_speed` on a GEOGRAPHIC CRS (epsg:4326) returns METRES PER SECOND -> `_ms`.
  * `add_direction` returns DEGREES, clockwise from north, [0, 360) -> `_deg_true`.
  * `TrajectoryStopDetector.max_diameter` is in METRES on a geographic CRS — it uses
    geodesic distance via geopy, not degrees. Passing a degree value would silently
    look for stops inside a 500 km box.

--------------------------------------------------------------------------------
WHY LOITER AND GAP ARE NOT FIELDS ON AisTrack

`AisTrack` is a PER-REPORT snapshot: one `report_time_utc`, one position, frozen. A
loiter is a SPAN across hundreds of reports. Stamping "in_loiter" onto each report in
the span repeats a span-shaped fact hundreds of times, and the thing an operator
actually needs — when it started, when it ended, where the centre was, how wide it
was — then exists only implicitly, recoverable by rescanning the track. Criterion 2
has to say "this vessel, because it sat over the cable route for 47 minutes", and
criterion 4 has to be able to quote that span. Neither survives being smeared across
per-report booleans.

So this module emits `BehaviourEvent` (below) — a local dataclass that MIRRORS the
contract type requested from ARCH in 99_scratch/requests.md. Lane A ships now; when
ARCH lands `contracts.BehaviourEvent` the swap is an import change and the field
names already match. Lanes C and D can build against this shape today.

What DOES belong on AisTrack is the per-report derived kinematics — implied speed and
implied course, computed from consecutive claimed positions. Those are one value per
report. They are requested as additive optional fields in the same request.

--------------------------------------------------------------------------------
"IMPLIED" IS A THIRD CATEGORY, AND IT MATTERS FOR CRITERION 3

contracts.py has `claimed_*` (what the transponder asserts) and `observed_*` (what
the camera sees). Speed computed from consecutive claimed POSITIONS is neither: it is
what the claim IMPLIES about itself. Hence the `implied_` prefix, requested rather
than invented on either side of the wall.

This is not pedantry — it is a spoofing detector that needs no camera at all. AIS
broadcasts SOG as its own field (`claimed_sog_kn`), and it separately broadcasts the
positions from which a speed can be computed. **A vessel that reports SOG 0.2 kn
while its own position reports move it 400 metres a minute has contradicted itself.**
That is `SpoofSubtype="kinematic"`, available on recorded data, with no EO frame
required — the cheapest criterion 3 evidence in the whole pipeline. It exists only if
the two speeds are kept in separate fields.

--------------------------------------------------------------------------------
THREE WAYS THIS MODULE CAN LIE, AND WHAT IT DOES ABOUT THEM

1. A MOORED VESSEL IS NOT A LOITERING VESSEL. The Fehmarn slice is full of tugs and
   port tenders — the tunnel construction fleet — that sit still at a berth all day.
   A bare stop detector finds them all and criterion 2's ranked list drowns in
   harbour furniture. Every event therefore carries the claimed navigational statuses
   observed during it, so lane D can discount "Moored" and "At anchor" rather than
   discovering the problem on stage.
2. A GAP IS NOT NECESSARILY SILENCE. This data is a bounding-box cut. A vessel that
   sails out of the box stops appearing while transmitting perfectly. Every gap event
   is therefore checked against the distance from the last-seen position to the slice
   boundary, and flagged `explained_by_area_exit` when it ends at the edge.
3. A GAP IS NOT NECESSARILY A SWITCHED-OFF TRANSPONDER. Receiver geometry produces
   gaps too — Bornholm's tracks are 2.5x sparser than Fehmarn's on the same day with
   the same transponders. Each event carries the vessel's own median reporting
   interval so a gap can be judged against that vessel's normal, not against a global
   constant.

None of these three flags is a verdict. They are the inputs that let lane C decide
whether to defer, and they exist because a patrol boat sent to a moored tug is the
exact failure criterion 2 is there to prevent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Literal, Sequence

import geopandas as gpd
import movingpandas as mpd
import pandas as pd
from shapely.geometry import box

# --------------------------------------------------------------------------------
# Defaults. Every one of these is a THRESHOLD, and a threshold is an argument, not a
# fact — so each is exposed as a parameter and the analysis reports a sensitivity
# sweep rather than a single number. A loiter count quoted without its thresholds is
# not a measurement.
# --------------------------------------------------------------------------------

#: Working CRS. ETRS89 / UTM zone 32N covers Denmark and the Fehmarn Belt, so
#: distances in it are metres with negligible distortion. Web Mercator (3857) would
#: be wrong by ~1.7x at this latitude — it is a display projection, not a metric one.
METRIC_CRS = "EPSG:25832"
GEOGRAPHIC_CRS = "EPSG:4326"

#: A vessel that stays inside a 500 m circle for 30 minutes. 500 m is a few ship
#: lengths — wide enough to absorb GPS scatter and a swinging anchor chain, tight
#: enough that a vessel making way cannot satisfy it.
DEFAULT_LOITER_DIAMETER_M = 500.0
DEFAULT_LOITER_MIN_DURATION = timedelta(minutes=30)

#: 10 minutes. Measured on this data: median inter-report interval is 10 SECONDS and
#: p99 is 235 s, so 10 minutes is roughly 150x the median and ~2.5x p99 — comfortably
#: outside normal jitter, and it is the threshold INVENTORY.md already reports
#: (660 occurrences) so the two documents stay comparable.
DEFAULT_MIN_GAP = timedelta(minutes=10)

#: How close to the slice boundary counts as "left the area". A vessel at 15 kn
#: covers 2 km in about 4 minutes, so a gap starting within 2 km of the edge is far
#: more likely an exit than an evasion.
DEFAULT_EDGE_MARGIN_M = 2000.0

#: A vessel whose own median reporting interval is this many times the area median is
#: in poor reception, not necessarily misbehaving.
DEFAULT_SPARSE_COVERAGE_FACTOR = 3.0

BehaviourKind = Literal["loiter", "ais_gap"]

#: Claimed navigational statuses that EXPLAIN a stop. Not a whitelist — a moored
#: vessel can still be a finding — but lane D must be able to separate "sat still at
#: a berth, as declared" from "sat still over a cable route while claiming to be
#: under way". The latter is the interesting one.
STATIONARY_NAV_STATUSES: frozenset[str] = frozenset({
    "Moored", "At anchor", "Aground", "Not under command",
})


@dataclass(frozen=True)
class BehaviourEvent:
    """
    A span-shaped finding derived from the CLAIMED track. Frozen, like every
    contract type, because a finding that can be edited after the fact is not
    evidence.

    LOCAL MIRROR of the contract type requested from ARCH in
    `99_scratch/requests.md`. Field names are the requested names, so replacing this
    with `from contracts import BehaviourEvent` is a one-line change.

    Nothing here is a verdict. `kind` says what was measured; the `explained_by_*`
    flags say what else could produce the same measurement. Lane C decides.
    """

    event_id: str
    track_id: str
    claimed_mmsi: str
    kind: BehaviourKind

    t_start_utc: datetime
    t_end_utc: datetime
    duration_s: float

    #: Loiter: the stop centroid. Gap: the last position seen before the silence.
    centroid_lat_deg: float
    centroid_lon_deg: float

    #: Loiter only — the radius of the stop, i.e. half the detector's max_diameter.
    radius_m: float | None = None

    #: Gap only — where the track resumed, and how far it jumped while silent.
    resume_lat_deg: float | None = None
    resume_lon_deg: float | None = None
    gap_distance_m: float | None = None
    #: Distance covered over gap duration. NOT a claim and NOT an observation: it is
    #: the minimum speed the vessel must have held to be in both places. A value far
    #: above the vessel's declared capability is a kinematic finding on its own.
    implied_gap_speed_ms: float | None = None

    #: How far the event start was from the edge of the analysed area, in metres.
    distance_to_area_edge_m: float | None = None
    explained_by_area_exit: bool = False
    explained_by_sparse_coverage: bool = False

    #: Claimed navigational statuses seen during the event, most frequent first.
    #: See STATIONARY_NAV_STATUSES — this is how lane D discounts moored harbour craft.
    claimed_nav_statuses: tuple[str, ...] = ()
    claimed_ship_type: str | None = None

    #: The thresholds this event was detected under. An event without its detector
    #: parameters is unreproducible, exactly as a Verdict is without its ruleset
    #: version.
    detector: dict[str, Any] = field(default_factory=dict)

    @property
    def is_explained(self) -> bool:
        """True when a benign cause is at least as likely as a finding."""
        return self.explained_by_area_exit or self.explained_by_sparse_coverage


# --------------------------------------------------------------------------------
# Input adapters. Two, for a memory reason worth stating.
#
# `from_tracks` is the contract-correct entry point: it takes AisTrack objects, which
# is what crosses a module boundary. `from_frames` takes the cleaned pandas frames
# that DmaCsvSource.frames() already produces.
#
# WHY BOTH: a full day of the Fehmarn slice is 431,751 reports. As pydantic model
# instances that is hundreds of megabytes of Python objects on a laptop that is also
# running a YOLO model and a demo. The frame path never materialises them. Use
# `from_tracks` for tests, small windows and anything crossing a lane boundary; use
# `from_frames` for whole-day analysis.
# --------------------------------------------------------------------------------

#: The flat table both adapters produce. Deliberately small — everything movingpandas
#: needs, plus exactly the three claim fields the explanation flags depend on.
POINT_COLUMNS = ("track_id", "claimed_mmsi", "t", "lat", "lon",
                 "claimed_sog_kn", "claimed_nav_status", "claimed_ship_type")


def from_tracks(tracks: Iterable[Any]) -> pd.DataFrame:
    """`Iterable[contracts.AisTrack]` -> the flat point table."""
    rows = [
        {
            "track_id": t.track_id,
            "claimed_mmsi": t.claimed_mmsi,
            "t": t.report_time_utc,
            "lat": t.claimed_lat_deg,
            "lon": t.claimed_lon_deg,
            "claimed_sog_kn": t.claimed_sog_kn,
            "claimed_nav_status": t.claimed_nav_status,
            "claimed_ship_type": t.claimed_ship_type,
        }
        for t in tracks
    ]
    return pd.DataFrame(rows, columns=list(POINT_COLUMNS))


def from_frames(frames: Iterable[pd.DataFrame], source_id: str) -> pd.DataFrame:
    """
    Cleaned `DmaCsvSource.frames()` output -> the flat point table.

    `source_id` must be the SAME value the ingest used, because `track_id` is derived
    from (source_id, MMSI). A different value here produces track ids that do not
    match the AisTrack objects lane C is holding, and the two would never join — with
    no error, just an empty association.
    """
    from ais_ingest import make_track_id, normalise_mmsi  # lane A's own module

    parts = []
    for frame in frames:
        mmsi = frame["MMSI"].map(normalise_mmsi)
        parts.append(pd.DataFrame({
            "track_id": mmsi.map(lambda m: make_track_id(source_id, m) if m else None),
            "claimed_mmsi": mmsi,
            "t": frame["report_time_utc"],
            "lat": pd.to_numeric(frame["Latitude"], errors="coerce"),
            "lon": pd.to_numeric(frame["Longitude"], errors="coerce"),
            "claimed_sog_kn": pd.to_numeric(frame.get("SOG"), errors="coerce"),
            "claimed_nav_status": frame.get("Navigational status"),
            "claimed_ship_type": frame.get("Ship type"),
        }))
    if not parts:
        return pd.DataFrame(columns=list(POINT_COLUMNS))
    points = pd.concat(parts, ignore_index=True)
    return points.dropna(subset=["track_id", "t", "lat", "lon"])


# --------------------------------------------------------------------------------
# Trajectory construction.
# --------------------------------------------------------------------------------

def build_trajectories(
    points: pd.DataFrame,
    *,
    min_points: int = 2,
    min_duration: timedelta | None = None,
) -> mpd.TrajectoryCollection:
    """
    Flat point table -> one movingpandas Trajectory per vessel.

    THE min_points=2 FLOOR IS NOT COSMETIC. `TrajectoryCollection.__iter__` RAISES
    ValueError on any trajectory with fewer than two points (verified in
    movingpandas/trajectory_collection.py). The Fehmarn slice contains vessels with a
    single report — INVENTORY.md §4.3 measures the minimum at 1 — so without this
    filter the whole analysis dies partway through iteration, on real data, with an
    exception that names movingpandas rather than the vessel that caused it.

    Points are sorted by (track_id, t) first. movingpandas computes speed and
    direction between CONSECUTIVE ROWS, so out-of-order input silently produces
    negative time deltas and nonsense speeds rather than an error. The DMA CSV
    happens to arrive time-ordered; that is not something to depend on.
    """
    if points.empty:
        return mpd.TrajectoryCollection([])

    points = points.sort_values(["track_id", "t"]).reset_index(drop=True)
    counts = points.groupby("track_id")["t"].transform("size")
    points = points[counts >= min_points]
    if points.empty:
        return mpd.TrajectoryCollection([])

    return mpd.TrajectoryCollection(
        points,
        traj_id_col="track_id",
        t="t",
        x="lon",
        y="lat",
        crs=GEOGRAPHIC_CRS,
        min_duration=min_duration,
    )


def add_kinematics(collection: mpd.TrajectoryCollection) -> mpd.TrajectoryCollection:
    """
    Add the speed profile: implied speed, implied course and the inter-report delta.

    NO UNIT ARGUMENTS ARE PASSED, deliberately. On a geographic CRS movingpandas
    returns metres per second and degrees-true-clockwise-from-north by default, which
    are already the project conventions (`_ms`, `_deg_true`). Passing a units tuple
    would be a second place for a unit to be wrong. The test suite asserts the
    defaults rather than trusting this comment.

    The `implied_` prefix is load-bearing: these are computed from consecutive
    CLAIMED POSITIONS, so they are neither a claim nor an observation. Comparing
    `implied_speed_ms` against `claimed_sog_kn` is a self-contradiction test on the
    AIS message alone — criterion 3 evidence with no camera involved.
    """
    collection.add_speed(overwrite=True, name="implied_speed_ms")
    collection.add_direction(overwrite=True, name="implied_course_deg_true")
    collection.add_timedelta(overwrite=True, name="report_delta")
    return collection


def implied_kinematics_table(collection: mpd.TrajectoryCollection) -> pd.DataFrame:
    """
    Per-report implied kinematics, keyed by (track_id, report_time_utc).

    This is the table that becomes the requested additive AisTrack fields
    (`implied_speed_ms`, `implied_course_deg_true`, `gap_before_s`) once ARCH lands
    them. Until then lane C can join it onto AisTrack on the same key.

    Built by iterating trajectories rather than `to_point_gdf()` so the trajectory id
    is attached explicitly and cannot be lost to a column-naming change upstream.
    """
    rows = []
    for traj in collection.trajectories:
        frame = traj.df
        rows.append(pd.DataFrame({
            "track_id": traj.id,
            "report_time_utc": frame.index,
            "implied_speed_ms": frame.get("implied_speed_ms"),
            "implied_course_deg_true": frame.get("implied_course_deg_true"),
            "gap_before_s": frame["report_delta"].dt.total_seconds()
            if "report_delta" in frame else None,
        }))
    if not rows:
        return pd.DataFrame(columns=["track_id", "report_time_utc", "implied_speed_ms",
                                     "implied_course_deg_true", "gap_before_s"])
    return pd.concat(rows, ignore_index=True)


# --------------------------------------------------------------------------------
# Distance to the edge of the analysed area — the "did it just leave the box" test.
# --------------------------------------------------------------------------------

def distance_to_area_edge_m(
    lats: Sequence[float], lons: Sequence[float],
    bbox: tuple[float, float, float, float],
) -> pd.Series:
    """
    Metres from each point to the boundary of the analysed bounding box.

    `bbox` is (lat_min, lat_max, lon_min, lon_max) — the same convention as
    `ais_ingest.filter_frame` and `subset_ais.py`, deliberately, so the three cannot
    disagree about which corner is which.

    WHY REPROJECT rather than work in degrees: one degree of longitude is ~64 km at
    54.6 N and one degree of latitude is ~111 km, so a "0.02 degree" margin means two
    different distances depending on which edge is nearest. ETRS89 / UTM 32N is the
    national grid for this water and its units are metres. Web Mercator would be
    wrong by a factor of ~1.7 at this latitude — it is a display projection.

    geopandas does the reprojection and shapely does the distance. Neither is
    hand-rolled here.
    """
    lat_min, lat_max, lon_min, lon_max = bbox
    boundary = (
        gpd.GeoSeries([box(lon_min, lat_min, lon_max, lat_max)], crs=GEOGRAPHIC_CRS)
        .to_crs(METRIC_CRS).iloc[0].exterior
    )
    points = gpd.GeoSeries(
        gpd.points_from_xy(lons, lats), crs=GEOGRAPHIC_CRS).to_crs(METRIC_CRS)
    return points.distance(boundary)


def pairwise_distance_m(
    lats_a: Sequence[float], lons_a: Sequence[float],
    lats_b: Sequence[float], lons_b: Sequence[float],
) -> pd.Series:
    """
    Straight-line metres between paired points. Vectorised: ONE reprojection for the
    whole batch, not one per pair.

    geopandas reprojects to ETRS89 / UTM 32N and shapely measures. `align=False` is
    required — with the default, geopandas aligns the two GeoSeries on their INDEX
    rather than pairing them positionally, and a mismatched index silently yields NaN
    instead of an error.
    """
    a = gpd.GeoSeries(gpd.points_from_xy(lons_a, lats_a),
                      crs=GEOGRAPHIC_CRS).to_crs(METRIC_CRS).reset_index(drop=True)
    b = gpd.GeoSeries(gpd.points_from_xy(lons_b, lats_b),
                      crs=GEOGRAPHIC_CRS).to_crs(METRIC_CRS).reset_index(drop=True)
    return a.distance(b, align=False)


def _event_id(kind: str, track_id: str, start: datetime) -> str:
    """Deterministic id, so a re-run produces the same event ids as the evidence
    record written against the previous run."""
    digest = hashlib.sha1(f"{kind}|{track_id}|{start.isoformat()}".encode()).hexdigest()
    return f"{'LTR' if kind == 'loiter' else 'GAP'}-{digest[:8]}"


def _claims_during(points: pd.DataFrame, track_id: str,
                   start: datetime, end: datetime) -> tuple[tuple[str, ...], str | None]:
    """Navigational statuses and ship type claimed during a span, most frequent first."""
    window = points[(points["track_id"] == track_id)
                    & (points["t"] >= start) & (points["t"] <= end)]
    if window.empty:
        return (), None
    statuses = tuple(
        str(s) for s in window["claimed_nav_status"].dropna().value_counts().index)
    types = window["claimed_ship_type"].dropna()
    return statuses, (str(types.mode().iloc[0]) if not types.empty else None)


# --------------------------------------------------------------------------------
# Loiter detection.
# --------------------------------------------------------------------------------

def detect_loiter(
    collection: mpd.TrajectoryCollection,
    points: pd.DataFrame,
    *,
    max_diameter_m: float = DEFAULT_LOITER_DIAMETER_M,
    min_duration: timedelta = DEFAULT_LOITER_MIN_DURATION,
    bbox: tuple[float, float, float, float] | None = None,
    n_processes: int = 1,
) -> list[BehaviourEvent]:
    """
    Vessels that stayed inside `max_diameter_m` for at least `min_duration`.

    `movingpandas.TrajectoryStopDetector` does the detection. On a geographic CRS its
    `max_diameter` is METRES, computed geodesically via geopy — verified in
    movingpandas/trajectory_stop_detector.py. Passing degrees here would search for
    stops inside a box hundreds of kilometres wide and return almost every vessel.

    THE COUNT THIS RETURNS IS NOT A LIST OF SUSPECTS. The Fehmarn Belt in August 2026
    is a construction site: tugs, port tenders and dredgers sit still for hours as
    their job. Every event therefore carries `claimed_nav_statuses`, and lane D is
    expected to separate a vessel stopped while declaring "Moored" from one stopped
    while declaring "Under way using engine". The second is the finding; the first is
    a berth.
    """
    if len(collection) == 0:
        return []

    detector = mpd.TrajectoryStopDetector(collection, n_processes=n_processes)
    stops = detector.get_stop_points(
        max_diameter=max_diameter_m, min_duration=min_duration)
    if stops.empty:
        return []

    edge = None
    if bbox is not None:
        edge = distance_to_area_edge_m(
            stops.geometry.y.tolist(), stops.geometry.x.tolist(), bbox).tolist()

    mmsi_by_track = dict(zip(points["track_id"], points["claimed_mmsi"]))
    events: list[BehaviourEvent] = []
    for i, (_, stop) in enumerate(stops.iterrows()):
        track_id = str(stop["traj_id"])
        start, end = stop["start_time"], stop["end_time"]
        statuses, ship_type = _claims_during(points, track_id, start, end)
        events.append(BehaviourEvent(
            event_id=_event_id("loiter", track_id, start),
            track_id=track_id,
            claimed_mmsi=str(mmsi_by_track.get(track_id, "")),
            kind="loiter",
            t_start_utc=start,
            t_end_utc=end,
            duration_s=float(stop["duration_s"]),
            centroid_lat_deg=float(stop.geometry.y),
            centroid_lon_deg=float(stop.geometry.x),
            radius_m=max_diameter_m / 2.0,
            distance_to_area_edge_m=float(edge[i]) if edge is not None else None,
            claimed_nav_statuses=statuses,
            claimed_ship_type=ship_type,
            detector={"library": f"movingpandas {mpd.__version__}",
                      "detector": "TrajectoryStopDetector.get_stop_points",
                      "max_diameter_m": max_diameter_m,
                      "min_duration_s": min_duration.total_seconds()},
        ))
    return events


# --------------------------------------------------------------------------------
# AIS gap detection.
# --------------------------------------------------------------------------------

def split_on_gaps(collection: mpd.TrajectoryCollection,
                  min_gap: timedelta = DEFAULT_MIN_GAP) -> mpd.TrajectoryCollection:
    """
    Continuous track segments, split wherever the vessel went silent.

    Provided for lane C, which should associate against a CONTINUOUS segment rather
    than a track with an hour-long hole in the middle. Read the warning in
    `detect_gaps` before using the segment count to count gaps.
    """
    return mpd.ObservationGapSplitter(collection).split(gap=min_gap)


def detect_gaps(
    collection: mpd.TrajectoryCollection,
    points: pd.DataFrame,
    *,
    min_gap: timedelta = DEFAULT_MIN_GAP,
    bbox: tuple[float, float, float, float] | None = None,
    edge_margin_m: float = DEFAULT_EDGE_MARGIN_M,
    sparse_coverage_factor: float = DEFAULT_SPARSE_COVERAGE_FACTOR,
) -> list[BehaviourEvent]:
    """
    Periods of AIS silence longer than `min_gap`.

    WHY THIS READS THE TIMEDELTA COLUMN RATHER THAN COUNTING SPLITTER SEGMENTS.
    `ObservationGapSplitter._split_traj` discards any resulting segment with fewer
    than two points (`if len(df) > 1`). So a lone report sitting between two silences
    is dropped, and the two gaps on either side of it silently merge into one. The
    segment count would then UNDER-report gaps, by an amount that depends on how
    sparse the vessel is — worst exactly where coverage is worst, which is the case
    criterion 4 cares about most. The timedelta column loses nothing, and it is the
    same arithmetic the splitter itself performs (`t.diff() > gap`), computed by
    `Trajectory.add_timedelta`, not by hand. `split_on_gaps()` above still exposes the
    segments for lane C's use.

    EVERY EVENT CARRIES ITS ALTERNATIVE EXPLANATIONS. INVENTORY.md §4.2: three
    different things produce a gap and this file cannot tell them apart —
      * the vessel sailed out of the box, still transmitting -> `explained_by_area_exit`
      * the receiver missed it -> `explained_by_sparse_coverage`
      * the transponder was switched off -> the only actual finding.
    A gap with neither flag set is still not a verdict. It is a candidate.
    """
    if len(collection) == 0:
        return []

    kinematics = implied_kinematics_table(collection)
    gaps = kinematics[kinematics["gap_before_s"] > min_gap.total_seconds()].copy()
    if gaps.empty:
        return []

    # Each vessel judged against ITS OWN normal reporting interval, not a global
    # constant — a vessel in poor reception is sparse everywhere, and calling that
    # evasion is the Bornholm false-positive in miniature.
    per_track_median = kinematics.groupby("track_id")["gap_before_s"].median()
    area_median = float(kinematics["gap_before_s"].median())

    ordered = points.sort_values(["track_id", "t"])
    by_track = {tid: g.reset_index(drop=True) for tid, g in ordered.groupby("track_id")}

    # PASS 1 — locate each gap's bracketing reports. No geometry yet.
    bracketed: list[dict[str, Any]] = []
    for _, row in gaps.iterrows():
        track_id = str(row["track_id"])
        resume_t = row["report_time_utc"]
        duration = float(row["gap_before_s"])
        start_t = resume_t - timedelta(seconds=duration)

        track_points = by_track.get(track_id)
        if track_points is None or track_points.empty:
            continue
        before = track_points[track_points["t"] <= start_t]
        after = track_points[track_points["t"] >= resume_t]
        if before.empty or after.empty:
            continue
        last, first = before.iloc[-1], after.iloc[0]
        bracketed.append({
            "track_id": track_id, "claimed_mmsi": str(last["claimed_mmsi"]),
            "start_t": start_t, "resume_t": resume_t, "duration_s": duration,
            "last_lat": float(last["lat"]), "last_lon": float(last["lon"]),
            "first_lat": float(first["lat"]), "first_lon": float(first["lon"]),
        })
    if not bracketed:
        return []

    # PASS 2 — all the geometry in two vectorised calls, not two per event. On the
    # Fehmarn day this is ~660 events; per-event reprojection would do 1,320
    # single-point CRS transforms for no reason.
    table = pd.DataFrame(bracketed)
    jump_m = pairwise_distance_m(
        table["last_lat"], table["last_lon"],
        table["first_lat"], table["first_lon"]).tolist()
    edge_m = (
        distance_to_area_edge_m(table["last_lat"], table["last_lon"], bbox).tolist()
        if bbox is not None else [None] * len(table)
    )

    track_median = per_track_median.to_dict()

    events: list[BehaviourEvent] = []
    for i, row in enumerate(table.to_dict("records")):
        track_id = row["track_id"]
        duration = row["duration_s"]
        statuses, ship_type = _claims_during(
            points, track_id, row["start_t"], row["resume_t"])
        this_edge = None if edge_m[i] is None else float(edge_m[i])
        median_here = float(track_median.get(track_id, area_median))
        events.append(BehaviourEvent(
            event_id=_event_id("ais_gap", track_id, row["start_t"]),
            track_id=track_id,
            claimed_mmsi=row["claimed_mmsi"],
            kind="ais_gap",
            t_start_utc=row["start_t"],
            t_end_utc=row["resume_t"],
            duration_s=duration,
            centroid_lat_deg=row["last_lat"],
            centroid_lon_deg=row["last_lon"],
            resume_lat_deg=row["first_lat"],
            resume_lon_deg=row["first_lon"],
            gap_distance_m=float(jump_m[i]),
            # Minimum speed the vessel must have held to be in both places. Neither a
            # claim nor an observation — what the claimed positions IMPLY. A value
            # above any plausible hull speed is a kinematic finding by itself.
            implied_gap_speed_ms=(float(jump_m[i]) / duration) if duration > 0 else None,
            distance_to_area_edge_m=this_edge,
            explained_by_area_exit=(
                this_edge is not None and this_edge <= edge_margin_m),
            explained_by_sparse_coverage=(
                median_here > sparse_coverage_factor * area_median),
            claimed_nav_statuses=statuses,
            claimed_ship_type=ship_type,
            detector={"library": f"movingpandas {mpd.__version__}",
                      "detector": "Trajectory.add_timedelta threshold",
                      "min_gap_s": min_gap.total_seconds(),
                      "edge_margin_m": edge_margin_m,
                      "sparse_coverage_factor": sparse_coverage_factor,
                      "area_median_interval_s": area_median},
        ))
    return events


# --------------------------------------------------------------------------------
# The whole analysis, and the sensitivity sweep that has to accompany it.
# --------------------------------------------------------------------------------

@dataclass
class TrajectoryAnalysis:
    """Everything one pass produces. Not a contract type — it never crosses a lane
    boundary; `BehaviourEvent` and the kinematics table are what lanes C and D take."""

    collection: mpd.TrajectoryCollection
    kinematics: pd.DataFrame
    loiter_events: list[BehaviourEvent]
    gap_events: list[BehaviourEvent]
    n_points: int
    n_vessels: int
    n_dropped_single_point: int

    def summary(self) -> str:
        unexplained_gaps = [g for g in self.gap_events if not g.is_explained]
        moving_loiters = [
            e for e in self.loiter_events
            if not (set(e.claimed_nav_statuses) & STATIONARY_NAV_STATUSES)
        ]
        return "\n".join([
            f"points analysed        : {self.n_points:,}",
            f"vessels                : {self.n_vessels:,}",
            f"dropped, <2 reports    : {self.n_dropped_single_point:,}",
            f"trajectories built     : {len(self.collection):,}",
            f"LOITER events          : {len(self.loiter_events):,}",
            f"  not declaring moored/anchored : {len(moving_loiters):,}",
            f"AIS GAP events         : {len(self.gap_events):,}",
            f"  explained by area exit       : "
            f"{sum(1 for g in self.gap_events if g.explained_by_area_exit):,}",
            f"  explained by sparse coverage : "
            f"{sum(1 for g in self.gap_events if g.explained_by_sparse_coverage):,}",
            f"  UNEXPLAINED (candidates)     : {len(unexplained_gaps):,}",
        ])


def analyse(
    points: pd.DataFrame,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    loiter_diameter_m: float = DEFAULT_LOITER_DIAMETER_M,
    loiter_min_duration: timedelta = DEFAULT_LOITER_MIN_DURATION,
    min_gap: timedelta = DEFAULT_MIN_GAP,
    n_processes: int = 1,
) -> TrajectoryAnalysis:
    """One pass: trajectories, speed profile, loiter events, gap events."""
    n_points = len(points)
    n_vessels = points["track_id"].nunique() if n_points else 0

    collection = build_trajectories(points)
    n_dropped = n_vessels - len(collection)

    add_kinematics(collection)
    return TrajectoryAnalysis(
        collection=collection,
        kinematics=implied_kinematics_table(collection),
        loiter_events=detect_loiter(
            collection, points, max_diameter_m=loiter_diameter_m,
            min_duration=loiter_min_duration, bbox=bbox, n_processes=n_processes),
        gap_events=detect_gaps(collection, points, min_gap=min_gap, bbox=bbox),
        n_points=n_points,
        n_vessels=n_vessels,
        n_dropped_single_point=n_dropped,
    )


def gap_sensitivity(collection: mpd.TrajectoryCollection,
                    thresholds: Sequence[timedelta]) -> pd.DataFrame:
    """
    Gap counts across several thresholds.

    WHY THIS EXISTS: "we found 660 AIS gaps" is not a measurement, it is a
    measurement plus a hidden threshold. A judge is entitled to ask what happens at
    five minutes instead of ten, and the answer must already be on the slide. It also
    keeps anyone from tuning the threshold until the number looks impressive.
    """
    kinematics = implied_kinematics_table(collection)
    seconds = kinematics["gap_before_s"]
    return pd.DataFrame([
        {"threshold": str(t),
         "gaps": int((seconds > t.total_seconds()).sum()),
         "vessels": int(kinematics.loc[
             seconds > t.total_seconds(), "track_id"].nunique())}
        for t in thresholds
    ])


def loiter_sensitivity(
    collection: mpd.TrajectoryCollection, points: pd.DataFrame,
    diameters_m: Sequence[float], durations: Sequence[timedelta],
    *, bbox: tuple[float, float, float, float] | None = None, n_processes: int = 1,
) -> pd.DataFrame:
    """Loiter counts across a threshold grid. Same reasoning as `gap_sensitivity`."""
    rows = []
    for diameter in diameters_m:
        for duration in durations:
            events = detect_loiter(
                collection, points, max_diameter_m=diameter, min_duration=duration,
                bbox=bbox, n_processes=n_processes)
            moving = [e for e in events
                      if not (set(e.claimed_nav_statuses) & STATIONARY_NAV_STATUSES)]
            rows.append({
                "diameter_m": diameter,
                "min_duration": str(duration),
                "loiter_events": len(events),
                "not_moored": len(moving),
                "vessels": len({e.track_id for e in events}),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------
# Entry point. This is a real report generator, not a smoke test — the numbers it
# prints are the ones that go into INVENTORY.md and onto a slide, which is why it
# prints its thresholds beside every count.
#
#   python3 03_src/ais_trajectory.py 02_data/slices/aisdk-2026-08-25_fehmarn_belt.csv
# --------------------------------------------------------------------------------

FEHMARN_BBOX = (54.40, 54.80, 11.00, 11.80)   # lat_min, lat_max, lon_min, lon_max


def main(csv_path: str, bbox: tuple[float, float, float, float] = FEHMARN_BBOX) -> None:
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from ais_ingest import DmaCsvSource

    source = DmaCsvSource(csv_path)
    points = from_frames(source.frames(), source.source_id)
    print(source.stats.summary())
    print()

    result = analyse(points, bbox=bbox)
    print(result.summary())

    print("\n--- GAP SENSITIVITY ---")
    print(gap_sensitivity(result.collection, [
        timedelta(minutes=m) for m in (5, 10, 15, 30, 60)]).to_string(index=False))

    print("\n--- LOITER SENSITIVITY ---")
    print(loiter_sensitivity(
        result.collection, points,
        diameters_m=(250.0, 500.0, 1000.0),
        durations=[timedelta(minutes=m) for m in (15, 30, 60)],
        bbox=bbox).to_string(index=False))

    print("\n--- LOITER EVENTS BY CLAIMED NAV STATUS (at the defaults) ---")
    tally: dict[str, int] = {}
    for event in result.loiter_events:
        key = event.claimed_nav_statuses[0] if event.claimed_nav_statuses else "(none)"
        tally[key] = tally.get(key, 0) + 1
    for status, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        moored = " <- declared stationary" if status in STATIONARY_NAV_STATUSES else ""
        print(f"  {count:6,}  {status}{moored}")

    print("\n--- SELF-CONTRADICTION CHECK (criterion 3, no camera needed) ---")
    joined = result.kinematics.dropna(subset=["implied_speed_ms"])
    claimed = points[["track_id", "t", "claimed_sog_kn"]].rename(
        columns={"t": "report_time_utc"})
    joined = joined.merge(claimed, on=["track_id", "report_time_utc"], how="inner")
    joined["claimed_ms"] = joined["claimed_sog_kn"] * 0.514444
    contradiction = joined[(joined["claimed_ms"] < 0.5)
                           & (joined["implied_speed_ms"] > 2.0)]
    print(f"  reports claiming SOG < 1 kn while their own positions imply > 2 m/s: "
          f"{len(contradiction):,} across {contradiction['track_id'].nunique():,} vessels")
    print("  NOT a verdict — GPS jitter and duplicate-position artefacts land here too.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
