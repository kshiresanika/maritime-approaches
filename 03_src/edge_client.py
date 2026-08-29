"""
03_src/edge_client.py — the MAC-SIDE CLIENT of an edge sensor node.

WHAT THIS IS NOT, AND WHY THAT MATTERS MORE THAN WHAT IT IS

It is NOT a second ingest endpoint and NOT a second node-liveness tracker.
`03_src/server.py` already owns both: `POST /ingest/contacts` accepts the contacts,
`_note_node()` records the node, and a 1 Hz watchdog flips `SensorNode.online` after
`NODE_STALE_AFTER_S` and emits a `NodeStatusEvent` only on the flip.

Duplicating that here would be the `PriorityScore.factors` mistake a second time: two
sources of truth for one fact, where the failure is SILENT. The console would render
this module's belief about a node while the server kept computing its own, and the two
would diverge with nothing raising. Section 8 of contracts.py records that reasoning;
this file is written to respect it.

SO WHAT DOES IT ADD? THE FAILURE server.py STRUCTURALLY CANNOT SEE.

server.py learns a node exists only when that node POSTs. Its knowledge is therefore
one-sided, and there is a whole failure class on the blind side:

    A Pi that is powered, running, detecting and serving MJPEG — but whose POSTs are
    not arriving.

To server.py that node is not "offline". It is NEVER-HEARD-OF: absent from
`ConsoleState.nodes` entirely, so the console shows no red strip, no stale timestamp,
nothing at all. An operator sees an empty queue and reads it as an empty sea.

This module polls the node's own `/health` from the Mac, which is the other direction,
and the two directions together resolve into a diagnosis:

    poll OK  + node reports uplink_ok   -> HEALTHY
    poll OK  + node reports uplink DOWN -> UP BUT NOT POSTING   <-- the blind spot
    poll FAIL + contacts still arriving -> HTTP PORT BLOCKED (data path fine)
    poll FAIL + no contacts             -> NODE DOWN

Only the second row is ambiguous without both views, and it is also the most likely
fault at a venue: a wrong --post URL, a firewall, or an HTTP 400 because a contact
failed validation. `reconcile()` names which one.

THREE RULES THIS FILE KEEPS

1. `measured_fps` is passed through from the node's own /health, verbatim, including
   None. None means NOBODY COUNTED. Substituting a configured rate breaks MEASURED
   NUMBERS ONLY and puts a number on the console that no code produced.
2. `last_seen` is the time of the last SUCCESSFUL poll RESPONSE, never `now()`, and
   stays None until one has happened. A node never heard from must not render as
   healthy — that is the single thing SensorNode.last_seen exists for.
3. It never writes to server.py's state. It returns SensorNode objects; what the
   console does with them is lane E's call.

NO NEW DEPENDENCIES. urllib from the stdlib, and contracts.py.

USAGE
    python3 03_src/edge_client.py --node http://192.168.4.2:8080 --watch
    python3 03_src/edge_client.py --node http://192.168.4.2:8080 \
                                  --server http://127.0.0.1:8000     # full reconcile
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from contracts import SensorKind, SensorNode  # noqa: E402

CLIENT_VERSION = "edge_client/0.1.0"

# How long a successful poll keeps a node believed-online. Deliberately the same 30 s
# as server.NODE_STALE_AFTER_S: two different staleness thresholds for one node is how
# the console ends up showing green on one strip and red on another for the same Pi.
# Named here rather than imported, because importing server.py would pull FastAPI into
# a module that must run without it.
# PINNED TO server.NODE_STALE_AFTER_S, and the pinning is prose, which is why it broke.
# Lane F moved the server side 30.0 -> 8.0 on 2026-08-29 (four missed 2 s heartbeats;
# at 30 s an unplugged Pi stayed green for half the pitch) and this constant was not
# moved with it. Consequence while they disagreed: a Pi silent for 10 s was OFFLINE to
# the server's watchdog and ONLINE to this poller, so reconcile() returned
# "UP BUT NOT POSTING" and sent the operator hunting a POST-path fault for 22 seconds
# during which the only true fact was that the node was briefly quiet. That is exactly
# the split-brain this module's own docstring says two thresholds cause.
#
# Not imported from server.py deliberately: that would pull FastAPI onto a module that
# must run without it. The durable fix is one constant in contracts.py, which both
# already import; until then this comment is the pin and it must be moved by hand.
POLL_STALE_AFTER_S = 8.0
DEFAULT_TIMEOUT_S = 2.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ================================================================================
# 1. One poll.
# ================================================================================

@dataclass
class PollResult:
    """The outcome of a single /health request. Every field is observed or None."""

    ok: bool
    at_utc: datetime
    rtt_ms: float | None = None
    health: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def node_id(self) -> str | None:
        v = self.health.get("node_id")
        return str(v) if v else None

    @property
    def measured_fps(self) -> float | None:
        """Verbatim from the node. None stays None — see rule 1 in the module docstring."""
        v = self.health.get("measured_fps")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def uplink_ok(self) -> bool | None:
        """The NODE's own belief about whether its POSTs are landing. None if the node
        is too old a build to report it — which is not the same as False and must not
        be collapsed into it."""
        v = self.health.get("uplink_ok")
        return bool(v) if isinstance(v, bool) else None


class EdgeNodeClient:
    """
    Polls one edge node's /health. Stateless except for the last result.

    WHY /health AND NOT A PING. ICMP proves a kernel is alive. It says nothing about
    whether the camera opened, whether the detector loaded, or whether frames are being
    counted — and a Pi that boots but whose picamera2 import failed answers ping
    perfectly while producing nothing. /health answers the question actually being
    asked.
    """

    def __init__(self, base_url: str, *, node_id: str | None = None,
                 kind: SensorKind = "edge_pi", timeout: float = DEFAULT_TIMEOUT_S,
                 stale_after_s: float = POLL_STALE_AFTER_S) -> None:
        self.base_url = base_url.rstrip("/")
        self._configured_node_id = node_id
        self.kind: SensorKind = kind
        self.timeout = timeout
        self.stale_after_s = stale_after_s

        self.last_ok: PollResult | None = None      # last SUCCESSFUL poll
        self.last_attempt: PollResult | None = None  # last poll of any outcome
        self.consecutive_failures = 0

    @property
    def node_id(self) -> str:
        """The node's own id wins over the configured one: a node that names itself is
        the authority on its name, and a mismatch is worth seeing rather than papering
        over. Falls back to the configured id, then to the URL — never to a fabricated
        identity, because a contact attributed to the wrong sensor is worse provenance
        than one attributed to an admittedly unknown sensor."""
        if self.last_ok and self.last_ok.node_id:
            return self.last_ok.node_id
        return self._configured_node_id or f"edge-at-{self.base_url}"

    def poll(self) -> PollResult:
        url = f"{self.base_url}/health"
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
            rtt = (time.perf_counter() - t0) * 1000.0
            health = json.loads(raw.decode("utf-8"))
            if not isinstance(health, dict):
                raise ValueError(f"/health returned {type(health).__name__}, not an object")
            res = PollResult(ok=True, at_utc=_now(), rtt_ms=round(rtt, 1), health=health)
            self.last_ok = res
            self.consecutive_failures = 0
        except Exception as exc:
            # Every failure mode is recorded with its TYPE. "connection refused" (nothing
            # listening), "timed out" (host up, port filtered) and "JSONDecodeError"
            # (something is listening but it is not our node) are three different
            # problems with three different fixes, and a single "unreachable" string
            # would erase the distinction.
            res = PollResult(ok=False, at_utc=_now(),
                             error=f"{type(exc).__name__}: {exc}")
            self.consecutive_failures += 1
        self.last_attempt = res
        return res

    def sensor_node(self) -> SensorNode:
        """
        Build a SensorNode from what has actually been observed.

        `online` here means REACHABLE BY POLL, which is a different claim from
        server.py's `online` (= has POSTed recently). Both are legitimate and they are
        not interchangeable; see reconcile() for how they combine.
        """
        last_seen = self.last_ok.at_utc if self.last_ok else None
        online = bool(
            self.last_ok
            and (_now() - self.last_ok.at_utc).total_seconds() <= self.stale_after_s
        )
        return SensorNode(
            node_id=self.node_id,
            kind=self.kind,
            online=online,
            last_seen=last_seen,
            measured_fps=self.last_ok.measured_fps if self.last_ok else None,
        )


# ================================================================================
# 2. Reconciliation — the actual product of this module.
# ================================================================================

DIAGNOSIS_HEALTHY = "HEALTHY"
DIAGNOSIS_UP_NOT_POSTING = "UP BUT NOT POSTING"
DIAGNOSIS_PORT_BLOCKED = "HTTP PORT BLOCKED (data path fine)"
DIAGNOSIS_DOWN = "NODE DOWN"
DIAGNOSIS_UNKNOWN = "NEVER HEARD FROM"


def reconcile(client: EdgeNodeClient,
              server_nodes: list[SensorNode] | None) -> dict:
    """
    Combine the two one-sided views into one diagnosis.

    `server_nodes` is what the shore station believes, normally fetched from the Mac's
    own /health or /api/state. Pass None when the server is not running — the poll view
    alone still separates NODE DOWN from HEALTHY, it just cannot see the blind spot.

    THE BLIND SPOT, STATED ONCE MORE BECAUSE IT IS THE REASON THIS FUNCTION EXISTS: a
    node absent from `server_nodes` is not offline to the server, it is UNKNOWN to it.
    The console draws nothing for it. Only this poll can tell the difference between
    "there is no such sensor" and "the sensor is fine and its uplink is broken".
    """
    poll = client.last_attempt or client.poll()
    known_to_server = None
    if server_nodes is not None:
        for n in server_nodes:
            if n.node_id == client.node_id:
                known_to_server = n
                break

    server_hears_it = bool(known_to_server and known_to_server.online)

    if poll.ok:
        # The node's own uplink flag is preferred when present, because the node knows
        # whether its POST returned 200 and the server only knows whether one arrived.
        # They disagree in exactly one interesting case: a POST that arrives and is
        # REJECTED with a 400 for a contract violation. The node sees the failure; the
        # server never records the node. uplink_ok False + not known to server is that
        # case, and it is why this branch trusts the node.
        if poll.uplink_ok is False or (server_nodes is not None and not server_hears_it):
            diagnosis = DIAGNOSIS_UP_NOT_POSTING
        else:
            diagnosis = DIAGNOSIS_HEALTHY
    else:
        if server_hears_it:
            diagnosis = DIAGNOSIS_PORT_BLOCKED
        elif client.last_ok is None:
            diagnosis = DIAGNOSIS_UNKNOWN
        else:
            diagnosis = DIAGNOSIS_DOWN

    return {
        "node_id": client.node_id,
        "url": client.base_url,
        "diagnosis": diagnosis,
        "poll_ok": poll.ok,
        "poll_rtt_ms": poll.rtt_ms,
        "poll_error": poll.error,
        "consecutive_failures": client.consecutive_failures,
        "node_reports_uplink_ok": poll.uplink_ok,
        "node_measured_fps": poll.measured_fps,
        "node_cpu_temp_c": poll.health.get("cpu_temp_c"),
        "node_throttled": poll.health.get("throttled"),
        "node_last_detection_utc": poll.health.get("last_detection_utc"),
        "node_post_failures": poll.health.get("post_failures"),
        "node_last_post_error": poll.health.get("last_post_error"),
        "server_knows_node": known_to_server is not None,
        "server_believes_online": server_hears_it,
        "checked_at_utc": poll.at_utc.isoformat(),
        # The remedy is part of the diagnosis. A monitor that names a fault without
        # naming the fix costs the operator the same lookup every time.
        "suggested_action": {
            DIAGNOSIS_HEALTHY: "none",
            DIAGNOSIS_UP_NOT_POSTING:
                "check the node's --post URL, that server.py runs with --host 0.0.0.0, "
                "and node_last_post_error for an HTTP 400 (a contract violation, not a "
                "network fault)",
            DIAGNOSIS_PORT_BLOCKED:
                "contacts are arriving, so the data path is fine; the node's HTTP port "
                "is filtered. The MJPEG relay will fail while the queue keeps working",
            DIAGNOSIS_DOWN: "node was reachable and is not now: power, wifi, or a crashed process",
            DIAGNOSIS_UNKNOWN: "never reachable: wrong IP or port, or the node has not started",
        }[diagnosis],
    }


def fetch_server_nodes(server_url: str, *, timeout: float = DEFAULT_TIMEOUT_S
                       ) -> list[SensorNode] | None:
    """
    Read the shore station's own view. Returns None if the server is not reachable —
    None means UNKNOWN and is deliberately distinct from an empty list, which means the
    server is running and has heard from nobody.
    """
    for path in ("/api/state", "/health"):
        try:
            req = urllib.request.Request(server_url.rstrip("/") + path,
                                         headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
        raw = data.get("nodes")
        if isinstance(raw, list):
            out: list[SensorNode] = []
            for n in raw:
                try:
                    out.append(SensorNode(**n) if isinstance(n, dict) else n)
                except Exception:
                    continue
            return out
    return None


# ================================================================================
# 3. Background monitor.
# ================================================================================

class EdgeNodeMonitor:
    """
    Polls a set of nodes on an interval from a daemon thread.

    Read `nodes()` for SensorNode objects and `report()` for the diagnoses. Nothing
    here pushes into server.py — this is a source lane E may choose to consume, not a
    writer into someone else's state.
    """

    def __init__(self, clients: list[EdgeNodeClient], *, interval: float = 5.0,
                 server_url: str | None = None) -> None:
        self.clients = clients
        self.interval = interval
        self.server_url = server_url
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._report: list[dict] = []
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            server_nodes = (fetch_server_nodes(self.server_url)
                            if self.server_url else None)
            rows = []
            for c in self.clients:
                c.poll()
                rows.append(reconcile(c, server_nodes))
            with self._lock:
                self._report = rows
            self._stop.wait(self.interval)

    def start(self) -> "EdgeNodeMonitor":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def nodes(self) -> list[SensorNode]:
        return [c.sensor_node() for c in self.clients]

    def report(self) -> list[dict]:
        with self._lock:
            return list(self._report)


# ================================================================================
# 4. CLI — the pre-demo check.
# ================================================================================

def _print_table(rows: list[dict]) -> None:
    print(f"{'node':<22} {'diagnosis':<34} {'fps':>6} {'temp':>6} {'rtt':>7}")
    print("-" * 80)
    for r in rows:
        fps = r["node_measured_fps"]
        tmp = r["node_cpu_temp_c"]
        rtt = r["poll_rtt_ms"]
        print(f"{r['node_id'][:22]:<22} {r['diagnosis']:<34} "
              f"{(fps if fps is not None else '--'):>6} "
              f"{(tmp if tmp is not None else '--'):>6} "
              f"{(rtt if rtt is not None else '--'):>7}")
        if r["diagnosis"] != DIAGNOSIS_HEALTHY:
            print(f"    -> {r['suggested_action']}")
            if r["node_last_post_error"]:
                print(f"    node's last post error: {r['node_last_post_error']}")
            if r["poll_error"]:
                print(f"    poll error: {r['poll_error']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Poll edge nodes from the Mac and diagnose the uplink.")
    p.add_argument("--node", action="append", required=True,
                   help="base URL of an edge node, e.g. http://192.168.4.2:8080. "
                        "Repeatable.")
    p.add_argument("--server", default=None,
                   help="shore station URL, e.g. http://127.0.0.1:8000. Without it the "
                        "'up but not posting' case cannot be distinguished from healthy.")
    p.add_argument("--watch", action="store_true", help="poll until interrupted")
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = p.parse_args(argv)

    clients = [EdgeNodeClient(u, timeout=args.timeout) for u in args.node]
    if args.server is None:
        print("NOTE: no --server given. Without the shore station's view this cannot "
              "tell HEALTHY from UP-BUT-NOT-POSTING when the node itself does not "
              "report uplink_ok.\n", file=sys.stderr)

    def once() -> list[dict]:
        server_nodes = fetch_server_nodes(args.server) if args.server else None
        rows = []
        for c in clients:
            c.poll()
            rows.append(reconcile(c, server_nodes))
        return rows

    try:
        while True:
            rows = once()
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                print(f"\n{CLIENT_VERSION}  {_now().strftime('%H:%M:%S')} UTC")
                _print_table(rows)
            if not args.watch:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass

    # Exit non-zero when anything is not healthy, so this is usable as a pre-demo gate
    # in a shell script rather than something a human has to read and interpret.
    return 0 if all(r["diagnosis"] == DIAGNOSIS_HEALTHY for r in once()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
