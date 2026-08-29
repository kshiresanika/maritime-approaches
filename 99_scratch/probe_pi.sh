#!/usr/bin/env bash
# =============================================================================
# probe_pi.sh — LANE F HARDWARE PROBE.  RUN THIS ON THE RASPBERRY PI.
# =============================================================================
#
# WHY THIS FILE EXISTS
# The agent cannot reach the Pi. Its shell on the Mac is a sandboxed Linux VM
# with no network egress, and the cloud container is a different machine again.
# So every number in 00_brief/HARDWARE.md has to be measured here and pasted
# back. This script is written to be SELF-INTERPRETING: it prints PASS / FAIL /
# UNKNOWN on its own so the pasted output needs no commentary to be read.
#
# USAGE
#   bash probe_pi.sh <MAC_IP> [MAC_PORT]
#   e.g.  bash probe_pi.sh 192.168.4.1 8000
# Get MAC_IP by running 99_scratch/probe_pi_mac.sh on the Mac FIRST.
# You may run without MAC_IP; section 4 will then only report the Pi's own side.
#
# DESIGN NOTE — WHY THERE IS NO `set -e`
# A probe must survive its own failures. If picamera2 is missing we still want
# the network answer. Each section is independently guarded; a section that
# fails prints FAIL and the next one still runs. `set -u` IS on, because an
# unset variable here would silently probe the wrong thing.
# =============================================================================
set -u

MAC_IP="${1:-}"
MAC_PORT="${2:-8000}"
OUTDIR="${HOME}/lane_f_probe"
mkdir -p "$OUTDIR"

line() { printf '\n=============================================================\n%s\n=============================================================\n' "$1"; }
kv()   { printf '  %-34s %s\n' "$1" "$2"; }

echo "LANE F PROBE  |  $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC  |  artefacts -> $OUTDIR"

# -----------------------------------------------------------------------------
# SECTION 1 — IDENTITY
# What we need and why: the Pi model sets the CPU budget for MOG2; the OS
# codename decides whether the camera stack is libcamera (Bookworm+) or the
# legacy stack (Buster/Bullseye), which decides whether picamera2 exists at all;
# the architecture decides which wheels install.
# -----------------------------------------------------------------------------
line "SECTION 1 — IDENTITY"

# /proc/device-tree/model is NUL-terminated. Without `tr -d '\0'` the shell
# appends a stray byte and the string compares wrong in any later script.
if [ -r /proc/device-tree/model ]; then
  kv "Pi model" "$(tr -d '\0' < /proc/device-tree/model)"
else
  kv "Pi model" "UNKNOWN (/proc/device-tree/model unreadable — not a Pi?)"
fi
kv "Board revision" "$(grep -m1 '^Revision' /proc/cpuinfo 2>/dev/null | awk '{print $3}' || echo UNKNOWN)"
kv "CPU cores" "$(nproc 2>/dev/null || echo UNKNOWN)"
kv "RAM total" "$(awk '/MemTotal/{printf "%.2f GB", $2/1048576}' /proc/meminfo 2>/dev/null || echo UNKNOWN)"

# shellcheck disable=SC1091
if [ -r /etc/os-release ]; then . /etc/os-release; fi
kv "OS PRETTY_NAME" "${PRETTY_NAME:-UNKNOWN}"
kv "OS VERSION_CODENAME" "${VERSION_CODENAME:-UNKNOWN}"
kv "Kernel" "$(uname -r)"

# THE 32/64-BIT TRAP, STATED EXPLICITLY.
# A Pi can run a 64-bit KERNEL under a 32-bit USERLAND. `uname -m` reports the
# kernel and will say aarch64 while every wheel pip installs is armv7l. The
# authority for userland is the Python interpreter's own word size, so both are
# printed and compared. Getting this wrong means a wheel that installs and then
# will not import.
KERN_ARCH="$(uname -m)"
USER_BITS="$(getconf LONG_BIT 2>/dev/null || echo UNKNOWN)"
kv "Kernel arch (uname -m)" "$KERN_ARCH"
kv "Userland word size" "${USER_BITS}-bit"
kv "dpkg architecture" "$(dpkg --print-architecture 2>/dev/null || echo UNKNOWN)"
if [ "$KERN_ARCH" = "aarch64" ] && [ "$USER_BITS" = "32" ]; then
  echo "  [WARN] 64-bit kernel over a 32-bit userland. Install arm64 wheels and they"
  echo "         will import-fail at runtime, not at install time. Treat armhf as truth."
fi

