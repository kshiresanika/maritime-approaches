"""
association.py — pair an EO observation with an AIS claim. Lane C (Fusion).

WHAT THIS MODULE DECIDES, AND WHAT IT DELIBERATELY DOES NOT
It decides ONE thing: which observed contact, if any, corresponds to which claimed
track. It never decides whether a pairing is honest. That is consistency.py's job,
and the split matters: if association silently rejected pairs that "looked wrong",
the spoofing detector would never see them. A vessel broadcasting a false identity
is still, physically, exactly where AIS says it is — it lies about WHAT it is, not
WHERE. So association must pair it happily, precisely so consistency.py can then
catch the class/length mismatch. Associate on geometry and time. Judge on attributes.
Never mix the two.

THE INFERENCE CHAIN THIS MODULE IMPLEMENTS
  observed : an EoContact gives a bearing (good, ~0.3-2.5 deg) and, sometimes, a
             monocular range (poor, ~12%). It has no identity by contract.
  claimed  : an AisTrack gives a position at a report time, plus SOG/COG.
  compare  : project the claim into what the camera WOULD have measured from the
             known pose — a predicted bearing and predicted range — then score the
             residual in SIGMAS, per dimension, not in metres.
  why sigmas: a 400 m range residual is nothing at 4 km with a 12% monocular
             estimate, and damning at 400 m. A metre-distance cost matrix cannot
             tell those two apart; a sigma cost matrix can. Criterion 4 asks for
             calibrated confidence, and this is where the calibration starts.
  what would change the answer: a better range estimate (stereo, AIS-cued zoom, or
             a horizon-line range) shrinks range_uncertainty_m and turns many
             ambiguous pairings into confident ones. Bearing accuracy is already
             good enough; range is the binding constraint.

WHY A GLOBAL ASSIGNMENT AND NOT A GREEDY NEAREST-NEIGHBOUR
Greedy matching is order-dependent: process contact A first and it takes the track
that contact B needed, and the result changes if you shuffle the input. In a busy
strait that is not a rare edge case, it is the normal case. scipy's
linear_sum_assignment solves the whole rectangular problem at once (Jonker-Volgenant),
minimising TOTAL cost, so the result is order-independent and reproducible — which
is a precondition for evidence-grade output. LIBRARIES.md, LIBRARY-FIRST rule.

THE THREE OUTCOMES, AND WHICH ABSENCE IS THE FINDING
  matched              contact_id set, track_id set   -> hand to consistency.py
  unmatched contact    track_id is None               -> DARK candidate (criterion 1)
  unmatched claim      contact_id is None             -> POSITION-SPOOF candidate

The third one is where honesty is easy to lose. An AIS track with no observation is
only suspicious if the camera was actually LOOKING at where it claims to be. Every
other track in the Baltic also has "no observation" and is entirely innocent. So
claims are gated by the camera's field of view and maximum range BEFORE any of them
is allowed to become a spoof candidate. Without that gate this module would emit
hundreds of false position-spoof candidates and criterion 2's ranking would be
buried in them.

LIBRARIES USED, AND THE ONE THING WRITTEN BY HAND
  scipy.optimize.linear_sum_assignment  the assignment itself
  pyproj.Geod                           WGS84 geodesic inverse/forward. Chosen over
                                        geopy because inv() returns the forward
                                        AZIMUTH as well as the distance in a single
                                        vectorised call, and the azimuth IS the
                                        predicted bearing. geopy.distance.geodesic
                                        returns distance only.
  numpy                                 the cost matrix
Written by hand: _shortest_arc_deg. LIBRARIES.md assigns that helper to lane B's
geometry.py, which is still an empty file. It is inlined here with a matching
implementation and a request filed to lane B (99_scratch/requests.md). Replace the
private copy with the import the moment geometry.py lands — two implementations of
angle wrapping is exactly how a silent 180-degree error survives.

UNITS: contracts.py conventions throughout. Degrees true, metres, m/s, aware UTC.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import NamedTuple, Sequence

import numpy as np
from loguru import logger
from pyproj import Geod
from scipy.optimize import linear_sum_assignment
from scipy.stats import chi2 as _chi2_dist
from scipy.stats import norm as _norm_dist

from contracts import AisTrack, Association, EoContact

# WGS84 ellipsoid. One instance, module level: constructing a Geod is not free and
# every call in this module uses the same ellipsoid by definition.
_GEOD = Geod(ellps="WGS84")

_KN_TO_MS = 0.514444  # exact by definition: 1 nautical mile = 1852 m, per hour.

# --------------------------------------------------------------------------------
# Tuning constants. Every one of these is an ASSUMPTION with a stated basis, not a
# magic number — because a verdict computed off an undocumented threshold is not
# evidence-grade. They are module constants rather than a config framework: there is
# one demo and one camera (CLAUDE.md: no config framework for one value).
# --------------------------------------------------------------------------------

# Nominal AIS position error. Class A transponders report a position-accuracy flag
# (DGNSS < 10 m vs GNSS < 100 m); the DMA CSV does not expose it reliably, so a
# single conservative figure is used and DECLARED. Effect if wrong: too small and
# honest pairings get gated out as spoofs; too large and distinct vessels merge into
# one ambiguous blob. 25 m sits between typical GNSS (~10-15 m) and the 100 m flag.
AIS_POSITION_SIGMA_M = 25.0

# Dead-reckoning error growth. When an AIS report is older than the frame, the claim
# is propagated forward along COG at SOG. That propagation is itself uncertain:
# assume 15% of the propagated distance (covers SOG rounding to 0.1 kn, COG error,
# and any real manoeuvre inside the gap).
DR_RELATIVE_ERROR = 0.15

# If SOG or COG is missing the claim CANNOT be propagated at all. The vessel may have
# moved anywhere within a disc of radius (dt * plausible speed). 15 kn covers most
# merchant traffic in the Fehmarn Belt. This deliberately inflates uncertainty rather
# than pretending the vessel stood still — assuming zero motion would manufacture a
# position mismatch out of a missing field.
UNKNOWN_SPEED_MS = 15.0 * _KN_TO_MS

# Gate: reject a pairing whose dof-corrected equivalent residual (see
# _equivalent_sigma) exceeds this many sigmas. 3.0 is the usual 99.7% two-sided
# figure. Raising it recovers pairings the sensor
# model is too optimistic about; lowering it produces more DARK candidates.
GATE_SIGMA = 3.0

# A rival pairing whose residual is within this many sigmas of the winner's makes the
# association AMBIGUOUS. Not a rejection — an honest label. contracts.py: "a mismatch
# computed off an ambiguous pairing is not evidence."
#
# WHY A SIGMA MARGIN AND NOT A SCORE RATIO. The first version of this compared
# scores (rival >= 0.7 * winner). That is wrong and it was caught by the two-rival
# test scene: because score is exp(-chi2/2), a fixed score ratio corresponds to a
# fixed difference in chi2, whose meaning changes completely with where on the curve
# you sit. Measured on that scene, a winner at 1.58 sigma and a rival at 1.91 sigma —
# a 0.33 sigma gap, plainly indistinguishable — produced a score ratio of 0.56 and
# was declared UNAMBIGUOUS. The sigma margin says the thing we actually mean: "a
# competing claim fits the observation within one standard deviation of the best
# one, so we cannot honestly say which hull was seen."
AMBIGUITY_SIGMA_MARGIN = 1.0

# Extra FOV slack so a contact detected right at the frame edge is not gated out by
# a fraction of a degree of pose error.
FOV_MARGIN_DEG = 2.0

# Above this 1-sigma bearing error an observation cannot localise anything and must
# not be used to pair.
#
# THIS GUARD EXISTS BECAUSE OF A SPECIFIC INTEGRATION HAZARD, NOT AS A GENERAL
# SANITY CHECK. Lane B's eo_detector.uncalibrated_benchmark_pose() sets
# yaw_uncertainty_deg = 180.0 deliberately, so that a throughput benchmark is usable
# for FPS and structurally unusable as evidence — and it folds yaw uncertainty INTO
# bearing_uncertainty_deg (eo_detector.py, the sqrt(yaw^2 + centroid^2) line). So an
# uncalibrated run arrives here as a contact with a 180-degree bearing sigma.
#
# Follow the chain: a 180-degree sigma makes every bearing residual essentially zero
# sigma, the bearing term drops out of chi2, and the pairing is then decided by RANGE
# ALONE — which can agree perfectly by chance and produce a HIGH assoc_score. Lane B's
# marker for "this run is not evidence" would arrive as a confident match. The guard
# inverts that: an uninformative bearing makes the contact unusable for pairing,
# which is what lane B meant.
#
# 15 degrees: at that sigma the 3-sigma gate spans +/-45 degrees, wider than most of a
# 60-degree frame, so the bearing has stopped localising anything.
MAX_USABLE_BEARING_SIGMA_DEG = 15.0

# Reject a claim whose report is this far from the frame time. Beyond a couple of
# minutes the dead-reckoned position is a guess, not a claim, and pairing on it
# would attribute a camera observation to a vessel that may have turned twice since.
MAX_TIME_DELTA_S = 180.0

# Finite stand-in for "forbidden pair" in the cost matrix. linear_sum_assignment
# treats np.inf as infeasible and can raise; a large finite cost always yields a
# solution, and the forbidden pairs are then removed by post-filtering. This is the
# standard formulation and it keeps the solver's failure modes out of the pipeline.
_FORBIDDEN_COST = 1.0e6

# Ceiling on the equivalent-sigma conversion. Beyond ~40 sigma the survival
# probability underflows to zero and the conversion would return inf, which would
# then poison the cost matrix. Anything past the 3-sigma gate is equally rejected, so
# the exact value up there is irrelevant — it only has to be finite and ordered.
_MAX_SIGMA = 40.0

RULESET_VERSION = "assoc-0.2.0"


class AssociationCounts(NamedTuple):
    """Reporting only. Lane-C-internal, never passed across a module boundary —
    contracts.py owns everything that crosses a seam, and a counts tuple is not a
    domain type, it is a print statement with names."""

    matched: int
    dark_candidates: int          # observation with no claim
    spoof_candidates: int         # in-view claim with no observation
    ambiguous: int
    claims_out_of_view: int       # excluded before matching; NOT a finding


class _Pose(NamedTuple):
    """Camera pose + viewing envelope, assembled once inside associate().

    Deliberately NOT a public parameter object: contracts.py owns every type that
    crosses a lane boundary and there is no CameraPose contract yet (request filed
    to ARCH). Until there is, the public API takes plain floats so lane C cannot
    accidentally define a cross-lane type it does not own."""

    lat_deg: float
    lon_deg: float
    boresight_deg_true: float
    fov_half_angle_deg: float
    max_range_m: float


# --------------------------------------------------------------------------------
# Geometry helpers.
# --------------------------------------------------------------------------------

def _shortest_arc_deg(a_deg: float | np.ndarray, b_deg: float | np.ndarray):
    """Signed shortest angular difference a - b, in (-180, +180].

    359 deg vs 1 deg is +2, not -358. Every angular comparison in this module goes
    through here. TEMPORARY: LIBRARIES.md assigns this to lane B's geometry.py.
    Delete this and import it when that file exists."""
    return ((np.asarray(a_deg, dtype=float) - np.asarray(b_deg, dtype=float) + 180.0)
            % 360.0) - 180.0


def _equivalent_sigma(chi2_val: np.ndarray, dof: np.ndarray) -> np.ndarray:
    """Map a chi-square residual with `dof` degrees of freedom onto a single
    comparable "how many sigmas" scale.

    WHY THIS EXISTS, AND WHAT IT REPLACED. The obvious shortcut is sqrt(chi2 / dof) —
    a per-dimension RMS. It is wrong in a way that matters here. Some pairs are
    judged on bearing alone (no monocular range), others on bearing AND range, and
    RMS normalisation lets an AGREEING dimension dilute a DISAGREEING one: a claim
    3.5 sigma off in bearing but perfect in range comes out at 2.47 "sigma" and slips
    inside a 3-sigma gate, purely because a second, innocent dimension was averaged
    in. For a spoof detector that is the wrong direction to fail in — a vessel
    broadcasting a false position is exactly the case where one dimension screams and
    the other agrees.

    The correct comparison is the chi-square SURVIVAL PROBABILITY: how likely a
    residual at least this large is, if the pairing were genuine. That probability is
    already dof-aware. Converting it back through the normal quantile gives an
    equivalent one-dimensional sigma, so a bearing-only pair and a bearing+range pair
    land on the same scale and can be ranked against each other.

    Sanity property: for dof = 1 this returns exactly |residual| / sigma, so the
    familiar reading of the number is unchanged. For the 3.5-sigma example above it
    returns 3.06, and the gate correctly rejects."""
    p = _chi2_dist.sf(chi2_val, dof)
    # Two-sided convention so dof=1 reduces to the plain |z| a reader expects.
    p = np.clip(p, 1e-300, 1.0)
    return np.minimum(_norm_dist.isf(p / 2.0), _MAX_SIGMA)


def _normalise_bearing(az_deg: np.ndarray) -> np.ndarray:
    """pyproj returns azimuths in (-180, 180]; contracts.py requires [0, 360)."""
    return np.mod(np.asarray(az_deg, dtype=float), 360.0)


def predict_measurement(
    *,
    camera_lat_deg: float,
    camera_lon_deg: float,
    target_lat_deg: np.ndarray,
    target_lon_deg: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """What the camera would measure for targets at these positions.

    Returns (bearing_deg_true, range_m), both arrays. Public because consistency.py
    needs the same projection to test a POSITION spoof — the claim says it is over
    there, the camera says the thing it can see is over here — and two independent
    implementations of that projection would be two chances to be wrong."""
    n = np.size(target_lat_deg)
    cam_lon = np.full(n, camera_lon_deg, dtype=float)
    cam_lat = np.full(n, camera_lat_deg, dtype=float)
    # Geod.inv is vectorised: one call for the whole population, not a Python loop.
    az12, _az21, dist_m = _GEOD.inv(
        cam_lon, cam_lat,
        np.asarray(target_lon_deg, dtype=float),
        np.asarray(target_lat_deg, dtype=float),
    )
    return _normalise_bearing(az12), np.asarray(dist_m, dtype=float)


def _dead_reckon(
    tracks: Sequence[AisTrack],
    to_time_utc: datetime,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Propagate every claim to a common instant, and say how much that cost us.

    Returns (lat, lon, time_delta_s, position_sigma_m).

    WHY: an AIS report 90 s old, from a vessel making 12 kn, describes a position
    555 m behind where the vessel now is. Compare the raw claim against a live frame
    and that 555 m appears as a position mismatch — a spoof verdict manufactured
    entirely out of latency. Propagating first removes the artefact; carrying the
    inflated sigma stops the propagation itself being trusted too far.

    time_delta_s is SIGNED, ais minus eo, per contracts.py. Negative = the AIS report
    predates the frame (the normal case)."""
    n = len(tracks)
    lat = np.array([t.claimed_lat_deg for t in tracks], dtype=float)
    lon = np.array([t.claimed_lon_deg for t in tracks], dtype=float)
    dt_s = np.array(
        [(t.report_time_utc - to_time_utc).total_seconds() for t in tracks],
        dtype=float,
    )

    # Speed and course, where claimed. None means "not available" (contracts.py), and
    # is handled as unknown motion below — never as zero motion.
    sog_ms = np.array(
        [t.claimed_sog_kn * _KN_TO_MS if t.claimed_sog_kn is not None else np.nan
         for t in tracks], dtype=float)
    cog = np.array(
        [t.claimed_cog_deg_true if t.claimed_cog_deg_true is not None else np.nan
         for t in tracks], dtype=float)

    can_dr = ~(np.isnan(sog_ms) | np.isnan(cog))
    sigma = np.full(n, AIS_POSITION_SIGMA_M, dtype=float)

    out_lat, out_lon = lat.copy(), lon.copy()
    if np.any(can_dr):
        idx = np.flatnonzero(can_dr)
        # Propagate BACKWARD by dt because dt = ais - eo: a negative dt (report older
        # than the frame) must move the vessel FORWARD along its course.
        dist = sog_ms[idx] * (-dt_s[idx])
        # Geod.fwd handles a negative distance as travel along the reciprocal azimuth,
        # which is exactly right for a report timestamped after the frame.
        f_lon, f_lat, _back_az = _GEOD.fwd(lon[idx], lat[idx], cog[idx], dist)
        out_lon[idx], out_lat[idx] = f_lon, f_lat
        # Propagation error: 15% of how far we moved it, added in quadrature with the
        # baseline GNSS error.
        sigma[idx] = np.hypot(AIS_POSITION_SIGMA_M, DR_RELATIVE_ERROR * np.abs(dist))

    if np.any(~can_dr):
        idx = np.flatnonzero(~can_dr)
        # No SOG/COG: the position is unpropagatable. Widen the error to the disc the
        # vessel could plausibly have reached. The claim is not discarded — a missing
        # field is itself a finding (contracts.py) and belongs to consistency.py.
        sigma[idx] = np.hypot(AIS_POSITION_SIGMA_M,
                              UNKNOWN_SPEED_MS * np.abs(dt_s[idx]))

    return out_lat, out_lon, dt_s, sigma


