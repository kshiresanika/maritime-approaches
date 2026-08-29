"""
edge/sensor_node.py — the Raspberry Pi sensor node. picamera2 + YOLO + MJPEG.

WHAT THIS IS
A Pi at the water's edge. It captures with picamera2, detects hulls with a COCO
'boat' YOLO model, turns each detection into an EoContact, POSTs those to the Mac's
/ingest/contacts, and serves an annotated MJPEG stream at /stream so the operator can
see WHAT THE DETECTOR SEES rather than a separate prettier picture.

THIS PROCESS HAS NO ACCESS TO AIS. It has never been told an MMSI, EoContact has
nowhere to put one, and it could not leak an identity into an observation if it tried.
That is the claimed/observed wall made physical: when someone asks how you know the
detector is not peeking at the AIS, you point at the Pi.

RELATIONSHIP TO 04_demo/pi_sensor.py — BOTH EXIST ON PURPOSE
pi_sensor.py is the CLASSICAL node: MOG2 background subtraction, no neural network,
runs anywhere, and does not care whether COCO has ever seen the object. This file is
the YOLO node. They are alternatives, not replacements, and the reason to keep both is
recorded in pi_sensor.py's own docstring: COCO's 'boat' class may simply not fire on a
printed silhouette, and that was the single identified risk that could kill the live
segment. Keep pi_sensor.py as the fallback and switch to it if section 3 of
04_demo/edge_benchmark.md comes back with a detection rate near zero.

The BEARING AND RANGE MATHS IS IMPORTED FROM pi_sensor.py, not re-implemented. There
is exactly one copy of it on the Pi. If lane D changes the bearing model, this file
inherits the change instead of silently disagreeing with it.

THREE RULINGS INHERITED FROM THE PROJECT, EACH ONE LOAD-BEARING

1. observed_class STAYS None. YOLO answers "is this a vessel", not "which class of
   vessel". contracts.py says so in detection_confidence's own description. Writing
   COCO's 'boat' into observed_class would manufacture class mismatches against every
   AIS claim that is not literally "boat", i.e. all of them — a SPOOF verdict generated
   inside our own sensor, invisible in the report. detection_confidence carries YOLO's
   number; observed_class and observed_class_confidence stay None together.

2. RANGE COMES FROM WATERLINE DEPRESSION, NEVER FROM APPARENT SIZE. Range from an
   assumed length makes observed_length collapse back to the assumption, so the length
   check compares a number to itself and can never fire. The whole spoof detector would
   run, report nothing, and look healthy. Geometry first, then length = pixels x range.

3. PERSISTENCE IS A PRECONDITION, NOT A BONUS. Lane C's 2026-08-29 fix makes
   track_length_frames >= 2 a precondition for the DARK label, because a single-frame
   blob does not support the claim "a vessel is present and not transmitting". Running
   with --no-track pins track_length_frames to 1 and therefore makes every contact from
   this node PERMANENTLY INELIGIBLE FOR DARK. The flag prints a warning saying so.

WHY THE STREAM AND THE DETECTOR SHARE ONE CAPTURE LOOP
Not for performance — for evidence. Two loops would draw boxes on a different frame
from the one that produced them, and "the operator sees what the detector sees" would
quietly become false. picamera2 also cannot be opened twice.

WHY THE POST RUNS ON ITS OWN THREAD
If the Mac is down, a blocking POST inside the capture loop stalls capture, which
freezes the MJPEG stream, which shows the operator a still image of a calm sea. A dead
uplink must never be able to impersonate an empty ocean. The poster thread times out
and drops; capture never waits for it.

DEPENDENCIES (see edge/requirements-edge.txt): picamera2 (apt), ultralytics, opencv,
fastapi, uvicorn. pydantic arrives with fastapi, which is why this file CAN validate
against contracts.py locally — see _build_contact().

RUN IT
    python3 edge/sensor_node.py \
        --pose 04_demo/camera_pose_TABLETOP.json \
        --post http://<mac-ip>:8000/ingest/contacts \
        --node-id edge-pi-01 --scale 20
    # then on the Mac:
    python3 03_src/server.py --host 0.0.0.0 --mjpeg-url http://<pi-ip>:8080/stream
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

NODE_VERSION = "sensor_node/0.1.0-yolo"

# THE MJPEG BOUNDARY IS NOT A FREE CHOICE.
# 03_src/server.py relays this stream with `_mjpeg_passthrough`, which forwards our
# bytes unchanged while declaring `boundary=frame` in ITS OWN Content-Type header. If
# we emit a different boundary token the relayed stream is undecodable in the browser
# while the direct stream works perfectly — a fault that appears only through the Mac
# and looks like a network problem. Keep this string equal to server.MJPEG_BOUNDARY.
MJPEG_BOUNDARY = "frame"

# --------------------------------------------------------------------------------
# Import bootstrap.
#
# 04_demo is added to sys.path so the geometry can be imported from pi_sensor.py.
# LIBRARIES.md settles the convention: 03_src and 04_demo begin with a digit, are not
# legal package names, so sys.path + flat imports. Do not invent a different one.
# --------------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT / "04_demo"), str(_ROOT / "03_src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from pi_sensor import (                      # noqa: E402
        bearing_uncertainty_deg,
        focal_length_px,
        range_from_waterline,
        relative_bearing_deg,
    )
    from pi_sensor import ClassicalDetector      # noqa: E402  (horizon finder only)
except ImportError as exc:                       # pragma: no cover
    raise SystemExit(
        f"cannot import the geometry from 04_demo/pi_sensor.py: {exc}\n"
        "This file deliberately does NOT carry its own copy of the bearing maths — a\n"
        "second copy is how two sensors start disagreeing about where north is.\n"
        "Sync 04_demo/pi_sensor.py to the Pi (edge/sync_to_pi.sh already does)."
    ) from exc

# contracts.py is OPTIONAL but strongly preferred. When present we construct real
# EoContact objects and validate here, at the source: a contract error caught on the Pi
# names the field in this file's own log, whereas the same error caught on the Mac
# arrives as an HTTP 400 in a process that did not create the data. server.py validates
# again at ingest regardless — that is its job and it is not redundant, it is the
# boundary check for a sensor we did not write.
try:
    from contracts import EoContact              # noqa: E402
    _HAVE_CONTRACTS = True
except Exception:                                 # pragma: no cover
    EoContact = None                              # type: ignore[assignment]
    _HAVE_CONTRACTS = False


# ================================================================================
# 1. The latest-frame slot.
# ================================================================================

class LatestFrame:
    """
    A one-slot buffer holding the most recent annotated JPEG.

    WHY ONE SLOT AND NOT A QUEUE. A queue gives a slow browser every frame it missed,
    so the stream falls further and further behind the sea while looking perfectly
    smooth. An operator watching a thirty-second-old picture and believing it is live
    is worse than an operator watching no picture at all. Dropping intermediate frames
    is the correct behaviour for a situational-awareness feed: latest always wins.

    The Condition (rather than a sleep-poll) means a viewer wakes when a frame is
    actually ready, so an idle stream costs no CPU on a device we are benchmarking.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._seq = 0

    def publish(self, jpeg: bytes) -> None:
        with self._cond:
            self._jpeg = jpeg
            self._seq += 1
            self._cond.notify_all()

    def wait_for_next(self, last_seq: int, timeout: float = 5.0) -> tuple[bytes | None, int]:
        """Block until a frame newer than last_seq exists. Returns (jpeg, seq)."""
        with self._cond:
            if self._seq <= last_seq:
                self._cond.wait(timeout)
            return self._jpeg, self._seq


