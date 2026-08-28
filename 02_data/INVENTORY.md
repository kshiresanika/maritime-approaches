# INVENTORY — Danish Maritime Authority bulk AIS

Lane A owns this file. Every number here was MEASURED from the files on disk on
2026-08-28. Nothing is estimated, and nothing is carried over from documentation.
Where a number came from a different tool than the pipeline itself, that is stated.

---

## 1. What was downloaded

| | |
|---|---|
| Source | Danish Maritime Authority bulk AIS, S3 bucket `aisdata.ais.dk` (eu-central-1) |
| URL form | `https://s3.eu-central-1.amazonaws.com/aisdata.ais.dk/aisdk-2026-08-25.zip` |
| Day | Tuesday **25 August 2026** — the most recent daily file available |
| Archive | `02_data/raw/aisdk-2026-08-25.zip`, **894,129,645 bytes** |
| Integrity | Byte count matches the S3 `Content-Length` exactly; `unzip -t` reports "No errors detected" (CRC verified) |
| Decompressed | `02_data/raw/aisdk-2026-08-25.csv`, **5,881,994,790 bytes** |
| Rows, whole day | **32,661,798** (counted, whole file streamed) |
| Coverage | All Danish waters, one calendar day |

**Path-style URLs are mandatory.** The bucket name contains dots, so
virtual-hosted-style S3 puts them in the hostname and fails TLS against the
`*.s3.eu-central-1.amazonaws.com` wildcard certificate — one label only. Path-style
moves the bucket name into the path and HTTPS works, which also means a captive
portal cannot intercept the request. This cost several hours once already.

---

## 2. LICENCE — read before anything leaves this machine

Access is granted under **Danish act no. 596 of 24 June 2005** on the further use of
public sector information.

1. **No warranty.** DMA does not guarantee the correctness of the data and accepts
   no liability for its use. Consequence for us: a missing report is not evidence
   that a vessel went dark. It may be evidence that the receiver missed it.
2. **No re-identification of individuals.** Recipients may **not** combine AIS with
   other datasets to identify individuals without authorisation from the Danish Data
   Protection Agency. Consequence: enriching a track with an ownership or crew
   registry is out of scope for this build, full stop.
3. **Redistribution is not addressed.** The published policy is silent on
   redistribution and attribution, and DMA is separately authorised to sell AIS data
   commercially.

**"Free to download" is therefore not "openly licensed", and this matters for the
first commit.** Committing raw DMA rows to a public repository is a redistribution
under terms that have not been granted. It also collides with CLAUDE.md's hard rule
that no real named vessel or operator may appear in anything public-facing.

**Lane A's ruling: only pseudonymised golden windows are committed.** See §6. The
real-identity files and the MMSI maps stay gitignored. This costs nothing and
removes both problems.

**Open action for the pitch:** if a slide is going to claim an open licence, get that
in writing from DMA first. Until then the slide says "free government data, licence
terms recorded, redistribution not claimed". This is criterion 4 territory, not
paperwork.

---

## 3. The working slice — `fehmarn_belt`

Cut by `02_data/subset_ais.py` from the full day. Chosen over Great Belt and
Bornholm on measured track continuity, and because the Fehmarnbelt corridor carries
real seabed infrastructure, so the cable-proximity narrative is honest rather than
decorative.

| | |
|---|---|
| File | `02_data/slices/aisdk-2026-08-25_fehmarn_belt.csv` |
| Size | **248,265,175 bytes** |
| Rows | **1,198,373** |
| Unique MMSIs | **337** total |
| Time span | **2026-08-25 00:00:00Z → 2026-08-25 23:59:58Z** (23 h 59 m 58 s) |
| Requested bbox | lat 54.40–54.80 N, lon 11.00–11.80 E |
| Actual data extent | lat **54.400007 – 54.758508** N, lon **11.000000 – 11.799997** E |
| Rows after dedupe + AtoN exclusion | **431,751** |
| Vessel MMSIs after exclusion | **321** |

The northern edge of the data stops 0.04° short of the requested boundary. That is
where the traffic stops, not where the data does.

### Composition by mobile class

| Type of mobile | raw rows | unique MMSI | note |
|---|---:|---:|---|
| Class A | 1,141,661 | 161 | full static + voyage data |
| Class B | 45,740 | 178 | sparse static data; a blank ship type here is NORMAL |
| AtoN | 10,972 | 16 | **not vessels** — buoys and beacons. Excluded by default |

No MMSI appears under more than one mobile class.

### Claimed ship type — raw row counts, all 1,198,373 rows

