#!/usr/bin/env bash
# =============================================================================
# probe_pi_mac.sh — LANE F, MAC SIDE.  RUN THIS ON THE MacBook.
# =============================================================================
#
# WHY A SECOND SCRIPT
# "Can the Pi reach the Mac" and "can the Mac reach the Pi" are different
# questions with different failure modes, and only one of them can be asked
# from each machine. This one answers the Mac's half AND produces the MAC_IP
# that probe_pi.sh needs as its argument.
#
# ORDER OF OPERATIONS
#   1. Run THIS on the Mac.            -> gives you MAC_IP
#   2. Run probe_pi.sh <MAC_IP> on Pi. -> gives you PI_IP
#   3. Re-run THIS with PI_IP:  bash probe_pi_mac.sh <PI_IP>
#
# Run it with bash explicitly — macOS defaults to zsh and /dev/tcp below is a
# bash builtin:   bash 99_scratch/probe_pi_mac.sh
# =============================================================================
set -u
PI_IP="${1:-}"
PORT="${2:-8000}"

line() { printf '\n=============================================================\n%s\n=============================================================\n' "$1"; }
kv()   { printf '  %-34s %s\n' "$1" "$2"; }

echo "LANE F MAC-SIDE PROBE  |  $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC"

line "MAC ADDRESSES — this is the MAC_IP that probe_pi.sh needs"
# route -n get default names the interface macOS would use for off-box traffic.
DEF_IF="$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')"
kv "Default interface" "${DEF_IF:-none}"
# networksetup maps the BSD device name (en0) to a human name (Wi-Fi / Ethernet).
# Without this mapping "en0" is not an answer to "which transport".
echo "  --- BSD device -> hardware port map ---"
networksetup -listallhardwareports 2>/dev/null | awk '/Hardware Port|Device/' | paste - - | sed 's/^/  /'
echo "  --- active IPv4 addresses ---"
for i in $(ifconfig -l 2>/dev/null); do
  [ "$i" = "lo0" ] && continue
  A="$(ipconfig getifaddr "$i" 2>/dev/null || true)"
  [ -n "$A" ] && printf '  %-8s %s\n' "$i" "$A"
done
if [ -n "${DEF_IF:-}" ]; then
  kv ">>> USE THIS AS MAC_IP" "$(ipconfig getifaddr "$DEF_IF" 2>/dev/null || echo UNRESOLVED)"
fi
kv "Mac mDNS name" "$(scutil --get LocalHostName 2>/dev/null).local"

line "IS THE SHORE STATION REACHABLE FROM OFF-BOX?"
# THE SINGLE MOST LIKELY DEMO KILLER. app.py's --host DEFAULTS to 127.0.0.1,
# which is loopback and is not reachable from the Pi under any network
# configuration. lsof shows the ACTUAL bind address, so this is measured, not
# assumed. A line reading 127.0.0.1:8000 means the Pi can never post.
echo "  --- listeners on port ${PORT} ---"
if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | sed 's/^/  /' | grep . ; then
  BINDS="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $9}')"
  case "$BINDS" in
    *127.0.0.1*) echo "  FAIL  Bound to 127.0.0.1 (LOOPBACK ONLY). The Pi cannot reach this."
                 echo "        Fix:  python3 04_demo/app.py --host 0.0.0.0" ;;
    *\*:*|*0.0.0.0*) echo "  PASS  Bound to all interfaces. Off-box clients can connect." ;;
    *) echo "  WARN  Unrecognised bind address: $BINDS" ;;
  esac
else
  echo "  NONE  Nothing is listening on ${PORT} yet. Start it with:"
  echo "        python3 04_demo/app.py --host 0.0.0.0 --port ${PORT}"
  echo "        then re-run this script."
fi

line "MACOS APPLICATION FIREWALL"
# A second, independent way for the POST to fail that looks identical to a
# network fault from the Pi: the socket is open and bound correctly, and macOS
# drops the inbound connection because python3 is not an approved listener.
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null | sed 's/^/  /' || echo "  UNKNOWN"
/usr/libexec/ApplicationFirewall/socketfilterfw --getblockall 2>/dev/null | sed 's/^/  /' || true
echo "  If the firewall is ON, the FIRST time app.py binds a non-loopback address"
echo "  macOS shows an 'allow incoming connections' dialog. If that dialog is"
echo "  dismissed or missed, every POST from the Pi is dropped silently."

line "MAC -> PI REACHABILITY"
if [ -z "$PI_IP" ]; then
  echo "  [SKIP] No PI_IP given. Run probe_pi.sh on the Pi first, take its IP from"
  echo "         section 4, then re-run:  bash probe_pi_mac.sh <PI_IP>"
else
  kv "Target Pi" "$PI_IP"
  echo "  --- ping ---"
  ping -c 3 -W 2000 "$PI_IP" 2>&1 | sed 's/^/  /'
  echo "  --- ARP entry (proves layer 2, even if ICMP is filtered) ---"
  arp -n "$PI_IP" 2>&1 | sed 's/^/  /'
  echo "  --- SSH reachability (port 22) ---"
  if timeout 3 bash -c "echo > /dev/tcp/${PI_IP}/22" 2>/dev/null; then
    echo "  PASS  TCP 22 open — you can scp the probe script across."
  else
    echo "  INFO  TCP 22 closed. Not a fault unless you wanted SSH; enable with"
    echo "        sudo raspi-config -> Interface Options -> SSH, on the Pi."
  fi
fi

line "MAC-SIDE PROBE COMPLETE — paste everything above back into the chat"
