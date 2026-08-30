# RUNBOOK.md — running the whole thing

**Every command verified 2026-08-29. Run from the repo root.**

---

## 0. Once, before anything

```bash
cd ~/Desktop/MaritimeApproaches
source .venv/bin/activate

# uvicorn does NOT bring a WebSocket transport with it. Without one it answers 404 to
# /ws — indistinguishable from a missing route — and the console silently drops to
# 2-second polling with a WEBSOCKET DOWN banner.
python -c "import websockets" 2>/dev/null || pip install websockets

# Everything else, checked and reported in one table. Writes nothing, starts nothing.
python 03_src/main.py --preflight-only
```

Every row must read `OK`, or be a `FAIL` you have read and accepted. The one non-fatal
row is the WebSocket transport.

---

## 1. Build the scene assets

```bash
# Camera A: two cuts of the same scene. They are NOT interchangeable — see §5.
python3 04_demo/make_scene_video.py                    # recorded cut  -> scene.mp4
python3 04_demo/make_scene_video.py --for detector \
        --out 04_demo/out/scene01/scene_detector.mp4   # detector cut

# Camera B: the SAME vessels from a second pose, 55 deg around the traffic centroid.
python3 04_demo/make_second_view.py --crossing-deg 55  # -> 04_demo/out/scene01b/
```

Afterwards you should have:

```
04_demo/out/scene01/scene.mp4               04_demo/out/scene01/scene_detector.mp4
04_demo/out/scene01/camera_pose_SCENE.json  04_demo/out/scene01b/scene_detector.mp4
                                            04_demo/out/scene01b/camera_pose_SCENE.json
```

---

## 2. T1 — the demo. One command.

```bash
python3 03_src/main.py \
    --video 04_demo/out/scene01/scene.mp4 --loop \
    --scene-b 04_demo/out/scene01b
```

Console opens at `http://127.0.0.1:8000/`.

**No `--pose`, no `--live`.** No sensor node runs; the recorded scene *is* the picture and
`--video` only supplies imagery. You get the full ranked queue — **2 SPOOF · 2 MATCH ·
2 DARK · 2 UNKNOWN** over 8 contacts and 12 AIS claims — plus both camera wedges, the
overlap and **4 cross-fixes** (crossings 61–71°, ellipses 275×131 to 337×195 m).

> The stored `04_demo/out/scene01/ranked.json` says 3 SPOOF / 2 DARK / 2 UNKNOWN /
> 1 MATCH. That file is a **snapshot from an older pipeline run**, not what the server
> computes today; the live figure above is what you will see. If the two matter to you,
> regenerate the snapshot — do not quote the file.

Press **v** for the operator layout (big map, video as picture-in-picture).

### A richer picture

```bash
python3 04_demo/make_scene_video.py --scene 04_demo/out/scene02
python3 03_src/main.py --scene 04_demo/out/scene02 \
    --video 04_demo/out/scene02/scene.mp4 --loop
```

12 records — 5 MATCH · 3 DARK · 2 SPOOF · 2 UNKNOWN. (No second camera built for
scene02 yet; `make_second_view.py --scene 04_demo/out/scene02 --out …` would.)

---

## 3. Live — two cameras, boxes that follow the ships

**Terminal 1**

```bash
python3 03_src/main.py --live \
    --scene-b 04_demo/out/scene01b \
    --video 04_demo/out/scene01/scene_detector.mp4 --loop \
    --pose 04_demo/out/scene01/camera_pose_SCENE.json --scale 1
```

It prints `SENSOR POSTS http://<lan-ip>:8000/api/contacts`. Use that address below.

**Terminal 2** — camera B. **No `--publish-frames`:** one node owns the video pane, or it
alternates between two viewpoints and the boxes belong to whichever arrived last.

```bash
source .venv/bin/activate
python3 04_demo/pi_sensor.py \
    --source 04_demo/out/scene01b/scene_detector.mp4 --loop \
    --pose 04_demo/out/scene01b/camera_pose_SCENE.json --scale 1 \
    --node-id sensor-02 --min-area 30 --interval 0.5 \
    --post http://<lan-ip>:8000/api/contacts
```

Measured on this path: **5 of 6 boxes track with stable contact ids** (352 px, 557 px,
125 px over six 2-second samples), and 1–2 live cross-fixes appear at 50–55° crossings.

The live picture is **thinner** than the recorded one — a background subtractor on a short
clip finds 2–3 hulls and no identities. That is the honest trade: motion, or a full ranked
picture. Switch source in the console at any time.

---

## 4. Verify without guessing

