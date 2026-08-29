#!/usr/bin/env python3
"""99_scratch/patch_video_source_7.py — PART 4: main.py (the one-command rig), plus an
honesty guard in server.py for the case where the imagery and the promoted contacts
come from different places."""
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


# ---- the guard that matters most: imagery from one place, boxes from another -----
patch("03_src/server.py", [
('''        if relay.fresh():
            return "frame-synchronised: the node published the frame it detected on"
        if video_source is not None or mjpeg_url:
            return ("reference imagery: decoded independently of the detector, so the "
                    "boxes are NOT guaranteed to belong to the frame beneath them")
        return "no imagery"''',

 '''        have_imagery = relay.fresh() or video_source is not None or bool(mjpeg_url)
        if not have_imagery:
            return "no imagery"

        # THE WORST CASE FIRST, BECAUSE IT IS THE ONE THAT LIES QUIETLY.
        # The video pane and the promoted contacts are independent: /stream shows
        # whatever imagery exists, while source_switch decides whose CONTACTS reach the
        # picture. Run a clip in the pane while RECORDED is the authority and the
        # console draws boxes from the recorded scene on top of unrelated imagery —
        # every box in the wrong place, nothing on screen saying so, and both halves
        # working exactly as designed. Said out loud here so the console can say it too.
        if backend.source.active == "RECORDED":
            return ("UNRELATED IMAGERY: the contacts on screen come from the RECORDED "
                    "scene, not from this video. The boxes do NOT belong to the frame "
                    "beneath them. Switch the source to VIDEO_STREAM to make the "
                    "picture one observation.")
        if relay.fresh():
            return "frame-synchronised: the node published the frame it detected on"
        return ("reference imagery: decoded independently of the detector, so the "
                "boxes are NOT guaranteed to belong to the frame beneath them")'''),
])