kv "python3 path" "$(command -v python3 || echo MISSING)"
kv "python3 version" "$(python3 -V 2>&1 || echo MISSING)"
if [ -n "${VIRTUAL_ENV:-}" ]; then
  echo "  [WARN] A virtualenv is ACTIVE: ${VIRTUAL_ENV}"
  echo "         picamera2 is installed by APT into the SYSTEM python. A venv created"
  echo "         WITHOUT --system-site-packages cannot see it, and the import below"
  echo "         will fail for a packaging reason, not a hardware reason."
fi

# -----------------------------------------------------------------------------
# SECTION 2 — CAMERA STACK AND ONE STILL TO DISK
# Two independent witnesses: the libcamera CLI (what the OS thinks is attached)
# and picamera2 (what our code will actually use). They must agree. If the CLI
# sees a camera and picamera2 does not, the fault is Python packaging. If
# neither sees one, the fault is the ribbon cable or the config.
# -----------------------------------------------------------------------------
line "SECTION 2 — CAMERA STACK"

CAM_CLI=""
for c in rpicam-hello libcamera-hello; do
  if command -v "$c" >/dev/null 2>&1; then CAM_CLI="$c"; break; fi
done
if [ -n "$CAM_CLI" ]; then
  kv "libcamera CLI" "$CAM_CLI"
  echo "  --- $CAM_CLI --list-cameras ---"
  "$CAM_CLI" --list-cameras 2>&1 | sed 's/^/  /'
else
  kv "libcamera CLI" "MISSING (neither rpicam-hello nor libcamera-hello on PATH)"
fi

# V4L2 nodes are listed for information ONLY. On Bookworm a CSI camera is NOT
# reliably exposed as a plain capture node, which is precisely why the brief
# forbids cv2.VideoCapture for it — see FINDING F-2 in 00_brief/HARDWARE.md.
kv "/dev/video* nodes" "$(ls /dev/video* 2>/dev/null | tr '\n' ' ' || echo none)"

echo "  --- picamera2 import + capture ---"
python3 - "$OUTDIR" <<'PY' 2>&1 | sed 's/^/  /'
import sys, traceback
outdir = sys.argv[1]

# STEP 1 — can we import at all? This is the single most common failure and it
# is a packaging failure, not a hardware one. Report it as such.
try:
    import picamera2
    from picamera2 import Picamera2
    print(f"PASS  picamera2 imported, version {getattr(picamera2, '__version__', 'unknown')}")
    print(f"      module file: {picamera2.__file__}")
except Exception:
    print("FAIL  picamera2 did NOT import.")
    traceback.print_exc()
    print("      Cause and effect: on Raspberry Pi OS picamera2 ships as the APT package")
    print("      python3-picamera2 into the SYSTEM interpreter. It is NOT on PyPI in a")
    print("      usable form. Fix:  sudo apt install -y python3-picamera2")
    print("      If you are in a venv, recreate it with --system-site-packages.")
    sys.exit(0)   # exit 0 on purpose: the outer script must continue to section 4

# STEP 2 — what does the library see? global_camera_info() is checked with
# hasattr rather than called blind, because it was added mid-0.3.x and calling a
# method that does not exist would abort the probe on an older build.
try:
    if hasattr(Picamera2, "global_camera_info"):
        info = Picamera2.global_camera_info()
        print(f"PASS  global_camera_info(): {len(info)} camera(s)")
        for i, c in enumerate(info):
            print(f"      [{i}] {c}")
        if not info:
            print("FAIL  Zero cameras. Check the ribbon seating and orientation, then")
            print("      re-run. On Bookworm no dtoverlay edit should be needed.")
            sys.exit(0)
    else:
        print("WARN  This picamera2 build has no global_camera_info(); continuing.")
except Exception:
    print("FAIL  global_camera_info() raised.")
    traceback.print_exc()
    sys.exit(0)