# ================================================================================
# 2. The detector.
# ================================================================================

class BoatDetector:
    """
    ultralytics YOLO restricted to the COCO 'boat' class.

    THE CLASS IS RESOLVED BY NAME, NEVER BY INDEX. 'boat' is index 8 in COCO today, but
    the index is a property of the weights file, not of the world. A custom or
    re-ordered model would silently filter for a different object and the node would
    report confident detections of, say, traffic lights. Resolving by name fails loudly
    on a model that has no 'boat' instead.
    """

    def __init__(self, weights: str, *, conf: float, imgsz: int, track: bool,
                 threads: int) -> None:
        # Thread count is set BEFORE torch is used for inference. On a 4-core Pi,
        # letting torch take every core starves the capture thread and the MJPEG
        # writer, so measured FPS goes DOWN as thread count goes up past cores-1.
        try:
            import torch
            torch.set_num_threads(max(1, threads))
        except Exception:
            pass

        from ultralytics import YOLO
        self.model = YOLO(weights)
        self.conf = conf
        self.imgsz = imgsz
        self.track = track

        names = getattr(self.model, "names", {}) or {}
        self.boat_ids = [int(i) for i, n in names.items() if str(n).lower() == "boat"]
        if not self.boat_ids:
            raise SystemExit(
                f"the model at {weights} has no class named 'boat'. Classes found: "
                f"{sorted(set(map(str, names.values())))[:20]}...\n"
                "Refusing to guess an index — a wrong index detects the wrong object "
                "confidently, which is worse than not running.")

        self._frames_seen: dict[int, int] = {}   # track_id -> frames observed

    def __call__(self, frame) -> list[dict]:
        """One BGR frame -> list of detection dicts. Never raises on an empty frame."""
        if self.track:
            # persist=True carries tracker state across calls. Without it every frame
            # is a new scene, ids restart at 1, and track_length_frames is a lie.
            res = self.model.track(frame, persist=True, classes=self.boat_ids,
                                   conf=self.conf, imgsz=self.imgsz, verbose=False,
                                   tracker="bytetrack.yaml")
        else:
            res = self.model.predict(frame, classes=self.boat_ids, conf=self.conf,
                                     imgsz=self.imgsz, verbose=False)
        if not res:
            return []
        boxes = getattr(res[0], "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        # boxes.id is None on the first tracked frame and whenever the tracker has
        # nothing associated. Guarding it is not defensive padding: an unguarded
        # .cpu() on None is an AttributeError that kills the capture thread and
        # freezes the stream, which is the failure mode this whole file avoids.
        ids = boxes.id.cpu().numpy() if getattr(boxes, "id", None) is not None else None

        out: list[dict] = []
        for i in range(len(xyxy)):
            x1, y1, x2, y2 = (float(v) for v in xyxy[i])
            tid = int(ids[i]) if ids is not None else -(i + 1)   # negative = untracked
            if tid > 0:
                self._frames_seen[tid] = self._frames_seen.get(tid, 0) + 1
                seen = self._frames_seen[tid]
            else:
                seen = 1
            out.append({
                "track_id": tid,
                "conf": float(confs[i]),
                "x": int(x1), "y": int(y1),
                "w": int(max(1.0, x2 - x1)), "h": int(max(1.0, y2 - y1)),
                "cx": (x1 + x2) / 2.0,
                # The WATERLINE is the BOTTOM edge of the box. This single line is what
                # makes ruling 2 above true: range is derived from where the hull meets
                # the water in the image, never from how big the box is.
                "waterline_y": float(y2),
                "frames": int(seen),
            })
        return out


def annotate(frame, dets: list[dict], horizon_y: float, fps: float, posted_ok: bool):
    """
    Draw exactly what the detector used, and nothing it did not.

    The horizon line is drawn because it is an INPUT to every range estimate. An
    operator who can see the fitted horizon sitting on a table edge instead of the
    waterline knows immediately why the ranges are wrong; without it the ranges are
    just unexplained numbers.
    """
    import cv2
    h, w = frame.shape[:2]
    cv2.line(frame, (0, int(horizon_y)), (w, int(horizon_y)), (255, 120, 0), 1)
    for d in dets:
        x, y, bw, bh = d["x"], d["y"], d["w"], d["h"]
        cv2.rectangle(frame, (x, y), (x + bw, y + bh), (0, 200, 255), 2)
        # The waterline pixel actually used, marked separately from the box.
        cv2.line(frame, (x, int(d["waterline_y"])), (x + bw, int(d["waterline_y"])),
                 (0, 0, 255), 1)
        cv2.putText(frame, f"id{d['track_id']} {d['conf']:.2f} f{d['frames']}",
                    (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (0, 200, 255), 1, cv2.LINE_AA)
    uplink = "UPLINK OK" if posted_ok else "UPLINK DOWN"
    cv2.putText(frame, f"{NODE_VERSION}  {fps:.1f} FPS  {len(dets)} det  {uplink}",
                (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (0, 255, 0) if posted_ok else (0, 0, 255), 1, cv2.LINE_AA)
    return frame


# ================================================================================
# 3. Detection -> EoContact.
# ================================================================================

def _build_contact(det: dict, *, horizon_y: float, pose: dict, frame_w: int,
                   frame_h: int, scale: float, frame_time: datetime,
                   frame_ref: str, node_id: str) -> dict:
    """
    One detection -> one EoContact as a JSON-ready dict.

    Every field here is either measured or None. Nothing is defaulted to a plausible
    number: contracts.py makes range_uncertainty_m optional precisely so that "we could
    not range this" is expressible, and inventing a range with no error bar is the
    failure MEASURED NUMBERS ONLY exists to stop.
    """
    hfov = pose["hfov_deg"]
    vfov = hfov * frame_h / frame_w

    rel = relative_bearing_deg(det["cx"], frame_w, hfov)
    true_brg = (pose["yaw_deg_true"] + rel) % 360.0
    b_unc = bearing_uncertainty_deg(det["w"], det["cx"], frame_w, hfov,
                                    pose.get("yaw_uncertainty_deg", 12.0))

    # scale: metres of sea per metre of table. 1.0 for a real coastal camera.
    cam_h = (pose.get("height_m") or 0.0)
    rng_m, rng_sigma = range_from_waterline(
        det["waterline_y"], horizon_y, frame_h, vfov, cam_h)

    length_m = length_sigma = None
    if rng_m is not None:
        f = focal_length_px(frame_w, hfov)
        length_m = det["w"] * rng_m / f                     # pixels x range. Never the reverse.
        length_sigma = max(1.0, length_m * math.sqrt(
            (rng_sigma / rng_m) ** 2 + (2.0 / max(det["w"], 1)) ** 2))

    payload = {
        "contact_id": f"{node_id}-{det['track_id']}",
        "frame_time_utc": frame_time.isoformat(),
        "frame_ref": frame_ref,
        "bbox_px": [det["x"], det["y"], det["x"] + det["w"], det["y"] + det["h"]],
        # YOLO's confidence answers "is this a vessel". It is NOT a class confidence.
        "detection_confidence": round(min(1.0, max(0.0, det["conf"])), 4),
        "observed_bearing_deg_true": round(true_brg, 3),
        "bearing_uncertainty_deg": round(b_unc, 3),
        "observed_bearing_rel_deg": round(max(-180.0, min(180.0, rel)), 3),
        "observed_range_m": round(rng_m, 1) if rng_m is not None else None,
        "range_uncertainty_m": round(rng_sigma, 1) if rng_sigma is not None else None,
        # RULING 1. Both stay None, together. See the module docstring.
        "observed_class": None,
        "observed_class_confidence": None,
        "observed_length_m": round(length_m, 2) if length_m is not None else None,
        "observed_length_uncertainty_m": (round(length_sigma, 2)
                                          if length_sigma is not None else None),
        "observed_heading_deg_true": None,
        "observed_speed_ms": None,
        "track_length_frames": int(max(1, det["frames"])),
        "camera_pose_ref": pose.get("pose_ref", "pi-unset"),
    }

    if _HAVE_CONTRACTS:
        # Validate at the source. model_dump(mode="json") is used rather than the raw
        # dict so the bytes on the wire are exactly what pydantic round-trips — a
        # datetime formatted by hand and a datetime formatted by pydantic are not
        # always the same string, and the difference only shows up as a 400.
        return EoContact(**payload).model_dump(mode="json")
    return payload


# ================================================================================
# 4. Shared node state.
# ================================================================================

class NodeState:
    """Everything /health reports and the poster thread reads. One lock, short holds."""

    def __init__(self, node_id: str) -> None:
        self.lock = threading.Lock()
        self.node_id = node_id
        self.started_at = time.time()
        self.frames = 0
        self.measured_fps: float | None = None     # None until something COUNTED
        self.contacts: list[dict] = []
        self.last_detection_utc: str | None = None
        self.backend: str | None = None
        self.posted_ok = False
        self.post_attempts = 0
        self.post_failures = 0
        self.last_post_error: str | None = None
        self.stop = threading.Event()


def read_cpu_temp_c() -> float | None:
    """Pi CPU temperature in degrees C, or None. Never a fabricated number."""
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(p.read_text().strip()) / 1000.0, 1)
    except Exception:
        return None


def read_throttled() -> str | None:
    """
    vcgencmd get_throttled. A Pi that throttled during a benchmark produced a number
    nobody can reproduce, so the flag travels WITH the FPS or the FPS is not evidence.
    """
    try:
        import subprocess
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=3)
        return out.stdout.strip() or None
    except Exception:
        return None


# ================================================================================
# 5. Capture loop and poster thread.
# ================================================================================

def open_capture(args):
    """
    Open the camera and return (read_frame, close, backend_name).

    WHY AN ABSTRACTION FOR EXACTLY TWO BACKENDS. The failover story needs this same
    node to run on the MacBook as the MAC_CAMERA source, and the MacBook has no CSI
    camera and no picamera2. Rather than a second sensor implementation — which would
    be a second bearing model, a second contract builder and a second set of bugs —
    the node keeps ONE pipeline and swaps only the frame source.

    read_frame always returns RGB, so nothing downstream has to know which backend won.

    KNOWN COLOUR-ORDER TRAP, STATED RATHER THAN GUESSED: libcamera's "RGB888" is widely
    reported to hand numpy the bytes in BGR order (and "BGR888" to hand back RGB). It
    affects the annotated stream far more than YOLO. If hulls come out blue on /stream,
    pass --swap-rgb; it is one flag rather than a silent wrong assumption baked in.
    """
    import cv2
    backend = args.backend
    if backend == "auto":
        model = Path("/proc/device-tree/model")
        try:
            is_pi = model.exists() and "raspberry pi" in model.read_text(errors="ignore").lower()
        except Exception:
            is_pi = False
        backend = "picamera2" if is_pi else "cv2"

    if backend == "picamera2":
        from picamera2 import Picamera2
        picam2 = Picamera2()
        # main= fixes the OUTPUT size and lets the ISP scale into it. We deliberately do
        # NOT select a narrow sensor mode: a cropped mode narrows the field of view and
        # silently invalidates hfov_deg. See FINDING F-3 in 00_brief/HARDWARE.md.
        picam2.configure(picam2.create_preview_configuration(
            main={"size": (args.width, args.height), "format": "RGB888"}))
        picam2.start()
        time.sleep(1.0)                              # let AE/AWB settle

        def _read():
            f = picam2.capture_array()
            return cv2.cvtColor(f, cv2.COLOR_BGR2RGB) if args.swap_rgb else f

        def _close():
            try:
                picam2.stop(); picam2.close()
            except Exception:
                pass
        return _read, _close, "picamera2"

    # cv2 path: the MacBook camera, or a video file for a bench run.
    src = int(args.source) if str(args.source).isdigit() else args.source
    cap = cv2.VideoCapture(src)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        raise SystemExit(
            f"could not open source {args.source!r} with the cv2 backend.\n"
            "On a Raspberry Pi this is EXPECTED for a CSI camera — use --backend "
            "picamera2 (the default on a Pi). On a Mac, index 0 is usually the "
            "built-in camera and Terminal needs camera permission the first time.")

    def _read():
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("capture read failed (camera unplugged or file ended)")
        # cv2 hands back BGR. Normalise to RGB so the rest of the loop is identical
        # whichever backend opened the camera.
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    return _read, cap.release, "cv2"


def capture_loop(state: NodeState, latest: LatestFrame, *, pose: dict, args) -> None:
    import cv2

    det = BoatDetector(args.weights, conf=args.conf, imgsz=args.imgsz,
                       track=not args.no_track, threads=args.threads)
    horizon = ClassicalDetector(use_horizon=True)   # used ONLY for find_horizon()

    read_frame, close_capture, backend_name = open_capture(args)
    state.backend = backend_name

    t0 = time.time()
    last_post = 0.0
    frames = 0
    print(f"{NODE_VERSION}  backend={backend_name}  pose={pose.get('pose_ref')}  "
          f"hfov={pose['hfov_deg']}  "
          f"yaw={pose['yaw_deg_true']}  sigma={pose.get('yaw_uncertainty_deg')}")
    print("NO AIS ON THIS PROCESS. It cannot know an identity.")

    try:
        while not state.stop.is_set():
            frame = read_frame()
            frames += 1
            h, w = frame.shape[:2]

            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            hy = horizon.find_horizon(gray)
            dets = det(frame)

            now = time.time()
            fps = frames / max(now - t0, 1e-6)

            ts = datetime.now(timezone.utc)
            contacts = []
            for d in dets:
                try:
                    contacts.append(_build_contact(
                        d, horizon_y=hy, pose=pose, frame_w=w, frame_h=h,
                        scale=args.scale, frame_time=ts,
                        frame_ref=f"pi://{pose.get('pose_ref')}/{ts.isoformat()}",
                        node_id=state.node_id))
                except Exception as exc:
                    # A contract failure on ONE detection must not kill the node. Print
                    # it loudly and keep the others — a sensor that dies on a bad box
                    # is a sensor that is offline at the worst moment.
                    print(f"contract rejected a detection: {type(exc).__name__}: {exc}",
                          file=sys.stderr)

            with state.lock:
                state.frames = frames
                state.measured_fps = round(fps, 2)
                state.contacts = contacts
                if contacts:
                    state.last_detection_utc = ts.isoformat()

            # BGR for cv2 drawing and JPEG encoding; picamera2 handed us RGB.
            annotated = annotate(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                                 dets, hy, fps, state.posted_ok)
            ok, buf = cv2.imencode(".jpg", annotated,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), args.jpeg_quality])
            if ok:
                latest.publish(buf.tobytes())

            if args.out and contacts and (now - last_post) >= args.interval:
                with open(args.out, "a", encoding="utf-8") as fh:
                    for c in contacts:
                        fh.write(json.dumps(c) + "\n")
                last_post = now
    finally:
        try:
            close_capture()
        except Exception:
            pass