```bash
# which video source is live, and whether the boxes belong to the frame under them
curl -s localhost:8000/health | python3 -m json.tool | grep -A6 '"source"'

# sensors, cross-fixes, verdict mix
curl -s localhost:8000/api/state | python3 -c "
import json,sys; from collections import Counter
d=json.load(sys.stdin)
print('sensors:',[(p['pose_ref'],p['role']) for p in d.get('sensors',[])])
print('records:',len(d.get('records',[])),
      Counter((r.get('verdict') or {}).get('label') for r in d.get('records',[])))
for f in d.get('fixes',[]):
    print(f'  fix {f[\"contact_ids\"]} cross {f[\"crossing_angle_deg\"]:.0f}d '
          f'{f[\"sigma_major_m\"]:.0f}x{f[\"sigma_minor_m\"]:.0f} m')"
```

---

## 5. If something looks wrong

| Symptom | Almost certainly | Do |
|---|---|---|
| Video looks frozen | **The recorded cut is static by design.** Hulls sit still so the boxes land on them; the scene is one time instant. Only the burnt-in `frame NNNN / 360` counter moves. | If the counter is advancing, it is fine. For motion use `scene_detector.mp4` + `--live` (§3). |
| `NO VIDEO SOURCE CONFIGURED` | No `--video` and no node publishing frames | Add `--video`, or start a node with `--publish-frames` |
| Boxes do not move | No `--live`, so no detector is running | §3 |
| Boxes sit off the hulls | The recorded scene's boxes over a *detector* clip, or vice versa | Match the cut to the mode: `scene.mp4` without `--live`, `scene_detector.mp4` with it |
| `WEBSOCKET DOWN`, polling every 2 s | `websockets` not installed | `pip install websockets` (§0) |
| Only 2 contacts, both DARK | You are on the live path — that is what a blob detector finds | Drop `--live` for the full recorded picture |
| Map blank / `MAP LIBRARY UNAVAILABLE` | Should not happen — there is an offline SVG backend | Check the browser console; report it |
| Node exits: `hfov_deg is 0` | The default pose is an unfilled template | Pass `--pose 04_demo/out/scene01/camera_pose_SCENE.json` |

---

## 6. The test battery

```bash
for t in check_triangulate lane_g_two_camera_check lane_g_console_check lane_video_check lane_video_e2e lane_f_failover_check; do
  printf "  %-30s" "$t"
  if python3 99_scratch/$t.py >/tmp/$t.log 2>&1 && grep -q "ALL CHECKS PASSED" /tmp/$t.log; then
    echo PASS
  else
    echo "FAIL — tail -40 /tmp/$t.log"
  fi
done
python3 -m pytest -q tests/
```

Expected: six PASS, then `163 passed`.

If your Python cannot import `movingpandas` you will see `145 passed, 18 skipped`
instead — the whole of `tests/test_ais_trajectory.py` skips itself, which is why
this file's expected count was wrong until 2026-08-30. A skipped file is not a
passing file. Install the geo stack before trusting a green run:
`pip install movingpandas geopandas shapely`.

> **Two defects fixed here on 2026-08-30, both mine, both found by running it on the Mac.**
>
> 1. The loop used `timeout`, which **does not exist on macOS** — it is GNU coreutils, and
>    it was written and tested on Linux. `timeout` was not found, the `&&` chain
>    short-circuited, and **all six checks reported FAIL without ever running.** If you
>    want a timeout on macOS: `brew install coreutils` gives you `gtimeout`.
> 2. Every check wrote to the same `/tmp/o.txt`, so after the loop that file held only the
>    LAST check's output. A failure pointed you at the wrong log. Each check now writes its
>    own.
>
> Also: pasting a line that starts with `#` into an interactive zsh gives
> `command not found: #` — zsh does not treat `#` as a comment interactively unless you
> `setopt interactive_comments`. The blocks in this file carry no inline comments for that
> reason.

---

## 7. Console keys

| Key | Does |
|---|---|
| **v** | console ⇄ operator layout |
| **m** | minimise / expand the picture-in-picture (stream keeps running) |
| **r** | slide the queue + evidence rail away |
| **l** | fold the legend |
| **↑ ↓** | move down the priority queue |
| **1–9** | jump to that rank |

---

## 8. Before it is in front of anyone

- Identities are pseudonymised on every outbound route. **Leave `--allow-real-identities`
  off.**
- Everything on screen is synthetic and says so — the scene caveat, the watermark on the
  imagery, the `SYNTHETIC · NO CAMERA` badge on every row.
- The honest sentence is *"the detector and the fusion, running on real recorded AIS at
  demo scale"*. Never *"our sensor watching the Elbe"*.
