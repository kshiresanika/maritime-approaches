"""
make_demo_scenario.py — a tiny, FULLY SYNTHETIC AIS scenario. Stdlib only.

WHY THIS EXISTS
The real data is 32.6 M rows and 5.9 GB. The pseudonymised golden hour is still 139
vessels, which is unreadable on a projector and impossible to narrate. And every one of
those files is derived from DMA data whose licence is silent on redistribution, so none
of it can be committed.

This script writes a file with none of those problems: ~8 vessels over 10 minutes, about
50 KB, INVENTED FROM NOTHING. No DMA rows, no real vessels, no licence question, no
download. It is committed to the repo so that `git clone` followed by two commands
produces a running demo.

WHAT IT IS AND IS NOT
It is a legibility aid and a smoke test. It is NOT evidence that the pipeline works on
real data, and no measured number in the pitch may come from it. The real numbers come
from the DMA golden window, which stays local. Both statements go on the slide.

Positions are in the Fehmarn Belt, MMSIs are on the 999 prefix (a MID unassigned to any
country, so recognisably fake), and names are obviously invented. Constraint 2: no real
vessel is ever named by this project.

    python 04_demo/make_demo_scenario.py
"""

from __future__ import annotations

import csv
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "demo_scenario.csv"

# Exact DMA daily-file header. ais_ingest.normalise_columns strips the leading "# ",
# and row_to_track_fields reads these names, so they must match character for character.
HEADER = [
    "# Timestamp", "Type of mobile", "MMSI", "Latitude", "Longitude",
    "Navigational status", "ROT", "SOG", "COG", "Heading", "IMO", "Callsign",
    "Name", "Ship type", "Cargo type", "Width", "Length",
    "Type of position fixing device", "Draught", "Destination", "ETA",
    "Data source type", "A", "B", "C", "D",
]

START = datetime(2026, 8, 25, 10, 30, 0, tzinfo=timezone.utc)
DURATION_S = 600
STEP_S = 10

M_PER_DEG_LAT = 111_320.0


def vessels() -> list[dict]:
    """
    Sixteen vessels, spread wide enough that the FALSE-POSITIVE CONTROLS have material.

    THE COMPOSITION IS THE POINT, and the first version of this file got it wrong.
    Eight vessels, all clustered in the shipping lane, meant seven fell inside the
    camera's 60-degree wedge and exactly one outside it. The harness needs out-of-view
    vessels to build coverage-hole controls, and it printed a warning saying so:

        "no out-of-view vessel available for a coverage_hole control.
         The false-positive measurement is weaker without it."

    A scene with no controls cannot measure a false-positive rate, and the
    false-positive rate is the number that decides whether this tool is usable. So the
    spread is deliberate:

      * eight in the central lane, inside any reasonable field of view -- the working
        traffic, and the pool the harness draws its injected faults from;
      * five wide to the east and west, outside a 60-degree wedge -- these exist ONLY
        to be coverage-hole controls, vessels whose AIS gap must NOT be called dark
        because nobody was looking at them;
      * three far south, beyond a plausible 8 km max range -- the same control by a
        different mechanism, distance rather than angle.

    Five honest ships remain untouched in the lane, because a scene that is all
    anomalies teaches an operator nothing about what normal looks like, and MATCH
    verdicts are what make a ranked list mean anything.
    """
    return [
        # --- central lane: in view, the pool for injected faults --------------------
        dict(mmsi="999100001", name="SYNTH ALPHA", stype="Cargo", length=180, width=28,
             lat=54.5750, lon=11.2800, cog=68.0, sog=12.4),
        dict(mmsi="999100002", name="SYNTH BRAVO", stype="Tanker", length=228, width=32,
             lat=54.5980, lon=11.3450, cog=247.0, sog=11.1),
        dict(mmsi="999100003", name="SYNTH CHARLIE", stype="Cargo", length=96, width=16,
             lat=54.5660, lon=11.3120, cog=71.0, sog=9.8),
        dict(mmsi="999100004", name="SYNTH DELTA", stype="Passenger", length=142, width=24,
             lat=54.6050, lon=11.2950, cog=195.0, sog=16.2),
        dict(mmsi="999100005", name="SYNTH ECHO", stype="Fishing", length=26, width=7,
             lat=54.5880, lon=11.3600, cog=310.0, sog=6.4),
        # The loiterer, almost on INFRA-A. Near-zero speed is the whole scenario.
        dict(mmsi="999100006", name="SYNTH FOXTROT", stype="Tug", length=34, width=11,
             lat=54.5805, lon=11.3210, cog=90.0, sog=0.4, status="At anchor"),
        # Large hull -- the natural victim for a length spoof.
        dict(mmsi="999100007", name="SYNTH GOLF", stype="Tanker", length=245, width=42,
             lat=54.5920, lon=11.2700, cog=64.0, sog=13.7),
        # Small hull near the cable -- the natural victim for going dark.
        dict(mmsi="999100008", name="SYNTH HOTEL", stype="Fishing", length=22, width=6,
             lat=54.5840, lon=11.3300, cog=150.0, sog=3.1),

        # --- wide of the wedge: coverage-hole controls by ANGLE ---------------------
        dict(mmsi="999100009", name="SYNTH INDIA", stype="Cargo", length=155, width=23,
             lat=54.6150, lon=11.1500, cog=88.0, sog=10.5),
        dict(mmsi="999100010", name="SYNTH JULIET", stype="Tanker", length=190, width=30,
             lat=54.6220, lon=11.5300, cog=268.0, sog=12.8),
        dict(mmsi="999100011", name="SYNTH KILO", stype="Fishing", length=19, width=6,
             lat=54.6300, lon=11.1750, cog=15.0, sog=4.2),
        dict(mmsi="999100012", name="SYNTH LIMA", stype="Cargo", length=110, width=18,
             lat=54.6180, lon=11.5000, cog=200.0, sog=8.9),
        dict(mmsi="999100013", name="SYNTH MIKE", stype="Tug", length=30, width=10,
             lat=54.6350, lon=11.2100, cog=340.0, sog=5.5),

        # --- beyond max range: coverage-hole controls by DISTANCE -------------------
        dict(mmsi="999100014", name="SYNTH NOVEMBER", stype="Passenger", length=165, width=26,
             lat=54.4900, lon=11.3050, cog=182.0, sog=17.4),
        dict(mmsi="999100015", name="SYNTH OSCAR", stype="Cargo", length=205, width=31,
             lat=54.4750, lon=11.3800, cog=250.0, sog=11.9),
        dict(mmsi="999100016", name="SYNTH PAPA", stype="Tanker", length=175, width=27,
             lat=54.4820, lon=11.2400, cog=70.0, sog=10.2),
    ]