def poster_loop(state: NodeState, *, url: str, interval: float, kind: str,
                heartbeat: float = 2.0) -> None:
    """
    POST the latest contacts on an interval, on its own thread.

    IT NEVER BLOCKS CAPTURE. A 2 s timeout and a bare except mean a dead Mac costs this
    thread two seconds and costs the camera nothing. The uplink state is written back
    so annotate() can stamp UPLINK DOWN on the video: an operator must be able to see
    that the shore station is not hearing this node, on the picture itself.

    The envelope matches server.py's rich form exactly — {contacts, node_id, kind,
    measured_fps} — so SensorNode.measured_fps on the console is a number this node
    actually counted, not a nominal one.
    """
    last_sent = 0.0
    while not state.stop.is_set():
        time.sleep(interval)
        with state.lock:
            contacts = list(state.contacts)
            fps = state.measured_fps
        now = time.time()

        # THE HEARTBEAT, AND WHY IT IS NOT OPTIONAL.
        #
        # Posting only when there are contacts means a perfectly healthy sensor watching
        # an empty sea posts NOTHING, and the shore station's watchdog marks it offline
        # after NODE_STALE_AFTER_S. The console then shows a dead sensor next to an empty
        # map — which is precisely the "quiet queue or dead sensor?" ambiguity SensorNode
        # exists to resolve, reintroduced from the sensor end.
        #
        # It carries heartbeat:true so the server can record liveness WITHOUT blanking
        # the picture or spinning the pipeline once per node per interval.
        is_heartbeat = not contacts
        if is_heartbeat and (now - last_sent) < heartbeat:
            continue
        last_sent = now

        body = json.dumps({"contacts": contacts, "node_id": state.node_id,
                           "kind": kind, "measured_fps": fps,
                           "heartbeat": is_heartbeat}).encode("utf-8")
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with state.lock:
            state.post_attempts += 1
        try:
            with urllib.request.urlopen(req, timeout=2.0) as r:
                r.read()
            with state.lock:
                state.posted_ok = True
                state.last_post_error = None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            with state.lock:
                state.posted_ok = False
                state.post_failures += 1
                state.last_post_error = f"HTTP {e.code}: {detail}"
            print(f"shore station rejected: {e.code} {detail}", file=sys.stderr)
        except Exception as e:
            with state.lock:
                state.posted_ok = False
                state.post_failures += 1
                state.last_post_error = f"{type(e).__name__}: {e}"
            print(f"post failed: {type(e).__name__}: {e}", file=sys.stderr)