| Ship type | rows | → VesselClass |
|---|---:|---|
| Tug | 264,315 | `tug` |
| Cargo | 196,558 | `cargo` |
| Passenger | 194,163 | `passenger` |
| Other | 187,933 | `unknown` |
| Tanker | 98,567 | `tanker` |
| Port tender | 51,588 | `unknown` |
| HSC | 44,263 | `unknown` |
| Dredging | 38,739 | `unknown` |
| Sailing | 34,598 | `small_craft` |
| Undefined | 31,355 | `None` — no claim was made |
| Pleasure | 14,974 | `small_craft` |
| SAR | 13,698 | `unknown` |
| Law enforcement | 8,496 | `unknown` |
| Fishing | 7,984 | `fishing` |
| Diving | 3,769 | `unknown` |
| Pilot | 3,484 | `unknown` |
| Reserved | 3,420 | `unknown` |
| Towing | 469 | `tug` |

The Tug and Port tender volume is the Fehmarnbelt tunnel construction fleet. It is
also why this area is a good demo: a lot of slow, loitering, working craft near
infrastructure is exactly the population a naive anomaly detector drowns in.

**`unknown` is not `None`.** `unknown` means the transponder made a claim that is not
comparable to a camera silhouette; `None` means it claimed nothing. Roughly 355k rows
(29.6%) carry a service-craft type with no VesselClass equivalent, and mapping those
by resemblance would manufacture SPOOF verdicts inside lane A. See the policy note in
`03_src/ais_ingest.py`.

---

## 4. GAPS AND DATA QUALITY

This is the criterion 4 section. Every item is a reason the system might be wrong.

### 4.1 Duplicate receptions — 63.5% of rows

**761,196 of 1,198,373 rows** are exact duplicates on `(Timestamp, MMSI, Latitude,
Longitude)`. Distinct `(Timestamp, MMSI)` pairs: **436,221**.

Several DMA basestations receive the same transmission; DMA writes one row per
reception. Unhandled, every vessel appears to report ~2.75× more often than it does,
and any "this vessel has gone quiet" threshold calibrated on that rate fires late or
never. `ais_ingest.DmaCsvSource` deduplicates by default and counts the drops.

### 4.2 Reporting interval, after dedupe — 431,751 vessel reports

| statistic | seconds |
|---|---:|
| median gap | 10.0 |
| p90 | 22.0 |
| p99 | 235.0 |
| max | 63,513 (17 h 39 m) |

| threshold | count | share |
|---|---:|---:|
| gap > 60 s | 22,515 | 5.22 % |
| gap > 600 s | 660 | 0.15 % |
| gap > 1800 s | 146 | 0.03 % |

**A GAP IN THIS FILE IS NOT A SWITCHED-OFF TRANSPONDER.** Three different things
produce one:

1. **The vessel left the bounding box.** This slice is a 0.4° × 0.8° cut. A ship
   crossing the box exits the data while still transmitting normally. That is what
   the 17-hour maximum is — a vessel that left and came back, not a vessel that went
   dark for 17 hours.
2. **The receiver missed it.** See §4.4.
3. **The transponder was switched off.** Only this one is a finding.

Lane A cannot distinguish these from the AIS file alone. Anything that reports a
dark-vessel verdict from an AIS gap must either have an EO observation in the same
place at the same time, or carry `defer_reasons=["sparse_ais_coverage"]`. A gap that
ends at the box edge should never be scored as evasion.

### 4.3 Reports per vessel — the long tail is real

Min **1**, median **492**, max **13,510**. **87 of 321 vessels (27%) have fewer than
100 reports** across the whole day — mostly transits that clip a corner of the box.

Consequence for lane C: those vessels have thin kinematic history, so a
position-consistency test over them rests on very few points. That is a
`defer_to_human` condition, not a reason to lower the threshold.

### 4.4 Coverage geometry — the Bornholm comparison

Measured across the three candidate areas cut from the same day:

| area | rows | unique MMSI | msgs/vessel |
|---|---:|---:|---:|
| fehmarn_belt | 1,198,373 | 337 | 3,556 |
| great_belt | 494,466 | 172 | 2,875 |
| bornholm | 485,449 | 339 | 1,432 |

Bornholm has the **most** vessels and **2.5× sparser** tracks. That is the edge of
Danish basestation coverage. Same ocean, same transponders, different receiver
geometry — and a dark-vessel detector tuned on Fehmarn and run over Bornholm would
generate false positives from antenna range and call them evasion.

**This is a pitch slide, not a footnote.** It is the cleanest available demonstration
that the system knows where it cannot tell.

### 4.5 Missing fields — per raw row (including duplicates and AtoN)

