#!/usr/bin/env python3
"""
99_scratch/lane_g_console_check.py — IS THE SECOND CAMERA ACTUALLY ON THE CONSOLE?

lane_g_two_camera_check.py proves the GEOMETRY helps. check_triangulate.py proves the
SOLVER is right. Neither touches the shore station, and for most of this project's life
triangulate.py was imported by nothing at all — proven maths the console had never seen.
This check covers the wiring: a real uvicorn, the real create_app, and the payload the
browser actually reads.

WHAT IT ASSERTS, and each one is a claim made somewhere on screen:
  1. /api/state publishes every registered sensor, PRIMARY FIRST, so the map can draw a
     wedge per camera and know which is which.
  2. Cross-fixes are published, and each lies inside BOTH cameras' DECLARED wedges —
     the map draws the declared wedges and their overlap, so a fix outside one would be
     the console contradicting itself.
  3. fix_by_contact is keyed by BOTH cameras' contact ids: the pipeline only ever holds
     camera A's, and the evidence layer needs B's.
  4. The fix is materially better than the monocular estimate it supersedes.
  5. The time gate refuses two bearings taken minutes apart.
  6. With ONE camera the keys still exist and are empty — an absent key and an empty
     list read identically to a console, and only one of them is a statement.
"""
import json, math, socket, sys, threading, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "03_src"), str(ROOT / "04_demo")]

import uvicorn                                             # noqa: E402
from server import create_app                              # noqa: E402
import multiview as mv                                     # noqa: E402
from contracts import EoContact                            # noqa: E402

A, B = ROOT / "04_demo/out/scene01", ROOT / "04_demo/out/scene01b"
FAILED = []


def ok(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILED.append(label)


def serve(**kw):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    app = create_app(scene_dir=A, web_dir=ROOT / "03_src/web",
                     audit_path=Path("/tmp/lane_g_console.jsonl"),
                     mjpeg_url=None, camera_index=None, allow_real_identities=False,
                     node_stale_after_s=8.0, video_source=None,
                     initial_source="RECORDED", **kw)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                        log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3); break
        except Exception:
            time.sleep(0.1)
    return srv, port


def state(port):
    return json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/state", timeout=8).read())


def visible_from(pose, lat, lon):
    """The DECLARED wedge — the same one the map draws. No slack beyond the pose's own
    stated yaw uncertainty, because drawn and gated must be one statement."""
    dlat, dlon = lat - pose["lat_deg"], lon - pose["lon_deg"]
    k = math.cos(math.radians((lat + pose["lat_deg"]) / 2))
    n, e = dlat * 111320.0, dlon * 111320.0 * k
    brg, rng = math.degrees(math.atan2(e, n)) % 360, math.hypot(n, e)
    half = pose.get("fov_half_angle_deg") or (pose.get("hfov_deg", 60) / 2)
    margin = pose.get("yaw_uncertainty_deg") or 2.0
    off = abs((brg - pose["boresight_deg_true"] + 180) % 360 - 180)
    return off <= half + margin and rng <= (pose.get("max_range_m") or 1e9)


print("\n=== 1-4. two cameras registered, fixes published and inside both wedges ===")
srv, port = serve(sensor_scenes=[B])
st = state(port)
sensors = st.get("sensors", [])
ok("both sensors are published", len(sensors) == 2, f"{len(sensors)}")
ok("the primary is first", sensors and sensors[0].get("role") == "primary",
   sensors[0].get("role") if sensors else "-")
fixes = st.get("fixes", [])
ok("cross-fixes are published", len(fixes) >= 2, f"{len(fixes)} fixes")

pa = json.loads((A / "scene_manifest.json").read_text())["pose"]
pb = json.loads((B / "scene_manifest.json").read_text())["pose"]
inside = all(visible_from(pa, f["lat_deg"], f["lon_deg"])
             and visible_from(pb, f["lat_deg"], f["lon_deg"])
             for f in fixes if f.get("lat_deg") is not None)
ok("every fix lies inside BOTH declared wedges (drawn == gated)", inside)

fbc = st.get("fix_by_contact", {})
both = all(all(cid in fbc for cid in f["contact_ids"]) for f in fixes)
ok("fix_by_contact is keyed by BOTH cameras' contact ids", both, f"{len(fbc)} keys")

aspects = [f["sigma_major_m"] / max(f["sigma_minor_m"], 1e-9) for f in fixes]
worst = max(aspects) if aspects else 99
ok("the fix ellipse is far rounder than a 1-camera quad (~6:1)", worst < 3.0,
   f"worst aspect {worst:.2f}")
majors = [f["sigma_major_m"] for f in fixes]
ok("...and its long axis is well under the ~1250 m monocular one",
   majors and max(majors) < 700, f"max major {max(majors):.0f} m" if majors else "-")
srv.should_exit = True; time.sleep(0.3)

print("\n=== 5. the time gate ===")
cs = [EoContact(**json.loads(l))
      for l in (A / "eo_contacts.jsonl").read_text().splitlines() if l.strip()]
cs += [EoContact(**json.loads(l))
       for l in (B / "eo_contacts.jsonl").read_text().splitlines() if l.strip()]
poses = {pa["pose_ref"]: pa, pb["pose_ref"]: pb}
# The recorded scene is ONE instant, so every dt is exactly 0 and the default window
# passes everything — which is the behaviour the recorded path needs. To test the gate,
# camera B's contacts are shifted a minute into the future: a 14-knot hull covers 430 m
# in that time, further than the ellipse this whole feature exists to shrink, so those
# two bearings are not one hull however well they cross.
from datetime import timedelta
n_same = len(mv.cross_fix(cs, poses, primary=pa["pose_ref"]))
shifted = [c.model_copy(update={"frame_time_utc": c.frame_time_utc + timedelta(seconds=60)})
           if c.camera_pose_ref == pb["pose_ref"] else c for c in cs]
n_shift = len(mv.cross_fix(shifted, poses, primary=pa["pose_ref"]))
n_open = len(mv.cross_fix(shifted, poses, primary=pa["pose_ref"], max_time_delta_s=None))
ok("same-instant pairs are found at the default window", n_same >= 2, f"{n_same}")
ok("...and a 60 s offset refuses every one of them", n_shift == 0, f"{n_shift} survived")
ok("...for the TIME and not the geometry (gate off, they return)", n_open == n_same,
   f"{n_open} with the gate open")

print("\n=== 6. one camera: the keys exist and are empty ===")
srv, port = serve()
st = state(port)
ok("one sensor is published", len(st.get("sensors", [])) == 1)
ok("`fixes` is present and empty, not absent",
   "fixes" in st and st["fixes"] == [])
ok("`fix_by_contact` likewise", "fix_by_contact" in st and st["fix_by_contact"] == {})
srv.should_exit = True; time.sleep(0.3)

print("\n" + "=" * 70)
print("ALL CHECKS PASSED" if not FAILED else f"FAILED: {FAILED}")
print("=" * 70)
sys.exit(1 if FAILED else 0)
