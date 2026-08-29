#!/usr/bin/env python3
"""
99_scratch/patch_video_source_13.py — PART 10: undo a regression of mine, and make the
rendered clip belong to the scene it was rendered from.

TWO SYMPTOMS, ONE CAUSE.

  OBSERVED  — "There are just 2 ships, both dark categorized" and "the detection
              quadrilateral ... stays stagnant while the ships move forward".
  CLAIMED   — the console shows the ranked picture with mixed MATCH/DARK/SPOOF verdicts,
              and its boxes are the detector's output over the imagery it detected on.
  THE CAUSE — patch 7 made `--video` set the ACTIVE SOURCE to VIDEO_STREAM. That was
              wrong in a way that is obvious in hindsight: the active source decides
              whose CONTACTS reach the picture, so promoting the live node discarded the
              recorded scene — 8 contacts, 12 AIS claims and an injected fault plan
              producing 3 SPOOF / 2 DARK / 2 UNKNOWN / 1 MATCH — and replaced it with
              whatever a background subtractor finds in a 30 s clip: two blobs, no
              identities, therefore two DARK. The picture did not break; it was replaced
              with a much poorer one, which is worse because it still looked plausible.
  AND THE BOXES — with RECORDED restored the second symptom becomes the same story from
              the other end. The recorded scene is ONE TIME INSTANT: its bboxes are
              static by construction. The clip's hulls drifted. Boxes that do not move
              over hulls that do is exactly what that mismatch looks like on a screen.

  THE FIX, IN TWO PARTS
    1. RECORDED is the default again, as it was before patch 7 and for the reason
       source_switch.py already gave: it is the only source that cannot fail. `--live`
       promotes the node when you actually want the live path.
    2. make_scene_video.py grows two modes. `--for recorded` (the new default) draws
       every hull STATIC, at the exact bbox the scene's own contact carries, so the
       console's boxes land on the hulls. `--for detector` keeps the moving,
       range-placed hulls, which is what the live node needs and what nothing else can
       use. One video cannot serve both, and pretending otherwise is what produced a
       stagnant box over a moving ship.

  A STATIC PICTURE NEEDS A LIVENESS CUE, and this is not decoration. server.py refuses
  to serve a frozen frame because a still on a watch screen is indistinguishable from a
  live view of calm water. A deliberately static rendering reintroduces exactly that
  ambiguity, so `recorded` mode burns a frame counter and elapsed time into the corner:
  if the number is not moving, the stream is not running, and that is readable from
  across a room.
"""
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


# ---------------------------------------------------------------------- main.py
patch("03_src/main.py", [
('''        # THE AUTHORITY FOLLOWS THE FLAG, and the reason is honesty rather than
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
                        else "RECORDED"),''',

 '''        # RECORDED UNLESS ASKED OTHERWISE. This reverses patch 7, which made --video
        # promote the live node automatically.
        #
        # WHY THAT WAS WRONG: the active source decides whose CONTACTS reach the
        # picture, not which imagery is shown. Promoting the node discarded the
        # recorded scene -- 8 contacts, 12 AIS claims, an injected fault plan producing
        # 3 SPOOF / 2 DARK / 2 UNKNOWN / 1 MATCH -- and replaced it with what a
        # background subtractor finds in a short clip: two blobs, no identities, two
        # DARK verdicts. Nothing errored. The picture was simply replaced by a much
        # poorer one that still looked plausible, which is the worst failure shape there
        # is.
        #
        # So the default is RECORDED again, for source_switch.py's original reason: it
        # is the only source that cannot fail. --live promotes the node when the live
        # path is actually what you want to show, and the operator can switch at any
        # time from the console.
        initial_source=("MAC_CAMERA" if (args.live and args.camera is not None)
                        else "VIDEO_STREAM" if (args.live and args.video is not None)
                        else "RECORDED"),'''),

('''    p.add_argument("--node-id", default="sensor-01",
                   help="provenance. Appears on every contact and in the case file.")''',
 '''    p.add_argument("--node-id", default="sensor-01",
                   help="provenance. Appears on every contact and in the case file.")
    p.add_argument("--live", action="store_true",
                   help="PROMOTE THE LIVE SENSOR at startup instead of the recorded "
                        "scene. Off by default: the recorded scene carries the full "
                        "ranked picture with its injected faults, and a blob detector "
                        "on a short clip does not. The console can switch either way "
                        "at any time; this only chooses what is live when it opens.")'''),
])

# --------------------------------------------------------------------- server.py
patch("03_src/server.py", [
('''        if backend.source.active == "RECORDED":
            return ("UNRELATED IMAGERY: the contacts on screen come from the RECORDED "
                    "scene, not from this video. The boxes do NOT belong to the frame "
                    "beneath them. Switch the source to VIDEO_STREAM to make the "
                    "picture one observation.")''',

 '''        if backend.source.active == "RECORDED":
            # ...unless the imagery IS this scene's own rendering. make_scene_video.py
            # writes scene.mp4 INTO the scene directory and draws every hull at the
            # exact bbox the scene's contact carries, so the boxes do belong to the
            # frame beneath them. Checked by path rather than by a flag: a flag can be
            # passed for a video that is nothing of the kind, and this claim is one the
            # operator will trust.
            try:
                own = (video_source is not None
                       and isinstance(video_source, str)
                       and Path(video_source).resolve().parent == scene_dir.resolve())
            except OSError:
                own = False
            if own:
                return ("scene rendering: this imagery was rendered from the very "
                        "contacts drawn over it. Synthetic — no camera observed it.")
            return ("UNRELATED IMAGERY: the contacts on screen come from the RECORDED "
                    "scene, not from this video. The boxes do NOT belong to the frame "
                    "beneath them. Switch the source to VIDEO_STREAM to make the "
                    "picture one observation.")'''),
])
print("PART 10a done — RECORDED is the default again.")
