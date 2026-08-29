"""
99_scratch/lane_c_consistency_check.py — lane C's self-check for consistency.py.

WHY THIS ONE RUNS ON THE ARCH FIXTURES AND THE ASSOCIATION CHECK DID NOT
tests/fixtures.py varies CLASS and LENGTH while leaving position and bearing random.
That made it useless for testing geometry (see lane_c_association_check.py) and makes
it exactly right here: scenario_identity_spoof() IS the criterion 3 exemplar, built by
ARCH for this module. So every scenario below is either a stock fixture or a small,
stated override of one.

WHAT IS BEING DEMONSTRATED, IN ORDER
  1  MATCH               claim and observation agree -> zero mismatches
  2  IDENTITY SPOOF      the marks case: class AND length, two independent dimensions
  3  LOW-CONFIDENCE      the same spoof with a poor observation -> weakens, as it must
  4  CONFUSABLE PAIR     cargo claimed, tanker observed -> must NOT be a strong finding
  5  BOW/STERN FLIP      heading 180 deg out -> must NOT become evidence
  6  REAL TURN           heading 90 deg out -> must become evidence
  7  STALENESS           a heading delta a stale report explains -> flagged, downgraded
  8  SERVICE CRAFT       claimed 'unknown' -> uncomparable, never a mismatch
  9  CLASS A SILENCE     missing static block on Class A -> uncomparable AND suspicious
 10  YOLO-ONLY PATH      observed_class None -> uncomparable, not a false agreement

Scenarios 4, 5, 7, 8 and 9 are FALSE-POSITIVE controls. They matter more than the
detections: a detector that fires on all of them has detected nothing, and every one
of them is ordinary innocent traffic in the Fehmarn Belt.

SYNTHETIC IDENTITIES ONLY — everything is built through fixtures.make_ais_track, so
MMSIs stay in the unissued 999xxxxxx range and names stay SYNTH-prefixed.

Run:  source .venv/bin/activate && python 99_scratch/lane_c_consistency_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "03_src", _ROOT / "tests", Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import consistency as C                                          # noqa: E402
from fixtures import (                                           # noqa: E402
    make_association,
    make_ais_track,
    make_eo_contact,
    scenario_identity_spoof,
    scenario_match,
)

_failures: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        _failures.append(message)


def _run(track, contact, *, assoc_overrides=None):
    assoc = make_association(
        seed=1,
        contact_id=contact.contact_id,
        track_id=track.track_id,
        **(assoc_overrides or {}),
    )
    return assoc, C.compare(assoc, track, contact)


def _report(title: str, result: C.ConsistencyResult, note: str = "") -> None:
    print()
    print("=" * 78)
    print(title)
    if note:
        for line in note.splitlines():
            print("  " + line)
    print("-" * 78)
    if result.mismatches:
        print("  MISMATCHES")
        for m in result.mismatches:
            delta = "n/a (categorical)" if m.delta is None else f"{m.delta:+g} {m.unit}"
            tol = "n/a" if m.tolerance is None else f"{m.tolerance:g} {m.unit}"
            print(f"    {m.dimension:<8} {str(m.claimed_value):>10} claimed  vs  "
                  f"{str(m.observed_value):<10} observed")
            print(f"             delta {delta}   tolerance {tol}")
            print(f"             significance {m.significance:.2f} sigma   "
                  f"severity {m.severity.upper()}   "
                  f"obs_conf {m.observation_confidence:.2f}"
                  + ("   EXPLAINED BY STALENESS" if m.explained_by_staleness else ""))
    else:
        print("  MISMATCHES: none")
    if result.agreements:
        print("  AGREED: " + ", ".join(
            f"{a.dimension} ({a.significance:.2f}s)" for a in result.agreements))
    if result.uncomparable:
        print("  NOT COMPARABLE")
        for u in result.uncomparable:
            print(f"    {u.dimension:<8} {u.reason}"
                  + ("   [SUSPICIOUS ABSENCE]" if u.suspicious else ""))
    print(f"  -> strongest {C.strongest_significance(result):.2f} sigma, "
          f"{C.independent_dimension_count(result)} independent dimension(s) "
          f"at major or above")


# ================================================================================

def scenario_1_match():
    track, contact = scenario_match()
    _, r = _run(track, contact)
    _report("1. MATCH — fixtures.scenario_match()", r,
            "The boring, essential case. A detector that cannot produce a clean\n"
            "MATCH here has not detected anything anywhere else.")
    _check(not r.mismatches,
           f"MATCH scenario produced {len(r.mismatches)} mismatch(es): "
           + ", ".join(f"{m.dimension}@{m.significance:.2f}" for m in r.mismatches))


def scenario_2_identity_spoof():
    track, contact = scenario_identity_spoof()
    _, r = _run(track, contact)
    _report("2. IDENTITY SPOOF — fixtures.scenario_identity_spoof()  *** CRITERION 3 ***", r,
            "AIS claims a 40 m fishing vessel. The camera sees a 248 m tanker.\n"
            "Both cannot be true. The case has to rest on TWO independent dimensions:\n"
            "a single flag is argued away as classifier error, two that fail for\n"
            "unrelated reasons is not.\n"
            "NOTE: this fixture controls CLASS and LENGTH only. Its heading is drawn\n"
            "at random, so any heading finding below is fixture noise, not a signal.")
    dims = {m.dimension for m in r.mismatches}
    _check("class" in dims, "identity spoof did not produce a CLASS mismatch")
    _check("length" in dims, "identity spoof did not produce a LENGTH mismatch")
    _check(C.independent_dimension_count(r) >= 2,
           f"identity spoof produced only {C.independent_dimension_count(r)} "
           "independent major+ dimension(s); the case needs two")
    _check(any(m.severity == "critical" for m in r.mismatches),
           "identity spoof produced no CRITICAL mismatch")
    return r


def scenario_3_low_confidence(strong: C.ConsistencyResult):
    """The variant tests/fixtures.py explicitly asks lane C to build."""
    track, contact = scenario_identity_spoof()
    contact = contact.model_copy(update={
        "observed_class_confidence": 0.55,     # the classifier is barely guessing
        "observed_length_uncertainty_m": 90.0,  # and the range estimate is poor
    })
    _, r = _run(track, contact)
    _report("3. THE SAME SPOOF, SEEN BADLY — low confidence variant", r,
            "fixtures.scenario_identity_spoof's docstring asks for this one by name.\n"
            "Identical claim, identical hull, worse observation. The evidence MUST\n"
            "weaken. If it does not, the pipeline is reporting the fixture rather\n"
            "than the measurement, and every confidence downstream is a decoration.")
    print(f"  strong observation: {C.strongest_significance(strong):.2f} sigma"
          f"   ->   weak observation: {C.strongest_significance(r):.2f} sigma")
    _check(C.strongest_significance(r) < C.strongest_significance(strong),
           "a WORSE observation did not reduce the strongest significance — "
           "confidence is not propagating")
    cls = [m for m in r.mismatches if m.dimension == "class"]
    _check(all(m.severity != "critical" for m in cls),
           "a 0.55-confidence class observation still produced a CRITICAL mismatch; "
           "the confidence blend is not capping class evidence")


def scenario_4_confusable_pair():
    track = make_ais_track(seed=11, claimed_ship_type="cargo", claimed_length_m=180.0,
                           claimed_heading_deg_true=90.0, claimed_sog_kn=11.0)
    contact = make_eo_contact(seed=11, observed_class="tanker",
                              observed_class_confidence=0.82,
                              observed_length_m=176.0,
                              observed_length_uncertainty_m=28.0,
                              observed_heading_deg_true=92.0,
                              observed_speed_ms=5.7,
                              track_length_frames=25)
    _, r = _run(track, contact)
    _report("4. CONFUSABLE PAIR — cargo claimed, tanker observed  [FALSE-POSITIVE CONTROL]", r,
            "At 2-8 km a cargo ship and a tanker are both long grey boxes. The length\n"
            "agrees. This is ordinary classifier error on innocent traffic and it must\n"
            "NOT produce a strong finding — the Fehmarn Belt is full of these, and a\n"
            "tool that flags them all has flagged nothing.")
    cls = [m for m in r.mismatches if m.dimension == "class"]
    _check(all(m.severity != "critical" for m in cls),
           "cargo-vs-tanker produced a CRITICAL class mismatch; the confusability "
           "matrix is not doing its job and innocent traffic will be flagged")
    _check(C.independent_dimension_count(r) < 2,
           "cargo-vs-tanker reached two independent major dimensions — that is a "
           "spoof-strength case built from one classifier slip")


def scenario_5_bow_stern_flip():
    track = make_ais_track(seed=21, claimed_ship_type="cargo", claimed_length_m=150.0,
                           claimed_heading_deg_true=45.0, claimed_sog_kn=10.0)
    contact = make_eo_contact(seed=21, observed_class="cargo",
                              observed_class_confidence=0.85,
                              observed_length_m=147.0,
                              observed_length_uncertainty_m=25.0,
                              observed_heading_deg_true=225.0,   # exactly reversed
                              observed_speed_ms=5.2,
                              track_length_frames=20)
    _, r = _run(track, contact)
    _report("5. BOW/STERN FLIP — heading exactly 180 deg out  [FALSE-POSITIVE CONTROL]", r,
            "A side-on silhouette is nearly symmetric and a classifier can read it\n"
            "backwards. Scored through a mixture likelihood, so a 180 deg disagreement\n"
            "is mostly explained by the flip and stays out of the evidence card.\n"
            "This is the silent 180-degree error LIBRARIES.md warns about, made loud.")
    hdg = [m for m in r.mismatches if m.dimension in ("heading", "cog")]
    _check(not hdg,
           ("a 180 deg bow/stern flip produced a heading mismatch at "
            f"{hdg[0].significance:.2f} sigma — every side-on vessel will be accused")
           if hdg else "")


def scenario_6_real_turn():
    track = make_ais_track(seed=22, claimed_ship_type="cargo", claimed_length_m=150.0,
                           claimed_heading_deg_true=45.0, claimed_sog_kn=10.0,
                           claimed_rot_deg_per_min=0.0)
    contact = make_eo_contact(seed=22, observed_class="cargo",
                              observed_class_confidence=0.85,
                              observed_length_m=147.0,
                              observed_length_uncertainty_m=25.0,
                              observed_heading_deg_true=135.0,   # 90 deg off
                              observed_speed_ms=5.2,
                              track_length_frames=20)
    _, r = _run(track, contact, assoc_overrides={"time_delta_s": -8.0})
    _report("6. REAL ORIENTATION MISMATCH — heading 90 deg out, 8 s old claim", r,
            "The counterpart to scenario 5. 90 degrees is not a flip and 8 seconds is\n"
            "not enough for a 150 m hull to turn. The mixture must NOT explain this\n"
            "away, or the bow/stern handling has cost us the dimension entirely.")
    hdg = [m for m in r.mismatches if m.dimension in ("heading", "cog")]
    _check(bool(hdg),
           "a 90 deg heading disagreement produced NO mismatch — the bow/stern "
           "mixture has swallowed a real finding")
    if hdg:
        _check(not hdg[0].explained_by_staleness,
               "a 90 deg turn in 8 s was credited to staleness — a 150 m hull "
               "cannot turn that fast and the turn-rate budget is too generous")


def scenario_7_staleness():
    track = make_ais_track(seed=23, claimed_ship_type="small_craft",
                           claimed_length_m=22.0, claimed_heading_deg_true=10.0,
                           claimed_sog_kn=8.0)
    contact = make_eo_contact(seed=23, observed_class="small_craft",
                              observed_class_confidence=0.80,
                              observed_length_m=24.0,
                              observed_length_uncertainty_m=5.0,
                              observed_heading_deg_true=90.0,    # 80 deg off
                              observed_speed_ms=4.1,
                              track_length_frames=18)
    _, r = _run(track, contact, assoc_overrides={"time_delta_s": -120.0})
    _report("7. STALENESS — 80 deg heading delta on a 22 m craft, 120 s old claim", r,
            "A small craft can turn 2 deg/s, so 120 seconds buys 240 degrees. This\n"
            "disagreement is fully explained by the gap. contracts.py: flag it BEFORE\n"
            "it reaches the verdict. Note the finding is KEPT and downgraded, not\n"
            "deleted — the record shows what was seen and why it was discounted.")
    hdg = [m for m in r.mismatches if m.dimension in ("heading", "cog")]
    if hdg:
        _check(hdg[0].explained_by_staleness,
               "an 80 deg delta on a small craft with a 120 s old claim was NOT "
               "flagged explained_by_staleness")
        _check(hdg[0].severity == "minor",
               f"a staleness-explained heading mismatch kept severity "
               f"{hdg[0].severity}; it must be downgraded")


def scenario_8_service_craft():
    track = make_ais_track(seed=31, claimed_ship_type="unknown",
                           claimed_length_m=48.0, claimed_heading_deg_true=200.0,
                           claimed_sog_kn=9.0, mobile_class="Class A")
    contact = make_eo_contact(seed=31, observed_class="tug",
                              observed_class_confidence=0.90,
                              observed_length_m=46.0,
                              observed_length_uncertainty_m=8.0,
                              observed_heading_deg_true=202.0,
                              observed_speed_ms=4.6,
                              track_length_frames=30)
    _, r = _run(track, contact)
    _report("8. SERVICE CRAFT — AIS ship type maps to 'unknown'  [FALSE-POSITIVE CONTROL]", r,
            "Lane A maps every service-craft type with no honest silhouette equivalent\n"
            "(pilot, SAR, dredger, port tender, law enforcement) onto 'unknown' —\n"
            "29.6% of rows in the measured slice. Comparing that against an observed\n"
            "class would manufacture SPOOF verdicts against a third of real traffic.")
    _check(not any(m.dimension == "class" for m in r.mismatches),
           "a claimed ship type of 'unknown' produced a CLASS mismatch — this would "
           "accuse 29.6% of the measured Fehmarn traffic")
    _check(any(u.dimension == "class" for u in r.uncomparable),
           "'unknown' ship type was not recorded as uncomparable, so the report "
           "cannot say which check it failed to run")


def scenario_9_class_a_silence():
    track = make_ais_track(seed=41, claimed_ship_type=None, claimed_length_m=None,
                           claimed_heading_deg_true=None, claimed_cog_deg_true=None,
                           claimed_sog_kn=None, mobile_class="Class A")
    contact = make_eo_contact(seed=41, observed_class="tanker",
                              observed_class_confidence=0.88,
                              observed_length_m=210.0,
                              observed_length_uncertainty_m=26.0,
                              observed_heading_deg_true=310.0,
                              observed_speed_ms=6.0,
                              track_length_frames=28)
    _, r = _run(track, contact)
    _report("9. CLASS A SILENCE — a Class A transponder sending no static block", r,
            "Not a mismatch: there is nothing to compare against. But contracts.py is\n"
            "explicit that a missing claim is ITSELF a finding, and a blank static\n"
            "block is normal on Class B and odd on Class A. It has to reach the\n"
            "verdict as a defer reason and a weak positive indicator, not vanish.")
    _check(not r.mismatches,
           "an all-None claim produced mismatches — something is comparing against "
           "a missing field")
    _check(any(u.suspicious for u in r.uncomparable),
           "a Class A transponder with no static block was not marked suspicious")
    _check(len(r.uncomparable) >= 3,
           f"only {len(r.uncomparable)} uncomparable dimension(s) recorded; the "
           "report must be able to say every check it could not run")


def scenario_10_yolo_only():
    track, contact = scenario_identity_spoof()
    contact = contact.model_copy(update={"observed_class": None,
                                         "observed_class_confidence": None})
    _, r = _run(track, contact)
    _report("10. YOLO-ONLY PATH — observed_class is None  [FALSE-POSITIVE CONTROL]", r,
            "Lane B's detector always emits observed_class=None: COCO has one maritime\n"
            "class, 'boat', and guessing a type from it would hand this module a\n"
            "comparison that was never actually made. Class must come back\n"
            "uncomparable — and note LENGTH still carries the spoof on its own.")
    _check(not any(m.dimension == "class" for m in r.mismatches),
           "a None observed_class produced a class mismatch")
    _check(any(u.dimension == "class" for u in r.uncomparable),
           "a None observed_class was not recorded as uncomparable")
    _check(any(m.dimension == "length" for m in r.mismatches),
           "with class unavailable, LENGTH should still carry the identity spoof")


def scenario_11_rejects_unmatched():
    """compare() must refuse an unmatched association rather than return empty."""
    track, contact = scenario_match()
    assoc = make_association(seed=1, contact_id=contact.contact_id, track_id=None)
    try:
        C.compare(assoc, track, contact)
    except ValueError:
        ok = True
    else:
        ok = False
    print()
    print("=" * 78)
    print("11. UNMATCHED ASSOCIATION — compare() must raise, not return empty")
    print(f"  raised ValueError: {ok}")
    print("  An empty result and 'there was nothing to compare' must never look the")
    print("  same — that is the difference between a MATCH and an unexamined vessel.")
    _check(ok, "compare() accepted an unmatched association instead of raising")


def scenario_12_end_to_end():
    """The whole criterion-3 chain on ONE vessel: geometry in, evidence out.

    Everything above feeds consistency.py Associations built by hand, which proves the
    comparisons and proves nothing about the seam. Here a spoofing vessel is placed on
    the water at a known bearing and range, association.py finds it by geometry, and
    consistency.py is handed the REAL Association — including the real time_delta_s
    that drives the staleness budget.

    The spoof: the transponder claims a 40 m fishing vessel and broadcasts its true
    position, so it associates cleanly and a position check would find nothing. The
    camera sees a 250 m tanker. This is the case the brief calls the discriminator and
    it is the one to put on screen."""
    import lane_c_association_check as G
    import association as A

    brg, rngm = 6.0, 4200.0
    lat, lon = G._place(brg, rngm)
    import random
    rng = random.Random(99)

    honest_track = G._claim(700, "ais-HONEST", *G._place(-14.0, 2600.0), rng)
    honest_contact = G._observe(701, "eo-HONEST", -14.0 % 360.0, 2600.0, rng)
    honest_contact = honest_contact.model_copy(update={
        "observed_class": honest_track.claimed_ship_type,
        "observed_class_confidence": 0.87,
        "observed_length_m": honest_track.claimed_length_m,
        "observed_length_uncertainty_m": 0.15 * (honest_track.claimed_length_m or 100.0),
    })

    spoof_track = G._claim(710, "ais-SPOOF", lat, lon, rng).model_copy(update={
        "claimed_ship_type": "fishing",
        "claimed_length_m": 40.0,
        "claimed_width_m": 8.0,
    })
    spoof_contact = G._observe(711, "eo-SPOOF", brg, rngm, rng).model_copy(update={
        "observed_class": "tanker",
        "observed_class_confidence": 0.88,
        "observed_length_m": 250.0,
        "observed_length_uncertainty_m": 32.0,
        "track_length_frames": 30,
    })

    contacts = [honest_contact, spoof_contact]
    tracks = [honest_track, spoof_track]
    assocs = A.associate(contacts, tracks,
                         camera_lat_deg=G.CAM_LAT, camera_lon_deg=G.CAM_LON,
                         boresight_deg_true=G.BORESIGHT_DEG,
                         fov_half_angle_deg=G.FOV_HALF_DEG,
                         max_range_m=G.MAX_RANGE_M)
    results = C.compare_all(assocs, tracks, contacts)

    print()
    print("=" * 78)
    print("12. END TO END — associate() then compare_all(), one honest hull and one spoof")
    print("  Both vessels broadcast their TRUE position, so both associate cleanly and")
    print("  a position check finds nothing on either. Only the claimed-vs-observed")
    print("  identity comparison separates them. That is criterion 3 in one screen.")
    print("-" * 78)
    matched = [a for a in assocs if a.contact_id and a.track_id]
    print(f"  association: {len(matched)} matched, "
          f"{sum(1 for a in assocs if a.track_id is None)} dark, "
          f"{sum(1 for a in assocs if a.contact_id is None)} spoof-candidate")
    for a in matched:
        r = results[a.association_id]
        print(f"  {a.contact_id} <-> {a.track_id}  "
              f"assoc_score {a.assoc_score:.3f}  dt {a.time_delta_s:+.1f}s  "
              f"gap {a.spatial_gap_m} m")
        print(f"      strongest {C.strongest_significance(r):.2f} sigma over "
              f"{C.independent_dimension_count(r)} independent major+ dimension(s): "
              + (", ".join(f"{m.dimension} {m.significance:.2f}s {m.severity}"
                           for m in r.mismatches) or "no mismatches"))

    by_contact = {a.contact_id: results[a.association_id] for a in matched}
    _check("eo-HONEST" in by_contact and "eo-SPOOF" in by_contact,
           "end-to-end: association did not match both vessels, so the comparison "
           "never ran on the seam it is meant to test")
    if "eo-HONEST" in by_contact and "eo-SPOOF" in by_contact:
        h, sp = by_contact["eo-HONEST"], by_contact["eo-SPOOF"]
        _check(C.independent_dimension_count(h) == 0,
               "end-to-end: the HONEST vessel produced a major finding — a false "
               "positive on ordinary traffic")
        _check(C.independent_dimension_count(sp) >= 2,
               "end-to-end: the SPOOF did not reach two independent major dimensions")
        _check(C.strongest_significance(sp) > C.strongest_significance(h),
               "end-to-end: the spoof did not outscore the honest vessel")


def main() -> int:
    strong = None
    scenario_1_match()
    strong = scenario_2_identity_spoof()
    scenario_3_low_confidence(strong)
    scenario_4_confusable_pair()
    scenario_5_bow_stern_flip()
    scenario_6_real_turn()
    scenario_7_staleness()
    scenario_8_service_craft()
    scenario_9_class_a_silence()
    scenario_10_yolo_only()
    scenario_11_rejects_unmatched()
    scenario_12_end_to_end()

    print()
    print("=" * 78)
    if _failures:
        print("FAIL")
        for f in _failures:
            print("  -", f)
        return 1
    print("PASS — all criterion 3 expectations and all false-positive controls met")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
