# Guarding the Maritime Approaches

**EDTH Hamburg — Topic 02.** A decision-support tool for a watch officer who has one
patrol boat and three thousand transits, and has to decide which single vessel is worth
sending it to.

This repository contains **no data**. It is ~150 KB of code and reasoning. Everything
here runs against data you fetch yourself; §5 tells you how, and you do not need the
data to understand the code.

---

## 1. The problem, in one paragraph

Every large vessel broadcasts AIS — a radio transponder saying "I am *this* ship, of
*this* type, *this* long, at *this* position." It is self-reported and unauthenticated.
Anyone can switch it off, and anyone can lie in it. A camera on a coastline sees
something entirely different: a hull, on a bearing, with a shape and a length in pixels.
Neither source alone is trustworthy. The tool compares them.

## 2. The one idea the whole codebase is built on

**There is a hard wall between what AIS CLAIMS and what the camera OBSERVES.**

It is not a coding convention, it is enforced by the type system in `03_src/contracts.py`:

- `AisTrack` has `claimed_*` fields **only**. Nothing observed can be written to it.
- `EoContact` has `observed_*` fields only, and **no identity field at all** — no MMSI,
  no name, nowhere to put one.

Why so strict: if a single object could hold both an MMSI and an observed length, then
somewhere in a 3000-line codebase at 04:00 a developer writes `contact.length = claim.length`,
and the spoof detector is now comparing a number against itself. It will report perfect
agreement forever and nobody will find out during a demo. The wall makes that a crash
instead of a silent lie.

Everything downstream is just: *project the claim into what the camera should have seen,
compare it to what the camera did see, and quantify the disagreement.*

## 3. The pipeline

```
  CAMERA                                    AIS
    |                                        |
 eo_detector.py                        ais_ingest.py
 YOLOv8n + ByteTrack                   CSV (recorded) or NMEA/TCP (live)
 pinhole bearing model                 dedupe, sentinel-null, dd/mm parse
    |                                        |
    v                                        v
 EoContact  ------------------------->  AisTrack
 (observed_*)          |                (claimed_*)
                       v
               association.py          "which observation is which claim?"
               dead-reckon claims to frame time
               field-of-view gate
               chi-square residual -> sigma
               Jonker-Volgenant global assignment
                       |
                       v
                 Association                 <- pairing only. NEVER judges honesty.
                       |
                       v
               consistency.py           "does the pair agree?"
               per-dimension claimed vs observed -> Mismatch[]
                       |
                       v
                 verdict.py             MATCH / DARK / SPOOF / UNKNOWN
                 deterministic. confidence. defer_to_human.
                       |
                       v
               prioritizer.py           rank against ONE scarce asset
                       |
                       v
               evidence.py              the case file
                       |
                       v
               app.py                   the operator screen (stdlib, offline)
```

## 4. How each verdict is actually reached

### MATCH
One observation pairs with one claim, the residual is small, and every compared
dimension agrees inside tolerance. Boring, and it is 99% of traffic. The value of a
MATCH is that it *removes* a contact from the operator's attention.

### DARK — "a ship is there and nothing is claiming it"
Falls out of association geometrically: an `EoContact` that the assignment step could
not pair with any `AisTrack` (`track_id is None`). That is the whole detection — there
is no separate dark-vessel algorithm.

The hard part is not detecting it, it is **not crying wolf**. An unpaired contact can be:
a small craft with no AIS obligation, a vessel outside the AIS slice's bounding box, a
coverage hole (measured: the Bornholm slice has 2.5x sparser tracks than Fehmarn purely
because of basestation coverage), or a false detection. Those are `defer_reasons`, not
verdicts.

### SPOOF — the discriminator, and the harder case
A vessel that switched AIS off is easy: you see a hull with no transponder. A vessel
**broadcasting a false identity is still exactly where it says it is** — it lies about
*what* it is, not *where*. So it associates perfectly. Detection has to come from
comparing the claim's *content* to the camera's *observation of the same hull*:

