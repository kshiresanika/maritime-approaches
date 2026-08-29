#!/usr/bin/env python3
"""99_scratch/patch_video_source_6.py — PART 3b: the node's capture loop."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def patch(rel, pairs):
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    for old, new in pairs:
        n = src.count(old)
        if n != 1:
            raise SystemExit(f"ANCHOR FAILED in {rel}: matched {n}, expected 1\n---\n{old[:240]}\n---")
        src = src.replace(old, new)
    p.write_text(src, encoding="utf-8")
    print(f"  patched {rel} ({len(pairs)} edits)")


patch("04_demo/pi_sensor.py", [

# --------------------------------------------------------------- imports
('''import urllib.error
import urllib.request
from datetime import datetime, timezone''',
 '''import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path'''),

# --------------------------------------------------------- capture set-up
('''    src: int | str = int(args.source) if args.source.isdigit() else args.source
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
    t0 = time.time()''',

 '''    cap, src, is_file = open_source(args.source)
    # Frame size is requested of a CAMERA only. Asking a file or a network stream to
    # change resolution is at best ignored and at worst reopens the decoder at a size
    # the container does not have, so the request is scoped to the case where it means
    # something.
    if isinstance(src, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        raise SystemExit(
            f"could not open source {args.source!r}.\\n"
            f"  a camera index must exist (try 0);\\n"
            f"  a file path must exist relative to where you ran this;\\n"
            f"  a URL must be one ffmpeg can read — http/https/rtsp, and an HLS .m3u8\\n"
            f"  counts. A YouTube *watch* page is not a stream: resolve it to a media\\n"
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
    RECONNECT_GIVE_UP_S = 15.0'''),

# ------------------------------------------------------------- the read branch
('''            ok, frame = cap.read()
            if not ok:
                break
            frames += 1''',
 '''            ok, frame = cap.read()

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
            frames += 1'''),

# ------------------------------------------------------ frame_ref + post + publish
('''                contacts = [
                    to_contact(d, horizon_y=horizon, pose=pose, frame_w=w, frame_h=h,
                               scale=args.scale, frame_time=ts,
                               frame_ref=f"pi://{pose.get('pose_ref')}/{ts.isoformat()}")
                    for d in dets if d["frames"] >= 2
                ]
                fps = frames / max(now - t0, 1e-6)
                print(f"[{ts.strftime('%H:%M:%S')}] {len(dets)} blobs, "
                      f"{len(contacts)} contacts, {fps:.1f} FPS, horizon y={horizon:.0f}")
                if args.post and contacts:
                    post(args.post, contacts)''',

 '''                contacts = [
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
                         measured_fps=fps)'''),

# -------------------------------------------------- publishing + pacing + settle
('''            if args.debug:
                for d in dets:''',
 '''            # ---- publish the frame this detector just ran on ---------------------
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
                for d in dets:'''),
])
print("PART 3b done — node loop handles files, loops, network reconnects and publishing.")
