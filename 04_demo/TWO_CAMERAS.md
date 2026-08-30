# TWO_CAMERAS.md — the second camera, the overlap, and what the map now shows

**2026-08-29. Lane G wired into the console.** `triangulate.py` and `multiview.py` carry
the maths; `server.py` registers the poses and publishes the fixes; the tactical picture
draws them.

---

## 1. Run it

```bash
# build camera B's view of the SAME vessels (once)
python3 04_demo/make_second_view.py --crossing-deg 55

# T1 — recorded picture, both cameras on the map, four cross-fixes
python3 03_src/main.py --video 04_demo/out/scene01/scene.mp4 --loop \
    --scene-b 04_demo/out/scene01b
```

For the **live** path — boxes that follow the ships — two nodes and `--live`:

```bash
python3 03_src/main.py --live --scene-b 04_demo/out/scene01b \
    --video 04_demo/out/scene01/scene_detector.mp4 --loop \
    --pose 04_demo/out/scene01/camera_pose_SCENE.json --scale 1

# in a second terminal — camera B. NO --publish-frames: one node owns the video pane.
python3 04_demo/pi_sensor.py --source 04_demo/out/scene01b/scene_detector.mp4 --loop \
    --pose 04_demo/out/scene01b/camera_pose_SCENE.json --scale 1 \
    --node-id sensor-02 --min-area 30 --interval 0.5 \
    --post http://<lan-ip>:8000/api/contacts
```

---

## 2. Reading the map

| Mark | Means |
|---|---|
| Grey wedge, solid | Camera A's declared field of view |
| Pink wedge, dashed | Camera B's |
| **Teal region** | **The overlap.** Only here can a hull be cross-fixed |
| Dotted pink line | The baseline between the cameras — it sets the crossing angle |
| Bright orange quad | A contact ONE camera sees. Long, because monocular range is bad |
| Faint brown dashed quad | The same quad after a cross-fix superseded it — kept, receded |
| Teal ellipse | The two-camera fix, 1σ |
| Small hull glyph inside it | The hull, drawn to its **camera-observed** length on the observed heading, coloured by verdict |

The whole argument is visible without a word: **long orange slivers outside the overlap,
small teal ellipses inside it.**

### Why the mark is not a ship-sized rectangle

The fix ellipse is ~275–340 × 130–195 m (1σ). A large tanker is 250–350 m; most vessels
are 100–200 m. **The uncertainty is still larger than the ship.** A hull-sized box at the
fix would assert "the vessel is exactly here and this is its outline" — the same overclaim
the monocular quad exists to avoid, one step further along. If the fix ever gets tighter
than a hull length, revisit.

What IS drawn is a hull **glyph** to the camera-observed length, inside the ellipse:
glyph = the hull, ellipse = where it might be, faint quad = what one camera alone knew.
That is how a tactical display is read everywhere.

**Observed length, never claimed.** Claimed length is the field a length-spoof lies about.
Drawing the map's hull from it would walk a claim across the claimed/observed wall and, on
exactly the contacts this tool exists to catch, draw the lie to scale.

---

## 3. How a pair is accepted, and why the gates are layered this way

| Gate | Kind | Why |
|---|---|---|
| Rays cross in front of both cameras at a workable angle | hard | `triangulate()`'s own refusals |
| Frame times within 5 s | hard | A 14-knot hull covers 430 m in a minute — further than the ellipse. Two bearings from different moments cross where no hull ever was, and the fix comes out *small and confident* |
| Crossing point inside **both declared wedges** | hard | Position, boresight and FOV are **surveyed**, not estimated. Bearing slack is the pose's own `yaw_uncertainty_deg`, so a careless rig does not borrow a careful one's tolerance |
| Range cross-check | **soft** — ranks, does not reject | See below |
| One-to-one assignment | — | `scipy.optimize.linear_sum_assignment`, per LIBRARIES.md |

### Why the range cross-check had to stop being a gate