| Dimension | AIS claims | Camera observes | Spoof signature |
|---|---|---|---|
| Length | `claimed_length_m` (msg 5, or Size A+B) | pixel extent x range -> metres | 30 m hull claiming to be a 180 m tanker |
| Class | `claimed_ship_type` | VLM silhouette classification | "cargo" claim over a naval/tug silhouette |
| Heading | `claimed_heading_deg` | apparent hull axis in frame | claim says 090, hull points 250 |
| Position | dead-reckoned `claimed_lat/lon` | bearing (+ range) from a surveyed pose | in-view claim with **no** hull where it should be |

Each disagreement becomes a `Mismatch` carrying **both literal values and both source
field names**, so a report sentence can be rebuilt mechanically without trusting any
module's internals. `significance` is in sigmas, not a vibe.

`Association` deliberately pairs a spoofer *happily* and passes it on. If association
rejected pairs that "looked wrong", `consistency.py` would never see the spoof at all.

### UNCERTAIN / defer to human
The `Verdict` type carries `defer_to_human: bool` and `defer_reasons: list`. Stale AIS,
an uncalibrated camera pose, an ambiguous assignment, a claim outside the field of view,
`ship_type == "unknown"` — each suppresses the comparison rather than guessing.

## 5. Techniques, and the case for each

Every technique here was chosen against a named alternative. If a decision has no
alternative it could have gone the other way on, it is not a decision and it is not
listed.

### 5.1 Is AI required? No. Is it useful? In exactly one place.

Take the inventory honestly. Of the eight stages in the pipeline, **six contain no model
of any kind** and never did:

| Stage | Technique | AI? |
|---|---|---|
| AIS ingest | pandas, chunked streaming, explicit `dd/mm/yyyy` parse, dedupe | none |
| Detection | MOG2 background subtraction + connected components (`pi_sensor.py`) | **none** |
| Bearing | `atan2` pinhole model | none |
| Range | waterline depression angle, `h / tan(d)` | none |
| Association | geodesics, dead reckoning, chi-square residual, Jonker–Volgenant | none |
| Consistency | arithmetic on two measurements and a stated uncertainty | none |
| Verdict | thresholds in sigmas + rules | none |
| Prioritization | weighted sum of five measured components | none |
| Evidence | string template over the record | none |

AI is genuinely useful in **one** place: assigning a silhouette class (is that a tanker
or a tug?), which a VLM does well and geometry does badly. That single capability powers
one of the four spoof dimensions. **Length, heading and position spoofing all work with
zero AI**, and so does the entire dark-vessel path.

There is a second, optional use — an LLM writing a nicer rationale sentence — but
`evidence.py` produces a complete, quotable rationale from a deterministic template with
no model, no network and no API key. The LLM is a presentation upgrade, never a
dependency.

**And the no-AI path is not the poor relation.** Three reasons it is the better default
here:

1. **It works on what we are actually pointing the camera at.** The indoor demo uses toy
   boats and printed silhouettes. COCO's `boat` class may not fire on a paper cut-out —
   that was the single identified risk that could kill the live segment. Background
   subtraction does not care what the object *is*; it sees a thing that was not there
   before, against a background that never moves.
2. **It is defensible as evidence.** "A connected region of 340 px, above the fitted
   waterline, aspect 2.8, persisting 14 frames, at these thresholds" survives a
   courtroom. "A network assigned 0.87" does not. Criterion 4 asks for evidence-grade
   output; an explainable detector is *structurally* better at it.
3. **It runs on the sensor.** Classical CV is far cheaper per frame than YOLOv8n on a
   Pi — MOG2 is a per-pixel operation with no neural network at all. The actual Pi
   frame rate is **UNMEASURED**; nothing has run on the Pi. See
   `04_demo/edge_benchmark.md`, which is the instrument and is still empty.

### 5.2 The decision register

