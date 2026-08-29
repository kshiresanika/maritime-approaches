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

### 4.10 Behaviour — loiter and AIS gaps, Fehmarn slice, full 24 h

Produced by `03_src/ais_trajectory.py` (movingpandas 0.23.0). Population after
deduplication and AtoN exclusion: **431,745 reports / 315 vessels**. Six vessels have
a single report and are dropped — `TrajectoryCollection.__iter__` raises on any
trajectory with fewer than two points, so the floor is structural, not cosmetic.

#### AIS gaps — exact counts

Same arithmetic as `Trajectory.add_timedelta` and `ObservationGapSplitter`
(`t.diff() > gap`, per trajectory), so these are the module's numbers.

| threshold | gaps | vessels affected |
|---|---:|---:|
| > 5 min | 3,265 | 157 |
| **> 10 min** | **660** | **108** |
| > 15 min | 333 | 89 |
| > 30 min | 146 | 65 |
| > 60 min | 60 | 36 |

Duration of gaps over 10 minutes: median **15.0 min**, p90 **55.5 min**, max
**17.6 h**.

**660 gaps is not 660 dark vessels.** The 17.6-hour maximum is a vessel that left the
0.4° × 0.8° box and came back, not a transponder switched off for a working day. Each
event carries `explained_by_area_exit` (last position within 2 km of the slice
boundary) and `explained_by_sparse_coverage` (this vessel's own median reporting
interval more than 3× the area median) so lane C can suppress rather than score them.
The explained/unexplained split is produced by the run, not estimated here.

#### Loiter — proxy counts pending the geometric run

The module detects loiter **geometrically**: `TrajectoryStopDetector`, stayed inside
`max_diameter` metres for `min_duration`. That needs shapely and geopandas.

The table below is a **different detector on a different input** — maximal runs of
**claimed SOG < 0.5 kn**, computed with pandas alone, no distance maths. It bounds the
answer and it exposes the ranking problem; **it is not the module's number**, and the
two are expected to differ. Where they differ is itself informative: a vessel claiming
SOG 0 while its own positions move is a kinematic self-contradiction.

| min duration | stationary periods | vessels | **not declaring a stationary status** |
|---|---:|---:|---:|
| ≥ 15 min | 416 | 133 | 341 |
| **≥ 30 min** | **293** | **117** | **224** |
| ≥ 60 min | 215 | 113 | 151 |

At the 30-minute threshold, by claimed navigational status:

| status | events | |
|---|---:|---|
| Under way using engine | 92 | **stopped while declaring way on** |
| Unknown value | 92 | no usable status claim |
| Moored | 56 | declared stationary |
| Restricted maneuverability | 39 | |
| At anchor | 9 | declared stationary |
| Not under command | 4 | declared stationary |
| Under way sailing | 1 | |

By claimed ship type, among those **not** declaring a stationary status:

| ship type | events |
|---|---:|
| Tug | 71 |
| Sailing | 65 |
| Pleasure | 18 |
| Other | 17 |
| Dredging | 10 |
| Passenger | 10 |
| Port tender | 10 |
| SAR | 6 |

**This table is the criterion 2 problem in one place.** A bare stop count is dominated
by the Fehmarnbelt tunnel construction fleet and by becalmed yachts. Ranking without
discounting them produces a list of harbour furniture, and criterion 2 is judged on
whether the top of the list deserves the patrol boat. Every `BehaviourEvent` therefore
carries the claimed statuses observed during it.

#### Thresholds are arguments, not facts

Every count above is quoted with the threshold that produced it, and
`ais_trajectory.gap_sensitivity()` / `loiter_sensitivity()` emit the full sweep. A
loiter count without its diameter and duration is not a measurement, and a judge is
entitled to ask what happens at five minutes instead of ten.

---

### 4.11 Plausibility — where the claim contradicts itself

Produced by `ais_ingest.PlausibilityChecker`. Compares each vessel's claimed SOG
against the speed implied by its OWN consecutive claimed positions, and (when a
coastline is configured) tests whether the segment between two reports crosses land.
Nothing is observed here; this is claim against claim.

#### The parameter that decides whether this check is useful at all

Naive version — implied speed over consecutive reports, no interval floor:
**705 findings across 146 vessels.** Of those, **564 (80%) are over Δt ≤ 2 s with a
median jump of 57 metres.** 57 m in one second is 111 knots. It is also the ordinary
scatter of a consumer GPS fix. At 1 Hz reporting, position NOISE exceeds position
CHANGE, so the naive check is measuring the receiver, not the ship.

| minimum sampling interval | pairs implying > 50 kn | vessels |
|---|---:|---:|
| 0 s (naive) | 705 | 146 |
| 2 s | 345 | 106 |
| 5 s | 50 | 34 |
| **10 s (shipped)** | **19** | **16** |
| 30 s | 6 | 6 |

Ship the naive version and 96% of what an operator sees is receiver noise — which is
how a decision-support tool teaches its user to ignore it.

#### Counts at the shipped thresholds

`max_speed_kn=50` (no vessel in the slice claims more than 47.0 kn),
`min_interval_s=10`, `min_jump_m=100`, `teleport_jump_m=1000`,
`zero_interval_jump_m=100`. Anchor rule: a report closer than the minimum interval is
skipped **without advancing the anchor**, so a 1 Hz vessel is still checked roughly
every ten seconds rather than never.

