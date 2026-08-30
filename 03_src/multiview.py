"""
03_src/multiview.py — WHICH CONTACT IN CAMERA B IS THE ONE CAMERA A IS LOOKING AT?

Lane G. Sits between `contracts` and `triangulate` and owns exactly one question: the
CORRESPONDENCE problem across cameras. triangulate.py knows how to turn N bearings on
ONE hull into a fix; it has no idea which bearings belong to the same hull. That is this
file, and nothing else is.

    triangulate.py   : bearings -> position          (pure geometry, no scene)
    multiview.py     : contacts -> which go together (this file)
    server.py        : publishes the result          (transport)

WHY THE CORRESPONDENCE IS THE HARD HALF, AND WHY IT IS SOLVED THIS WAY
A camera cannot see identity. Camera A's `c-0004` and camera B's `c-0011` carry no field
that says they are the same vessel — if they did, we would already have the answer we are
trying to compute. So the pairing has to come from GEOMETRY ALONE, and it has to be able
to say "none of these match".

The evidence available for a candidate pair (a, b) is:
  1. Do the two bearing rays cross at all, in front of both cameras, at a workable angle?
     triangulate() answers this itself and refuses when they do not — a fix behind a
     camera, a crossing under ~8 degrees, or a bearing sigma too wide to bound anything.
  2. Does the crossing point agree with what EACH camera independently thought the range
     was? This is the only INDEPENDENT check available with exactly two stations — with
     three, the residual does it — and it is what `TriangulatedFix.range_crosscheck`
     exists to report: the fix range minus the station's own monocular range, in units of
     that station's own range sigma.

Monocular range is bad (lane C: ~1250 m along-range sigma at 5 km), so the gate is
deliberately LOOSE — three sigma of a bad measurement. It is not there to confirm the
pair; it is there to throw out the pair whose rays happen to cross ten kilometres from
where both cameras agree the vessel is. A tight gate here would silently reject correct
pairs and the loss would look like "the second camera does not help".

ONE-TO-ONE, AND SOLVED RATHER THAN GREEDILY TAKEN
A hull is one hull: a contact in A may pair with at most one contact in B. Greedy
assignment on the best score is not the same as the best set of assignments, and the
project's LIBRARIES.md is explicit — `scipy.optimize.linear_sum_assignment`, never a
hand-rolled matcher. association.py matches EO to AIS with the same call for the same
reason; this is that argument applied one level up.

WHAT THIS FILE DELIBERATELY DOES NOT DO
It does not add camera B's contacts to the pipeline. Feeding both cameras' contacts to
association.py would produce two associations per hull, two verdicts and two rows in the
priority queue for one vessel — the ranked list would double-count and the operator would
be asked to spend a scarce asset twice on one target. Camera B contributes a BETTER
POSITION for a hull the pipeline already reasons about, and nothing else. The verdict, the
confidence and the ranking stay exactly where they were.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from contracts import EoContact
from triangulate import BearingObservation, TriangulatedFix, triangulate

MULTIVIEW_VERSION = "multiview/0.1.0"

# Three sigma of each station's OWN range estimate. See the module docstring: this is a
# sanity gate against absurd crossings, not a confirmation of the pair.
DEFAULT_MAX_CROSSCHECK_SIGMA = 3.0

# HOW FAR APART TWO OBSERVATIONS MAY BE IN TIME AND STILL BE ONE FIX.
#
# A vessel making 14 knots covers 430 m in a minute — further than the fix ellipse this
# whole file exists to shrink. Two bearings taken at different moments therefore cross at
# a point the hull never occupied, and nothing downstream can tell: the fix comes out
# small and confident, which is the worst possible shape for a wrong answer.
#
# Five seconds is chosen against the SENSOR, not the vessel: pi_sensor.py posts on a
# 0.5-1 s interval, so five seconds is several posts of slack for nodes that are not
# synchronised, while capping the error a 14-knot hull can accumulate at about 36 m —
# comfortably inside the ~150 m minor axis of a good fix. A pair with no timestamps on
# either side is allowed through, because the recorded path has one stored instant and
# refusing it would disable the feature it was built for.
DEFAULT_MAX_TIME_DELTA_S = 5.0

# The range cross-check is now a RANKING signal with a loose ceiling, not the gate.
# See patch_fovgate.py: at 4-10 px of depression a monocular range is worth so little
# that gating on it disabled triangulation precisely where triangulation is the answer.
# Above SOFT, a fix is still emitted and CARRIES THE DISAGREEMENT IN ITS LIMITATIONS.
CROSSCHECK_SOFT_SIGMA = 3.0
CROSSCHECK_HARD_SIGMA = 40.0

# WHAT IS GATED MUST BE WHAT IS DRAWN.
#
# OBSERVED, on the live two-camera run: a cross-fix ellipse appeared on the map OUTSIDE
# the overlap polygon — outside the region the same screen says is the only place a
# cross-fix is possible. Nothing was wrong with the fix; the two statements were simply
# computed from different numbers. The map draws each camera's DECLARED wedge and their
# intersection; the gate allowed a fix 25% beyond the declared range and 5 degrees
# outside the declared bearing.
#
# A console that contradicts itself is worse than one that shows less: an operator who
# spots it once stops trusting the pane, and this is the pane that says where to send
# the boat. So the range margin is gone — `max_range_m` is a DECLARED limit, not a
# measurement, and a camera that says it cannot see past 8 km does not get to contribute
# a bearing to something at 9 km. The bearing margin survives because a boresight IS a
# measurement, and it is taken from the pose's own stated yaw uncertainty rather than
# from a constant invented here.
FOV_MARGIN_FALLBACK_DEG = 2.0
RANGE_MARGIN = 1.0

# A stand-in cost for a pair the solver must never choose. linear_sum_assignment cannot
# take inf (it raises on an infeasible matrix), so impossible pairs get a number far
# above any real score and are filtered out of the SOLUTION afterwards. Filtering after
# rather than before is what lets the solver see the whole matrix and still return a
# valid assignment when some pairs are impossible.
_FORBIDDEN = 1.0e6


@dataclass(frozen=True)
class CrossFix:
    """One hull, seen from two or more stations, with the fix that resulted."""

    fix: TriangulatedFix
    contact_ids: tuple[str, ...]
    station_ids: tuple[str, ...]
    crosscheck_sigma: float

    def to_dict(self) -> dict[str, Any]:
        """
        JSON for the console and the case file.

        The ELLIPSE is published as its three parameters rather than as a drawn shape:
        the console needs them to draw a polygon, evidence.py needs them to state a
        number, and a pre-drawn shape would serve neither. `crossing_angle_deg` is
        included because it is the single number that explains why a given fix is good
        or poor — near 90 degrees is a circle, near 0 is a sliver — and an operator
        looking at a large ellipse is entitled to know it is the geometry's fault and
        not the sensor's.
        """
        f = self.fix
        return {
            "lat_deg": f.lat_deg,
            "lon_deg": f.lon_deg,
            "sigma_major_m": f.sigma_major_m,
            "sigma_minor_m": f.sigma_minor_m,
            "sigma_circular_m": f.sigma_circular_m,
            "ellipse_orientation_deg": f.ellipse_orientation_deg,
            "crossing_angle_deg": f.crossing_angle_deg,
            "station_count": f.station_count,
            "station_ids": list(self.station_ids),
            "contact_ids": list(self.contact_ids),
            "crosscheck_sigma": round(self.crosscheck_sigma, 2),
            "usable_for_position": f.usable_for_position,
            "refusal_reason": f.refusal_reason,
            "limitations": list(f.limitations),
            "version": MULTIVIEW_VERSION,
        }


def station_of(contact: EoContact) -> str | None:
    """
    Which camera produced this contact.

    `camera_pose_ref` is the only field on an EoContact that names its sensor, and it is
    a STRING, not a position — resolving it to a lat/lon is the caller's job and is done
    through the pose registry. That indirection is deliberate: a contact that carried its
    camera's coordinates would let a sensor's position be edited in one record and not
    another, and the fix would then be computed from two disagreeing versions of where
    the same camera stood.
    """
    return getattr(contact, "camera_pose_ref", None)


def observation(contact: EoContact, pose: dict[str, Any],
                station_id: str) -> BearingObservation:
    """One contact plus its camera's position, in the shape the solver takes."""
    return BearingObservation(
        station_id=station_id,
        lat_deg=float(pose["lat_deg"]),
        lon_deg=float(pose["lon_deg"]),
        bearing_deg_true=float(contact.observed_bearing_deg_true),
        bearing_sigma_deg=float(contact.bearing_uncertainty_deg or 2.0),
        own_range_m=contact.observed_range_m,
        own_range_sigma_m=contact.range_uncertainty_m,
        contact_id=contact.contact_id,
        frame_time_utc=(contact.frame_time_utc.isoformat()
                        if getattr(contact, "frame_time_utc", None) else None),
    )


