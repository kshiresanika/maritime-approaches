# HARDWARE.md — Lane F

**Created 2026-08-29. Status: SCAFFOLD + THREE FINDINGS. All hardware measurements are UNMEASURED.**

Owner: lane F (hardware / sensor node). Subject: the Raspberry Pi that runs
`04_demo/pi_sensor.py`, its camera, and the link to the Mac shore station.

---

## 0. Why every number below says UNMEASURED

The agent cannot reach the Pi. Its shell on the Mac is a sandboxed Linux VM with no
network egress (see `env_execution_constraints` in project memory), and the cloud
container is a different machine again. The Pi is on neither path.

The project rule is **MEASURED NUMBERS ONLY**, so nothing is filled in by inference from
a model name. Two probes were written instead:

| Script | Runs on | Produces |
|---|---|---|
| `99_scratch/probe_pi_mac.sh` | the MacBook | `MAC_IP`, the shore-station bind address, firewall state |
| `99_scratch/probe_pi.sh` | the Raspberry Pi | sections 1–4 of this document |

Order: Mac script → Pi script (with `MAC_IP`) → Mac script again (with `PI_IP`).

---

## 1. Identity — UNMEASURED

| Field | Value | Source once run |
|---|---|---|
| Pi model | **UNMEASURED** | `/proc/device-tree/model` |
| Board revision | **UNMEASURED** | `/proc/cpuinfo` |
| Cores / RAM | **UNMEASURED** | `nproc`, `/proc/meminfo` |
| OS pretty name | **UNMEASURED** | `/etc/os-release` |
| OS codename | **UNMEASURED** | `VERSION_CODENAME` |
| Kernel | **UNMEASURED** | `uname -r` |
| Kernel arch | **UNMEASURED** | `uname -m` |
| Userland word size | **UNMEASURED** | `getconf LONG_BIT` |
| python3 path + version | **UNMEASURED** | `command -v python3`, `python3 -V` |

**Why the OS codename is load-bearing, not trivia.** Bookworm and later drive the CSI
camera through libcamera and ship `picamera2`. Bullseye and earlier default to the legacy
firmware stack, where `picamera2` may be absent and the deprecated `picamera` v1 is what
is installed. The codename therefore decides whether the brief's mandated capture path
exists at all. If it comes back as Bullseye or older, stop and re-plan the capture
backend rather than installing v1.

**Why both arch fields are recorded.** A Pi can run a 64-bit kernel under a 32-bit
userland. `uname -m` reports the kernel and will say `aarch64` while every wheel `pip`
resolves is `armv7l`. The failure mode is a wheel that installs cleanly and then fails to
import — a packaging fault wearing the costume of a code fault. `getconf LONG_BIT` is the
authority for userland; the probe prints both and warns when they disagree.

---

## 2. Camera — UNMEASURED

| Field | Value |
|---|---|
| libcamera CLI present | **UNMEASURED** (`rpicam-hello` on Bookworm, `libcamera-hello` on Bullseye) |
| `--list-cameras` output | **UNMEASURED** |
| `picamera2` imports | **UNMEASURED** |
| `picamera2` version | **UNMEASURED** |
| Sensor model reported | **UNMEASURED** |
| `PixelArraySize` | **UNMEASURED** |
| Still captured to disk | **UNMEASURED** → `~/lane_f_probe/still_default.jpg` |
| Lens HFOV (`hfov_deg`) | **UNMEASURED — AND NOT OBTAINABLE FROM THE PROBE.** See §2.2 |

The probe takes **two independent witnesses**: the libcamera CLI (what the OS believes is
attached) and `picamera2` (what our code will actually use). They must agree. CLI sees a
camera, `picamera2` does not → Python packaging fault. Neither sees one → ribbon cable or
seating. Conflating the two costs an hour.

The probe checks `hasattr(Picamera2, "global_camera_info")` before calling it, and reads
`sensor_modes` with `getattr`, rather than assuming the API. Per the standing rule, an
API that could not be read from the docs here is introspected at runtime instead of
guessed.

### 2.1 The still must be checked by eye

A JPEG of plausible size can still be black. A black frame makes MOG2 report a perfectly
quiet scene — the detector runs, posts nothing, and looks healthy. The probe prints
`ExposureTime`, `AnalogueGain` and `Lux` alongside the byte count for exactly this
reason, and sleeps 2 s before capture so AE/AWB have settled.

### 2.2 `hfov_deg` is a survey task, not a probe output

`04_demo/camera_pose_TABLETOP.json` currently has `"hfov_deg": 0.0`, and
`pi_sensor.py:341` refuses to start until it is non-zero. **The probe cannot fill it.**
A datasheet HFOV is for the full sensor array; the actual frame HFOV depends on the crop
and scale of the mode in use, which §3 shows can change silently.

Measure it the way the code's own error message says: put two references whose bearings
you know at opposite frame edges; the difference **is** the horizontal field of view.
Re-measure after **any** resolution change (§3, Finding F-3).

