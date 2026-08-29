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

---
---

# Session 2 — 03_src/geometry.py

`03_src/geometry.py`, 1013 lines. `eo_detector.py` refactored to import from it: the
camera model now exists once.

## What moved, and what that changed

`CameraPose`, `relative_bearing_deg`, `true_bearing_deg`, `bearing_uncertainty_deg` and
`uncalibrated_benchmark_pose` moved out of `eo_detector.py` into `geometry.py`, as the
last session said they would. `eo_detector.py` is 4.6 KB smaller and imports them.

**Nothing about eo_detector's behaviour changed.** Verified numerically, not asserted:
the bearing sigma for the reference case is still 1.0838 deg to four decimals, the
right-frame-edge bearing is still exactly +hfov/2, a pose JSON written before
`geometry.py` existed still loads, and unknown keys in a hand-edited pose file are now
ignored rather than fatal. `CameraPose` gained `pitch_deg`, `roll_deg`, `height_m` and
their uncertainties, all defaulting to the level, unrolled, height-unknown camera that
`eo_detector` already assumed.

`predict_measurement()` was NOT duplicated. Lane C owns lat/lon -> bearing+range and its
docstring is right that a second implementation is a second chance to be wrong.
`geometry.py` provides the inverse, `project_to_position()` (Geod.fwd), which nothing
had and which D0 needs to place a synthetic contact on the map.

## THE HEADLINE. Put this on the limits slide.

Measured, 1920x1080, 70 deg lens, camera 20 m up, pitch known to 0.5 deg:

| range | rel. sigma | waterline row | usable for a position mismatch? |
|---|---|---|---|
| 153 m | 10% | 716.0 | yes |
| 299 m | 16% | 628.5 | yes |
| 585 m | 30% | 583.7 | yes |
| 1,144 m | 59% | 560.9 | **no** |
| 2,237 m | 116% | 549.3 | **no** |
| 8,553 m | 578% | 540.8 | **no** |
| 16,725 m | 14437% | 540.0 | **no** |

**Look at the last two rows. Doubling the range from 8.5 km to 16.7 km moves the
waterline by eight tenths of one pixel.** No algorithm recovers range from 0.8 px — the
information is not in the image. Monocular waterline ranging is a short-range technique,
and any claim past roughly a kilometre at this height and focal length is false.

The code does not hide this: past that point `estimate_range_from_waterline` sets
`usable_for_position_mismatch=False` and fills `limitations`, so the pipeline defers
rather than inventing. That is criterion 4 doing its job.

## What actually buys range — measured, in order of value

1. **Mark the horizon in the frame** instead of trusting an inclinometer. Attitude is
   the dominant error term at every range past ~150 m. A horizon marked to 4 px is
   worth 0.17 deg against an inclinometer's 0.5 deg. Measured: sigma improves **1.7x**,
   usable range goes **675 m -> 1,125 m**. This is free.
2. **Longer focal length.** Error is linear in degrees-per-pixel, so 3x lens = 3x usable
   range. Cheaper than any software.
3. **Camera height.** Dominates the budget only under ~100 m, where it is the largest
   single term.
4. **A second bearing from a separate site.** The real answer past a kilometre. Two
   bearings triangulate; one bearing plus a waterline does not.

## Two counter-intuitive results, both measured

**Flat earth UNDERestimates range**, and everyone guesses the opposite. Curvature drops
the target below the tangent plane, so a target at a given range shows a LARGER
depression than flat geometry predicts — therefore a given MEASURED depression means a
LONGER range than flat geometry says. At h=20 m: -1.8% at 0.5 deg depression, **-26.8%
at 0.15 deg**. A flat-earth pipeline reports vessels as closer than they are, which is
the direction that sends a patrol boat at the wrong contact.

**Roll is the sleeper.** It contributes exactly zero at the frame centre and up to
**+14.7% of range** at the frame edge (2 deg roll, 940 px off-axis). If ranges look fine
in the middle of the frame and wrong at the sides, the mount is not level — it will look
like a detector bug and it is not.

Related: the same waterline ROW means different ranges at different COLUMNS. At row 800,
the centre column gives 104 m and the frame edge gives 126 m, **21% apart**. That is why
`depression_below_horizontal_deg` uses the full 3-D ray norm instead of the cheaper
difference-of-atans.

## Every assumption, stated (A1-A6 in the module docstring)

