# handoff_F.md — Lane F, 2026-08-29 (session 2: the edge node)

## Built

| File | Lines | What |
|---|---|---|
| `edge/sensor_node.py` | 708 | Pi node: picamera2 + YOLO(COCO `boat`) + EoContact + POST + MJPEG `/stream` + `/health` |
| `edge/bench_edge.py` | 320 | measurement harness: imgsz sweep, 5-min soak, temps, throttle, detection rate |
| `edge/sync_to_pi.sh` | — | rsync-over-ssh from the Mac, with the exclude list that keeps 894 MB off the SD card |
| `edge/requirements-edge.txt` | — | the edge subset only; apt vs pip split spelled out |
| `03_src/edge_client.py` | 453 | Mac-side CLIENT of the node (not a second server — see below) |
| `04_demo/edge_benchmark.md` | 211 | method + the frame-rate argument; all Pi numbers UNMEASURED |
| `99_scratch/lane_f_edge_check.py` | 263 | 42 checks; 41 pass, 1 fails on a real lane D defect |

## Verified by execution (cloud container: python 3.11.15, pydantic 2.13.3, cv2 4.13.0)

picamera2 / ultralytics / fastapi / uvicorn / torch stubbed through `sys.modules`, which
makes every non-hardware path testable on a laptop. **41 of 42 checks pass.** The
valuable ones:

- the node's payload validates as a real `EoContact` across 12 yaw/position combinations;
- **box width does not move range** — proves apparent size has not leaked into the range
  estimate, which is the failure that would silently disable the length check forever;
- `observed_class` stays `None` while `detection_confidence` carries the YOLO score;
- above the horizon, range is `None` rather than a guess, and the contact is still valid;
- an injected `claimed_mmsi` is rejected — the identity wall holds on the wire;
- the four geometry helpers ARE `pi_sensor`'s objects, so one copy of the bearing maths;
- all six rows of `reconcile()`'s diagnosis matrix.

**NOT verified:** anything needing the Pi. No picamera2 open, no YOLO load, no FPS.

## The two design decisions worth knowing

**1. `edge_client.py` is a client, not a second server** (your call this session).
`server.py` already owns ingest and liveness. Duplicating it would be the
`PriorityScore.factors` mistake again: two sources of truth, silent divergence. So this
polls the node's `/health` from the Mac — the *other* direction — and adds the one case
server.py structurally cannot see:

> A Pi that is powered, detecting and streaming, but whose POSTs are not arriving, is not
> "offline" to server.py. It is **absent from `ConsoleState.nodes` entirely** — no red
> strip, no stale timestamp, nothing. The operator sees an empty queue and reads it as an
> empty sea.

`reconcile()` resolves the two views into HEALTHY / UP-BUT-NOT-POSTING / PORT-BLOCKED /
DOWN / NEVER-HEARD-FROM, each with a `suggested_action`. `POLL_STALE_AFTER_S` is pinned to
server.py's 30 s so the two views cannot disagree about staleness.

**2. YOLO and classical both survive.** `sensor_node.py` does not replace `pi_sensor.py`.
COCO `boat` may not fire on a printed silhouette — recorded in pi_sensor's own docstring
as the one risk that could kill the live segment. `edge_benchmark.md` §3 makes the switch
criterion explicit and forbids the tempting wrong move (lowering `--conf` until something
appears, which manufactures detections out of background texture).

## P0 found while building: the fallback is currently broken

`pi_sensor.to_contact()` omits `detection_confidence`, required since 2026-08-29. Measured:
`EoContact(**...)` → `detection_confidence Field required`. So `server.py` returns **HTTP
400 on every POST from the classical node**. Filed to lane D. The regression guard in
`lane_f_edge_check.py` fails on purpose until it is fixed.

## What Amol has to run (the agent cannot reach the Pi)

```bash
bash edge/sync_to_pi.sh pi@<pi-host>            # Mac
# Pi, once:
sudo apt install -y python3-picamera2 python3-opencv python3-numpy
pip3 install --break-system-packages -r edge/requirements-edge.txt
# Pi, test object in frame:
python3 edge/bench_edge.py --mode sweep
python3 edge/bench_edge.py --mode soak
# live:
python3 edge/sensor_node.py --pose 04_demo/camera_pose_TABLETOP.json \
    --post http://<mac-ip>:8000/ingest/contacts --node-id edge-pi-01 --scale 20
# Mac:
python3 03_src/server.py --host 0.0.0.0 --mjpeg-url http://<pi-ip>:8080/stream
python3 03_src/edge_client.py --node http://<pi-ip>:8080 --server http://127.0.0.1:8000 --watch
```

Also run `python3 99_scratch/lane_f_edge_check.py` on the Mac — it has never executed there.

## Blocked / open

1. **`camera_pose_TABLETOP.json` still has `hfov_deg: 0.0`.** `sensor_node.py` refuses to
   start, by design. This is a SURVEY task, not a probe output, and it now blocks two nodes.
2. All of `00_brief/HARDWARE.md` §§1–4 — the probes from session 1 were never run.
3. `ultralytics` pulls torch, ~200 MB on aarch64. **Install before the venue.**
4. The demo run sheet needs "move the models ≤ 5 cm/s" — at 20 cm/s and low FPS the
   tracker's 60 px association limit breaks, which resets `track_length_frames` and makes
   the vessel ineligible for DARK. See `edge_benchmark.md` §5.
5. Lane F still absent from `FILE_OWNERSHIP.md`; add `edge/` and `03_src/edge_client.py`.
6. `.gitignore` has no rule for `04_demo/edge_benchmark.json` (bench_edge.py's output).