# ------------------------------------------------------------------------ main.py
patch("03_src/main.py", [

('''def start_local_node(args, port: int) -> subprocess.Popen | None:
    """Run the Mac camera as a sensor node, posting to our own ingest route.

    The SAME node the Pi runs, with only the frame source swapped -- one pipeline, one
    bearing model, one set of geometry helpers. Running a second, Mac-specific detector
    would mean the thing rehearsed is not the thing demonstrated.

    Posts to 127.0.0.1 deliberately even though the server binds 0.0.0.0: a local node
    has no reason to leave the machine, and routing it via the LAN address would make
    the demo depend on the venue network being up in order to talk to itself."""
    node = _DEMO / "pi_sensor.py"
    cmd = [sys.executable, str(node),
           "--camera", str(args.camera),
           "--pose", str(args.pose),
           "--scale", str(args.scale),
           "--post", f"http://127.0.0.1:{port}/api/contacts"]''',

 '''def start_local_node(args, port: int) -> subprocess.Popen | None:
    """Run THIS MACHINE'S video as a sensor node, posting to our own ingest route.

    One node, one bearing model, one set of geometry helpers, whatever the frame source
    is -- a lens, a clip on disk, or a network stream. Running a second, source-specific
    detector would mean the thing rehearsed is not the thing demonstrated.

    Posts to 127.0.0.1 deliberately even though the server binds 0.0.0.0: a local node
    has no reason to leave the machine, and routing it via the LAN address would make
    the demo depend on the venue network being up in order to talk to itself.

    DEFECT FIXED HERE, 2026-08-29, AND IT HAD NEVER WORKED.
      OBSERVED  -- `python 03_src/main.py --camera 0` started, printed the node command,
                   and then the console showed no live node at all.
      CLAIMED   -- this function launches the sensor node with the chosen camera.
      THE MISMATCH -- it passed `--camera`, and the node's flag was `--source`.
                   argparse rejected the unknown flag and the child exited immediately
                   with status 2. Because the child's output is piped and only relayed
                   by pump(), the argparse error went where nobody was looking.
      WHY IT MATTERS -- the live-sensor path was dead on the ONE flag that turns it on,
                   and it failed silently: no traceback in the parent, no node in the
                   console, nothing to distinguish it from a camera that sees nothing.
      THE FIX   -- pass `--source`, which the node has always accepted; the node now
                   also accepts `--camera` as an alias for the same dest, so neither
                   spelling can be wrong again.
      CONFIDENCE -- certain; it is one argparse definition and one string.

    THE NODE ALSO PUBLISHES ITS FRAMES. server.py then relays exactly the JPEG the
    detector ran on, so the console's boxes and its imagery are one observation. The
    server is deliberately NOT given the same video to decode for itself: two decoders
    on one file hold two independent positions in it and the boxes drift off the hulls.
    One decoder, and if it dies /stream honestly reports that the video stopped."""
    node = _DEMO / "pi_sensor.py"
    source = args.video if args.video is not None else str(args.camera)
    cmd = [sys.executable, str(node),
           "--source", str(source),
           "--pose", str(args.pose),
           "--scale", str(args.scale),
           "--node-id", str(args.node_id),
           "--publish-frames", f"http://127.0.0.1:{port}/ingest/frame",
           "--post", f"http://127.0.0.1:{port}/api/contacts"]
    if args.loop:
        cmd.append("--loop")'''),

# ------------------------------------------------------------------- CLI additions
('''    p.add_argument("--camera", type=int, default=None,
                   help="also run this local camera index as a sensor node")''',
 '''    p.add_argument("--camera", type=int, default=None,
                   help="also run this local camera index as a sensor node")
    p.add_argument("--video", default=None,
                   help="THE EO SOURCE THAT REPLACED THE PI. A video file on disk, or "
                        "a network stream URL (http/https/rtsp; an HLS .m3u8 counts). "
                        "Runs the sensor node on it and shows it in the console. "
                        "Mutually exclusive with --camera.")
    p.add_argument("--loop", action="store_true",
                   help="rewind a video file at the end, so the clip outlasts the "
                        "pitch instead of the sensor going dark mid-sentence")
    p.add_argument("--node-id", default="sensor-01",
                   help="provenance. Appears on every contact and in the case file.")'''),

# --------------------------------------------------------------- examples block
('''  python 03_src/main.py --camera 0 --scale 20
      T1 plus the Mac camera as a live sensor node over the tabletop rig.''',
 '''  python 03_src/main.py --camera 0 --scale 20
      T1 plus a lens on this machine as a live sensor node over the tabletop rig.

  python 03_src/main.py --video 02_data/clips/approach.mp4 --loop --scale 20
      T1 plus a VIDEO as the EO sensor -- the path that replaced the Raspberry Pi.
      The node detects on the clip and publishes the frames it detected on, so the
      console shows the video with its own boxes over it.

  python 03_src/main.py --video "https://host/live/stream.m3u8" --scale 1
      The same, on a network stream. CHECK THE FEED'S TERMS BEFORE SHOWING IT, and
      keep people out of frame: vessels are the subject, not persons.'''),

# ------------------------------------------------------- mutual exclusion + wiring
('''    args = p.parse_args(argv)

    print("=" * 78)
    print("  MARITIME APPROACHES — shore station, one command")
    print("=" * 78)''',
 '''    args = p.parse_args(argv)

    if args.video is not None and args.camera is not None:
        raise SystemExit("--video and --camera are mutually exclusive: one node, one "
                         "frame source, so the case file can name it.")

    print("=" * 78)
    print("  MARITIME APPROACHES — shore station, one command")
    print("=" * 78)'''),

# ------------------------------------------------- initial source follows the flag
('''        node_stale_after_s=8.0,
        initial_source="RECORDED",''',
 '''        node_stale_after_s=8.0,
        # THE AUTHORITY FOLLOWS THE FLAG, and the reason is honesty rather than
        # convenience. /stream shows whatever imagery exists; source_switch decides
        # whose CONTACTS reach the picture. Leave the authority on RECORDED while a
        # video is in the pane and the console draws the recorded scene's boxes over
        # unrelated imagery -- every box in the wrong place, both halves working as
        # designed, nothing on screen saying so. So: asked for a video source, start on
        # it. Asked for nothing, start on RECORDED, which is still the only source that
        # cannot fail. The operator can switch either way at any time, and /health's
        # video_sync names the mismatch if they land in it deliberately.
        initial_source=("MAC_CAMERA" if args.camera is not None
                        else "VIDEO_STREAM" if args.video is not None
                        else "RECORDED"),'''),

# ------------------------------------------------------------- node start condition
('''    node_proc = start_local_node(args, args.port) if args.camera is not None else None''',
 '''    node_proc = (start_local_node(args, args.port)
                 if (args.camera is not None or args.video is not None) else None)'''),

# --------------------------------------------------------------------- the banner
('''    print(f"  PI POSTS TO  http://{ip}:{args.port}/api/contacts")''',
 '''    print(f"  SENSOR POSTS http://{ip}:{args.port}/api/contacts")'''),

('''    print(f"  local node   " + (f"camera {args.camera}, scale {args.scale:g}"
                                if node_proc else "none (Pi posts on its own)"))''',
 '''    print(f"  EO source    " + (
        f"video {args.video}{' (looping)' if args.loop else ''}, scale {args.scale:g}"
        if node_proc and args.video is not None else
        f"camera {args.camera}, scale {args.scale:g}"
        if node_proc else
        "none — RECORDED scene only. Pass --video <file|url> or --camera 0 to put a "
        "live sensor and a picture on the console."))'''),

('''    print("  On the Pi:")
    print(f"    python3 edge/sensor_node.py --post http://{ip}:{args.port}"
          f"/api/contacts --pose <pose.json>")
    print(f"    python3 04_demo/pi_sensor.py --post http://{ip}:{args.port}"
          f"/api/contacts --pose <pose.json>      # classical fallback")''',
 '''    print("  A sensor node on ANOTHER machine, if you ever want one:")
    print(f"    python3 04_demo/pi_sensor.py --source <file|url|index> \\\\")
    print(f"        --pose <pose.json> \\\\")
    print(f"        --post http://{ip}:{args.port}/api/contacts \\\\")
    print(f"        --publish-frames http://{ip}:{args.port}/ingest/frame")
    print(f"    (needs --host 0.0.0.0, which is this file's default)")'''),
])
print("PART 4 done — main.py wired, honesty guard added.")