| | pairs |
|---|---:|
| evaluated | 278,934 |
| skipped, inside the minimum interval | 151,998 |
| skipped, jump below the 100 m noise floor | 243,278 |
| same-timestamp pairs | 498 |
| first report of a vessel | 321 |

**TOTAL: 41 findings across 27 vessels.**

| kind | count | meaning |
|---|---:|---|
| `zero_interval_jump` | 17 | two positions in the same second, > 100 m apart |
| `impossible_speed` | 15 | > 50 kn implied, jump ≤ 1 km |
| `teleport` | 9 | > 50 kn implied, jump > 1 km |
| `crosses_land` | — | **not run: no coastline configured yet** |

By claimed ship type:

| ship type | impossible_speed | teleport | zero_interval_jump |
|---|---:|---:|---:|
| Sailing | 9 | 7 | 10 |
| Pleasure | 3 | 1 | 2 |
| Cargo | 1 | 1 | 2 |
| Law enforcement | 2 | 0 | 0 |
| Tanker | 0 | 0 | 1 |
| Diving | 0 | 0 | 1 |
| Port tender | 0 | 0 | 1 |

**26 of 41 findings are on vessels claiming "Sailing", and every worked example is
Class B.** The leading explanation is a cheap consumer GPS on a small craft, not
deception. This is a data-quality signal. Lane C may promote it to
`SpoofSubtype="kinematic"` only with corroboration — promoted alone it puts the word
"spoof" against a real named yacht.

#### The zero-interval trap

956 consecutive pairs in the slice share a timestamp exactly. `ais_ingest`
deduplicates on `(Timestamp, MMSI, Latitude, Longitude)`, so two basestations that
decoded the SAME transmission to slightly different coordinates both survive.
**583 of the 956 differ by under 5 metres** — that is rounding. Δt = 0 makes implied
speed infinite, so unhandled these are 956 phantom teleports and a ZeroDivisionError
waiting for whichever consumer divides first.

But **21 of them differ by more than 100 metres in the same second**, and that is not
rounding: a hull cannot be in two places at once. The leading explanation is the
limitation §5.1 already records — **two vessels sharing one MMSI**, which is exactly
the identity spoof criterion 3 calls the discriminator.

#### Three examples (MMSI anonymised — 999 prefix, an unassigned MID)

**A — teleport.** MMSI 999000037, claimed "Pleasure", Class B.
09:29:01 at (54.40784, 11.19411) claiming SOG 4.9 kn; 09:29:58 at (54.40116,
11.11360) claiming 6.9 kn. **5,281 m in 57 s = 180 kn implied**, against a claim of
6.9 kn.

**B — impossible speed.** MMSI 999000278, claimed "Sailing", Class B.
06:18:19 at (54.53826, 11.46414); 06:18:29 at (54.54063, 11.45120).
**878 m in 10 s = 171 kn implied**, against a claim of 5.8 kn.

**C — zero-interval jump.** MMSI 999000115, claimed "Sailing", Class B.
Two positions timestamped 17:55:22 exactly — (54.55415, 11.05359) and (54.55382,
11.04490), **564 m apart in the same second**, claiming 7.1 and 6.7 kn.

In all three the claimed SOG is entirely plausible while the positions are not. That
is the shape of a bad fix, and it is also the shape of a position spoof. Neither this
module nor lane A can tell them apart, which is why the finding defers.

#### Coastline for the land check — LICENSED, unlike the AIS

**EEA coastline for analysis (polygon), version 3.0, March 2017.**

| | |
|---|---|
| Licence | **CC-BY 4.0**, copyright holder European Environment Agency. "No limitations to public access". Attribution required |
| Scale | 1:100,000 minimum mapping unit |
| Native CRS | **EPSG:3035** (ETRS89-extended / LAEA Europe) — not 4326, must be reprojected |
| Lineage | Hybrid of EUHYDRO and GSHHG, cut at EUDEM altitude 0 |
| Download | https://sdi.eea.europa.eu/data/9faa6ea1-372a-4826-a3c7-fb5b05e31c52 |

This is the one dataset in the project whose reuse terms are **explicit and
permissive**. It can be cited on a slide without the hedging §2 requires for the DMA
AIS. Attribution to the EEA is a condition, not a courtesy.

**Why not a coarser coastline.** geopandas 1.1.4 removed its bundled Natural Earth
dataset, and the only shapefile otherwise on disk is a 1:110,000,000 fixture inside
pyogrio's test folder. Its coastline error is kilometres; the Fehmarn Belt is about
18 km wide. Using it would report that every vessel near Rødbyhavn and Puttgarden had
sailed overland — false positives aimed precisely at the vessels closest to the
infrastructure this tool exists to protect. **A wrong coastline is worse than no
coastline, because a missing one is visible and a wrong one is not.**

**Two deliberate restrictions on the land check.** Land is ERODED inward by 250 m
before testing, because at 1:100,000 a vessel moored alongside a quay legitimately
falls inside the land polygon. And the check only runs when the two reports are
within 60 s of each other: over a longer interval the straight line between them is
not the path the vessel took, so the segment across a three-hour silence crosses
Lolland for every vessel that rounded it. Both trades lose real detections to avoid
false accusations, deliberately.

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
