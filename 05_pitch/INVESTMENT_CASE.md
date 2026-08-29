# The investment case — and the case against it

*Written 2026-08-29, event day 2. Every number in section 1 is quoted from a file in this
repo and is reproducible. Every number in section 3 is from a named public source. Nothing
here is estimated; where a figure is not measured, this document says so.*

---

## 0. The question, and why the honest answer is a different one

The question asked was: *why is this the best in the world, what is the out-of-this-world
secret, make the argument an investor cannot refuse.*

The measured state of this repository does not support that claim, and it is worth being
precise about why, because the reason is also the argument.

From `04_demo/failure_modes.md`, generated 2026-08-29T13:39 by `03_src/report.py` over
scene01 — not hand-written, not estimated:

| Measured fact | Value |
|---|---|
| Injected faults labelled correctly | **2 of 6 (33.3%)** |
| Of the 4 misses, ones that returned `MATCH` | **2** |
| Verdicts flagged `defer_to_human` | **8 of 8 (100%)** |
| Actionable false positives over 4 control cases | **0 (0.0%)** |
| Of those 4 controls, ones that were `NO_RECORD` (silence from absence, not judgement) | **2** |
| Of those 4 controls, ones labelled DARK/SPOOF and rescued only by the defer flag | **1** |
| Evidential coverage across 4 matched pairs | **80%** (class dimension: **0%** comparable) |
| Cameras that have observed an actual vessel in this project | **0** |

A `MATCH` returned on an injected spoof is the system vouching for a claim that was false.
The document says it plainly: *"MATCH is the tool vouching for a claim that was false, and
it is the failure that would matter in court."* That sentence was written by this project
about itself.

**So: any pitch built on detection performance loses.** The first judge or investor who
opens `failure_modes.md` — a file this project generated, published, and is proud of —
ends the conversation. A claim of world-beating detection would be refuted by the
repository's own output, which is the single worst way to be wrong in front of a buyer.

There is a strong argument here. It is not that argument.

---

## 1. The actual thesis: this is not a detector, it is an admissibility instrument

**The market failure is not that vessels go undetected. It is that detections do not
survive contact with a decision.**

The causal chain, which is the whole pitch:

1. A watch officer sees a suspicious contact.
2. To act, they must commit a scarce asset — one patrol boat against thousands of transits.
3. To hold the vessel afterwards, the finding must survive a legal challenge.
4. Step 3 fails routinely. Suspect vessels in Baltic cable incidents have been released
   because intent could not be proven.
5. Therefore the binding constraint on the whole system is **the evidentiary quality of the
   finding**, not the sensitivity of the detector.

Everyone at this hackathon — and every incumbent — is optimising step 1. The value is in
steps 2 through 4, and they are engineering problems that almost nobody treats as such.

Four things in this repository attack steps 2–4 directly. They are measured, they are
defensible, and they are what makes this different in kind rather than in degree.

### 1.1 The blind spots are published, derived, and exact

From `03_src/consistency.py`, `check_position()`, quoted in `failure_modes.md` §6.1 —
measured at 5 km:

```
cross-range (bearing) sigma : 2.5 deg x 5000 m  =  218 m
along-range (range) sigma   : 25% of 5000 m     = 1250 m
combined                    :                     1269 m

a 1000 m injected spoof -> 0.79 sigma combined  (invisible)
                        -> 4.58 sigma cross-range only (obvious)
```

**A vessel that displaces its broadcast position directly toward or away from the camera is
not detectable by one monocular sensor, at any offset, ever.** Not difficult — forbidden.
There is no measurement to make. No threshold, no model and no volume of training data
changes it. Fixing it requires a second baseline, a radar, or the vessel to move.

And from `01_research` / lane A's measurements on the real Danish Maritime Authority slice
for the demo day:

| Measurement | Value |
|---|---|
| AIS gaps over 5 min, Fehmarn slice, 24 h | 3,265 |
| AIS gaps over 10 min, across 108 vessels | 660 |
| Messages per vessel, Fehmarn | 3,556 |
| Messages per vessel, Bornholm, **same day** | 1,432 |
| Bornholm sparsity relative to Fehmarn | **2.5x sparser** |

Same day, same transponders, 2.5x sparser tracks — from receiver geometry alone. **An AIS
gap caused by coverage is not an AIS gap caused by evasion, and this pipeline cannot tell
them apart.** It does not pretend to.

*Why this is an asset and not a confession:* the buyer is a government agency that will be
audited on its decisions. A vendor who cannot state where their system is blind cannot be
procured by anyone who will later be asked to justify a boarding. Every competitor pitching
camera-based spoof detection either has not measured 6.1 or is not saying it. Publishing it
is the credential.

