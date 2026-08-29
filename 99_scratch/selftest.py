#!/usr/bin/env python3
"""
99_scratch/selftest.py — ONE COMMAND THAT ANSWERS ALL THREE SYMPTOMS.

    python3 99_scratch/selftest.py

Starts the whole rig exactly as the demo does, drives it for ~40 s, and prints a
self-interpreting PASS/FAIL for each of:

    A. is a SENSOR NODE alive and posting?
    B. do the DETECTION BOXES move between samples?
    C. does EXPORT EVIDENCE return a document?

WHY THIS EXISTS. Every symptom reported so far — "boxes static", "boxes misaligned",
"export not working", "no second camera" — has had the same shape: something upstream
failed silently and the console showed a plausible-looking picture anyway. A screenshot
cannot distinguish "the pipeline is wrong" from "the node never started", and neither
can reading the code. This runs it and looks.

It captures the NODE'S OWN STDERR, which is the single most important output in the
system and the one nobody ever sees: a node that refuses to start (unfilled pose,
missing file, bad flag) prints its reason and dies into a pipe.

Nothing here is stubbed and nothing is inferred. Every line below is measured from a
real server on a real port.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENE = ROOT / "04_demo/out/scene01"
PORT = int(os.environ.get("SELFTEST_PORT", "8077"))     # NOT 8000: never fight the demo
BASE = f"http://127.0.0.1:{PORT}"

FAIL: list[str] = []
def ok(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAIL.append(name)
    return cond

def hdr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)

def get(path, timeout=4.0):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:                                    # noqa: BLE001
        return None, str(e).encode()

def getj(path):
    st, body = get(path)
    if st != 200:
        return None
    try:
        return json.loads(body)
    except Exception:                                         # noqa: BLE001
        return None


hdr("0. PRECONDITIONS")
cuts = {}
for name in ("scene.mp4", "scene_detector.mp4"):
    f = SCENE / name
    ok(f"{name} exists", f.exists(), str(f) if not f.exists() else f"{f.stat().st_size} B")
    cuts[name] = f
pose = SCENE / "camera_pose_SCENE.json"
ok("camera_pose_SCENE.json exists", pose.exists())
if pose.exists():
    pj = json.loads(pose.read_text())
    ok("pose hfov_deg is non-zero", float(pj.get("hfov_deg", 0)) > 0,
       f"hfov={pj.get('hfov_deg')}  rendered_for={pj.get('rendered_for')}")

# WHICH CUT IS WHICH, measured rather than assumed. The two files are easy to swap and
# a detector cut sitting in scene.mp4 is invisible until every box is in the wrong place.
try:
    import cv2, numpy as np
    for name, f in cuts.items():
        if not f.exists():
            continue
        cap = cv2.VideoCapture(str(f)); prev = None; d = []
        n = 0
        while n < 120:
            r, fr = cap.read()
            if not r:
                break
            n += 1
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            if prev is not None:
                d.append(float(cv2.absdiff(g, prev).mean()))
            prev = g
        cap.release()
        mean = float(np.mean(d)) if d else 0.0
        kind = "DETECTOR cut (hulls drift)" if mean > 0.03 else "RECORDED cut (static)"
        want = "RECORDED cut (static)" if name == "scene.mp4" else "DETECTOR cut (hulls drift)"
        ok(f"{name} contains the {want.split()[0]} cut", kind == want,
           f"inter-frame diff {mean:.4f} -> {kind}")
except ImportError:
    print("  (cv2 unavailable — skipping the cut identification)")

st, _ = get("/health", timeout=1.5)
ok(f"port {PORT} is free", st is None, "something is already answering" if st else "free")

hdr("1. START THE RIG  (main.py --live, the demo command)")
cmd = [sys.executable, "-u", str(ROOT / "03_src/main.py"),
       "--video", str(SCENE / "scene_detector.mp4"), "--loop", "--live",
       "--pose", str(pose), "--scale", "1", "--min-area", "30",
       "--port", str(PORT), "--no-browser"]
print("  " + " ".join(cmd) + "\n")
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1, cwd=str(ROOT))

lines: list[str] = []
import threading
def pump():
    for ln in proc.stdout:                                    # type: ignore[union-attr]
        lines.append(ln.rstrip())
threading.Thread(target=pump, daemon=True).start()

deadline = time.time() + 45
up = False
while time.time() < deadline:
    if getj("/health"):
        up = True
        break
    if proc.poll() is not None:
        break
    time.sleep(0.5)
ok("server answered /health", up,
   "process exited" if proc.poll() is not None else ("" if up else "timed out"))

if not up:
    print("\n  ---- the rig's own output, which says why ----")
    for ln in lines[-40:]:
        print("   | " + ln)
    proc.terminate()
    sys.exit(1)

print("  waiting 25 s for the node to detect, track and post...")
time.sleep(25)

hdr("2. SYMPTOM A — IS A SENSOR NODE ALIVE AND POSTING?")
h = getj("/health") or {}
src = (h.get("source") or {})
state = getj("/api/state") or {}
sw = state.get("source") or {}
nodes = h.get("nodes") or state.get("nodes") or []
print(f"  active source   : {sw.get('active_source')}")
print(f"  video           : {src.get('video')}")
print(f"  video_sync      : {str(src.get('video_sync'))[:110]}")
print(f"  demoted_nodes   : {sw.get('demoted_nodes')}")
print(f"  nodes           : {json.dumps(nodes, default=str)[:200]}")
print(f"  seq             : {h.get('event_seq')}")

ok("a sensor node has registered", bool(nodes), f"{len(nodes)} node(s)")
ok("the active source is VIDEO_STREAM (not RECORDED)",
   sw.get("active_source") == "VIDEO_STREAM", str(sw.get("active_source")))
ok("no node is being DEMOTED (posting but ignored)",
   not (sw.get("demoted_nodes") or {}), str(sw.get("demoted_nodes")))
ok("the pipeline has recomputed at least once (seq > 0)",
   (h.get("event_seq") or 0) > 0, f"seq={h.get('event_seq')}")

# THE NODE'S OWN VOICE. If A failed, the reason is almost always in here.
nodelines = [l for l in lines if l.startswith("[node]")]
print(f"\n  ---- the node's own output ({len(nodelines)} lines) ----")
for ln in nodelines[-14:]:
    print("   | " + ln)
if not nodelines:
    print("   | (NOTHING. The node was never launched — check that --live is set,")
    print("   |  and look for a '[node] could not start' line above.)")
    for ln in lines[-12:]:
        print("   | " + ln)

hdr("3. SYMPTOM B — DO THE DETECTION BOXES MOVE?")
def boxes():
    s2 = getj("/api/state") or {}
    out = {}
    for r in (s2.get("records") or []):
        c = r.get("eo_contact")
        if c and c.get("bbox_px"):
            out[c["contact_id"]] = tuple(c["bbox_px"])
    for c in (s2.get("unresolved") or s2.get("loose") or []):
        if c.get("bbox_px"):
            out.setdefault(c["contact_id"], tuple(c["bbox_px"]))
    return out

b1 = boxes()
print(f"  sample 1: {len(b1)} boxes  {dict(list(b1.items())[:3])}")
time.sleep(6)
b2 = boxes()
print(f"  sample 2: {len(b2)} boxes  {dict(list(b2.items())[:3])}")
common = set(b1) & set(b2)
moved = [k for k in common if b1[k] != b2[k]]
same = [k for k in common if b1[k] == b2[k]]
print(f"  shared ids: {len(common)}   moved: {len(moved)}   identical: {len(same)}")
ok("boxes exist at all", bool(b2), f"{len(b2)}")
ok("boxes MOVE between samples", bool(moved) or not common,
   f"{len(moved)} moved / {len(common)} shared")
if b2 and all(str(k).startswith("c-") for k in b2):
    print("  NOTE: every id starts 'c-' — these are the RECORDED SCENE's contacts,")
    print("        not a live sensor's ('pi-*'). That is symptom A, showing up here.")

hdr("4. SYMPTOM C — DOES EXPORT EVIDENCE RETURN A DOCUMENT?")
recs = (getj("/api/state") or {}).get("records") or []
if not recs:
    ok("there is a record to export", False, "no records")
else:
    rid = recs[0].get("record_id")
    print(f"  record_id : {rid}")
    for fmt, want in (("html", b"<table"), ("md", b"#"), ("json", b"{")):
        st, body = get(f"/evidence/{rid}?format={fmt}")
        ok(f"GET /evidence/<id>?format={fmt} returns 200",
           st == 200, f"HTTP {st}, {len(body)} bytes")
        if st == 200:
            ok(f"  ...and the {fmt} body looks like a case file", want in body,
               body[:70].decode("utf-8", "replace").replace("\n", " "))
        else:
            print(f"       body: {body[:200].decode('utf-8','replace')}")

hdr("VERDICT")
if not FAIL:
    print("  ALL CHECKS PASSED — the rig is working end to end.")
else:
    print(f"  {len(FAIL)} CHECK(S) FAILED:")
    for f in FAIL:
        print(f"    - {f}")
    print("\n  Read section 2's node output first: almost every downstream symptom")
    print("  ('boxes static', 'boxes misaligned') is a consequence of the node not")
    print("  running, because the console then falls back to the recorded scene.")

print("\n  shutting the rig down...")
try:
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=8)
except Exception:                                             # noqa: BLE001
    proc.kill()
print("  done.")
sys.exit(1 if FAIL else 0)