| Decision | Rejected alternative | Why |
|---|---|---|
| Claimed and observed are **separate frozen types** | one `Vessel` object holding both | With one object, someone eventually writes `contact.length = claim.length` and the spoof detector compares a number to itself — reporting perfect agreement forever, silently. The wall makes that a crash. |
| Compare in **sigmas** | thresholds in metres/degrees | A 200 m gap at 8 km with a 2° pose error is noise; at 800 m it is a lie. A metre threshold gets both wrong, quietly. |
| Range from **waterline depression** | range from apparent size | Apparent size needs an assumed length. If range came from assumed length, `observed_length` collapses to the claim and the length check **can never fire**. The detector would run, report nothing, and look healthy. |
| Position tested **cross-range only** | pooled position error | At 5 km: bearing error 218 m, monocular range error 1250 m. Pooling them buries a 4.6σ signal at 0.79σ. A monocular sensor measures direction well and distance badly; averaging the two throws away the good measurement. |
| **Jonker–Volgenant** global assignment | greedy nearest-neighbour | Greedy pairs the first contact to the nearest claim and cascades errors down the list. In a dense lane that reorders half the scene. |
| Association **never judges honesty** | reject implausible pairs during matching | A spoofer is exactly where it says it is, so it pairs perfectly. Filtering "wrong-looking" pairs would discard the spoof before anything could examine it. |
| Deterministic **rationale template** by default | always call the LLM | Works offline, cannot hallucinate a fact absent from the record, and keeps the whole project runnable with no AI. |
| **Classical CV** on the sensor | YOLOv8n | See 5.1 — kill-risk, explainability, frame rate. |
| MATCH confidence **capped at 0.95 x evidential coverage** | allow 1.0 | The tool cannot exclude a systematic error it does not know about; a mis-surveyed pose rotates every bearing equally and looks exactly like confidence. The ceiling is a statement about the method. |
| Deferred verdicts are **damped, not dropped** | hide low-confidence findings | Hiding the uncertain cases from the operator is the opposite of criterion 4. |
| Prioritizer includes an **actionability** term | rank by severity alone | A 9σ spoofer two hours away ranks below a 5σ loiterer six miles out. Suspicion is a property of a contact; priority is a property of a contact, an asset and a clock. |
| Web app **has a stdlib + zero-network fallback** | FastAPI + Leaflet only | A demo that needs `pip install` or a CDN is one venue-wifi failure from not existing. |

### 5.3 Dead logic found and removed

The D0 harness exists to catch code that looks like diligence and does nothing. It has
already found two such cases in this repo, both invisible to any test that only checks
for crashes:

- **The class check could never fire.** Significance used the normal quantile, mapping
  0.92 confidence to 1.41σ — permanently below the 2.0 reporting threshold. The check
  ran on every pair, cost time, and was mathematically incapable of producing a finding.
  Replaced with log-odds (`ln(p/(1-p))`), the natural scale for evidence.
- **The position check could never detect a position spoof.** See 5.2, row 4.

Both were found by injecting a *known* fault and noticing it did not come back. That is
the argument for the harness in one sentence.

## 6. Get the data

**Source:** Danish Maritime Authority bulk AIS. Free, governmental, Baltic, no rate limit.
The bucket name contains dots, so **virtual-hosted HTTPS breaks against the wildcard cert**
— use path-style, or you will chase a TLS error for an hour:

```bash
curl -sSO https://s3.eu-central-1.amazonaws.com/aisdata.ais.dk/aisdk-2026-08-25.zip
unzip aisdk-2026-08-25.zip -d 02_data/raw/
python 02_data/subset_ais.py            # cuts 3 candidate areas in one streaming pass
python 02_data/make_golden_window.py    # 1 h demo window, pseudonymised
```

~894 MB zipped, 5.88 GB decompressed, 32.6 M rows. That is why it is not in git — and
why `02_data/slices/` is not either (one slice is 248 MB, over GitHub's hard 100 MB
per-file limit).

**Licence, and why it constrains us:** Danish act no. 596 of 24 June 2005. No warranty.
**No combining with other datasets to identify individuals** without Danish DPA
authorisation. Redistribution is *not addressed* — silence, not permission. Combined with
the hard rule against naming real vessels, the ruling is: **only pseudonymised golden
windows are ever committed**, with synthetic MMSIs on the 999 prefix (MID 999 is
unassigned to any country, so it is recognisably fake).

**There is no training data and no trained model.** `yolov8n.pt` is stock pretrained
COCO weights; we use the `boat` class by name. Nothing in this project is trained. If a
teammate asks "where are the labels" — there are none, and that is a design choice: a
hackathon-trained detector is a liability you cannot characterise, and criterion 4 asks
for honest limits.

