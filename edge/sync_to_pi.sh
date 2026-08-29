#!/usr/bin/env bash
# =============================================================================
# edge/sync_to_pi.sh — push the edge code to the Pi over rsync+ssh. RUN ON THE MAC.
# =============================================================================
#
# WHY THIS IS A SCRIPT AND NOT A COMMAND YOU TYPE
# The exclude list is the whole point. A naive `rsync -a project/ pi:` copies the 894 MB
# AIS zip, a 248 MB slice, the .venv built for macOS arm64 and the .git history onto a
# Pi's SD card over wifi. This transfers roughly 7 MB.
#
# USAGE
#   bash edge/sync_to_pi.sh <pi-host>            # e.g. pi@192.168.4.2  or  pi@raspberrypi.local
#   bash edge/sync_to_pi.sh <pi-host> --dry-run  # list what WOULD move, transfer nothing
#
# The agent cannot run this. Its shell on the Mac is a network-less Linux VM and the Pi
# is not reachable from it — see 00_brief/HARDWARE.md §0. Amol runs it.
# =============================================================================
set -euo pipefail

PI_HOST="${1:-}"
shift || true
DRY=""
for a in "$@"; do [ "$a" = "--dry-run" ] && DRY="--dry-run"; done

if [ -z "$PI_HOST" ]; then
  echo "usage: bash edge/sync_to_pi.sh <user@host> [--dry-run]" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="~/maritime-approaches"

echo "source : $ROOT"
echo "dest   : ${PI_HOST}:${DEST}"
[ -n "$DRY" ] && echo "MODE   : DRY RUN — nothing will be written"

# --relative with ./ anchors keeps the project's directory names on the Pi, so the
# sys.path bootstrap in sensor_node.py (which walks up to ../04_demo and ../03_src)
# resolves identically on both machines. Flattening the tree would break that import
# with a message that points at the wrong thing.
cd "$ROOT"
rsync -avz --relative $DRY \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  -e ssh \
  ./edge/ \
  ./03_src/contracts.py \
  ./04_demo/pi_sensor.py \
  ./04_demo/camera_pose_TABLETOP.json \
  ./04_demo/camera_pose_TEMPLATE.json \
  ./yolov8n.pt \
  "${PI_HOST}:${DEST}/"

echo
echo "WHAT WENT, AND WHY EACH ONE:"
echo "  edge/                       the node, the benchmark, this script"
echo "  03_src/contracts.py         so the Pi validates EoContact at the source"
echo "  04_demo/pi_sensor.py        the ONLY copy of the bearing/range maths + the"
echo "                              horizon finder; sensor_node.py imports from it"
echo "  04_demo/camera_pose_*.json  the pose. hfov_deg MUST be non-zero or the node"
echo "                              refuses to start"
echo "  yolov8n.pt                  6.5 MB weights, so the Pi never downloads at the"
echo "                              venue. ultralytics silently fetches from the"
echo "                              internet when the file is missing, which is a"
echo "                              demo that depends on hackathon wifi"
echo
echo "WHAT DID NOT GO, ON PURPOSE:"
echo "  02_data/  .venv/  .git/  01_research/  05_pitch/  99_scratch/"
echo "  — bulk AIS, a macOS-arm64 venv and history are all useless on the Pi, and the"
echo "    raw DMA rows must not leave the Mac at all (licence + real vessel identities)."
echo
echo "NEXT ON THE PI:"
echo "  ssh ${PI_HOST}"
echo "  cd ~/maritime-approaches"
echo "  sudo apt install -y python3-picamera2 python3-opencv python3-numpy"
echo "  pip3 install --break-system-packages -r edge/requirements-edge.txt"
echo "  python3 edge/bench_edge.py --mode sweep"
