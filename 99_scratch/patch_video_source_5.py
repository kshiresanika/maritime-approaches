#!/usr/bin/env python3
"""
99_scratch/patch_video_source_5.py — PART 3: the sensor node reads a video instead of
a Pi camera, and publishes the frames it detected on.
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


patch("04_demo/pi_sensor.py", [

# ------------------------------------------------------------- 1. the header
('''"""
pi_sensor.py — the coastal sensor node. Classical CV. NO NEURAL NETWORK.

WHAT THIS IS
A Raspberry Pi with a camera module, standing in for a camera at the water's edge. It
detects hulls, computes a true bearing and a range for each, and POSTs them to the shore
station as EoContact-shaped JSON. It is the physical embodiment of the claimed/observed
wall: THIS PROCESS HAS NO ACCESS TO AIS AT ALL. It has never been told an MMSI, has
nowhere to put one, and could not leak an identity into an observation if it tried.
When someone asks how you know the detector is not peeking at the AIS, you point at the
Pi and say it has no AIS connection.''',

'''"""
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
"this is our sensor watching the Elbe".''' ),

# ---------------------------------------------------------- 2. usage line at the end
('''    python3 04_demo/pi_sensor.py --post http://<mac-ip>:8000/api/contacts \\
        --pose 04_demo/camera_pose_TABLETOP.json --scale 20
"""''',
 '''    # a clip on disk, publishing the frames it detects on (the demo path)
    python3 04_demo/pi_sensor.py --source 02_data/clips/approach.mp4 --loop \\
        --pose 04_demo/camera_pose_TABLETOP.json --scale 20 \\
        --post http://127.0.0.1:8000/api/contacts \\
        --publish-frames http://127.0.0.1:8000/ingest/frame

    # a network stream (HLS/RTSP/HTTP) instead — same command, different --source
    python3 04_demo/pi_sensor.py --source "https://example.org/live/stream.m3u8" ...

    # a lens on this machine
    python3 04_demo/pi_sensor.py --source 0 ...
"""'''),

# ------------------------------------------------------------------- 3. post()
('''def post(url: str, contacts: list[dict]) -> None:
    body = json.dumps({"contacts": contacts}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})''',
 '''def post(url: str, contacts: list[dict], *, node_id: str = "sensor-01",
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
                                 headers={"Content-Type": "application/json"})'''),

# ---------------------------------------------------- 4. publish_frame() helper
('''def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Classical EO sensor node. No AI.")''',
 '''def publish_frame(url: str, frame, *, node_id: str, frame_ref: str,
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
    p = argparse.ArgumentParser(description="Classical EO sensor node. No AI.")'''),

# ------------------------------------------------------------------- 5. CLI flags
('''    p.add_argument("--source", default="0", help="camera index, or a video file path")''',
 '''    p.add_argument("--source", "--camera", dest="source", default="0",
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
                        "camera or a network stream (already arriving at their rate).")'''),

])
print("PART 3a done — node header, post(), publish_frame(), CLI.")
