# Judging Criteria — Topic 02

Marked 2026-08-29 by the pre-pitch compliance audit. Every row names the artifact that
discharges it and the artifact that *evidences* it, because a criterion met only in
source is a criterion a judge cannot check.

**Status key** — ✅ met and evidenced · ⚠️ met in code, evidence artifact stale or
missing · ☐ not built.

| # | Criterion | Owner module(s) | Evidence a judge can open | Status |
|---|---|---|---|---|
| 1 | Working pipeline: EO frame + AIS in → **MATCH / dark-vessel / spoofing** verdict out, with bearing and rough range | `03_src/eo_detector.py` · `03_src/ais_ingest.py` · `03_src/association.py` · `03_src/geometry.py` · `03_src/verdict.py`, run by `04_demo/run_pipeline.py` | `04_demo/out/scene01/ranked.json` — 8 records, all four labels, **bearing and range present on 8/8**. Console panes 1–2. | ✅ |
| 2 | **Prioritization step** ranking which contact is worth a scarce asset — not a flat alert list | `03_src/prioritizer.py` — weights are data (`Weights`), `breakdown_sums_to_score()` is asserted | Console pane 3 + the "why it is ranked N" breakdown in pane 4 | ⚠️ **artifact stale — see below** |
| 3 | Explicit handling of **AIS spoofing**, not only vessels that switch AIS off | `03_src/consistency.py` — `check_length` / `check_class` / `check_heading` / `check_speed` / `check_position`; threat model in `01_research/ais_spoofing_methods.md` | `04_demo/failure_modes.md` §3 (the uncomparable-dimension coverage table) + the claimed/observed wall in console pane 4 | ✅ |
| 4 | **Evidence-grade output** — identity, track, timestamp, short rationale — plus honest limits where it defers to a human | `03_src/evidence.py` · `03_src/report.py` | `04_demo/failure_modes.md` (every number computed, nothing hand-written) + `/evidence/<record_id>` export. **8/8 records carry confidence, `defer_to_human`, timestamp, rationale and limitations.** | ✅ |

## The one open item on this table

**Criterion 2's committed evidence artifact is stale.** `04_demo/out/scene01/ranked.json`
was generated before the prioritizer reweighting and carries the superseded weights —
`verdict_severity 0.32 / infrastructure_proximity 0.22`. `prioritizer.py` now holds
`infrastructure_proximity 0.30 / verdict_severity 0.26`, and its own comment argues
explicitly *against* the pair still baked into the artifact:

> *"An earlier version had verdict_severity at 0.32 above proximity at 0.22, which ranks
> by SUSPICION rather than by CONSEQUENCE — and suspicion is what every other team's
> flat alert list already sorts on."*

So the screen currently demonstrates the configuration the code repudiates, on the
criterion the brief says most teams fail. The same stale artifact is why 6 of 8 case
files render the `BREAKDOWN DOES NOT SUM` warning: the damping fix landed in
`prioritizer.py:501-502` and has never been re-run into the scene.

**One command clears all of it. Run on the Mac, in the venv, before rehearsing:**

```bash
python 04_demo/make_synthetic_eo.py --ais 04_demo/demo_scenario.csv --out 04_demo/out/scene01 --seed 20260830
python 04_demo/run_pipeline.py --scene 04_demo/out/scene01
python 03_src/report.py --scene 04_demo/out/scene01     # refreshes 04_demo/failure_modes.md
```

Then confirm: the console's priority breakdown shows `infrastructure_proximity 0.30`,
and no case file shows `BREAKDOWN DOES NOT SUM`.

## Where to weight effort
Criterion 2 is where most teams will fail. A detector is easy. A defensible
recommendation — "send the boat here, because X, confidence Y" — is the actual problem
in the brief.

Criterion 3 is the discriminator. AIS-off is easy (a ship with no transponder).
AIS-spoofing means catching the mismatch between CLAIMED and OBSERVED. Build for the
harder case and demonstrate it explicitly.

## The architectural guardrail judges will notice
The deterministic pipeline produces the VERDICT and the CONFIDENCE.
The LLM produces the RATIONALE only.
Keep this separation visible in the architecture diagram.

**Audited and held.** No verdict anywhere originates from a model. `verdict.py` and
`report.py` each state in their header that they make no model call and import no module
that does; `report.py` reads a constant out of `eo_vlm.py` by parsing its AST rather than
importing it, specifically to keep that true. Every record in `scene01` is attributed
`deterministic-template (no model)`, and the console prints that attribution beside the
rationale so the separation is visible on screen rather than only in the repo.

The one model in the tree is `03_src/eo_vlm.py`, and it produces an **observation**
(apparent vessel class) that `consistency.py` then compares against the AIS claim. That
is an input to the deterministic check, not a verdict.
