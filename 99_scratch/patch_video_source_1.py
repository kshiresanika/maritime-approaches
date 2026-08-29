#!/usr/bin/env python3
"""
99_scratch/patch_video_source_1.py — PART 1 of the Pi retirement.

WHAT CHANGED IN THE WORLD (2026-08-29): the Raspberry Pi + camera module is out of the
rig. The electro-optical source is now a video THIS MACHINE decodes — a file on disk or
a network stream.

WHAT THIS PART DOES: the VOCABULARY only. It adds the `video_stream` sensor kind, makes
it a selectable source, retires EDGE_PI from the selectable set without deleting it from
the contract, and fixes the undeclared-node default that would otherwise make every
undeclared node permanently unpromotable. No I/O, no streaming, no new routes — those
are parts 2-4. Split this way so that if a later part has to be reverted, the vocabulary
does not have to be reverted with it.

Every replacement asserts its anchor. A patch that silently matches nothing is worse
than one that crashes: it reports success and changes nothing.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def patch(rel: str, pairs: list[tuple[str, str]]) -> None:
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    for old, new in pairs:
        n = src.count(old)
        if n != 1:
            raise SystemExit(f"ANCHOR FAILED in {rel}: matched {n} times, expected 1\n"
                             f"---\n{old[:200]}\n---")
        src = src.replace(old, new)
    p.write_text(src, encoding="utf-8")
    print(f"  patched {rel} ({len(pairs)} edit{'s' if len(pairs) != 1 else ''})")


# ---------------------------------------------------------------- contracts.py
patch("03_src/contracts.py", [(
'''SensorKind = Literal[
    "edge_pi",     # Raspberry Pi at the water's edge, classical CV, NO AIS access
    "mac_camera",  # the MacBook camera — the T2 live path
    "file",        # a recorded scene replayed from disk — the T1 guaranteed path
]''',
'''SensorKind = Literal[
    # RETIRED 2026-08-29. The Raspberry Pi node is no longer part of the rig; the EO
    # source is now a video this machine decodes. THE MEMBER IS KEPT, NOT DELETED,
    # because operator_audit.jsonl and every evidence record written before today
    # carries this value. A Literal that can no longer parse its own audit trail turns
    # a historical case file into a validation error, which is precisely the opposite
    # of what criterion 4 asks of us. Nothing emits it any more: source_switch.py no
    # longer offers it as a selectable source.
    "edge_pi",
    "mac_camera",    # a lens on THIS machine — the live-lens path
    "video_stream",  # imagery this machine DECODES and detects on, now: a video file
                     # on disk, or a network stream (HTTP/HLS/RTSP). This is the EO
                     # source that replaced the Pi.
    "file",          # a recorded SCENE replayed from disk — contacts already computed,
                     # not imagery being detected on now. The T1 guaranteed path.
                     #
                     # 'file' vs 'video_stream' is the difference between REPLAYING a
                     # finished observation and MAKING one. They are kept apart on
                     # purpose: collapsing them would let a projection be presented on
                     # a watch screen as an observation, which is the one substitution
                     # this whole tool exists to prevent.
]'''
)])

# ------------------------------------------------------------- source_switch.py
patch("03_src/source_switch.py", [
    # -- the module docstring: it currently describes a rig that no longer exists.
    ('''A source is a NODE, not a new concept. `RECORDED` is the `scene-file` node server.py
already registers at startup; `EDGE_PI` is the Pi; `MAC_CAMERA` is the same
`edge/sensor_node.py` running on the MacBook with `--backend cv2 --kind mac_camera`.
Nothing new has to exist for a source to exist, which is why this is a thirty-minute
change and not a subsystem.''',
     '''A source is a NODE, not a new concept. `RECORDED` is the `scene-file` node server.py
already registers at startup; `VIDEO_STREAM` is 04_demo/pi_sensor.py decoding a video
file or a network stream on this machine; `MAC_CAMERA` is the same node with a lens
instead of a file. Nothing new has to exist for a source to exist, which is why this is
a thirty-minute change and not a subsystem.

UPDATED 2026-08-29 — EDGE_PI IS RETIRED. The Raspberry Pi left the rig. Everything below
about failover still holds word for word; only the name of the source that can die has
changed, and that is the point of having had the switch at all.'''),

    ('''LABEL_TO_KIND: dict[str, SensorKind] = {
    "EDGE_PI": "edge_pi",
    "MAC_CAMERA": "mac_camera",
    "RECORDED": "file",
}
KIND_TO_LABEL: dict[str, str] = {v: k for k, v in LABEL_TO_KIND.items()}''',
     '''LABEL_TO_KIND: dict[str, SensorKind] = {
    "VIDEO_STREAM": "video_stream",
    "MAC_CAMERA": "mac_camera",
    "RECORDED": "file",
}
KIND_TO_LABEL: dict[str, str] = {v: k for k, v in LABEL_TO_KIND.items()}

# RETIRED 2026-08-29 — the Raspberry Pi is out of the rig.
#
# WHY THE LABEL IS REMOVED FROM THE SELECTABLE SET RATHER THAN LEFT IN PLACE:
# status() publishes LABEL_TO_KIND as `available_sources`, and the console renders one
# button per member. Leaving EDGE_PI there offers the operator a source that can never
# go live. A button that does nothing is exactly the class of confusion this tool exists
# to remove, and a judge who presses it gets a dead picture and a good question.
#
# WHY IT IS RECOGNISED RATHER THAN SIMPLY DELETED: a node that has not been told may
# still post kind 'edge_pi'. normalise() keeps recognising the spelling so that it can
# answer "that source is retired" instead of "unknown source" — a ten-second diagnosis
# instead of a ten-minute one at 03:00. accepts() catches the ValueError and returns
# False, so a Pi that reappears is still never silently promoted into the picture.
RETIRED_LABELS: dict[str, SensorKind] = {"EDGE_PI": "edge_pi"}'''),

    ('''    if s.lower() in KIND_TO_LABEL:
        return KIND_TO_LABEL[s.lower()]
    raise ValueError(''',
     '''    if s.lower() in KIND_TO_LABEL:
        return KIND_TO_LABEL[s.lower()]
    if s.upper() in RETIRED_LABELS or s.lower() in RETIRED_LABELS.values():
        raise ValueError(
            f"source {source!r} is RETIRED. The Raspberry Pi node left the rig on "
            f"2026-08-29; the EO source is now VIDEO_STREAM — a video file or network "
            f"stream decoded on this machine. Valid: {sorted(LABEL_TO_KIND)}")
    raise ValueError('''),
])

# -------------------------------------------------------------------- server.py
patch("03_src/server.py", [
    ('''        kind: SensorKind = meta.get("kind") or "edge_pi"
        if kind not in ("edge_pi", "mac_camera", "file"):''',
     '''        # DEFAULT CHANGED 2026-08-29, and it is load-bearing rather than cosmetic.
        # A node that does not declare its kind used to be ASSUMED to be the Pi. With
        # EDGE_PI retired from the selectable sources, that assumption would make an
        # undeclared node permanently unpromotable: it POSTs 200 OK for ever, both ends
        # look healthy, and nothing it sees ever reaches the picture. The default is now
        # the kind the rig actually runs.
        kind: SensorKind = meta.get("kind") or "video_stream"
        if kind not in ("edge_pi", "mac_camera", "video_stream", "file"):'''),

    ('''        Switch the live source. EDGE_PI | MAC_CAMERA | RECORDED.''',
     '''        Switch the live source. VIDEO_STREAM | MAC_CAMERA | RECORDED.'''),

    ('''            raise HTTPException(400, "body needs {'source': 'EDGE_PI|MAC_CAMERA|RECORDED'}")''',
     '''            raise HTTPException(
                400, "body needs {'source': 'VIDEO_STREAM|MAC_CAMERA|RECORDED'}")'''),
])

# ----------------------------------------------------------- 03_src/web/index.html
patch("03_src/web/index.html", [
    ('''const SOURCE_WORDS = { edge_pi: "EDGE PI", mac_camera: "MAC CAM", file: "RECORDED" };''',
     '''/* edge_pi is RETIRED (2026-08-29) but stays in this map: records written before the
   Pi left the rig still carry the kind, and an unlabelled badge on a historical record
   reads as a bug in the console rather than as history. */
const SOURCE_WORDS = { video_stream: "VIDEO", mac_camera: "MAC CAM",
                       file: "RECORDED", edge_pi: "EDGE PI (RETIRED)" };'''),
])

print("PART 1 done — vocabulary only. Nothing streams yet; that is parts 2-4.")
