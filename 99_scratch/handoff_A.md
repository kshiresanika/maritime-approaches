# HANDOFF — Lane A (AIS)

Session: 2026-08-28, pre-event. Author: lane A.

## What exists now

| Path | State |
|---|---|
| `02_data/raw/aisdk-2026-08-25.{zip,csv}` | Downloaded, CRC-verified. 894 MB / 5.88 GB. Gitignored |
| `02_data/slices/aisdk-2026-08-25_fehmarn_belt.csv` | 248 MB, 1,198,373 rows. The working slice. Gitignored |
| `02_data/INVENTORY.md` | **Measured** counts, licence, gaps, limitations. Criterion 4 source material |
| `02_data/DATASETS.md` | Licence table added |
| `02_data/subset_ais.py` | Day → three candidate areas. Already run |
| `02_data/make_golden_window.py` | Slice → two nested demo windows, real + pseudonymised. **NOT YET RUN** |
| `03_src/ais_ingest.py` | The module. CSV + live NMEA, one interface, yields `AisTrack` |
| `tests/test_ais_ingest.py` | 24 tests, one per silent-failure mode. **NOT YET RUN** |

## The interface, for whoever picks this up

```python
from ais_ingest import DmaCsvSource, NmeaLiveSource   # both are AisSource

src = DmaCsvSource("02_data/golden/fehmarn_2026-08-25_1000-1100.csv")
for track in src.tracks():      # or: for track in src
    ...                         # track is a contracts.AisTrack — CLAIMS ONLY
print(src.stats.summary())      # what was read, dropped and why

live = NmeaLiveSource("feed.example", 5631)   # T2. Same loop, same objects.
```

`DmaCsvSource.frames()` yields the cleaned pandas frames one stage earlier. That exists
so `make_golden_window.py` writes files cleaned by the SAME code that cleans the
stream. Do not add a second cleaning path.

## The five things that will bite the next person

1. **63.5% of raw rows are duplicate receptions.** Several DMA basestations hear one
   transmission and DMA writes a row for each. Dedupe is ON by default. If you turn it
   off, every reporting-rate number inflates ~2.75x and lane C sees three identical
   association candidates per hull.
2. **AtoN rows are buoys, not vessels** — 16 MMSIs, 10,972 rows. Excluded by default.
   Left in, they are a permanent claim with no possible observation, which reads
   downstream as a position spoof that never goes away.
3. **Timestamps are dd/mm/yyyy and pandas assumes mm/dd/yyyy.** `add_report_time()` is
   the single parse site and passes an explicit format. It raises nothing when wrong —
   it just silently swaps every date with day <= 12. Do not add a second parse site.
4. **"Unknown" and "Undefined" are STRINGS, not nulls.** 295,344 IMO rows say the
   literal text "Unknown". `clean_string()` handles it. Bypass it and the evidence
   record claims an IMO of "Unknown".
5. **`unknown` (VesselClass) and `None` are different findings.** `unknown` = a claim
   was made that a camera cannot be compared against. `None` = the transponder claimed
   nothing. Filed to lane C in `99_scratch/requests.md`.

## Open, and who owns it

| # | Item | Owner | Blocks |
|---|---|---|---|
| 1 | `.gitignore` has no rule for `02_data/golden/` — real identities would be committed | **ARCH** | The first commit |
| 2 | `"unknown"` must suppress the class comparison | **C** | Criterion 3 correctness |
| 3 | One MMSI collapses to one track — kinematic track-breaking | **C** | The identity-spoof case |
| 4 | An AIS gap alone must not reach DARK | **C, D** | Criterion 2 false positives |
| 5 | Live AIS feed licence **not read** | A | **T2 entirely** |
| 6 | DMA timestamps assumed UTC, not verified against a second source | A | A 2-hour error in every `time_delta_s` if wrong |
| 7 | `make_golden_window.py` and `pytest` not yet run on the Mac | A | Golden window on disk |

Items 1–4 are filed in `99_scratch/requests.md` with cause and effect.

## Next actions for lane A, in order

1. Run the paste block below on the Mac: pytest, then the golden-window cutter.
   Paste `IngestStats` output back and cross-check against `INVENTORY.md` §6.