A1 pinhole, square pixels, no lens distortion · A2 principal point at frame centre ·
A3 known camera height · A4 known attitude, or a visible horizon · A5 **the bottom of
the bounding box is the waterline — the weakest link, say it first** · A6 earth shape
and refraction (7/6 optical; **4/3 is the RADIO figure and using it overstates the
horizon by 7%**).

Each one is a live input to `estimate_range_from_waterline`, not an asserted caveat:
`RangeEstimate.components_m` gives the metres of error each assumption contributed to
this particular contact, so "which assumption dominates?" is answerable per contact.

## Verified in the Linux VM (pyproj stubbed — see the gap below)

```
PASS  horizon vs mariner's rule 2.08*sqrt(h) NM at h=2,10,20,50,100 m   agree 0.09%
PASS  sphere round trip range->depression->range, 36 cases    worst 6.2e-07
PASS  flat round trip, 24 cases                               worst 2.4e-16
PASS  bisection agrees with closed form phi=alpha-arccos(A cos alpha)   5e-07
PASS  saturation: depression -> dip gives range -> horizon distance
PASS  refuses at/above horizon; refuses with no camera height
PASS  pitch <-> horizon row round trip                        3.6e-15 deg
PASS  on-axis exact == difference-of-atans; off-axis differs by 1.87 deg
PASS  sigma_m equals the quadrature of components_m
PASS  tabletop flat-plane round trip at h=0.30 m, ranges 0.5-3.0 m
PASS  SCALE INVARIANCE: rel sigma 10.26% at BOTH h=20m/s=200m and h=0.3m/s=3m
PASS  eo_detector: no undefined names, no unused imports, bearing sigma unchanged
```

Two of my own test assertions were WRONG and the code was right: I had the flat-vs-sphere
direction backwards, and I expected `signed_delta_deg(180, 0)` to be +180 when it is
-180. Both are now documented in the module rather than quietly fixed in the test.

## The verification gap

`project_to_position()` and `inverse_geodesic()` were **NOT EXECUTED**. pyproj is not
installed in the agent's Linux VM and there is no network there to install it, so both
were type-checked and their argument order confirmed against the pyproj docs — pyproj
takes **LONGITUDE FIRST**, and `Geod.fwd` returns the **BACK** azimuth as its third
value by default. Both wrappers exist partly so no call site has to remember that again.
Run part 3 below to close the gap.

---

## PASTE-READY BLOCK — geometry, run on the Mac

### Part 3 — prove the geodesy and print the error bands

```sh
cd ~/Desktop/MaritimeApproaches && source .venv/bin/activate && python3 - << 'GEOCHK'
import sys; sys.path.insert(0, "03_src")
import geometry as G

# 1. The two pyproj wrappers the agent could not execute. A round trip must return
#    the bearing and range it started with.
lat, lon = 54.5000, 11.3000            # a plausible Fehmarn Belt anchor
for brg, rng in ((0.0, 1000.0), (90.0, 5000.0), (231.7, 12345.6)):
    la2, lo2 = G.project_to_position(lat, lon, brg, rng)
    b2, r2 = G.inverse_geodesic(lat, lon, la2, lo2)
    ok = abs(float(b2) - brg) < 1e-6 and abs(float(r2) - rng) < 1e-6
    print(("PASS  " if ok else "FAIL  ") +
          "geodesic round trip brg=%7.2f rng=%9.1f -> %.6f, %.6f  (back %8.4f deg, %9.3f m)"
          % (brg, rng, la2, lo2, b2, r2))

# 2. Sanity on the ellipsoid: 1 arc-minute of latitude should be ~1852 m (a nautical mile).
_b, d = G.inverse_geodesic(54.0, 11.0, 54.0 + 1/60.0, 11.0)
print("PASS  " if abs(float(d) - 1852) < 15 else "FAIL  ",
      "one arc-minute of latitude = %.1f m (nautical mile is 1852)" % float(d))
GEOCHK

# 3. The error band a judge will ask for. SEA case — put this table on a slide.
python 03_src/geometry.py --error-band --height 20 --hfov 70

# 4. TABLETOP case. Use your MEASURED camera height above the table.
python 03_src/geometry.py --error-band --flat --height 0.30 --hfov 70
```

### Part 4 — the only real accuracy claim available to this project

There is no water access, so the tabletop at 1:2000 scale is where this geometry meets
ground truth. The relative error band is **scale invariant** (verified: 10.26% at both
h=20 m/s=200 m and h=0.30 m/s=3.0 m), so a tape measure on a table validates the same
maths that would run on the Elbe.