# STEP 3 — open it, report properties and sensor modes, capture one still.
try:
    picam2 = Picamera2()
    props = getattr(picam2, "camera_properties", {}) or {}
    print(f"PASS  Picamera2() opened. Model={props.get('Model')}")
    print(f"      PixelArraySize={props.get('PixelArraySize')}  UnitCellSize={props.get('UnitCellSize')}")

    modes = getattr(picam2, "sensor_modes", None)
    if modes:
        print(f"PASS  sensor_modes: {len(modes)} raw mode(s)")
        print("      (RAW SENSOR MODES. These are not the same as output sizes — see")
        print("       FINDING F-3. A narrower mode may CROP and change the field of view.)")
        for m in modes:
            print(f"      size={m.get('size')} fps={m.get('fps')} bit_depth={m.get('bit_depth')} "
                  f"crop_limits={m.get('crop_limits')} format={m.get('format')}")
    else:
        print("WARN  sensor_modes unavailable on this build.")

    cfg = picam2.create_still_configuration()
    picam2.configure(cfg)
    picam2.start()
    import time; time.sleep(2)          # let AE/AWB settle or the still is dark
    path = f"{outdir}/still_default.jpg"
    picam2.capture_file(path)
    md = picam2.capture_metadata()
    picam2.stop()
    picam2.close()
    import os
    print(f"PASS  captured {path}  ({os.path.getsize(path)} bytes)")
    print(f"      ExposureTime={md.get('ExposureTime')} AnalogueGain={md.get('AnalogueGain')} "
          f"Lux={md.get('Lux')}")
    print("      CHECK THIS IMAGE BY EYE. A file of the right size can still be black,")
    print("      and a black frame makes MOG2 report a perfectly quiet scene.")
except Exception:
    print("FAIL  capture path raised.")
    traceback.print_exc()
PY

# -----------------------------------------------------------------------------
# SECTION 3 — RESOLUTION vs THROUGHPUT
# The brief asks for the SMALLEST resolution that still lets a vessel-sized
# object be detected. That is two measurements, not one: (a) how fast the
# detector runs at each size, measured here; (b) how many pixels wide the
# silhouette actually is at the demo geometry, which needs the table set up.
# This section measures (a) with the SAME MOG2 configuration pi_sensor.py uses,
# so the number transfers. (b) is recorded in HARDWARE.md as still open.
# -----------------------------------------------------------------------------
line "SECTION 3 — RESOLUTION vs MOG2 THROUGHPUT"
python3 - "$OUTDIR" <<'PY' 2>&1 | sed 's/^/  /'
import sys, time, traceback
outdir = sys.argv[1]
try:
    import cv2, numpy as np
    print(f"PASS  cv2 {cv2.__version__}, numpy {np.__version__}")
except Exception:
    print("FAIL  cv2/numpy missing — pi_sensor.py cannot run at all.")
    print("      Fix:  sudo apt install -y python3-opencv python3-numpy")
    traceback.print_exc(); sys.exit(0)

try:
    from picamera2 import Picamera2
    have_cam = True
except Exception:
    have_cam = False
    print("WARN  no picamera2 — benchmarking on synthetic frames instead. The MOG2")
    print("      cost is per-pixel and transfers, but the CAPTURE cost does not, so")
    print("      the FPS below is an UPPER BOUND on the real pipeline.")

# Candidate sizes, smallest first. 640x480 is pi_sensor.py's current default.
CANDIDATES = [(320, 240), (640, 480), (1280, 720)]
N = 60

for (w, h) in CANDIDATES:
    try:
        # Same detector construction as pi_sensor.ClassicalDetector uses.
        mog = cv2.createBackgroundSubtractorMOG2(detectShadows=False)
        frames = []
        if have_cam:
            picam2 = Picamera2()
            # main= fixes the OUTPUT size. The ISP scales into it. We deliberately do
            # NOT pick a narrow sensor mode, because that would crop the FOV and
            # silently invalidate hfov_deg in the pose file.
            picam2.configure(picam2.create_preview_configuration(main={"size": (w, h), "format": "RGB888"}))
            picam2.start(); time.sleep(1.0)
            t0 = time.time()
            for _ in range(N):
                f = picam2.capture_array()
                g = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
                m = mog.apply(g)
                cv2.connectedComponentsWithStats(m, connectivity=8)
            dt = time.time() - t0
            got = picam2.capture_array().shape
            picam2.stop(); picam2.close()
            print(f"  {w}x{h}: {N/dt:5.1f} FPS  ({dt/N*1000:5.1f} ms/frame)  actual array {got}  [capture+MOG2+CC]")
        else:
            rng = np.random.default_rng(0)
            base = rng.integers(0, 255, (h, w), dtype=np.uint8)
            for _ in range(N): frames.append(base)
            t0 = time.time()
            for g in frames:
                m = mog.apply(g)
                cv2.connectedComponentsWithStats(m, connectivity=8)
            dt = time.time() - t0
            print(f"  {w}x{h}: {N/dt:5.1f} FPS  ({dt/N*1000:5.1f} ms/frame)  [MOG2+CC only, NO capture]")
    except Exception:
        print(f"  {w}x{h}: FAIL")
        traceback.print_exc()