def group_by_station(contacts: Iterable[EoContact],
                     poses: dict[str, dict[str, Any]]) -> dict[str, list[EoContact]]:
    """
    Split contacts by the camera that produced them.

    A contact whose `camera_pose_ref` is not in the registry is DROPPED rather than
    guessed at. A bearing is meaningless without knowing where it was taken from, and
    attributing it to the wrong camera would not fail loudly — it would produce a
    confident fix in the wrong place, which is the worst output this system can make.
    """
    out: dict[str, list[EoContact]] = {}
    for c in contacts:
        ref = station_of(c)
        if ref and ref in poses and c.observed_bearing_deg_true is not None:
            out.setdefault(ref, []).append(c)
    return out


def _crosscheck_score(fix: TriangulatedFix) -> float:
    """
    Worst disagreement, in sigma, between the fix range and any station's own range.

    The WORST and not the mean: a pair in which one camera agrees beautifully and the
    other is wildly out is not half-right, it is wrong. A fix with no cross-check
    available at all scores high enough to be gated out, because "no evidence" must not
    read as "good evidence".
    """
    xc = fix.range_crosscheck or {}
    return max((abs(v) for v in xc.values()), default=99.0)


def _bearing_range_from(pose: dict[str, Any], lat: float, lon: float
                        ) -> tuple[float, float]:
    """
    Bearing TRUE and range in metres from a station to a point.

    Local flat-earth, which is right for a few tens of kilometres and wrong for
    hundreds — stated because this is the only place in this file that assumes a shape
    for the planet. It is used ONLY to ask "is this point inside the wedge", a question
    whose answer never turns on the last metre.
    """
    import math  # noqa: PLC0415
    dlat = lat - float(pose["lat_deg"])
    dlon = lon - float(pose["lon_deg"])
    k = math.cos(math.radians((lat + float(pose["lat_deg"])) / 2.0))
    north = dlat * 111320.0
    east = dlon * 111320.0 * k
    return (math.degrees(math.atan2(east, north)) % 360.0, math.hypot(north, east))


