# Lane D handoff — evidence.py (criterion 4)

Session 2026-08-29, ~08:00–09:15 UTC. Written by the lane D agent, for whoever picks
lane D up next.

| # | File | State |
|---|---|---|
| 1 | `03_src/evidence.py` | REWRITTEN AND EXTENDED. 1133 lines. Parses; statically verified; **not yet executed** — see §5 |
| 2 | `03_src/evidence.py.bak-0526` | The 05:26 version, kept for diffing. Delete once §5 is green |
| 3 | `99_scratch/lane_d_evidence_check.py` | 19 checks. **Not yet run** — needs the Mac's venv |
| 4 | `99_scratch/requests.md` | Two new entries, one of them P0 |

`report.py` is still 0 bytes and is still lane D's. It is deliberately out of scope for
this session — see §6.

---

## 1. What changed, and why each change was necessary

The 05:26 version was sound in its bones: `render_rationale()` and `build_limitations()`
were kept almost verbatim, and the module docstring's three-properties framing was kept
and extended to five. Five substantive changes on top.

### 1a. `association` was a parameter that went nowhere

`build_record()` accepted an `Association` and never read it. Every mismatch in a record
is a comparison between one AIS claim and one camera contact, and it is only meaningful
if the two are the same hull. The first question a defence lawyer asks is not "how big
was the disagreement" — it is **"how do you know that camera contact is my client's
transponder?"** The record could not answer it.

`contracts.EvidenceRecord` has no field for an Association, and adding one is an ARCH
change nobody has time for, so the pairing reaches the case file three ways instead:

- **the rationale**, always, one sentence, including for a clean pairing.
  Deliberately unconditional: a report that mentions the pairing only when it is weak
  teaches its reader that silence means strong, and that habit will eventually be wrong.
- **the limitations**, but only when the pairing actually limits the conclusion —
  ambiguous, thin margin over the runner-up, score below 0.20, or uncontested.
  Rationale = what we know; limitations = what we cannot stand behind. A limitations
  list padded with reassurance is one an operator learns to skip.
- **a `pairing_basis` block in the JSON**, held outside the typed record so a consumer
  can see at a glance what came from `contracts.py` and what this layer added.

`build_record()` now also **refuses** a verdict and an association whose
`association_id`s disagree. A record assembled from a pairing that belongs to a
different hull is not a wrong answer, it is a fabricated one, and there is no honest way
to render it.

### 1b. Anonymisation — the boundary, decided by Amol this session

The tension is real and has no single answer. A watch officer NEEDS the true MMSI;
pointing a boat at "vessel 999000004" is not decision support, and on the T2 live path
identities are real by definition. But a slide, a repo or a demo must never put a real
hull's name beside the word SPOOF.

**Ruling: the record holds the truth; the renderers pseudonymise by default.**
`to_json()` and `render_markdown()` replace MMSI, name, IMO and callsign with
999-prefixed synthetic values unless passed `allow_real_identities=True`, which also
stamps a DO-NOT-DISTRIBUTE banner into the document itself. Same 999 scheme and same
first-seen numbering as `ais_ingest.anonymise_mmsi()`, so one hull carries one synthetic
id across the AIS CSV, the synthetic scene and the case file.

**The scrub is a substring pass over every string, not a field whitelist.** This is the
whole point and it is worth not undoing. Two leak paths defeat a whitelist:

1. `render_rationale()` writes `"a contact broadcasting MMSI 219019876"` into free text.
2. Lane A's `track_id` is a surrogate over (source, MMSI), so it *embeds* the MMSI.

Pseudonymising the structured identity fields while leaving those two produces a
document that **looks** anonymised and is not — the worst of the three outcomes, because
it is the one nobody re-checks. `assert_no_real_identities()` is the last line of
defence and is a hard stop, mirroring `make_synthetic_eo.assert_pseudonymised()`.

Already-999 input passes through **unchanged**. Renumbering the golden window's MMSIs
would break every cross-reference back to `02_data/golden/*_anon.csv`, and would do it
invisibly, because the old and the new value look equally synthetic.

**One `Anonymiser` per run, shared across records** — that is why `write_dossier()`
exists rather than a loop at the call site. A dossier where the same hull is 999000003
on page 2 and 999000007 on page 5 destroys the only thing a case file is for.

**Say "pseudonymised", never "anonymised", on the slide.** Position, time, course, speed
and dimensions are untouched, so a reader with AIS history can re-identify the vessel
from its track. The rendered footer and the JSON `disclosure` block both say so. Lane A's
ruling; overclaiming it inside a document whose entire purpose is accuracy would be a
poor thing to be caught doing in a Q&A.

### 1c. Lane C's limitations now have a route in — and their absence is declared

`build_record()` gained `consistency_limitations: Sequence[str] | None`. The **caller**
computes `verdict.limitation_strings(result, verdict)` and passes the strings, because
run_pipeline is the only party holding both objects, and because `list[str]` crosses the
lane seam where `ConsistencyResult` must not (handoff_C.md §6b).

