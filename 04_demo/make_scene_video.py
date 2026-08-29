#!/usr/bin/env python3
"""
make_scene_video.py — render the RECORDED scene as a video, so the T1 path has imagery.

WHY THIS EXISTS
The Raspberry Pi left the rig on 2026-08-29 and the EO source became a video this
machine decodes. That immediately exposed something the Pi had been hiding: the recorded
scene has NO IMAGERY AT ALL. make_synthetic_eo.py produces EoContact records — bearings,
ranges, bounding boxes — by projecting real AIS truth through a camera pose. It has never
produced a picture. So on the guaranteed T1 path the console's video pane was empty and
always had been, and the boxes floated over nothing.

This renders the picture those contacts imply, and writes the matching camera pose beside
it so the sensor node can run on it with nothing assumed.

    THE HONESTY RULE THIS FILE IS BUILT AROUND, AND IT IS NOT NEGOTIABLE.

The output is a RENDERING OF A PROJECTION. No camera observed any of it. A rendering that
could be mistaken for camera imagery would be the single most damaging artefact this
project could produce — it would turn an honest synthetic pipeline into a fake sensor
feed, in a domain where the whole argument is that evidence must be defensible. So the
watermark is burned into every frame, it cannot be switched off, and there is no flag to
remove it. For a picture with no watermark, point the node at real footage: that is what
--video is for.

WHAT IS REAL HERE AND WHAT IS DRAWN
  REAL   — every hull's starting position and size, and the RATE AND DIRECTION it drifts
           across frame. Positions come from eo_contacts.jsonl; the drift is computed
           from each contact's own observed speed, heading and range (see drift_px_per_s).
  DRAWN  — the sea, the sky, the haze and the horizon line. The horizon is placed above
           the highest contact; it is scenery, not a measurement, and geometry.py's range
           estimate does not use it.

TWO THINGS LEARNED BY POINTING THE DETECTOR AT AN EARLIER VERSION OF THIS FILE, both of
which are really lessons about the detector and belong here where they are visible:

  1. THE SEA MUST NOT MOVE. pi_sensor.py is MOG2 background subtraction: it finds what
     CHANGED against a background that does not. The first version animated swell lines
     across the whole frame while the hulls sat nearly still, so the detector dutifully
     found the waves — 11 "contacts", none of them a ship. The sea is now static texture
     and the hulls are the only moving thing, which is the condition the method needs.
  2. THE WATERMARK MUST NOT BE A SOLID BAR. find_horizon() takes the row of maximum mean
     vertical gradient. A full-width black bar across the top of the frame is a far
     stronger horizontal edge than a horizon, so the estimate pinned to y=4 and every
     range collapsed to tens of metres. The watermark is now outlined text, whose
     gradient is confined to letter strokes, and the sky/sea step is the strongest
     horizontal edge in the frame by construction.

WHY IT MOVES AT ALL, AND WHAT --time-lapse MEANS
At six kilometres a 14-knot vessel crosses about 2 px of frame per second: real-time
motion is invisible to a viewer and far too slow for background subtraction to separate
from the background it is busy learning. Frames are therefore rendered in COMPRESSED
SCENE TIME — the default 60x means one second of video is a minute of sea. The factor is
printed, written into the pose sidecar, and burned into the watermark, because a viewer
who reads the drift as real-time would badly misjudge every speed on screen.

    python3 04_demo/make_scene_video.py
    python3 03_src/main.py --video 04_demo/out/scene01/scene.mp4 --loop \
        --pose 04_demo/out/scene01/camera_pose_SCENE.json --scale 1

DEPENDENCIES: opencv-python and numpy. Nothing else.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np

_DEMO = Path(__file__).resolve().parent
DEFAULT_SCENE = _DEMO / "out" / "scene01"


def load_scene(scene_dir: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
    contacts = [json.loads(line) for line in
                (scene_dir / "eo_contacts.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
    if not contacts:
        raise SystemExit(f"{scene_dir}/eo_contacts.jsonl is empty — run "
                         f"make_synthetic_eo.py first.")
    return manifest, contacts


def focal_length_px(width_px: int, hfov_deg: float) -> float:
    return (width_px / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def drift_px_per_s(c: dict, f_px: float, time_lapse: float) -> float:
    """
    How fast this hull crosses the frame, from ITS OWN numbers.

    A vessel's apparent sideways motion is the component of its velocity ACROSS the line
    of sight, divided by range — that is the rate its bearing changes — times the focal
    length in pixels. So:

        v_cross = speed * sin(heading - bearing_to_vessel)      [m/s]
        omega   = v_cross / range                               [rad/s of bearing]
        px/s    = omega * f_px * time_lapse

    Nothing is invented: speed, heading, bearing and range are all fields on the contact,
    and a contact missing any of them simply does not drift. The ONLY made-up quantity in
    the whole animation is `time_lapse`, which is why it is stated everywhere.

    Sign follows bearing: positive means bearing increasing, which is rightwards in the
    image, because the projection puts larger relative bearing at larger x.
    """
    v = c.get("observed_speed_ms")
    hdg = c.get("observed_heading_deg_true")
    brg = c.get("observed_bearing_deg_true")
    rng = c.get("observed_range_m")
    if not all(isinstance(x, (int, float)) for x in (v, hdg, brg, rng)) or not rng:
        return 0.0
    v_cross = v * math.sin(math.radians(hdg - brg))
    return (v_cross / rng) * f_px * time_lapse


def render(manifest: dict, contacts: list[dict], out: Path, *,
           seconds: float, fps: float, time_lapse: float, mode: str) -> Path:
    pose = manifest["pose"]
    W = int(pose.get("image_width_px", 1920))
    H = int(pose.get("image_height_px", 1080))
    f_px = focal_length_px(W, float(pose["hfov_deg"]))

    # ---- the horizon, and WHY IT IS NOW A REAL PART OF THE GEOMETRY --------------
    #
    # It used to be scenery placed above the highest bbox. Pointing the detector at that
    # version showed why that was not good enough:
    #   OBSERVED  — the node recovered ~880 m for a hull whose contact says 5868 m.
    #   CLAIMED   — the video depicts what a camera at this pose would see.
    #   THE CAUSE — pi_sensor reads range from the WATERLINE DEPRESSION ANGLE: a hull
    #               whose waterline sits d degrees below the horizon is at h/tan(d). The
    #               harness's bbox ROWS were not produced by that relationship, so
    #               drawing hulls at those rows and then measuring them back gave an
    #               answer seven times wrong — and every association downstream would
    #               have failed for a reason that looked like a fusion bug.
    #   THE FIX   — draw each hull where the camera model says it belongs: row from its
    #               own range, apparent size from its own length and range. The COLUMN
    #               still comes from the contact's bbox, because that is bearing and the
    #               harness computed it correctly.
    #   CONSEQUENCE, AND IT IS THE HONEST ONE — at 25 m height, everything past 2 km
    #               sits within a few pixels of the horizon. The picture is a thin band
    #               of small hulls, which is exactly what a real coastal camera sees, and
    #               it is why monocular range at this distance carries the uncertainty
    #               the node reports. Do not "fix" that by spreading them out.
    cam_h = float(pose.get("height_m") or 25.0)

    if mode == "recorded":
        # EVERY HULL EXACTLY WHERE ITS CONTACT SAYS IT IS, AND NOT MOVING.
        # The console draws its boxes from those same bbox_px values, so this is the
        # only placement under which the box lands on the hull. The recorded scene is
        # ONE TIME INSTANT — its contacts have no later position to drift to — so any
        # motion here would put a moving ship under a stationary box, which is precisely
        # the defect this mode exists to remove.
        horizon = max(60, min(int(c["bbox_px"][1]) for c in contacts) - 30)

        def place(c):
            return tuple(int(v) for v in c["bbox_px"])
    else:
        # DETECTOR MODE: placed by the camera model the sensor node reads back with.
        #   OBSERVED  — the node recovered ~880 m for a hull whose contact says 5868 m.
        #   THE CAUSE — pi_sensor reads range from the WATERLINE DEPRESSION ANGLE:
        #               h/tan(d). The harness puts every bbox at a FIXED row
        #               (image_height * 0.55, see make_synthetic_eo.make_contact), so
        #               its rows carry no range at all. Drawing hulls there and
        #               measuring them back gives an answer many times wrong, and every
        #               association downstream fails for a reason that looks like a
        #               fusion bug.
        #   THE FIX   — row from each contact's own range, apparent size from its own
        #               length and range. The COLUMN still comes from the contact's
        #               bbox, because that is bearing and the harness computes it right.
        #   THE HONEST CONSEQUENCE — at 25 m height everything past 2 km sits within a
        #               few pixels of the horizon, so this is a thin band of small
        #               hulls. That is what a real coastal camera sees, and it is why
        #               monocular range at this distance carries the uncertainty the
        #               node reports. Do not "fix" it by spreading them out.
        horizon = int(H * 0.42)

        def place(c):
            rng_m = c.get("observed_range_m")
            len_m = c.get("observed_length_m")
            x0, _, x1, _ = c["bbox_px"]
            cx = (x0 + x1) / 2.0                  # bearing — the harness got this right
            if not rng_m or not len_m:
                return None
            d_rad = math.atan(cam_h / float(rng_m))
            y_water = horizon + f_px * math.tan(d_rad)
            w = max(6.0, float(len_m) * f_px / float(rng_m))
            h_px = max(4.0, w * 0.30)             # freeboard + superstructure, drawn
            return (int(cx - w / 2), int(y_water - h_px), int(cx + w / 2), int(y_water))

    watermark = ("RENDERED FROM SYNTHETIC CONTACTS - NOT CAMERA IMAGERY"
                 + (f"  - TIME-LAPSE x{time_lapse:g}" if mode == "detector" else
                    "  - STATIC SCENE, ONE TIME INSTANT"))

    # ---- the static background ---------------------------------------------------
    # Built once. It MUST NOT change between frames: MOG2 learns it as background and
    # anything that moves in it becomes a false contact.
    base = np.zeros((H, W, 3), np.uint8)
    for y in range(horizon):                                   # sky, hazing to the horizon
        k = y / max(horizon, 1)
        base[y, :] = (int(120 + 78 * k), int(104 + 74 * k), int(88 + 70 * k))
    for y in range(horizon, H):                                # sea, darkening with depth
        k = (y - horizon) / max(H - horizon, 1)
        base[y, :] = (int(74 + 34 * k), int(55 + 22 * k), int(42 + 14 * k))

    # The sky/sea step is now ~120 levels, and it is deliberately the strongest
    # horizontal edge in the frame so find_horizon() locks onto it.
    cv2.line(base, (0, horizon), (W, horizon), (210, 196, 176), 3, cv2.LINE_AA)

    # Static speckle. Gives the background model some texture to learn instead of a flat
    # plane, without moving. Seeded, so re-running produces the same file.
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 4.0, (H - horizon, W, 1)).astype(np.float32)
    band = base[horizon:, :, :].astype(np.float32) + noise
    base[horizon:, :, :] = np.clip(band, 0, 255).astype(np.uint8)

    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    if not writer.isOpened():
        raise SystemExit(f"could not open {out} for writing")

    # Recorded mode does not drift: see the placement comment above. The rate is still
    # computed in detector mode from each contact's own speed, heading and range.
    drifts = ([0.0] * len(contacts) if mode == "recorded"
              else [drift_px_per_s(c, f_px, time_lapse) for c in contacts])
    n_frames = max(1, int(round(seconds * fps)))

    for i in range(n_frames):
        frame = base.copy()
        t = i / fps

        for c, dx_rate in zip(contacts, drifts):
            box = place(c)
            if box is None:
                continue
            x0, y0, x1, y1 = box
            w_px, h_px = max(3, x1 - x0), max(3, y1 - y0)
            dx = int(round(dx_rate * t))
            bob = 0 if mode == "recorded" else int(round(1.5 * math.sin(t * 1.3 + x0 * 0.01)))
            x0b, x1b = x0 + dx, x1 + dx
            if x1b < 0 or x0b > W:                             # sailed out of frame
                continue
            y0b, y1b = y0 + bob, y1 + bob

            # Hull: a blunt trapezoid, much darker than the sea so background
            # subtraction has a strong signal. The superstructure is scaled off the box,
            # never off the vessel class — the class is a CLAIM, and drawing it would
            # leak a claim into an image that is supposed to carry only observations.
            hull = np.array([[x0b, y1b], [x1b, y1b],
                             [x1b - int(w_px * 0.10), y0b + int(h_px * 0.45)],
                             [x0b + int(w_px * 0.06), y0b + int(h_px * 0.45)]], np.int32)
            cv2.fillPoly(frame, [hull], (34, 28, 24), cv2.LINE_AA)
            sx = x0b + int(w_px * 0.55)
            cv2.rectangle(frame, (sx, y0b), (sx + max(3, int(w_px * 0.16)),
                                             y0b + int(h_px * 0.5)), (50, 44, 40), -1)
            # Reflection. Sells the waterline, which is the feature the range estimate
            # is actually built on.
            for k in range(1, 7):
                yy = y1b + k
                if 0 <= yy < H:
                    a, b = max(0, x0b), min(W, x1b)
                    if b > a:
                        frame[yy, a:b] = (frame[yy, a:b] * 0.74).astype(np.uint8)

        # ---- LIVENESS. A DELIBERATELY STATIC PICTURE NEEDS THIS. -------------------
        # server.py refuses to serve a frozen frame, because a still on a watch screen
        # is indistinguishable from a live view of calm water. A static rendering
        # reintroduces exactly that ambiguity by design, so the frame number and elapsed
        # time are burned in: if the number is not advancing, the stream is not running,
        # and that is readable from across a room.
        stamp = f"frame {i + 1:04d} / {n_frames}   t+{t:05.2f}s"
        cv2.putText(frame, stamp, (W - 430, 34), cv2.FONT_HERSHEY_SIMPLEX,
                    0.70, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(frame, stamp, (W - 430, 34), cv2.FONT_HERSHEY_SIMPLEX,
                    0.70, (170, 255, 170), 2, cv2.LINE_AA)

        # ---- the watermark: OUTLINED TEXT, never a solid bar ----------------------
        # A filled rectangle across the frame is a stronger horizontal edge than the
        # horizon and captures find_horizon(). Outlined text keeps the gradient inside
        # the letter strokes, where it cannot win an argmax over a full row.
        for y_at in (34, H - 22):
            cv2.putText(frame, watermark, (16, y_at), cv2.FONT_HERSHEY_SIMPLEX,
                        0.78, (0, 0, 0), 5, cv2.LINE_AA)
            cv2.putText(frame, watermark, (16, y_at), cv2.FONT_HERSHEY_SIMPLEX,
                        0.78, (60, 210, 255), 2, cv2.LINE_AA)

        writer.write(frame)

    writer.release()

    # ---- the pose that goes WITH this video -------------------------------------
    # The node refuses to run on a pose whose hfov_deg is 0, and it is right to: every
    # bearing it emits is wrong until that number is measured. There is exactly one case
    # where nothing needs measuring, and this is it — the video was DRAWN from this
    # pose, so the camera that "shot" it IS this pose, by construction. Written beside
    # the video so the two cannot be separated and mismatched later.
    # WRITTEN FOR THE DETECTOR CUT ONLY, and that is not tidiness.
    # Both cuts would write the same filename with a DIFFERENT time_lapse_factor, so
    # whichever ran last would leave the other cut's pose lying about its own time
    # base. Nothing reads a pose for the recorded cut anyway — no sensor node runs on
    # it; the console draws the scene's own contacts. One writer, one meaning.
    pose_out = out.parent / "camera_pose_SCENE.json"
    if mode == "detector":
        pose_out.write_text(json.dumps({
            "pose_ref": pose.get("pose_ref", "scene"),
            "yaw_deg_true": pose["boresight_deg_true"],
            "hfov_deg": pose["hfov_deg"],
            "yaw_uncertainty_deg": pose.get("yaw_uncertainty_deg", 2.0),
            "lat_deg": pose["lat_deg"],
            "lon_deg": pose["lon_deg"],
            "height_m": pose.get("height_m", 25.0),
            "image_width_px": W,
            "image_height_px": H,
            "hfov_source": "EXACT — this video was rendered from this pose",
            # Only the detector cut is time-lapsed. Writing 60 beside a static render
            # would tell a reader to divide speeds that were never multiplied.
            "time_lapse_factor": (time_lapse if mode == "detector" else 1.0),
            "rendered_for": mode,
            "note": ("Emitted by make_scene_video.py alongside scene.mp4. Every value is "
                     "copied from scene_manifest.json, the pose make_synthetic_eo projected "
                     "the AIS truth through. NOTHING HERE IS MEASURED FROM A REAL CAMERA, "
                     "because there is no real camera: the imagery is a rendering. Valid "
                     "for that video and nothing else. NOTE time_lapse_factor: the "
                     "DETECTOR cut runs in compressed scene time, so any SPEED inferred "
                     "from frame-to-frame motion in it is that many times too fast. "
                     "Bearings and ranges are unaffected — they are per-frame geometry. "
                     "The RECORDED cut is static and has no time lapse. Point the node "
                     "at real footage and you must measure hfov_deg yourself; see "
                     "camera_pose_TEMPLATE.json."),
        }, indent=2) + "\n", encoding="utf-8")

    moving = sum(1 for d in drifts if abs(d) > 1.0)
    print(f"  MODE: {mode}" + ("  — hulls STATIC at each contact's own bbox_px, so the "
                               "console's boxes land on them" if mode == "recorded"
                               else "  — hulls placed by h/tan(depression) and drifting, "
                                    "for the live sensor node"))
    print(f"wrote {out}  ({n_frames} frames, {n_frames / fps:.1f}s @ {fps:g} fps, {W}x{H})")
    print(f"  {len(contacts)} hulls at the projection's own pixel positions; "
          f"{moving} of them drift visibly")
    if mode == "detector":
        print(f"  drift {min(drifts):+.0f} to {max(drifts):+.0f} px/s at time-lapse "
              f"x{time_lapse:g} — from each contact's own speed, heading and range")
    boxes = [b for b in (place(c) for c in contacts) if b]
    if boxes and mode == "detector":
        print(f"  horizon y={horizon}; waterlines y={min(b[3] for b in boxes)}"
              f"-{max(b[3] for b in boxes)} — placed by h/tan(depression), the same "
              f"model the node reads them back with")
        print(f"  apparent lengths {min(b[2]-b[0] for b in boxes)}"
              f"-{max(b[2]-b[0] for b in boxes)} px. A thin band of small hulls is what "
              f"a 25 m camera actually sees past 2 km.")
    elif boxes:
        print(f"  horizon y={horizon} (scenery); hull boxes are the contacts' own "
              f"bbox_px, untouched — the console draws the same numbers")
    print(f"  sea is STATIC by design: MOG2 detects what moves, so only hulls do")
    if mode == "detector":
        print(f"wrote {pose_out}")
        print(f"  hfov={pose['hfov_deg']:g}  yaw={pose['boresight_deg_true']:.2f}T  "
              f"height={pose.get('height_m', 25.0):g} m — EXACT for this video")
    else:
        print("  no pose sidecar: nothing reads one for the recorded cut — no sensor "
              "node runs on it, the console draws the scene's own contacts")
    if mode == "recorded":
        print(f"\nshow it (T1 — the recorded scene IS the picture; no sensor node):\n"
              f"  python3 03_src/main.py --video {out} --loop")
        print(f"\nfor the live-sensor path instead, build the other cut:\n"
              f"  python3 04_demo/make_scene_video.py --for detector "
              f"--out {out.parent / 'scene_detector.mp4'}")
    else:
        print(f"\nshow it (live sensor node on the clip):\n"
              f"  python3 03_src/main.py --video {out} --loop --live \\\n"
              f"      --pose {pose_out} --scale 1")
    return pose_out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Render the recorded scene as watermarked video + matching pose.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    p.add_argument("--out", type=Path, default=None, help="default: <scene>/scene.mp4")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--fps", type=float, default=12.0,
                   help="12 is plenty and keeps the file small")
    p.add_argument("--for", dest="mode", choices=("recorded", "detector"),
                   default="recorded",
                   help="WHO THE VIDEO IS FOR. 'recorded' (default) draws every hull "
                        "static at its own contact's bbox, so the console's boxes land "
                        "on the hulls while RECORDED is the live source — this is the "
                        "T1 demo. 'detector' places hulls by h/tan(depression) and "
                        "drifts them in compressed scene time, which is what "
                        "pi_sensor.py needs to detect anything and to recover a range. "
                        "ONE VIDEO CANNOT SERVE BOTH: the recorded scene is a single "
                        "time instant, so its boxes cannot follow a moving hull.")
    p.add_argument("--time-lapse", type=float, default=60.0,
                   help="seconds of sea per second of video. At 1.0 a vessel 6 km out "
                        "crosses about 2 px/s: invisible to a viewer and too slow for "
                        "background subtraction to separate from the background. "
                        "Stated in the watermark and in the pose sidecar because a "
                        "viewer reading the drift as real-time would misjudge every "
                        "speed on screen.")
    args = p.parse_args(argv)

    manifest, contacts = load_scene(args.scene)
    out = args.out or (args.scene / "scene.mp4")
    render(manifest, contacts, out, seconds=args.seconds, fps=args.fps,
           time_lapse=args.time_lapse, mode=args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