`02_data/INVENTORY.md` records every measured number about the real data. Read it instead
of downloading, if you only need to understand.

## 7. Five properties of the real AIS data that will silently corrupt your results

Measured on the Fehmarn Belt slice, not assumed. All five are handled in `ais_ingest.py`.

1. **63.5% of rows are duplicate receptions.** Several coastal basestations hear one
   transmission; DMA writes a row for each. Unhandled, every reporting rate inflates 2.75x
   and fusion sees three identical candidates per hull.
2. **Aids-to-navigation are in the file.** 16 MMSIs are buoys — a claim with no possible
   observation, i.e. an eternal false "position spoof".
3. **ITU sentinels.** DMA has pre-decoded them, but the live `pyais` path delivers them raw
   (speed 102.3, course 360.0, heading 511). Both paths null them; the CSV path *counts*
   them, so a source that stops pre-cleaning shows up as a counter moving rather than as
   data quietly changing meaning.
4. **"Unknown" is a string, not a null** — 295,344 IMO rows. A naive reader puts a claimed
   IMO of `"Unknown"` onto an evidence record.
5. **Timestamps are dd/mm/yyyy.** pandas defaults to month-first and mis-parses every date
   with day <= 12 **without raising**. One parse site, explicit format.

## 8. Module status

| File | Owner | Lines | State |
|---|---|---|---|
| `contracts.py` | ARCH | 438 | Done. 7 frozen pydantic types, `extra=forbid`, zero logic |
| `ais_ingest.py` | A | 851 | Done. 24 tests, one per silent-failure mode |
| `eo_detector.py` | B | 888 | Done. Bearing maths unit-verified; FPS measured 45 on M4 |
| `association.py` | C | 686 | Done. Geometry only |
| `consistency.py` | C | 529 | Done. Per-dimension claimed-vs-observed, in sigmas |
| `verdict.py` | D | 409 | Done. MATCH/DARK/SPOOF/UNKNOWN + calibrated confidence |
| `prioritizer.py` | D | 339 | Done. Weighted ranking incl. actionability |
| `evidence.py` | D | 256 | Done. Case file + deterministic rationale, no model |
| `geometry.py` | B | 0 | Empty; `association.py` has a temporary local copy of `_shortest_arc_deg` |

`03_src` starts with a digit, so it is not a legal Python package name. Imports are flat
via `sys.path` — see `tests/conftest.py`.

## 9. Run the demo — two commands, no downloads

A fully synthetic 16-vessel scenario is committed, so this works on a fresh clone.
(It was widened from 8 to 16 because the harness warned it had no out-of-view vessel
to build a coverage-hole control from, and a scene with no controls cannot measure a
false-positive rate.)

```bash
python 04_demo/make_synthetic_eo.py --ais 04_demo/demo_scenario.csv --out 04_demo/out/scene01
python 04_demo/run_pipeline.py --scene 04_demo/out/scene01
python 04_demo/app.py --scene 04_demo/out/scene01     # then open http://127.0.0.1:8000
```

The app is Python standard library only and the page loads nothing from the network —
no CDN, no map tiles, no fonts. It runs with the wifi off, which at a hackathon venue is
not a nicety.

**The sensor node** (`04_demo/pi_sensor.py`) runs on a Raspberry Pi with a camera module
and needs only `opencv-python` and `numpy`. It detects hulls with background subtraction
and connected components — **no neural network** — computes bearing through the same
pinhole model and range from the waterline depression angle, and POSTs EoContact JSON to
the app. It has no AIS connection at all, which is the claimed/observed wall made
physical: it cannot leak an identity into an observation because it has never been told
one.

## 10. What this is not

- **Not automated enforcement.** It ranks and evidences. A human decides. Nothing here
  determines intent, and nothing here should ever be wired to an actuator.
- **Not an accusation.** Output about real vessels stays local and pseudonymised.
  Attribution in this domain is legally fragile — cases have failed and suspect vessels
  have been released. The output is the *beginning of a case file*, not a verdict of guilt.
- **Not a confidence machine.** Every verdict carries a calibrated confidence and an
  explicit defer-to-human threshold.
