"""
99_scratch/lane_c_verdict_check.py — lane C's self-check for verdict.py.

WHAT IS BEING DEMONSTRATED
  1  MATCH                  agreeing pair -> MATCH, no defer
  2  IDENTITY SPOOF         the criterion 3 exemplar -> SPOOF, identity, stated
  3  SAME SPOOF SEEN BADLY  -> confidence collapses, defer_to_human True
  4  SINGLE DIMENSION       one 6-sigma finding alone -> must NOT be stated confidently
  5  STALENESS ONLY         every mismatch explained by staleness -> no spoof
  6  AMBIGUOUS ASSOCIATION  0.99 evidence off a contested pairing -> defer regardless
  7  COINCIDENTAL PAIRING   assoc_score 0.047 -> defer (no ambiguity flag to catch it)
  8  NOTHING COMPARABLE     Class A silence -> UNKNOWN, never a confident MATCH
  9  DARK, GOOD COVERAGE    -> DARK, stated
 10  DARK, SPARSE COVERAGE  -> DARK, sparse_ais_coverage, defer (the Bornholm caveat)
 11  UNOBSERVED CLAIM       in-view claim, nothing seen -> capped, always defers
 12  PRIOR SENSITIVITY      how each verdict moves across plausible priors
 13  END TO END             associate -> compare -> decide, honest hull vs spoof
 14  DETERMINISM            same input twice -> byte-identical verdict

Scenarios 4-8, 10 and 11 are the FALSE-CONFIDENCE controls. They matter more than the
detections: this module's job is to know when it does NOT know, and every one of them
is a way a detector talks itself into certainty it has not earned.

SYNTHETIC IDENTITIES ONLY — everything is built through fixtures.make_ais_track.

Run:  source .venv/bin/activate && python 99_scratch/lane_c_verdict_check.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "03_src", _ROOT / "tests", Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import consistency as C                                          # noqa: E402
import verdict as V                                              # noqa: E402
from fixtures import (                                           # noqa: E402
    make_association,
    make_ais_track,
    make_eo_contact,
    scenario_identity_spoof,
    scenario_match,
)

# Fixed so every run produces byte-identical verdicts.
T_DECIDE = datetime(2026, 8, 25, 14, 2, 30, tzinfo=timezone.utc)

_failures: list[str] = []


def _check(ok: bool, message: str) -> None:
    if not ok:
        _failures.append(message)


def _pipeline(track, contact, *, assoc_overrides=None, **decide_kwargs):
    assoc = make_association(seed=1, contact_id=contact.contact_id,
                             track_id=track.track_id, **(assoc_overrides or {}))
    result = C.compare(assoc, track, contact)
    v, ex = V.decide(assoc, result, decided_at_utc=T_DECIDE, **decide_kwargs)
    return assoc, result, v, ex


def _report(title: str, v, ex, note: str = "") -> None:
    print()
    print("=" * 78)
    print(title)
    if note:
        for line in note.splitlines():
            print("  " + line)
    print("-" * 78)
    print(f"  LABEL {v.label:<8} confidence {v.confidence:.3f}   "
          f"defer_to_human {v.defer_to_human}"
          + (f"   subtype {v.spoof_subtype}" if v.spoof_subtype else ""))
    if v.defer_reasons:
        print(f"  defer_reasons: {', '.join(v.defer_reasons)}")
    if ex.contributions:
        print(f"  posterior built from prior 1/{1/ex.prior:.0f}:")
        for c in ex.contributions:
            print(f"    {c.dimension:<8} {c.significance:>5.2f} sigma   "
                  f"P(disc|honest) {c.p_given_honest:.2e}   "
                  f"P(disc|spoof) {c.p_given_spoof:.2f}   "
                  f"LR {c.likelihood_ratio:.3g}   damping {c.damping:.2f}   "
                  f"{c.log10_contribution:+.2f} decades")
    print(f"  evidential coverage {ex.coverage_fraction*100:.0f}% of attempted comparisons")
    for n in ex.notes:
        print(f"  note: {n}")


# ================================================================================

def s1_match():
    track, contact = scenario_match()
    _, _, v, ex = _pipeline(track, contact)
    _report("1. MATCH — an agreeing pair", v, ex,
            "Claim and observation agree on class, length, heading and speed.")
    _check(v.label == "MATCH", f"agreeing pair got {v.label}, expected MATCH")
    _check(not v.defer_to_human,
           f"a clean MATCH deferred: {v.defer_reasons}")
    _check(v.confidence < 1.0, "MATCH confidence reached 1.0 — nothing here is certain")


def s2_identity_spoof():
    track, contact = scenario_identity_spoof()
    _, result, v, ex = _pipeline(track, contact)
    _report("2. IDENTITY SPOOF — the criterion 3 exemplar  *** THE MARKS CASE ***", v, ex,
            "AIS claims a 40 m fishing vessel. The camera sees a 248 m tanker.\n"
            "Two independent dimensions disagree, for unrelated reasons.")
    _check(v.label == "SPOOF", f"identity spoof got {v.label}, expected SPOOF")
    _check(v.spoof_subtype == "identity",
           f"spoof_subtype is {v.spoof_subtype}, expected identity")
    _check(v.confidence >= V.CONFIDENCE_STATE,
           f"identity spoof confidence {v.confidence:.3f} is below the "
           f"{V.CONFIDENCE_STATE} stating threshold")
    return result, v


def s3_seen_badly(strong_v):
    track, contact = scenario_identity_spoof()
    contact = contact.model_copy(update={"observed_class_confidence": 0.55,
                                         "observed_length_uncertainty_m": 90.0})
    _, _, v, ex = _pipeline(track, contact)
    _report("3. THE SAME SPOOF, SEEN BADLY  [FALSE-CONFIDENCE CONTROL]", v, ex,
            "Identical claim, identical hull, worse observation. tests/fixtures.py\n"
            "asks for this variant by name and says the verdict must flip to UNKNOWN\n"
            "with defer_to_human True. If it does not, the defer threshold is not\n"
            "wired up and every confidence downstream is a decoration.")
    print(f"  strong observation: {strong_v.label} {strong_v.confidence:.3f} "
          f"defer={strong_v.defer_to_human}")
    print(f"  weak   observation: {v.label} {v.confidence:.3f} defer={v.defer_to_human}")
    _check(v.defer_to_human,
           "the low-confidence variant did NOT set defer_to_human — this is the "
           "exact check tests/fixtures.py asks for")
    _check(v.confidence < strong_v.confidence or v.label != strong_v.label,
           "a worse observation produced an equally strong verdict")


def s4_single_dimension():
    """One big finding, alone. handoff_C.md item 3."""
    track = make_ais_track(seed=51, claimed_ship_type=None, claimed_length_m=40.0,
                           claimed_heading_deg_true=90.0, claimed_sog_kn=11.0,
                           mobile_class="Class B")
    contact = make_eo_contact(seed=51, observed_class=None,
                              observed_class_confidence=None,
                              observed_length_m=250.0,
                              observed_length_uncertainty_m=30.0,
                              observed_heading_deg_true=92.0,
                              observed_speed_ms=5.6, track_length_frames=25)
    _, _, v, ex = _pipeline(track, contact)
    _report("4. SINGLE DIMENSION — a 6-sigma length finding and nothing else  "
            "[FALSE-CONFIDENCE CONTROL]", v, ex,
            "No class claim, no class observation — length carries the whole case.\n"
            "One dimension is a classifier or range error waiting to be alleged.\n"
            "It must not be stated as a machine conclusion on its own.")
    _check(v.defer_to_human,
           f"a single-dimension case was stated without deferring "
           f"(confidence {v.confidence:.3f}, reasons {v.defer_reasons})")
    _check(v.confidence <= V.SINGLE_DIMENSION_CONFIDENCE_CAP + 1e-9,
           f"a single-dimension case reported confidence {v.confidence:.3f}, above "
           f"the {V.SINGLE_DIMENSION_CONFIDENCE_CAP} cap — criterion 2 ranks a patrol "
           "boat on this number and it would outrank a corroborated case")


def s5_staleness_only():
    track = make_ais_track(seed=52, claimed_ship_type="small_craft",
                           claimed_length_m=22.0, claimed_heading_deg_true=10.0,
                           claimed_sog_kn=8.0)
    contact = make_eo_contact(seed=52, observed_class="small_craft",
                              observed_class_confidence=0.80,
                              observed_length_m=23.0,
                              observed_length_uncertainty_m=4.0,
                              observed_heading_deg_true=90.0,
                              observed_speed_ms=4.1, track_length_frames=18)
    _, _, v, ex = _pipeline(track, contact,
                            assoc_overrides={"time_delta_s": -120.0})
    _report("5. STALENESS ONLY — every mismatch explained by a 120 s old claim  "
            "[FALSE-CONFIDENCE CONTROL]", v, ex,
            "A small craft can turn 2 deg/s, so 120 seconds buys 240 degrees.\n"
            "handoff_C.md item 5: a staleness-explained mismatch is kept in the\n"
            "record to show what was seen and why it was discounted — it never\n"
            "carries a verdict.")
    _check(v.label != "SPOOF",
           "a purely staleness-explained delta produced a SPOOF verdict")


def s6_ambiguous_association(strong_result):
    """Overwhelming evidence off a contested pairing. handoff_C.md item 2."""
    track, contact = scenario_identity_spoof()
    assoc = make_association(seed=1, contact_id=contact.contact_id,
                             track_id=track.track_id, assoc_ambiguous=True,
                             assoc_score=0.88, runner_up_score=0.80)
    result = C.compare(assoc, track, contact)
    v, ex = V.decide(assoc, result, decided_at_utc=T_DECIDE)
    _report("6. AMBIGUOUS ASSOCIATION — the same overwhelming evidence, wrong hull "
            "possible  [FALSE-CONFIDENCE CONTROL]", v, ex,
            "A rival claim fits this observation nearly as well. contracts.py: a\n"
            "mismatch computed off an ambiguous pairing is not evidence. Arithmetic\n"
            "cannot rescue this — 0.99 confident about possibly the wrong hull is\n"
            "still 0.99 confident about possibly the wrong hull.")
    _check(v.defer_to_human, "an ambiguous association did not defer")
    _check("ambiguous_association" in v.defer_reasons,
           f"ambiguous_association missing from defer_reasons: {v.defer_reasons}")
    _check(v.confidence <= V.UNCERTAIN_ASSOCIATION_CONFIDENCE_CAP + 1e-9,
           f"an ambiguous pairing reported confidence {v.confidence:.3f} — it would "
           "top the ranked list on evidence that may be about the wrong hull")


def s7_coincidental_pairing():
    """assoc_score 0.047 with NO ambiguity flag. handoff_C.md item 1."""
    track, contact = scenario_identity_spoof()
    assoc = make_association(seed=1, contact_id=contact.contact_id,
                             track_id=track.track_id, assoc_score=0.047,
                             assoc_ambiguous=False, candidate_count=1,
                             runner_up_score=None)
    result = C.compare(assoc, track, contact)
    v, ex = V.decide(assoc, result, decided_at_utc=T_DECIDE)
    _report("7. COINCIDENTAL PAIRING — assoc_score 0.047, no ambiguity flag  "
            "[FALSE-CONFIDENCE CONTROL]", v, ex,
            "The measured case from lane_c_association_check scene 3: a dark vessel\n"
            "and an unrelated silent claim, one feasible candidate so nothing raises\n"
            "the ambiguity flag. handoff_C.md item 1 exists because of this exact\n"
            "verdict.")
    _check(v.defer_to_human,
           f"a 0.047 association did not defer — the verdict is being read off a "
           f"pairing that is almost certainly wrong (reasons: {v.defer_reasons})")
    _check(v.confidence <= V.UNCERTAIN_ASSOCIATION_CONFIDENCE_CAP + 1e-9,
           f"a 0.047 association reported confidence {v.confidence:.3f}")


def s8_nothing_comparable():
    track = make_ais_track(seed=53, claimed_ship_type=None, claimed_length_m=None,
                           claimed_heading_deg_true=None, claimed_cog_deg_true=None,
                           claimed_sog_kn=None, mobile_class="Class A")
    contact = make_eo_contact(seed=53, observed_class="tanker",
                              observed_class_confidence=0.88,
                              observed_length_m=210.0,
                              observed_length_uncertainty_m=26.0,
                              observed_heading_deg_true=310.0,
                              observed_speed_ms=6.0, track_length_frames=28)
    _, _, v, ex = _pipeline(track, contact)
    _report("8. NOTHING COMPARABLE — Class A transponder sending no static block  "
            "[FALSE-CONFIDENCE CONTROL]", v, ex,
            "THE MOST DANGEROUS FAILURE MODE IN verdict.py. With no evidence, a\n"
            "Bayesian posterior just returns the prior — 99.8% honest — so without\n"
            "the coverage cap this vessel sails through as a high-confidence MATCH.\n"
            "It is not a match. It is an unexamined vessel.")
    _check(v.label == "UNKNOWN",
           f"a vessel with nothing comparable got {v.label} at {v.confidence:.3f} — "
           "the coverage cap is not holding and the prior is being reported as a "
           "clean bill of health")
    _check(v.defer_to_human, "an UNKNOWN verdict did not defer")
    _check(v.confidence >= 0.8,
           f"UNKNOWN reported confidence {v.confidence:.3f}. Confidence is confidence "
           "in the STATED LABEL, and the stated label is 'cannot determine' — a "
           "vessel we could not examine at all should be a CONFIDENT UNKNOWN, not a "
           "vague one, or an operator reads it as noise and skips it")


def s9_dark_good_coverage():
    contact = make_eo_contact(seed=54, track_length_frames=22)
    assoc = make_association(seed=9, contact_id=contact.contact_id, track_id=None,
                             assoc_score=0.0, assoc_ambiguous=False,
                             candidate_count=0, runner_up_score=None,
                             time_delta_s=None, spatial_gap_m=None)
    v, ex = V.decide(assoc, None, ais_coverage_confidence=0.88,
                     decided_at_utc=T_DECIDE)
    _report("9. DARK — observation with no claim, well-covered area", v, ex,
            "Fehmarn: 3,556 AIS messages per vessel on the measured demo day. Zero\n"
            "feasible candidates after the association gate is the cleanest dark\n"
            "signature available.")
    _check(v.label == "DARK", f"unmatched contact got {v.label}, expected DARK")
    _check("sparse_ais_coverage" not in v.defer_reasons,
           "sparse_ais_coverage fired in a well-covered area")


def s10_dark_sparse_coverage():
    contact = make_eo_contact(seed=55, track_length_frames=22)
    assoc = make_association(seed=10, contact_id=contact.contact_id, track_id=None,
                             assoc_score=0.0, assoc_ambiguous=False,
                             candidate_count=0, runner_up_score=None,
                             time_delta_s=None, spatial_gap_m=None)
    v, ex = V.decide(assoc, None, ais_coverage_confidence=0.45,
                     decided_at_utc=T_DECIDE)
    _report("10. DARK IN A COVERAGE HOLE — the Bornholm caveat  "
            "[FALSE-CONFIDENCE CONTROL]", v, ex,
            "MEASURED, same day, same file: Bornholm tracks are 2.5x sparser than\n"
            "Fehmarn (1,432 vs 3,556 messages per vessel) purely from basestation\n"
            "geometry. An AIS gap caused by coverage is not an AIS gap caused by a\n"
            "switched-off transponder, and this module cannot tell them apart.")
    _check("sparse_ais_coverage" in v.defer_reasons,
           f"sparse coverage did not raise sparse_ais_coverage: {v.defer_reasons}")
    _check(v.defer_to_human, "a dark finding in a coverage hole did not defer")


def s11_unobserved_claim():
    track = make_ais_track(seed=56)
    assoc = make_association(seed=11, contact_id=None, track_id=track.track_id,
                             assoc_score=0.0, assoc_ambiguous=False,
                             candidate_count=0, runner_up_score=None,
                             time_delta_s=-12.0, spatial_gap_m=None)
    v, ex = V.decide(assoc, None, decided_at_utc=T_DECIDE)
    _report("11. UNOBSERVED CLAIM — in view, nothing seen  [FALSE-CONFIDENCE CONTROL]", v, ex,
            "association.py already excluded 'nobody looked'. What it cannot exclude\n"
            "is a missed detection or an occluding hull, and there is no occlusion\n"
            "model here. Raising the cap without one means accusing a real named\n"
            "vessel of a spoof the detector invented by blinking.")
    _check(v.confidence <= V.POSITION_SPOOF_CONFIDENCE_CAP + 1e-9,
           f"position-spoof confidence {v.confidence:.3f} exceeded the "
           f"{V.POSITION_SPOOF_CONFIDENCE_CAP} cap")
    _check(v.defer_to_human, "an unobserved-claim finding did not defer")


def s12_prior_sensitivity(strong_result):
    print()
    print("=" * 78)
    print("12. PRIOR SENSITIVITY — the least defensible number, made visible")
    print("  The prior is a declared judgment, not a measurement. If a verdict flips")
    print("  across plausible priors it is a preference, not a finding. Put this")
    print("  table in the pitch, next to the confidence.")
    print("-" * 78)
    print("  identity-spoof exemplar:")
    for p, post, decades in V.verdict_sensitivity(strong_result):
        label = "SPOOF" if post >= V.CONFIDENCE_ALLEGE_FLOOR else "not alleged"
        stated = "STATED" if post >= V.CONFIDENCE_STATE else "defer"
        print(f"    prior 1/{1/p:<6.0f} -> posterior {post:.4f}   {label:<11} {stated}")
    print(f"  evidence strength: {V.verdict_sensitivity(strong_result)[0][2]:.1f} "
          "decades of likelihood ratio")
    print("  The posterior pins at its ceiling for every plausible prior. That is not")
    print("  the table failing — it is the case being strong enough that the")
    print("  assumption stops mattering, which is exactly what we want to be able to")
    print("  show a judge who challenges the prior.")
    flips = V.is_prior_sensitive(strong_result)
    print(f"  label flips across plausible priors: {flips}")
    _check(not flips,
           "the exemplar's LABEL depends on the prior — the finding rests on the "
           "assumption rather than on the evidence")


def s13_end_to_end():
    import lane_c_association_check as G
    import association as A
    import random

    rng = random.Random(99)
    brg, rngm = 6.0, 4200.0
    lat, lon = G._place(brg, rngm)

    honest_track = G._claim(700, "ais-HONEST", *G._place(-14.0, 2600.0), rng)
    honest_contact = G._observe(701, "eo-HONEST", -14.0 % 360.0, 2600.0, rng
                                ).model_copy(update={
        "observed_class": honest_track.claimed_ship_type,
        "observed_class_confidence": 0.87,
        "observed_length_m": honest_track.claimed_length_m,
        "observed_length_uncertainty_m": 0.15 * (honest_track.claimed_length_m or 100.0),
        "track_length_frames": 28,
    })
    spoof_track = G._claim(710, "ais-SPOOF", lat, lon, rng).model_copy(update={
        "claimed_ship_type": "fishing", "claimed_length_m": 40.0,
        "claimed_width_m": 8.0})
    spoof_contact = G._observe(711, "eo-SPOOF", brg, rngm, rng).model_copy(update={
        "observed_class": "tanker", "observed_class_confidence": 0.88,
        "observed_length_m": 250.0, "observed_length_uncertainty_m": 32.0,
        "track_length_frames": 30})

    contacts, tracks = [honest_contact, spoof_contact], [honest_track, spoof_track]
    assocs = A.associate(contacts, tracks,
                         camera_lat_deg=G.CAM_LAT, camera_lon_deg=G.CAM_LON,
                         boresight_deg_true=G.BORESIGHT_DEG,
                         fov_half_angle_deg=G.FOV_HALF_DEG,
                         max_range_m=G.MAX_RANGE_M)
    results = C.compare_all(assocs, tracks, contacts)
    verdicts = V.decide_all(assocs, results, ais_coverage_confidence=0.88,
                            decided_at_utc=T_DECIDE)

    print()
    print("=" * 78)
    print("13. END TO END — associate -> compare -> decide")
    print("  Both vessels broadcast their TRUE position, so both associate cleanly.")
    print("  Only the claimed-vs-observed identity comparison separates them.")
    print("-" * 78)
    by_track = {}
    for v, ex in verdicts:
        a = next(a for a in assocs if a.association_id == v.association_id)
        key = a.track_id or a.contact_id
        by_track[key] = v
        print(f"  {a.contact_id or '-':<12} <-> {a.track_id or '-':<12} "
              f"assoc {a.assoc_score:.3f}  ->  {v.label:<8} {v.confidence:.3f}  "
              f"defer={v.defer_to_human}"
              + (f"  [{v.spoof_subtype}]" if v.spoof_subtype else ""))
        if v.defer_reasons:
            print(f"       defer_reasons: {', '.join(v.defer_reasons)}")

    _check(by_track.get("ais-HONEST") is not None
           and by_track["ais-HONEST"].label == "MATCH",
           "end-to-end: the honest vessel was not labelled MATCH")
    _check(by_track.get("ais-SPOOF") is not None
           and by_track["ais-SPOOF"].label == "SPOOF",
           "end-to-end: the spoofing vessel was not labelled SPOOF")
    if by_track.get("ais-SPOOF"):
        _check(not by_track["ais-SPOOF"].defer_to_human,
               "end-to-end: a clean two-dimension spoof still deferred — the "
               "threshold is so conservative the tool never concludes anything")


def s14_determinism():
    track, contact = scenario_identity_spoof()
    _, _, v1, _ = _pipeline(track, contact)
    _, _, v2, _ = _pipeline(track, contact)
    same = v1.model_dump() == v2.model_dump()
    print()
    print("=" * 78)
    print("14. DETERMINISM — same input twice")
    print(f"  byte-identical verdict: {same}")
    print("  No LLM in this path, nothing stochastic. A verdict that changes between")
    print("  runs is not evidence, and contracts.py freezes every model for the same")
    print("  reason.")
    _check(same, "two runs on identical input produced different verdicts")


def main() -> int:
    s1_match()
    strong_result, strong_v = s2_identity_spoof()
    s3_seen_badly(strong_v)
    s4_single_dimension()
    s5_staleness_only()
    s6_ambiguous_association(strong_result)
    s7_coincidental_pairing()
    s8_nothing_comparable()
    s9_dark_good_coverage()
    s10_dark_sparse_coverage()
    s11_unobserved_claim()
    s12_prior_sensitivity(strong_result)
    s13_end_to_end()
    s14_determinism()

    print()
    print("=" * 78)
    print(f"CALIBRATION STATEMENT: {V.CALIBRATION_STATEMENT}")
    print("=" * 78)
    if _failures:
        print("FAIL")
        for f in _failures:
            print("  -", f)
        return 1
    print("PASS — all verdict expectations and all false-confidence controls met")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