2. Get ARCH to fix `.gitignore` BEFORE the first `git add`.
3. Read the live AIS feed terms and fill the row in `DATASETS.md`. Until then T2 has
   no legal data source, however well `NmeaLiveSource` works.
4. Verify the UTC assumption against one known port call.
5. Hand lane C the golden window and sit with them for the first `associate()` run.
   FILE_OWNERSHIP.md §3: schedule that meeting, do not let it happen at 04:00.

## Verification block — run on the Mac, paste the output back

```sh
cd ~/Desktop/MaritimeApproaches && source .venv/bin/activate

# 1. Lane A regression tests. Expect all green. These cover the five silent
#    failure modes above; a red one means the data changed meaning, not that a
#    threshold needs nudging.
python3 -m pytest tests/test_ais_ingest.py -q

# 2. Whole suite still green (nothing here should have touched contracts).
python3 -m pytest tests/ -q

# 3. Cut the golden windows. Prints measured IngestStats per window.
#    CROSS-CHECK against 02_data/INVENTORY.md section 6:
#      1000-1100 -> 22,528 rows / 139 vessels / 4,576,253 bytes
#      1010-1020 ->  3,703 rows / 121 vessels /   750,125 bytes
#    A disagreement is a bug in ais_ingest, not a rounding difference.
python3 02_data/make_golden_window.py

# 4. End-to-end: real AisTrack objects out of the iteration window.
python3 -c "
import sys; sys.path.insert(0,'03_src')
from ais_ingest import DmaCsvSource
s = DmaCsvSource('02_data/golden/fehmarn_2026-08-25_1010-1020.csv')
ts = list(s.tracks())
print(s.stats.summary())
print('AisTrack objects:', len(ts))
t = ts[0]
print('sample:', t.track_id, t.claimed_mmsi, t.report_time_utc.isoformat(),
      t.claimed_ship_type, t.claimed_length_m, t.claimed_sog_kn)
print('tz-aware:', t.report_time_utc.tzinfo is not None)
"

# 5. Confirm the gitignore fix once ARCH has made it. Expect NO output for the
#    _anon file, and a matching rule printed for the real one.
git check-ignore -v 02_data/golden/fehmarn_2026-08-25_1000-1100_anon.csv
git check-ignore -v 02_data/golden/fehmarn_2026-08-25_1000-1100.csv
```

---

# ADDENDUM — trajectory analysis (2026-08-28 15:40)

## New files

| Path | State |
|---|---|
| `03_src/ais_trajectory.py` | Per-vessel trajectories, speed profile, loiter, gaps. **NOT YET RUN** |
| `tests/test_ais_trajectory.py` | 16 tests. `importorskip("movingpandas")`. **NOT YET RUN** |

`ais_ingest.py` is UNCHANGED and still imports nothing but pandas and contracts. The
geo stack is confined to `ais_trajectory.py` on purpose: a teammate with a broken
geopandas install must still be able to read AIS.

## Interface

```python
from ais_ingest import DmaCsvSource
from ais_trajectory import from_frames, analyse

src    = DmaCsvSource("02_data/slices/aisdk-2026-08-25_fehmarn_belt.csv")
points = from_frames(src.frames(), src.source_id)     # flat table, low memory
result = analyse(points, bbox=(54.40, 54.80, 11.00, 11.80))

result.loiter_events   # list[BehaviourEvent]
result.gap_events      # list[BehaviourEvent]
result.kinematics      # per-report implied_speed_ms / implied_course_deg_true / gap_before_s
print(result.summary())
```

`from_tracks(Iterable[AisTrack])` is the contract-correct entry point for small
windows and tests. `from_frames` exists because 431,745 reports as pydantic instances
is hundreds of megabytes on a laptop that is also running YOLO.

## Four things that will bite the next person

1. **`TrajectoryCollection.__iter__` RAISES on a trajectory with fewer than two
   points**, and the Fehmarn slice has six single-report vessels. `build_trajectories`
   filters at `min_points=2`. Remove that floor and the whole analysis dies partway
   through, blaming movingpandas.
2. **Do NOT count gaps by counting splitter segments.** `ObservationGapSplitter`
   discards segments with fewer than two points, so a lone report between two silences
   is dropped and the two gaps merge into one. The under-count is worst where
   reporting is sparsest — exactly the case criterion 4 cares about. `detect_gaps`
   reads the timedelta column instead. There is a test for this.