When it is omitted the record says so, in its own limitations section: *"the calibration
basis of the confidence below is NOT DECLARED here... treat the confidence as
uncalibrated."* Fail loud, not silent. A confidence number with no stated calibration
basis is the single easiest thing in this document for a lawyer to take apart, and the
gap must be visible in the artefact rather than only in a handoff nobody reads.

### 1d. A defer reason can no longer vanish

The old lookup was `[_DEFER_TEXT[r] for r in verdict.defer_reasons if r in _DEFER_TEXT]`.
A reason this module had not been taught was **silently dropped** — the pipeline
correctly decided to defer, and the operator was never told why. That is precisely the
criterion-4 failure mode. `_defer_text()` now returns a loud fallback naming the raw
reason. Prose for lane C's two requested-but-not-yet-added reasons
(`single_dimension_evidence`, `uncorroborated_pairing`) is already in the table so the
wording does not lag the enum.

### 1e. The rendered view

`render_markdown()` is ordered the way a reader has to be **convinced**, not the way the
pipeline computed it: verdict → what the camera saw → what the transponder claimed →
where they disagree → why they are the same hull → what to do about it → rationale →
limitations. Sections 1 and 2 are never merged; that separation is the whole
architecture, and a judge who knows the domain will look for exactly that.

Written for a watch officer and a lawyer, so every load-bearing number is given twice —
as the figure and as what the figure means. `sigma` gets an explicit gloss ending "it is
not a probability of guilt". Section 2 opens with "Every value in this section is a
**claim broadcast by a transponder**. This tool does not assert that any of it is true."
The footer disclaimer is unconditional.

---

## 2. The public interface

```
Anonymiser(order=None)          .mmsi/.name/.imo/.callsign/.substitutions/.mapping
build_record(*, verdict, association, mismatches, track, contact,
             priority=None, behaviour_notes=None,
             consistency_limitations=None,          # <- NEW, from lane C
             rationale_text=None, rationale_model=None, now=None) -> EvidenceRecord
render_rationale(...)           deterministic, no model, no network
build_limitations(...)          generated, never hand-written
record_to_dict / to_json(record, *, anonymiser, association, allow_real_identities)
render_markdown(record, *, anonymiser, association, allow_real_identities)
write_case_file(record, out_dir, ...)   -> (json_path, md_path)
write_dossier(records, out_dir, associations={assoc_id: Association}, ...)
assert_no_real_identities(payload, subs)
```

`04_demo/run_pipeline.py:131` keeps working untouched — every addition is a keyword
argument with a default. One line should be added there once the P0 below is fixed; the
request is filed.

---

## 3. THE P0 THAT IS NOT LANE D'S AND BLOCKS EVERYTHING

`verdict.py:122` does `from consistency import ConsistencyResult,
independent_dimension_count`. **`consistency.py` defines neither** — on the working copy
and at HEAD, verified by AST rather than by guessing. `check_pair()` returns a bare
`list[Mismatch]`; there is no `ConsistencyResult`, no `.uncomparable`, no
`independent_dimension_count`.

`import verdict` therefore raises ImportError, and criteria 1, 3 and 4 are all
downstream of it. Separately, `run_pipeline.py:111` passes `check_all()`'s
`dict[str, list[Mismatch]]` into a `decide_all()` annotated
`dict[str, ConsistencyResult]`, so even a fixed import leaves the wrong shape at the
call site. Full cause-and-effect chain, including what it does to MATCH-confidence
coverage, is in `requests.md` under `2026-08-29 09:05 UTC | D -> C + ARCH`.

**Lane D is not blocked by it.** `evidence.py` imports `contracts.py` and the stdlib
only — asserted by AST, the same technique lane A used on the geo stack. Keep it that
way: a top-level import of `verdict.py` here would make the case-file layer die for a
reason that has nothing to do with case files.

---

## 4. What was verified, and how — no benchmark is claimed that was not run

Executed in the Linux VM (no pydantic there, so no runtime test was possible):

- `ast.parse` on `evidence.py` and on the check harness — both parse.
- **189 contract field accesses** in `evidence.py` cross-checked against the field names
  declared in `contracts.py`, by parsing both files' ASTs. Zero mismatches. This is the
  check that matters for code that could not be executed, because a typo'd attribute is
  the failure mode a reading pass misses.
- Top-level imports of `evidence.py` enumerated by AST: `__future__, json, re,
  collections.abc, datetime, pathlib, typing, contracts`. No lane B or lane C module.
- The `ConsistencyResult` / `independent_dimension_count` absence in `consistency.py`
  confirmed by AST on both the working copy and `git show HEAD:`.

**Not run: the 19 behavioural checks.** See §5. No number in this handoff is a
measurement of behaviour, and none should be quoted as one until §5 is green.

---

## 5. Run this first, next session (Mac, ~10 seconds)

```bash
cd ~/Desktop/MaritimeApproaches
.venv/bin/python 99_scratch/lane_d_evidence_check.py
```