1. Set the camera up for D1. Measure the height above the table with a ruler.
2. Place each silhouette, tape-measure its distance, run `eo_detector.py`, and read
   `bbox_px[3]` (the fourth number) out of the contacts JSONL as `row_px`.
3. Fill `04_demo/tabletop_truth_TEMPLATE.csv` (copy it to `tabletop_truth.csv`) and:

```sh
python 03_src/geometry.py --validate 04_demo/tabletop_truth.csv \
  --flat --height 0.30 --hfov 70 --pitch 15
```

**The number that matters is the last line: what fraction of truths landed inside the
1-sigma band.** An honestly calibrated band sits near 68%. Far above and the band is
padded; far below and the uncertainties are understated — which is the dangerous
direction, because understated sigma manufactures `Mismatch.significance` out of nothing
and the pipeline then accuses a real hull.

A nonzero mean residual is a CALIBRATION error (height, pitch or FOV), not noise. Chase
it before Sunday; it is the cheapest accuracy in the project.

## Still not done in lane B

- **Nothing wires range into `EoContact` yet.** `eo_detector.py` still emits
  `observed_range_m=None`. Connecting it needs a real surveyed pose with `height_m` set,
  which does not exist until the D1 tabletop is built. Deliberate: an unsurveyed pose
  would produce ranges, and ranges that look plausible are worse than none.
- **Cable proximity** is absent — there is no cable route geometry in the repo. When
  there is, it is `shapely` distance-to-LineString in a projected CRS, in this file.
