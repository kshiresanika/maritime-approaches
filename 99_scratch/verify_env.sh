#!/usr/bin/env bash
# =============================================================================
# verify_env.sh — EDTH Hamburg / Topic 02 — macOS environment verification
#
# WHY THIS FILE EXISTS
# The agent's shell is a sandboxed Linux VM with no network and no Apple GPU, so it
# cannot measure this machine. Every check below must run on the Mac itself. Run it
# here, paste the output back. MEASURED NUMBERS ONLY — nothing in this file guesses.
#
# HOW TO READ THE OUTPUT
# Every check prints exactly one line starting PASS / FAIL / SKIP, followed by the
# evidence that justifies it. A FAIL line always says what would change the answer.
#
# BEFORE RUNNING (optional, only needed for section 5):
#   source ~/.config/edth-hamburg/edth-hamburg-2026.env
# That file defines EDTH_FEATHERLESS_KEY, and optionally
# EDTH_FEATHERLESS_CHAT_MODEL / EDTH_FEATHERLESS_VISION_MODEL.
# Do NOT use a bare FEATHERLESS_API_KEY in ~/.zshrc — multiple Featherless keys
# exist across projects and a single global name collides between them.
#
# Run:  bash 99_scratch/verify_env.sh 2>&1 | tee 99_scratch/verify_env_output.txt
# =============================================================================

set -u  # WHY: unset variables are a bug in a verification script, not a default.
        # NOT set -e: one failing check must not abort the remaining checks — the
        # whole point is a complete picture of what works and what does not.

echo "============================================================"
echo "0. MACHINE IDENTITY"
echo "WHY: proves these numbers came from the Mac and not from a VM."
echo "============================================================"
uname -m -s -r
sw_vers 2>/dev/null || echo "sw_vers absent -> NOT macOS"
sysctl -n machdep.cpu.brand_string 2>/dev/null
echo "python3 resolves to: $(command -v python3 || echo NONE)"
python3 -V 2>&1
echo

echo "============================================================"
echo "1. LIBRARY IMPORTS"
echo "WHY: CLAUDE.md forbids hand-rolling AIS parsing, geodesy, trajectory"
echo "     resampling and spatial joins. Each import below is the library that"
echo "     rule points at. A missing one means that rule cannot be honoured."
echo "============================================================"
python3 - <<'PY_IMPORTS'
import importlib

# Each entry: module name -> the algorithm it exists to stop us hand-rolling.
# This mapping is the point of the check: a FAIL is not "a package is missing",
# it is "we are one step from writing a parser we were told not to write".
TARGETS = {
    "pyais":        "NMEA/AIS sentence decoding",
    "pandas":       "tabular AIS handling",
    "geopandas":    "spatial joins (cable corridors, port polygons)",
    "shapely":      "geometry predicates (proximity to infrastructure)",
    "movingpandas": "trajectory resampling and track algebra",
    "ultralytics":  "EO vessel detection",
    "pydantic":     "contracts.py cross-module types",
    "torch":        "the MPS backend ultralytics runs on",
    "cv2":          "camera capture and frame handling",
}

for mod, purpose in TARGETS.items():
    try:
        m = importlib.import_module(mod)
        ver = getattr(m, "__version__", "version-attribute-absent")
        print(f"PASS  {mod:<13} {ver:<20} ({purpose})")
    except Exception as e:
        # Printing the exception type matters: ModuleNotFoundError means "pip install",
        # anything else (ImportError, OSError) means a broken build, which is a
        # different and slower fix.
        print(f"FAIL  {mod:<13} {type(e).__name__}: {e}")
        print(f"      -> blocks: {purpose}")
        print(f"      -> fix: python3 -m pip install {mod}")
PY_IMPORTS
echo

echo "============================================================"
echo "2. ULTRALYTICS ON device=\"mps\" — MEASURED FPS"
echo "WHY: T2 (live Elbe demo) only exists if EO inference keeps up with a live"
echo "     camera. This measures it rather than assuming it. If MPS is absent the"
echo "     script says so instead of silently falling back to CPU and reporting a"
echo "     number that would mislead the pitch."
echo "============================================================"
python3 - <<'PY_MPS'
import time

