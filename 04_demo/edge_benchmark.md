# edge_benchmark.md — the Pi sensor node, measured

**Created 2026-08-29 (lane F). Status: METHOD + ARGUMENT WRITTEN. ALL PI NUMBERS UNMEASURED.**

Subject: `edge/sensor_node.py` — picamera2 capture, ultralytics YOLO on COCO `boat`,
EoContact out, MJPEG at `/stream`.

---

## 0. Why the numbers are empty

The agent cannot reach the Pi. Its shell on the Mac is a sandboxed Linux VM with no
network egress; the cloud container is a different machine. The Pi is on neither path,
so it cannot `rsync`, cannot run the node, and cannot read a thermal zone. Filling these
tables from a spec sheet would break the project's MEASURED NUMBERS ONLY rule.

`edge/bench_edge.py` is the instrument. It prints a markdown block in exactly the shape
of the tables below — paste its output over them.

```bash
# on the Mac
bash edge/sync_to_pi.sh pi@<pi-host>

# on the Pi, once
sudo apt install -y python3-picamera2 python3-opencv python3-numpy
pip3 install --break-system-packages -r edge/requirements-edge.txt

# on the Pi, with the test object in frame
python3 edge/bench_edge.py --mode sweep      # §2 and §3
python3 edge/bench_edge.py --mode soak       # §4  (5 minutes)
```

---

## 1. Machine — UNMEASURED

| Field | Value |
|---|---|
| Pi model | **UNMEASURED** |
| Kernel / arch | **UNMEASURED** |
| Python | **UNMEASURED** |
| picamera2 version | **UNMEASURED** |
| ultralytics / torch version | **UNMEASURED** |
| Capture size | 640×480 (default; see `00_brief/HARDWARE.md` §3 for why) |
| torch threads | cores − 1 (default) |

Everything in `00_brief/HARDWARE.md` §§1–4 is still unmeasured too. **That document is
the prerequisite for this one** — if `picamera2` does not import, none of this runs.

---

## 2. Frame rate vs inference size — UNMEASURED

`--mode sweep`. imgsz is the one lever that matters; the sweep is what makes the choice
measured rather than asserted.

| imgsz | FPS p50 | FPS p95 | FPS worst | capture ms | inference ms | encode ms | det rate | conf p50 |
|---|---|---|---|---|---|---|---|---|
| 256 | | | | | | | | |
| 320 | | | | | | | | |
| 416 | | | | | | | | |
| 640 | | | | | | | | |

FPS is reported as a **distribution, not a mean**: a mean of 6 hides a 400 ms stall every
two seconds, and the stall is what an operator notices. The per-stage split is there
because "6 FPS" is not actionable while "capture 8 ms, inference 140 ms, encode 12 ms" tells
you the only lever worth pulling.

---

## 3. Detection on a test object — UNMEASURED

| Field | Value |
|---|---|
| Test object | **UNMEASURED** (describe it: printed silhouette / toy hull / monitor clip) |
| Distance and lighting | **UNMEASURED** |
| Frames with ≥1 detection / frames total | **UNMEASURED** |
| Detection rate | **UNMEASURED** |
| Confidence p50 / min | **UNMEASURED** |

**This is the number that decides whether the YOLO node is viable at all.** COCO's
`boat` class may not fire on a printed cut-out — that risk is recorded in
`pi_sensor.py`'s own docstring as the single identified thing that could kill the live
segment.

If the rate comes back near zero, the answer is **switch to `04_demo/pi_sensor.py`**
(classical MOG2, no neural network, does not care what the object is). The answer is
**not** to lower `--conf` until something appears: that manufactures detections out of
background texture and every one of them enters the pipeline as an observation.

---

## 4. Sustained run and thermals — UNMEASURED

`--mode soak`, five minutes.

| Field | Value |
|---|---|
| FPS overall over 5 min | **UNMEASURED** |
| CPU temp start → end (max) | **UNMEASURED** |
| `vcgencmd get_throttled` at end | **UNMEASURED** |
| Detection rate over the run | **UNMEASURED** |

| t (s) | CPU temp °C | FPS in window |
|---|---|---|
| | | |

The throttle flag travels **with** the FPS or the FPS is not evidence. A Pi is fast for
thirty seconds and then throttles; a benchmark that stops at thirty seconds measures a
machine that will not exist during a five-minute demo slot. `bench_edge.py` stamps the
whole report **UNRELIABLE** if the flag is non-zero, rather than quietly publishing it.

---

## 5. If the frame rate is low — the argument, not an apology

Record the number. Then say this, because it is arithmetic rather than consolation.

**A vessel at 12 knots covers 6.17 m/s.**