def _fix_visible_from(pose: dict[str, Any], lat: float, lon: float) -> bool:
    """
    Could this camera actually be looking at that point?

    THE ONLY HARD GATE ON A PAIR, and deliberately the one that uses no measured range.
    A camera's position, boresight and field of view are SURVEYED quantities from the
    pose file; whether a crossing point falls inside that wedge is therefore a question
    about the rig, not about the sensor's worst estimate. A pair whose rays cross
    somewhere neither camera is pointing is not a hull seen twice, whatever the maths
    says about the intersection.
    """
    import math  # noqa: PLC0415
    brg, rng = _bearing_range_from(pose, lat, lon)
    half = float(pose.get("fov_half_angle_deg")
                 or (float(pose.get("hfov_deg") or 60.0) / 2.0))
    bore = float(pose.get("boresight_deg_true") or 0.0)
    # The slack is the pose's OWN stated yaw uncertainty. A rig that surveyed its
    # boresight to 1 degree gets 1 degree of slack; one that read a phone compass indoors
    # and honestly wrote 12 gets 12. Using a constant here would have handed the careless
    # rig the careful rig's tolerance.
    margin = float(pose.get("yaw_uncertainty_deg") or FOV_MARGIN_FALLBACK_DEG)
    off = abs((brg - bore + 180.0) % 360.0 - 180.0)
    if off > half + margin:
        return False
    max_r = float(pose.get("max_range_m") or 0.0)
    return not (max_r and rng > max_r * RANGE_MARGIN)


def _time_delta_s(a: EoContact, b: EoContact) -> float | None:
    """Seconds between two frames, or None when either side does not say."""
    ta, tb = getattr(a, "frame_time_utc", None), getattr(b, "frame_time_utc", None)
    if ta is None or tb is None:
        return None
    try:
        return abs((ta - tb).total_seconds())
    except Exception:                                      # noqa: BLE001
        return None


