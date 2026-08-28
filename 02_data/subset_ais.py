"""
Cut a full day of Danish AIS down to demo-sized slices for three candidate areas.

WHY THIS EXISTS
A day of DMA AIS is ~700-900 MB zipped and several GB as CSV. Nothing in a 48-hour
build should read that repeatedly. This runs ONCE, streams the file in chunks so it
never loads the whole thing into memory, and writes one small CSV per candidate area.
It then reports row and vessel counts per area so the demo location is chosen from
EVIDENCE rather than from a guess about where the traffic is.

WHAT IT DELIBERATELY DOES NOT DO
No verdicts, no detection, no filtering by vessel behaviour. This is data preparation.
Choosing the area is a human decision made from the counts this prints.

Usage:  python3 02_data/subset_ais.py 02_data/raw/aisdk-2026-08-25.csv
"""
import sys
from pathlib import Path
import pandas as pd

# --- candidate areas -------------------------------------------------------------
# Three approaches worth considering, cut in one pass so the choice is data-driven.
# Bounds are (lat_min, lat_max, lon_min, lon_max) in WGS84 degrees.
AREAS = {
    # Dense commercial traffic, a fixed link, and cable crossings. The safe choice:
    # highest confidence that there is enough traffic to make prioritisation matter.
    "great_belt":   (55.20, 55.50, 10.70, 11.10),
    # The Germany-Denmark strait. Busy, and a live construction corridor.
    "fehmarn_belt": (54.40, 54.80, 11.00, 11.80),
    # Thematically the strongest for a seabed-cable narrative, but it sits at the far
    # edge of Danish basestation coverage — the counts below will show whether the
    # data is actually there. Do not assume it is.
    "bornholm":     (54.80, 55.60, 14.00, 15.50),
}

CHUNK = 500_000          # rows per chunk; ~100 MB of RAM at this width
OUT_DIR = Path("02_data/slices")


def detect_dialect(path: Path):
    """
    Settle the delimiter and decimal separator from the file itself.

    WHY: the DMA README prints coordinates as '57,8794' (Danish decimal comma). If the
    file were both comma-delimited AND comma-decimal it would be ambiguous. Guessing
    wrong does not crash — it silently shifts every position, which is the worst
    possible failure for a tool whose output is meant to be evidence. So we look.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        header = fh.readline().rstrip("\n")
        first = fh.readline().rstrip("\n")

    sep = ";" if header.count(";") > header.count(",") else ","
    # If the separator is ';' the decimal is almost certainly ','. If the separator is
    # ',' then the decimal must be '.' or the file could not be parsed at all.
    dec = "," if sep == ";" else "."
    print(f"  header : {header[:160]}")
    print(f"  row 1  : {first[:160]}")
    print(f"  -> delimiter={sep!r}  decimal={dec!r}")
    return sep, dec


def main(csv_path: str):
    path = Path(csv_path)
    if not path.exists():
        sys.exit(f"not found: {path}")

    print(f"reading {path} ({path.stat().st_size / 1e9:.2f} GB)")
    sep, dec = detect_dialect(path)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    buckets = {name: [] for name in AREAS}
    total = 0

    reader = pd.read_csv(
        path, sep=sep, decimal=dec, chunksize=CHUNK,
        low_memory=False,
        # Do NOT parse dates here. The format is dd/mm/yyyy and pandas' inference
        # defaults to month-first, silently swapping every date where day <= 12.
        # Timestamps are parsed later with an explicit format, once, deliberately.
    )

    for i, chunk in enumerate(reader, 1):
        # Column names are taken from the file, not assumed. The DMA header has been
        # seen with and without a leading '# ' on the first column across years.
        cols = {c.strip().lstrip("# ").lower(): c for c in chunk.columns}
        lat_c, lon_c = cols.get("latitude"), cols.get("longitude")
        if lat_c is None or lon_c is None:
            sys.exit(f"no Latitude/Longitude column found. Columns seen: {list(chunk.columns)}")

        lat = pd.to_numeric(chunk[lat_c], errors="coerce")
        lon = pd.to_numeric(chunk[lon_c], errors="coerce")
        total += len(chunk)

        for name, (la0, la1, lo0, lo1) in AREAS.items():
            m = lat.between(la0, la1) & lon.between(lo0, lo1)
            if m.any():
                buckets[name].append(chunk[m])

        if i % 10 == 0:
            print(f"  ...{total:,} rows scanned")

    print(f"\nscanned {total:,} rows total\n")
    print(f"{'area':<14} {'rows':>12} {'vessels':>9}   file")
    print("-" * 62)
    for name in AREAS:
        parts = buckets[name]
        if not parts:
            print(f"{name:<14} {0:>12} {0:>9}   (empty — no coverage here)")
            continue
        df = pd.concat(parts, ignore_index=True)
        mmsi_c = next((c for c in df.columns if c.strip().lower() == "mmsi"), None)
        vessels = df[mmsi_c].nunique() if mmsi_c else -1
        out = OUT_DIR / f"{path.stem}_{name}.csv"
        df.to_csv(out, index=False)
        print(f"{name:<14} {len(df):>12,} {vessels:>9,}   {out}")

    print("\nPick the area with enough vessels to make prioritisation a real problem.")
    print("An area with 20 vessels does not demonstrate 'which one gets the patrol'.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
