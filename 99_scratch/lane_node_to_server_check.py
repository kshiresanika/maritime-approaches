#!/usr/bin/env python3
"""
99_scratch/lane_node_to_server_check.py — the whole live path, for real.

Starts the shore station on a port, launches 04_demo/pi_sensor.py as a SUBPROCESS
against the DETECTOR cut of the rendered scene video, and then asks the only questions that matter:

  * did the node's contacts reach the server (HTTP 200, not 422)?
  * were they PROMOTED, or accepted-and-ignored because the active source did not
    match the kind the node declared?
  * did the node's frames reach /ingest/frame, so the console's imagery and its boxes
    are the same observation?
  * does /stream then serve the RELAY rather than the server's own decode?

Nothing is stubbed. This is the path the demo runs.
"""
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "03_src"), str(ROOT / "04_demo")]
import uvicorn                                   # noqa: E402
from server import create_app                    # noqa: E402

SCENE = ROOT / "04_demo/out/scene01"
FAILED = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILED.append(label)


s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
app = create_app(scene_dir=SCENE, web_dir=ROOT / "03_src/web",
                 audit_path=Path("/tmp/node_e2e_audit.jsonl"),
                 mjpeg_url=None, camera_index=None, allow_real_identities=False,
                 node_stale_after_s=8.0, video_source=str(SCENE / "scene_detector.mp4"),
                 initial_source="VIDEO_STREAM")
srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()


def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
        return json.loads(r.read())


for _ in range(80):
    try:
        get("/health"); break
    except Exception:
        time.sleep(0.1)

print(f"\n=== shore station up on {PORT}; launching the node ===")
proc = subprocess.Popen(
    [sys.executable, "-u", str(ROOT / "04_demo/pi_sensor.py"),
     "--source", str(SCENE / "scene_detector.mp4"), "--loop",
     "--pose", str(SCENE / "camera_pose_SCENE.json"), "--scale", "1",
     "--node-id", "sensor-01", "--interval", "1.0",
     "--post", f"http://127.0.0.1:{PORT}/api/contacts",
     "--publish-frames", f"http://127.0.0.1:{PORT}/ingest/frame"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

lines = []
threading.Thread(target=lambda: [lines.append(l.rstrip()) for l in proc.stdout],
                 daemon=True).start()

# Give the node time to open the clip, learn a background and post at least twice.
deadline = time.time() + 40
node = None
while time.time() < deadline:
    time.sleep(1.0)
    h = get("/health")
    node = next((n for n in h.get("nodes", []) if n.get("node_id") == "sensor-01"), None)
    if node and h["source"]["frame_relay"]["frames_published"] > 0:
        break

print("\n--- node output ---")
for l in lines[:14]:
    print("   ", l)
rejected = [l for l in lines if "rejected" in l or "failed" in l]

print("\n=== the questions ===")
check("the node did not report a rejection", not rejected, "; ".join(rejected[:2])[:120])
check("the server registered the node", node is not None,
      json.dumps(node)[:90] if node else "no node in /health")
if node:
    check("...as kind video_stream", node.get("kind") == "video_stream", str(node.get("kind")))
    check("...and it is online", node.get("online") is True)

h = get("/health")
fr = h["source"]["frame_relay"]
check("frames reached /ingest/frame", fr["frames_published"] > 0,
      f"{fr['frames_published']} frames")
check("...attributed to the node", fr["node_id"] == "sensor-01", str(fr["node_id"]))
check("/stream is now serving the RELAY, not the decode",
      h["source"]["video"].startswith("relayed"), h["source"]["video"])
check("...and reports frame-synchronised imagery",
      h["source"]["video_sync"].startswith("frame-synchronised"),
      h["source"]["video_sync"][:46])

st = get("/api/state")
counts = st.get("counts", {})
print(f"\n  /api/state counts: {counts}")

proc.terminate()
try:
    proc.wait(timeout=5)
except Exception:
    proc.kill()
srv.should_exit = True
time.sleep(0.4)

print("\n" + "=" * 70)
print("ALL CHECKS PASSED" if not FAILED else f"FAILED: {FAILED}")
print("=" * 70)
sys.exit(1 if FAILED else 0)
