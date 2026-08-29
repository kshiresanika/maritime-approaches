"""
pi_sensor.py — the coastal sensor node. Classical CV. NO NEURAL NETWORK.

THE FILENAME IS NOW HISTORICAL. Renaming it mid-event would touch main.py, server.py,
the handoffs, the benchmark and the demo script for no behavioural gain, so the name
stays and this paragraph carries the correction: there is no Raspberry Pi. Rename it to
sensor_node.py after the event, in one commit, when nothing depends on the clock.

WHAT THIS IS (UPDATED 2026-08-29 — THE Pi LEFT THE RIG)
A coastal sensor node standing in for a camera at the water's edge. It reads frames from
whatever it is pointed at — a lens on this machine, a video file on disk, or a network
stream off the internet — detects hulls, computes a true bearing and a range for each,
and POSTs them to the shore station as EoContact-shaped JSON.

WHAT DID NOT CHANGE WHEN THE HARDWARE DID, AND IT IS THE PART THAT MATTERS: this is
still the physical embodiment of the claimed/observed wall. THIS PROCESS HAS NO ACCESS
TO AIS AT ALL. It has never been told an MMSI, has nowhere to put one, and could not
leak an identity into an observation if it tried. That argument was never a property of
the Pi — it is a property of this process's imports and its network calls, both of which
are still one-way. When someone asks how you know the detector is not peeking at the
AIS, you show them that this file imports cv2, numpy and urllib and nothing else, and
that its only outbound route is a POST.

WHAT DID CHANGE, STATED PLAINLY BECAUSE IT BOUNDS THE CLAIM
A recorded clip or an internet stream is EVIDENCE OF THE PIPELINE, not evidence of the
sea. The bearings and ranges below are computed from a pose that describes a real camera
at a real height; point this at a video shot from a different camera and the geometry is
arithmetic performed on an assumption. Say so when demonstrating it. The honest sentence
is "this is the detector and the fusion running on real imagery at demo scale", never
"this is our sensor watching the Elbe".

WHY CLASSICAL AND NOT YOLO — THREE REASONS, IN ORDER OF IMPORTANCE

1. IT WORKS ON THE THING WE ARE ACTUALLY POINTING IT AT. The indoor demo uses toy boats
   or printed silhouettes on a table. COCO's 'boat' class may simply not fire on a paper
   cut-out, and that was the single identified risk that could kill the live segment.
   Background subtraction does not care what the object IS. It sees a thing that was not
   there before, against a background that never moves. Toy boats on a static table are
   close to the best case for this method and close to the worst case for a COCO
   detector.

2. IT IS DEFENSIBLE AS EVIDENCE. The output of this pipeline ends up in a case file.
   "A connected region of 340 pixels, above the fitted waterline, aspect ratio 2.8,
   persisting 14 frames, at these thresholds" is something a court can examine. "A
   network assigned 0.87" is not. Criterion 4 asks for evidence-grade output, and an
   explainable detector is structurally better at it.

3. IT RUNS. YOLOv8n on a Pi CPU is a few frames a second. This runs at 30 with headroom.

WHAT IT GIVES UP, STATED PLAINLY
Silhouette classification. This detector reports SHAPE, not type, so observed_class is
left None and no class-spoof comparison can be made from this sensor. consistency.py
handles that correctly — a None class simply skips the check. Length, heading and
position spoofing all work with zero AI. Only the class dimension needs a model, and it
is the weakest of the four anyway.

THE RANGE METHOD, AND WHY IT MUST BE THIS ONE
Range comes from the WATERLINE DEPRESSION ANGLE: with a known camera height h, a hull
whose waterline appears d degrees below the horizon is at range h / tan(d).

The tempting alternative — estimate range from apparent size — is a trap that silently
destroys criterion 3. Apparent size needs an ASSUMED length; if range came from assumed
length, then observed_length collapses back to the assumed length, the length check
compares a number to itself, and it can never fire. The whole spoof detector would run,
report nothing, and look like it was working.

    Range from geometry. Length from pixels times range. Never the reverse.

DEPENDENCIES: opencv-python and numpy. Nothing else. No pydantic, no torch, no
ultralytics. Validation happens on the shore station, at the boundary where the contract
is consumed.

    # a clip on disk, publishing the frames it detects on (the demo path)
    python3 04_demo/pi_sensor.py --source 02_data/clips/approach.mp4 --loop \
        --pose 04_demo/camera_pose_TABLETOP.json --scale 20 \
        --post http://127.0.0.1:8000/api/contacts \
        --publish-frames http://127.0.0.1:8000/ingest/frame

    # a network stream (HLS/RTSP/HTTP) instead — same command, different --source
    python3 04_demo/pi_sensor.py --source "https://example.org/live/stream.m3u8" ...

    # a lens on this machine
    python3 04_demo/pi_sensor.py --source 0 ...
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

SENSOR_VERSION = "pi_sensor/0.1.0-classical"


# ============================================================================
# Geometry. Duplicated deliberately from eo_detector.py: this file must run on a Pi
# with no project imports and no pydantic, so it cannot import 03_src. The formulas
# are byte-identical. If lane B changes the bearing model, change it here too.
# ============================================================================

def focal_length_px(image_width_px: int, hfov_deg: float) -> float:
    return (image_width_px / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def relative_bearing_deg(centre_x_px: float, image_width_px: int, hfov_deg: float) -> float:
    """
    atan2, NOT linear interpolation across the frame. A rectilinear lens does not map
    angle linearly onto pixels; on a wide lens the linear approximation is several
    degrees wrong at the frame edges, and several degrees is larger than the
    disagreements this whole system exists to detect.
    """
    f = focal_length_px(image_width_px, hfov_deg)
    return math.degrees(math.atan2(centre_x_px - image_width_px / 2.0, f))


def bearing_uncertainty_deg(bbox_width_px: float, centre_x_px: float,
                            image_width_px: int, hfov_deg: float,
                            yaw_uncertainty_deg: float) -> float:
    """Pose error and centroid error, combined in quadrature because they are
    independent. Pose usually dominates and is systematic, not random."""
    f = focal_length_px(image_width_px, hfov_deg)
    centroid_px = max(bbox_width_px * 0.10, 1.0)
    d_deg_d_px = math.degrees(f / (f * f + (centre_x_px - image_width_px / 2.0) ** 2))
    return math.sqrt(yaw_uncertainty_deg ** 2 + (centroid_px * d_deg_d_px) ** 2)


def range_from_waterline(waterline_y_px: float, horizon_y_px: float,
                         image_height_px: int, vfov_deg: float,
                         camera_height_m: float) -> tuple[float, float] | tuple[None, None]:
    """
    Range from the depression angle of the hull's waterline below the horizon.

    Returns (range_m, sigma_m), or (None, None) when the waterline is at or above the
    horizon — which means the object is further than this method can resolve, and
    returning a number anyway would be inventing one.

    The uncertainty is dominated by the derivative dR/dd, which grows as the square of
    range: at long range one pixel of waterline error is worth hundreds of metres. That
    is a real and severe property of the method, and reporting it honestly is what stops
    a distant contact from carrying a confident position mismatch.
    """
    dy = waterline_y_px - horizon_y_px
    if dy <= 1.0:
        return None, None
    deg_per_px = vfov_deg / image_height_px
    d_deg = dy * deg_per_px
    d_rad = math.radians(d_deg)
    if d_rad <= 1e-6:
        return None, None
    rng = camera_height_m / math.tan(d_rad)
    # One-pixel uncertainty on the waterline row, propagated.
    d_rad_1px = math.radians(deg_per_px)
    sigma = abs(camera_height_m * d_rad_1px / (math.sin(d_rad) ** 2))
    sigma = max(sigma, rng * 0.15)   # floor: no monocular range beats 15%.
    return rng, sigma


# ============================================================================
# Detection. Background subtraction and connected components.
# ============================================================================

class ClassicalDetector:
    """
    Five stages, each of which can be inspected with --debug:

      1. horizon   — the row of maximum vertical gradient, smoothed over time. Everything
                     above it is sky and is discarded before any other work happens,
                     which removes clouds and birds for free.
      2. background— MOG2. Sea motion is high-frequency and low-amplitude, and a static
                     camera absorbs it into the background model within a few seconds.
                     A moving camera breaks this method completely, which is why the
                     tripod is not optional.
      3. morphology— open to kill speckle, close to rejoin a hull split by a mast.
      4. components— filtered on area, aspect ratio and proximity to the waterline.
      5. tracking  — nearest-centroid across frames, to build track_length_frames. A
                     contact seen once is a wave; a contact seen fifteen times is a hull.
    """

    def __init__(self, *, min_area_px: int = 120, max_area_frac: float = 0.25,
                 min_aspect: float = 1.2, history: int = 250,
                 var_threshold: float = 26.0, use_horizon: bool = True):
        self.bg = cv2.createBackgroundSubtractorMOG2(
            history=history, varThreshold=var_threshold, detectShadows=False)
        self.min_area_px = min_area_px
        self.max_area_frac = max_area_frac
        self.min_aspect = min_aspect
        self.use_horizon = use_horizon
        self.horizon_y: float | None = None
        self.tracks: dict[int, dict] = {}
        self._next_id = 1

    def find_horizon(self, gray: np.ndarray) -> float:
        """
        Row of maximum mean vertical gradient, exponentially smoothed.

        Smoothing matters more than the estimate: a horizon that jumps between frames
        makes every range estimate jump with it, and a range that flickers reads as a
        broken tool even when the mean is right.
        """
        g = cv2.Sobel(cv2.GaussianBlur(gray, (5, 5), 0), cv2.CV_32F, 0, 1, ksize=3)
        profile = np.abs(g).mean(axis=1)
        y = float(np.argmax(profile))
        self.horizon_y = y if self.horizon_y is None else 0.9 * self.horizon_y + 0.1 * y
        return self.horizon_y

    def detect(self, frame: np.ndarray) -> tuple[list[dict], float]:
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        horizon = self.find_horizon(gray) if self.use_horizon else 0.0

        mask = self.bg.apply(frame)
        if self.use_horizon:
            # Everything above the horizon is sky. Zeroing it before morphology means
            # a bird cannot merge with a hull during the closing step.
            mask[: int(max(0, horizon - 5)), :] = 0

        k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        k7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k3, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k7, iterations=2)

        n, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        out = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if area < self.min_area_px or area > self.max_area_frac * w * h:
                continue
            aspect = bw / max(bh, 1)
            if aspect < self.min_aspect:
                # Ships are long and low. A tall narrow blob is a mast, a person, a
                # post, or a lens artefact. This one filter removes most indoor clutter.
                continue
            out.append({"x": int(x), "y": int(y), "w": int(bw), "h": int(bh),
                        "area": int(area), "cx": float(cents[i][0]),
                        "cy": float(cents[i][1]), "waterline_y": float(y + bh)})
        return out, horizon

    def track(self, dets: list[dict], max_move_px: float = 60.0) -> list[dict]:
        """
        Nearest-centroid association across frames.

        Greedy and simple on purpose: this runs on a Pi, the scene has a handful of
        objects, and the shore station already does a globally optimal assignment where
        it matters. Over-engineering here buys nothing and costs frames.
        """
        used: set[int] = set()
        for d in dets:
            best, best_dist = None, max_move_px
            for tid, t in self.tracks.items():
                if tid in used:
                    continue
                dist = math.hypot(d["cx"] - t["cx"], d["cy"] - t["cy"])
                if dist < best_dist:
                    best, best_dist = tid, dist
            if best is None:
                best = self._next_id
                self._next_id += 1
                self.tracks[best] = {"frames": 0}
            used.add(best)
            t = self.tracks[best]
            t.update({"cx": d["cx"], "cy": d["cy"], "frames": t.get("frames", 0) + 1,
                      "last_seen": time.time()})
            d["track_id"] = best
            d["frames"] = t["frames"]
        # Retire stale tracks so ids do not grow without bound over a long run.
        now = time.time()
        for tid in [k for k, v in self.tracks.items()
                    if now - v.get("last_seen", now) > 3.0]:
            self.tracks.pop(tid, None)
        return dets


# ============================================================================
# Emission.
# ============================================================================

def _classical_detection_confidence(area_px: float, frames: int,
                                    min_area_px: int) -> float:
    """How much this detector believes the box is a vessel at all. NEVER 1.0.

    detection_confidence became a REQUIRED field of EoContact on 2026-08-29 and this
    function did not have one to give, so every POST from this node was rejected with
    HTTP 400 and the classical fallback -- the escape route for COCO 'boat' failing to
    fire on a printed silhouette -- delivered nothing at all.

    The tempting fix is to hardcode 1.0. That is worse than the bug: it tells lane C
    every blob is a certain vessel, and detection_confidence feeds the DARK path, where
    "a vessel is present and not transmitting" rests on the first half of that sentence
    being true. A constant maximum would inflate every DARK confidence with a number
    nobody measured, which breaks MEASURED NUMBERS ONLY silently and in the confident
    direction.

    A background-subtraction detector has exactly two pieces of evidence and no learned
    score, so the value is built from those two and nothing else:
      * how far above the noise floor the region is (area against min_area_px) -- a
        blob at the floor is indistinguishable from the 6-22 px clutter the harness
        deliberately plants in the same band;
      * how many frames it survived -- a wave crest does not persist.
    Saturating, floored at 0.30 and capped at 0.90: this detector is never certain and
    the number must not be able to say otherwise."""
    a = min(1.0, area_px / (4.0 * max(min_area_px, 1)))
    f = min(1.0, frames / 15.0)
    return round(0.30 + 0.60 * (0.5 * a + 0.5 * f), 4)


def to_contact(det: dict, *, horizon_y: float, pose: dict, frame_w: int, frame_h: int,
               scale: float, frame_time: datetime, frame_ref: str,
               min_area_px: int = 120) -> dict:
    """
    One detection -> one EoContact-shaped dict.

    observed_class is None and stays None. This sensor reports shape, not type. Leaving
    it None is not a gap to be filled with a guess — it makes consistency.py skip the
    class comparison, which is the correct behaviour for a detector that cannot classify.
    """
    hfov = pose["hfov_deg"]
    vfov = hfov * frame_h / frame_w
    rel = relative_bearing_deg(det["cx"], frame_w, hfov)
    true_brg = (pose["yaw_deg_true"] + rel) % 360.0
    b_unc = bearing_uncertainty_deg(det["w"], det["cx"], frame_w, hfov,
                                    pose.get("yaw_uncertainty_deg", 5.0))

    # scale: metres of sea per metre of table. 1.0 for a real coastal camera.
    cam_h = pose.get("height_m") or 20.0
    rng_m, rng_sigma = range_from_waterline(
        det["waterline_y"], horizon_y, frame_h, vfov, cam_h)

    length_m = length_sigma = None
    if rng_m is not None:
        f = focal_length_px(frame_w, hfov)
        length_m = det["w"] * rng_m / f
        # Length error inherits range error plus a couple of pixels of box error.
        length_sigma = max(1.0, length_m * math.sqrt(
            (rng_sigma / rng_m) ** 2 + (2.0 / max(det["w"], 1)) ** 2))

    # SCALE IS NOW APPLIED. It was accepted, documented and never read, which meant the
    # tabletop rig emitted TABLE-metres labelled as sea-metres: a 0.4 m model at
    # --scale 20 reported an 0.4 m vessel, so every length check compared a claimed
    # 180 m hull against 0.4 m and returned a colossal, confident, meaningless spoof.
    # Silent corruption, the dd/mm/yyyy class -- no crash, just wrong numbers.
    #
    # Ranges and lengths are LINEAR in the scale factor and scale together. Bearings do
    # NOT: an angle is dimensionless, so scaling it would be wrong. Uncertainties scale
    # with the quantity they qualify, which keeps every sigma ratio -- and therefore
    # every significance lane C computes -- invariant under the choice of scale. That
    # invariance is the property that makes a tabletop rehearsal predict the real run.
    #
    # Default is 1.0, so a real coastal camera is unaffected.
    if scale != 1.0:
        if rng_m is not None:
            rng_m *= scale
            rng_sigma *= scale
        if length_m is not None:
            length_m *= scale
            length_sigma *= scale

    return {
        "contact_id": f"pi-{det['track_id']}",
        "frame_time_utc": frame_time.isoformat(),
        "frame_ref": frame_ref,
        "bbox_px": [det["x"], det["y"], det["x"] + det["w"], det["y"] + det["h"]],
        "observed_bearing_deg_true": round(true_brg, 3),
        "bearing_uncertainty_deg": round(b_unc, 3),
        "observed_bearing_rel_deg": round(max(-180.0, min(180.0, rel)), 3),
        # `is not None`, not truthiness: contracts.py is explicit that None means
        # "not available" and never means zero. A vessel measured at range 0.0 or with
        # a 0.0 sigma is a real measurement, and `if rng_m` would silently convert it
        # into an absence -- which lane C reads as "uncomparable" rather than "known".
        "observed_range_m": round(rng_m, 1) if rng_m is not None else None,
        "range_uncertainty_m": round(rng_sigma, 1) if rng_sigma is not None else None,
        "observed_class": None,
        "observed_class_confidence": None,
        "observed_length_m": round(length_m, 2) if length_m is not None else None,
        "observed_length_uncertainty_m": (
            round(length_sigma, 2) if length_sigma is not None else None),
        "detection_confidence": _classical_detection_confidence(
            det.get("area", det["w"] * det["h"]), int(det["frames"]), min_area_px),
        "observed_heading_deg_true": None,
        "observed_speed_ms": None,
        "track_length_frames": int(det["frames"]),
        "camera_pose_ref": pose.get("pose_ref", "pi-unset"),
    }


def post(url: str, contacts: list[dict], *, node_id: str = "sensor-01",
         kind: str = "video_stream", measured_fps: float | None = None) -> None:
    """
    POST observations to the shore station.

    node_id AND kind ARE NOW SENT, AND THE OMISSION USED TO BE LOAD-BEARING. The server
    defaults an undeclared node's kind, and source_switch only promotes contacts whose
    kind IS THE ACTIVE SOURCE. A node that does not say what it is therefore depends on
    the server guessing the same thing the operator selected — and when the Pi was
    retired from the selectable sources, the old guess ('edge_pi') became a kind that
    could never be promoted. The node would have POSTed 200 OK for ever, both ends
    reporting healthy, and nothing it saw would have reached the picture. Declaring the
    kind removes the guess.
    """
    body = json.dumps({"contacts": contacts, "node_id": node_id, "kind": kind,
                       "measured_fps": measured_fps}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=2.0) as r:
            r.read()
    except urllib.error.HTTPError as e:
        # A 400 means the shore station rejected the contract. Print it — a sensor that
        # silently fails validation looks exactly like a sensor that sees nothing.
        print(f"shore station rejected: {e.code} {e.read().decode()[:200]}",
              file=sys.stderr)
    except Exception as e:
        print(f"post failed: {type(e).__name__}: {e}", file=sys.stderr)


def publish_frame(url: str, frame, *, node_id: str, frame_ref: str,
                  quality: int = 70) -> None:
    """
    Send the shore station the JPEG THIS DETECTOR JUST RAN ON.

    WHY THE NODE PUBLISHES THE FRAME INSTEAD OF THE SERVER OPENING THE VIDEO ITSELF:
    the console draws its detection boxes as an overlay on top of /stream. Two processes
    decoding the same file hold two independent positions in it, so the boxes come from
    frame N and are painted over frame M, and on a looping clip the two drift apart
    without bound. The operator then sees a box asserting a hull is somewhere it is not.
    Publishing the frame the detector used makes the imagery and the boxes one
    observation with one timestamp. server.py's _FrameRelay carries the same argument
    from the other end.

    RAW BYTES, NOT BASE64 IN JSON: a third more bandwidth per frame for nothing. Errors
    are printed and swallowed — a failure to publish IMAGERY must never stop the node
    posting CONTACTS, because the contacts are the evidence and the picture is the
    illustration.
    """
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return
    q = urllib.parse.urlencode({"node_id": node_id, "frame_ref": frame_ref})
    req = urllib.request.Request(f"{url}?{q}", data=buf.tobytes(),
                                 headers={"Content-Type": "image/jpeg"})
    try:
        with urllib.request.urlopen(req, timeout=2.0) as r:
            r.read()
    except Exception as e:                                 # noqa: BLE001
        print(f"frame publish failed: {type(e).__name__}: {e}", file=sys.stderr)


def open_source(spec: str):
    """
    Open a camera index, a file path or a URL. cv2 treats all three the same, which is
    why there is one function here and not three.

    Returns (capture, source, is_file). `is_file` decides two behaviours that are wrong
    for a live source and necessary for a recorded one: pacing to the native frame rate,
    and rewinding at the end.
    """
    src: int | str = int(spec) if spec.isdigit() else spec
    is_file = isinstance(src, str) and Path(src).expanduser().exists()
    if is_file:
        src = str(Path(str(src)).expanduser())
    cap = cv2.VideoCapture(src)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # live sources: watch now, not the past
    except Exception:                                      # noqa: BLE001
        pass
    return cap, src, is_file


def frame_scheme(src, is_file: bool) -> str:
    """
    The provenance prefix that goes into every EoContact's frame_ref.

    It used to be 'pi://' unconditionally. That is now a false statement about where an
    observation came from, and frame_ref is a CASE FILE FIELD: criterion 4 asks which
    vessel on what track from what sensor, and a record that says a Raspberry Pi
    observed something a video file showed is exactly the kind of unattributable claim
    the evidence layer exists to prevent.
    """
    if isinstance(src, int):
        return "cam"
    if is_file:
        return "file"
    return "net"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Classical EO sensor node. No AI.")
    p.add_argument("--pose", required=True, help="camera pose JSON")
    p.add_argument("--post", default=None, help="shore station /api/contacts URL")
    p.add_argument("--out", default=None, help="also append contacts to this JSONL")
    p.add_argument("--source", "--camera", dest="source", default="0",
                   help="THE EO SOURCE. A camera index (0), a video file path, or a "
                        "network stream URL (http/https/rtsp; an HLS .m3u8 counts). "
                        "--camera is accepted as an alias because main.py and a year "
                        "of muscle memory both spell it that way; one dest, so the two "
                        "spellings cannot diverge.")
    p.add_argument("--loop", action="store_true",
                   help="Rewind a video file at the end. The demo outlasts the clip; "
                        "without this the node exits mid-pitch and the console "
                        "correctly reports a dead sensor.")
    p.add_argument("--publish-frames", default=None,
                   help="Shore station /ingest/frame URL. Sends the JPEG this detector "
                        "ran on, so the console's boxes and imagery are ONE "
                        "observation. Strongly preferred over pointing the server at "
                        "the same video with --video: see publish_frame().")
    p.add_argument("--publish-fps", type=float, default=8.0,
                   help="Cap on published frames per second. The picture is an "
                        "illustration of the evidence, not the evidence; 8 is plenty "
                        "and leaves the CPU to the detector.")
    p.add_argument("--node-id", default="sensor-01",
                   help="Provenance. Appears on every contact and in the case file.")
    p.add_argument("--pace", action="store_true", default=None,
                   help="Play a video file at its own frame rate. Default ON for a "
                        "file (a clip read flat out is over in seconds), OFF for a "
                        "camera or a network stream (already arriving at their rate).")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--scale", type=float, default=1.0,
                   help="metres of sea per metre of table. 20 for the 1cm=20m tabletop.")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between posts")
    p.add_argument("--min-area", type=int, default=120)
    p.add_argument("--no-horizon", action="store_true",
                   help="Tabletop with no visible horizon: use the frame top instead.")
    p.add_argument("--debug", action="store_true", help="show the mask window")
    args = p.parse_args(argv)

    pose = json.loads(open(args.pose, encoding="utf-8").read())
    if not pose.get("hfov_deg"):
        raise SystemExit(
            "hfov_deg is 0 in the pose file. Measure it before running: point the "
            "camera at two references whose bearings you know, put each at a frame "
            "edge, and the difference between them IS the horizontal field of view. "
            "Every bearing this sensor emits is wrong until this number is right.")

    cap, src, is_file = open_source(args.source)
    # Frame size is requested of a CAMERA only. Asking a file or a network stream to
    # change resolution is at best ignored and at worst reopens the decoder at a size
    # the container does not have, so the request is scoped to the case where it means
    # something.
    if isinstance(src, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        raise SystemExit(
            f"could not open source {args.source!r}.\n"
            f"  a camera index must exist (try 0);\n"
            f"  a file path must exist relative to where you ran this;\n"
            f"  a URL must be one ffmpeg can read — http/https/rtsp, and an HLS .m3u8\n"
            f"  counts. A YouTube *watch* page is not a stream: resolve it to a media\n"
            f"  URL first (yt-dlp -g <url>) and pass that.")

    scheme = frame_scheme(src, is_file)
    # Pacing: default ON for a file, OFF for anything already arriving at its own rate.
    pace = args.pace if args.pace is not None else is_file
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    period = (1.0 / native_fps) if (pace and 0.0 < native_fps <= 120.0) else 0.0

    det = ClassicalDetector(min_area_px=args.min_area, use_horizon=not args.no_horizon)
    print(f"{SENSOR_VERSION}  pose={pose.get('pose_ref')}  "
          f"hfov={pose['hfov_deg']}  yaw={pose['yaw_deg_true']}  "
          f"sigma={pose.get('yaw_uncertainty_deg')}")
    # WHERE THE FIELD OF VIEW CAME FROM, PRINTED EVERY RUN. hfov_deg feeds every
    # bearing, every bearing sigma and therefore every SPOOF confidence in the case
    # file. A measured 61.4 and a guessed 60 look identical in the JSON and produce
    # verdicts of very different worth, so the pose is asked to declare its provenance
    # and the node repeats the declaration where the operator will see it.
    src_note = str(pose.get("hfov_source") or "NOT STATED")
    if "ASSUM" in src_note.upper() or src_note == "NOT STATED":
        print(f"  !! HFOV PROVENANCE: {src_note}. Bearings from an unmeasured field of "
              f"view are arithmetic on a guess. Usable for a pipeline demo; NOT "
              f"evidence. Measure it before any bearing leaves this machine as a "
              f"claim — camera_pose_TEMPLATE.json says how.")
    else:
        print(f"  hfov provenance: {src_note}")
    print(f"source={args.source!r}  kind={'file' if is_file else 'camera' if isinstance(src, int) else 'network'}  "
          f"native_fps={native_fps:.1f}  paced={'yes' if period else 'no'}  "
          f"loop={'yes' if args.loop else 'no'}  frame_ref={scheme}://")
    if not is_file and not isinstance(src, int):
        print("NETWORK SOURCE. Check the feed's terms of use before showing this to a "
              "room, and keep people out of frame: vessels are the subject, not "
              "persons.")
    print("NO AIS ON THIS PROCESS. It cannot know an identity.")

    last_post = 0.0
    last_publish = 0.0
    last_ok = time.time()
    settle = 0            # frames to skip after a rewind — see the rewind branch
    frames = 0
    t0 = time.time()
    # How long a LIVE source may deliver nothing before the node gives up. A file gets
    # no reconnect (it either loops or ends); a camera or a stream gets bounded retries,
    # because "it blinked" and "it is gone" both look like a failed read and only time
    # separates them.
    RECONNECT_GIVE_UP_S = 15.0
    try:
        while True:
            ok, frame = cap.read()

            if not ok:
                if is_file and args.loop:
                    if not cap.set(cv2.CAP_PROP_POS_FRAMES, 0):
                        # Some containers will not seek. Reopening costs a few hundred
                        # milliseconds and always works.
                        cap.release()
                        cap, src, is_file = open_source(args.source)
                    # THE BACKGROUND MODEL MUST BE REBUILT, AND FORGETTING THIS IS A
                    # DEMO-VISIBLE BUG. A rewind teleports the scene back several
                    # seconds; to a background subtractor that is the entire frame
                    # changing at once, so it reports the whole picture as foreground
                    # and the console fills with contacts that are an artefact of the
                    # loop. Rebuilding the detector and skipping the first frames lets
                    # it re-learn the background before anything is claimed as an
                    # observation.
                    det = ClassicalDetector(min_area_px=args.min_area,
                                            use_horizon=not args.no_horizon)
                    settle = 25
                    last_ok = time.time()
                    continue
                if is_file:
                    print("end of clip. Pass --loop to repeat it for the length of "
                          "the demo.", file=sys.stderr)
                    break
                if time.time() - last_ok > RECONNECT_GIVE_UP_S:
                    print(f"live source delivered nothing for "
                          f"{RECONNECT_GIVE_UP_S:.0f}s — stopping. The console will "
                          f"show this node offline, which is the true statement.",
                          file=sys.stderr)
                    break
                time.sleep(0.5)
                cap.release()
                cap, src, is_file = open_source(args.source)
                continue

            last_ok = time.time()
            frames += 1
            h, w = frame.shape[:2]
            dets, horizon = det.detect(frame)
            dets = det.track(dets)
            now = time.time()

            if now - last_post >= args.interval:
                ts = datetime.now(timezone.utc)
                contacts = [
                    to_contact(d, horizon_y=horizon, pose=pose, frame_w=w, frame_h=h,
                               scale=args.scale, frame_time=ts,
                               frame_ref=f"{scheme}://{pose.get('pose_ref')}/"
                                         f"{ts.isoformat()}")
                    for d in dets if d["frames"] >= 2
                ] if settle <= 0 else []
                fps = frames / max(now - t0, 1e-6)
                print(f"[{ts.strftime('%H:%M:%S')}] {len(dets)} blobs, "
                      f"{len(contacts)} contacts, {fps:.1f} FPS, horizon y={horizon:.0f}"
                      + (f"  [settling after rewind: {settle}]" if settle > 0 else ""))
                if args.post and contacts:
                    post(args.post, contacts, node_id=args.node_id,
                         kind=("mac_camera" if isinstance(src, int) else "video_stream"),
                         measured_fps=fps)
                if args.out:
                    with open(args.out, "a", encoding="utf-8") as fh:
                        for c in contacts:
                            fh.write(json.dumps(c) + "\n")
                last_post = now

            # ---- publish the frame this detector just ran on ---------------------
            # Rate-capped independently of the contact interval: contacts are the
            # evidence and go at --interval; the picture is the illustration and 8 fps
            # is a smooth enough one. Published AFTER detection so the frame and the
            # boxes are the same observation.
            if args.publish_frames and args.publish_fps > 0:
                if now - last_publish >= 1.0 / args.publish_fps:
                    publish_frame(args.publish_frames, frame, node_id=args.node_id,
                                  frame_ref=f"{scheme}://{pose.get('pose_ref')}")
                    last_publish = now

            if settle > 0:
                settle -= 1

            # ---- pace a recorded clip to its own frame rate ----------------------
            # Without this a 30 s clip is consumed in about two seconds: the pipeline
            # sees the whole scene before anyone can look at it, and with --loop it
            # then spins a core for the rest of the demo. A camera and a network
            # stream are not paced — they already arrive at their own rate, and
            # sleeping on top of that is latency added to a live picture.
            if period:
                time.sleep(max(0.0, period - (time.time() - now)))

            if args.debug:
                for d in dets:
                    cv2.rectangle(frame, (d["x"], d["y"]),
                                  (d["x"] + d["w"], d["y"] + d["h"]), (0, 200, 255), 2)
                cv2.line(frame, (0, int(horizon)), (w, int(horizon)), (255, 120, 0), 1)
                cv2.imshow("pi_sensor", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if args.debug:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
