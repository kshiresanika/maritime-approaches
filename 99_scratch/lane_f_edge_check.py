"""
99_scratch/lane_f_edge_check.py — lane F verification for edge/sensor_node.py and
03_src/edge_client.py.

RUN IT (from the repo root, on the Mac):
    python3 99_scratch/lane_f_edge_check.py

WHAT IT DOES. picamera2, ultralytics, fastapi, uvicorn and torch are STUBBED through
sys.modules, so the contract path and the geometry path are exercised with no hardware
and no web stack. It needs only cv2, numpy and pydantic.

WHAT IT CANNOT TELL YOU. Whether picamera2 opens, whether YOLO loads, or what the frame
rate is. Those need the Pi — see 04_demo/edge_benchmark.md.

PROVENANCE. 29 checks, all passing when written, measured in the cloud container
(Linux, python 3.11.15, pydantic 2.13.3, cv2 4.13.0). NOT yet run on the Mac.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in ("03_src", "04_demo", "edge"):
    p = str(_ROOT / _p)
    if p not in sys.path:
        sys.path.insert(0, p)

# Stub the Pi-only and web-only modules. Doing this BEFORE importing sensor_node is the
# whole trick: the module's own imports then succeed and every non-hardware code path
# becomes testable on a laptop.
for _name in ("picamera2", "ultralytics", "fastapi", "fastapi.responses", "uvicorn", "torch"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["picamera2"].Picamera2 = object
sys.modules["ultralytics"].YOLO = object
sys.modules["torch"].set_num_threads = lambda n: None

import pi_sensor                    # noqa: E402
import sensor_node as sn            # noqa: E402
import edge_client as ec            # noqa: E402
from contracts import EoContact, SensorNode  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"   <- {extra}"))
    if not cond:
        FAILS.append(name)


POSE = {"pose_ref": "check-pose", "yaw_deg_true": 90.0, "hfov_deg": 66.0,
        "yaw_uncertainty_deg": 12.0, "height_m": 20.0}
W, H = 640, 480
NOW = datetime.now(timezone.utc)


def det(cx, waterline_y, conf=0.72, frames=5, w=40, tid=7):
    return {"track_id": tid, "conf": conf, "x": int(cx - w / 2),
            "y": int(waterline_y - 20), "w": w, "h": 20, "cx": float(cx),
            "waterline_y": float(waterline_y), "frames": frames}


def build(d, horizon_y=240.0, pose=POSE):
    return sn._build_contact(d, horizon_y=horizon_y, pose=pose, frame_w=W, frame_h=H,
                             scale=1.0, frame_time=NOW, frame_ref="pi://check/1",
                             node_id="edge-pi-01")


# ================================================================== PART A: the node
print("\n########## PART A: edge/sensor_node.py ##########")

print("\n=== the geometry is IMPORTED from pi_sensor, not re-implemented ===")
for fn in ("relative_bearing_deg", "bearing_uncertainty_deg", "range_from_waterline",
           "focal_length_px"):
    check(f"{fn} is pi_sensor's object, not a second copy",
          getattr(sn, fn) is getattr(pi_sensor, fn))
check("contracts.py importable -> the node validates at the source", sn._HAVE_CONTRACTS)
check("MJPEG boundary equals server.py's hardcoded 'frame'", sn.MJPEG_BOUNDARY == "frame")

print("\n=== the payload validates as a real EoContact ===")
p = build(det(400, 300))
check("returns a dict", isinstance(p, dict))
try:
    EoContact(**p); _ok, _err = True, ""
except Exception as e:
    _ok, _err = False, str(e)[:300]
check("EoContact(**payload) validates", _ok, _err)
check("payload is JSON-serialisable as posted", isinstance(json.dumps(p), str))

print("\n=== RULING 1: observed_class stays None; detection_confidence carries YOLO ===")
check("observed_class is None", p["observed_class"] is None)
check("observed_class_confidence is None", p["observed_class_confidence"] is None)
check("detection_confidence == the YOLO score", abs(p["detection_confidence"] - 0.72) < 1e-6)

print("\n=== RULING 2: range from waterline depression, NEVER from apparent size ===")
near = build(det(320, 400, w=40))
far = build(det(320, 260, w=40))
check("a lower waterline is NEARER",
      near["observed_range_m"] < far["observed_range_m"],
      f"near={near['observed_range_m']} far={far['observed_range_m']}")
wide = build(det(320, 400, w=120))
check("box WIDTH does not move range (size must not leak into range)",
      wide["observed_range_m"] == near["observed_range_m"],
      f"{wide['observed_range_m']} vs {near['observed_range_m']}")
check("...but width DOES move observed_length (length = pixels x range)",
      wide["observed_length_m"] > near["observed_length_m"])
check("range always carries an uncertainty", near["range_uncertainty_m"] is not None)
check("length always carries an uncertainty",
      near["observed_length_uncertainty_m"] is not None)

print("\n=== bearing: true = yaw + relative ===")
ctr = build(det(W / 2, 400))
check("frame centre bears exactly the boresight (90.0)",
      abs(ctr["observed_bearing_deg_true"] - 90.0) < 1e-6,
      ctr["observed_bearing_deg_true"])
right = build(det(W - 1, 400))
check("right edge bears ~half the HFOV clockwise (~123 deg)",
      120.0 < right["observed_bearing_deg_true"] < 125.0,
      right["observed_bearing_deg_true"])
check("bearing sigma >= the pose's own yaw uncertainty (it cannot beat its frame)",
      right["bearing_uncertainty_deg"] >= 12.0, right["bearing_uncertainty_deg"])

print("\n=== wraparound: every bearing must land in [0,360) or the contract rejects ===")
_bad = None
for yaw in (0.0, 350.0, 359.9, 180.0):
    pz = dict(POSE); pz["yaw_deg_true"] = yaw
    for cx in (0, W // 2, W - 1):
        q = build(det(cx, 400), pose=pz)
        if not (0.0 <= q["observed_bearing_deg_true"] < 360.0):
            _bad = (yaw, cx, q["observed_bearing_deg_true"]); break
        EoContact(**q)
check("all 12 yaw/position combinations produce a legal bearing", _bad is None, str(_bad))

print("\n=== an object ABOVE the horizon cannot be ranged, and says so ===")
above = build(det(320, 200))
check("range is None above the horizon (not a guess)", above["observed_range_m"] is None)
check("length is None when range is None", above["observed_length_m"] is None)
check("...and it is STILL a valid bearing-only EoContact", bool(EoContact(**above)))

print("\n=== untracked detections still satisfy track_length_frames >= 1 ===")
u = build(det(320, 400, frames=1, tid=-1))
check("track_length_frames == 1, the contract minimum", u["track_length_frames"] == 1)
check("...and it validates", bool(EoContact(**u)))

print("\n=== the identity wall: nothing on the wire can carry an MMSI ===")
_banned = ("mmsi", "name", "imo", "callsign", "identity", "ais")
_leak = [k for k in p if any(b in k.lower() for b in _banned)]
check("no identity-shaped key in the payload", not _leak, str(_leak))
try:
    EoContact(**{**p, "claimed_mmsi": "999000001"}); _rej = False
except Exception:
    _rej = True
check("an injected claimed_mmsi is REJECTED (extra=forbid)", _rej)

print("\n=== REGRESSION GUARD: 04_demo/pi_sensor.py, the classical fallback ===")
_legacy = pi_sensor.to_contact(
    {"track_id": 3, "x": 300, "y": 380, "w": 40, "h": 20, "area": 800, "cx": 320.0,
     "cy": 390.0, "waterline_y": 400.0, "frames": 5},
    horizon_y=240.0, pose=POSE, frame_w=W, frame_h=H, scale=1.0,
    frame_time=NOW, frame_ref="r")
try:
    EoContact(**_legacy); _legacy_ok = True
except Exception:
    _legacy_ok = False
check("pi_sensor.to_contact() output validates as EoContact", _legacy_ok,
      "MISSING detection_confidence — server.py returns HTTP 400 on EVERY post from "
      "the classical node. Filed to lane D as P0 in 99_scratch/requests.md.")


# ============================================================== PART B: the client
print("\n\n########## PART B: 03_src/edge_client.py ##########")


def mk(*, ok, uplink=None, fps=None, temp=None, ever_ok=False):
    c = ec.EdgeNodeClient("http://pi:8080")
    h = {"node_id": "edge-pi-01", "measured_fps": fps, "cpu_temp_c": temp,
         "throttled": "throttled=0x0", "last_detection_utc": None,
         "post_failures": 0, "last_post_error": None}
    if uplink is not None:
        h["uplink_ok"] = uplink
    if ok:
        r = ec.PollResult(ok=True, at_utc=datetime.now(timezone.utc), rtt_ms=3.1, health=h)
        c.last_ok = r; c.last_attempt = r
    else:
        c.last_attempt = ec.PollResult(ok=False, at_utc=datetime.now(timezone.utc),
                                       error="ConnectionRefusedError: refused")
        c.consecutive_failures = 3
        if ever_ok:
            c.last_ok = ec.PollResult(
                ok=True, at_utc=datetime.now(timezone.utc) - timedelta(minutes=5), health=h)
    return c


SRV_ON = [SensorNode(node_id="edge-pi-01", kind="edge_pi", online=True,
                     last_seen=datetime.now(timezone.utc), measured_fps=4.2)]
SRV_NONE: list[SensorNode] = []

print("\n=== the 2x2 diagnosis matrix ===")
check("poll OK  + server hears it           -> HEALTHY",
      ec.reconcile(mk(ok=True, uplink=True, fps=4.2), SRV_ON)["diagnosis"]
      == ec.DIAGNOSIS_HEALTHY)
check("poll OK  + node says uplink DOWN     -> UP BUT NOT POSTING  (the blind spot)",
      ec.reconcile(mk(ok=True, uplink=False), SRV_ON)["diagnosis"]
      == ec.DIAGNOSIS_UP_NOT_POSTING)
check("poll OK  + server never heard of it  -> UP BUT NOT POSTING",
      ec.reconcile(mk(ok=True, uplink=True), SRV_NONE)["diagnosis"]
      == ec.DIAGNOSIS_UP_NOT_POSTING)
check("poll FAIL + contacts still arriving  -> HTTP PORT BLOCKED",
      ec.reconcile(mk(ok=False, ever_ok=True), SRV_ON)["diagnosis"]
      == ec.DIAGNOSIS_PORT_BLOCKED)
check("poll FAIL + was up, now silent       -> NODE DOWN",
      ec.reconcile(mk(ok=False, ever_ok=True), SRV_NONE)["diagnosis"]
      == ec.DIAGNOSIS_DOWN)
check("poll FAIL + never once reached       -> NEVER HEARD FROM",
      ec.reconcile(mk(ok=False, ever_ok=False), SRV_NONE)["diagnosis"]
      == ec.DIAGNOSIS_UNKNOWN)

print("\n=== RULE 1: measured_fps is passed through, never invented ===")
check("fps None on the node stays None on SensorNode",
      mk(ok=True, uplink=True, fps=None).sensor_node().measured_fps is None)
check("fps 4.2 arrives as 4.2",
      mk(ok=True, uplink=True, fps=4.2).sensor_node().measured_fps == 4.2)
check("a non-numeric fps degrades to None rather than raising",
      ec.PollResult(ok=True, at_utc=NOW, health={"measured_fps": "fast"}).measured_fps is None)

print("\n=== RULE 2: last_seen is never fabricated ===")
_n = ec.EdgeNodeClient("http://pi:8080").sensor_node()
check("never-polled node: last_seen is None", _n.last_seen is None)
check("never-polled node: online is False", _n.online is False)
_s = mk(ok=False, ever_ok=True)
_s.last_ok.at_utc = datetime.now(timezone.utc) - timedelta(seconds=120)
_sn = _s.sensor_node()
check("last seen 120 s ago is believed OFFLINE (stale after 30 s)", _sn.online is False)
check("...but last_seen still carries the real observation time", _sn.last_seen is not None)

print("\n=== RULE 3: uplink_ok None is not collapsed into False ===")
check("a node too old to report uplink_ok -> None, not False",
      mk(ok=True, uplink=None).last_ok.uplink_ok is None)
check("...and with the server hearing it, that is HEALTHY, not a false alarm",
      ec.reconcile(mk(ok=True, uplink=None), SRV_ON)["diagnosis"] == ec.DIAGNOSIS_HEALTHY)

print("\n=== node identity and remedies ===")
_c = ec.EdgeNodeClient("http://pi:8080", node_id="configured-name")
check("before any poll, falls back to the configured id", _c.node_id == "configured-name")
_c2 = mk(ok=True, uplink=True); _c2._configured_node_id = "configured-name"
check("after a poll, the node's self-reported id wins", _c2.node_id == "edge-pi-01")
_r = ec.reconcile(mk(ok=True, uplink=False), SRV_NONE)
check("a non-healthy row names the fix", len(_r["suggested_action"]) > 20)
check("reconcile output is JSON-serialisable (it goes on a wire)",
      isinstance(json.dumps(_r), str))
check("unreachable server -> None (unknown), not [] (running, heard nobody)",
      ec.fetch_server_nodes("http://127.0.0.1:9") is None)

print()
print("=" * 66)
print("ALL CHECKS PASSED" if not FAILS else f"{len(FAILS)} FAILURE(S): {FAILS}")
print("=" * 66)
raise SystemExit(1 if FAILS else 0)