try:
    import torch
    import numpy as np
    from ultralytics import YOLO
except Exception as e:
    print(f"SKIP  cannot benchmark: {type(e).__name__}: {e}")
    print("      -> fix: install torch + ultralytics, then re-run section 2.")
    raise SystemExit(0)

# STEP 1 — is the Metal backend actually there?
# built() vs is_available(): built() means this torch was compiled with MPS,
# is_available() means the machine can use it now. Both must be true, and
# distinguishing them tells us whether to reinstall torch or replace the machine.
built = torch.backends.mps.is_built()
avail = torch.backends.mps.is_available()
print(f"torch {torch.__version__} | mps built={built} available={avail}")
if not avail:
    print("FAIL  MPS unavailable -> EO inference would run on CPU.")
    print("      -> what would change the answer: an Apple-silicon Mac with a")
    print("         torch build compiled for MPS (pip install torch, not a CPU wheel).")
    raise SystemExit(0)

# STEP 2 — warm up, then time.
# WHY warm up: the first MPS call pays kernel compilation and weight-transfer cost.
# Including it would understate steady-state FPS by a large factor, and the pitch
# needs the number an operator would actually see, not the first-frame number.
model = YOLO("yolov8n.pt")            # nano: the honest choice on a laptop GPU
frame = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)

for _ in range(5):
    model.predict(frame, device="mps", verbose=False)

N = 30
t0 = time.perf_counter()
for _ in range(N):
    model.predict(frame, device="mps", verbose=False)
dt = time.perf_counter() - t0

fps = N / dt
print(f"PASS  yolov8n @ 640x640, device=mps: {fps:.1f} FPS "
      f"({dt/N*1000:.1f} ms/frame, {N} frames after 5 warmup)")
# Interpretation is deliberately explicit so the number is not quoted out of context.
print(f"      note: synthetic noise frame, batch=1, no tracker, no NMS tuning.")
print(f"      note: a real 1080p camera frame will be slower — re-measure on the")
print(f"            actual capture path before this number goes in a slide.")
PY_MPS
echo

echo "============================================================"
echo "3. MACBOOK CAMERA VIA OPENCV"
echo "WHY: T2 depends on it. NOTE: the first run triggers a macOS camera-permission"
echo "     prompt for Terminal. If you see no prompt and this FAILs, the answer is in"
echo "     System Settings > Privacy & Security > Camera, not in the code."
echo "============================================================"
python3 - <<'PY_CAM'
try:
    import cv2
except Exception as e:
    print(f"SKIP  cv2 unavailable: {type(e).__name__}: {e}")
    raise SystemExit(0)

# CAP_AVFOUNDATION is named explicitly rather than left to auto-detection: on macOS
# the default backend selection has historically picked a stub that opens without
# error and then returns empty frames, which would look like a camera fault.
cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
if not cap.isOpened():
    print("FAIL  VideoCapture(0, CAP_AVFOUNDATION) did not open.")
    print("      -> what would change the answer: granting Terminal camera access,")
    print("         or another process (Zoom, Photo Booth) releasing the device.")
    raise SystemExit(0)

ok, frame = cap.read()   # opening is not proof; a real frame is.
cap.release()            # release explicitly — a held camera blocks the next run.

if ok and frame is not None:
    h, w = frame.shape[:2]
    print(f"PASS  captured one frame: {w}x{h}, dtype={frame.dtype}")
else:
    print("FAIL  camera opened but returned no frame (permission stub or in use).")
PY_CAM
echo

echo "============================================================"
echo "4. DANISH MARITIME AUTHORITY — BULK AIS ENDPOINT"
echo "WHY: DATASETS.md makes this the PRIMARY source and the whole T1 demo rests on"
echo "     it. Three separate things are checked: is it reachable, what is actually"
echo "     served, and how big is one file."
echo "NOTE: http:// on purpose. The host presents a TLS certificate whose hostname"
echo "     does not match, so https:// fails cert verification. That is a real"
echo "     constraint on any download script, not a typo."
echo "============================================================"
INDEX_URL="http://web.ais.dk/aisdata/"
echo "-- HTTP status --"
curl -sS -o /dev/null -w "%{http_code} in %{time_total}s\n" --max-time 20 "$INDEX_URL"