| column | rows absent | share |
|---|---:|---:|
| ROT | 306,833 | 25.6 % |
| Heading | 120,243 | 10.0 % |
| Draught | 81,402 | 6.8 % |
| COG | 38,954 | 3.3 % |
| SOG | 14,222 | 1.2 % |
| D | 11,770 | 1.0 % |
| C | 11,415 | 1.0 % |
| B | 10,548 | 0.9 % |
| A | 6,332 | 0.5 % |
| Width | 6,322 | 0.5 % |
| Length | 5,995 | 0.5 % |
| Name | 4,317 | 0.4 % |

### 4.6 Static-data completeness — per VESSEL, of 321

This is the table that matters for criterion 3, because these are the fields a
camera observation gets compared against.

| field | vessels with a usable value | share |
|---|---:|---:|
| Name | 311 | 96.9 % |
| Ship type | 306 | 95.3 % |
| Length | 305 | 95.0 % |
| Width | 304 | 94.7 % |
| A (GPS→bow) | 302 | 94.1 % |
| B (GPS→stern) | 287 | 89.4 % |
| C (GPS→starboard) | 287 | 89.4 % |
| D (GPS→port) | 284 | 88.5 % |
| Destination | 156 | 48.6 % |
| **IMO** | **137** | **42.7 %** |

**IMO is present for fewer than half the vessels.** An IMO number is assigned at
build and does not change with ownership or flag, which makes it the one identity
field that is genuinely hard to forge consistently — and it is missing 57% of the
time, mostly on Class B. Identity checks cannot lean on it.

A+B is the hull length about the GPS antenna and C+D the beam, so together they
cross-check the declared Length and Width **from a second, independently broadcast
field**. A dimension spoof has to keep two fields consistent, not one. That check is
available on 88–94% of vessels here, and it is a RECORDED-PATH-ONLY capability — on
the live NMEA path Length is derived from A+B, so the two agree by construction and
prove nothing.

### 4.7 Sentinels — this export is already decoded

Occurrences of the ITU "not available" encodings in the slice:

| sentinel | occurrences |
|---|---:|
| Heading = 511 | **0** |
| SOG = 102.3 | **0** |
| COG = 360.0 | **0** |

DMA converts them to empty fields before publishing. The absent-value spellings that
**do** appear are the strings `Unknown` (295,344 IMO rows, 17,370 Callsign rows) and
`Undefined` (ship type). These are text, not nulls, and pass straight through a naive
reader into an evidence record as a claimed IMO of "Unknown".

`ais_ingest` still checks for the numeric sentinels and **counts** them, so a future
DMA export that stops pre-cleaning shows up as a counter moving rather than as data
quietly changing meaning. On this day all three counters are expected to read zero.

### 4.8 Timestamps

Format is **`dd/mm/yyyy HH:MM:SS`**, confirmed against row 1 (`25/08/2026 00:00:00`).
pandas defaults to month-first and will silently swap every date where the day is
≤ 12, raising nothing. `ais_ingest.add_report_time` is the **single** parse site in
the project and passes an explicit format. Do not add a second one.

Dialect confirmed from the file: delimiter `,`, decimal `.`. The DMA README's
`57,8794` was Danish-locale prose, not the file format.

### 4.9 Timezone

DMA publishes basestation time. It is treated as UTC and every `AisTrack` carries a
tz-aware UTC datetime, because `contracts.AwareDatetime` rejects naive ones. **Not
independently verified against a second source.** If DMA publishes local time, every
timestamp is 2 hours off in August and every `time_delta_s` inherits it. Cheap to
check against one known vessel's port call; not yet done.

---

## 5. Known limitations that reach a verdict

1. **One MMSI collapses to one track.** `track_id` is a surrogate over
   (source, MMSI). Two hulls broadcasting the same MMSI — the textbook identity
   spoof — become one track with an impossible interleaved trajectory. Separating
   them needs kinematic track-breaking, which belongs with lane C's kinematic spoof
   logic. **Downstream must not read "one track_id" as "one vessel".**
2. **A bbox gap is not a coverage gap is not a dark vessel.** §4.2.
3. **Service-craft types are not comparable to silhouettes.** §3, and the policy note
   in `ais_ingest.py`.
4. **No AIS message-type information survives the CSV.** DMA merges static and
   dynamic fields into every row, so it is impossible to tell from this file whether
   a static field was broadcast now or an hour ago. On the live path that distinction
   exists and is handled by the AISTracker merge.
5. **Pseudonymisation is not anonymisation.** §6.

---

## 6. Golden windows — the committed demo data

