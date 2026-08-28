"""
99_scratch/lane_c_association_check.py — lane C's self-check for association.py.

WHY THIS FILE EXISTS, AND WHY IT IS IN 99_scratch AND NOT IN tests/
tests/fixtures.py is ARCH-owned and its scenarios are built for consistency.py: they
vary CLASS and LENGTH so an attribute mismatch can be detected, and they leave
position and bearing random. That is correct for what they are for, but it means a
geometric matcher run against scenario_match() correctly returns NO match — the
claim and the observation in that fixture are not in the same place, and never were
meant to be.

So association.py cannot be measured on the existing fixtures alone. This script
builds the missing thing: a scene with KNOWN GROUND TRUTH. A synthetic camera is
placed on the Fehmarn shore, synthetic vessels are placed in and out of its field of
view, and each EoContact's bearing and range are DERIVED from the vessel's true
position with realistic sensor noise injected. Because the pairing is known before
the matcher runs, the output is not just a count — it is a count of RIGHT and WRONG
pairings, which is the only number worth reporting.

A request is filed to ARCH (99_scratch/requests.md) to fold these generators into
tests/fixtures.py properly, at which point this file should be deleted rather than
maintained in parallel.

SYNTHETIC IDENTITIES ONLY. Every track is built through fixtures.make_ais_track, so
every MMSI stays in the unissued 999xxxxxx range and every name stays SYNTH-prefixed.
Nothing here can collide with a real vessel.

DETERMINISTIC. One seed, same scene every run, on every machine.

Run:  source .venv/bin/activate && python 99_scratch/lane_c_association_check.py
      add --capacity to also re-measure the frustum contact capacity (~45 s)
"""

from __future__ import annotations

import random
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
from pyproj import Geod

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "03_src", _ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import association as A                                        # noqa: E402
from fixtures import T0, make_ais_track, make_eo_contact       # noqa: E402
from fixtures import make_ais_population, make_eo_population   # noqa: E402

_GEOD = Geod(ellps="WGS84")
_KN_TO_MS = 0.514444

# --------------------------------------------------------------------------------
# The synthetic camera. Puttgarden, on Fehmarn's north shore, looking due north
# across the Belt towards Rodby — the T1 demo geometry. 60 deg horizontal field of
# view matches the 1920x1080 capture already measured on the Mac; 8 km is a generous
# but defensible detection horizon for a ship-sized target on a coastal camera.
# --------------------------------------------------------------------------------
CAM_LAT, CAM_LON = 54.5050, 11.2200
BORESIGHT_DEG = 0.0
FOV_HALF_DEG = 30.0
MAX_RANGE_M = 8000.0


# How far apart two ground-truth vessels must be, measured in the SAME metric the
# matcher uses (dof-corrected equivalent sigma over bearing and range together).
#
# Why not metres, and why not degrees. Both were tried and both are wrong. Metres:
# the first run placed a dark vessel 489 m from an unrelated silent claim and the
# matcher paired them — 489 m is far in a harbour and nothing at 5.6 km. Degrees: the
# second run enforced 4 deg of angular separation and STILL produced a wrong pairing,
# because 4 deg is only 2.7 sigma at this camera's 1.5 deg bearing error, and then
# observation noise closed another degree of it. Ground truth has to clear the
# matcher's 3-sigma gate PLUS the noise that will be added on top, so the separation
# is expressed in sigmas and set above the gate.
MIN_TRUTH_SEPARATION_SIGMA = 4.0

# Physical plausibility floor. Two hulls do not occupy the same water.
MIN_SEPARATION_M = 400.0

# Worst-case sensor errors from _observe(), used to measure truth separation. Worst
# case, not typical, so the separation guarantee holds for every vessel in the scene.
NOMINAL_BEARING_SIGMA_DEG = 1.5
NOMINAL_RANGE_SIGMA_FRAC = 0.12

# Vessels are kept off the frustum edge, where FOV_MARGIN_DEG slack would make
# in-view/out-of-view a coin toss rather than a test.
PLACEMENT_HALF_ANGLE_DEG = 28.0


def _place(bearing_deg: float, range_m: float) -> tuple[float, float]:
    """True position of a vessel at this bearing and range from the camera."""
    lon, lat, _ = _GEOD.fwd(CAM_LON, CAM_LAT, bearing_deg, range_m)
    return float(np.asarray(lat).ravel()[0]), float(np.asarray(lon).ravel()[0])