*The line to use:* "Every other tool you will see today tells you what it catches. This is
the only one that will tell you, with the arithmetic, what it cannot catch — and that is
the half a court will ask about."

### 1.2 The confidence number has an arguable meaning, and the threshold is derived

Most tools ship a 0–100 "risk score" with no stated semantics. This one ships a posterior.

From `03_src/verdict.py` and its `CALIBRATION_STATEMENT`, shipped with **every** record:
significances from `consistency.py` are P(discrepancy this large | claim honest) under a
declared noise model, combined as likelihood ratios against a declared **1-in-500 prior**,
with declared per-dimension P(discrepancy | spoof) and 0.6 damping for correlated evidence.

The honest phrase is *"calibrated against a declared model"* — never *trained* or
*validated*. There is no labelled maritime spoofing data in this project, so no Platt or
isotonic calibration was fitted. Fitting on invented labels produces a number that looks
trained and means nothing.

**The defer-to-human threshold of 0.85 is derived from the dataset, not chosen by taste:**

- The Fehmarn demo day carries **321 distinct vessels** after dedup.
- At a 1-in-500 prior, the whole day contains **~0.64 real spoofing vessels**.
- A tool stating conclusions at confidence C is wrong about (1−C) of them.
- Auto-concluding on five vessels a day at C=0.85 → **~0.75 false conclusions/day** —
  already comparable to the real event rate.
- At C=0.70 it is **1.5/day**, more than double the real rate, and the watch officer
  correctly stops believing the tool.

Band: ≥0.85 stated; 0.50–0.85 stated **and** deferred; <0.50 not alleged.

*Why this matters commercially:* it is the only answer to "how sure are you?" that survives
cross-examination, and it is the thing an agency's own risk officer can argue with rather
than accept. It converts a black box into a negotiable instrument.

### 1.3 Ranking by consequence, not by suspicion — and the operator can see the sum

Criterion 2 of the brief, and the place the whole category fails.

From `03_src/prioritizer.py`: infrastructure proximity carries the **heaviest weight
(0.30)**, above verdict severity (0.26). That ordering is deliberate and it is the product
thesis in one number — sorting by suspicion is what every flat alert list already does, and
suspicion is not consequence.

**Measured over 20 contacts: a 0.96-confidence SPOOF 31 km out ranks EIGHTH**, behind a
midfield dark contact. More suspicious, less worth the only boat.

Two supporting decisions, both measured:

- **Confidence in a benign label is not urgency.** A `MATCH` at 0.95 means "95% sure this
  vessel is FINE." Fed raw into the score, an honest vessel took 0.1425 of its own priority,
  ranked **first**, and was the only contact recommended for the boat — above a SPOOF 128 m
  from the same cable. Now scaled by threat-relevance: SPOOF/DARK 1.0, UNKNOWN 0.5, MATCH 0.0.
- **The breakdown sums to the score exactly**, asserted as an invariant over all 20 contacts
  by `breakdown_sums_to_score()`. A prior version applied deferral damping *after* the
  weighted sum and displayed components summing to 0.9970 beside a score of 0.6481 — a 35%
  discrepancy in the one column the operator is asked to trust.

*Why an investor should care:* this is the only component of the system that is a genuine
product opinion rather than an implementation. It is also the one that is directly
observable in a demo, in ten seconds, by a non-technical buyer.

### 1.4 The identity case is staleness-immune, and that is why it carries the verdict

Criterion 3, the discriminator. From `03_src/consistency.py`:

Heading and speed mismatches are **always arguable** — the vessel manoeuvred, the report was
stale, the current set it sideways. A defence lawyer dismantles them for free.

**A ship does not change class or grow 200 metres in ninety seconds.**

So the identity dimensions — length (physical extent, cannot be argued with) and class
(scored through a declared silhouette confusability matrix, deliberately capped near 3 sigma
so it can never convict alone) — are what the case rests on. Measured on the criterion 3
exemplar (40 m fishing claimed vs 248 m tanker observed): length **6.70 sigma CRITICAL** +
class **3.09 sigma MAJOR** = 2 independent dimensions. The same hull seen badly (confidence
0.55, length sigma 90 m): all MINOR, 0 independent dimensions — the defer case.