def is_in_field_of_view(
    bearing_deg_true: np.ndarray,
    range_m: np.ndarray,
    *,
    boresight_deg_true: float,
    fov_half_angle_deg: float,
    max_range_m: float,
) -> np.ndarray:
    """Boolean mask: could the camera have seen a target here at all?

    This is the gate that keeps criterion 4 honest. A claim outside the frustum has
    no observation because nobody looked, not because anybody lied. Calling that a
    position spoof would flood the operator with false candidates and make the
    ranked list — criterion 2 — worthless."""
    off_axis = np.abs(_shortest_arc_deg(bearing_deg_true, boresight_deg_true))
    return (off_axis <= (fov_half_angle_deg + FOV_MARGIN_DEG)) & (range_m <= max_range_m)


# --------------------------------------------------------------------------------
# Cost matrix.
# --------------------------------------------------------------------------------

def _pair_scores(
    contacts: Sequence[EoContact],
    tracks: Sequence[AisTrack],
    pose: _Pose,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Score every (contact, track) pair.

    Returns (score, n_sigma, feasible, time_delta_s, spatial_gap_m), each shaped
    (n_contacts, n_tracks).

    n_sigma is carried alongside score rather than recovered from it because the
    gate and the ambiguity test are both expressed in sigmas, and inverting
    exp(-n^2/2) at scores near zero loses precision exactly where those decisions
    are being made.

    score in (0, 1] is exp(-0.5 * n_sigma^2), where n_sigma is the dof-corrected
    equivalent sigma from _equivalent_sigma(). So 1.0 means the claim lands exactly
    where the camera measured, 0.61 is one sigma out, 0.011 is the 3-sigma gate. It
    is bounded, monotonic and unit-free, which is what Association.assoc_score is
    declared to be.

    Pairs judged on bearing alone (no monocular range) and pairs judged on bearing
    AND range end up on the same scale, so they can be ranked against each other
    without the number of available dimensions tilting the result."""
    n_c, n_t = len(contacts), len(tracks)
    score = np.zeros((n_c, n_t), dtype=float)
    nsig = np.full((n_c, n_t), np.inf, dtype=float)
    feasible = np.zeros((n_c, n_t), dtype=bool)
    dt_out = np.full((n_c, n_t), np.nan, dtype=float)
    gap_out = np.full((n_c, n_t), np.nan, dtype=float)

    for i, c in enumerate(contacts):
        # Each frame has its own timestamp, so the claims are propagated to THIS
        # contact's instant. One vectorised pass per contact, not per pair.
        lat, lon, dt_s, pos_sigma = _dead_reckon(tracks, c.frame_time_utc)
        pred_brg, pred_rng = predict_measurement(
            camera_lat_deg=pose.lat_deg, camera_lon_deg=pose.lon_deg,
            target_lat_deg=lat, target_lon_deg=lon,
        )
        dt_out[i, :] = dt_s

        # --- bearing residual, in sigmas -------------------------------------
        # The claim's POSITION error becomes a BEARING error that shrinks with range:
        # 25 m of position error subtends 2.9 deg at 500 m but only 0.29 deg at 5 km.
        # atan2 does that conversion honestly at every range, including very close in
        # where a small-angle approximation would understate it badly.
        pred_brg_sigma = np.degrees(np.arctan2(pos_sigma, np.maximum(pred_rng, 1.0)))
        brg_sigma = np.hypot(c.bearing_uncertainty_deg, pred_brg_sigma)
        d_brg = _shortest_arc_deg(c.observed_bearing_deg_true, pred_brg)
        chi2 = (d_brg / brg_sigma) ** 2
        dof = np.ones(n_t, dtype=float)

        # --- range residual, in sigmas (only if the camera produced one) ------
        if c.observed_range_m is not None:
            # contracts.py requires range_uncertainty_m whenever range is set. If a
            # producer breaks that, fall back to a deliberately pessimistic 25% so a
            # missing error bar can never manufacture a confident pairing.
            r_sigma_obs = (c.range_uncertainty_m
                           if c.range_uncertainty_m is not None
                           else 0.25 * c.observed_range_m)
            rng_sigma = np.hypot(r_sigma_obs, pos_sigma)
            d_rng = c.observed_range_m - pred_rng
            chi2 = chi2 + (d_rng / rng_sigma) ** 2
            dof = dof + 1.0

            # Ground distance between the observation's implied position and the
            # claim. Reported for the operator, NOT used as the cost: it re-mixes the
            # precise bearing with the sloppy range and would throw away exactly the
            # discrimination the sigma-space cost is built to keep.
            obs_lon, obs_lat, _ = _GEOD.fwd(
                pose.lon_deg, pose.lat_deg,
                c.observed_bearing_deg_true, c.observed_range_m)
            # pyproj returns a scalar for scalar input; np.asarray().ravel()[0] pins
            # it to a plain float either way so np.full below cannot silently
            # broadcast a length-1 array into the wrong shape.
            obs_lon = float(np.asarray(obs_lon).ravel()[0])
            obs_lat = float(np.asarray(obs_lat).ravel()[0])
            _a, _b, gap = _GEOD.inv(
                np.full(n_t, obs_lon), np.full(n_t, obs_lat), lon, lat)
            gap_out[i, :] = np.abs(gap)

        n_sigma = _equivalent_sigma(chi2, dof)
        nsig[i, :] = n_sigma
        score[i, :] = np.exp(-0.5 * n_sigma ** 2)

        # Feasibility: inside the statistical gate AND inside the time window. The
        # time window is separate on purpose — a 10-minute-old report can be
        # dead-reckoned onto the right bearing by luck, and pairing on it would
        # attribute a live observation to a stale claim.
        if c.bearing_uncertainty_deg > MAX_USABLE_BEARING_SIGMA_DEG:
            # Uninformative bearing: refuse every pairing for this contact rather
            # than let a chance range agreement carry it. See the constant's note.
            logger.warning(
                "contact {} has bearing_uncertainty_deg={:.1f} (> {:.1f}); it cannot "
                "be paired and is emitted unmatched-and-ambiguous, not as a dark "
                "vessel. This normally means the camera pose is uncalibrated.",
                c.contact_id, c.bearing_uncertainty_deg,
                MAX_USABLE_BEARING_SIGMA_DEG)
            feasible[i, :] = False
        else:
            feasible[i, :] = (n_sigma <= GATE_SIGMA) & (np.abs(dt_s) <= MAX_TIME_DELTA_S)

    return score, nsig, feasible, dt_out, gap_out


# --------------------------------------------------------------------------------
# Public entry point.
# --------------------------------------------------------------------------------

def associate(
    contacts: Sequence[EoContact],
    tracks: Sequence[AisTrack],
    *,
    camera_lat_deg: float,
    camera_lon_deg: float,
    boresight_deg_true: float,
    fov_half_angle_deg: float,
    max_range_m: float,
    scene_time_utc: datetime | None = None,
) -> list[Association]:
    """Globally optimal pairing of observations to claims.

    Returns one Association per outcome:
      * every matched pair        (contact_id and track_id both set)
      * every unmatched contact   (track_id None)    -> DARK candidate
      * every unmatched IN-VIEW claim (contact_id None) -> position-spoof candidate

    Claims outside the camera frustum are excluded entirely and only counted. They
    are not findings and must not reach the operator's ranked list.

    Camera pose is passed as plain floats rather than an object because contracts.py
    owns every cross-lane type and there is no CameraPose contract yet — see the
    request filed to ARCH in 99_scratch/requests.md.

    scene_time_utc is the instant claims are propagated to for the field-of-view
    test. Defaults to the latest frame time among the contacts."""
    if scene_time_utc is None:
        if not contacts:
            raise ValueError(
                "scene_time_utc is required when there are no EO contacts: without a "
                "frame time there is no instant to propagate the AIS claims to, and "
                "the field-of-view gate cannot be evaluated.")
        scene_time_utc = max(c.frame_time_utc for c in contacts)

    pose = _Pose(camera_lat_deg, camera_lon_deg, boresight_deg_true,
                 fov_half_angle_deg, max_range_m)

    # ---- 1. Field-of-view gate on the claims ---------------------------------
    if tracks:
        s_lat, s_lon, _s_dt, _s_sig = _dead_reckon(tracks, scene_time_utc)
        s_brg, s_rng = predict_measurement(
            camera_lat_deg=camera_lat_deg, camera_lon_deg=camera_lon_deg,
            target_lat_deg=s_lat, target_lon_deg=s_lon)
        in_view = is_in_field_of_view(
            s_brg, s_rng,
            boresight_deg_true=boresight_deg_true,
            fov_half_angle_deg=fov_half_angle_deg,
            max_range_m=max_range_m)
    else:
        in_view = np.zeros(0, dtype=bool)

    viewable_idx = np.flatnonzero(in_view)
    viewable = [tracks[j] for j in viewable_idx]
    logger.debug("association: {} contacts, {} claims, {} of them in view",
                 len(contacts), len(tracks), len(viewable))

    associations: list[Association] = []

    # ---- 2. Degenerate cases -------------------------------------------------
    if not contacts or not viewable:
        # Nothing to solve, but the findings still have to be emitted: every contact
        # is dark, every in-view claim is a position-spoof candidate.
        for c in contacts:
            associations.append(_unmatched_contact(c, candidate_count=0,
                                                   ambiguous=False))
        for t in viewable:
            associations.append(_unmatched_track(t, scene_time_utc,
                                                 candidate_count=0))
        return associations

    # ---- 3. Score and solve --------------------------------------------------
    score, nsig_m, feasible, dt_m, gap_m = _pair_scores(contacts, viewable, pose)

    # Cost = -log(score), so minimising total cost maximises the product of the
    # per-pair likelihoods. Infeasible pairs get a large finite cost rather than inf:
    # linear_sum_assignment can reject an inf-laden matrix as infeasible, whereas a
    # big finite cost always solves and the bad pairs are filtered out afterwards.
    cost = -np.log(np.clip(score, 1e-300, None))
    cost = np.where(feasible, cost, _FORBIDDEN_COST)

    row_idx, col_idx = linear_sum_assignment(cost)

    matched_c: dict[int, int] = {}
    for i, j in zip(row_idx, col_idx):
        if feasible[i, j]:
            matched_c[int(i)] = int(j)
        # else: the solver was forced to place this pair to complete the assignment.
        # It is not a match; dropping it is the post-filter the _FORBIDDEN_COST
        # formulation requires.

    matched_t = {j: i for i, j in matched_c.items()}

    # ---- 4. Emit matched pairs ----------------------------------------------
    for i, j in sorted(matched_c.items()):
        c, t = contacts[i], viewable[j]
        # Ambiguity is checked in BOTH directions. Row: did another claim explain
        # this observation nearly as well? Column: did another observation fit this
        # claim nearly as well? Either one means the operator is being pointed at a
        # hull that might be the wrong hull.
        # The rival is the lowest-residual feasible alternative in either direction.
        rival_sig = min(_best_other_sigma(nsig_m[i, :], feasible[i, :], exclude=j),
                        _best_other_sigma(nsig_m[:, j], feasible[:, j], exclude=i))
        winner = float(score[i, j])
        winner_sig = float(nsig_m[i, j])
        ambiguous = (np.isfinite(rival_sig)
                     and (rival_sig - winner_sig) < AMBIGUITY_SIGMA_MARGIN)
        # runner_up_score is the contract's field, so the rival is reported on the
        # same 0-1 scale as the winner even though the DECISION was made in sigmas.
        runner_up = (float(np.exp(-0.5 * rival_sig ** 2))
                     if np.isfinite(rival_sig) else 0.0)

        associations.append(Association(
            association_id=f"assoc-{c.contact_id}-{t.track_id}",
            assoc_time_utc=c.frame_time_utc,
            contact_id=c.contact_id,
            track_id=t.track_id,
            assoc_score=round(winner, 6),
            assoc_ambiguous=bool(ambiguous),
            candidate_count=int(feasible[i, :].sum()),
            runner_up_score=round(runner_up, 6) if runner_up > 0.0 else None,
            time_delta_s=float(dt_m[i, j]),
            spatial_gap_m=(None if math.isnan(gap_m[i, j])
                           else round(float(gap_m[i, j]), 1)),
        ))

    # ---- 5. Emit unmatched contacts — DARK candidates ------------------------
    for i, c in enumerate(contacts):
        if i in matched_c:
            continue
        if c.bearing_uncertainty_deg > MAX_USABLE_BEARING_SIGMA_DEG:
            # NOT a dark vessel — an unusable observation. candidate_count reports
            # every claim the camera was pointing at, so verdict.py can see that
            # alternatives existed and must defer instead of labelling this DARK.
            associations.append(_unmatched_contact(
                c, candidate_count=len(viewable), ambiguous=True))
            continue
        n_cand = int(feasible[i, :].sum())
        # A contact with zero feasible claims is a clean dark candidate. A contact
        # WITH feasible claims that all went to other contacts is a different animal:
        # the pairing was contested and lost, so this is flagged ambiguous rather
        # than reported as a confident dark vessel.
        associations.append(_unmatched_contact(c, candidate_count=n_cand,
                                               ambiguous=n_cand > 0))

    # ---- 6. Emit unmatched in-view claims — POSITION-SPOOF candidates --------
    for j, t in enumerate(viewable):
        if j in matched_t:
            continue
        associations.append(_unmatched_track(
            t, scene_time_utc, candidate_count=int(feasible[:, j].sum())))

    return associations


def _best_other_sigma(nsig: np.ndarray, feas: np.ndarray, *, exclude: int) -> float:
    """Lowest feasible residual in this row/column other than the winner — i.e. the
    best rival. +inf when the winner had no rival at all, which reads correctly
    through the sigma-margin test: an infinite gap is never ambiguous."""
    mask = feas.copy()
    mask[exclude] = False
    return float(nsig[mask].min()) if mask.any() else float("inf")


def _unmatched_contact(c: EoContact, *, candidate_count: int,
                       ambiguous: bool) -> Association:
    """An observation with no claim. track_id is None and THAT is the finding —
    contracts.py, the DARK case."""
    return Association(
        association_id=f"assoc-{c.contact_id}-none",
        assoc_time_utc=c.frame_time_utc,
        contact_id=c.contact_id,
        track_id=None,
        assoc_score=0.0,
        assoc_ambiguous=ambiguous,
        candidate_count=candidate_count,
        runner_up_score=None,
        time_delta_s=None,
        spatial_gap_m=None,
    )


def _unmatched_track(t: AisTrack, scene_time_utc: datetime, *,
                     candidate_count: int) -> Association:
    """An in-view claim with no observation. contact_id is None — the position-spoof
    case. It is a CANDIDATE only: an occluded vessel, a missed detection, or one hull
    hidden behind another produce the same signature, and separating those is
    consistency.py's and verdict.py's problem, not this module's."""
    return Association(
        association_id=f"assoc-none-{t.track_id}",
        assoc_time_utc=scene_time_utc,
        contact_id=None,
        track_id=t.track_id,
        assoc_score=0.0,
        assoc_ambiguous=False,
        candidate_count=candidate_count,
        runner_up_score=None,
        time_delta_s=(t.report_time_utc - scene_time_utc).total_seconds(),
        spatial_gap_m=None,
    )


def count_outcomes(associations: Sequence[Association],
                   claims_out_of_view: int = 0) -> AssociationCounts:
    """Tally the three outcomes. Reporting helper — the counts are derived from the
    Associations themselves so a count can never disagree with the list it describes."""
    matched = sum(1 for a in associations
                  if a.contact_id is not None and a.track_id is not None)
    dark = sum(1 for a in associations if a.track_id is None)
    spoof = sum(1 for a in associations if a.contact_id is None)
    ambiguous = sum(1 for a in associations if a.assoc_ambiguous)
    return AssociationCounts(matched, dark, spoof, ambiguous, claims_out_of_view)
