"""
pi_sensor.py — the coastal sensor node. Classical CV. NO NEURAL NETWORK.

WHAT THIS IS
A Raspberry Pi with a camera module, standing in for a camera at the water's edge. It
detects hulls, computes a true bearing and a range for each, and POSTs them to the shore
station as EoContact-shaped JSON. It is the physical embodiment of the claimed/observed
wall: THIS PROCESS HAS NO ACCESS TO AIS AT ALL. It has never been told an MMSI, has
nowhere to put one, and could not leak an identity into an observation if it tried.
When someone asks how you know the detector is not peeking at the AIS, you point at the
Pi and say it has no AIS connection.

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

    python3 04_demo/pi_sensor.py --post http://<mac-ip>:8000/api/contacts \
        --pose 04_demo/camera_pose_TABLETOP.json --scale 20
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

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

def to_contact(det: dict, *, horizon_y: float, pose: dict, frame_w: int, frame_h: int,
               scale: float, frame_time: datetime, frame_ref: str) -> dict:
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

    return {
        "contact_id": f"pi-{det['track_id']}",
        "frame_time_utc": frame_time.isoformat(),
        "frame_ref": frame_ref,
        "bbox_px": [det["x"], det["y"], det["x"] + det["w"], det["y"] + det["h"]],
        "observed_bearing_deg_true": round(true_brg, 3),
        "bearing_uncertainty_deg": round(b_unc, 3),
        "observed_bearing_rel_deg": round(max(-180.0, min(180.0, rel)), 3),
        "observed_range_m": round(rng_m, 1) if rng_m else None,
        "range_uncertainty_m": round(rng_sigma, 1) if rng_sigma else None,
        "observed_class": None,
        "observed_class_confidence": None,
        "observed_length_m": round(length_m, 2) if length_m else None,
        "observed_length_uncertainty_m": round(length_sigma, 2) if length_sigma else None,
        "observed_heading_deg_true": None,
        "observed_speed_ms": None,
        "track_length_frames": int(det["frames"]),
        "camera_pose_ref": pose.get("pose_ref", "pi-unset"),
    }


def post(url: str, contacts: list[dict]) -> None:
    body = json.dumps({"contacts": contacts}).encode("utf-8")
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Classical EO sensor node. No AI.")
    p.add_argument("--pose", required=True, help="camera pose JSON")
    p.add_argument("--post", default=None, help="shore station /api/contacts URL")
    p.add_argument("--out", default=None, help="also append contacts to this JSONL")
    p.add_argument("--source", default="0", help="camera index, or a video file path")
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

    src: int | str = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        raise SystemExit(f"could not open source {args.source}")

    det = ClassicalDetector(min_area_px=args.min_area, use_horizon=not args.no_horizon)
    print(f"{SENSOR_VERSION}  pose={pose.get('pose_ref')}  "
          f"hfov={pose['hfov_deg']}  yaw={pose['yaw_deg_true']}  "
          f"sigma={pose.get('yaw_uncertainty_deg')}")
    print("NO AIS ON THIS PROCESS. It cannot know an identity.")

    last_post = 0.0
    frames = 0
    t0 = time.time()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
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
                               frame_ref=f"pi://{pose.get('pose_ref')}/{ts.isoformat()}")
                    for d in dets if d["frames"] >= 2
                ]
                fps = frames / max(now - t0, 1e-6)
                print(f"[{ts.strftime('%H:%M:%S')}] {len(dets)} blobs, "
                      f"{len(contacts)} contacts, {fps:.1f} FPS, horizon y={horizon:.0f}")
                if args.post and contacts:
                    post(args.post, contacts)
                if args.out:
                    with open(args.out, "a", encoding="utf-8") as fh:
                        for c in contacts:
                            fh.write(json.dumps(c) + "\n")
                last_post = now

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