Cut by `02_data/make_golden_window.py`, which reuses `ais_ingest`'s own frame
functions so the demo runs on data cleaned by exactly the code that cleans the
stream. Two nested windows, both from the Fehmarn Belt slice.

Counts below were measured directly from the slice with the documented rules
(explicit `dd/mm/yyyy` parse, AtoN excluded, dedupe on `Timestamp+MMSI+Lat+Lon`).
They are the expected output of the cutter; the cutter prints its own `IngestStats`
for cross-check, and any disagreement is a bug.

### `fehmarn_2026-08-25_1000-1100` — the demo window

| | |
|---|---:|
| Window | 2026-08-25 **10:00:00 – 11:00:00 UTC** |
| Raw rows in window | 50,348 |
| Dropped — AtoN / base station | 372 |
| Dropped — duplicate receptions | 27,448 |
| **Rows out** | **22,528** |
| **Distinct vessels** | **139** |
| Class A / Class B rows | 20,112 / 2,416 |
| CSV size | **4,576,253 bytes** (4.58 MB) |

An hour is chosen over ten minutes because criterion 2 ranks *behaviour*. In ten
minutes nothing loiters, alters course or goes quiet, so the prioritizer has nothing
to separate. 4.58 MB is comfortably inside GitHub's 100 MB per-file limit.

### `fehmarn_2026-08-25_1010-1020` — the iteration window

| | |
|---|---:|
| Window | 2026-08-25 **10:10:00 – 10:20:00 UTC** (nested inside the above) |
| Raw rows in window | 8,225 |
| Dropped — AtoN / base station | 57 |
| Dropped — duplicate receptions | 4,465 |
| **Rows out** | **3,703** |
| **Distinct vessels** | **121** |
| Class A / Class B rows | 3,299 / 404 |
| CSV size | **750,125 bytes** (0.75 MB) |

The densest ten minutes of the day by distinct vessels — 121 of the day's 321 are
in the box simultaneously.

### Which files may be committed

| file | commit? | why |
|---|---|---|
| `<window>_anon.csv` | **YES** | Synthetic identities. No real vessel is named |
| `<window>.csv` | **NO** | Real MMSIs, names, IMOs. §2 |
| `<window>_mmsi_map.csv` | **NO** | Reverses the pseudonymisation |
| `02_data/slices/*.csv` | **NO** | 248 MB — over GitHub's hard per-file limit |
| `02_data/raw/*` | **NO** | 6.4 GB |

Pseudonymisation replaces MMSI with a **999-prefixed** synthetic number (MID 999 is
unassigned to any country, so it is recognisably fake to anyone who reads AIS) and
replaces Name, IMO, Callsign and Destination.

**It is pseudonymisation, not anonymisation.** Position, time, course, speed and
dimensions are untouched, because those are what the pipeline reasons over. Anyone
with their own AIS archive could re-identify a vessel from its track. The pitch must
not claim otherwise.

---

## 7. Reproducing this

```sh
cd <repo> && source .venv/bin/activate

# 1. one day of DMA bulk AIS — PATH-STYLE HTTPS, and -sS never -s
curl -sS -o 02_data/raw/aisdk-2026-08-25.zip \
  https://s3.eu-central-1.amazonaws.com/aisdata.ais.dk/aisdk-2026-08-25.zip
unzip -t 02_data/raw/aisdk-2026-08-25.zip          # expect "No errors detected"
unzip -o 02_data/raw/aisdk-2026-08-25.zip -d 02_data/raw/

# 2. cut the three candidate areas (~10 min, streams in 500k-row chunks)
python3 02_data/subset_ais.py 02_data/raw/aisdk-2026-08-25.csv

# 3. cut the golden windows and print measured IngestStats
python3 02_data/make_golden_window.py

# 4. lane A regression tests
python3 -m pytest tests/test_ais_ingest.py -q
```

---

## 8. Other sources — status

| Source | Licence | Status |
|---|---|---|
| DMA bulk AIS | Danish act 596/2005, §2 above | **IN USE**, downloaded and verified |
| Copernicus / Sentinel-1 SAR | Free, ESA/EU | Not used. Cross-check only if time allows |
| Live AIS aggregator (T2) | **UNVERIFIED — terms not read** | Blocks T2. Read before connecting |
| MacBook camera (T2) | Owned | Available |
| SeaShips / Singapore Maritime | Academic, licence unverified | Not used |

**The live AIS feed for T2 has no recorded licence.** `NmeaLiveSource` exists and is
ready, but nothing may connect to a third-party feed until its terms are read and
written into this table. That is a hard rule in CLAUDE.md, not a preference.