| Frame rate | Distance between frames |
|---|---|
| 10 FPS | 0.62 m |
| 5 FPS | 1.23 m |
| 2 FPS | 3.09 m |
| 1 FPS | 6.17 m |

The pipeline's **measured** cross-range position uncertainty at 5 km is **218 m** (lane C,
2026-08-29). At 2 FPS the inter-frame step is **1.4 % of the uncertainty the verdict
already carries**. Inter-frame displacement would only equal that sigma after **35
seconds** — i.e. at 0.028 FPS.

In the units a tracker actually works in, a vessel crossing at 5 km moves **0.69 px/s** on
a 66° lens over 640 px. At 1 FPS that is **0.69 pixels between frames**.

> Frame rate is the binding constraint on a self-driving car, which has to decide inside
> a stopping distance. It is not the binding constraint on a maritime picture, where the
> object of interest takes minutes to cross the frame and the position uncertainty is
> two orders of magnitude larger than the inter-frame step. What binds here is bearing
> accuracy and the honesty of the confidence — and neither improves by running faster.

### The one place low FPS DOES bite — and it is the indoor demo, not the sea

Indoors at `--scale 20`, a 1.2 m table across 640 px is **1.88 mm per pixel**, so a hand
moving a model hull produces far higher pixel rates than any real vessel:

| Hand speed | px/s | @5 FPS | @2 FPS | @1 FPS |
|---|---|---|---|---|
| 5 cm/s | 26.7 | 5.3 px | 13.3 px | 26.7 px |
| 10 cm/s | 53.3 | 10.7 px | 26.7 px | 53.3 px |
| 20 cm/s | 106.7 | 21.3 px | 53.3 px | **106.7 px — exceeds the 60 px association limit, track BREAKS** |

A broken track resets `track_length_frames` to 1, and lane C's 2026-08-29 fix makes
persistence a **precondition** for the DARK label. So a hand moving a toy boat too
quickly does not merely look jittery — **it silently makes that vessel ineligible for
the verdict the demo is trying to show.**

**Operational rule for the demo: move the models slowly, ≤ 5 cm/s.** Measure the FPS
first, then set the hand speed from the table above. This belongs on the run sheet.

---

## 6. What has been verified, and where

`99_scratch/lane_f_edge_check.py` — **42 checks, 41 passing**, run in the cloud container
(Linux, python 3.11.15, pydantic 2.13.3, cv2 4.13.0) with picamera2/ultralytics/fastapi/
uvicorn/torch stubbed. NOT run on the Mac and NOT on the Pi.

Confirmed by execution, not by reading:
- the node's payload validates as a real `EoContact`, over 12 yaw/position combinations;
- **range does not move when box width changes** — the check that proves apparent size
  has not leaked into range, which is what would silently disable the length test;
- `observed_class` stays `None` while `detection_confidence` carries the YOLO score;
- an object above the horizon yields `observed_range_m = None` rather than a guess, and
  is still a valid bearing-only contact;
- an injected `claimed_mmsi` is rejected by the contract — the identity wall holds;
- the geometry helpers are `pi_sensor`'s own objects, so there is exactly **one** copy of
  the bearing maths on the Pi;
- all six rows of `edge_client.reconcile()`'s diagnosis matrix.

**The one failing check is a real defect in lane D's file, not in this lane** — see §7.

---

## 7. P0 found while building this — the classical fallback is currently broken

`04_demo/pi_sensor.py`'s `to_contact()` does not emit `detection_confidence`, which
became a **required** field of `EoContact` on 2026-08-29. Measured:

```
EoContact(**pi_sensor.to_contact(...))
  -> 1 validation error for EoContact
     detection_confidence  Field required [type=missing]
```

Consequence: `server.py` returns **HTTP 400 on every POST** from the classical node, so
`pi_sensor.py` cannot currently deliver a single contact. That matters more than it
looks — the whole fallback plan in §3 assumes pi_sensor works. Filed to lane D as P0 in
`99_scratch/requests.md`.

---

## 8. Open

1. Everything marked UNMEASURED above, and all of `00_brief/HARDWARE.md`.
2. `camera_pose_TABLETOP.json` still has `hfov_deg: 0.0` and `height_m: 0.0`. The node
   **refuses to start** on a zero HFOV, by design. Survey it (two known bearings at
   opposite frame edges) before any of this runs.
3. `ultralytics` pulls torch — a ~200 MB aarch64 install. **Do it before the venue.**
4. NCNN/ONNX export is the known large speedup for YOLO on a Pi and is deliberately not
   attempted here. If §2 comes back unusable and there is time, that is the next lever.
