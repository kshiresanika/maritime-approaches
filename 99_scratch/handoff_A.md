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