def cross_fix(contacts: Sequence[EoContact], poses: dict[str, dict[str, Any]], *,
              primary: str | None = None,
              max_crosscheck_sigma: float = CROSSCHECK_HARD_SIGMA,
              max_time_delta_s: float | None = DEFAULT_MAX_TIME_DELTA_S
              ) -> list[CrossFix]:
    """
    Pair contacts across cameras and triangulate each pair. One fix per hull, at most.

    Returns only pairs that BOTH triangulated successfully and survived the range
    cross-check. An empty list is a perfectly ordinary answer — it means no vessel was
    seen by two cameras at once, which is the normal state outside the overlap.

    ONLY THE TWO-STATION CASE IS SOLVED HERE. With three or more cameras the
    correspondence becomes a multi-way assignment, which is a genuinely harder problem
    and is not needed by a rig with two. `triangulate()` itself takes N observations and
    reports a residual from three up, so the geometry is already ready when the rig is;
    this function is the piece that would need rewriting, and saying so is cheaper than
    pretending it generalises.
    """
    by_station = group_by_station(contacts, poses)
    stations = sorted(by_station)
    if len(stations) < 2:
        return []
    if len(stations) > 2:
        # Take the two busiest. Stated rather than silently ignored.
        stations = sorted(by_station, key=lambda s: -len(by_station[s]))[:2]
    # `primary` pins which camera is station A, so contact_ids[0] is always the camera
    # the pipeline is reasoning about. Without it the order falls out of a string sort,
    # which put "synthetic-B-..." ahead of "synthetic-auto-..." purely because an
    # uppercase B sorts before a lowercase a — a reader of the console would then see
    # the second camera's id in the first position and reasonably conclude the panes
    # disagreed about which sensor was which.
    if primary in stations and stations[0] != primary:
        stations = [primary] + [s for s in stations if s != primary]

    sa, sb = stations
    ca_list, cb_list = by_station[sa], by_station[sb]
    pa, pb = poses[sa], poses[sb]

    # ---- score every candidate pair --------------------------------------------
    # The cost matrix is dense because triangulate() is cheap (a 2x2 solve) and the
    # contact counts are tens, not thousands. Precomputing every fix also means the
    # solver's chosen pairs need no recomputation afterwards.
    import numpy as np  # noqa: PLC0415 — lazy, as everywhere else in this project
    from scipy.optimize import linear_sum_assignment  # noqa: PLC0415

    n, m = len(ca_list), len(cb_list)
    cost = np.full((n, m), _FORBIDDEN, dtype=float)
    fixes: dict[tuple[int, int], TriangulatedFix] = {}
    for i, ca in enumerate(ca_list):
        oa = observation(ca, pa, sa)
        for j, cb in enumerate(cb_list):
            # TIME FIRST, AND BEFORE ANY GEOMETRY. Two bearings from different moments
            # are not two views of one hull, however beautifully they cross.
            if max_time_delta_s is not None:
                dt = _time_delta_s(ca, cb)
                if dt is not None and dt > max_time_delta_s:
                    continue
            fix = triangulate([oa, observation(cb, pb, sb)])
            if not fix.usable_for_position:
                continue
            # HARD GATE: both cameras must actually be looking at the crossing point.
            if not (_fix_visible_from(pa, fix.lat_deg, fix.lon_deg)
                    and _fix_visible_from(pb, fix.lat_deg, fix.lon_deg)):
                continue
            score = _crosscheck_score(fix)
            if score > max_crosscheck_sigma:
                continue
            # SOFT: the pair stands, but a fix whose own cameras' ranges disagree with
            # it must say so on its own record. An operator reading a tight ellipse is
            # entitled to know that the only independent check available to two stations
            # did not corroborate it.
            if score > CROSSCHECK_SOFT_SIGMA:
                fix.limitations.append(
                    f"The two cameras' own monocular ranges disagree with this fix by "
                    f"up to {score:.0f} sigma. At this geometry a monocular range is "
                    f"worth very little (a vessel kilometres out sits a few pixels "
                    f"below the horizon), so the fix is still the better position — but "
                    f"it is UNCORROBORATED. A third bearing would settle it.")
            cost[i, j] = score
            fixes[(i, j)] = fix

    if not fixes:
        return []

    rows, cols = linear_sum_assignment(cost)
    out: list[CrossFix] = []
    for i, j in zip(rows, cols):
        fix = fixes.get((int(i), int(j)))
        if fix is None:
            # The solver had to fill a row with a forbidden cell to complete a square
            # assignment. Dropped here, which is why the matrix uses a large finite
            # number rather than inf: the solver stays feasible, and this filter is
            # what keeps the impossible pairs out of the answer.
            continue
        out.append(CrossFix(
            fix=fix,
            contact_ids=(ca_list[int(i)].contact_id, cb_list[int(j)].contact_id),
            station_ids=(sa, sb),
            crosscheck_sigma=_crosscheck_score(fix),
        ))
    out.sort(key=lambda cf: cf.crosscheck_sigma)
    return out


def fixes_by_contact(fixes: Sequence[CrossFix]) -> dict[str, dict[str, Any]]:
    """
    contact_id -> fix, for EVERY contact in the pair.

    Keyed both ways on purpose. The console holds records built from camera A's contacts
    and would only ever look up the A id, but the evidence layer and any future
    B-authority run need the B id to find the same fix, and a second lookup table built
    later from the same data is how the two drift apart.
    """
    out: dict[str, dict[str, Any]] = {}
    for cf in fixes:
        d = cf.to_dict()
        for cid in cf.contact_ids:
            if cid:
                out[cid] = d
    return out