PASS looks like `19/19 checks passed` and exit code 0. The harness needs `contracts.py`
and `tests/fixtures.py` only — **it does not import `verdict.py` or `consistency.py`, so
it runs green through the P0 above.** What each check targets is in its docstring; the
five that matter most are 7a–7e, the pseudonymisation leak paths, because that is the
failure that ends the project rather than delays it.

Check 13b is an `[INFO]`, not a check: `fixtures.make_priority_score()` is hand-written
and its components sum to ~0.826 against a published score of 0.81, so it does **not**
satisfy lane D's own breakdown-sums-to-score invariant. Do not quote a priority table
built from a fixture. The real check is `prioritizer.breakdown_sums_to_score()`.

---

## 6. What lane D does next, in order

1. **Run §5.** Nothing else until it is green.
2. `report.py` — the batch/dossier layer and the optional LLM rationale via Featherless.
   Held out of this session deliberately: the LLM path cannot be exercised from the
   agent's VM (no egress), so writing it here would ship unmeasured code, and the
   MEASURED-NUMBERS-ONLY rule makes that worthless. `write_dossier()` already covers the
   deterministic multi-record path, so the demo does not need `report.py` to work.
   When it is written: it passes `rationale_text` **and** `rationale_model` into
   `build_record()`, which refuses text from an unnamed model. Keep that refusal.
3. One line in `run_pipeline.py` once the P0 is fixed — request already filed.
4. `05_pitch/` — the case file renders straight to a slide. The strongest single artefact
   is a rendered `.md` for `fixtures.scenario_identity_spoof` (40 m fishing claimed vs
   248 m tanker observed, two independent dimensions), shown beside the JSON.

---

## 7. Suggested STATUS.md line — ARCH to paste, lane D did not edit that file

```
2026-08-29 09:15 UTC | D | evidence.py REWRITTEN — CRITERION 4. Structured JSON + rendered Markdown case file, pseudonymised at the render boundary. FIVE SUBSTANTIVE FIXES over the 05:26 version: (1) `association` was accepted by build_record and never read — the pairing quality, which is the premise every mismatch rests on, was silently discarded; it now reaches the record three ways (a rationale sentence, always, even for a clean pairing; limitations, only when the pairing actually limits the conclusion; a pairing_basis block in the JSON) and a verdict/association_id disagreement is now REFUSED at build time because that record would be fabricated rather than merely wrong. (2) ANONYMISATION BOUNDARY DECIDED BY AMOL: the record holds the truth, the renderers pseudonymise by default, allow_real_identities=True stamps a DO-NOT-DISTRIBUTE banner into the document. The scrub is a SUBSTRING PASS OVER EVERY STRING, not a field whitelist, because two leak paths defeat a whitelist — render_rationale writes "broadcasting MMSI <n>" into free text, and lane A's track_id is a surrogate over (source, MMSI) so it embeds the MMSI; a whitelist produces a document that LOOKS anonymised and is not, which is the worst outcome because nobody re-checks it. Already-999 input passes through unchanged (renumbering would silently break cross-reference to *_anon.csv). One Anonymiser per run so a hull keeps one synthetic id across a dossier. Output says PSEUDONYMISED not anonymised, in the footer and in a JSON disclosure block: position/time/course/speed/dimensions are untouched and the pitch must not overclaim. (3) build_record gained consistency_limitations=, the route for verdict.limitation_strings(); when absent the record DECLARES that its confidence has no stated calibration basis rather than shipping an unbacked number. (4) an untaught DeferReason used to be silently dropped — the pipeline deferred and the operator was never told why, the exact criterion-4 failure mode — now produces a loud fallback. (5) markdown ordered the way a reader is convinced rather than the way the pipeline computed: verdict, what the camera saw, what the transponder CLAIMED, where they disagree, why they are the same hull, priority, rationale, limitations; sections 1 and 2 never merged. VERIFIED STATICALLY ONLY, no behaviour measured yet: 189 contract field accesses cross-checked against contracts.py by AST (zero mismatches), and an AST assertion that evidence.py imports contracts.py plus the stdlib and nothing from lane B or C. 99_scratch/lane_d_evidence_check.py written, 19 checks, NOT YET RUN — needs the Mac venv, ~10 s. | BLOCKED / P0 FILED, NOT LANE D'S: verdict.py:122 imports ConsistencyResult and independent_dimension_count from consistency.py and consistency.py defines NEITHER (verified by AST on the working copy AND at HEAD; check_pair returns a bare list[Mismatch]). `import verdict` raises ImportError, so criteria 1, 3 and 4 do not start; separately run_pipeline.py:111 passes check_all's dict-of-lists into a decide_all annotated dict[str, ConsistencyResult]. Consequence beyond the import: _coverage_fraction has no uncomparable count, so MATCH confidence loses its coverage cap and a Class A transponder sending no static block sails through as a confident MATCH — the failure verdict.py's own notes say was closed. limitation_strings() is structurally unreachable, so criterion 4's honesty channel is severed. Lane D is NOT blocked: evidence.py imports contracts.py and the stdlib only, by design and by AST assertion. Two requests filed. NEXT for D: run the harness, then report.py.
```
