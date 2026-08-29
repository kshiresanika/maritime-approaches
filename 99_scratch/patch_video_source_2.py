#!/usr/bin/env python3
"""
99_scratch/patch_video_source_2.py — PART 2 of the Pi retirement: THE VIDEO ITSELF.

Two capabilities, and the second one is a correctness fix rather than a feature:

  1. /stream can DECODE any source cv2 understands — a camera index, a video file on
     disk, or a network stream (HTTP/HLS/RTSP). One code path, because
     cv2.VideoCapture already accepts all three and writing three is how they drift.
  2. /stream can RELAY the exact JPEG the sensor node computed its contacts from.
     Without this there are two decoders on one video and the console draws boxes from
     frame N over frame M. See _FrameRelay's docstring for why that is not cosmetic.

Anchors are asserted. A patch that matches nothing must crash, not congratulate itself.
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
                             f"---\n{old[:240]}\n---")
        src = src.replace(old, new)
    p.write_text(src, encoding="utf-8")
    print(f"  patched {rel} ({len(pairs)} edits)")


patch("03_src/server.py", [

# ---------------------------------------------------------------- 1. imports
("""import sys
import time""",
 """import sys
import threading
import time"""),

# ------------------------------------- 2. the whole video section, replaced
("""def _mjpeg_local_camera(index: int, *, quality: int = 80) -> Iterator[bytes]:
    \"\"\"
    Encode the local camera as MJPEG.

    cv2 is imported INSIDE the function, deliberately. A top-level import would make
    the whole shore station refuse to start on any machine without opencv — including
    a teammate's laptop that only needs the console — for a feature that is optional.
    The same lazy-import discipline lane A enforces on the geo stack, for the same
    reason: reading the picture must not require the ability to produce it.
    \"\"\"
    import cv2  # noqa: PLC0415 — see docstring

    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"[server] /stream: camera {index} would not open", file=sys.stderr)
        return
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                continue
            payload = buf.tobytes()
            yield (
                f"--{MJPEG_BOUNDARY}\\r\\n"
                f"Content-Type: image/jpeg\\r\\n"
                f"Content-Length: {len(payload)}\\r\\n\\r\\n"
            ).encode("ascii") + payload + b"\\r\\n"
    finally:
        cap.release()""",

 '''def _mjpeg_part(payload: bytes) -> bytes:
    """One multipart/x-mixed-replace part. One definition, so the two producers below
    cannot disagree about the wire format."""
    return (
        f"--{MJPEG_BOUNDARY}\\r\\n"
        f"Content-Type: image/jpeg\\r\\n"
        f"Content-Length: {len(payload)}\\r\\n\\r\\n"
    ).encode("ascii") + payload + b"\\r\\n"


def parse_video_source(spec: "str | int | None") -> "str | int | None":
    """
    '0' -> 0 (camera index).  Anything else stays a string: a path or a URL.

    ONE PARSER, called by every entry point, because the alternative is main.py and
    server.py each deciding what "0" means and disagreeing on the one run where it
    matters. cv2.VideoCapture takes an int OR a str and treats a file path and a URL
    identically, which is why a camera, a clip and an internet stream need one code
    path here and not three.
    """
    if spec is None:
        return None
    if isinstance(spec, int):
        return spec
    s = str(spec).strip()
    if not s:
        return None
    return int(s) if s.isdigit() else s


def _is_replayable_file(source: "str | int") -> bool:
    """A local file can be paced and looped. A camera or a network stream cannot be
    either: there is no 'again' and no native frame rate to honour."""
    return isinstance(source, str) and Path(source).expanduser().exists()


def _mjpeg_decode(source: "str | int", *, quality: int = 80,
                  loop: bool = True, live_timeout_s: float = 10.0) -> Iterator[bytes]:
    """
    Decode ANY cv2 source and serve it as MJPEG: camera index, file path, or URL.

    cv2 is imported INSIDE the function, deliberately. A top-level import would make
    the whole shore station refuse to start on any machine without opencv — including
    a teammate's laptop that only needs the console — for a feature that is optional.
    The same lazy-import discipline lane A enforces on the geo stack, for the same
    reason: reading the picture must not require the ability to produce it.

    THREE BEHAVIOURS THAT ARE NOT OBVIOUS AND ARE EACH THERE FOR A MEASURED REASON.

    PACING (files only). A file read as fast as the loop can turn plays a 30 s clip in
    about two seconds and then ends. The browser shows a smear and then a dead frame.
    So a file is paced to its own CAP_PROP_FPS. A camera and a network stream are NOT
    paced: they already arrive at their own rate, and sleeping on top of that only adds
    latency to a live picture.

    LOOPING (files only). The demo runs longer than the clip. At EOF the position is
    rewound; if the container refuses to seek, the capture is reopened.

    ENDING RATHER THAN FREEZING (live sources). If a live source stops delivering, this
    generator RETURNS after live_timeout_s and the console reports the stream stopped.
    It never re-yields the last frame. An MJPEG <img> keeps painting the final part it
    received, so a producer that quietly stops leaves a still image on a watch screen —
    and a still of calm water is indistinguishable from a live view of calm water. That
    confusion is the whole reason SensorNode.last_seen exists; the video pane is not
    allowed to reintroduce it.
    """
    import cv2  # noqa: PLC0415 — see docstring

    replayable = _is_replayable_file(source)
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"[server] /stream: source {source!r} would not open. A file path must "
              f"exist; a URL must be one ffmpeg can read (http/https/rtsp, and an "
              f"HLS .m3u8 counts).", file=sys.stderr)
        return

    # Ask for the shallowest buffer the backend will give us. On a live source a deep
    # buffer means the operator is watching the past, and a bearing computed from a
    # frame thirty seconds old is a wrong bearing, not a late one.
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:                                      # noqa: BLE001
        pass

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    period = (1.0 / fps) if (replayable and 0.0 < fps <= 120.0) else 0.0
    next_at = time.monotonic()
    last_frame_at = time.monotonic()

    try:
        while True:
            ok, frame = cap.read()

            if not ok:
                if replayable and loop:
                    # Rewind. Reopen if the container will not seek — some MP4s and
                    # most transport streams will not.
                    if not cap.set(cv2.CAP_PROP_POS_FRAMES, 0):
                        cap.release()
                        cap = cv2.VideoCapture(source)
                        if not cap.isOpened():
                            return
                    continue
                if replayable:
                    return                       # a clip that was asked not to loop
                # A live source hiccuped. Reconnect, but only for a bounded time.
                if time.monotonic() - last_frame_at > live_timeout_s:
                    print(f"[server] /stream: {source!r} delivered no frame for "
                          f"{live_timeout_s:.0f}s — ending the stream rather than "
                          f"freezing the last one.", file=sys.stderr)
                    return
                time.sleep(0.5)
                cap.release()
                cap = cv2.VideoCapture(source)
                continue

            last_frame_at = time.monotonic()
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                continue
            yield _mjpeg_part(buf.tobytes())

            if period:
                next_at += period
                slack = next_at - time.monotonic()
                if slack > 0:
                    time.sleep(slack)
                else:
                    next_at = time.monotonic()   # fell behind; do not accumulate debt
    finally:
        cap.release()


class _FrameRelay:
    """
    The latest JPEG a sensor node published, and nothing else.

    WHY THIS EXISTS, AND IT IS A CORRECTNESS FIX RATHER THAN A FEATURE.

    OBSERVED  — the console draws its detection boxes as an SVG overlay ON TOP of
                /stream (index.html, renderSensor: "boxes are the detector's output").
    CLAIMED   — that the box and the hull under it are the same observation.
    THE MISMATCH — when the server decodes the video AND the node decodes the same
                video separately, there are two decoders holding two independent
                positions in one file. The boxes come from frame N and are painted over
                frame M, and on a looping clip N and M drift apart without bound.
    WHY IT MATTERS — a box drawn over the wrong frame is a watch screen asserting a
                vessel is somewhere it is not. That is not a rendering defect; it is the
                tool making a false observation, and it would survive every functional
                test because both halves work perfectly on their own.
    THE FIX   — ONE decoder. The node that computed the contacts publishes the frame it
                computed them FROM, and the server relays that. Imagery and boxes are
                then the same observation with the same timestamp.
    WHAT WOULD CHANGE THE ANSWER — a genuinely live source (a lens, or a broadcast
                stream both ends open independently) is close enough in wall-clock time
                that the drift is sub-second. The relay is still preferred there; the
                unbounded-drift argument is specific to seekable files.

    NEVER SERVES A STALE FRAME. If the node stops publishing, frames() RETURNS and the
    console reports the stream as stopped. See _mjpeg_decode on why a frozen frame is
    worse than no frame.
    """

    def __init__(self, stale_after_s: float = 5.0) -> None:
        self.stale_after_s = stale_after_s
        self._jpeg: bytes | None = None
        self._seq = 0
        self._at = 0.0
        self._node_id: str | None = None
        self._frame_ref: str | None = None
        self._cv = threading.Condition()

    def publish(self, jpeg: bytes, *, node_id: str | None = None,
                frame_ref: str | None = None) -> int:
        with self._cv:
            self._jpeg = jpeg
            self._seq += 1
            self._at = time.monotonic()
            self._node_id = node_id
            self._frame_ref = frame_ref
            self._cv.notify_all()
            return self._seq

    def fresh(self) -> bool:
        with self._cv:
            return (self._jpeg is not None
                    and (time.monotonic() - self._at) <= self.stale_after_s)

    def status(self) -> dict[str, Any]:
        with self._cv:
            age = (time.monotonic() - self._at) if self._jpeg is not None else None
            return {
                "frames_published": self._seq,
                "node_id": self._node_id,
                "frame_ref": self._frame_ref,
                "age_s": round(age, 2) if age is not None else None,
                "fresh": self._jpeg is not None and age is not None
                         and age <= self.stale_after_s,
            }

    def frames(self) -> Iterator[bytes]:
        """Yield each newly published frame. Blocks between them, in a threadpool."""
        sent = -1
        while True:
            with self._cv:
                if self._seq == sent or self._jpeg is None:
                    # Wake at the staleness deadline even if nothing arrives, so the
                    # "publisher stopped" branch below is reachable.
                    self._cv.wait(timeout=self.stale_after_s)
                if self._jpeg is None:
                    return
                if (time.monotonic() - self._at) > self.stale_after_s:
                    print("[server] /stream: the node stopped publishing frames — "
                          "ending the stream rather than freezing the last one.",
                          file=sys.stderr)
                    return
                if self._seq == sent:
                    continue
                sent, payload = self._seq, self._jpeg
            yield _mjpeg_part(payload)'''),

# ------------------------------------------- 3. create_app signature
("""    mjpeg_url: str | None,
    camera_index: int | None,
    allow_real_identities: bool,""",
 """    mjpeg_url: str | None,
    camera_index: int | None,
    allow_real_identities: bool,
    video_source: "str | int | None" = None,
    frame_stale_after_s: float = 5.0,"""),

])
print("PART 2a done — decoder + relay in place. Routes next.")
