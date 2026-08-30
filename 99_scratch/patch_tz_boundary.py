#!/usr/bin/env python3
"""
patch_tz_boundary.py — restore UTC awareness at the movingpandas boundary.

THE DEFECT
movingpandas applies `df.tz_localize(None)` to every trajectory it builds
(movingpandas/trajectory.py:162, emitting TimeZoneWarning). Every timestamp that
comes back OUT of a TrajectoryCollection is therefore tz-NAIVE, while the flat
point table this module builds trajectories FROM keeps its tz-aware
datetime64[ns, UTC] column. Comparing the two raises:

    TypeError: Invalid comparison between dtype=datetime64[ns, UTC] and Timestamp

...at ais_trajectory.py:436 (_claims_during) and :589 (detect_gaps). Nine tests.

WHY FIX THE BOUNDARY, NOT THE TWO COMPARISONS
Patching the two `>=` sites would silence the error and leave the real problem in
place: BehaviourEvent.t_start_utc / t_end_utc are named _utc and would still carry
NAIVE timestamps, and _event_id() hashes start.isoformat() — so an event id would
depend on whether the timestamp happened to have travelled through movingpandas.
The module's contract is UTC in, UTC out. Enforce it at every crossing instead.

`tz_localize(None)` on a UTC-aware value keeps the UTC WALL CLOCK and drops the
label, so re-localising to UTC is its exact inverse — no time is shifted. Naive
input is assumed UTC, which is what POINT_COLUMNS["t"] already promises (it is
built from AisTrack.report_time_utc).

CROSSINGS PATCHED
  1. build_trajectories      — coerce points["t"] to UTC on the way IN
  2. implied_kinematics_table— re-localise frame.index on the way OUT
  3. detect_loiter           — re-localise stop start_time / end_time on the way OUT
  4. _claims_during          — coerce both sides before comparing
  5. detect_gaps             — coerce both sides before comparing
"""
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "03_src" / "ais_trajectory.py"
text = SRC.read_text()
orig = text

def sub(old, new, label):
    global text
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"[FAIL] {label}: expected 1 occurrence, found {n}")
    text = text.replace(old, new)
    print(f"[ok] {label}")

# ---------------------------------------------------------------- 0. the helper
HELPER = '''
# --------------------------------------------------------------------------------
# The movingpandas timezone boundary.
# --------------------------------------------------------------------------------

def _utc(value):
    """
    Force a timestamp, datetime or Series onto tz-aware UTC.

    WHY THIS EXISTS. movingpandas strips the timezone from every trajectory it
    builds — `df.tz_localize(None)`, movingpandas/trajectory.py, which raises
    TimeZoneWarning as it does so. So a timestamp read back out of a
    TrajectoryCollection is NAIVE, while the flat point table it was built from is
    tz-aware datetime64[ns, UTC]. pandas refuses to compare the two:

        TypeError: Invalid comparison between dtype=datetime64[ns, UTC] and Timestamp

    `tz_localize(None)` on a UTC value keeps the UTC wall clock and discards only
    the label, so re-localising to UTC here is its exact inverse — nothing is
    shifted by an offset. A naive value from anywhere else is assumed UTC, which is
    what this module's `t` column already promises: it is populated from
    `AisTrack.report_time_utc`.

    Applied at every crossing rather than at the comparison sites, so that
    `BehaviourEvent.t_start_utc` is genuinely UTC as its name claims, and so that
    `_event_id` — which hashes `start.isoformat()` — cannot produce two different
    ids for one event depending on whether the timestamp travelled through
    movingpandas on the way.
    """
    if isinstance(value, pd.Series):
        out = pd.to_datetime(value, utc=True)
        return out
    if isinstance(value, pd.DatetimeIndex):
        return value.tz_localize("UTC") if value.tz is None else value.tz_convert("UTC")
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")


'''

sub(
    "def _event_id(kind: str, track_id: str, start: datetime) -> str:",
    HELPER.lstrip("\n") + "def _event_id(kind: str, track_id: str, start: datetime) -> str:",
    "0. insert _utc() helper",
)

# ------------------------------------------------- 1. build_trajectories, on the way in
sub(
    '''    points = points.sort_values(["track_id", "t"]).reset_index(drop=True)
    counts = points.groupby("track_id")["t"].transform("size")''',
    '''    points = points.sort_values(["track_id", "t"]).reset_index(drop=True)
    # UTC in. movingpandas will strip the zone regardless; coercing here means the
    # zone it strips is known, so `_utc()` can restore it exactly on the way out.
    points["t"] = _utc(points["t"])
    counts = points.groupby("track_id")["t"].transform("size")''',
    "1. build_trajectories coerces t to UTC",
)

# -------------------------------------------- 2. implied_kinematics_table, on the way out
sub(
    '''            "report_time_utc": frame.index,''',
    '''            # Re-labelled UTC: movingpandas dropped the zone when it built the
            # trajectory, and this column's name promises it back.
            "report_time_utc": _utc(pd.DatetimeIndex(frame.index)),''',
    "2. implied_kinematics_table restores UTC",
)

# ------------------------------------------------------ 3. detect_loiter, on the way out
sub(
    '''        start, end = stop["start_time"], stop["end_time"]''',
    '''        # TrajectoryStopDetector reads the trajectory frame, so these come back
        # naive. t_start_utc must not be a lie, and _event_id hashes it.
        start, end = _utc(stop["start_time"]), _utc(stop["end_time"])''',
    "3. detect_loiter restores UTC",
)

# ----------------------------------------------------------- 4. _claims_during comparison
sub(
    '''    window = points[(points["track_id"] == track_id)
                    & (points["t"] >= start) & (points["t"] <= end)]''',
    '''    # Both sides onto UTC before comparing. `points` is the caller's frame and
    # `start`/`end` came out of movingpandas, so their awareness is not guaranteed
    # to agree even after the crossings above.
    t = _utc(points["t"])
    start, end = _utc(start), _utc(end)
    window = points[(points["track_id"] == track_id)
                    & (t >= start) & (t <= end)]''',
    "4. _claims_during compares in UTC",
)

# -------------------------------------------------------------- 5. detect_gaps comparison
sub(
    '''    ordered = points.sort_values(["track_id", "t"])
    by_track = {tid: g.reset_index(drop=True) for tid, g in ordered.groupby("track_id")}''',
    '''    ordered = points.sort_values(["track_id", "t"]).copy()
    ordered["t"] = _utc(ordered["t"])   # compared below against trajectory-derived times
    by_track = {tid: g.reset_index(drop=True) for tid, g in ordered.groupby("track_id")}''',
    "5a. detect_gaps normalises the point table",
)

sub(
    '''        resume_t = row["report_time_utc"]
        duration = float(row["gap_before_s"])
        start_t = resume_t - timedelta(seconds=duration)''',
    '''        resume_t = _utc(row["report_time_utc"])
        duration = float(row["gap_before_s"])
        start_t = resume_t - timedelta(seconds=duration)''',
    "5b. detect_gaps normalises the gap's resume time",
)

if text == orig:
    raise SystemExit("[FAIL] nothing changed")
SRC.write_text(text)
print(f"\nwrote {SRC}")
