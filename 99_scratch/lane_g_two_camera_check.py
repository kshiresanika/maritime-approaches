#!/usr/bin/env python3
"""
99_scratch/lane_g_two_camera_check.py — DOES A SECOND CAMERA ACTUALLY HELP?

    python3 04_demo/make_second_view.py          # build camera B's view first
    python3 99_scratch/lane_g_two_camera_check.py

Takes camera A's contacts and camera B's contacts — two independent synthetic
observations of the SAME vessels, each with its own measurement noise — pairs them
across the cameras, triangulates every pair, and measures the fix against the position
the vessels were projected from.

THE NUMBER THIS PRODUCES IS THE ONE TO SAY OUT LOUD. Everything else about a second
camera is an argument; this is a measurement of how far the fix lands from the truth,
compared with what one camera managed on the same vessel at the same moment.

NOTE ON WHAT "TRUTH" MEANS HERE. Both cameras' contacts were projected from the AIS
tracks' positions, so those positions are the ground truth for GEOMETRY. This measures
the fix's geometric accuracy, not whether AIS was telling the truth — that is
consistency.py's question, and a different one.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "03_src"))

from contracts import AisTrack, EoContact                       # noqa: E402
from association import predict_measurement                      # noqa: E402
import triangulate as tri                                        # noqa: E402

A_DIR = ROOT / "04_demo/out/scene01"
B_DIR = ROOT / "04_demo/out/scene01b"

FAIL = 0
def ok(name, cond, detail=""):
    global FAIL
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    if not cond: FAIL += 1

def hdr(t): print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)

def load(d: Path):
    man = json.loads((d / "scene_manifest.json").read_text())
    cs = [EoContact.model_validate_json(l)
          for l in (d / "eo_contacts.jsonl").read_text().splitlines() if l.strip()]
    ts = [AisTrack.model_validate_json(l)
          for l in (d / "ais_tracks.jsonl").read_text().splitlines() if l.strip()]
    return man, cs, ts

hdr("0. THE TWO VIEWS")
if not B_DIR.exists():
    print(f"  {B_DIR} does not exist — run 04_demo/make_second_view.py first.")
    raise SystemExit(2)
man_a, cs_a, tracks = load(A_DIR)
man_b, cs_b, _ = load(B_DIR)
pa, pb = man_a["pose"], man_b["pose"]
print(f"  A: {len(cs_a)} contacts from {pa['lat_deg']:.5f},{pa['lon_deg']:.5f} "
      f"bore {pa['boresight_deg_true']:.1f}T")
print(f"  B: {len(cs_b)} contacts from {pb['lat_deg']:.5f},{pb['lon_deg']:.5f} "
      f"bore {pb['boresight_deg_true']:.1f}T")
ok("both views carry contacts", bool(cs_a) and bool(cs_b))
ok("the two cameras are in different places",
   abs(pa["lat_deg"] - pb["lat_deg"]) + abs(pa["lon_deg"] - pb["lon_deg"]) > 1e-4)

def obs(c: EoContact, pose: dict, station: str) -> tri.BearingObservation:
    return tri.BearingObservation(
        station_id=station, lat_deg=pose["lat_deg"], lon_deg=pose["lon_deg"],
        bearing_deg_true=c.observed_bearing_deg_true,
        bearing_sigma_deg=c.bearing_uncertainty_deg,
        own_range_m=c.observed_range_m, own_range_sigma_m=c.range_uncertainty_m,
        contact_id=c.contact_id)

hdr("1. PAIR ACROSS THE CAMERAS AND TRIANGULATE EVERY CANDIDATE")
# Every A x B pair is offered to triangulate(); its own gates reject the impossible
# ones (behind a camera, degenerate crossing, bearings too vague). What survives is
# then scored by how well the fix agrees with BOTH cameras' own monocular ranges,
# which is the only independent check available with exactly two stations.
cands = []
for ca in cs_a:
    for cb in cs_b:
        fix = tri.triangulate([obs(ca, pa, "cam-A"), obs(cb, pb, "cam-B")])
        if not fix.usable_for_position:
            continue
        xc = fix.range_crosscheck
        score = max(abs(v) for v in xc.values()) if xc else 99.0
        cands.append((score, ca, cb, fix))
cands.sort(key=lambda t: t[0])
print(f"  {len(cs_a)} x {len(cs_b)} = {len(cs_a)*len(cs_b)} pairs offered; "
      f"{len(cands)} produced a usable fix")

# Greedy one-to-one assignment on the cross-check score.
used_a, used_b, pairs = set(), set(), []
for score, ca, cb, fix in cands:
    if ca.contact_id in used_a or cb.contact_id in used_b: continue
    if score > 3.0:   # both cameras' own ranges must agree with the fix
        continue
    used_a.add(ca.contact_id); used_b.add(cb.contact_id)
    pairs.append((ca, cb, fix, score))
ok("at least two vessels were paired across the cameras", len(pairs) >= 2, f"{len(pairs)} pairs")

hdr("2. HOW FAR IS THE FIX FROM THE TRUTH?  (the number that matters)")
tlat = np.array([t.claimed_lat_deg for t in tracks])
tlon = np.array([t.claimed_lon_deg for t in tracks])

def nearest_truth(lat, lon):
    _, d = predict_measurement(camera_lat_deg=lat, camera_lon_deg=lon,
                               target_lat_deg=tlat, target_lon_deg=tlon)
    i = int(np.argmin(d))
    return float(d[i]), i

print(f"  {'pair':<28}{'cross':>7}{'2-cam err':>11}{'1-cam err':>11}{'ellipse (1s)':>16}")
two_err, one_err = [], []
for ca, cb, fix, score in pairs:
    e2, i = nearest_truth(fix.lat_deg, fix.lon_deg)
    # The SAME vessel from camera A alone: its own bearing + its own monocular range.
    from geometry import project_to_position
    alat, alon = project_to_position(pa["lat_deg"], pa["lon_deg"],
                                     ca.observed_bearing_deg_true, ca.observed_range_m)
    _, d1 = predict_measurement(camera_lat_deg=alat, camera_lon_deg=alon,
                                target_lat_deg=np.array([tlat[i]]),
                                target_lon_deg=np.array([tlon[i]]))
    e1 = float(d1[0])
    two_err.append(e2); one_err.append(e1)
    print(f"  {ca.contact_id[:12]:<13}+{cb.contact_id[:12]:<14}"
          f"{fix.crossing_angle_deg:>6.0f}d{e2:>10.0f}m{e1:>10.0f}m"
          f"{fix.sigma_major_m:>8.0f} x{fix.sigma_minor_m:>5.0f} m")

if two_err:
    m2, m1 = float(np.median(two_err)), float(np.median(one_err))
    print(f"\n  median position error   TWO cameras {m2:>7.0f} m")
    print(f"                          ONE camera  {m1:>7.0f} m")
    print(f"  improvement factor      {m1/max(m2,1e-9):>7.1f}x")
    ok("the two-camera fix beats the single camera on median error", m2 < m1,
       f"{m2:.0f} m vs {m1:.0f} m")
    mean_major = float(np.mean([f.sigma_major_m for _, _, f, _ in pairs]))
    mean_minor = float(np.mean([f.sigma_minor_m for _, _, f, _ in pairs]))
    print(f"  mean fix ellipse        {mean_major:.0f} x {mean_minor:.0f} m (1 sigma)")
    ok("the fix ellipse is far rounder than a single-camera quad",
       mean_major / max(mean_minor, 1e-9) < 3.0,
       f"aspect {mean_major/max(mean_minor,1e-9):.2f} (a 1-camera quad is ~6:1)")
    ok("the true position lies within ~3 sigma of the fix",
       m2 < 3.0 * mean_major, f"{m2:.0f} m vs 3x{mean_major:.0f} m")

hdr("3. THE REFUSALS STILL FIRE")
# One camera alone.
f1 = tri.triangulate([obs(cs_a[0], pa, "cam-A")])
ok("a single bearing is refused", not f1.usable_for_position, f1.refusal_reason or "")
# Two contacts from the SAME camera.
if len(cs_a) >= 2:
    f2 = tri.triangulate([obs(cs_a[0], pa, "cam-A"), obs(cs_a[1], pa, "cam-A")])
    ok("two detections from ONE camera are refused", not f2.usable_for_position,
       (f2.refusal_reason or "")[:70])
# Two stations means no residual, and that must be stated rather than reported as 0.
if pairs:
    ok("a two-station fix reports NO residual (nothing to check against)",
       pairs[0][2].residual_sigma is None, str(pairs[0][2].residual_sigma))
    ok("...and says so in its limitations",
       any("TWO STATIONS" in l for l in pairs[0][2].limitations))

print("\n" + "=" * 78)
print("ALL CHECKS PASSED" if not FAIL else f"FAILED: {FAIL}")
print("=" * 78)
raise SystemExit(1 if FAIL else 0)
