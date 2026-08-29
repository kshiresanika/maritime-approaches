#!/usr/bin/env python3
"""
99_scratch/lane_video_e2e.py — the console's video pane, end to end, through a REAL server.

lane_video_check.py tests the generators in isolation. This runs uvicorn on a port and
asks the questions an operator asks:

  1. --video configured, no node   -> /health says "decoding ...", /stream serves JPEGs
  2. a node publishes a frame      -> /health says "relayed from node ...", frame-synchronised
  3. nothing configured            -> /health says exactly "none configured", /stream 503
  4. RECORDED authority + imagery  -> video_sync warns UNRELATED IMAGERY

Check 3 matters more than it looks: index.html compares against the literal string
"none configured" to decide whether a 503 is a configuration statement or a fault.
Check 4 is the honesty guard — the case that lies quietly.

WHY uvicorn IN A THREAD AND NOT TestClient: /stream is an unbounded generator by design.
TestClient closes a streaming response by draining it, so it waits for a stream that is
never meant to end. A real socket can simply be closed, which is also what a browser
does when the operator navigates away — so this tests the actual behaviour rather than
a testing-client artefact.
"""
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "03_src"), str(ROOT / "04_demo")]

import urllib.request                            # noqa: E402
import json as _json                             # noqa: E402
import uvicorn                                   # noqa: E402
from server import create_app                    # noqa: E402

SCENE = ROOT / "04_demo/out/scene01"
VIDEO = SCENE / "scene.mp4"
FAILED = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILED.append(label)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


class Server:
    """uvicorn in a daemon thread. Started per case so each gets a clean relay."""

    def __init__(self, **kw):
        self.port = free_port()
        app = create_app(scene_dir=SCENE, web_dir=ROOT / "03_src/web",
                         audit_path=Path("/tmp/e2e_audit.jsonl"),
                         mjpeg_url=None, camera_index=None,
                         allow_real_identities=False, node_stale_after_s=8.0, **kw)
        cfg = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        self.srv = uvicorn.Server(cfg)
        threading.Thread(target=self.srv.run, daemon=True).start()
        for _ in range(120):
            try:
                self.get("/health"); return
            except Exception:
                time.sleep(0.1)
        raise SystemExit("server did not come up")

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        with urllib.request.urlopen(self.url(path), timeout=4.0) as r:
            return r.status, r.read()

    def get_json(self, path):
        return _json.loads(self.get(path)[1])

    def post_bytes(self, path, data, ctype="image/jpeg"):
        req = urllib.request.Request(self.url(path), data=data,
                                     headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=4.0) as r:
                return r.status, _json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.read()[:200]

    def count_jpegs(self, want=3, budget_s=12.0):
        """Read the start of /stream, count complete JPEGs, then close the socket."""
        buf, deadline = b"", time.monotonic() + budget_s
        try:
            r = urllib.request.urlopen(self.url("/stream"), timeout=6.0)
        except urllib.error.HTTPError as e:
            return e.code, 0
        try:
            while time.monotonic() < deadline:
                chunk = r.read(65536)
                if not chunk:
                    break
                buf += chunk
                if buf.count(b"\xff\xd8\xff") >= want:
                    break
        finally:
            r.close()
        return 200, buf.count(b"\xff\xd8\xff")

    def stop(self):
        self.srv.should_exit = True
        time.sleep(0.3)


print("\n=== 1. --video configured, no node publishing ===")
s = Server(video_source=str(VIDEO), initial_source="VIDEO_STREAM")
h = s.get_json("/health")
v = h["source"]["video"]
check("/health reports the video is being decoded", v.startswith("decoding"), v)
check("nothing relayed yet", h["source"]["frame_relay"]["frames_published"] == 0)
st, n = s.count_jpegs()
check("/stream returns 200", st == 200, str(st))
check("...and delivers real JPEG frames", n >= 3, f"{n} JPEGs")

print("\n=== 2. a node starts publishing mid-run ===")
import cv2                                                    # noqa: E402
cap = cv2.VideoCapture(str(VIDEO)); ok, fr = cap.read(); cap.release()
jpeg = cv2.imencode(".jpg", fr)[1].tobytes()
code, body = s.post_bytes("/ingest/frame?node_id=sensor-01&frame_ref=file://x", jpeg)
check("POST /ingest/frame accepted", code == 200, str(code))
check("...and reports a frame sequence", isinstance(body, dict) and body.get("frame_seq") == 1,
      str(body)[:80])
h = s.get_json("/health")
check("/health now says the picture is RELAYED",
      h["source"]["video"].startswith("relayed from node sensor-01"), h["source"]["video"])
check("...and calls it frame-synchronised",
      h["source"]["video_sync"].startswith("frame-synchronised"),
      h["source"]["video_sync"][:50])
code, _ = s.post_bytes("/ingest/frame", b"not a jpeg")
check("a non-JPEG body is refused with 400", code == 400, str(code))
s.stop()

print("\n=== 3. nothing configured — the exact string the console depends on ===")
s = Server(video_source=None)
h = s.get_json("/health")
check('/health says exactly "none configured"',
      h["source"]["video"] == "none configured", repr(h["source"]["video"]))
check("video_sync says there is no imagery",
      h["source"]["video_sync"] == "no imagery", h["source"]["video_sync"])
st, _ = s.count_jpegs(want=1, budget_s=3.0)
check("/stream returns 503", st == 503, str(st))
s.stop()

print("\n=== 4. THE QUIET LIE: imagery present, RECORDED is the authority ===")
# Two cases, and the distinction is the whole point. The console draws the RECORDED
# scene's boxes over whatever imagery /stream serves. When that imagery is the scene's
# OWN rendering the boxes do belong to it; when it is any other video they do not, and
# only one of those may be presented without a warning.
s = Server(video_source=str(VIDEO), initial_source="RECORDED")
sync = s.get_json("/health")["source"]["video_sync"]
check("the scene's own rendering is NOT called unrelated",
      sync.startswith("scene rendering"), sync[:70])
check("...and says plainly that no camera observed it", "no camera" in sync.lower())
s.stop()

import shutil
OUTSIDE = Path("/tmp/lane_e2e_outside.mp4")
shutil.copy(VIDEO, OUTSIDE)
s = Server(video_source=str(OUTSIDE), initial_source="RECORDED")
sync = s.get_json("/health")["source"]["video_sync"]
check("a video from ANYWHERE ELSE warns UNRELATED IMAGERY",
      sync.startswith("UNRELATED IMAGERY"), sync[:70])
check("...and names the fix", "VIDEO_STREAM" in sync)
s.stop()
OUTSIDE.unlink(missing_ok=True)

print("\n" + "=" * 70)
print("ALL CHECKS PASSED" if not FAILED else f"FAILED: {FAILED}")
print("=" * 70)
sys.exit(1 if FAILED else 0)