MEASURED, two live nodes (`99_scratch/diag_pairs` run):

| pair | crossing | range cross-check |
|---|---|---|
| sensor-01-1 + sensor-02-2 | 69° | 10.5 σ |
| sensor-01-1 + sensor-02-3 | 47° | 8.0 σ |
| sensor-01-5 + sensor-02-2 | 50° | 17.3 σ |

Every pair crossed cleanly; every pair failed a 3σ range check, so the live path produced
**zero** fixes while the recorded path produced four at 0.8–1.3σ.

That is not a bug in the gate. From 25 m above the water a vessel 4–10 km out sits **4–10
pixels** below the horizon, so one pixel is a 10–25% range error. Gating on monocular range
makes triangulation unavailable exactly where it is most needed — **which is the argument
for the second camera, arriving as a bug report.** The check now ranks candidates, and any
fix above 3σ carries the disagreement in its own `limitations`.

---

## 4. A defect this surfaced in the renderer

`pi_sensor.find_horizon()` returned **456.00 on every frame** against a horizon
`make_scene_video.py` drew at 453 — a **+3.00 px bias with zero variance**. The cause was
the renderer: a 3-px anti-aliased line has its steepest gradient at its *lower edge*.

At 4–10 px of depression, 3 px shortens every range by tens of percent, in one direction,
on every contact — the exact signature seen downstream (2865–5730 m recovered against
truths of 3815–10088 m, always short). A real horizon is a step, not a stripe; the line is
now 1 px and the bias is **+1.00 px**.

---

## 5. Does the tracking rectangle follow the ships?

**On the live path, yes — measured.** Two nodes running, boxes sampled every 2 s:

| contact | x₀ over six samples | moved |
|---|---|---|
| sensor-01-1 | 1056 → 978 → 919 → 841 → 782 → 704 | 352 px |
| sensor-02-2 | 1179 → 1291 → 1386 → 1514 → 1609 → 1736 | 557 px |
| sensor-01-5 | 1543 → … → 1668 | 125 px |

**5 of 6 boxes tracked, with stable contact ids** — the same track following the hull, not
a new detection each frame.

**On the recorded path, no — and it cannot.** `04_demo/out/scene01` is **one time
instant**: its contacts have no later position to move to. A box that moved there would be
an animation, not a track. That is why `make_scene_video.py --for recorded` draws the hulls
static: so the boxes land on them. Use `--for detector` + `--live` when you want tracking.

---

## 6. Measured

| Check | Result |
|---|---|
| `check_triangulate.py` | PASS — solver against hand-computed geometry |
| `lane_g_two_camera_check.py` | PASS — **617 m two-camera median error vs 1138 m one-camera, 1.8×**; ellipse aspect 1.89 against a 6:1 monocular quad |
| `lane_g_console_check.py` | PASS — real uvicorn: sensors published primary-first, every fix inside both declared wedges, `fix_by_contact` keyed both ways, time gate, one-camera empty case |
| `lane_video_check` · `lane_video_e2e` · `lane_f_failover_check` | PASS |
| `pytest tests/` | 145 passed, 1 skipped |

Recorded path: **4 fixes, crossings 61–71°, ellipses 275×131 to 337×195 m, cross-check
0.81–1.3σ.** Live two-node path: 1–2 fixes, crossings 50–55°, ellipses ~400–480 × 190–220 m.

**UNVERIFIED:** none of this has run on macOS with the project venv — all of it ran in the
Linux sandbox and a headless Chromium.

---

## 7. Glossary

**baseline** the line between two camera positions; with the target it sets the crossing
angle · **crossing angle** angle between two bearings at the fix; near 90° gives a circular
ellipse, near 0° a sliver · **cross-fix** a position constructed from two or more bearings ·
**FOV** field of view · **σ (sigma)** one standard deviation · **range cross-check** the fix
range minus a station's own monocular range, in units of that station's range sigma
