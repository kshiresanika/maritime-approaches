"""
lane_d_evidence_check.py — the measured harness for 03_src/evidence.py. CRITERION 4.

WHY A SCRATCH HARNESS AND NOT tests/: tests/ is ARCH-owned (FILE_OWNERSHIP.md) and lane
C set the precedent with lane_c_*_check.py. This file is lane D's, it is self-contained,
and it prints its own PASS/FAIL so the pasted-back output is self-interpreting.

WHAT IT IS ACTUALLY TESTING. Not "does the function return a string" — that is not a
risk. The risks in an evidence layer are all silent ones, and each check below targets
one of them:

  * a document that LOOKS pseudonymised and is not, because the real MMSI survived
    inside free text or inside a track_id that embeds it (checks 7a-7e). This is the
    failure mode that ends the project, so it gets five checks.
  * a record built from a verdict and a pairing that do not belong together, which is
    not a wrong answer but a fabricated one (check 2).
  * a defer reason the report module was never taught, silently vanishing from the case
    file so the operator is told the tool deferred but never why (check 6).
  * lane C's calibration statement never arriving, leaving a confidence number with no
    stated basis (checks 4 and 5).
  * a dossier in which the same hull carries two different synthetic ids (check 8).

Run:  cd <repo root> && .venv/bin/python 99_scratch/lane_d_evidence_check.py
Exit code 0 = every check passed.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "03_src", _ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import fixtures  # noqa: E402
from contracts import AisTrack  # noqa: E402

import evidence  # noqa: E402

_RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _RESULTS.append((bool(condition), name, detail))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail else ""))


def info(name: str, detail: str) -> None:
    print(f"[INFO] {name}\n         {detail}")


# ============================================================================
# A REAL-LOOKING vessel. The fixtures deliberately emit 999-prefixed MMSIs, which the
# anonymiser passes through unchanged by design — so they cannot test the scrub at all.
# MID 219 is Denmark. This object exists solely to prove the scrub works on the kind of
# identity the T2 live path will actually hand it.
#
# NOTE: this is a SYNTHETIC identity chosen to look Danish. It is not a real vessel and
# must not be presented as one.
# ============================================================================

REAL_MMSI = "219019876"
REAL_NAME = "NORDIC TRADER"
REAL_IMO = "9123456"
REAL_CALLSIGN = "OWXY2"


def real_looking_track() -> AisTrack:
    return fixtures.make_ais_track(
        seed=1,
        # track_id EMBEDS THE MMSI on purpose: lane A's track_id is a surrogate over
        # (source, MMSI), so this is what the real pipeline produces, and it is the
        # sneakiest of the leak paths because nobody thinks of track_id as an identity
        # field.
        track_id=f"dma:{REAL_MMSI}",
        claimed_mmsi=REAL_MMSI,
        claimed_name=REAL_NAME,
        claimed_imo=REAL_IMO,
        claimed_callsign=REAL_CALLSIGN,
        claimed_ship_type="fishing",
        claimed_length_m=40.0,
    )


def main() -> int:
    print("=" * 78)
    print("LANE D — evidence.py measured check")
    print("=" * 78)

    verdict = fixtures.make_verdict(seed=1)
    assoc = fixtures.make_association(seed=1)
    mismatch = fixtures.make_mismatch(seed=1)
    priority = fixtures.make_priority_score(seed=1)
    contact = fixtures.make_eo_contact(seed=1)
    track = real_looking_track()

    # -- 1. it builds ------------------------------------------------------
    rec = evidence.build_record(
        verdict=verdict, association=assoc, mismatches=[mismatch],
        track=track, contact=contact, priority=priority,
        consistency_limitations=["CALIBRATION: model-calibrated, not learned."])
    check("1. build_record returns a valid EvidenceRecord",
          rec.record_id == "ev-vd-0001",
          f"record_id={rec.record_id}, limitations={len(rec.limitations)}, "
          f"range_nm={rec.range_nm}")

    # -- 2. provenance guard ----------------------------------------------
    try:
        evidence.build_record(
            verdict=verdict, association=fixtures.make_association(seed=9),
            mismatches=[mismatch], track=track, contact=contact)
        guarded = False
        why = "NO ERROR RAISED — a record can be assembled from a verdict and a "\
              "pairing that describe different hulls"
    except ValueError as exc:
        guarded = True
        why = str(exc)[:110]
    check("2. mismatched verdict/association provenance is refused", guarded, why)

    # -- 3. the association is actually read ------------------------------
    check("3. the pairing appears in the rationale (was silently dropped before)",
          "0.870" in rec.rationale_text and "paired" in rec.rationale_text,
          rec.rationale_text[rec.rationale_text.find("The camera contact was paired"):]
          [:150] or "PAIRING SENTENCE ABSENT")

    # -- 4. missing consistency limitations fails LOUD --------------------
    rec_nolim = evidence.build_record(
        verdict=verdict, association=assoc, mismatches=[mismatch],
        track=track, contact=contact, priority=priority)
    check("4. a missing calibration statement is declared, not silently absent",
          any("NOT DECLARED" in x for x in rec_nolim.limitations),
          next((x[:120] for x in rec_nolim.limitations if "NOT DECLARED" in x),
               "NO WARNING EMITTED"))

    # -- 5. supplied ones are carried and suppress the warning ------------
    check("5. supplied consistency limitations are carried verbatim",
          any("CALIBRATION" in x for x in rec.limitations)
          and not any("NOT DECLARED" in x for x in rec.limitations),
          f"first limitation: {rec.limitations[0][:90]}")

    # -- 6. an unknown defer reason is never dropped ----------------------
    unknown = evidence._defer_text("a_reason_nobody_taught_this_module")
    check("6. an unteachable defer reason produces text rather than vanishing",
          "escalate" in unknown, unknown[:110])

    # -- 7. THE ANONYMISATION BOUNDARY ------------------------------------
    md = evidence.render_markdown(rec, association=assoc)
    js = evidence.to_json(rec, association=assoc)

    check("7a. real MMSI absent from the rendered Markdown",
          REAL_MMSI not in md,
          f"searched for {REAL_MMSI}; synthetic present: {'999000001' in md}")
    check("7b. real name, IMO and callsign absent from the Markdown",
          REAL_NAME not in md and REAL_IMO not in md and REAL_CALLSIGN not in md,
          f"name={REAL_NAME not in md} imo={REAL_IMO not in md} "
          f"callsign={REAL_CALLSIGN not in md}")
    check("7c. real MMSI absent from the FREE-TEXT rationale (the sneaky leak)",
          REAL_MMSI not in md.split("## 6. Rationale")[1],
          "the deterministic rationale writes 'broadcasting MMSI <n>' into prose; a "
          "field-whitelist scrub would miss it")
    check("7d. real MMSI absent from track_id inside the JSON (the other sneaky leak)",
          REAL_MMSI not in js,
          f"track_id in source was dma:{REAL_MMSI}; "
          f"rendered as {json.loads(js)['record']['ais_track']['track_id']}")

    md_real = evidence.render_markdown(rec, association=assoc,
                                       allow_real_identities=True)
    check("7e. allow_real_identities=True emits the truth AND a do-not-distribute banner",
          REAL_MMSI in md_real and "DO NOT DISTRIBUTE" in md_real,
          f"real id present: {REAL_MMSI in md_real}, "
          f"banner present: {'DO NOT DISTRIBUTE' in md_real}")

    # -- 8. one hull, one synthetic identity, across a whole dossier ------
    anon = evidence.Anonymiser()
    rec_b = evidence.build_record(
        verdict=fixtures.make_verdict(seed=2, association_id="assoc-0002"),
        association=fixtures.make_association(seed=2),
        mismatches=[], track=track,
        contact=fixtures.make_eo_contact(seed=2), priority=None)
    md_a = evidence.render_markdown(rec, anonymiser=anon, association=assoc)
    md_b = evidence.render_markdown(rec_b, anonymiser=anon)
    check("8. the same hull carries the same synthetic id across records",
          "999000001" in md_a and "999000001" in md_b,
          f"page 1: {'999000001' in md_a}, page 2: {'999000001' in md_b}; "
          f"reidentification key = {anon.mapping()}")

    # -- 9. already-pseudonymised input is NOT renumbered ------------------
    golden = fixtures.make_ais_track(seed=5)          # fixtures emit 999-prefixed
    a9 = evidence.Anonymiser()
    check("9. an already-999 MMSI passes through unchanged",
          a9.mmsi(golden.claimed_mmsi) == golden.claimed_mmsi,
          f"{golden.claimed_mmsi} -> {a9.mmsi(golden.claimed_mmsi)} "
          "(renumbering would break cross-reference to *_anon.csv)")

    # -- 10. the last line of defence actually fires -----------------------
    try:
        evidence.assert_no_real_identities(
            f"a document still containing {REAL_MMSI}", {REAL_MMSI: "999000001"})
        fired = False
    except SystemExit:
        fired = True
    check("10. assert_no_real_identities refuses a leaking document", fired,
          "hard stop, not a warning — the failure it catches is a document that "
          "looks anonymised and is not")

    # -- 11. structure a non-engineer can follow ---------------------------
    headings = ["## 1. What the camera observed",
                "## 2. What the transponder claimed",
                "## 3. Where the claim and the observation disagree",
                "## 4. Why these are believed to be the same vessel",
                "## 5. Priority for a scarce asset",
                "## 6. Rationale",
                "## 7. Limitations"]
    missing = [h for h in headings if h not in md]
    check("11. all seven case-file sections render", not missing,
          f"missing: {missing}" if missing else f"{len(headings)} sections, "
          f"{len(md.splitlines())} lines")

    check("11b. the non-negotiable footer is present and unconditional",
          "Decision support, not enforcement." in md
          and "pseudonymisation, not anonymisation" in md,
          "the disclaimer and the honest privacy caveat both survive rendering")

    check("11c. the defer-to-human flag renders as a visible banner",
          "DEFERRED TO A HUMAN OPERATOR" in md
          and "not making a recommendation" in md,
          f"verdict.defer_to_human={verdict.defer_to_human}")

    # -- 12. a DARK contact has no identity to protect ---------------------
    dark_assoc = fixtures.make_association(seed=3, track_id=None,
                                           association_id="assoc-0003",
                                           candidate_count=0, runner_up_score=None,
                                           spatial_gap_m=None)
    dark_rec = evidence.build_record(
        verdict=fixtures.make_verdict(seed=3, association_id="assoc-0003",
                                      label="DARK", spoof_subtype=None,
                                      mismatch_ids=[],
                                      defer_reasons=["sparse_ais_coverage"]),
        association=dark_assoc, mismatches=[], track=None,
        contact=fixtures.make_eo_contact(seed=3))
    dark_md = evidence.render_markdown(dark_rec, association=dark_assoc)
    check("12. a DARK record renders with no claimed identity anywhere",
          "**No AIS claim.**" in dark_md
          and evidence.Anonymiser().substitutions(dark_rec) == {},
          "a camera cannot observe an identity, so there is nothing to pseudonymise")

    # -- 13. the priority breakdown is rendered faithfully -----------------
    contrib_rows = [ln for ln in md.splitlines()
                    if ln.startswith("| infrastructure_proximity |")]
    expected = (priority.component_scores["infrastructure_proximity"]
                * priority.component_weights["infrastructure_proximity"])
    check("13. each priority row shows score x weight, not a re-derived number",
          bool(contrib_rows) and f"{expected:.3f}" in contrib_rows[0],
          f"row: {contrib_rows[0] if contrib_rows else 'ABSENT'} (expected "
          f"contribution {expected:.3f})")

    total = sum(priority.component_scores[k] * priority.component_weights.get(k, 0.0)
                for k in priority.component_scores)
    info("13b. breakdown-sums-to-score, on the FIXTURE",
         f"components sum to {total:.4f} against a published score of "
         f"{priority.score:.4f} (delta {abs(total - priority.score):.4f}). The fixture "
         f"is hand-written and does not satisfy the invariant; the real check is "
         f"prioritizer.breakdown_sums_to_score(). Flagged so nobody quotes this table "
         f"from a fixture-built record.")

    # -- 14. both views written from one anonymiser ------------------------
    with tempfile.TemporaryDirectory() as tmp:
        pairs = evidence.write_dossier([rec, rec_b], tmp,
                                       associations={"assoc-0001": assoc,
                                                     "assoc-0002":
                                                     fixtures.make_association(seed=2)})
        blobs = [p.read_text(encoding="utf-8") for pair in pairs for p in pair]
        check("14. write_dossier emits JSON+MD per record with no real id in any of them",
              len(pairs) == 2 and all(REAL_MMSI not in b for b in blobs),
              f"{len(blobs)} files written, "
              f"{sum(b.count('999000001') for b in blobs)} synthetic-id occurrences")

    print("=" * 78)
    failed = [n for ok, n, _ in _RESULTS if not ok]
    print(f"{len(_RESULTS) - len(failed)}/{len(_RESULTS)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