def advance(lat: float, lon: float, cog_deg: float, sog_kn: float, dt_s: float):
    """Straight-line dead reckoning. Flat-earth over 10 minutes at these speeds is
    accurate to well under a metre, and using a geodesic here would add a dependency
    for no measurable gain."""
    dist_m = sog_kn * 0.514444 * dt_s
    d_lat = dist_m * math.cos(math.radians(cog_deg)) / M_PER_DEG_LAT
    d_lon = (dist_m * math.sin(math.radians(cog_deg))
             / (M_PER_DEG_LAT * math.cos(math.radians(lat))))
    return lat + d_lat, lon + d_lon


def main() -> int:
    rows = []
    for v in vessels():
        lat, lon = v["lat"], v["lon"]
        for t in range(0, DURATION_S + 1, STEP_S):
            ts = START + timedelta(seconds=t)
            if t:
                lat, lon = advance(lat, lon, v["cog"], v["sog"], STEP_S)
            # Size A/B/C/D kept CONSISTENT with Length/Width. AIS declares dimensions
            # twice, and a scenario where they already disagree would hand the spoof
            # detector a free win on every honest vessel.
            a = round(v["length"] * 0.72, 1)
            b = round(v["length"] - a, 1)
            c = round(v["width"] / 2.0, 1)
            d = round(v["width"] - c, 1)
            rows.append({
                "# Timestamp": ts.strftime("%d/%m/%Y %H:%M:%S"),
                "Type of mobile": "Class A",
                "MMSI": v["mmsi"],
                "Latitude": f"{lat:.6f}",
                "Longitude": f"{lon:.6f}",
                "Navigational status": v.get("status", "Under way using engine"),
                "ROT": "0.0",
                "SOG": f"{v['sog']:.1f}",
                "COG": f"{v['cog']:.1f}",
                "Heading": f"{v['cog']:.0f}",
                "IMO": "Unknown",
                "Callsign": f"S{v['mmsi'][-4:]}",
                "Name": v["name"],
                "Ship type": v["stype"],
                "Cargo type": "Undefined",
                "Width": f"{v['width']:.0f}",
                "Length": f"{v['length']:.0f}",
                "Type of position fixing device": "GPS",
                "Draught": "",
                "Destination": "SYNTHETIC",
                "ETA": "",
                "Data source type": "AIS",
                "A": f"{a}", "B": f"{b}", "C": f"{c}", "D": f"{d}",
            })

    rows.sort(key=lambda r: (r["# Timestamp"], r["MMSI"]))
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {OUT}")
    print(f"  vessels : {len(vessels())}")
    print(f"  rows    : {len(rows)}")
    print(f"  window  : {START.isoformat()} + {DURATION_S}s, one report per "
          f"{STEP_S}s per vessel")
    print(f"  size    : {OUT.stat().st_size/1024:.1f} KB")
    print("\nFULLY SYNTHETIC. No DMA rows, no real vessel, no licence question.")
    print("Safe to commit. Never quote a measured number from this file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
