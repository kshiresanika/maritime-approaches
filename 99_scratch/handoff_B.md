# handoff_B.md — Lane B (EO + Geometry)

Session: 2026-08-28, pre-event. Author: agent, on Amol's instruction.

## What exists now

`03_src/eo_detector.py` (888 lines, heavily commented). Frames in, contract-shaped
`EoContact` out. Verified: parses clean; the pinhole bearing maths is unit-tested
against hand-computed values (see "Verification" below); every kwarg it passes to
`EoContact` exists in `contracts.py` except the one field that is pending.

Three sources, dispatched on `--source`:

| Source | Spec | `frame_time_utc` comes from |
|---|---|---|
| MacBook camera | `camera` or `camera:N` | wall clock at grab (`CAP_AVFOUNDATION`) |
| Recorded clip | a file path | `--clip-start-utc` + `CAP_PROP_POS_MSEC` |
| Still frames | a directory | each file's mtime |

**Why a source layer instead of `model.predict(source=<path>)`:** ultralytics' loader
supplies neither `frame_time_utc` nor `frame_ref`, and for a recorded clip the frame
time must come from the clip's own start, not the wall clock. T1 replays an August-25
AIS slice — wall-clock stamps put every observation three days from every claim, and
`association.py` then reports a sea full of dark vessels. The module refuses to run on
a video without `--clip-start-utc` rather than guess.

Detection: `yolov8n.pt`, `device="mps"`, `imgsz=640`, class filter resolved by the
NAME `boat` (not hardcoded index 8 — wrong-index symptom is "no vessels found", which
is indistinguishable from an empty sea). **No training.** COCO `boat` as instructed.

Tracking: `model.track(persist=True, tracker="bytetrack.yaml")` on camera and video,
so `track_length_frames` is a real count. Image folders get `predict` and
`track_length_frames=1` — ByteTrack across an unordered folder of stills would invent
track identities linking unrelated pictures and lie to lane C about motion evidence.

## The one thing that blocks it — ARCH interrupt

`eo_detector.py` will raise a named `RuntimeError` on first use until
`detection_confidence` exists on `EoContact`. Request filed in `99_scratch/requests.md`
dated 2026-08-28. **Exact change, `03_src/contracts.py`, insert directly after the
`bbox_px` field and before `observed_bearing_deg_true`:**

```python
    detection_confidence: float = Field(
        ge=0.0, le=1.0,
        description="The EO detector's confidence that this box IS A VESSEL. Distinct "
                    "from observed_class_confidence, which is confidence in WHICH "
                    "class it is. YOLO answers the first and cannot answer the "
                    "second; merging them mis-calibrates DARK confidence silently.")
```

Then `tests/fixtures.py` needs the new required arg on its `EoContact` factory.

**Why it cannot be skipped:** COCO has one maritime class. YOLO can say "vessel, 0.83"
and categorically cannot say "tanker". Writing 0.83 into `observed_class_confidence`
asserts a class confidence that was never computed; `verdict.py` then weights a DARK
verdict with a number that does not mean what its field name says. No exception, just
a wrong confidence on the criterion-4 output.

## What this module deliberately does NOT emit

`observed_class` is **always `None`** — not `"unknown"`. `contracts.py` defines `None`
as "not available", and class comes from the VLM. Also `None`: `observed_range_m`,
`observed_length_m`, `observed_heading_deg_true`, `observed_speed_ms`,
`observed_lat_deg`, `observed_lon_deg` — all need monocular range, which is not built.
A length with no error bar is exactly the number that produces a confident SPOOF
verdict about the wrong hull.

## Bearing, and its inference chain

    OBSERVED  box (x1,y1,x2,y2) in frame F at time T
    BEARING   pinhole atan2 of the box centre through CameraPose(yaw, hfov)
    SIGMA     sqrt(yaw_uncertainty^2 + centroid_sigma^2), centroid sigma converted
              at the LOCAL angular scale f/(f^2+dx^2), not the frame average
    CHANGES   a wrong yaw rotates EVERY bearing equally -> a systematic association
              failure across ALL contacts is a pose bug, check camera_pose_ref first.
              A failure on only the DISTANT contacts is optics/pixel extent.

`CameraPose` and the three bearing helpers live in `eo_detector.py` for now. They
belong in `geometry.py` once that file exists (same lane, no ownership conflict). They
are here because `observed_bearing_deg_true` is a REQUIRED contract field — the module
cannot emit a legal contact without them.

