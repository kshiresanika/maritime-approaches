# HANDOFF — Lane C (Fusion + Verdict)

Sessions: 2026-08-28. Owner: lane C.

| Session | Files |
|---|---|
| 1 | `03_src/association.py`, `99_scratch/lane_c_association_check.py`, 6 requests |
| 2 | `03_src/consistency.py`, `99_scratch/lane_c_consistency_check.py`, `association.py` (arc helper made public), 2 requests |
| 3 | `03_src/verdict.py`, `99_scratch/lane_c_verdict_check.py`, 1 request |
| 4 | `03_src/prioritizer.py` **(lane D's file — edited on instruction, API unchanged, disclosed in requests.md)**, `99_scratch/lane_c_prioritizer_check.py` |

**Lane C is complete end to end: associate -> compare -> decide.** All check scripts
pass. `evidence.py` and `report.py` are lane D.

> **READ THIS BEFORE TRUSTING SECTIONS 1b/3b BELOW.** As of 2026-08-29,
> `consistency.py` has been REPLACED by a different implementation from another
> session (STATUS records a class check that "could never fire" — that is not the
> confusability-matrix version described here). `association.py` and `verdict.py` are
> still byte-for-byte the ones documented below. Sections describing consistency.py
> are the record of what lane C built, not necessarily what is on disk now. Diff
> before relying on them.

---

## 1. What exists now

`03_src/association.py` — pairs `EoContact` to `AisTrack` and emits `Association`
objects. Written against contracts.py; no bare dicts cross the boundary.

Public API:

```python
associate(contacts, tracks, *, camera_lat_deg, camera_lon_deg,
          boresight_deg_true, fov_half_angle_deg, max_range_m,
          scene_time_utc=None) -> list[Association]

predict_measurement(...)     # claim -> (bearing, range) as the camera would see it
is_in_field_of_view(...)     # the frustum gate
count_outcomes(assocs)       # reporting tally
```

Three outcomes, per contracts.py:

| Association | Meaning | Next stop |
|---|---|---|
| `contact_id` and `track_id` set | matched | `consistency.py` — compare attributes |
| `track_id is None` | DARK candidate | `verdict.py` — label DARK |
| `contact_id is None` | position-spoof candidate | `verdict.py` — needs occlusion reasoning |

`consistency.py` and `verdict.py` are still empty. Next session.

---

## 1b. consistency.py — CRITERION 3

```python
compare(association, track, contact) -> ConsistencyResult
compare_all(associations, tracks, contacts) -> dict[association_id, ConsistencyResult]
strongest_significance(result) -> float
independent_dimension_count(result, min_severity="major") -> int
class_confusion_probability(claimed, observed) -> float
```

`ConsistencyResult` carries three lists, and all three matter:

- **mismatches** — `Mismatch` objects, sorted strongest first.
- **agreements** — comparisons that were made and PASSED. Evidence-grade output has to
  show what agreed. "Class and length agree, heading disagrees" is a case; "heading
  disagrees" alone is an allegation.
- **uncomparable** — checks that could NOT be run, with a reason and a `suspicious`
  flag. contracts.py: a missing claim is itself a finding. These become
  `DeferReason.claim_field_missing` in verdict.py and `EvidenceRecord.limitations` in
  lane D. An empty result and "there was nothing to compare" must never look the same.

Four dimensions compared: **class, length, heading (or COG as a labelled proxy),
speed**. Two deliberately not compared — see section 4b.

Every Mismatch carries `delta`, `tolerance`, `significance` (sigmas),
`observation_confidence`, `severity` and `explained_by_staleness`. Never a boolean.
`significance` is what verdict.py must read, not `delta`.

---

## 1c. verdict.py — the label, the confidence, the defer flag

```python
decide(association, result=None, *, ais_coverage_confidence=0.70,
       decided_at_utc=None, prior=SPOOF_PRIOR) -> (Verdict, VerdictExplanation)
decide_all(associations, results, **kwargs) -> list[(Verdict, VerdictExplanation)]
limitation_strings(result, verdict) -> list[str]     # LANE D CALLS THIS
verdict_sensitivity(result, priors) -> [(prior, posterior, evidence_decades)]
is_prior_sensitive(result, priors) -> bool
```

**No LLM in this path and there must never be one.** Nothing stochastic; scenario 14
asserts byte-identical verdicts across runs. `scipy` is not even imported — the
sigma-to-probability step uses `math.erfc`, so verdict.py runs even while the scipy
install is still outstanding.

**Labels come from contracts.py, not from the task brief.** The brief said
MATCH / DARK_VESSEL / SPOOFING; `contracts.VerdictLabel` is
**MATCH / DARK / SPOOF / UNKNOWN** and it is ARCH-owned and frozen. The fourth is not
decoration — contracts.py calls UNKNOWN "the machine-readable defer-to-human state.
NOT optional." A three-label system has nowhere to put "we looked and we cannot say".

**`confidence` is always confidence in the STATED LABEL.** SPOOF: P(claim is false).
MATCH: P(claim is honest), capped by coverage. DARK: P(there is genuinely no claim for
this hull). UNKNOWN: how sure we are it CANNOT be determined — so a vessel with nothing
comparable is a *confident* UNKNOWN (0.99), not a vague one. **Lane D's prioritizer
must read label and confidence together**: a confident UNKNOWN beside a cable is worth
a look precisely because it is unexamined, and ranking on the number alone buries it.

---

## 1d. prioritizer.py — CRITERION 2 (lane D's file, extended on instruction)

Public API unchanged; everything added is a keyword argument with a default. Both live
call sites verified working: `04_demo/run_pipeline.py` calls
`rank_all(verdicts, tracks_by_assoc, contacts_by_assoc)` and `04_demo/app.py` does
`from prioritizer import DEMO_INFRASTRUCTURE, Asset`.

```python
rank_all(verdicts, tracks_by_assoc, contacts_by_assoc, *, asset=Asset(),
         assets=None, infrastructure=DEMO_INFRASTRUCTURE, weights=DEFAULT_WEIGHTS,
         loiter_events_by_assoc=None)
breakdown_sums_to_score(ps) -> bool     # the invariant, exported
explain(ps, notes) -> str               # the "and here is why", operator-readable
```

**Weights — infrastructure is now the heaviest single term**, per the brief's named
scenario: `infrastructure_proximity 0.30, verdict_severity 0.26, behaviour_anomaly
0.18, actionability 0.14, confidence 0.12`. It was 0.22, *below* verdict_severity at
0.32 — which ranks by SUSPICION rather than by CONSEQUENCE, and suspicion is what every
other team's flat alert list already sorts on.

**THE INVARIANT, now guaranteed and tested:**
`sum(component_scores[k] * component_weights[k]) == score`, exactly, for every score.

**Loiter comes from lane A's `ais_trajectory.BehaviourEvent`**, duck-typed by attribute
name so `prioritizer` still imports nothing but the standard library and `contracts` —
lane A went to trouble keeping movingpandas/geopandas lazily imported and this does not
undo it. Dwell GOVERNS when present; the single-report speed proxy is the fallback for
when the trajectory pass has not run.

**Asset availability is time, not a flag.** `Asset(available=..., available_in_min=...)`.
An unavailable hull is excluded; a hull free in 45 minutes has the wait added to its
transit. With no hull free the ranking survives intact and every
`scarce_asset_recommended` is withdrawn — the ordering is advice and must outlive the
boat being busy.

---

## 2. The four design decisions, and why

**Match on geometry, judge on attributes — never mix.** A vessel broadcasting a false
identity is physically exactly where AIS says it is. It lies about WHAT it is, not
WHERE. So association must pair it happily, so that `consistency.py` can then catch
the class/length mismatch. If association rejected pairs that "looked wrong", the
spoofing detector — criterion 3, the discriminator — would never see its own cases.

**Score in sigmas, not metres.** The claim is projected into what the camera WOULD
have measured from the pose, then bearing and range residuals are scored separately
against their own uncertainties. Bearing is good (0.4–1.5°); monocular range is poor
(~12%). A 400 m range residual is nothing at 4 km and damning at 400 m; a
metre-distance cost matrix cannot tell those apart. This is where criterion 4's
calibrated confidence starts.

**Global assignment, not greedy.** `scipy.optimize.linear_sum_assignment` over the
whole rectangular cost matrix. Greedy nearest-neighbour is order-dependent — shuffle
the input and the answer changes — and reproducibility is a precondition for
evidence-grade output.

**Field-of-view gate before any claim becomes a spoof candidate.** An AIS track with
no observation is only suspicious if the camera was LOOKING at where it claims to be.
Every other track in the Baltic also has "no observation" and is innocent. Without
this gate the module emits hundreds of false position-spoof candidates and criterion
2's ranked list drowns. In the check scene, 14 of 20 claims are excluded by it; 0
leak into findings.

**Class is scored through a confusability matrix, not as a boolean.** At 2-8 km a
cargo ship and a tanker are both long grey boxes; a 40 m fishing vessel and a 250 m
tanker cannot be confused by anything that can see at all. Same boolean, completely
different evidential weight. The matrix is declared in the module so a judge can argue
with the numbers rather than with a black box, and it is labelled as ESTIMATED — there
is no labelled maritime EO set here to calibrate against, and pretending otherwise is
the exact overstatement criterion 4 forbids.

**The bow/stern flip is modelled, not special-cased.** A side-on silhouette is nearly
symmetric and a classifier can read it backwards, so the likelihood of an observed
heading under an HONEST claim is a mixture: 75% a Gaussian on the claim, 25% on the
claim plus 180. A 180-degree disagreement then scores 1.15 sigma and stays out of the
evidence card; a 90-degree disagreement scores 4.5 sigma and goes in. A hard "suppress
near 180" rule would put a cliff between 149 and 151 degrees and would discard a
genuine 180-degree spoof with no way to recover it.

**Is the confidence calibrated? Say the true thing: it is MODEL-calibrated, not
learned.** There is no labelled maritime spoofing dataset in this repo, so no
scikit-learn calibration was fitted — no Platt scaling, no isotonic regression —
because both need labelled outcomes and fitting them on invented labels produces a
number that looks trained and means nothing. What is done instead: consistency.py
already expresses each mismatch as a significance in sigmas, which under the declared
noise model IS P(discrepancy this large | claim honest). Those combine as likelihood
ratios against a DECLARED prior, so the output is a posterior. "Confidence 0.82" then
means something arguable: *under the declared sensor model, the declared per-dimension
spoof likelihoods, and a prior of 1 in 500, the posterior probability the claim is
false is 0.82.* `CALIBRATION_STATEMENT` ships that caveat with every record.

**The defer threshold sits at 0.85, and here is the derivation.** The Fehmarn demo day
carries **321 distinct vessels** after lane A's dedup. At the declared 1-in-500 prior,
the expected number of real spoofing vessels in the entire day is **about 0.64** —
fewer than one. A tool stating conclusions at confidence C is wrong about (1 - C) of
them, so auto-concluding on even five vessels a day at C = 0.85 yields ~0.75 false
conclusions per day, already comparable to the ~0.64 real events the whole day
contains. At C = 0.70 the same five yield 1.5 — more than twice the real event rate,
and the watch officer correctly stops believing the tool. **Below roughly 0.85 the tool
generates more false conclusions than there are real events in the water.**

```
confidence >= 0.85   stated as the machine's conclusion
0.50 - 0.85          label stated AND defer_to_human set
below 0.50           not enough to allege anything
```

Because the threshold is applied to a *posterior*, "0.85 confident" already accounts
for the base rate — reading it as "15% of these are wrong" is correct by construction,
which is not true of a weighted score.

---

## 3. Measured results — check script, seed 7

`python 99_scratch/lane_c_association_check.py` (add `--capacity` for the slow probe).

**Scene 1 — ground truth, Fehmarn Belt.** Camera 54.5050 N 11.2200 E, boresight 000
true, FOV ±30°, max range 8 km. Input 6 EO contacts, 20 AIS claims (14 out of frustum
by construction).

```
MATCHED            4   (truth 4)   correct 4, WRONG 0
DARK candidates    2   (truth 2)
SPOOF candidates   2   (truth 2)
ambiguous flags    0
out-of-view claims leaked into findings: 0
assoc_score        0.624 – 0.964
spatial_gap_m      132 / 233 / 548  (min / median / max)
time_delta_s       -21.5 – -8.5
```

**Scene 2 — two claims 60 m apart at 4 km, one observation.** matched 1,
`assoc_ambiguous=True`, score 0.223, runner-up 0.084. Required behaviour: an
ambiguity flag, not a confident pick. Pointing a boarding team at the wrong one of two
adjacent hulls is the failure this whole tool exists to prevent.

**Scene 4 — uncalibrated pose.** A contact with `bearing_uncertainty_deg = 180`,
range agreeing exactly with a real claim. matched **0**, emitted unmatched with
`assoc_ambiguous=True`, `candidate_count=1`. With the guard disabled it matches at
score 0.172 — so the guard is load-bearing, not decoration. See section 4.

**Scene 5 — the existing `tests/fixtures.py` populations, unmodified.** 8 EO contacts,
25 AIS claims → **MATCHED 0, DARK 8, SPOOF-candidate 0.** This is the CORRECT result,
not a defect. Those fixtures draw AIS positions and EO bearings from independent
random streams, so no true pairing exists to find, and all 25 claims fall outside a
60° wedge to the north. Reporting a match there would mean the matcher was inventing
one. See request 3 to ARCH.

---

## 3b. Measured results — consistency, `lane_c_consistency_check.py`

Twelve scenarios, **all pass**. Five are FALSE-POSITIVE controls and they matter more
than the detections: every one of them is ordinary innocent traffic in the Fehmarn
Belt, and a detector that fires on them has detected nothing.

**The criterion 3 exemplar** (`fixtures.scenario_identity_spoof`): AIS claims a 40 m
fishing vessel, camera sees a 248 m tanker.

```
length    40.0 claimed  vs  248.0 observed   delta +208 m, tolerance 46.6 m
          significance 6.70 sigma   CRITICAL   obs_conf 0.88
class     fishing       vs  tanker           categorical
          significance 3.09 sigma   MAJOR      obs_conf 0.88
-> 2 INDEPENDENT dimensions at major or above
```

**The same hull seen badly** (class confidence 0.55, length uncertainty 90 m):
everything collapses to MINOR, **0** independent major dimensions, strongest falls
6.70 -> 2.95 sigma. That is the variant `tests/fixtures.py` asks for by name, and it
is the signal verdict.py must turn into UNKNOWN + `defer_to_human`.

**End to end** — `associate()` then `compare_all()`, one honest hull and one spoof,
both broadcasting their TRUE position:

```
eo-HONEST <-> ais-HONEST   assoc_score 0.477   no mismatches
eo-SPOOF  <-> ais-SPOOF    assoc_score 0.970   length 6.55s CRITICAL, class 3.09s MAJOR
```

**Put that pair on the screen.** The spoofing vessel is the BEST geometric match in
the scene — 0.970 against the honest vessel's 0.477 — because it is exactly where it
says it is. Position tells you nothing about it. Only the claimed-versus-observed
identity comparison separates them. That is the whole argument for criterion 3 in two
lines of output.

False-positive controls, all held: cargo-claimed/tanker-observed produced no mismatch;
a 180-degree bow/stern flip produced no mismatch; an 80-degree heading delta on a 22 m
craft with a 120 s old claim was flagged `explained_by_staleness` and downgraded to
minor; a claimed ship type of `unknown` (lane A's service-craft mapping — 29.6% of
rows in the measured slice) came back uncomparable rather than as a class mismatch;
`observed_class=None` on the YOLO-only path came back uncomparable, with LENGTH still
carrying the spoof on its own.

---

## 3c. Measured results — verdict, `lane_c_verdict_check.py`

Fourteen scenarios, **all pass**. Seven are FALSE-CONFIDENCE controls: this module's
job is to know when it does not know.

| # | Scenario | Result |
|---|---|---|
| 1 | agreeing pair | MATCH 0.950, no defer |
| 2 | identity spoof (criterion 3 exemplar) | **SPOOF 0.999, identity, stated** |
| 3 | same spoof seen badly | SPOOF 0.579, **defer** |
| 4 | single 6.98-sigma dimension alone | SPOOF **0.800 (capped)**, defer |
| 5 | every mismatch staleness-explained | MATCH 0.950, no spoof |
| 6 | ambiguous association | SPOOF **0.700 (capped)**, defer |
| 7 | coincidental pairing, assoc 0.047 | SPOOF **0.700 (capped)**, defer |
| 8 | Class A silence, nothing comparable | **UNKNOWN 0.990**, defer |
| 9 | dark, good coverage | DARK 0.900, stated |
| 10 | dark, sparse coverage | DARK 0.517, `sparse_ais_coverage`, defer |
| 11 | in-view claim, nothing seen | SPOOF 0.600 (capped), defer |
| 13 | end to end | honest MATCH 0.950 no defer / spoof SPOOF 0.999 no defer |
| 14 | determinism | byte-identical |

The exemplar's evidence is **12.7 decades of likelihood ratio**, dominated by length
(+10.45) then class (+1.49 after damping). Scenario 12 prints the posterior across
priors from 1/50 to 1/10000: the label never flips. That is the answer to a judge who
challenges the prior — the case is strong enough that the assumption stops mattering,
and `is_prior_sensitive()` exists to detect the cases where it does not.

---

## 3d. Measured results — prioritizer, 20 contacts, one patrol boat

Scene composition is deliberate: mostly innocent, because real Fehmarn traffic is
overwhelmingly innocent and a scene of twenty plausible spoofers would flatter the
ranker enormously.

```
 #  contact                     score  label     infra_m   tti  boat
 1  V01-spoof-on-cable         0.9948  SPOOF          89     2  YES
 2  V02-dark-on-cable          0.9132  DARK          114     3  YES
 3  V03-spoof-deferred-cable   0.8431  SPOOF         146     2   .
 4  V07-unknown-on-cable       0.6552  UNKNOWN        67     5   .
 5  V04-honest-loiter-cable    0.6316  MATCH           0     2  YES
 6  V11-moored-declared        0.5106  MATCH          32     5   .
 7  V06-dark-midfield          0.4509  DARK         7158    11   .
 8  V05-spoof-far              0.4057  SPOOF       31128    49   .
 ... 12 routine transits, 0.30 down to 0.07
```

Rank 1, broken out — **this is criterion 2**:

```
rank 1  score 0.9948  (V01-spoof-on-cable)
    infrastructure_proximity   0.999 x 0.30 = 0.2997  (30.1% of the score)
    verdict_severity           1.000 x 0.26 = 0.2600  (26.1%)
    behaviour_anomaly          1.000 x 0.18 = 0.1800  (18.1%)
    actionability              0.991 x 0.14 = 0.1387  (13.9%)
    confidence                 0.970 x 0.12 = 0.1164  (11.7%)
                                              0.9948  TOTAL
  nearest asset at risk : INFRA-A at 89 m
  patrol boat           : 1.6 km, 2 min to intercept
  - held station within 120 m
  - stopped for 90 min WITHOUT declaring moored or anchored
  - and it did so within 2 km of protected infrastructure
```

Two orderings worth pointing at on stage. **V05 is a 0.96-confidence SPOOF and it ranks
eighth**, below a dark contact in midfield, because it is 31 km away and 49 minutes of
transit — more suspicious, less worth the only boat. **V04 is an honest vessel and it
ranks fifth and IS recommended**, because stationary over a cable is the brief's named
scenario and its AIS being truthful does not make it less interesting; but all three
findings at the same cable sit above it, which is the ordering guarantee, asserted as a
test rather than tuned.

---

## 4. Two bugs the check script caught — recorded so they are not reintroduced

**Ambiguity by score ratio was wrong.** The first version flagged ambiguity when the
rival scored ≥ 0.7 × the winner. Because score is `exp(-n²/2)`, a fixed score ratio is
a fixed difference in chi², whose meaning changes completely with where on the curve
you sit. Measured: a winner at 1.58σ and a rival at 1.91σ — a 0.33σ gap, plainly
indistinguishable — gave a ratio of 0.56 and was declared UNAMBIGUOUS. Replaced with a
**sigma margin**: ambiguous when a rival fits within 1.0σ of the winner. Says what we
actually mean and is quotable in the pitch.

**RMS-over-dof normalisation was wrong.** `sqrt(chi2/dof)` lets an AGREEING dimension
dilute a DISAGREEING one: a claim 3.5σ off in bearing but perfect in range came out at
2.47 "sigma" and slipped inside a 3σ gate. For a spoof detector that is the wrong
direction to fail in — a false position is exactly the case where one dimension
screams and the other agrees. Replaced with a chi-square **survival probability**
mapped back through the normal quantile (`_equivalent_sigma`), which is dof-aware, puts
bearing-only and bearing+range pairs on one comparable scale, and reduces exactly to
`|residual|/sigma` when dof = 1. The 3.5σ example now returns 3.06σ and is correctly
gated out.

**Lane B's 180-degree "not evidence" marker arrived as a PERMISSIVE gate.**
`eo_detector.uncalibrated_benchmark_pose()` sets `yaw_uncertainty_deg = 180.0` on
purpose, and eo_detector folds yaw into `bearing_uncertainty_deg`. Follow the chain: a
180-degree sigma makes every bearing residual ~0 sigma, the bearing term drops out of
chi2, and the pairing is then decided by RANGE ALONE — which can agree by chance.
Lane B's marker for "this run is not evidence" would have arrived here as a match.
Fixed by `MAX_USABLE_BEARING_SIGMA_DEG = 15.0`: above that the contact is unusable for
pairing and is emitted unmatched-AND-ambiguous — deliberately NOT as a dark vessel,
because nothing was established either way. Verdict.py must not read that row as DARK.

**The class confidence blend double-counted classifier error.** The first version
computed `p = conf * P(observed|claimed) + (1-conf) * P_uniform`, reasoning that a
low-confidence observation should carry less weight. But `_CONFUSABLE` already IS the
rate at which the camera reports B when the truth is A — misclassification included —
so the discount was applied twice. Measured on the criterion 3 exemplar: the class
mismatch came out at 2.35 sigma and only MINOR, the spoof rested on length alone, and
the two-independent-dimension case collapsed. Fixed by giving
`observed_class_confidence` its correct role — PER-INSTANCE quality, scaling how much
of the matrix to believe against a nominal 0.85 — after which the same fixture gives
3.09 sigma MAJOR and two dimensions.

**`stale_ais` fired on any staleness-explained mismatch, and deferred everything.** A
fully explained mismatch is a reason to IGNORE that dimension, not to escalate.
Measured on the end-to-end scene, a random heading delta on a 27-second-old claim was
correctly discounted from the arithmetic and then forced BOTH vessels — the honest one
included — to defer. A tool that defers on everything is not a decision-support system,
it is an expensive alarm. `stale_ais` now fires only when staleness changed what could
be concluded: every mismatch discounted AND nothing agreed, or a claim older than 120 s.

**Confidence caps were missing, so the ranking was inverted.** The dimension count
originally moved only the defer flag, not the number — so a lone 6.98-sigma length
finding and a finding computed off a 0.047 association both reported **0.999** and
would have outranked a properly corroborated case. Criterion 2 points a patrol boat at
this number. Now `SINGLE_DIMENSION_CONFIDENCE_CAP = 0.80` and
`UNCERTAIN_ASSOCIATION_CONFIDENCE_CAP = 0.70` cap the posterior itself. The reasoning
is not squeamishness: a likelihood ratio is only valid if the sensor model behind it is
right, and with one dimension an unmodelled systematic (a range estimate 40% low
scaling apparent length) produces that exact signature at any significance you like.

**`UNKNOWN` reported confidence 0.000.** Confidence is confidence in the STATED label,
and the stated label is "cannot determine" — so a vessel we could not examine at all
should be a *confident* UNKNOWN. 0.000 read as "no idea and no confidence in that
either", which an operator skips as noise. Now 0.99.

**prioritizer: the breakdown did not sum to the score.** `score *= 0.65` was applied
after the weighted sum, so a deferred SPOOF showed components summing to 0.9970 beside
a score of 0.6481 — a 35% discrepancy in the one place the operator is asked to trust
the arithmetic. Deferral now damps the two finding-derived components before the sum.

**prioritizer: a confident MATCH bought urgency with its own confidence.** 0.1425 of
priority for being confidently harmless. Measured consequence before the fix: an honest
vessel ranked FIRST and was the only contact recommended for the boat, above a SPOOF
128 m from the same cable.

**prioritizer: the snapshot overrode the span.** Behaviour took `max(speed proxy,
dwell)`, so a vessel that DECLARED itself at anchor had its dwell correctly discounted
to 0.52 and then the naive proxy returned 1.00 and won — silently discarding every
innocent explanation lane A had computed. Dwell now governs when present. The proxy's
nav-status branch also flipped from `+0.15` to `x0.45`, because the same fact was
discounting in one path and aggravating in the other, so a vessel scored differently
depending on whether lane A's trajectory pass had run.

---

## 4b. What consistency.py deliberately does NOT compare

**Position, on a matched pair.** association.py gated that pair at 3 sigma on bearing
and range, so a matched pair agrees in position BY CONSTRUCTION. Emitting it again is
one measurement counted twice, and a reviewer who asks "then how did it pass
association?" has found the hole. The real consequence is a DETECTION GAP, documented
rather than papered over: **a position spoof smaller than the association gate (~250 m
cross-range at 5.6 km) is absorbed into the match and is invisible.** Position spoofing
is caught by association's unmatched-CLAIM output instead, and
`Association.spatial_gap_m` already carries the number for the report.

**Width.** `MismatchDimension` allows it but `EoContact` has no `observed_width_m` —
the camera never measures beam. Deriving one from the bbox aspect ratio would encode
the vessel's ASPECT ANGLE as if it were its width, so a ship viewed bow-on would read
as a 20 m beam on a 250 m hull and generate a critical spoof out of pure geometry.
Request filed rather than a guess made.

---

## 5. Honest limits — feed these straight into criterion 4

**Coincidental pairing cannot be detected at this layer.** A DARK vessel and an
unrelated SILENT claim close together produce one feasible pairing, so there is no
rival to raise the ambiguity flag, and association pairs them. MEASURED confusion
window: the wrong pairing survives the gate at **150 m** of cross-range separation at
5.6 km and is rejected from **250 m** outward. Two consolations, both real:
- The wrong pairing scores **0.047**. `verdict.py` MUST defer on low `assoc_score`,
  not only on `assoc_ambiguous`. This is the single most important thing for the next
  session to wire up.
- The defence is downstream. The dark vessel's observed class and length belong to a
  different ship than the claim, so `consistency.py` sees a class/length mismatch and
  the pair surfaces as a SPOOF rather than a MATCH. The operator is told to look —
  the correct outcome from a wrong pairing.

**Frustum contact capacity: 13.** MEASURED — the maximum number of mutually
resolvable contacts this geometry can hold (60° FOV, 1.2–7.0 km, 4σ separation).
Beyond that density, pairings degrade into ambiguity flags. It is bearing-limited, so
the fix is a narrower FOV or a better range estimate, not a better matcher. This is a
pitch slide, not a footnote.

**Range is the binding constraint everywhere.** Bearing accuracy is already good
enough. Every ambiguity, every confusion, every wide `spatial_gap_m` traces back to
the ~12% monocular range estimate. "What would change the answer" for almost every
deferred verdict is: a better range.

**AIS position sigma is an assumption, not a measurement.** `AIS_POSITION_SIGMA_M =
25.0`, because the DMA CSV does not reliably expose the position-accuracy flag. Too
small and honest pairings get gated out as spoofs; too large and distinct vessels
merge. Declared in the module, and it belongs in the limitations list of any
EvidenceRecord built on it.

**Two lane A properties reach this module.** (i) 63.5% of DMA rows are duplicate
receptions. Lane A dedupes; if duplicates ever leak, identical rivals sit within 1
sigma of each other, so they surface as ambiguity flags rather than silent
mispairings — it degrades safely, but the counts inflate. (ii) `track_id` is a
surrogate over (source, MMSI), so **two hulls sharing one MMSI collapse into ONE
track** with an interleaved impossible trajectory. Lane A explicitly hands kinematic
track-breaking to lane C. Until that exists, association sees one claim where there
are two vessels, and the textbook identity spoof is invisible at this layer. Do not
read "one track_id" as "one vessel".

**Class evidence has a ceiling of about 3 sigma, by construction.** Two silhouettes
can always be confused at some rate, so no single class disagreement can ever be
overwhelming. This is a feature: it forces the identity case to rest on class AND
length together, which is what makes it survive being argued with. Never tune the
confusability matrix to break the ceiling.

**Observed heading and speed have NO uncertainty fields on EoContact**, so
consistency.py assumes 20 degrees and 25%. Those two constants set the significance of
every heading and speed mismatch, hence their severity, hence the verdict confidence —
a heading sigma wrong by a factor of two moves a finding two severity bands. This is
the largest uncalibrated assumption in the module. Request filed to ARCH and lane B.

**The confusability matrix is estimated, not measured.** There is no labelled maritime
EO set here to calibrate it against. It is declared in the module so it can be argued
with, and it must reach `EvidenceRecord.limitations` — never presented as validated.

**Sparse-coverage caveat still applies.** The Bornholm finding in STATUS.md — an AIS
gap from receiver geometry is not an AIS gap from a switched-off transponder — is NOT
handled in this module. A dark candidate here means "no claim within the gate", which
in a coverage hole is a false positive. `DeferReason.sparse_ais_coverage` exists in
contracts.py for exactly this and `verdict.py` must set it.

---

## 5b. verdict.py's declared assumptions — all three reach the evidence card

**The prior, `SPOOF_PRIOR = 1/500`.** The most consequential undeclared number in most
detection systems, so it is declared. There is no authoritative published rate for AIS
identity spoofing in the Baltic and none was invented. Failure direction is asymmetric:
too high and the tool accuses innocent traffic, which is the legally dangerous
direction in a domain where attribution has repeatedly failed in court. When in doubt,
lower it. `verdict_sensitivity()` makes its effect visible.

**`P_DISCREPANCY_GIVEN_SPOOF`** — per-dimension P(discrepancy | the vessel IS
spoofing). Not 1.0, because a competent spoofer picks a plausible cover identity.
Behavioural dimensions are much lower (heading 0.15, speed 0.20) than identity ones
(class and length 0.60): identity spoofing does not *imply* a heading discrepancy, and
a heading mismatch is far likelier a manoeuvre than a lie.

**`CORRELATION_DAMPING = 0.60`.** Likelihood ratios multiply only if independent, and
class and length both ride on the same observation — a bad range estimate corrupts both
together. The strongest dimension counts in full, each weaker one is discounted in
sequence. A deliberate under-count: two dimensions still say much more than one, but
five cannot stack into spurious certainty.

## 6. What verdict.py must do — DONE, kept as the record of what was required

All seven implemented; scenarios 4, 5, 6, 7, 8, 10 in the verdict check are the proof.

1. **Defer on low `assoc_score`, not only on `assoc_ambiguous`.** The coincidental
   dark/silent pairing scores 0.047 and carries no ambiguity flag.
2. **Read `Association.assoc_ambiguous`.** consistency.py logs a warning but computes
   the mismatches anyway — they are real measurements taken against a possibly wrong
   hull. contracts.py: a mismatch off an ambiguous pairing is not evidence.
3. **Weight `independent_dimension_count()` above `strongest_significance()`.** One
   6-sigma dimension is a classifier error waiting to be alleged; two dimensions
   failing for unrelated reasons is a case.
4. **Turn every `Uncomparable` into a defer reason**, and every `suspicious=True` one
   into a weak positive indicator as well — a Class A transponder sending no static
   block is odd, even though it is not a mismatch.
5. **Never let `explained_by_staleness=True` mismatches carry a verdict.** They are
   kept in the record to show what was seen and why it was discounted.
6. **Set `spoof_subtype`**: identity (class/length), position (unmatched claim),
   kinematic (speed/heading beyond the staleness budget).
7. **Set `DeferReason.sparse_ais_coverage` on dark candidates** — the Bornholm caveat.

### 6b. What lane D must know

- **Call `verdict.limitation_strings(result, verdict)`** to fill
  `EvidenceRecord.limitations`. It is a function returning `list[str]`, not a type, so
  `ConsistencyResult` never crosses the lane seam. This is the ONLY channel by which
  "checks we could not run" reaches the case file — ordinary un-run checks deliberately
  do not defer (deferring on every Class B vessel would bury the operator), so without
  this call the report lists agreeing dimensions while silently having compared fewer.
- **Read label AND confidence together.** See section 1c.
- **`VerdictExplanation` is lane C internal** and must not cross into lane D. The
  Verdict carries what lane D needs; the explanation is for debugging and for
  `report.py` to build its LLM prompt from facts rather than a re-derivation.
- **The LLM writes the rationale only.** `Verdict` has nowhere to put model text, by
  design (contracts.py). Keep it that way.

## 7. What blocks the next session

0. **Kinematic track-breaking (one MMSI, two hulls) is lane C's, per lane A's
   handoff.** It is not written. It is the textbook identity spoof and criterion 3
   material — size it before consistency.py, not after.
1. **scipy is not in `.venv`.** `association.py` imports `scipy.optimize` and
   `scipy.stats`; the site-packages listing shows no scipy and no scikit-learn.
   Install and verify BEFORE anything else — see request 5. Watch pip's output for
   `Uninstalling numpy-...`, which is how this environment breaks.
2. `03_src/geometry.py` is empty — request 2 to lane B.
3. No `CameraPose` contract — request 1 to ARCH. The pose is currently five bare
   floats.
4. Real data has not touched this module. Lane A's golden window and lane B's frame
   detections are the first integration; schedule it explicitly.

---

## 8. Suggested STATUS.md line — ARCH to paste, lane C may not edit that file

```
2026-08-28 14:54 UTC | C | association.py written and MEASURED on a ground-truth scene built in 99_scratch/lane_c_association_check.py (the ARCH fixtures cannot test geometry — their positions and bearings come from independent random streams, so scenario_match() correctly yields NO geometric match). Scene: camera 54.5050N 11.2200E, boresight 000T, FOV +/-30, 8 km. 6 EO contacts, 20 AIS claims. RESULT: 4/4 correct pairings, 0 wrong, 2/2 dark, 2/2 position-spoof candidates, 0 of 14 out-of-frustum claims leaked into findings. Two-claims-60m-apart scene correctly flagged assoc_ambiguous. TWO DEFECTS FOUND AND FIXED BY THE HARNESS: (1) ambiguity by score-ratio declared a 0.33-sigma gap unambiguous — replaced by a 1.0-sigma margin; (2) sqrt(chi2/dof) let an agreeing range dimension dilute a 3.5-sigma bearing disagreement down to 2.47 and through the gate — replaced by chi2 survival probability mapped to an equivalent sigma. MEASURED LIMITS for criterion 4: coincidental dark/silent pairing survives the gate below ~250 m cross-range at 5.6 km (scores 0.047, so verdict.py MUST defer on low assoc_score, not only on the ambiguity flag); frustum holds 13 mutually resolvable contacts. THIRD DEFECT, an integration one: lane B's uncalibrated_benchmark_pose sets yaw_uncertainty_deg=180 as a deliberate 'not evidence' marker and eo_detector folds yaw into bearing_uncertainty_deg — a 180-degree sigma removes the bearing term from chi2 and lets RANGE ALONE carry a pairing, so the marker arrived as a permissive gate (verified: matches at 0.172 with the guard off). Guarded by MAX_USABLE_BEARING_SIGMA_DEG=15; such contacts are emitted unmatched-AND-ambiguous, never as DARK. | BLOCKED: scipy is NOT in .venv (association.py will ImportError); 03_src/geometry.py still empty so the shortest-arc helper is temporarily inlined; no CameraPose contract so the pose is passed as five bare floats. 5 requests filed in 99_scratch/requests.md.
```


---

## 9. Suggested STATUS.md line for session 2 — ARCH to paste

```
2026-08-28 21:45 UTC | C | consistency.py written — CRITERION 3. Four dimensions compared (class, length, heading-or-COG, speed), every Mismatch graded with delta + tolerance + significance-in-sigmas + observation_confidence + severity + explained_by_staleness; never a boolean. Class scored through a DECLARED silhouette-confusability matrix, not equality, because cargo-vs-tanker at 6 km is two grey boxes and fishing-vs-tanker is close to proof. Bow/stern flip handled as a MIXTURE likelihood (75% claim, 25% claim+180) so a 180-degree disagreement scores 1.15 sigma and stays out of the evidence card while 90 degrees scores 4.5 and goes in. MEASURED on 12 scenarios, all pass, 5 of them false-positive controls. Criterion 3 exemplar (fixtures.scenario_identity_spoof, 40 m fishing claimed vs 248 m tanker observed): length 6.70 sigma CRITICAL + class 3.09 sigma MAJOR = 2 INDEPENDENT dimensions. Same hull seen badly (conf 0.55, length sigma 90 m): everything MINOR, 0 independent dimensions, strongest 6.70 -> 2.95 — the defer case fixtures.py asks for by name. END TO END through associate(): the spoofing vessel is the BEST geometric match in the scene (assoc_score 0.970 vs the honest vessel's 0.477) because it is exactly where it says it is — position tells you nothing, only identity separates them. That pair is the demo screen. DEFECT FOUND AND FIXED: the class confidence blend double-counted classifier error (the confusability matrix already includes misclassification), which held the exemplar's class mismatch to 2.35 sigma MINOR and collapsed the two-dimension case; observed_class_confidence now scales trust in the matrix against a nominal 0.85 instead. NOT COMPARED, on purpose: position on a matched pair (circular — association already gated it at 3 sigma; the honest consequence is that a position spoof under ~250 m cross-range at 5.6 km is invisible) and width (EoContact has no observed_width_m; deriving beam from bbox aspect would encode aspect angle as width). association.py: shortest_arc_deg promoted to public so consistency.py imports it rather than writing a second copy of angle wrapping. | BLOCKED: scipy still NOT in .venv — both modules will ImportError. 03_src/geometry.py still empty. No CameraPose contract. EoContact has no uncertainty fields for observed heading or speed, so 20 deg and 25% are ASSUMED and are the largest uncalibrated assumption in consistency.py. 8 requests now open in 99_scratch/requests.md. NEXT: verdict.py — handoff_C.md section 6 lists the seven things it must do.
```


---

## 10. Suggested STATUS.md line for session 3 — ARCH to paste

```
2026-08-28 22:30 UTC | C | verdict.py written — LANE C NOW COMPLETE END TO END (associate -> compare -> decide). Deterministic, NO LLM, no scipy import (math.erfc), byte-identical across runs. Labels are contracts.py's MATCH/DARK/SPOOF/UNKNOWN, not the brief's MATCH/DARK_VESSEL/SPOOFING — UNKNOWN is the machine-readable defer state and a three-label system has nowhere to put "we looked and cannot say". CONFIDENCE IS MODEL-CALIBRATED, NOT LEARNED, and says so in CALIBRATION_STATEMENT shipped with every record: no labelled spoofing data exists here, so no scikit-learn Platt/isotonic was fitted; instead consistency.py's sigmas are P(discrepancy|honest), combined as likelihood ratios against a DECLARED 1-in-500 prior with declared per-dimension P(discrepancy|spoof) and 0.6 damping for correlated evidence, giving a posterior rather than an index. DEFER THRESHOLD 0.85, DERIVED NOT PICKED: the Fehmarn day carries 321 distinct vessels, so at a 1-in-500 prior the whole day contains ~0.64 real spoofing vessels; auto-concluding on five vessels at C=0.85 yields ~0.75 false conclusions/day, already comparable to the real event rate, and at C=0.70 it is 1.5 — more than double. Below ~0.85 the tool generates more false conclusions than there are real events in the water. Band: >=0.85 stated, 0.50-0.85 stated AND deferred, <0.50 not alleged. 14 scenarios, ALL PASS, 7 of them false-confidence controls. Exemplar: SPOOF 0.999 identity, 12.7 decades of likelihood ratio, label stable across priors 1/50 to 1/10000. THREE DEFECTS FOUND AND FIXED: (1) stale_ais fired on any staleness-explained mismatch and deferred BOTH end-to-end vessels including the honest one — a fully explained mismatch means ignore that dimension, not escalate; now fires only when staleness left nothing to stand on or the claim is >120 s old. (2) confidence caps were missing so the RANKING was inverted — a lone 6.98-sigma dimension and a finding off a 0.047 association both reported 0.999 and would have outranked a corroborated case that criterion 2 should point the boat at; now capped at 0.80 and 0.70 respectively, on the posterior itself. (3) UNKNOWN reported confidence 0.000, which reads as noise; confidence is confidence in the STATED label and "cannot determine" should be CONFIDENT — now 0.99. THE MOST DANGEROUS FAILURE MODE, closed: with no evidence a Bayesian posterior returns the prior (99.8% honest), so a Class A transponder sending no static block would sail through as a high-confidence MATCH; MATCH confidence is now capped by evidential coverage and below 50% coverage the label is UNKNOWN, not MATCH. | BLOCKED: scipy STILL not in .venv (association.py and consistency.py will ImportError; verdict.py will not). geometry.py still empty. No CameraPose contract. 9 requests open. NEXT: lane D — prioritizer.py must call verdict.limitation_strings() and must read label AND confidence together.
```


---

## 11. Suggested STATUS.md line for session 4 — ARCH to paste

```
2026-08-29 07:15 UTC | C | prioritizer.py EXTENDED IN PLACE (lane D's file, on Amol's instruction; API unchanged and both 04_demo call sites verified working; disclosed in requests.md). Plus 99_scratch/lane_c_prioritizer_check.py — 20 contacts, ranked, top 3 broken out factor by factor. THREE DEFECTS FOUND AND MEASURED. (1) THE PUBLISHED BREAKDOWN DID NOT SUM TO THE PUBLISHED SCORE: `score *= 0.65` ran AFTER the weighted sum, so a deferred SPOOF displayed components summing to 0.9970 beside a score of 0.6481 — a 35% discrepancy in the exact column criterion 2 is marked on. Deferral now damps the two finding-derived components before the sum; breakdown_sums_to_score() is exported and asserted over all 20. (2) A CONFIDENT MATCH BOUGHT URGENCY WITH ITS OWN CONFIDENCE — verdict.py's confidence is confidence in the STATED label, so a MATCH at 0.95 means "95% sure this is FINE" and it was adding 0.1425 to that vessel's priority; measured, an honest vessel ranked FIRST and was the ONLY contact recommended for the boat, above a SPOOF 128 m from the same cable. Confidence is now scaled by label threat-relevance (SPOOF/DARK 1.0, UNKNOWN 0.5, MATCH 0.0). (3) THE SNAPSHOT OVERRODE THE SPAN, found by the new test: behaviour took max(speed proxy, dwell), so a vessel that DECLARED itself at anchor had its dwell correctly discounted to 0.52 and the single-report proxy then returned 1.00 and won — discarding every innocent explanation lane A computes. Dwell now GOVERNS; the proxy's nav-status branch flipped from +0.15 to x0.45 because the same fact was discounting in one path and aggravating in the other. GAPS CLOSED: infrastructure_proximity is now the heaviest weight (0.30, was 0.22 below verdict_severity's 0.32) per the brief's named scenario; Asset gained available/available_in_min and with no hull free the ranking survives while every tasking recommendation is withdrawn; loiter consumes lane A's ais_trajectory.BehaviourEvent duck-typed, so no movingpandas import. MEASURED, 20 contacts: a 0.96-confidence SPOOF 31 km out ranks EIGHTH behind a midfield dark contact — more suspicious, less worth the only boat, which is the actionability argument in one line. An honest vessel loitering on the cable ranks fifth and IS recommended, with all three findings at that cable above it (ordering guarantee, asserted as a test). | NOTE FOR ARCH: consistency.py on disk is NO LONGER the lane C confusability-matrix implementation — another session replaced it. association.py and verdict.py are still byte-identical to lane C's. handoff_C.md sections 1b/3b describe the replaced file and are flagged as such. Rankings WILL move with the reweighting; 04_demo expected output may need refreshing.
```