echo "-- first filenames in the index --"
# Regex over the autoindex rather than an assumed filename: the naming scheme is
# not something we should guess. Whatever the server lists is the ground truth.
curl -sS --max-time 20 "$INDEX_URL" \
  | grep -oE 'aisdk-[0-9-]+\.(zip|csv|rar)' \
  | sort -u | tail -5

echo "-- size of one recent file (HEAD, no download) --"
SAMPLE=$(curl -sS --max-time 20 "$INDEX_URL" \
         | grep -oE 'aisdk-[0-9-]+\.(zip|csv|rar)' | sort -u | tail -1)
if [ -n "${SAMPLE:-}" ]; then
  echo "sample file: $SAMPLE"
  # -I = HEAD: asks for the size without pulling a multi-GB body over hotel wifi.
  curl -sSI --max-time 20 "${INDEX_URL}${SAMPLE}" \
    | grep -iE 'content-length|content-type|last-modified'
else
  echo "FAIL  could not extract a filename from the index."
  echo "      -> what would change the answer: open $INDEX_URL in a browser and"
  echo "         report the naming scheme; the index may have moved to"
  echo "         http://aisdata.ais.dk/ which renders via JavaScript."
fi
echo
echo "LICENCE — record this, do not re-derive it:"
echo "  Access is granted under Danish act no. 596 of 24 June 2005 on the further"
echo "  use of public sector information. DMA does not guarantee correctness and"
echo "  accepts no liability. Recipients may NOT combine AIS with other datasets to"
echo "  identify individuals without Danish Data Protection Agency authorisation."
echo "  Redistribution and attribution are NOT addressed in the published policy,"
echo "  and DMA is separately authorised to sell AIS data commercially."
echo "  => 'free to download' is not 'openly licensed'. Confirm in writing before"
echo "     any pitch slide claims an open licence. This is criterion 4 territory."
echo

echo "============================================================"
echo "5. FEATHERLESS — CHAT AND VISION"
echo "WHY: the LLM writes the RATIONALE (never the verdict). Both a text call and a"
echo "     vision call must work: vision produces the OBSERVED vessel class that the"
echo "     deterministic pipeline compares against the AIS CLAIM (criterion 3)."
echo "============================================================"
# --- project-scoped key resolution ---------------------------------------
# Canonical names for THIS project are EDTH_*. The generic FEATHERLESS_* names are
# accepted as a fallback only. Resolution order is deliberate: project-specific
# beats generic, so a stray generic export from another project cannot shadow this
# project's key.
FEATHERLESS_API_KEY="${EDTH_FEATHERLESS_KEY:-${FEATHERLESS_API_KEY:-}}"
FEATHERLESS_CHAT_MODEL="${EDTH_FEATHERLESS_CHAT_MODEL:-${FEATHERLESS_CHAT_MODEL:-}}"
FEATHERLESS_VISION_MODEL="${EDTH_FEATHERLESS_VISION_MODEL:-${FEATHERLESS_VISION_MODEL:-}}"
# exported because the inline python in 5c reads it from os.environ
export FEATHERLESS_VISION_MODEL
# -------------------------------------------------------------------------
if [ -z "${FEATHERLESS_API_KEY:-}" ]; then
  echo "SKIP  FEATHERLESS_API_KEY not set in this shell."
  echo "      -> fix: export FEATHERLESS_API_KEY=... then re-run."
