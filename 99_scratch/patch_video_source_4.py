#!/usr/bin/env python3
"""99_scratch/patch_video_source_4.py — PART 2c: server.py CLI (--video) and the
section header that still describes the retired rig."""
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


patch("03_src/server.py", [

# --------------------------------------------------- the section header, rewritten
('''# ================================================================================
# 3. THE VIDEO STREAM.
#
# HONEST STATEMENT OF WHAT THIS IS, BECAUSE THE BRIEF ASSUMED SOMETHING THAT IS NOT
# TRUE OF THE CODE: 04_demo/pi_sensor.py serves NO video. It is a push client — it
# opens the camera locally, runs background subtraction, and POSTs EoContact JSON with
# urllib. There is no HTTP server on the Pi and no MJPEG endpoint to proxy.
#
# Rather than invent one (CLAUDE.md: NEVER INVENT AN API), /stream does two real
# things and refuses honestly when neither is configured:
#
#   --mjpeg-url  Straight passthrough of ANY multipart/x-mixed-replace source. That is
#                the standard wire format of mjpg-streamer and motion, either of which
#                runs on a Pi alongside pi_sensor.py without a line of new code. The
#                server does not parse the stream, so it cannot be wrong about it.
#   --camera N   Encode the LOCAL camera with cv2 (measured present on the Mac,
#                1080p PASS). This is the indoor D1/D2 path.
#
# Configured neither way it returns 503 with a stated reason. It NEVER returns a
# placeholder or a frozen last frame: a still image on a watch screen is indistinguish-
# able from a live one showing calm water, and that confusion is the whole failure mode
# SensorNode.last_seen exists to prevent.
# ================================================================================''',

'''# ================================================================================
# 3. THE VIDEO STREAM.
#
# REWRITTEN 2026-08-29 WHEN THE RASPBERRY PI LEFT THE RIG. The EO sensor is now a video
# THIS MACHINE decodes — a file on disk, or a network stream off the internet — and the
# console has to be able to show it. Four ways in, in order of how much they can be
# trusted:
#
#   RELAY        A sensor node POSTs the JPEG it computed its contacts from to
#                /ingest/frame, and /stream hands that straight on. This is the only
#                option where the imagery under the detection boxes is GUARANTEED to be
#                the imagery the boxes came from — see _FrameRelay for why that is a
#                correctness property and not a nicety. Preferred whenever available;
#                it is chosen automatically while a node is publishing.
#   --video      Decode a file path or a URL here (http/https/rtsp, and HLS .m3u8
#                counts). Files are paced to their own frame rate and looped; live
#                sources are neither. This is the source that replaced the Pi.
#   --camera N   A lens on this machine. Same code path as --video: cv2.VideoCapture
#                takes an int, a path or a URL and does not care which.
#   --mjpeg-url  Straight passthrough of ANY multipart/x-mixed-replace source. The
#                server does not parse the stream, so it cannot be wrong about it.
#
# Configured none of these it returns 503 with a stated reason. It NEVER returns a
# placeholder or a frozen last frame: a still image on a watch screen is indistinguish-
# able from a live one showing calm water, and that confusion is the whole failure mode
# SensorNode.last_seen exists to prevent.
#
# ONE CAMERA, ONE CONSUMER. macOS will not usually open the built-in camera twice, so
# --camera on the server AND a node on the same index is a contest one of them loses.
# A file or a network URL has no such limit — which is a further reason the relay is
# the right default and --video the right flag to reach for.
# ================================================================================'''),

# ------------------------------------------------------------------- the CLI flag
('''    p.add_argument("--camera", type=int, default=None,
                   help="Local camera index to encode on /stream when no --mjpeg-url "
                        "is given.")''',
 '''    p.add_argument("--camera", type=int, default=None,
                   help="Local camera index to encode on /stream when no --mjpeg-url "
                        "is given. Shorthand for --video <index>.")
    p.add_argument("--video", default=None,
                   help="EO SOURCE THAT REPLACED THE PI. A video file on disk, or a "
                        "network stream URL (http/https/rtsp; an HLS .m3u8 counts), or "
                        "a bare camera index. Files are paced to their own frame rate "
                        "and looped so the clip outlasts the pitch. Run the sensor "
                        "node on the SAME source with --publish-frames so the boxes "
                        "and the imagery are one observation.")'''),

# ---------------------------------------------------------------- mutual exclusion
('''    if args.mjpeg_url and args.camera is not None:
        # Refuse rather than silently prefer one. Two configured video sources means
        # the operator does not know which one they are watching, and "which camera is
        # this" is a question a case file has to answer.
        raise SystemExit("--mjpeg-url and --camera are mutually exclusive: pick the "
                         "source the evidence should name.")''',
 '''    # Refuse rather than silently prefer one. Two configured video sources means the
    # operator does not know which one they are watching, and "which camera is this" is
    # a question a case file has to be able to answer.
    if sum(x is not None for x in (args.mjpeg_url, args.camera, args.video)) > 1:
        raise SystemExit("--mjpeg-url, --camera and --video are mutually exclusive: "
                         "pick the source the evidence should name.")'''),

# ------------------------------------------------------------------- create_app call
('''        mjpeg_url=args.mjpeg_url,
        camera_index=args.camera,
        allow_real_identities=args.allow_real_identities,
        node_stale_after_s=args.node_stale_after,
        initial_source=args.initial_source,
    )''',
 '''        mjpeg_url=args.mjpeg_url,
        camera_index=args.camera,
        video_source=args.video,
        allow_real_identities=args.allow_real_identities,
        node_stale_after_s=args.node_stale_after,
        initial_source=args.initial_source,
    )'''),

# ------------------------------------------------------------------- startup banner
('''    print(f"  edge node   : POST http://{args.host}:{args.port}/api/contacts")
    print(f"                 (alias of /ingest/contacts — pi_sensor.py's default)")
    print(f"  video       : " + (f"relay {args.mjpeg_url}" if args.mjpeg_url
                                 else f"local camera {args.camera}"
                                 if args.camera is not None
                                 else "none configured -> /stream returns 503"))''',
 '''    print(f"  sensor node : POST http://{args.host}:{args.port}/api/contacts")
    print(f"                 (alias of /ingest/contacts — the node's default)")
    print(f"  frames      : POST http://{args.host}:{args.port}/ingest/frame")
    print(f"                 (raw image/jpeg; a node publishing here takes /stream)")
    print(f"  video       : " + (f"relay {args.mjpeg_url}" if args.mjpeg_url
                                 else f"decoding {args.video}" if args.video
                                 else f"local camera {args.camera}"
                                 if args.camera is not None
                                 else "none configured -> /stream returns 503 until a "
                                      "node publishes frames"))'''),

# ------------------------------------------------------------------- localhost note
('''        print("  NOTE: bound to localhost, so the Pi cannot reach it. Use "
              "--host 0.0.0.0 for the live path.")''',
 '''        print("  NOTE: bound to localhost. Fine now that the sensor node runs on "
              "this machine; use --host 0.0.0.0 only if a node posts from another "
              "device on the network.")'''),
])
print("PART 2c done — server CLI knows --video.")