def _truth_separation_sigma(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Separation between two true (bearing, range) positions, in the matcher's own
    equivalent-sigma metric. Errors on the two vessels are independent, hence the
    sqrt(2) on each sigma."""
    d_brg = ((a[0] - b[0] + 180.0) % 360.0) - 180.0
    d_rng = a[1] - b[1]
    s_brg = NOMINAL_BEARING_SIGMA_DEG * np.sqrt(2.0)
    s_rng = NOMINAL_RANGE_SIGMA_FRAC * 0.5 * (a[1] + b[1]) * np.sqrt(2.0)
    chi2 = (d_brg / s_brg) ** 2 + (d_rng / s_rng) ** 2
    return float(A._equivalent_sigma(np.array(chi2), np.array(2.0)))


def _greedy_place(rng, n_max: int, tries: int):
    """Place as many mutually resolvable vessels as fit, up to n_max.

    Greedy rejection sampling in the measurement metric. Returns whatever it managed,
    so the same routine both builds the scene and measures the frustum's capacity —
    one implementation, so the capacity number cannot drift from the scene."""
    placed: list[tuple[float, float]] = []
    coords: list[tuple[float, float]] = []
    for _ in range(tries):
        if len(placed) >= n_max:
            break
        brg = (BORESIGHT_DEG + rng.uniform(-PLACEMENT_HALF_ANGLE_DEG,
                                           PLACEMENT_HALF_ANGLE_DEG)) % 360.0
        rngm = rng.uniform(1200.0, 7000.0)
        cand = (brg if brg <= 180.0 else brg - 360.0, rngm)   # unwrapped for arithmetic
        if any(_truth_separation_sigma(cand, p) < MIN_TRUTH_SEPARATION_SIGMA
               for p in placed):
            continue
        lat, lon = _place(brg, rngm)
        if any(float(np.asarray(_GEOD.inv(lon, lat, c[1], c[0])[2]).ravel()[0])
               < MIN_SEPARATION_M for c in coords):
            continue
        placed.append(cand)
        coords.append((lat, lon))
    return [(p[0] % 360.0, p[1], c[0], c[1]) for p, c in zip(placed, coords)]


def _place_resolvable(rng, n: int, tries: int = 4000):
    """Exactly n mutually resolvable positions inside the frustum, or fail loudly.

    The failure is informative, not just an error: it means the frustum physically
    cannot hold n contacts this camera could tell apart."""
    out = _greedy_place(rng, n, tries)
    if len(out) < n:
        raise RuntimeError(
            f"could only place {len(out)} of {n} mutually resolvable vessels in "
            f"a {2*PLACEMENT_HALF_ANGLE_DEG:.0f} deg x 1.2-7.0 km frustum at "
            f"{MIN_TRUTH_SEPARATION_SIGMA} sigma")
    return out


def measure_frustum_capacity(seed: int = 3, attempts: int = 8) -> int:
    """How many mutually resolvable contacts this camera can hold at once.

    A measured property of the geometry, not a tuning knob: the answer to "how
    crowded can the water get before this sensor stops being able to tell two hulls
    apart". It belongs in the honest-limits half of criterion 4 — beyond this
    density, association degrades to ambiguity flags and the tool should say so
    rather than keep emitting confident pairings.

    MEASURED: 13 for this geometry (60 deg FOV, 1.2-7.0 km, 4 sigma separation).
    Not a coincidence that it is small — it is bearing-limited, and the fix is a
    narrower FOV or a better range estimate, not a better matcher."""
    return max(len(_greedy_place(random.Random(seed * 1000 + a), 10**6, 20000))
               for a in range(attempts))


def _observe(seed, contact_id, true_brg, true_rng, rng):
    """Turn a TRUE position into what the camera actually reports.

    The noise model is the honest part: bearing error is sub-degree because a
    calibrated pixel column maps to a tight angle, range error is ~12% because it is
    monocular. Those are the same figures association.py scores against, so this
    tests the matcher, not the noise model — a mismatch between the two would show
    up as an inflated false-pairing rate."""
    sigma_b = rng.uniform(0.4, 1.5)
    sigma_r_frac = 0.12
    return make_eo_contact(
        seed=seed,
        contact_id=contact_id,
        frame_time_utc=T0,
        observed_bearing_deg_true=round((true_brg + rng.gauss(0.0, sigma_b)) % 360.0, 4),
        bearing_uncertainty_deg=round(sigma_b, 3),
        observed_range_m=round(true_rng * (1.0 + rng.gauss(0.0, sigma_r_frac)), 1),
        range_uncertainty_m=round(true_rng * sigma_r_frac, 1),
    )


def _claim(seed, track_id, true_lat, true_lon, rng):
    """Turn a TRUE position into what the transponder broadcast a moment earlier.

    The report is back-dated 5-60 s and the claimed position is the true position
    rolled BACKWARD along the vessel's course, plus GNSS noise. So the matcher only
    succeeds if its dead-reckoning is right: if the DR sign were flipped, every
    vessel would be projected to twice its offset and the match rate would collapse.
    That is the point of building it this way rather than claiming the true position
    directly."""
    dt_s = rng.uniform(5.0, 60.0)
    sog_kn = rng.uniform(6.0, 14.0)
    cog = rng.uniform(0.0, 359.9)
    back_m = sog_kn * _KN_TO_MS * dt_s
    lon_r, lat_r, _ = _GEOD.fwd(true_lon, true_lat, (cog + 180.0) % 360.0, back_m)
    lat_r = float(np.asarray(lat_r).ravel()[0]) + rng.gauss(0.0, 15.0) / 111_320.0
    lon_r = float(np.asarray(lon_r).ravel()[0]) + rng.gauss(0.0, 15.0) / 64_600.0
    return make_ais_track(
        seed=seed,
        track_id=track_id,
        report_time_utc=T0 - timedelta(seconds=dt_s),
        claimed_lat_deg=round(lat_r, 6),
        claimed_lon_deg=round(lon_r, 6),
        claimed_sog_kn=round(sog_kn, 1),
        claimed_cog_deg_true=round(cog, 1),
    )


def build_scene(seed: int = 7):
    """A scene with known ground truth.

    Composition, and what each group proves:
      4 paired vessels      broadcasting AND visible -> must MATCH, correctly
      2 dark vessels        visible, no transponder  -> must be DARK candidates
      2 silent claims       in view, no observation  -> position-spoof candidates

    Eight in-frustum vessels, not more: see measure_frustum_capacity(). All eight are
    placed in ONE pass so the separation guarantee holds ACROSS the groups. The
    accidental dark-vessel/silent-claim collision in the first run happened precisely
    because the groups were placed independently of each other.
     14 out-of-view claims  behind the camera or beyond the horizon -> must be
                            EXCLUDED, not reported. This group is the criterion 4
                            control: if any of them surfaces as a spoof candidate,
                            the tool is inventing suspects out of geometry it never
                            looked at."""
    rng = random.Random(seed)
    contacts, tracks, truth = [], [], {}

    # All eleven in-frustum vessels are placed in one stratified pass so the minimum
    # angular gap holds ACROSS the groups, not just within them. The accidental
    # dark-vessel/silent-claim collision in the first run happened precisely because
    # the groups were placed independently.
    slots = _place_resolvable(rng, 8)

    # -- 4 true pairs ---------------------------------------------------------
    for k in range(4):
        brg, rngm, lat, lon = slots[k]
        cid, tid = f"eo-P{k:02d}", f"ais-P{k:02d}"
        contacts.append(_observe(100 + k, cid, brg, rngm, rng))
        tracks.append(_claim(200 + k, tid, lat, lon, rng))
        truth[cid] = tid

    # -- 3 dark vessels: seen, never claimed ----------------------------------
    for k in range(2):
        brg, rngm, lat, lon = slots[4 + k]
        cid = f"eo-D{k:02d}"
        contacts.append(_observe(300 + k, cid, brg, rngm, rng))
        truth[cid] = None

    # -- 2 in-view claims with nothing seen -----------------------------------
    silent = []
    for k in range(2):
        brg, rngm, lat, lon = slots[6 + k]
        tid = f"ais-S{k:02d}"
        tracks.append(_claim(400 + k, tid, lat, lon, rng))
        silent.append(tid)

    # -- 14 claims the camera cannot see --------------------------------------
    out_of_view = []
    for k in range(14):
        if k % 2 == 0:                       # behind or beside the camera
            brg = (BORESIGHT_DEG + rng.uniform(60.0, 300.0)) % 360.0
            rngm = rng.uniform(1500.0, 7000.0)
        else:                                # ahead but beyond the horizon
            brg = (BORESIGHT_DEG + rng.uniform(-25.0, 25.0)) % 360.0
            rngm = rng.uniform(12000.0, 30000.0)
        lat, lon = _place(brg, rngm)
        tid = f"ais-X{k:02d}"
        tracks.append(_claim(500 + k, tid, lat, lon, rng))
        out_of_view.append(tid)

    return contacts, tracks, truth, silent, out_of_view


def build_coincidence_scene(seed: int = 13, separation_m: float = 150.0):
    """A DARK vessel and an unrelated SILENT claim, close enough to be confusable.

    Reproduces the case the first run of this script stumbled into by accident. One
    vessel is running without a transponder; a different vessel is broadcasting but
    was not detected (occluded, or missed by the detector). They are 150 m apart at
    ~5.6 km — 1.5 deg, inside the camera's own bearing error. There is exactly ONE
    feasible claim for the observation, so there is no rival to raise the ambiguity
    flag, and association.py pairs them.

    MEASURED BREAKDOWN POINT: swept over separation, the wrong pairing survives the
    gate at 150 m and is rejected from 250 m outward. So the confusion window for
    this geometry is under ~250 m of cross-range separation at 5.6 km. The sweep is
    printed by the script so the number stays measured rather than remembered.

    THE PAIRING IS WRONG AND ASSOCIATION CANNOT KNOW IT. That is not a defect to be
    patched here — no geometric matcher can separate two hulls closer than its own
    range error. What defends against it is downstream: the dark vessel's OBSERVED
    class and length come from a different ship than the one making the claim, so
    consistency.py sees a class/length mismatch and the pair surfaces as a SPOOF
    rather than a MATCH. The operator is then told to look, which is the correct
    outcome from a wrong pairing.

    What WOULD fix it at this layer: a better range estimate. Range is the binding
    constraint everywhere in this module — bearing is already good enough."""
    rng = random.Random(seed)
    brg, rngm = 350.0, 5600.0
    lat, lon = _place(brg, rngm)
    # Offset the claim across the line of sight, where bearing error dominates.
    d_brg = np.degrees(separation_m / rngm)
    lat2, lon2 = _place(brg + d_brg, rngm)
    dark_contact = _observe(950, "eo-DARK", brg, rngm, rng)
    silent_claim = _claim(960, "ais-SILENT", lat2, lon2, rng)
    return [dark_contact], [silent_claim]


def build_ambiguity_scene(seed: int = 11):
    """One observation, two claims 60 m apart at ~4 km.

    At that range the two claims are 0.86 deg apart in bearing — comparable to the
    camera's own bearing error. There is no honest way to tell which hull was seen,
    so the required behaviour is a match FLAGGED AMBIGUOUS, not a confident pick.
    Pointing a boarding team at the wrong one of two adjacent hulls is the specific
    failure this whole tool exists to prevent."""
    rng = random.Random(seed)
    brg, rngm = 8.0, 4000.0
    lat, lon = _place(brg, rngm)
    lat2, lon2 = _place(brg + 0.86, rngm)
    contact = _observe(900, "eo-AMB", brg, rngm, rng)
    t1 = _claim(910, "ais-AMB-a", lat, lon, rng)
    t2 = _claim(911, "ais-AMB-b", lat2, lon2, rng)
    return [contact], [t1, t2]


def build_uncalibrated_scene(seed: int = 17):
    """A contact whose bearing is uninformative, sitting where a claim really is.

    Lane B's uncalibrated_benchmark_pose() sets yaw_uncertainty_deg = 180 on purpose,
    and eo_detector folds yaw into bearing_uncertainty_deg — so an uncalibrated run
    reaches association.py as a 180-degree bearing sigma. Follow the chain: a
    180-degree sigma makes every bearing residual ~0 sigma, the bearing term vanishes
    from chi2, and the pairing is decided by RANGE ALONE. Range here agrees exactly,
    so without a guard this returns a CONFIDENT match on a run lane B marked as not
    evidence.

    Required behaviour: no match, and the contact emitted unmatched-AND-ambiguous —
    not as a dark vessel, because nothing was established either way."""
    rng = random.Random(seed)
    brg, rngm = 5.0, 4000.0
    lat, lon = _place(brg, rngm)
    contact = _observe(970, "eo-UNCAL", brg, rngm, rng).model_copy(
        update={"bearing_uncertainty_deg": 180.0})
    return [contact], [_claim(980, "ais-UNCAL", lat, lon, rng)]


def _run(contacts, tracks):
    return A.associate(
        contacts, tracks,
        camera_lat_deg=CAM_LAT, camera_lon_deg=CAM_LON,
        boresight_deg_true=BORESIGHT_DEG,
        fov_half_angle_deg=FOV_HALF_DEG,
        max_range_m=MAX_RANGE_M,
    )


def main() -> int:
    failures = []

    # ============================ scene 1 ====================================
    contacts, tracks, truth, silent, out_of_view = build_scene()
    assocs = _run(contacts, tracks)

    matched = [a for a in assocs if a.contact_id and a.track_id]
    dark = [a for a in assocs if a.track_id is None]
    spoof = [a for a in assocs if a.contact_id is None]

    correct = [a for a in matched if truth.get(a.contact_id) == a.track_id]
    wrong = [a for a in matched if truth.get(a.contact_id) != a.track_id]
    reported = {a.track_id for a in spoof}
    leaked = sorted(reported & set(out_of_view))

    print("=" * 78)
    print("SCENE 1 — geometric ground truth, Fehmarn Belt")
    print(f"  camera {CAM_LAT:.4f} N {CAM_LON:.4f} E, boresight {BORESIGHT_DEG:03.0f} true, "
          f"FOV +/-{FOV_HALF_DEG:.0f} deg, max range {MAX_RANGE_M/1000:.0f} km")
    print(f"  input: {len(contacts)} EO contacts, {len(tracks)} AIS claims "
          f"({len(out_of_view)} of them outside the frustum by construction)")
    print("-" * 78)
    print(f"  MATCHED              {len(matched):>3}   (truth: 4)")
    print(f"    correct pairing    {len(correct):>3}")
    print(f"    WRONG pairing      {len(wrong):>3}")
    print(f"  DARK candidates      {len(dark):>3}   (truth: 2)")
    print(f"  SPOOF candidates     {len(spoof):>3}   (truth: 2)")
    print(f"  ambiguous flags      {sum(1 for a in assocs if a.assoc_ambiguous):>3}")
    print(f"  out-of-view claims leaked into findings: {len(leaked)}   (must be 0)")
    if matched:
        print(f"  assoc_score  min {min(a.assoc_score for a in matched):.3f}"
              f"  max {max(a.assoc_score for a in matched):.3f}")
        gaps = [a.spatial_gap_m for a in matched if a.spatial_gap_m is not None]
        if gaps:
            print(f"  spatial_gap_m  min {min(gaps):.0f}  median "
                  f"{sorted(gaps)[len(gaps)//2]:.0f}  max {max(gaps):.0f}"
                  "   <- monocular range error, NOT evidence of a position spoof")
        dts = [a.time_delta_s for a in matched if a.time_delta_s is not None]
        print(f"  time_delta_s   min {min(dts):.1f}  max {max(dts):.1f}"
              "   (negative = AIS report predates the frame, dead-reckoned forward)")

    if len(correct) != 4:
        failures.append(f"expected 4 correct pairings, got {len(correct)}")
    if wrong:
        failures.append(f"{len(wrong)} wrong pairings: "
                        + ", ".join(f"{a.contact_id}->{a.track_id}" for a in wrong))
    if {a.contact_id for a in dark} != {c for c, t in truth.items() if t is None}:
        failures.append("dark candidates are not exactly the unclaimed contacts")
    if reported != set(silent):
        failures.append(f"spoof candidates {sorted(reported)} != silent claims {sorted(silent)}")
    if leaked:
        failures.append(f"out-of-view claims leaked as spoof candidates: {leaked}")

    # ============================ scene 2 ====================================
    ac, at = build_ambiguity_scene()
    aa = _run(ac, at)
    am = [a for a in aa if a.contact_id and a.track_id]
    print()
    print("=" * 78)
    print("SCENE 2 — two claims 60 m apart at 4 km, one observation")
    print(f"  matched {len(am)}, "
          f"ambiguous={am[0].assoc_ambiguous if am else 'n/a'}, "
          f"score={am[0].assoc_score:.3f} runner_up={am[0].runner_up_score}"
          if am else "  matched 0")
    print(f"  candidate_count={am[0].candidate_count if am else 'n/a'}, "
          f"unmatched-claim rows emitted: {sum(1 for a in aa if a.contact_id is None)}")
    if not am:
        failures.append("ambiguity scene produced no match at all")
    elif not am[0].assoc_ambiguous:
        failures.append("ambiguity scene matched CONFIDENTLY — the ambiguity flag is "
                        "not wired up, and a wrong hull can be recommended")

    # ============================ scene 3 ====================================
    # A KNOWN, IRREDUCIBLE LIMITATION, reproduced on purpose. See the docstring.
    cc, ct = build_coincidence_scene()
    ca = _run(cc, ct)
    cm = [a for a in ca if a.contact_id and a.track_id]
    print()
    print("=" * 78)
    print("SCENE 3 — dark vessel 150 m from an unrelated silent claim")
    if cm:
        print(f"  matched {len(cm)}  score={cm[0].assoc_score:.3f}  "
              f"ambiguous={cm[0].assoc_ambiguous}  "
              f"candidate_count={cm[0].candidate_count}  "
              f"gap={cm[0].spatial_gap_m} m")
        print("  This pairing is WRONG in truth and association cannot know it: with")
        print("  one feasible claim there is no rival to flag. Two hulls closer than")
        print("  the sensor's own range error are not separable by geometry.")
        print("  DEFENCE IS DOWNSTREAM: the observed class/length belong to a")
        print("  different ship than the claim, so consistency.py raises a SPOOF and")
        print("  the operator is told to look. Verdict.py must ALSO defer on a low")
        print(f"  assoc_score — here {cm[0].assoc_score:.3f} — not only on the")
        print("  ambiguity flag.")
    else:
        print("  matched 0 — the gate rejected the pairing.")
    print("  separation sweep (where the confusion window closes):")
    for sep in (150.0, 250.0, 450.0, 800.0):
        sc_, st_ = build_coincidence_scene(separation_m=sep)
        sm_ = [a for a in _run(sc_, st_) if a.contact_id and a.track_id]
        print(f"    {sep:>6.0f} m  ->  "
              + (f"WRONG pairing survives, score {sm_[0].assoc_score:.3f}"
                 if sm_ else "gated out"))
    if cm and cm[0].assoc_score > 0.8:
        failures.append("coincidence scene produced a HIGH-confidence wrong pairing; "
                        "the gate or the sensor model is too permissive")

    # ============================ scene 4 ====================================
    # Integration guard against lane B's deliberate "not evidence" marker.
    uc, ut = build_uncalibrated_scene()
    ua = _run(uc, ut)
    um = [a for a in ua if a.contact_id and a.track_id]
    unm = [a for a in ua if a.contact_id and a.track_id is None]
    print()
    print("=" * 78)
    print("SCENE 4 — uncalibrated pose (bearing sigma 180 deg), range agrees exactly")
    print(f"  matched {len(um)}   unmatched-contact rows {len(unm)}"
          + (f"   ambiguous={unm[0].assoc_ambiguous} candidate_count={unm[0].candidate_count}"
             if unm else ""))
    print("  Required: NO match. A 180 deg bearing sigma removes the bearing term from")
    print("  chi2 and would otherwise let range alone carry a confident pairing on a")
    print("  run lane B deliberately marked as unusable for evidence.")
    if um:
        failures.append("uncalibrated-pose contact was MATCHED — lane B's 180 deg "
                        "'not evidence' marker is being read as a permissive gate")
    elif unm and not unm[0].assoc_ambiguous:
        failures.append("uncalibrated-pose contact emitted as a clean DARK candidate; "
                        "it must be flagged ambiguous, nothing was established")

    # ============================ scene 5 ====================================
    # The existing ARCH fixtures, run unchanged, to record what they actually do.
    fc, ft = make_eo_population(n=8, seed=0), make_ais_population(n=25, seed=0)
    fa = _run(fc, ft)
    fm = [a for a in fa if a.contact_id and a.track_id]
    print()
    print("=" * 78)
    print("SCENE 5 — tests/fixtures.py populations, unmodified")
    print(f"  input: {len(fc)} EO contacts, {len(ft)} AIS claims")
    print(f"  MATCHED {len(fm)}   DARK {sum(1 for a in fa if a.track_id is None)}   "
          f"SPOOF-candidate {sum(1 for a in fa if a.contact_id is None)}")
    print("  Bearings and positions in these fixtures are drawn from independent")
    print("  random streams, so there is no true pairing to find. A near-zero match")
    print("  count here is the CORRECT result, not a defect — it is what a matcher")
    print("  should do when nothing actually corresponds.")

    print()
    print("=" * 78)
    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS — all ground-truth expectations met")
    return 0


if __name__ == "__main__":
    if "--capacity" in sys.argv:
        # Slow (~45 s of rejection sampling), so opt-in. Last measured: 13.
        print(f"frustum contact capacity at {MIN_TRUTH_SEPARATION_SIGMA} sigma: "
              f"{measure_frustum_capacity(attempts=4)} simultaneous resolvable contacts")
    raise SystemExit(main())
