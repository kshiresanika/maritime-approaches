"""
make_golden_window.py — cut the T1 demo windows out of the Fehmarn Belt slice.
Lane A owns this file.

WHY THIS EXISTS
FILE_OWNERSHIP.md makes lane A's first deliverable a "golden window": a small,
committed, reproducible slab of real AIS that lanes C and D can develop against
instead of fixtures. Two windows are cut, nested:

  * 60 min, 10:00-11:00 UTC — what the demo runs on. Long enough that a vessel can
    loiter, alter course or go quiet, which is the raw material criterion 2's
    prioritizer ranks. A ten-minute window is a single snapshot: nothing has time to
    behave anomalously, so there is nothing to rank.
  * 10 min, 10:10-10:20 UTC — the fast iteration loop. Measured as the densest
    ten minutes of the day at 121 distinct vessels.

Both windows are cleaned by ais_ingest's own frame functions — the SAME code that
cleans the stream at runtime. If this script cleaned data its own way, the demo
would run on a file the pipeline had never seen, and the first divergence would
appear on stage.

TWO OUTPUTS PER WINDOW, AND WHY
  <name>.csv        real identities. GITIGNORED. Local use only.
  <name>_anon.csv   pseudonymised. This is the one that may be committed.

CAUSE AND EFFECT: the DMA licence (Danish act no. 596 of 24 June 2005) does NOT
address redistribution, and separately forbids combining AIS with other datasets to
identify individuals without Danish Data Protection Agency authorisation. On top of
that, CLAUDE.md's hard rule is that no real named vessel or operator may appear in
anything public-facing. A hackathon repo is public-facing. Committing raw MMSIs and
vessel names would breach the second rule outright and take an unforced position on
the first. Pseudonymising costs nothing and removes both problems, so it is the
default for anything that leaves this machine.

The MMSI -> synthetic-MMSI mapping is written beside the files and gitignored, so
lane D can resolve a demo contact back to the real vessel locally when it needs to,
and cannot do so from the repo.

Usage:
    python3 02_data/make_golden_window.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# 03_src has a leading digit and is not a legal package name — sys.path + flat
# imports is the settled convention (03_src/LIBRARIES.md). Do not invent another.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "03_src"))

from ais_ingest import DmaCsvSource  # noqa: E402

SLICE = REPO / "02_data" / "slices" / "aisdk-2026-08-25_fehmarn_belt.csv"
OUT_DIR = REPO / "02_data" / "golden"

WINDOWS = {
    "fehmarn_2026-08-25_1000-1100": (
        datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc),
    ),
    "fehmarn_2026-08-25_1010-1020": (
        datetime(2026, 8, 25, 10, 10, tzinfo=timezone.utc),
        datetime(2026, 8, 25, 10, 20, tzinfo=timezone.utc),
    ),
}

#: Maritime Identification Digits 999 is unassigned to any country, so a 999-prefixed
#: MMSI is recognisably synthetic to anyone who reads AIS. Chosen deliberately over a
#: hash: a hashed MMSI still looks like a real MMSI, and someone will eventually paste
#: one into a vessel database.
SYNTHETIC_MMSI_BASE = 999_000_000

#: Columns that identify a real vessel or operator. Replaced, not blanked — a blank
#: where an identity was is worse than the identity, because it changes the shape of
#: the data and hides that anything was removed.
IDENTITY_COLUMNS = ("MMSI", "Name", "IMO", "Callsign", "Destination")


def pseudonymise(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Replace every real identity with a stable synthetic one.

    Returns (anonymised frame, mapping frame). The mapping is ordered by first
    appearance, so the same input file always produces the same synthetic ids and an
    evidence record written yesterday still resolves today.

    NOTE ON WHAT THIS DOES NOT HIDE: position, time, course, speed and dimensions are
    untouched, because those are the data the pipeline reasons over — removing them
    would leave nothing to demo. A determined reader with their own AIS archive could
    re-identify a vessel from its track. This is pseudonymisation, not anonymisation,
    and the pitch must not claim otherwise.
    """
    real = frame["MMSI"].astype("int64")
    order = list(dict.fromkeys(real.tolist()))          # first-appearance order
    mapping = {m: SYNTHETIC_MMSI_BASE + i + 1 for i, m in enumerate(order)}

    out = frame.copy()
    out["MMSI"] = real.map(mapping)
    out["Name"] = ["VESSEL-%03d" % (mapping[m] - SYNTHETIC_MMSI_BASE) for m in real]
    out["IMO"] = "Unknown"          # DMA's own absent-value spelling, so the reader
    out["Callsign"] = "Unknown"     # path is exercised exactly as on real data
    out["Destination"] = "Unknown"

    table = pd.DataFrame(
        {"real_mmsi": list(mapping.keys()), "synthetic_mmsi": list(mapping.values())}
    )
    return out, table


def cut(name: str, start: datetime, end: datetime) -> dict[str, object]:
    """Cut one window, write both variants, and return MEASURED counts."""
    source = DmaCsvSource(SLICE, start_utc=start, end_utc=end)
    frames = list(source.frames())
    if not frames:
        raise SystemExit(f"{name}: window is empty — check the date and the slice")
    window = pd.concat(frames, ignore_index=True)

    # report_time_utc was added by ais_ingest for filtering. Drop it before writing so
    # the golden file has EXACTLY the DMA schema and is read back by the same reader,
    # with the same explicit dd/mm/yyyy parse. A file with a pre-parsed timestamp
    # column would let the date landmine slip past unnoticed on the demo path.
    window = window.drop(columns=["report_time_utc"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    real_path = OUT_DIR / f"{name}.csv"
    anon_path = OUT_DIR / f"{name}_anon.csv"
    map_path = OUT_DIR / f"{name}_mmsi_map.csv"

    window.to_csv(real_path, index=False)
    anon, table = pseudonymise(window)
    anon.to_csv(anon_path, index=False)
    table.to_csv(map_path, index=False)

    return {
        "name": name,
        "rows": len(window),
        "vessels": int(window["MMSI"].nunique()),
        "class_a": int((window["Type of mobile"] == "Class A").sum()),
        "class_b": int((window["Type of mobile"] == "Class B").sum()),
        "bytes_real": real_path.stat().st_size,
        "bytes_anon": anon_path.stat().st_size,
        "stats": source.stats,
    }


def main() -> None:
    print(f"source: {SLICE}")
    for name, (start, end) in WINDOWS.items():
        r = cut(name, start, end)
        print(f"\n=== {r['name']} ===")
        print(r["stats"].summary())
        print(f"rows written : {r['rows']:,}")
        print(f"vessels      : {r['vessels']:,}  (Class A rows {r['class_a']:,} / "
              f"Class B rows {r['class_b']:,})")
        print(f"size real    : {r['bytes_real'] / 1e6:.2f} MB")
        print(f"size anon    : {r['bytes_anon'] / 1e6:.2f} MB")
    print("\nCOMMIT THE *_anon.csv FILES ONLY. The real-identity files and the mmsi "
          "maps must stay gitignored — see the licence note in 02_data/INVENTORY.md.")


if __name__ == "__main__":
    main()
