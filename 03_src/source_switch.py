"""
03_src/source_switch.py — which sensor is feeding the picture, and nothing else.

THE PROBLEM THIS SOLVES
The Pi dies mid-pitch. The operator needs the picture back from another sensor in
seconds, without restarting the server and without losing the queue, the verdicts, the
ranking or the audit trail they have been talking the judges through.

THE DESIGN DECISION THAT MAKES THE GUARANTEE REAL
This module holds NO reference to the contact store, the record store, the evidence
queue or the anonymiser. It cannot clear them. That is not a promise kept by careful
coding — it is a property of the object graph, and `lane_f_failover_check.py` asserts it
by inspecting `__dict__`. A switch implemented as "reset state, then re-seed from the new
source" would pass every functional test on a good day and lose the operator's queue on
the one day it fires.

    The switch selects an AUTHORITY. It does not move data.

WHAT A SOURCE IS
A source is a NODE, not a new concept. `RECORDED` is the `scene-file` node server.py
already registers at startup; `EDGE_PI` is the Pi; `MAC_CAMERA` is the same
`edge/sensor_node.py` running on the MacBook with `--backend cv2 --kind mac_camera`.
Nothing new has to exist for a source to exist, which is why this is a thirty-minute
change and not a subsystem.

WHY THE LABELS MAP ONTO SensorKind RATHER THAN BECOMING A SECOND ENUM
`contracts.SensorKind` is already `edge_pi | mac_camera | file`. Introducing a parallel
`SourceKind` with the same three members and different spellings is the
`PriorityScore.factors` failure in a new costume: two vocabularies for one fact, drifting
the first time someone adds a member to one of them. The operator-facing LABELS are the
uppercase names the brief asked for; the stored value is always a `SensorKind`.

THE OTHER HALF OF THE FAILOVER, AND IT IS NOT OPTIONAL
A node that is not the active source still POSTs, and those posts are still ACCEPTED —
they keep `SensorNode.last_seen` fresh so the console can show the Pi coming back to
life. They are simply not PROMOTED into the picture. Two consequences worth stating:

  * A Pi that reboots mid-demo cannot silently seize the picture back from the Mac
    camera. The operator switches back deliberately, or it stays where it was put.
  * The console can say "edge-pi-01 is posting but MAC_CAMERA is live", which is a very
    different sentence from "edge-pi-01 is offline" and stops an operator chasing a
    fault that does not exist.

NO IMPORTS BEYOND THE STDLIB AND contracts. In particular no FastAPI: this module is
imported by the server, and a web dependency here would make it unimportable from a
test or from the Pi.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from contracts import SensorKind

SWITCH_VERSION = "source_switch/0.1.0"

# The operator-facing labels the brief specifies, mapped onto the contract's own
# vocabulary. This dict is the ONLY place the two spellings meet.
LABEL_TO_KIND: dict[str, SensorKind] = {
    "EDGE_PI": "edge_pi",
    "MAC_CAMERA": "mac_camera",
    "RECORDED": "file",
}
KIND_TO_LABEL: dict[str, str] = {v: k for k, v in LABEL_TO_KIND.items()}

# RECORDED is the default because it is the only source that cannot fail. The scene is
# already on disk and already loaded; if every camera in the room dies, the demo still
# has a picture. Defaulting to a live source means a cold start with no sensor shows an
# empty screen, which is indistinguishable from a broken tool.
DEFAULT_SOURCE = "RECORDED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalise(source: str) -> str:
    """
    Accept either an operator label ('EDGE_PI') or a contract kind ('edge_pi').

    Accepting both is not sloppiness: the console posts labels, and server.py's own
    ingest path has a `SensorKind` in hand. Forcing one to translate before calling
    means a translation site that can be forgotten.
    """
    if source is None:
        raise ValueError("source is None")
    s = str(source).strip()
    if s.upper() in LABEL_TO_KIND:
        return s.upper()
    if s.lower() in KIND_TO_LABEL:
        return KIND_TO_LABEL[s.lower()]
    raise ValueError(
        f"unknown source {source!r}. Valid: {sorted(LABEL_TO_KIND)} "
        f"or {sorted(KIND_TO_LABEL)}")


@dataclass(frozen=True)
class SwitchEvent:
    """
    One source change. Frozen, because this is evidence.

    An operator changing sensor in the middle of an incident is part of the case file:
    it explains why the picture changed, and an after-action review that cannot see the
    switch will read the discontinuity as the sea behaving strangely.
    """

    at_utc: datetime
    from_source: str
    to_source: str
    operator_id: str
    reason: str | None = None
    elapsed_ms: float = 0.0
    # True when the requested source was already active. Recorded rather than dropped:
    # "the operator pressed it and nothing needed to happen" is different from "the
    # operator never pressed it", and only one of those means they are confused.
    no_op: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "at_utc": self.at_utc.isoformat(),
            "from_source": self.from_source,
            "to_source": self.to_source,
            "operator_id": self.operator_id,
            "reason": self.reason,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "no_op": self.no_op,
        }


@dataclass
class SourceSwitch:
    """
    The active-source authority.

    DELIBERATELY HOLDS NOTHING ELSE. See the module docstring: the inability to clear the
    queue is structural. Every field below is a label, a timestamp or a log line.
    """

    active: str = DEFAULT_SOURCE
    operator_id: str = "system"
    changed_at_utc: datetime = field(default_factory=_now)
    history: list[SwitchEvent] = field(default_factory=list)
    # node_id -> why its last post was not promoted. Purely for the console; it is what
    # lets the screen distinguish "posting but not live" from "offline".
    _demoted: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.active = normalise(self.active)

    # ------------------------------------------------------------------ properties
    @property
    def active_kind(self) -> SensorKind:
        return LABEL_TO_KIND[self.active]

    # --------------------------------------------------------------------- switch
    def switch(self, to: str, *, operator_id: str = "operator",
               reason: str | None = None) -> SwitchEvent:
        """
        Change the authoritative source. O(1), no I/O, no pipeline call, no restart.

        The elapsed_ms recorded here is the SWITCH itself, and it is deliberately
        measured around the smallest possible region — this is the number that must be
        honest. The pipeline recompute that server.py triggers afterwards is a separate,
        larger cost and is timed separately, because reporting them as one figure would
        hide which half is which.
        """
        t0 = time.perf_counter()
        target = normalise(to)
        with self._lock:
            prev = self.active
            no_op = (target == prev)
            if not no_op:
                self.active = target
                self.changed_at_utc = _now()
                self.operator_id = operator_id
                # Demotion reasons are cleared on every real switch: they describe the
                # PREVIOUS authority and are meaningless under the new one. Leaving
                # them would show the operator a stale explanation for a node that is
                # now perfectly live.
                self._demoted.clear()
            ev = SwitchEvent(
                at_utc=_now(), from_source=prev, to_source=target,
                operator_id=operator_id, reason=reason,
                elapsed_ms=(time.perf_counter() - t0) * 1000.0, no_op=no_op)
            self.history.append(ev)
        return ev

    # -------------------------------------------------------------------- gating
    def accepts(self, node_kind: str, node_id: str | None = None) -> bool:
        """
        Is this node the current authority?

        Returns False for a node that is alive and posting but not selected. The caller
        must still record liveness for it — see note_demoted().
        """
        try:
            return normalise(node_kind) == self.active
        except ValueError:
            # An unknown kind is never the authority, but it is also not an exception
            # here: the ingest path already validates SensorKind and returns 400. This
            # branch exists so a future kind cannot crash the switch.
            return False

    def note_demoted(self, node_id: str, node_kind: str) -> None:
        """Record that a live node's contacts were received but not promoted."""
        with self._lock:
            self._demoted[node_id] = (
                f"posting as {node_kind}, but {self.active} is the live source")

    def demoted(self) -> dict[str, str]:
        with self._lock:
            return dict(self._demoted)

    # -------------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        """
        What the console renders. Flat and JSON-ready so it can be spliced into
        /api/state without a second serialisation step.
        """
        with self._lock:
            return {
                "active_source": self.active,
                "active_kind": LABEL_TO_KIND[self.active],
                "available_sources": list(LABEL_TO_KIND),
                "changed_at_utc": self.changed_at_utc.isoformat(),
                "changed_by": self.operator_id,
                "switch_count": sum(1 for e in self.history if not e.no_op),
                "last_switch_ms": next(
                    (e.elapsed_ms for e in reversed(self.history) if not e.no_op), None),
                "demoted_nodes": dict(self._demoted),
                "version": SWITCH_VERSION,
            }