# ================================================================================
# 6. The web app.
# ================================================================================

def create_app(state: NodeState, latest: LatestFrame, *, kind: str):
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

    app = FastAPI(title="edge sensor node", docs_url=None, redoc_url=None)

    def mjpeg_generator():
        """
        multipart/x-mixed-replace, one part per frame.

        MJPEG and not WebRTC on purpose: WebRTC needs signalling, ICE and a negotiation
        that can fail in a dozen ways on a venue network. MJPEG is an HTTP response that
        never ends, works in every browser with zero negotiation, and degrades by
        dropping frames rather than by failing to connect.
        """
        seq = 0
        while not state.stop.is_set():
            jpeg, seq = latest.wait_for_next(seq, timeout=5.0)
            if jpeg is None:
                continue
            yield (b"--" + MJPEG_BOUNDARY.encode() + b"\r\n"
                   b"Content-Type: image/jpeg\r\n"
                   b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                   + jpeg + b"\r\n")

    @app.get("/stream")
    def stream():
        return StreamingResponse(
            mjpeg_generator(),
            media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")

    @app.get("/health")
    def health():
        """
        What 03_src/edge_client.py polls.

        measured_fps is None until frames have been counted, and stays None rather than
        becoming a nominal figure. A node reporting a configured 30 while delivering 4
        is how a demo gets questioned on stage with no answer.
        """
        with state.lock:
            return JSONResponse({
                "status": "ok",
                "node_id": state.node_id,
                "kind": kind,
                "node_version": NODE_VERSION,
                "backend": state.backend,
                "uptime_s": round(time.time() - state.started_at, 1),
                "frames": state.frames,
                "measured_fps": state.measured_fps,
                "contacts_now": len(state.contacts),
                "last_detection_utc": state.last_detection_utc,
                "uplink_ok": state.posted_ok,
                "post_attempts": state.post_attempts,
                "post_failures": state.post_failures,
                "last_post_error": state.last_post_error,
                "cpu_temp_c": read_cpu_temp_c(),
                "throttled": read_throttled(),
            })

    @app.get("/contacts")
    def contacts():
        """The contacts as last POSTed. For debugging the wire format from a browser."""
        with state.lock:
            return JSONResponse(state.contacts)

    @app.get("/")
    def index():
        return HTMLResponse(
            "<html><body style='background:#111;color:#ddd;font-family:system-ui'>"
            f"<h3>{NODE_VERSION} — {state.node_id}</h3>"
            "<img src='/stream' style='max-width:100%'>"
            "<p><a style='color:#6cf' href='/health'>/health</a> · "
            "<a style='color:#6cf' href='/contacts'>/contacts</a></p>"
            "<p style='color:#888'>This process has no AIS connection.</p>"
            "</body></html>")

    return app


# ================================================================================
# 7. main
# ================================================================================

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pi edge sensor node: picamera2 + YOLO + MJPEG.")
    p.add_argument("--pose", required=True, help="camera pose JSON")
    p.add_argument("--post", default=None, help="Mac /ingest/contacts URL")
    p.add_argument("--node-id", default=f"edge-{socket.gethostname()}")
    p.add_argument("--kind", default="edge_pi", choices=["edge_pi", "mac_camera", "file"])
    p.add_argument("--backend", default="auto",
                   choices=["auto", "picamera2", "cv2"],
                   help="frame source. auto = picamera2 on a Raspberry Pi, cv2 "
                        "elsewhere. Use cv2 on the MacBook to run this node as the "
                        "MAC_CAMERA failover source.")
    p.add_argument("--source", default="0",
                   help="cv2 backend only: camera index or video file path.")
    p.add_argument("--swap-rgb", action="store_true",
                   help="picamera2 only: swap channel order if hulls look blue on "
                        "/stream. libcamera's RGB888 is widely reported to hand back "
                        "BGR bytes.")
    p.add_argument("--heartbeat", type=float, default=2.0,
                   help="seconds between 'alive, nothing in frame' posts. Must stay "
                        "well under the server's --node-stale-after (8 s) or a healthy "
                        "node watching an empty sea is declared dead.")
    p.add_argument("--weights", default=str(_ROOT / "yolov8n.pt"))
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--imgsz", type=int, default=320,
                   help="YOLO inference size. Smaller is faster and blinder. "
                        "edge/bench_edge.py sweeps this so the choice is MEASURED.")
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) - 1),
                   help="torch threads. Default cores-1: leaving a core for capture "
                        "and the MJPEG writer measurably beats using all of them.")
    p.add_argument("--no-track", action="store_true",
                   help="disable ByteTrack. FASTER, but pins track_length_frames to 1, "
                        "which makes every contact from this node permanently "
                        "ineligible for the DARK label.")
    p.add_argument("--jpeg-quality", type=int, default=70)
    p.add_argument("--interval", type=float, default=1.0, help="seconds between POSTs")
    p.add_argument("--scale", type=float, default=1.0,
                   help="metres of sea per metre of table. 1.0 for a real camera.")
    p.add_argument("--out", default=None, help="also append contacts to this JSONL")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args(argv)

    pose = json.loads(Path(args.pose).read_text(encoding="utf-8"))
    if not pose.get("hfov_deg"):
        raise SystemExit(
            "hfov_deg is 0 in the pose file. Measure it before running: point the "
            "camera at two references whose bearings you know, put each at a frame "
            "edge, and the difference between them IS the horizontal field of view. "
            "Every bearing this sensor emits is wrong until this number is right.")
    if not pose.get("height_m"):
        print("WARNING: height_m is 0 — range_from_waterline will return None for "
              "every detection, so every contact goes out bearing-only. That is a "
              "VALID EoContact and lane C handles it, but no range check can fire.",
              file=sys.stderr)
    if args.no_track:
        print("WARNING: --no-track pins track_length_frames=1. Lane C requires >=2 for "
              "DARK, so nothing from this node can ever be labelled DARK.",
              file=sys.stderr)
    if not _HAVE_CONTRACTS:
        print("WARNING: contracts.py not importable here — contacts are NOT validated "
              "on the Pi. server.py still validates at ingest, so a bad field arrives "
              "as an HTTP 400 rather than a message naming the field.", file=sys.stderr)

    state = NodeState(args.node_id)
    latest = LatestFrame()

    cap = threading.Thread(target=capture_loop, args=(state, latest),
                           kwargs={"pose": pose, "args": args}, daemon=True)
    cap.start()
    if args.post:
        threading.Thread(target=poster_loop, args=(state,),
                         kwargs={"url": args.post, "interval": args.interval,
                                 "kind": args.kind, "heartbeat": args.heartbeat},
                         daemon=True).start()

    import uvicorn
    print("=" * 70)
    print(f"  edge node   : {state.node_id}  ({NODE_VERSION})")
    print(f"  stream      : http://{args.host}:{args.port}/stream")
    print(f"  health      : http://{args.host}:{args.port}/health")
    print(f"  posting to  : {args.post or 'NOTHING (--post not given)'}")
    print(f"  relay on Mac: python3 03_src/server.py --host 0.0.0.0 "
          f"--mjpeg-url http://<pi-ip>:{args.port}/stream")
    print("=" * 70)
    try:
        uvicorn.run(create_app(state, latest, kind=args.kind),
                    host=args.host, port=args.port, log_level="warning")
    finally:
        state.stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