**Uncalibrated runs are structurally unusable as evidence.** With `--hfov-deg` and no
`--pose-file`, the pose is built with `yaw_uncertainty_deg = 180.0`, which makes every
bearing worthless at any significance. That is deliberate: a plausible-looking default
of 2 degrees is how an uncalibrated bearing reaches a verdict unnoticed.

Fill `04_demo/camera_pose_TEMPLATE.json` before any run whose bearings are evidence.

## The one assumed number

`BBOX_CENTROID_SIGMA_FRACTION = 0.10` — 1-sigma error in locating a box's horizontal
centre, as a fraction of box width. **ASSUMED, not calibrated.** It feeds every bearing
sigma, therefore every position-mismatch significance, therefore every SPOOF
confidence. It is written into every run manifest's `limitations` and must reach
`EvidenceRecord.limitations`.

## Outputs

`--out-dir` writes `<run_id>_contacts.jsonl` (one `EoContact` per line — appendable, and
a truncated file from a killed live capture still parses) and `<run_id>_manifest.json`
(pose, weights, device, thresholds, class filter, counts, timings, limitations). The
manifest is what makes `camera_pose_ref` resolve to something a judge can inspect.

## Verification done

Ran in the Linux VM against the real file source (ultralytics/pydantic unavailable
there, so the pure-maths functions were extracted by AST and exec'd):

```
PASS  centre pixel -> 0 deg relative
PASS  right edge -> +hfov/2                       (35.000000 at hfov=70)
PASS  left edge  -> -hfov/2
PASS  symmetry about boresight
PASS  wrap 350 + 20 -> 10 ; 10 + -20 -> 350
PASS  edge pixel subtends less angle than centre  (1.0386 vs 1.0838 deg)
PASS  uncalibrated pose floors sigma at 180 deg
PASS  zero-width bbox still yields sigma > 0      (0.0418 deg, not 0)
PASS  only unknown EoContact kwarg is detection_confidence
      nonlinearity: quarter-frame = 19.30 deg vs linear approx 17.50 deg
      -> a linear pixel->angle map would inject 1.80 deg of systematic bias
```

## NOT MEASURED — the open item

**FPS and detection counts on a test clip are NOT MEASURED.** There is no clip in
`04_demo/`, and the agent's shell is a network-less Linux VM with no Metal, no camera
and no ultralytics. Reporting a number from there would violate MEASURED NUMBERS ONLY.
The existing `45.0 FPS yolov8n @ 640 mps` in `verify_env_output.txt` is a synthetic-frame
figure and says nothing about detection rate on water.

Run the block in the next section on the Mac and paste the output back.

## Clip spec — what to record

MacBook Air camera, because it IS the T2 sensor; a number from iPhone optics says
nothing about whether T2 works.

- 60–90 s, **static** (rest the laptop on a sill). Handheld inflates ByteTrack ID churn
  and makes `track_length_frames` meaningless.
- **1080p30, landscape. Not 4K60** — at `imgsz=640` that measures the H.264 decoder.
- Horizon in the upper third, water in the lower two-thirds.
- **2–3 vessels at different ranges.** The far one is the real test: on the Air's wide
  lens a vessel at 2 km is ~20 px and COCO `boat` will likely miss it. That miss is the
  finding, and it is an optics finding, not a model finding.
- Note the time and the compass direction. That becomes `camera_pose_ref`.

Shoot a second clip on iPhone at 5x from the same spot. If iPhone detects where the Air
misses, the bottleneck is optics — a defensible slide, not a failure.

This doubles as the Friday line-of-sight check CLAUDE.md demands.

---

## PASTE-READY BLOCK — run on the Mac, paste the output back

Two parts. Part 1 applies the pending contract change and proves the suite is still
green. Part 2 measures. Run part 1 first; if it fails, do not run part 2.

### Part 1 — apply the contract field (ARCH: contracts.py + fixtures.py)

```sh
# Idempotent: re-running it is a no-op, not a double insert.
cd ~/Desktop/MaritimeApproaches && source .venv/bin/activate && python3 - << 'PATCH'
import re, pathlib

# --- contracts.py: detection_confidence, inserted directly before the bearing block.
# WHY here: it describes the DETECTION, so it belongs before anything derived from it.
c = pathlib.Path("03_src/contracts.py"); s = c.read_text()
FIELD = '''    detection_confidence: float = Field(
        ge=0.0, le=1.0,
        description="The EO detector's confidence that this box IS A VESSEL. Distinct "
                    "from observed_class_confidence, which is confidence in WHICH "
                    "class it is. YOLO answers the first and cannot answer the "
                    "second; merging them mis-calibrates DARK confidence silently.")

'''
ANCHOR = "    observed_bearing_deg_true: float = Field(\n"
if "detection_confidence" in s:
    print("SKIP  contracts.py already has detection_confidence")
elif ANCHOR not in s:
    print("FAIL  anchor not found in contracts.py -- insert the field by hand")
else:
    c.write_text(s.replace(ANCHOR, FIELD + ANCHOR, 1))
    print("PASS  contracts.py patched")

# --- fixtures.py: the factory now needs the new required arg.
f = pathlib.Path("tests/fixtures.py"); t = f.read_text()
FANCHOR = "        bearing_uncertainty_deg=round(rng.uniform(0.3, 2.5), 2),\n"
FLINE = "        detection_confidence=round(rng.uniform(0.45, 0.97), 2),\n"
if "detection_confidence" in t:
    print("SKIP  fixtures.py already has detection_confidence")
elif FANCHOR not in t:
    print("FAIL  anchor not found in fixtures.py -- add the arg by hand")
else:
    f.write_text(t.replace(FANCHOR, FANCHOR + FLINE, 1))
    print("PASS  fixtures.py patched")
PATCH
echo "--- diff ---"; git -C ~/Desktop/MaritimeApproaches diff --stat
echo "--- tests ---"; cd ~/Desktop/MaritimeApproaches && python -m pytest -q 2>&1 | tail -15
```

PASS looks like: two `PASS` lines, then `50 passed` (49 existing + nothing new — the
change is purely additive). Anything red here means stop and read it; a broken
`contracts.py` breaks four lanes at once.

### Part 2 — measure

```sh
cd ~/Desktop/MaritimeApproaches && source .venv/bin/activate

# 2a. Camera smoke test, 5 frames. The FIRST cv2 capture from Terminal raises the
#     macOS TCC camera prompt -- accept it. A failure here is permissions, not code.
python 03_src/eo_detector.py --source camera --hfov-deg 60 --max-frames 5 --benchmark

# 2b. THE REAL MEASUREMENT. Replace the clip name and the start time with yours.
#     --hfov-deg 60 is a PLACEHOLDER: with no --pose-file the pose is built with
#     yaw_uncertainty 180 deg, so bearings from this run are timing-only by design
#     and the FOV value cannot corrupt anything. Measure the real HFOV before any
#     run whose bearings are evidence (see 04_demo/camera_pose_TEMPLATE.json).
python 03_src/eo_detector.py \
  --source 04_demo/clip_elbe_01.mov \
  --clip-start-utc 2026-08-28T18:20:00+02:00 \
  --hfov-deg 60 \
  --out-dir 04_demo/eo_runs \
  --benchmark

# 2c. If 2b finds nothing, ask whether it is a threshold problem before concluding
#     the model failed. Do NOT jump to training.
python 03_src/eo_detector.py \
  --source 04_demo/clip_elbe_01.mov \
  --clip-start-utc 2026-08-28T18:20:00+02:00 \
  --hfov-deg 60 --conf 0.10 --imgsz 1280 --benchmark
```

Running `python 03_src/eo_detector.py` from the repo root works because Python puts
the script's own directory on `sys.path[0]` — the flat-import convention from
LIBRARIES.md holds, no `PYTHONPATH` needed.

What the output means:

- **`inference only` FPS** — the detector's own throughput. Compare against the
  45.0 FPS already in `verify_env_output.txt`; a large drop means the frames are
  bigger than 640 and are being resized every call.
- **`end-to-end incl. decode` FPS** — what T2 actually achieves. This is the number
  that decides whether the live Elbe feed keeps up. FAIL below 25 FPS.
- **`frames with >=1 detection`** — the honest recall proxy on a clip where you know
  vessels were present.
- **`bbox width px` median** — the discriminator. Under ~30 px means COCO `boat` is
  operating below the scale it was trained at, and the fix is optics or `--imgsz`,
  not a different model. Say that on the slide rather than hiding it.

Paste the whole block back. It goes into STATUS.md as measured, or not at all.