PY

# -----------------------------------------------------------------------------
# SECTION 4 — NETWORK
# The question is not "is there wifi". It is "can the Pi deliver a POST to the
# shore station, over which transport, and can the Mac reach back". Those are
# three different tests and a pass on one does not imply the others.
# -----------------------------------------------------------------------------
line "SECTION 4 — NETWORK"
kv "Pi hostname" "$(hostname)"
kv "Pi mDNS name" "$(hostname).local"
echo "  --- all IPv4 addresses on this Pi ---"
ip -o -4 addr show 2>/dev/null | awk '{printf "  %-10s %s\n", $2, $4}' || echo "  ip(8) missing"
echo "  --- link state per interface ---"
for i in $(ls /sys/class/net 2>/dev/null); do
  [ "$i" = "lo" ] && continue
  ST="$(cat /sys/class/net/$i/operstate 2>/dev/null || echo ?)"
  printf '  %-10s operstate=%s' "$i" "$ST"
  case "$i" in
    wlan*) S="$(iw dev "$i" link 2>/dev/null | awk '/SSID/{$1="";print}' | xargs || true)"
           printf '  SSID=%s' "${S:-none}" ;;
    eth*|en*) printf '  (ethernet)' ;;
    usb*|ncm*) printf '  (USB gadget)' ;;
  esac
  printf '\n'
done
kv "Default route" "$(ip route show default 2>/dev/null | head -1 || echo none)"

if [ -z "$MAC_IP" ]; then
  echo "  [SKIP] No MAC_IP given. Run 99_scratch/probe_pi_mac.sh on the Mac, then"
  echo "         re-run:  bash probe_pi.sh <MAC_IP> ${MAC_PORT}"
else
  kv "Target Mac" "${MAC_IP}:${MAC_PORT}"
  # THE TRANSPORT ANSWER. `ip route get` asks the kernel which interface it would
  # ACTUALLY use for this specific destination. That is the honest answer to
  # "which transport is in use" — reading the wifi SSID is not, because the Pi
  # may be on wifi and still route to the Mac over ethernet.
  IFACE="$(ip route get "$MAC_IP" 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1)"
  SRC="$(ip route get "$MAC_IP" 2>/dev/null | sed -n 's/.* src \([^ ]*\).*/\1/p' | head -1)"
  kv "Egress interface to Mac" "${IFACE:-UNRESOLVED}"
  kv "Source IP used" "${SRC:-UNRESOLVED}"
  case "${IFACE:-}" in
    eth*|en*) kv "Transport in use" "ETHERNET" ;;
    wlan*)    kv "Transport in use" "WIFI / HOTSPOT (SSID $(iw dev "$IFACE" link 2>/dev/null | awk '/SSID/{$1="";print}' | xargs || echo '?'))" ;;
    usb*|ncm*) kv "Transport in use" "USB GADGET" ;;
    "")       kv "Transport in use" "NO ROUTE — the Pi has no path to that address at all" ;;
    *)        kv "Transport in use" "$IFACE (unclassified)" ;;
  esac

  echo "  --- ICMP ping Pi -> Mac ---"
  if ping -c 3 -W 2 "$MAC_IP" 2>&1 | sed 's/^/  /'; then
    echo "  PASS  ICMP reaches the Mac."
  else
    echo "  FAIL  ICMP did not reach the Mac. NOTE: macOS can be configured to drop"
    echo "        ICMP while still accepting TCP, so check the TCP test below before"
    echo "        concluding there is no link."
  fi

  echo "  --- TCP connect Pi -> Mac:${MAC_PORT} (this is what the POST actually needs) ---"
  if timeout 3 bash -c "echo > /dev/tcp/${MAC_IP}/${MAC_PORT}" 2>/dev/null; then
    echo "  PASS  TCP ${MAC_PORT} open. The sensor POST has a path."
  else
    echo "  FAIL  TCP ${MAC_PORT} refused or timed out. THREE DISTINCT CAUSES, in the"
    echo "        order they are most likely — do not guess between them:"
    echo "        1. app.py is bound to 127.0.0.1 (its DEFAULT). Loopback is not"
    echo "           reachable from another machine, ever. Start it with --host 0.0.0.0."
    echo "        2. The macOS application firewall is blocking incoming to python3."
    echo "        3. No route / wrong IP. Section 4 above already answered that one."
  fi
fi

line "PROBE COMPLETE — paste everything above back into the chat"
echo "Also confirm by eye: does ${OUTDIR}/still_default.jpg show the scene, not black?"
