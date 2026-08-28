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
               consistency.py           "does the pair agree?"   [NOT WRITTEN]
               per-dimension claimed vs observed -> Mismatch[]
                       |
                       v
                 verdict.py             MATCH / DARK / SPOOF / UNCERTAIN  [NOT WRITTEN]
                 deterministic. confidence. defer_to_human.
                       |
                       v
               prioritizer.py           rank contacts against one scarce asset  [NOT WRITTEN]
                       |
                       v
               evidence.py + report.py  the case file  [NOT WRITTEN]
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

## 5. Get the data

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

## 6. Five properties of the real AIS data that will silently corrupt your results

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

## 7. Module status

| File | Owner | Lines | State |
|---|---|---|---|
| `contracts.py` | ARCH | 438 | Done. 7 frozen pydantic types, `extra=forbid`, zero logic |
| `ais_ingest.py` | A | 851 | Done. 24 tests, one per silent-failure mode |
| `eo_detector.py` | B | 888 | Done. Bearing maths unit-verified; FPS measured 45 on M4 |
| `association.py` | C | 686 | Done. Geometry only |
| `consistency.py` | C | 0 | **Empty — criterion 3 lives here** |
| `verdict.py` | D | 0 | **Empty** |
| `prioritizer.py` | D | 0 | **Empty — criterion 2, where most teams fail** |
| `evidence.py` / `report.py` | D | 0 | **Empty — criterion 4** |
| `geometry.py` | B | 0 | Empty; `association.py` has a temporary local copy of `_shortest_arc_deg` |

`03_src` starts with a digit, so it is not a legal Python package name. Imports are flat
via `sys.path` — see `tests/conftest.py`.

## 8. What this is not

- **Not automated enforcement.** It ranks and evidences. A human decides. Nothing here
  determines intent, and nothing here should ever be wired to an actuator.
- **Not an accusation.** Output about real vessels stays local and pseudonymised.
  Attribution in this domain is legally fragile — cases have failed and suspect vessels
  have been released. The output is the *beginning of a case file*, not a verdict of guilt.
- **Not a confidence machine.** Every verdict carries a calibrated confidence and an
  explicit defer-to-human threshold.
