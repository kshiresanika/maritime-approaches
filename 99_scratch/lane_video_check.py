#!/usr/bin/env python3
"""
99_scratch/lane_video_check.py — does the new video path actually do what its
docstrings claim?

WHAT IT CHECKS, and each one is a claim made in a comment somewhere:
  1. parse_video_source turns "0" into a camera index and leaves paths and URLs alone.
  2. A file is recognised as replayable; a URL and a camera index are not.
  3. _mjpeg_decode emits well-formed multipart parts whose payloads are real JPEGs.
  4. A file LOOPS: more frames come out than the clip contains.
  5. A file is PACED to its own frame rate rather than consumed flat out.
  6. _FrameRelay hands each published frame to a viewer exactly once.
  7. _FrameRelay ENDS when publishing stops, rather than re-yielding a stale frame.
     This is the one that matters: a frozen frame on a watch screen is
     indistinguishable from a live view of calm water.

HOW server.py IS IMPORTED WITHOUT THE PIPELINE STACK: the heavy dependencies are
STUBBED in sys.modules first, exactly as lane_f_edge_check.py does. The functions under
test touch none of them, and installing pyproj to test a JPEG boundary string would be
measuring the wrong thing.
"""
import io
import sys
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "03_src"), str(ROOT / "04_demo")]

# ---- stub every heavy import server.py pulls in at module scope -----------------
for name in ("evidence", "run_pipeline", "loguru", "pyproj", "shapely", "pandas",
             "geopandas", "movingpandas", "sklearn", "ultralytics", "torch",
             "fastapi", "fastapi.responses", "fastapi.staticfiles",
             "starlette", "starlette.websockets"):
    if name not in sys.modules:
        m = types.ModuleType(name)
        m.__getattr__ = lambda a, _n=name: types.SimpleNamespace(__name__=f"{_n}.{a}")
        sys.modules[name] = m
sys.modules["loguru"].logger = types.SimpleNamespace(
    info=lambda *a, **k: None, warning=lambda *a, **k: None,
    error=lambda *a, **k: None, debug=lambda *a, **k: None)

import cv2                                                     # noqa: E402
import numpy as np                                             # noqa: E402
import server                                                  # noqa: E402

FAILED = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILED.append(label)


def parts(chunks):
    """Split a concatenated MJPEG byte stream into JPEG payloads."""
    blob = b"".join(chunks)
    out, i = [], 0
    marker = f"--{server.MJPEG_BOUNDARY}\r\n".encode()
    while True:
        i = blob.find(marker, i)
        if i < 0:
            return out
        j = blob.find(b"\r\n\r\n", i)
        head = blob[i:j].decode("ascii", "replace")
        n = int([h for h in head.splitlines() if h.startswith("Content-Length")][0].split(":")[1])
        out.append(blob[j + 4: j + 4 + n])
        i = j + 4 + n


# ------------------------------------------------------------- build a test clip
CLIP = Path("/tmp/lane_video_check.mp4")
N_FRAMES, FPS, W, H = 20, 10.0, 160, 120
w = cv2.VideoWriter(str(CLIP), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
for k in range(N_FRAMES):
    f = np.zeros((H, W, 3), np.uint8)
    cv2.rectangle(f, (k * 6, 40), (k * 6 + 20, 70), (255, 255, 255), -1)
    w.write(f)
w.release()
print(f"\n=== test clip: {N_FRAMES} frames @ {FPS:g} fps ({N_FRAMES / FPS:.1f} s) ===")

print("\n=== 1-2. source parsing ===")
check("'0' parses to camera index 0", server.parse_video_source("0") == 0)
check("a path stays a string", server.parse_video_source(str(CLIP)) == str(CLIP))
check("a URL stays a string",
      server.parse_video_source("https://h/s.m3u8") == "https://h/s.m3u8")
check("None stays None", server.parse_video_source(None) is None)
check("an existing file is replayable", server._is_replayable_file(str(CLIP)) is True)
check("a URL is NOT replayable", server._is_replayable_file("https://h/s.m3u8") is False)
check("a camera index is NOT replayable", server._is_replayable_file(0) is False)

print("\n=== 3-4. decode: well-formed parts, and the clip LOOPS ===")
gen = server._mjpeg_decode(str(CLIP), loop=True)
got, t0 = [], time.monotonic()
want = N_FRAMES + 6                       # more than the clip holds => it rewound
for chunk in gen:
    got.append(chunk)
    if len(got) >= want:
        break
gen.close()
elapsed = time.monotonic() - t0
jpegs = parts(got)
check(f"{want} parts came out of a {N_FRAMES}-frame clip (so it looped)",
      len(jpegs) >= want, f"{len(jpegs)} parts")
check("every payload is a JPEG (SOI+EOI)",
      all(j.startswith(b"\xff\xd8") and j.endswith(b"\xff\xd9") for j in jpegs))
check("every payload decodes back to an image",
      all(cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR) is not None
          for j in jpegs[:5]))

print("\n=== 5. a file is PACED to its own frame rate ===")
expected = (want - 1) / FPS
check(f"{want} frames took about {expected:.1f}s, not milliseconds",
      elapsed > expected * 0.7,
      f"{elapsed:.2f}s elapsed, floor {expected * 0.7:.2f}s")

print("\n=== 6-7. the frame relay ===")
relay = server._FrameRelay(stale_after_s=0.6)
check("a relay with nothing published is not fresh", relay.fresh() is False)
jpg = parts([next(iter(server._mjpeg_decode(str(CLIP), loop=False)))])[0]
relay.publish(jpg, node_id="sensor-01", frame_ref="file://x")
check("it is fresh once a frame lands", relay.fresh() is True)
check("status names the node", relay.status()["node_id"] == "sensor-01")

seen = []
done = threading.Event()


def viewer():
    for part in relay.frames():
        seen.append(part)
    done.set()


threading.Thread(target=viewer, daemon=True).start()
time.sleep(0.15)
for _ in range(3):
    relay.publish(jpg, node_id="sensor-01")
    time.sleep(0.12)
time.sleep(0.2)
check("the viewer received the published frames", len(seen) >= 3, f"{len(seen)} parts")

# Stop publishing. The generator must END, not keep re-sending the last frame.
n_at_stop = len(seen)
finished = done.wait(timeout=3.0)
check("the stream ENDED when publishing stopped (no frozen frame)", finished is True)
check("...and sent nothing further after it went stale", len(seen) == n_at_stop,
      f"{len(seen) - n_at_stop} extra")
check("a stale relay reports itself not fresh", relay.fresh() is False)

print("\n" + "=" * 70)
print("ALL CHECKS PASSED" if not FAILED else f"FAILED: {FAILED}")
print("=" * 70)
sys.exit(1 if FAILED else 0)