And the result that should be the demo screen: **end to end, the spoofing vessel is the
BEST geometric match in the scene** (assoc_score 0.970 vs the honest vessel's 0.477) —
because it is exactly where it says it is. **Position tells you nothing. Only identity
separates them.** That single fact is the argument for why AIS-only tools cannot solve
criterion 3, and why an EO feed is not a nice-to-have.

---

## 2. What is *not* here, stated plainly

An investor will find these in the first hour of diligence. Saying them first is worth more
than the ground they cost.

1. **There is no moat.** No proprietary data, no trained model, no patent, no network
   effect. Everything in section 1 is engineering judgment that a competent team could
   reproduce in a quarter. The defensibility, if it comes, is (a) the calibration
   methodology becoming a certifiable artifact, (b) regulatory timing, (c) being the
   reference implementation an agency adopts. None of those exist yet.
2. **No camera has observed a vessel.** EO contacts are forward-projected from real DMA AIS
   through a declared camera and noise model. Detection rates therefore measure *decision
   logic against faults this project handed itself* — internal consistency, not field
   performance. This is stated in `failure_modes.md` §1 and must be stated on stage.
3. **100% deferral is not yet a virtue.** `verdict.py`'s own notes: *"a tool that defers on
   everything is an expensive alarm, not decision support."* Much of the current 8-of-8 is a
   configuration artifact — `run_scene()` does not pass a coverage confidence, so it uses
   `DEFAULT_AIS_COVERAGE_CONFIDENCE = 0.7`, which is below `SPARSE_COVERAGE_THRESHOLD = 0.75`
   and raises `sparse_ais_coverage` on every verdict. Fixable, and must be understood before
   it is quoted either way.
4. **The control set is n≈1, not n=4.** Of four control cases, two produced `NO_RECORD` —
   silence from absence, not from judgement — and one was labelled DARK and rescued only by
   the defer flag. The "0 actionable false positives" figure is true and nearly weightless.
5. **The weakest physical assumption (A5):** the bottom of the bounding box is not reliably
   the waterline — wake, spray, clipping, near-side hull. Every range estimate rests on it.
   Say it first when asked, not second.
6. **Monocular ranging is short-range.** Measured at 1920x1080, 70° lens, 20 m height:
   usable to **~1.1 km**; at 8.5 km relative sigma is 578%. Doubling range from 8.5 km to
   16.7 km moves the waterline **0.8 pixels**. Any range claim past a kilometre is false, and
   the fix is a second bearing from a separate site, not better software.

---

## 3. Why the market is real, from government sources

This is the half of the argument that does not depend on the code being good yet.

**The money is appropriated and the pilot is in the Baltic.** The European Commission's
Connecting Europe Facility (CEF) Digital work programme, updated **11 February 2026**,
allocates **€347 million** to submarine cable security. Within it:

| Line | Amount | Purpose |
|---|---|---|
| Monitoring tools call | **€20 million** | tools that detect damage and track conditions under the sea |
| Cable repair equipment, 2026 | €60 million | repair equipment at ports and shipyards |
| Priority projects, 2026–27 | €267 million | 13 designated cable projects of European interest |
| Rapid repair initiative | €20 million | **initial pilot in the Baltic Sea** |

**The policy pillar is named "detection".** The EU Action Plan on Cable Security
(JOIN(2025) 9, presented 21 February 2025) is built on prevention, **detection**, response
and recovery, and deterrence, and explicitly calls for *"enhancing threat-monitoring
capabilities per sea basin, such as the Mediterranean or the Baltic Seas, to build a
comprehensive situational picture."* Submarine cables carry 99% of intercontinental
internet traffic.

**The buyer already procures this class of service.** EMSA operates Copernicus Maritime
Surveillance and CleanSeaNet, and has issued RFIs specifically for *maritime anomaly
detection systems*. This is not a market that must be created — it is a procurement
category with an existing budget line and an existing tender vocabulary.

**The threat is measured at scale by third parties.** Windward's Q2 2026 figures, cited as
vendor-published rather than government data: 2,157 unique cargo ships and tankers over
10,000 DWT conducting at least one prolonged dark activity event; **3.35 million false
ship-to-ship meetings** since 28 February 2026 caused by injected positioning coordinates
that made vessels appear to rendezvous when no meeting took place; 275 internationally
trading tankers broadcasting the flag of a fraudulent registry across 22 fraudulent
registries. The EU Council continues to list shadow-fleet vessels package by package.

*The inference chain:* position spoofing at that volume means AIS position, on its own, has
stopped being evidence. That is precisely the condition under which an independent
observation — a camera — stops being a supplement and becomes the only ground truth. The
market is not "cable monitoring". It is **independent verification of a claim that has been
industrially falsified.**

---

## 4. Where the incumbents are not

Windward, Spire, Kpler and the satellite-RF players operate at **global, fleet, commercial-
compliance scale**: AIS + SAR + RF, sold to insurers, traders and flag states for risk
scoring across thousands of hulls.

What they do not publish, in any material reviewed: a false positive rate, a stated
calibration basis for their confidence numbers, or an evidentiary standard.

The unoccupied segment is the other end of the telescope:

| | Incumbents | This |
|---|---|---|
| Scale | Global fleet | One approach, one cable corridor, one wind farm |
| Sensor | Satellite SAR/RF/AIS | A camera on a pier, plus the AIS picture |
| Customer | Insurers, traders, flag states | Port authority, cable operator, wind farm operator, coastal agency |
| Asset being allocated | Analyst attention | **One boat, tonight** |
| Output | Risk score | A case file with stated limits |
| Confidence semantics | Unstated | Posterior against a declared prior |

*Why the segment is defensible on its merits:* at 2–8 km from a pier the satellite
revisit is hours and the SAR pixel is metres; the camera is continuous and the target is
the identity, not the position. A global risk score does not tell a harbourmaster which of
the three contacts off the cable route gets the boat at 02:00.

---

## 5. The raise-able sentence

> **The EU has appropriated money to protect its seabed infrastructure and has no standard
> for what counts as proof that a vessel did it. We are building that standard, and the
> reference implementation of it.**

The detector is the demo. The methodology is the product. The audit trail is the moat that
does not exist yet and is the only one worth building.

**What this justifies raising for, honestly:** not a Series A on detection performance. A
pre-seed / grant-funded programme whose milestones are (1) a real camera on real water with
a measured detection rate and a measured false-positive rate against surveyed truth,
(2) a second baseline to close the along-line-of-sight blindness in §1.1, (3) the
calibration methodology written up against a named agency's evidentiary requirements. The
CEF **€20 million monitoring-tools call** is the natural first non-dilutive instrument, and
it does not require the moat to exist yet.

---

## 6. What to actually say on stage tomorrow

In order, because the order is the argument:

1. **Open with the ranking result.** "A 0.96-confidence spoofer 31 km away ranks eighth on
   our list, behind a dark contact in the middle distance. More suspicious. Less worth the
   only boat you have." — measured, counter-intuitive, criterion 2, and nobody else has it.
2. **Then the identity line for criterion 3.** "Heading and speed are always arguable. A
   ship does not change class or grow 200 metres in ninety seconds." Then the screen where
   the spoofer is the *best* geometric match at 0.970 and only identity separates it from
   the honest vessel.
3. **Then open `failure_modes.md` in front of them.** "Two of six. A hundred percent
   deferred. Zero cameras have seen a real ship. Here is the geometry that says a
   line-of-sight position spoof is invisible to us forever." — This is the moment that wins
   or loses the room, and it is a much better moment than a fabricated 95%.
4. **Close on the threshold derivation.** 321 vessels, 1-in-500, 0.64 real events, 0.75
   false conclusions at 0.85. "We did not pick that number. We derived it, and we will show
   you the arithmetic."

Do not claim: world-leading, best-in-class, state-of-the-art, validated, trained, or any
detection percentage as a capability rather than a self-test. Every one of them is refuted
by a file in this repository, and the repository is public.

---

## Sources

- [EU Action Plan on Cable Security, JOIN(2025) 9 — EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=JOIN%3A2025%3A9%3AFIN)
- [European Commission / High Representative present actions on submarine cable security, 21 Feb 2025](https://ireland.representation.ec.europa.eu/news-and-events/news/european-commission-and-eu-high-representative-foreign-affairs-and-security-policy-present-strong-2025-02-21_en)
- [€347 million to protect Europe's submarine cables — EU Blue Economy Observatory, 11 Feb 2026](https://blue-economy-observatory.ec.europa.eu/news/eu347-million-protect-europes-submarine-cables-2026-02-11_en)
- [Submarine Cable Security Toolbox and Cable Projects of European Interest — European Commission](https://digital-strategy.ec.europa.eu/en/library/submarine-cable-security-toolbox-and-cable-projects-european-interest)
- [Copernicus Maritime Surveillance — EMSA](https://www.emsa.europa.eu/copernicus.html)
- [CleanSeaNet satellite-based services — EMSA](https://www.emsa.europa.eu/csn-menu.html)
- [EMSA RFI, Maritime Anomaly Detection Systems (c.2_RFI_2015_208088)](https://emsa.europa.eu/technical-ppr/download/4053/2676/41.html)
- [Council sanctions 41 vessels of the Russian shadow fleet — Consilium, 18 Dec 2025](https://www.consilium.europa.eu/en/press/press-releases/2025/12/18/russia-s-war-of-aggression-against-ukraine-council-sanctions-41-vessels-of-the-russian-shadow-fleet/)
- [Where Maritime Domain Awareness Is Heading in 2026 — Windward (vendor-published, not government data)](https://windward.ai/blog/where-maritime-domain-awareness-is-heading/)

Repository sources: `04_demo/failure_modes.md` (generated 2026-08-29T13:39),
`03_src/consistency.py`, `03_src/verdict.py`, `03_src/prioritizer.py`, `03_src/geometry.py`,
`99_scratch/handoff_A.md`, `STATUS.md`.