3. **`max_diameter` is METRES on EPSG:4326**, computed geodesically via geopy. Passing
   a degree value searches a box hundreds of kilometres wide and returns every vessel.
4. **`geopandas` `.distance()` aligns on INDEX by default.** `pairwise_distance_m`
   passes `align=False`; without it a mismatched index yields NaN, not an error, and
   every gap silently reports zero movement.

## Measured this session (Fehmarn slice, full 24 h)

- **660 AIS gaps over 10 minutes, across 108 vessels.** 3,265 over 5 min; 146 over
  30 min; 60 over 60 min. Max gap 17.6 h — a box exit, not a switched-off transponder.
- **293 stationary periods ≥ 30 min** by the claimed-SOG proxy, 224 of them **not**
  declaring a stationary nav status. Top ship types among those: Tug 71, Sailing 65.
- The geometric loiter count is pending the run below. The proxy is a bound, not the
  answer, and the two are expected to differ.

## Verification block — run on the Mac, paste the output back

```sh
cd ~/Desktop/MaritimeApproaches && source .venv/bin/activate

# 1. Trajectory tests. The library-default assertions (speed in m/s, direction in
#    degrees true) are the ones to watch — a movingpandas minor version that changed
#    either would silently rescale every speed in the pipeline.
python3 -m pytest tests/test_ais_trajectory.py -q

# 2. Whole suite.
python3 -m pytest tests/ -q

# 3. The report. Prints loiter and gap counts, both sensitivity sweeps, the nav-status
#    breakdown, and the self-contradiction check. Takes a few minutes on the full day.
#    CROSS-CHECK the gap row against 02_data/INVENTORY.md §4.10:
#      >5min 3,265 / >10min 660 / >15min 333 / >30min 146 / >60min 60
#    Those were computed independently with pandas using the same arithmetic, so a
#    disagreement is a bug in ais_trajectory, not a rounding difference.
python3 03_src/ais_trajectory.py 02_data/slices/aisdk-2026-08-25_fehmarn_belt.csv
```

## Open, added to the list above

| # | Item | Owner | Blocks |
|---|---|---|---|
| 8 | `contracts.BehaviourEvent` + `implied_*` fields on `AisTrack` + `EvidenceRecord.behaviour_events` | **ARCH** | Behaviour cannot reach the evidence card; lane A ships a local mirror meanwhile |
| 9 | Gap `explained_by_*` flags must SUPPRESS, not score; loiter must discount declared-stationary vessels | **C, D** | Criterion 2 ranks harbour furniture without it |
| 10 | Geometric loiter count not yet produced | A | The number that goes on the slide |

---

# ADDENDUM 2 — plausibility check (2026-08-28 16:55)

## What changed

`03_src/ais_ingest.py` only. **No new modules.** Added `PlausibilityChecker`,
`PlausibilityMismatch`, `LandMask`, `PlausibilityStats`, `anonymise_mmsi()`,
`plausibility_report()` and a `__main__` entry point. 13 tests appended to
`tests/test_ais_ingest.py`. **NOT YET RUN.**

**The lazy-import rule still holds and is now enforced by a test.**
`test_reading_ais_still_does_not_require_the_geo_stack` parses the module's own AST
and asserts that geopandas, shapely, pyproj, movingpandas and pyais are NOT top-level
imports. Reading a CSV still needs pandas alone; only the land check needs the geo
stack, and only the distance checks need pyproj.

## Interface

```python
from ais_ingest import DmaCsvSource, PlausibilityChecker, LandMask

mask    = LandMask("02_data/coastline/eea_coastline_polygon.shp",
                   bbox=(54.40, 54.80, 11.00, 11.80))   # optional
checker = PlausibilityChecker(land_mask=mask)

for track in DmaCsvSource("...csv").tracks():
    for finding in checker.check(track):      # 0..n PlausibilityMismatch
        ...
print(checker.stats.summary())
```

Streaming, one anchor per MMSI, so it works unchanged on `NmeaLiveSource` — T2 can
flag a bad fix as it arrives. Vessels may be interleaved; no sorting needed.

## Five things that will bite the next person