else
  BASE="https://api.featherless.ai/v1"   # OpenAI-compatible surface

  echo "-- 5a. reachability + available model ids --"
  # Listing models first is deliberate: model ids on Featherless are HuggingFace
  # repo paths and are NOT guessable. Hardcoding one turns a wrong-id 404 into
  # something that looks like a network failure. Pick ids from this list.
  curl -sS -o /tmp/fl_models.json -w "GET /models -> %{http_code}\n" \
    --max-time 30 -H "Authorization: Bearer $FEATHERLESS_API_KEY" "$BASE/models"
  echo "   sample of ids returned:"
  python3 -c "import json,sys; d=json.load(open('/tmp/fl_models.json')); ids=[m.get('id') for m in d.get('data',[])]; print('   count:',len(ids)); [print('   ',i) for i in ids[:8]]" 2>/dev/null \
    || { echo "   could not parse /models response; raw head:"; head -c 400 /tmp/fl_models.json; echo; }
  echo "   vision-capable candidates (name heuristic only — confirm in their docs):"
  python3 -c "import json; d=json.load(open('/tmp/fl_models.json')); ids=[m.get('id','') for m in d.get('data',[])]; c=[i for i in ids if any(k in i.lower() for k in ('vl','vision','llava'))]; print('   none matched' if not c else '\n'.join('   '+i for i in c[:8]))" 2>/dev/null

  CHAT_MODEL="${FEATHERLESS_CHAT_MODEL:-}"
  if [ -z "$CHAT_MODEL" ]; then
    echo "SKIP  5b chat: FEATHERLESS_CHAT_MODEL not set."
    echo "      -> pick an id from the list above, export it, re-run."
  else
    echo "-- 5b. chat completion ($CHAT_MODEL) --"
    # max_tokens kept tiny on purpose: this proves auth + routing + response shape.
    # It is not a quality test, and constraints.md caps context at 32K anyway.
    curl -sS -o /tmp/fl_chat.json -w "POST /chat/completions -> %{http_code}\n" \
      --max-time 60 -H "Authorization: Bearer $FEATHERLESS_API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"model\":\"$CHAT_MODEL\",\"max_tokens\":24,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: FEATHERLESS_CHAT_OK\"}]}" \
      "$BASE/chat/completions"
    head -c 500 /tmp/fl_chat.json; echo
  fi

  VISION_MODEL="${FEATHERLESS_VISION_MODEL:-}"
  if [ -z "$VISION_MODEL" ]; then
    echo "SKIP  5c vision: FEATHERLESS_VISION_MODEL not set."
    echo "      -> pick a vision-capable id from 5a, export it, re-run."
  else
    echo "-- 5c. vision call ($VISION_MODEL) --"
    # A synthetic test image, not a real vessel photo: this checks the transport
    # (does a data-URI image reach the model and come back described), not detection
    # quality. Using a real ship photo here would confuse a plumbing failure with a
    # model-accuracy failure.
    python3 - <<'PY_IMG'
import base64, numpy as np, cv2
# Dark background with a bright horizontal bar — crudely ship-like in aspect ratio,
# unambiguous enough that any working VLM will mention a shape, and containing no
# real vessel and no person (constraints.md rule 5).
img = np.zeros((240, 480, 3), np.uint8)
cv2.rectangle(img, (60, 110), (420, 150), (200, 200, 200), -1)
ok, buf = cv2.imencode(".png", img)
open("/tmp/fl_test.b64", "w").write(base64.b64encode(buf.tobytes()).decode())
print("   test image written: 480x240 synthetic, no real vessel, no persons")
PY_IMG
    B64=$(cat /tmp/fl_test.b64)
    python3 -c "
import json,os
# Built with json.dumps rather than string interpolation: the base64 payload is long
# and shell-quoting it by hand is the classic way this call fails for the wrong reason.
b64=open('/tmp/fl_test.b64').read().strip()
p={'model':os.environ['FEATHERLESS_VISION_MODEL'],'max_tokens':48,'messages':[{'role':'user','content':[{'type':'text','text':'Describe the shape in one short sentence.'},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+b64}}]}]}
open('/tmp/fl_vision_req.json','w').write(json.dumps(p))
"
    curl -sS -o /tmp/fl_vision.json -w "POST /chat/completions (vision) -> %{http_code}\n" \
      --max-time 90 -H "Authorization: Bearer $FEATHERLESS_API_KEY" \
      -H "Content-Type: application/json" \
      --data-binary @/tmp/fl_vision_req.json "$BASE/chat/completions"
    head -c 600 /tmp/fl_vision.json; echo
  fi
fi
echo

echo "============================================================"
echo "DONE. Paste everything above back."
echo "A FAIL is information, not a problem — it says which of T1/T2 is at risk"
echo "and what specifically would change the answer."
echo "============================================================"