- FPS and detection counts are **still not measured** (session 1's open item).

---
---

# Session 3 — 03_src/eo_vlm.py (the vision observer)

1118 lines. Featherless vision through the `openai` SDK with `base_url`, base64 JPEG
crops, two calls in flight maximum. New lane B file — add it to FILE_OWNERSHIP.md under
lane B beside `eo_detector.py` and `geometry.py`.

## THE HARD RULE IS ENFORCED, NOT PROMPTED

A prompt asking a model to behave is a request. The one response in fifty that says
"this is likely a spoofed tanker" arrives looking exactly like the forty-nine that
behaved, and lands in an evidence record a judge reads as the system's finding.

So there are two independent nets, and both **reject** rather than sanitise:

1. **A closed output schema.** Every field is a number or a member of a fixed enum.
   There is no free-text field anywhere, so a conforming response has nowhere to put a
   conclusion even if the model wants to.
2. **A raw-text scan** for decision language, absolute sizes and identity claims, which
   catches the case where the model ignores the schema and writes prose.

A rejected response yields `observed_class=None` plus a `refusal_reason`, and **the raw
text is kept in `rejected_text`**. Silently stripping the offending words would hide a
model that is trying to decide, which is exactly what an operator needs to see.

Verified against 17 adversarial responses — all rejected with the correct reason — and 6
legitimate ones, none rejected. One real bug surfaced: the 9-digit MMSI pattern was
firing on a confidence of `0.123456789`. Found by a false-positive test, not by reading.

## It never writes a length, and that is load-bearing

`consistency.check_length` names the circularity trap and puts the guarantee on us:
"observed_length_m must be derived from pixel extent multiplied by an INDEPENDENTLY
MEASURED RANGE... Lane B owns that guarantee; this module cannot verify it."

A VLM asked how long a ship is will answer confidently in metres **from its prior about
what ships of that type are**. That is not a measurement of this hull. Feed it to
`check_length` and the strongest spoof signal in the system silently becomes a comparison
between an AIS claim and a stereotype. So `eo_vlm` never writes `observed_length_m`, the
validator rejects any response containing an absolute size, and `apply_to_contact` was
verified by AST to write exactly two fields: `observed_class` and
`observed_class_confidence`. Metres come from `geometry.py` or stay None.

What the model may say about size is dimensionless and about the image:
`hull_fraction_of_crop` and `length_to_height_ratio`, both in pixels.

## MEASURED: what a class observation is actually worth

Computed against lane C's own `_probability_to_sigma` and thresholds:

| VLM confidence | significance | class mismatch fires? |
|---|---|---|
| 0.90 | 1.282 | no |
| 0.95 | 1.645 | no |
| 0.9772 | 2.000 | threshold |
| 0.98 | 2.054 | **yes** |

**A confusable pair can NEVER fire.** Max reachable significance is 3.719σ (confidence
clips at 0.9999); halved by lane C's discount that is 1.860, against a 2.0 floor. So
cargo/tanker, tug/small_craft, fishing/small_craft, tug/fishing and passenger/cargo
cannot produce a class mismatch at any confidence at all. **Teaching this model to tell a
cargo ship from a tanker buys the pipeline nothing.** The prompt is therefore built
around the distinctions that CAN fire — large-commercial vs small-craft vs passenger vs
naval — and the scorer reports group agreement separately for that reason.

## Uncalibrated confidence is capped below the firing threshold

Self-reported VLM confidence is not a probability. Until an agreement rate is measured,
every confidence is capped at **0.95 → 1.645σ**, below lane C's 2.0 floor. The
observation still reaches the evidence record, still discounts
`_observation_confidence` on every other mismatch for that contact, and still cannot
raise a class mismatch on its own.

Same pattern as `geometry.yaw_uncertainty_deg=180.0`: make the uncalibrated state
**structurally inert**, not merely documented. There is no flag to raise the ceiling. The
only way through is data.

The ceiling from data is the **Wilson 95% lower bound** on measured agreement, not the
raw hit rate — 18 correct out of 20 is 90%, but the true accuracy could easily be 72%,
and a small lucky sample must not license high-confidence accusations against real named
vessels. Measured consequence:

| sample | agreement | ceiling | sigma | fires? |
|---|---|---|---|---|
| 18/20 | 90% | 0.6990 | 0.521 | no |
| 20/20 | 100% | 0.8389 | 0.990 | no |
| 50/50 | 100% | 0.9286 | 1.466 | no |
| 100/100 | 100% | 0.9630 | 1.787 | no |
| 200/200 | 100% | 0.9812 | 2.079 | **yes** |

**About 165 hand-labelled crops at 100% agreement are needed before a class mismatch may
fire.** A hackathon-sized sample will not get there, so the class dimension will
contribute zero mismatches in the demo. **That is the correct outcome, not a failure** —
lane C's own docstring says silhouette classification is the weakest evidence in the
system and the arithmetic should say so. Request filed to lane C so they know; the lever
to change it is in their file and it is their call, not lane B's. LENGTH mismatch is
unaffected and remains the strong path.

## Budget

`BoundedSemaphore(2)` — Qwen3-VL-30B-A3B costs 2 of the plan's 4 units. **Measured: peak
concurrency 2 across 12 contacts.** The budget is per ACCOUNT, not per module: if
`report.py` (lane D) calls the same endpoint during the demo it must
`from eo_vlm import FEATHERLESS_UNITS` and acquire the same semaphore, or you take 429s
in both lanes at once. Worth one line in lane D's handoff.

Crops, not whole frames: a contact under 48 px on its long edge is **refused before any
API call**, because a 20 px vessel carries no silhouette and the model will still answer,
fluently, with a confidence attached.

## AGREEMENT RATE: NOT MEASURED

There are no images anywhere in this repo, no Featherless key or network egress in the
agent's VM, and `openai` is not in `.venv`. Any agreement number from here would be
fabricated. The harness is built and the workflow is below.

---

## PASTE-READY BLOCK — the vision observer

### Part 5 — install, verify the model id, smoke-test the pipe

```sh
cd ~/Desktop/MaritimeApproaches && source .venv/bin/activate
source ~/.config/edth-hamburg/edth-hamburg-2026.env

# WATCH pip's output for "Uninstalling numpy-..." or "Uninstalling pydantic-...".
# openai pulls shared deps and a silent downgrade under the working torch/geo stack is
# how this environment breaks mid-hackathon. If you see one, stop and tell me.
python -m pip install openai
python -c "import openai, numpy, pydantic; print('openai', openai.__version__,
'| numpy', numpy.__version__, '| pydantic', pydantic.__version__)"

# Confirm the vision model id EXISTS rather than trusting the env var. Model ids are
# not guessable and a 404 on the vision call usually means a wrong id, not a fault.
python3 - << 'IDCHK'
import os
from openai import OpenAI
c = OpenAI(base_url="https://api.featherless.ai/v1", api_key=os.environ["EDTH_FEATHERLESS_KEY"])
want = os.environ.get("EDTH_FEATHERLESS_VISION_MODEL", "Qwen/Qwen3-VL-30B-A3B-Instruct")
ids = {m.id for m in c.models.list().data}
print(("PASS  " if want in ids else "FAIL  ") + "vision model available: " + want)
if want not in ids:
    print("  candidates:", sorted(i for i in ids if "VL" in i or "vision" in i.lower())[:10])
IDCHK

# Smoke-test the PLUMBING (auth, model id, JSON mode, schema conformance) on a
# synthetic image. The model should answer "unknown" — that is a PASS. This tests the
# pipe, not the accuracy.
python3 - << 'SMOKE'
import sys, numpy as np; sys.path.insert(0, "03_src")
import eo_vlm as V
img = np.full((300, 500, 3), 120, np.uint8); img[:150] = (200, 170, 140)
img[150:210, 120:380] = (60, 60, 65)
o = V.observe_crop(img, "smoke-1", client=V.build_client(), model=None or
                   __import__("os").environ.get("EDTH_FEATHERLESS_VISION_MODEL",
                   V.DEFAULT_VISION_MODEL))
print("class:", o.observed_class, "| raw conf:", o.class_confidence_raw,
      "| effective:", o.class_confidence_effective)
print("refusal:", o.refusal_reason, "| latency ms:", round(o.latency_ms or 0))
print("notes:", o.notes)
print("PASS — the pipe works" if o.refusal_reason is None else
      "CHECK — " + str(o.refusal_reason) + "\nraw: " + str(o.rejected_text)[:400])
SMOKE
```

If the smoke test reports `server rejected response_format` or `server wanted
max_completion_tokens` in `notes`, that is fine — the module degrades and records it.
Tell me and I will pin the working shape.

### Part 6 — the agreement rate

You need frames with real vessels in them. From the D2 screen-as-scene fallback, or any
harbour clip:

```sh
# 1. Extract frames, run the detector, cut crops.
mkdir -p 04_demo/frames 04_demo/crops
ffmpeg -i 04_demo/clip.mp4 -vsync 0 -q:v 2 04_demo/frames/clip_%06d.jpg
python 03_src/eo_detector.py --source 04_demo/frames --hfov-deg 60 \
  --out-dir 04_demo/eo_runs
python 03_src/eo_vlm.py make-crops \
  --contacts 04_demo/eo_runs/<run>_contacts.jsonl \
  --frames-dir 04_demo/frames --out-dir 04_demo/crops

# 2. LABEL BY EYE. Open 04_demo/crops/labels_TEMPLATE.csv, fill true_class from
#    cargo tanker fishing passenger tug naval small_craft unknown.
#    Save as labels.csv.
#
#    LABEL WHAT YOU SEE, NEVER WHAT AIS SAYS. An AIS-derived label makes the agreement
#    rate measure agreement with the very claim we are testing — circular, and it would
#    read as 100% for a genuinely spoofed vessel.
#    Use "unknown" freely. A crop you cannot classify is data, not a gap.
#    30 crops is a useful sample. 165 is what firing would require.

# 3. Measure.
python 03_src/eo_vlm.py score --labels 04_demo/crops/labels.csv \
  --out-calibration 04_demo/vlm_calibration.json \
  --out-observations 04_demo/vlm_score_observations.jsonl

# 4. Run the observer over real contacts, with the earned ceiling.
python 03_src/eo_vlm.py observe \
  --contacts 04_demo/eo_runs/<run>_contacts.jsonl \
  --frames-dir 04_demo/frames \
  --calibration 04_demo/vlm_calibration.json \
  --out-contacts 04_demo/eo_runs/<run>_contacts_vlm.jsonl \
  --out-observations 04_demo/eo_runs/<run>_vlm.jsonl
```

`score` prints exact agreement, group agreement, the confusion matrix, a
confidence-vs-actual-accuracy calibration table, and the earned ceiling with what it
implies. **Paste that output back.** It is the only honest statement available about
whether the class observation is worth anything, and it belongs on the limits slide
either way — including if the answer is "not much".

## Still open in lane B

- **`EoContact.detection_confidence` STILL NOT APPLIED** to contracts.py (checked again
  this session: zero matches). `eo_detector.py` raises its named guard on first use, so
  the whole EO path is blocked behind a one-field edit. Part 1 in session 1's block.
- `EoContact` has no field naming the model that classified a contact, so provenance
  lives in the `<run>_vlm.jsonl` sidecar. Not worth a second contract interrupt, but it
  is a gap in the criterion-4 chain and lane D should read the sidecar when building
  evidence records.
- FPS and detection counts still not measured (session 1).
- Nothing wires `geometry` range into `EoContact` yet (session 2).
