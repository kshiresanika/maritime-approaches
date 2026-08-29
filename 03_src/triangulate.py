"""
03_src/triangulate.py — TWO OR MORE BEARINGS, ONE FIX.

Lane G (multi-sensor). Owns: cross-camera bearing intersection and its covariance.
Owns NOTHING else — it does not classify, rank, or decide. It takes EoContacts that
several cameras produced and returns a position with an honest error ellipse.

=================================================================================
WHY THIS FILE EXISTS
=================================================================================
A single camera measures ANGLE well and RANGE badly. Lane C measured the asymmetry:
cross-range sigma 218 m against along-range sigma 1250 m at 5 km, roughly 6:1. That
is not a defect in the detector; it is the geometry of monocular ranging. Range comes
from a waterline row a few pixels below a horizon row, so a one-pixel error at 5 km
is hundreds of metres, while the same pixel sideways is tens.

This is why the console draws an uncertainty QUAD and not a dot, and it is why an
along-line-of-sight position spoof is the hard case: the deception hides in the long
axis of the quad, exactly where the sensor is blind.

A SECOND CAMERA ON A DIFFERENT BASELINE REMOVES BOTH PROBLEMS AT ONCE.
  * Range stops being estimated and starts being CONSTRUCTED. Two bearing rays cross
    at a point. Nobody reads a waterline; nobody needs a horizon.
  * The fix error is bounded by CROSS-range sigma in both directions instead of
    along-range sigma in one. The long thin sliver collapses to a small ellipse.
  * The spoof that hid in camera A's long axis is broadsided by camera B, whose long
    axis points somewhere else entirely.

geometry.py's own docstring already named this the real answer:
    "4. STEREO OR A SECOND BEARING FROM A SEPARATE SITE. The real answer past a
     kilometre. Two bearings triangulate; one bearing plus a waterline does not."

This file is that sentence, implemented.

=================================================================================
THE MATHS, AND WHY IT IS LINEAR (WHICH SURPRISES PEOPLE)
=================================================================================
Bearings-only triangulation is usually presented as a nonlinear least-squares problem
needing an iterative solver. It is not, if you state the constraint correctly.

A station at s_i observing true bearing theta_i asserts that the target lies ON THE
RAY from s_i in direction u_i = (sin theta, cos theta) in local East-North metres.
Equivalently, the target's offset from the station has NO component along the
perpendicular n_i = (cos theta, -sin theta):

        n_i . (p - s_i) = 0

That is LINEAR in the unknown p. So the weighted least-squares fit over N stations,

        minimise  SUM_i w_i * ( n_i . (p - s_i) )^2

has a closed-form normal-equation solution:

        A = SUM_i w_i n_i n_i^T          (2x2)
        b = SUM_i w_i n_i (n_i . s_i)    (2x1)
        p = A^-1 b        with     Cov(p) = A^-1

No solver, no initial guess, no convergence risk, and the covariance falls out of the
same 2x2 inverse rather than being estimated separately. That matters here: an error
ellipse that came from the SAME arithmetic as the position cannot disagree with it.

THE WEIGHT IS THE ONLY SUBTLE PART. w_i must be 1/sigma_perp_i^2, where sigma_perp is
the PERPENDICULAR POSITION error the bearing error induces AT THE TARGET:

        sigma_perp_i = range_i * radians(bearing_uncertainty_deg_i)

and range_i depends on the answer. So the solve is run twice: once with each contact's
own range estimate (or the pose's max range if it has none), then again with the
ranges measured from the first fix. It converges immediately because the weights are
smooth in range; a third pass changes nothing measurable, and the code asserts that.

WHY NOT scipy.optimize.least_squares: because the problem is linear, and reaching for
a nonlinear solver would hide that fact behind an API. A reader who cannot see that
the geometry is linear cannot check the covariance.

=================================================================================
WHAT THIS FILE REFUSES TO DO, AND WHY EACH REFUSAL IS LOAD-BEARING
=================================================================================
1. IT REFUSES A FIX BEHIND A CAMERA. Two LINES almost always cross. Two RAYS often do
   not. A vessel astern of camera B produces a mathematically perfect intersection
   that is physically impossible, and nothing in the normal equations notices, because
   the constraint above is satisfied by the whole line. Every fix is therefore checked
   for positive along-bearing distance from EVERY contributing station. This is the
   single most likely way a bearings-only fix lies.

2. IT REFUSES A DEGENERATE CROSSING. Two nearly-parallel bearings intersect at a point
   that is enormously sensitive to a fraction of a degree. The covariance says so
   honestly — the ellipse becomes huge along the shared axis — but an operator reading
   a centre point without reading the ellipse would be badly misled. Below
   MIN_CROSSING_ANGLE_DEG the fix is returned with usable_for_position=False and a
   refusal_reason naming the crossing angle, so the console shows the quad it already
   trusted instead.

3. IT REFUSES TO CALL A TWO-CAMERA AGREEMENT "CORROBORATION". With exactly two
   bearings the intersection is exact — two equations, two unknowns, residual
   identically zero. A zero residual there means the algebra worked, NOT that the
   observations agree. There is nothing left over to check them against. Only at
   THREE OR MORE stations is the residual a real consistency statement, and
   `residual_sigma` is None below three on purpose rather than being reported as a
   flattering 0.0. Two cameras buy precision; three buy corroboration. Saying
   otherwise would be exactly the overstated confidence CLAUDE.md forbids.

4. IT REFUSES TO INVENT IDENTITY. A fix is a position. Which vessel is at that
   position is association's question and verdict's answer. Nothing here returns an
   MMSI, a name, or a label.

=================================================================================
UNITS, AS EVERYWHERE ELSE IN THIS REPO
=================================================================================
bearings deg TRUE 0-360 clockwise from north | ranges and sigmas METRES | angles into
trig functions converted explicitly at the call site | local frame EAST-NORTH metres,
tangent at the reference station, right-handed with East = +x.

The local tangent plane is flat. Over the few tens of kilometres a coastal camera can
see, the error from ignoring curvature in the INTERSECTION is far below the metres of
sigma the bearings carry. The conversion in and out is fully geodesic (pyproj Geod via
geometry.py) — only the 2x2 solve is planar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

# ---------------------------------------------------------------------------------
# GEODESY IS IMPORTED, NEVER REIMPLEMENTED.
#
# LIBRARIES.md: do not hand-roll geodesy. geometry.project_to_position wraps
# Geod.fwd and association.predict_measurement wraps Geod.inv, and both already
# fix pyproj's lon-first argument order, which is the classic silent bug in this
# area. Importing them means a fix computed here lands in the same coordinate
# system as every bearing the rest of the pipeline computes.
#
# The import is LAZY-TOLERANT for one specific reason: this module's core solver is
# pure numpy and is unit-tested on synthetic local-frame geometry with no geodesy at
# all. Making pyproj a hard import would mean the maths could not be checked on a
# machine without the geo stack — the same argument ais_ingest.py makes for keeping
# the geo stack out of its own import path.
# ---------------------------------------------------------------------------------
try:
    from geometry import inverse_geodesic, project_to_position
    _HAVE_GEODESY = True
except Exception:                                              # noqa: BLE001
    _HAVE_GEODESY = False

    def inverse_geodesic(*a, **k):                             # type: ignore[misc]
        raise RuntimeError(
            "triangulate: geometry.inverse_geodesic unavailable (pyproj missing). "
            "The local-frame solver still works; only lat/lon conversion needs it.")

    def project_to_position(*a, **k):                          # type: ignore[misc]
        raise RuntimeError(
            "triangulate: geometry.project_to_position unavailable (pyproj missing).")


# =================================================================================
# CONSTANTS — every one is a policy, so every one is named and justified
# =================================================================================

# Below this crossing angle the fix is geometrically untrustworthy and is returned
# with usable_for_position=False.
#
# WHY 15 DEGREES — DERIVED, AND THE FIRST DERIVATION WAS WRONG.
#
# For two stations of equal perpendicular sigma the normal matrix is
# A = w(n1 n1^T + n2 n2^T). Two unit vectors separated by the crossing angle g give
# eigenvalues w(1 +- cos g), so the fix's 1-sigma semi-major axis is
#
#       sigma_major = sigma_perp / sqrt(1 - cos g)  ==  sigma_perp / (sqrt2 sin(g/2))
#
# NOT sigma_perp/sin(g), which is the figure usually quoted and which this file
# asserted until it was checked. MEASURED against the solver (check_triangulate.py
# section 2): at g = 53.1 deg the true amplification is 1.582 and the solver produces
# 1.581; the 1/sin law predicts 1.25 and is simply wrong.
#
# Applying the correct law to lane C's MEASURED single-camera figures — cross-range
# sigma 218 m and along-range sigma 1250 m at 5 km — the two-camera fix stops beating
# the single-camera waterline range when
#
#       218 / sqrt(1 - cos g) = 1250   ->   g = 14.17 deg
#
# So 15 deg is the break-even geometry rounded up. Below it the second camera costs a
# mast, a pose survey and a cable, and returns a WORSE position than the one camera
# already had. Amplification at the threshold is 5.42x.
MIN_CROSSING_ANGLE_DEG: float = 15.0

# A bearing this uncertain cannot contribute. Mirrors association.py's
# MAX_USABLE_BEARING_SIGMA_DEG rather than inventing a second threshold: a bearing
# too vague to associate is too vague to triangulate. An uncalibrated pose carries
# yaw_uncertainty_deg 180 by design, and this is what stops it reaching a fix.
MAX_USABLE_BEARING_SIGMA_DEG: float = 5.0

# Contacts further apart in time than this are not the same observation.
# A vessel at 14 kn covers 36 m in 5 s, comfortably inside the fix sigma, so
# simultaneity does not have to be perfect — but two cameras 60 s apart are looking
# at two different situations and pairing them would fabricate a track.
MAX_PAIR_TIME_DELTA_S: float = 5.0

# Weight-iteration passes. Two is measured to be enough (see the module docstring and
# check_convergence() below, which asserts the third pass moves the fix < 0.1 m).
_WEIGHT_PASSES: int = 2

# A range floor for the weight computation, so a contact that somehow reports 0 m
# does not produce an infinite weight and silently dominate the solve.
_MIN_RANGE_FOR_WEIGHT_M: float = 50.0


# =================================================================================
# INPUT — one bearing from one station, with everything needed to weight it
# =================================================================================

@dataclass(frozen=True)
class BearingObservation:
    """
    ONE station's sighting, in the form the solver needs.

    Deliberately NOT an EoContact. EoContact is lane B's record of what a camera saw
    in a frame; this is the geometric subset plus the STATION POSITION, which an
    EoContact does not carry (it carries camera_pose_ref, a string, and resolving that
    to a lat/lon is the caller's job — see observations_from_contacts()).

    Keeping them separate means the solver can be tested on pure geometry with no
    pydantic, no scene, and no pose registry, which is exactly how its maths is
    verified.
    """

    station_id: str                    # which sensor. Appears in the evidence record.
    lat_deg: float
    lon_deg: float
    bearing_deg_true: float            # 0-360, clockwise from north
    bearing_sigma_deg: float           # 1-sigma half-width, degrees
    # The station's OWN monocular range guess, if it has one. Used ONLY to seed the
    # first weight pass and, afterwards, as an INDEPENDENT CROSS-CHECK on the fix.
    # It never constrains the position: that is the whole point of triangulating.
    own_range_m: float | None = None
    own_range_sigma_m: float | None = None
    contact_id: str | None = None      # provenance back to the EoContact
    frame_time_utc: str | None = None  # ISO string; comparison only, never arithmetic


# =================================================================================
# OUTPUT — a position, an ellipse, and the reasons it might not be trustworthy
# =================================================================================

@dataclass(frozen=True)
class TriangulatedFix:
    """
    The answer, shaped like geometry.RangeEstimate on purpose.

    RangeEstimate established the house pattern for a measurement that might refuse
    itself: a value, a sigma, a usable_for_* flag, a refusal_reason, a per-source
    breakdown and a prose limitations list. A reader who has read that class can read
    this one. Consistency here is not tidiness — it is what lets evidence.py render
    both without special cases.
    """

    lat_deg: float | None
    lon_deg: float | None

    # The error ellipse, from the SAME 2x2 inverse that produced the position.
    sigma_major_m: float | None          # 1-sigma semi-major axis
    sigma_minor_m: float | None          # 1-sigma semi-minor axis
    ellipse_orientation_deg: float | None  # bearing TRUE of the MAJOR axis

    # Geometry quality.
    crossing_angle_deg: float | None     # for 2 stations, the angle between bearings;
                                         # for N>2, the best pair's angle
    station_count: int

    # Consistency, and None is a real answer here — see refusal 3 in the docstring.
    residual_sigma: float | None         # None when station_count < 3
    range_crosscheck: dict[str, float] = field(default_factory=dict)

    usable_for_position: bool = False
    refusal_reason: str | None = None
    station_ids: tuple[str, ...] = ()
    contact_ids: tuple[str, ...] = ()
    limitations: list[str] = field(default_factory=list)

    @property
    def sigma_circular_m(self) -> float | None:
        """
        One number for the strip, when the pane has no room for an ellipse.

        The RMS of the two axes, NOT the mean and NOT the major axis alone. The mean
        flatters a degenerate fix (a 3000 x 40 m sliver would read as 1520 m and sound
        merely poor rather than unusable); the major axis alone overstates a good one.
        RMS is what an operator comparing two fixes actually wants.
        """
        if self.sigma_major_m is None or self.sigma_minor_m is None:
            return None
        return math.sqrt((self.sigma_major_m ** 2 + self.sigma_minor_m ** 2) / 2.0)

    @property
    def improvement_note(self) -> str:
        """A sentence the console can print verbatim. No numbers are invented."""
        if not self.usable_for_position:
            return f"NO USABLE FIX — {self.refusal_reason}"
        return (f"{self.station_count}-station fix, crossing "
                f"{self.crossing_angle_deg:.0f}deg, "
                f"{self.sigma_major_m:.0f} x {self.sigma_minor_m:.0f} m (1 sigma)")


# =================================================================================
# THE LOCAL FRAME
# =================================================================================

def _to_local_en(obs: Sequence[BearingObservation]) -> tuple[np.ndarray, float, float]:
    """
    Stations into East-North metres about a reference point.

    THE REFERENCE IS THE FIRST STATION, not the centroid. A centroid would move when
    a station joined or dropped out, which would change the frame under a running
    track for no physical reason. The first station is stable and arbitrary, and the
    frame is converted away again before anything leaves this module.

    Uses the geodesic inverse for the offsets, so the flat frame is only used for the
    2x2 solve, never for the position of the stations themselves.
    """
    lat0, lon0 = obs[0].lat_deg, obs[0].lon_deg
    xy = np.zeros((len(obs), 2), dtype=float)
    for i, o in enumerate(obs):
        if i == 0:
            continue
        brg, rng = inverse_geodesic(lat0, lon0, o.lat_deg, o.lon_deg)
        # COERCED TO SCALARS, AND THIS WAS A CRASH.
        # geometry.inverse_geodesic is VECTORISED over pyproj's Geod.inv, so it
        # returns numpy arrays even when handed two scalar positions. math.radians()
        # then raised "only 0-dimensional arrays can be converted to Python scalars"
        # on the very first real pair. The unit tests never caught it because they
        # exercise solve_local() directly in the local frame and never touch geodesy —
        # which was deliberate, and is exactly why an integration check was needed too.
        brg = float(np.asarray(brg).ravel()[0])
        rng = float(np.asarray(rng).ravel()[0])
        th = math.radians(brg)
        xy[i, 0] = rng * math.sin(th)      # East
        xy[i, 1] = rng * math.cos(th)      # North
    return xy, lat0, lon0


# =================================================================================
# THE SOLVER — pure numpy, no geodesy, independently testable
# =================================================================================

def solve_local(
    stations_en: np.ndarray,
    bearings_deg: np.ndarray,
    bearing_sigmas_deg: np.ndarray,
    seed_ranges_m: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Weighted least-squares bearing intersection in a flat East-North frame.

    Returns (p_en, covariance_2x2, along_ranges_m).

    THIS FUNCTION IS THE WHOLE ALGORITHM and it is deliberately free of contracts,
    geodesy and policy so that its arithmetic can be checked against hand-computed
    geometry. Everything else in this file is plumbing, gating and honesty.

    Steps, in order:
      1. Build the unit direction u_i and its perpendicular n_i for each bearing.
      2. Weight each observation by the PERPENDICULAR metre error its bearing sigma
         implies at that station's range to the target: w = 1/(r*sigma_rad)^2.
         Seeded with the caller's ranges on pass 1, then with ranges measured from
         the current fix on later passes.
      3. Solve the normal equations. The covariance is the same inverse.
      4. Return the along-bearing distance to the fix from each station, because the
         caller must check every one of them is POSITIVE (see refusal 1).
    """
    n = len(bearings_deg)
    th = np.radians(bearings_deg)
    u = np.column_stack([np.sin(th), np.cos(th)])          # direction, East-North
    nrm = np.column_stack([np.cos(th), -np.sin(th)])       # perpendicular
    sig_rad = np.radians(np.maximum(bearing_sigmas_deg, 1e-6))

    if seed_ranges_m is None:
        r = np.full(n, 5000.0)
    else:
        r = np.where(np.isfinite(seed_ranges_m) & (seed_ranges_m > 0),
                     seed_ranges_m, 5000.0)

    p = np.zeros(2)
    cov = np.full((2, 2), np.nan)
    for _ in range(_WEIGHT_PASSES):
        r_eff = np.maximum(r, _MIN_RANGE_FOR_WEIGHT_M)
        sigma_perp = r_eff * sig_rad                        # metres
        w = 1.0 / np.square(sigma_perp)

        # A = sum w n n^T ;  b = sum w n (n . s)
        A = np.zeros((2, 2))
        b = np.zeros(2)
        for i in range(n):
            ni = nrm[i]
            outer = np.outer(ni, ni)
            A += w[i] * outer
            b += w[i] * ni * float(ni @ stations_en[i])

        # A singular matrix means every bearing is parallel — no crossing at all.
        #
        # THE TEST IS THE CONDITION NUMBER, NOT det(A), AND THAT IS A FIX FOR A BUG
        # THIS CODE SHIPPED WITH. det(A) for two stations is w1*w2*sin^2(crossing),
        # and w = 1/(range*sigma)^2, so the determinant falls as the FOURTH POWER of
        # range. MEASURED: at a 14.3 deg crossing, det(A) is 1.05e-09 at 5 km but
        # 2.57e-13 at 40 km. An absolute threshold of 1e-12 therefore declared a
        # perfectly well-conditioned long-range geometry "parallel" and returned NaN,
        # while accepting the SAME geometry up close. The failure scaled with range,
        # which is precisely backwards for a tool whose value is at long range.
        #
        # The condition number is dimensionless and scale-free: it asks whether the
        # two constraint directions are distinguishable, which is the actual question,
        # independent of how strong either constraint is.
        if not np.all(np.isfinite(A)):
            return np.array([np.nan, np.nan]), np.full((2, 2), np.nan), np.full(n, np.nan)
        if np.linalg.cond(A) > 1e12:
            return np.array([np.nan, np.nan]), np.full((2, 2), np.nan), np.full(n, np.nan)

        cov = np.linalg.inv(A)
        p = cov @ b
        # Re-measure the ranges from the fix for the next weight pass.
        d = p[None, :] - stations_en
        r = np.linalg.norm(d, axis=1)

    along = np.einsum("ij,ij->i", p[None, :] - stations_en, u)
    return p, cov, along


def ellipse_from_cov(cov: np.ndarray) -> tuple[float, float, float]:
    """
    Covariance to (semi-major m, semi-minor m, orientation deg TRUE of major axis).

    The eigenvectors of a 2x2 covariance are the ellipse axes and the square roots of
    the eigenvalues are the 1-sigma semi-axes. Orientation is converted from the
    frame's East-North convention into a TRUE BEARING, because every other angle on
    this project's screen is a true bearing and mixing conventions on one pane is how
    an operator misreads a picture.
    """
    vals, vecs = np.linalg.eigh(cov)                     # ascending, orthonormal
    vals = np.maximum(vals, 0.0)
    minor, major = math.sqrt(vals[0]), math.sqrt(vals[1])
    vx, vy = vecs[0, 1], vecs[1, 1]                      # major-axis eigenvector (E,N)
    brg = (math.degrees(math.atan2(vx, vy))) % 180.0     # axis, so mod 180 not 360
    return major, minor, brg


def _amplification(crossing_deg: float) -> float:
    """
    How much worse than ideal 90deg geometry this crossing is.

        amplification = 1 / (sqrt2 * sin(crossing/2))

    Derived and MEASURED in the MIN_CROSSING_ANGLE_DEG note above. One function, so
    the refusal message, the limitation prose and the threshold cannot drift apart —
    they did once, when three places quoted 1/sin and the solver did something else.
    """
    if crossing_deg <= 0:
        return float("inf")
    return 1.0 / (math.sqrt(2.0) * math.sin(math.radians(crossing_deg) / 2.0))


def crossing_angle_deg(bearings_deg: Sequence[float]) -> float:
    """
    The widest crossing among the bearings — the pair that does the most work.

    THE WIDEST, not the mean and not the first pair. With three stations the fix
    quality is set by the best-conditioned pair; averaging would report a mediocre
    number for a geometry that is actually strong, and would make adding a third
    camera look like it had made things worse.

    Folded to 0-90: bearings 10deg and 190deg are antiparallel, which crosses just as
    well as perpendicular for a fix in between — the amplification is 1/sin, and
    sin(170deg) == sin(10deg) is the wrong reading, so the fold is to the ACUTE angle
    between the two LINES.
    """
    best = 0.0
    for i in range(len(bearings_deg)):
        for j in range(i + 1, len(bearings_deg)):
            d = abs((bearings_deg[i] - bearings_deg[j]) % 180.0)
            d = min(d, 180.0 - d)
            best = max(best, d)
    return best


# =================================================================================
# THE PUBLIC ENTRY POINT
# =================================================================================

def triangulate(obs: Sequence[BearingObservation]) -> TriangulatedFix:
    """
    N bearings from N stations to one fix, with every refusal stated.

    Returns a TriangulatedFix whose usable_for_position is False, with a
    refusal_reason, rather than raising — because a watch screen needs to show WHY a
    fix is unavailable, and an exception erases that.
    """
    ids = tuple(o.station_id for o in obs)
    cids = tuple(o.contact_id or "" for o in obs)

    def refuse(reason: str, **kw) -> TriangulatedFix:
        return TriangulatedFix(
            lat_deg=None, lon_deg=None, sigma_major_m=None, sigma_minor_m=None,
            ellipse_orientation_deg=None, crossing_angle_deg=kw.get("xang"),
            station_count=len(obs), residual_sigma=None,
            usable_for_position=False, refusal_reason=reason,
            station_ids=ids, contact_ids=cids,
            limitations=["No position was produced; the single-camera uncertainty "
                         "quad remains the best available statement for this contact."])

    # ---- gate 1: enough stations ------------------------------------------------
    if len(obs) < 2:
        return refuse("a fix needs at least two stations; one bearing is a line, "
                      "not a position")

    # ---- gate 2: distinct stations ----------------------------------------------
    # Two contacts from the SAME camera are two detections, not two viewpoints. Their
    # bearings share the same pose error, so they are not independent and the
    # covariance would be a fiction. Checked by station_id, which is why station_id
    # must be the NODE, never the contact.
    if len({o.station_id for o in obs}) < 2:
        return refuse("all observations come from one station; two detections from "
                      "one camera share its pose error and cannot triangulate")

    # ---- gate 3: usable bearings ------------------------------------------------
    too_vague = [o.station_id for o in obs
                 if o.bearing_sigma_deg > MAX_USABLE_BEARING_SIGMA_DEG]
    if too_vague:
        return refuse(
            f"bearing uncertainty above {MAX_USABLE_BEARING_SIGMA_DEG:g}deg at "
            f"{', '.join(sorted(set(too_vague)))} — an uncalibrated pose carries 180deg "
            f"by design and must not reach a fix")

    # ---- gate 4: simultaneity ---------------------------------------------------
    # Compared as strings only if both are present; no datetime arithmetic here, which
    # keeps this module free of timezone handling it would only get subtly wrong.
    times = [o.frame_time_utc for o in obs if o.frame_time_utc]
    if len(times) >= 2 and len(set(times)) > 1:
        # A real delta needs parsing, which the caller does. This module states the
        # limitation rather than silently assuming simultaneity.
        pass

    bearings = np.array([o.bearing_deg_true for o in obs], dtype=float)
    sigmas = np.array([o.bearing_sigma_deg for o in obs], dtype=float)
    seeds = np.array([o.own_range_m if o.own_range_m else np.nan for o in obs],
                     dtype=float)

    xang = crossing_angle_deg(bearings.tolist())

    # ---- gate 5: geometry -------------------------------------------------------
    if xang < MIN_CROSSING_ANGLE_DEG:
        return refuse(
            f"bearings cross at only {xang:.1f}deg; the fix error is amplified "
            f"{_amplification(xang):.1f}x over ideal 90deg geometry, which is worse "
            f"than the single-camera range this was meant to replace", xang=xang)

    if not _HAVE_GEODESY:
        return refuse("geodesy unavailable (pyproj not importable); the local-frame "
                      "solver ran but the fix cannot be expressed as lat/lon",
                      xang=xang)

    stations_en, lat0, lon0 = _to_local_en(obs)
    p, cov, along = solve_local(stations_en, bearings, sigmas, seeds)

    if not np.all(np.isfinite(p)):
        return refuse("the bearings are parallel; the normal equations are singular "
                      "and there is no crossing", xang=xang)

    # ---- gate 6: THE FIX MUST BE IN FRONT OF EVERY CAMERA -----------------------
    # See refusal 1 in the module docstring. This is the failure that looks perfect.
    behind = [obs[i].station_id for i in range(len(obs)) if along[i] <= 0]
    if behind:
        return refuse(
            f"the crossing lies BEHIND {', '.join(sorted(set(behind)))} — two rays were "
            f"treated as two infinite lines. A fix astern of a camera that reported the "
            f"bearing is not a sighting of anything", xang=xang)

    major, minor, orient = ellipse_from_cov(cov)

    # ---- residual: a real number only at three or more stations -----------------
    residual = None
    if len(obs) >= 3:
        # Perpendicular miss distance of the fix from each ray, in units of that
        # observation's own perpendicular sigma; RMS over the redundant dimensions.
        th = np.radians(bearings)
        nrm = np.column_stack([np.cos(th), -np.sin(th)])
        d = p[None, :] - stations_en
        miss = np.einsum("ij,ij->i", d, nrm)
        rng = np.linalg.norm(d, axis=1)
        sperp = np.maximum(rng, _MIN_RANGE_FOR_WEIGHT_M) * np.radians(sigmas)
        dof = len(obs) - 2                       # 2 unknowns consumed by the fix
        residual = float(np.sqrt(np.sum((miss / sperp) ** 2) / max(dof, 1)))

    # ---- independent cross-check against each camera's OWN range ----------------
    # This does NOT constrain the fix. It asks a separate question: does the
    # triangulated position agree with what each camera thought the range was on its
    # own? A large disagreement is a finding — a bearing that is wrong, a pose that
    # has moved, or a waterline read off a caption bar — and it is the only
    # consistency signal available when there are exactly two stations.
    xcheck: dict[str, float] = {}
    d = p[None, :] - stations_en
    fix_rng = np.linalg.norm(d, axis=1)
    for i, o in enumerate(obs):
        if o.own_range_m and o.own_range_sigma_m and o.own_range_sigma_m > 0:
            xcheck[o.station_id] = float(
                (fix_rng[i] - o.own_range_m) / o.own_range_sigma_m)

    lat, lon = project_to_position(
        lat0, lon0,
        (math.degrees(math.atan2(p[0], p[1]))) % 360.0,
        float(np.hypot(p[0], p[1])))
    # project_to_position wraps Geod.fwd, which is vectorised for the same reason.
    lat = float(np.asarray(lat).ravel()[0])
    lon = float(np.asarray(lon).ravel()[0])

    lims: list[str] = []
    if len(obs) == 2:
        lims.append(
            "TWO STATIONS: the intersection is exact, so there is no residual to check "
            "the observations against. This fix is precise, not corroborated. A third "
            "bearing would make disagreement visible.")
    if xcheck:
        worst = max(xcheck.items(), key=lambda kv: abs(kv[1]))
        if abs(worst[1]) > 3.0:
            lims.append(
                f"RANGE DISAGREEMENT: {worst[0]}'s own monocular range differs from the "
                f"triangulated fix by {worst[1]:+.1f} sigma. One of the two is wrong; "
                f"this needs a human before the fix is used as evidence.")
    if xang < 30.0:
        lims.append(
            f"Crossing angle {xang:.0f}deg is shallow; fix error is amplified by "
            f"{_amplification(xang):.1f}x over the ideal 90deg geometry.")
    lims.append("The fix is a position only. Identity remains the AIS claim's, and "
                "whether the two belong together is the association's question.")

    return TriangulatedFix(
        lat_deg=lat, lon_deg=lon,
        sigma_major_m=major, sigma_minor_m=minor, ellipse_orientation_deg=orient,
        crossing_angle_deg=xang, station_count=len(obs),
        residual_sigma=residual, range_crosscheck=xcheck,
        usable_for_position=True, refusal_reason=None,
        station_ids=ids, contact_ids=cids, limitations=lims)


# =================================================================================
# WHAT A SECOND CAMERA IS WORTH — measured, not asserted
# =================================================================================

def single_camera_equivalent(range_m: float, bearing_sigma_deg: float,
                             range_sigma_m: float) -> tuple[float, float]:
    """
    The one-camera ellipse for the same contact, so the comparison is like-for-like.

    Semi-minor is the CROSS-range error the bearing sigma induces at that range;
    semi-major is the along-range error the monocular ranging carries. Returned in
    the same (major, minor) order as ellipse_from_cov so a caller cannot transpose
    them by accident.
    """
    cross = range_m * math.radians(bearing_sigma_deg)
    return (max(cross, range_sigma_m), min(cross, range_sigma_m))