1. **The anchor rule is the whole check.** A report closer than `min_interval_s` is
   skipped *without advancing the anchor*. Advance it and a 1 Hz vessel is compared
   over one second, where GPS scatter (tens of metres) exceeds real movement —
   705 findings instead of 41, 96% of them noise. Skip *and* advance and a 1 Hz
   vessel is never checked at all. There is a test for exactly this.
2. **Δt can be 0.** 956 pairs in the slice share a timestamp. Divide without checking
   and you get a ZeroDivisionError partway through a day of AIS. 583 of them differ
   by under 5 m (basestation rounding); the 21 that differ by over 100 m are the
   interesting ones — a hull cannot be in two places at once.
3. **`PlausibilityMismatch` is a local mirror, not `contracts.Mismatch`.** Two
   required fields on the real type have no truthful value here: `association_id`
   (nothing was paired) and `observation_confidence` (nothing observed it). Contract
   extension filed to ARCH; the swap is one import.
4. **Never put an implied value in an `observed_` field.** `comparison=
   "claimed_vs_implied"` is the discriminator. Without it, lane C weights a verdict
   using an EO uncertainty that was never computed — silently.
5. **A wrong coastline is worse than none.** The 1:110m fixture inside pyogrio's test
   folder is the obvious shortcut and it is actively harmful in an 18 km strait.

## Measured this session (Fehmarn slice, full 24 h)

**41 findings across 27 vessels** — 17 `zero_interval_jump`, 15 `impossible_speed`,
9 `teleport`. 278,934 pairs evaluated. Land check NOT RUN: no coastline on disk.

**26 of the 41 are on vessels claiming "Sailing", every worked example Class B.** The
leading explanation is a cheap consumer GPS, not deception. Full tables, the
sensitivity sweep and three anonymised worked examples are in `02_data/INVENTORY.md`
§4.11.

## To do, in order

1. **Download the coastline.** EEA coastline for analysis (polygon) v3.0, CC-BY 4.0,
   EPSG:3035, 1:100,000:
   `https://sdi.eea.europa.eu/data/9faa6ea1-372a-4826-a3c7-fb5b05e31c52`
   Unzip to `02_data/coastline/`. Credit the EEA on any slide showing a land finding.
   Add `02_data/coastline/` to `.gitignore` — it is a large third-party dataset.
2. Run the block below and paste the output back.
3. Chase ARCH on the two open contract interrupts.

## Verification block — run on the Mac, paste the output back

```sh
cd ~/Desktop/MaritimeApproaches && source .venv/bin/activate

# 1. Lane A tests, now 37 in this file. The two to watch are the anchor rule and the
#    claimed-vs-implied wall test — both are silent failures if they regress.
python3 -m pytest tests/test_ais_ingest.py -q

# 2. Whole suite.
python3 -m pytest tests/ -q

# 3. The report, WITHOUT the coastline. Land check must print UNAVAILABLE rather
#    than a clean result nobody computed.
#    CROSS-CHECK against 02_data/INVENTORY.md §4.11:
#      41 findings / 27 vessels — 17 zero_interval_jump, 15 impossible_speed, 9 teleport
#      278,934 pairs evaluated; sensitivity 0s -> 705, 10s -> 19 (speed pairs only)
#    Those were computed independently with geopy (the same WGS84 geodesic pyproj
#    uses), so a disagreement is a bug, not rounding.
python3 03_src/ais_ingest.py 02_data/slices/aisdk-2026-08-25_fehmarn_belt.csv

# 4. And again WITH the coastline, once downloaded. This is the only way to get the
#    crosses_land count — it cannot be computed without shapely.
python3 03_src/ais_ingest.py 02_data/slices/aisdk-2026-08-25_fehmarn_belt.csv \
        02_data/coastline/eea_coastline_polygon.shp
```

## Open, added to the list

| # | Item | Owner | Blocks |
|---|---|---|---|
| 11 | `Mismatch` needs `position_implausible`, optional `association_id`, and a `comparison` discriminator | **ARCH** | Plausibility findings cannot reach the evidence card |
| 12 | A `position_implausible` finding must not reach SPOOF alone | **C, D** | Defamation risk against real named yachts |
| 13 | EEA coastline not downloaded; `crosses_land` never run | A | One of the three requested checks |
| 14 | `02_data/coastline/` needs a `.gitignore` rule | **ARCH** | A large third-party dataset in the repo |