`yaw_uncertainty_deg` stays at 12.0 unless the boresight is surveyed against a known
reference. It floors every bearing sigma and therefore caps every SPOOF confidence.

---

## 3. Resolution — DERIVED, PICK PENDING

The brief asks for the smallest resolution that still lets a vessel-sized object be
detected. That is two questions with two different answers, and only one can be settled
without hardware.

### 3.1 The detection floor — derived from `pi_sensor.py`'s own constants

`ClassicalDetector` requires a connected component of `min_area_px = 120`. For a
silhouette of aspect ratio `A`, area ≈ w²/A, so the minimum blob **width** is
`w = sqrt(120·A)`:

| Aspect A | 2.0 | 2.8 | 4.0 | 6.0 |
|---|---|---|---|---|
| min width px | 15.5 | **18.3** | 21.9 | 26.8 |

Working floor **k = 18.3 px** (A = 2.8, the aspect quoted in `pi_sensor.py`'s docstring).

Frame width needed, `W = k · 2·tan(HFOV/2) · d / L`, for camera-to-silhouette distance
`d` and silhouette length `L`:

| HFOV | d/L=5 | d/L=10 | d/L=15 | d/L=20 | d/L=30 |
|---|---|---|---|---|---|
| 66° | 119 | 238 | 357 | 476 | 714 |
| 75° | 141 | 281 | 422 | 563 | 844 |
| 102° | 226 | 453 | 679 | 905 | 1358 |
| 120° | 317 | 635 | 952 | 1270 | 1905 |

### 3.2 The floor is not the operating point, and this is the important part

At the floor the silhouette is ~18 px wide. `04_demo/make_synthetic_eo.py` places
**clutter at 6–22 px apparent width**, and a real 12 px vessel was deliberately given
`detection_confidence` 0.49 so the two bands overlap — that overlap is the honest
false-positive rate the scene is built to expose.

So a silhouette at the detection floor lands **inside the clutter band**. Size can no
longer separate vessel from clutter, and the only remaining separator is persistence
(`frames >= 2`) — which the 2026-08-29 fix made a *precondition* for the DARK label
rather than a confidence penalty. Running at the floor therefore does not merely make
detection marginal; it removes a discriminator the verdict logic depends on.

**Operate at ~3k ≈ 55 px**, which puts the silhouette clear of the clutter band:

| HFOV | d/L=5 | d/L=10 | d/L=15 | d/L=20 | d/L=30 |
|---|---|---|---|---|---|
| 66° | 357 | 714 | 1071 | 1428 | 2143 |
| 75° | 422 | 844 | 1266 | 1688 | 2532 |
| 102° | 679 | 1358 | 2037 | 2716 | 4074 |
| 120° | 952 | 1905 | 2857 | 3810 | 5715 |

### 3.3 The conclusion that actually matters: fix the geometry, not the sensor

Read the tables by column, not by row. Resolution is the **expensive** lever — MOG2 cost
is per-pixel, so 640→1280 is ~3× the work and cuts frame rate accordingly. `d/L` is the
**free** lever.

- 100 mm silhouette at 1.5 m → d/L = 15 → needs **1071 px** at 66°. 640 fails.
- 200 mm silhouette at 1.0 m → d/L = **5** → needs **357 px** at 66°. 640 passes with
  1.8× margin, at a quarter of the pixel cost.

**Recommendation: print larger silhouettes and move the camera closer before increasing
resolution.** Provisional pick **640×480** — which is already `pi_sensor.py`'s default,
so no argument change — *conditional on holding d/L ≤ 5–8 and the lens being ~66–75°.*
If the camera is a Wide variant (~102–120°), 640 is insufficient at any sensible table
distance and the geometry must absorb it or the resolution must go to 1280×720.

Lowering `min_area_px` is the third lever and is **not recommended**: it lowers k, but it
lowers the clutter rejection threshold by exactly the same amount, so it buys detection
range by buying false positives. It is also lane D's file.

**PENDING:** the pick is not final until (a) §2.2 gives a measured HFOV, (b) the table
geometry fixes `d` and `L`, (c) `probe_pi.sh` §3 returns the measured MOG2 frame rate at
320/640/1280. The probe benchmarks with the same `createBackgroundSubtractorMOG2` +
`connectedComponentsWithStats` calls `pi_sensor.py` uses, so the number transfers.

| Resolution | Measured FPS (capture + MOG2 + CC) |
|---|---|
| 320×240 | **UNMEASURED** |
| 640×480 | **UNMEASURED** |
| 1280×720 | **UNMEASURED** |

> The README's claim of "30 FPS on a Raspberry Pi" for classical CV is **not a measured
> number for this Pi**. It must not appear on a slide until the table above is filled.

---

## 4. Network — UNMEASURED

| Field | Value |
|---|---|
| Pi hostname / mDNS | **UNMEASURED** |
| Pi IPv4 (all interfaces) | **UNMEASURED** |
| Mac IPv4 (default interface) | **UNMEASURED** |
| Transport actually in use | **UNMEASURED** |
| Pi → Mac ICMP | **UNMEASURED** |
| Pi → Mac TCP 8000 | **UNMEASURED** |
| Mac → Pi ICMP | **UNMEASURED** |
| macOS firewall state | **UNMEASURED** |

**"Which transport" is answered by `ip route get <MAC_IP>`, not by the wifi SSID.** The
kernel is asked which interface it would use *for that specific destination*. A Pi can be
associated to wifi and still route to the Mac over ethernet; reading the SSID would
report the wrong answer confidently.

ICMP and TCP are tested separately and neither is allowed to stand in for the other:
macOS can drop ICMP while accepting TCP, so a failed ping is not proof of no link, and a
successful ping is not proof the POST will arrive.

**If Pi and Mac cannot see each other, breakout HW-N1 runs before anything else.** Per
the brief, that is a full stop, not a note.

---

## FINDINGS — establishable without the Pi

### F-1 — P0, DEMO KILLER. The shore station is bound to loopback.

`04_demo/app.py:169` — `p.add_argument("--host", default="127.0.0.1")`.

127.0.0.1 is loopback. It is not reachable from another machine under **any** network
configuration. `pi_sensor.py`'s POST to `http://<mac-ip>:8000/api/contacts` will fail,
and it will fail as a connection refused or timeout — indistinguishable at the Pi from a
cable fault, a wrong IP, or a firewall. Cost: an hour of debugging the network, on the
demo floor, for a one-word cause.

**No code change is needed — the argument already exists.** It is an operational fact
that must reach the run sheet:

```
python3 04_demo/app.py --host 0.0.0.0 --port 8000
```

Corollary: the first non-loopback bind triggers the macOS "allow incoming connections"
dialog. Dismissed or missed, every POST is dropped silently. `probe_pi_mac.sh` reports
firewall state for this reason. **Accept that dialog before the demo, not during it.**

### F-2 — P0, contradicts the Lane F brief. `pi_sensor.py` uses `cv2.VideoCapture` for the CSI camera.

`04_demo/pi_sensor.py:349` — `cap = cv2.VideoCapture(src)` with `--source` defaulting to
`"0"`. The Lane F brief mandates `picamera2` and forbids exactly this.

Cause and effect, and the second case is the dangerous one:

1. **No USB camera attached.** On Bookworm the CSI sensor is driven by libcamera and is
   not reliably exposed as a plain V4L2 capture node. `cv2.VideoCapture(0)` opens nothing
   and `pi_sensor.py` exits at line 353 with `could not open source 0`. Loud, immediate,
   cheap.
2. **A USB webcam is attached.** Index 0 may resolve to the **webcam**, not the CSI
   module. The sensor then runs perfectly — while computing every bearing from the
   `hfov_deg` surveyed for the *other* lens. Every bearing is wrong, no error is raised,
   and the association stage receives a confidently mis-pointed observation. This is a
   silent-corruption failure of the same class as the dd/mm/yyyy and duplicate-reception
   traps already in the ledger.

Lane F does not own `04_demo/`. **Filed to lane D in `99_scratch/requests.md`** with the
suggested shape: a `--backend {picamera2,cv2}` selection defaulting to `picamera2` on a
Pi, keeping the `cv2` path for video files and the Mac. Not written here — this session
writes no pipeline code.

### F-3 — Silent trap. Sensor mode is not output size, and choosing wrongly changes the field of view.

`picamera2` answers "supported resolutions" two different ways:

- **`sensor_modes`** — the raw modes the sensor supports. Selecting a narrower one can
  **crop** the array.
- **`main={"size": (w,h)}`** — the output stream size. The ISP scales into it, and it is
  largely arbitrary and independent of the mode.

Downscaling a full-array mode preserves the field of view. Selecting a cropped sensor
mode **narrows the HFOV** — and `hfov_deg` is a single scalar in the pose file that
floors every bearing sigma and caps every SPOOF confidence. Change resolution by cropping
and every bearing shifts, with nothing raising an error.

**Rule for lane F:** pin the widest full-array sensor mode, vary only the `main` stream
size, and **re-survey `hfov_deg` after any resolution change.** The probe's §3 benchmark
is written this way — it sets `main` and never selects a mode — and it prints the actual
returned array shape so a silent ISP substitution is visible rather than assumed.

---

## OPEN — lane F

1. Every measurement in §§1–4. Blocked on running the two probes.
2. `hfov_deg` and `height_m` in `camera_pose_TABLETOP.json` are still `0.0`. `pi_sensor.py`
   refuses to start until `hfov_deg` is set. Survey task, not a probe output (§2.2).
3. F-2 is filed to lane D and unresolved. Until it is, run `pi_sensor.py` with **no USB
   camera attached**, so failure mode 1 (loud) is guaranteed over failure mode 2 (silent).
4. Lane F has no entry in `FILE_OWNERSHIP.md`. Filed to ARCH.
5. Breakout HW-N1 is undefined. If §4 fails, there is no written procedure to fall back on.
