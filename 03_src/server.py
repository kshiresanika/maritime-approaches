"""
server.py — the shore station. Lane E (transport + operator console backend).

WHAT THIS FILE IS, IN ONE SENTENCE
It is a socket with a contract on it. Every judgement in this project already exists
somewhere else; this file moves those judgements to a browser, records what the human
did about them, and does nothing else.

WHAT THIS FILE IS NOT — AND THIS IS THE LOAD-BEARING PART
It contains NO association logic, NO consistency logic, NO verdict logic, NO
prioritisation and NO evidence assembly. It imports all five. If you find yourself
about to write `if mismatch.significance > ...` in this file, stop: you are about to
create a second opinion that disagrees with the pipeline's, and the two will diverge
under load at 03:00 with no error raised anywhere. The pipeline decides; the server
carries.

WHY FastAPI HERE, WHEN README.md RECORDS THE OPPOSITE DECISION
README's decision register says the web app is stdlib + zero network, chosen over
FastAPI because "a demo that needs `pip install` or a CDN is one venue-wifi failure
from not existing". That reasoning is still correct and it has NOT been overturned.
What changed is that this file needs a WebSocket, and the only stdlib route to one is
hand-rolling the RFC 6455 handshake and frame codec — which CLAUDE.md's LIBRARY-FIRST
rule forbids for exactly the right reason.

So the risk is managed by SEPARATION, not by argument:

    04_demo/app.py   — stdlib, zero dependencies, ALREADY RUN END TO END on the Mac.
                       Untouched by this file. THIS IS THE REHEARSED FALLBACK.
    03_src/server.py — this file. FastAPI + uvicorn. Push instead of poll, plus the
                       audit trail. If the import fails at the venue, run app.py.

Both serve the SAME page and the SAME /api/state payload, because /api/state here is
lane D's own State class imported rather than copied (see ConsoleBackend below). So
the fallback is a one-word change to a command line, not a change of product.

MEASURED, 2026-08-29, on the Mac's .venv/lib/python3.12/site-packages:
    pydantic 2.13.4 and pydantic_core PRESENT.
    fastapi, uvicorn, starlette, websockets: NOT PRESENT in the venv tree.
The venv is --system-site-packages, so they may exist in the framework Python; that
was not verifiable from the agent shell. The import guard at the bottom of this file
therefore prints the exact install command rather than a traceback, because a
ModuleNotFoundError at 09:00 on Sunday costs ten minutes of the wrong kind.

THE THREE PROTOCOL PROPERTIES, AND WHERE EACH ONE IS IMPLEMENTED
contracts.py section 8 states three properties the console layer must have. They are
not decorative and each one has a named home here:

  ORDERING  — `_next_seq()`. One monotonic, gap-free counter allocated UNDER THE SAME
              LOCK that queues the event, so wire order and seq order cannot disagree.
              A client that sees 411 then 413 knows it lost 412.
  RECOVERY  — `_snapshot()` + the first message on /ws. A refresh at minute three of a
              four-minute pitch repaints the whole picture from one message. The
              snapshot carries the `last_seq` it corresponds to, so the client resumes
              at last_seq+1 instead of double-applying or skipping.
  PROVENANCE— `SensorNode` bookkeeping in `_note_node()` and `_watchdog()`. "The queue
              went quiet" and "the Pi died" are OPPOSITE findings that look identical
              on a map. last_seen is the evidence; `online` is the belief; both ship.

BROADCAST ON CHANGE, NOT ON A TIMER — AND WHY THAT IS A CORRECTNESS RULE
A queue that reshuffles every second looks broken, because a watch officer reads
motion as new information. Worse, it IS broken as evidence: re-emitting an unchanged
ranking makes "the ranking changed" unobservable, and criterion 2's whole claim is
that the order means something. So every emitter here is gated on a CONTENT
FINGERPRINT (`_queue_key`, `_verdict_key`, `_contact_key`) and stays silent when the
content is identical. The one background timer in this file is the node watchdog, and
it too emits only on a state FLIP — see `_watchdog()` for why that is not an exception
to this rule.

RUN IT
    python 03_src/server.py --scene 04_demo/out/scene01
    open http://127.0.0.1:8000

    # edge node (unchanged — its default POST path is aliased below):
    python3 04_demo/pi_sensor.py --post http://<mac-ip>:8000/api/contacts ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

# --------------------------------------------------------------------------------
# Import bootstrap.
#
# WHY sys.path AND NOT A PACKAGE: `03_src` and `04_demo` both begin with a digit and
# are therefore not legal Python package names. LIBRARIES.md settles the convention as
# sys.path + flat imports; FILE_OWNERSHIP.md trap 6 says do not invent a different one.
#
# 04_demo is on the path because this server ORCHESTRATES lane D's runner rather than
# re-sequencing the pipeline itself. That direction of dependency (03_src -> 04_demo)
# is unusual and deliberate: run_pipeline.run_scene() is the ONLY tested sequencing of
# the five modules, and a second copy of it in this file would be a second product.
# --------------------------------------------------------------------------------

_SRC = Path(__file__).resolve().parent
_ROOT = _SRC.parent
_DEMO = _ROOT / "04_demo"
for _p in (str(_SRC), str(_DEMO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evidence as evidence_mod            # noqa: E402
import run_pipeline                        # noqa: E402
from contracts import (                    # noqa: E402
    ConsoleState,
    ContactUpdateEvent,
    EoContact,
    EvidenceRecord,
    NodeStatusEvent,
    OperatorAction,
    OperatorActionAckEvent,
    PriorityScore,
    QueueUpdateEvent,
    SensorKind,
    SensorNode,
    StreamEvent,
    Verdict,
    VerdictUpdateEvent,
)
from source_switch import (                # noqa: E402  (lane F)
    DEFAULT_SOURCE,
    LABEL_TO_KIND,
    SourceSwitch,
    normalise as normalise_source,
)
from pydantic import TypeAdapter, ValidationError  # noqa: E402

# THE ONE LINE contracts.py SECTION 8 ASSIGNS TO THIS LANE.
#
# contracts.py deliberately holds no TypeAdapter, so that pi_sensor.py can import it on
# a Raspberry Pi with no web stack. The adapter belongs to whichever module owns the
# transport, and that is this one.
#
# It is used here for the OUTBOUND dump rather than an inbound parse: dumping through
# the union guarantees the bytes on the wire are exactly what a client validating
# against `StreamEvent` will accept. Dumping the concrete subclass directly would work
# today and would stop working silently the day the union gains a member.
_EVENTS: TypeAdapter[Any] = TypeAdapter(StreamEvent)

DEFAULT_AUDIT_PATH = _DEMO / "operator_audit.jsonl"
SERVER_VERSION = "server/0.1.0"

# Policy constants. Named, not inlined, because each one is a judgement someone will
# ask about on stage.
# LANE F 2026-08-29: 30.0 -> 8.0. DERIVED, not taste. The edge node posts contacts
# every --interval (default 1.0 s) and a heartbeat every --heartbeat (default 2.0 s)
# when the sea is empty, so 8 s is FOUR consecutive missed heartbeats before a node is
# called dead — comfortably past ordinary jitter, and short enough to be useful inside
# a five-minute pitch. At the old 30 s a Pi unplugged on stage stayed green for half a
# minute, which is most of the slot. Do not lower it below ~3x the heartbeat interval:
# a threshold under that flaps on one dropped packet, and a strip that flickers between
# green and red teaches the operator to ignore it.
NODE_STALE_AFTER_S = 8.0      # see _watchdog(): the online/offline policy call
WATCHDOG_TICK_S = 1.0         # how often staleness is RE-EVALUATED (not re-broadcast)
SUBSCRIBER_QUEUE_MAX = 256    # see _broadcast(): why overflow closes rather than drops
MJPEG_BOUNDARY = "frame"


def _now() -> datetime:
    """UTC, always aware. contracts.AwareDatetime rejects naive datetimes at every
    boundary; producing one here would fail at the model, which is the point."""
    return datetime.now(timezone.utc)


# ================================================================================
# 1. CHANGE FINGERPRINTS — the mechanism behind "broadcast on change, not on a timer".
#
# Each function reduces an object to the tuple of fields whose change an OPERATOR
# would care about. Two objects with equal keys produce no event, however many times
# the pipeline re-ran.
#
# WHY FIELD TUPLES AND NOT `==` ON THE MODEL: every model here is frozen, so equality
# is well defined — but it also covers fields that churn without meaning anything.
# `Verdict.decided_at_utc` changes on every single recompute; comparing whole objects
# would therefore emit a verdict_update every pass and the "broadcast on change" rule
# would be silently dead while appearing to be implemented. The keys below exclude
# exactly those fields, and that exclusion IS the rule.
# ================================================================================

def _round(x: float | None, places: int = 3) -> float | None:
    """
    Quantise before comparing.

    WHY: confidence and score are floats computed through a long chain. Two runs over
    identical inputs can differ in the 15th decimal place, and an un-quantised
    comparison would report that as a change — reproducing the every-second reshuffle
    this whole section exists to prevent. Three places is finer than anything the
    console displays (it shows two), so no visible change can hide below the rounding.
    """
    return None if x is None else round(x, places)


def _verdict_key(v: Verdict) -> tuple:
    """What makes a verdict newsworthy: the label, how sure it is, which deception,
    whether it defers, and which comparisons produced it. NOT decided_at_utc."""
    return (v.verdict_id, v.label, _round(v.confidence), v.spoof_subtype,
            v.defer_to_human, tuple(sorted(v.defer_reasons)),
            tuple(sorted(v.mismatch_ids)))


def _queue_key(queue: list[PriorityScore]) -> tuple:
    """
    The ranking as a total order.

    Rank AND verdict_id AND score together: a re-ordering with identical scores is a
    change (the operator is being told to go somewhere else), and a score drift with
    an identical order is also a change (the argument moved even though the conclusion
    did not). Either alone would miss one of those.
    """
    return tuple((p.rank, p.verdict_id, _round(p.score), p.scarce_asset_recommended)
                 for p in queue)


def _contact_key(c: EoContact) -> tuple:
    """
    What makes a raw detection newsworthy before adjudication.

    Bearing and range are rounded harder than confidence: at 5 km, lane B measured
    cross-range sigma at 218 m, so a 0.01-degree bearing wobble is far inside the
    sensor's own noise and re-drawing the map for it would be showing the operator
    jitter as if it were motion.
    """
    return (c.contact_id, _round(c.observed_bearing_deg_true, 2),
            _round(c.observed_range_m, 0), c.observed_class,
            _round(c.detection_confidence, 2), c.track_length_frames)


def _node_key(n: SensorNode) -> tuple:
    """A node is newsworthy when it comes up, goes down, or re-reports a MEASURED rate.
    last_seen alone is excluded on purpose — it advances on every single POST, and
    emitting for it would turn the provenance strip into the timer this file refuses
    to be."""
    return (n.node_id, n.kind, n.online, _round(n.measured_fps, 1))


# ================================================================================
# 2. THE BACKEND — one object holding the whole picture, behind one lock.
# ================================================================================

class ConsoleBackend:
    """
    Everything the shore station knows.

    CONCURRENCY MODEL, STATED ONCE SO NOBODY HAS TO INFER IT:
      * All mutation happens inside `self._lock`, an asyncio.Lock. There is exactly one
        event loop and no threads mutate state.
      * The pipeline itself is CPU-bound and synchronous, so it runs in
        `asyncio.to_thread` — but OUTSIDE the lock's critical section, with the result
        installed under the lock. Holding an asyncio.Lock across a multi-hundred-
        millisecond CPU burn would stall every WebSocket send and the console would
        stutter exactly when the pipeline is busiest, i.e. when it matters.
      * Consequence, stated rather than discovered: two overlapping recomputes are
        possible, and the second to finish wins. That is correct for a watch screen —
        the operator wants the CURRENT picture, never a backlog of stale ones. It is
        the same reasoning app.py records for using a lock rather than a queue.
    """

    def __init__(
        self,
        scene_dir: Path,
        *,
        audit_path: Path,
        node_stale_after_s: float = NODE_STALE_AFTER_S,
        allow_real_identities: bool = False,
        initial_source: str = DEFAULT_SOURCE,
    ) -> None:
        self._lock = asyncio.Lock()
        self._audit_path = audit_path
        self._node_stale_after_s = node_stale_after_s
        self._allow_real = allow_real_identities

        # ONE ANONYMISER FOR THE WHOLE RUN — evidence.py's docstring is explicit that
        # this must be shared, and the failure mode if it is not is subtle and total:
        # Anonymiser numbers by FIRST-SEEN order, so a fresh instance per record
        # allocates index 0 every time and EVERY vessel on screen becomes 999000001.
        # The console would look correctly anonymised and would have merged sixteen
        # hulls into one.
        self._anon = evidence_mod.Anonymiser()
        self._started_at = _now()
        self._started_monotonic = time.monotonic()

        # THE /api/state COMPATIBILITY PATH.
        #
        # This is lane D's own State class, IMPORTED, not reimplemented. Lane D's
        # index.html polls /api/state and expects a specific payload — mode,
        # infrastructure, asset, caveat, scoring, counts, records. Hand-copying that
        # assembly into this file would create two producers of one shape, and they
        # would drift the first time D adds a field.
        #
        # The coupling is deliberate and it fails LOUDLY: if lane D renames State or
        # recompute(), this raises at server startup, in the open, rather than
        # producing a subtly wrong payload on stage.
        from app import State as _DemoState  # noqa: PLC0415  (see comment above)

        self._demo = _DemoState()
        self._demo.scene = run_pipeline.load_scene(scene_dir)
        self.scene_dir = scene_dir

        # Stream bookkeeping.
        self._seq = 0
        self._subscribers: set[asyncio.Queue[bytes]] = set()

        # The picture, in contract types.
        # TWO RECORD STORES, AND THE SPLIT IS THE SAFEGUARD.
        #   _records        REAL identities. Never leaves this process except through
        #                   /evidence, which has its own two gates.
        #   _public_records PSEUDONYMISED. Everything the console can see — the
        #                   WebSocket snapshot, every stream event, /api/state.
        # Holding one list and remembering to scrub at each of four call sites is how
        # a real MMSI reaches a projector. Holding two makes the safe one the default.
        self._records: list[EvidenceRecord] = []
        self._public_records: list[EvidenceRecord] = []
        self._records_by_id: dict[str, EvidenceRecord] = {}
        self._unresolved: list[EoContact] = []
        self._nodes: dict[str, SensorNode] = {}
        self._actions: list[OperatorAction] = []
        # contact_id -> node_id. Transport provenance, kept OFF EoContact on purpose;
        # see _node_for_contact() for why that separation is not a style choice.
        self._contact_node: dict[str, str] = {}

        # ---- SCRIPTED INJECTION (lane E) ---------------------------------------
        # The pristine scene, captured ONCE. Every injection is expressed as a
        # transform of this tuple rather than as an edit of the live list, so "reset"
        # is exact rather than approximate — an undo built by re-editing state cannot
        # prove it arrived back where it started, and on stage there is no time to
        # check.
        self._scene_pristine: tuple[EoContact, ...] = tuple(
            (self._demo.scene or {}).get("contacts", []))
        self._injection: dict[str, Any] | None = None

        # ---- LANE F: source failover -------------------------------------------
        # The active-source authority. It holds NO data — see source_switch.py's
        # docstring for why that is the whole point.
        self.source = SourceSwitch(active=initial_source, operator_id="startup")

        # THE BUFFER THAT MAKES THE SWITCH FAST.
        #
        # Every ingest stores its contacts here keyed by sensor kind, INCLUDING posts
        # from a node that is not currently the authority. So when the operator switches
        # to MAC_CAMERA, the Mac camera's most recent contacts are ALREADY IN MEMORY and
        # the switch costs one pipeline recompute rather than a cold start waiting for
        # the next post.
        #
        # The operational consequence, which belongs on the run sheet: RUN BOTH NODES
        # FROM THE BEGINNING. The switch selects between two streams that are already
        # flowing. Starting the second node at the moment of failure turns a sub-second
        # switch into however long that node takes to boot, load YOLO and see something.
        self._last_by_kind: dict[str, list[EoContact]] = {}
        self._last_seen_by_kind: dict[str, datetime] = {}

        # Existing audit lines, counted ONCE. The trail is append-only across runs on
        # purpose: a restart mid-demo must not reset the record of what the operator
        # already decided.
        self._audit_lines = (
            sum(1 for _ in audit_path.open(encoding="utf-8"))
            if audit_path.exists() else 0)

        # Fingerprints of what the subscribers have already been told.
        self._sent_verdicts: dict[str, tuple] = {}
        self._sent_contacts: dict[str, tuple] = {}
        self._sent_queue: tuple = ()
        self._sent_nodes: dict[str, tuple] = {}

        # The replayed scene is itself a sensor with a provenance story: its contacts
        # were projected from AIS, not observed. Registering it as a `file` node means
        # the console can never show a contact with no node behind it, which is the
        # unattributable-claim case criterion 4 exists to prevent.
        self._nodes["scene-file"] = SensorNode(
            node_id="scene-file", kind="file", online=True, last_seen=_now(),
            measured_fps=None)

    # ---------------------------------------------------------------- properties
    @property
    def uptime_s(self) -> float:
        return time.monotonic() - self._started_monotonic

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def nodes(self) -> list[SensorNode]:
        """Read-only view for /health. A copy, not the dict: the caller must not be
        able to mutate the provenance record it is reporting on."""
        return list(self._nodes.values())

    @property
    def audit_lines(self) -> int:
        return self._audit_lines

    @property
    def pipeline_version(self) -> str:
        return evidence_mod.PIPELINE_VERSION

    def _next_seq(self) -> int:
        """
        ORDERING, per contracts.py section 8.

        Called ONLY from inside `self._lock`, immediately before the event is queued to
        every subscriber. Allocating a seq anywhere else — or before an await — would
        let two events reach the wire in the opposite order to their numbers, and the
        client's gap detector would then report losses that never happened.
        """
        self._seq += 1
        return self._seq

    # ------------------------------------------------------------------ streaming
    def _queue_event(self, event: Any) -> None:
        """
        Serialise once, fan out to every subscriber. Caller MUST hold `self._lock`.

        WHY ONE SERIALISATION FOR ALL SUBSCRIBERS: the bytes are the evidence. If each
        connection re-serialised, two operators could in principle be looking at
        different renderings of one event. One dump, one truth.

        WHY A FULL QUEUE CLOSES THE CONNECTION INSTEAD OF DROPPING THE EVENT: dropping
        would break the gap-free seq guarantee that section 8 of contracts.py rests on
        — the client would see 411 then 413 and correctly conclude it lost an event,
        with no way to recover except a reconnect. So we skip the pretence and force
        the reconnect, which delivers a fresh ConsoleState and a consistent picture.
        A slow client gets a brief blank instead of a permanently wrong screen.
        """
        raw = _EVENTS.dump_json(event)
        dead: list[asyncio.Queue[bytes]] = []
        for q in self._subscribers:
            try:
                q.put_nowait(raw)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)
            # Sentinel: the reader task sees b"" and closes the socket. A bare discard
            # would leave that task awaiting a queue nobody feeds again — the socket
            # would stay open, showing a frozen picture, which is worse than a visibly
            # dropped connection because nothing on screen says it is stale.
            #
            # The queue is FULL by definition here, so room has to be made first. We
            # discard the OLDEST buffered event rather than the newest: this client is
            # being disconnected regardless, and the sentinel must arrive.
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(b"")
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue[bytes]:
        """Register a WebSocket. Bounded on purpose — see _queue_event."""
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[bytes]) -> None:
        self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------------- snapshot
    def _snapshot(self) -> ConsoleState:
        """
        RECOVERY, per contracts.py section 8. The entire picture in one message.

        `last_seq` is read from the same counter that stamps events, while the caller
        holds the lock, so the snapshot and the stream cannot describe different
        moments. The client applies this, then ignores every buffered event with
        seq <= last_seq. Getting that join wrong is how a console silently
        double-applies or skips, and both look like a UI bug rather than a protocol
        one — which is how an hour disappears.

        The ranked queue is NOT duplicated into a field of its own: it is
        reconstructible from record.priority.rank, and a second ordered copy is a
        second thing that can disagree with the first.
        """
        return ConsoleState(
            state_time_utc=_now(),
            last_seq=self._seq,
            pipeline_version=self.pipeline_version,
            nodes=list(self._nodes.values()),
            records=list(self._public_records),
            unresolved_contacts=list(self._unresolved),
            recent_actions=list(self._actions[-50:]),
        )

    async def snapshot_json(self) -> bytes:
        async with self._lock:
            return self._snapshot().model_dump_json().encode("utf-8")

    # -------------------------------------------------------------------- sensors
    def _note_node(
        self,
        node_id: str,
        kind: SensorKind,
        *,
        measured_fps: float | None,
    ) -> None:
        """
        Record that a node was heard from. Caller holds the lock.

        THE ONE POLICY CALL contracts.py EXPLICITLY DELEGATES HERE: `online` is the
        shore station's current belief and `last_seen` is the evidence for it, and
        contracts.py refuses to derive one from the other because a threshold is logic.
        This is the threshold: heard from within `node_stale_after_s` -> online.
        Both fields ship to the console so the operator can audit the belief against
        its evidence.

        measured_fps IS NOT INVENTED HERE, and this is the important half. The server
        can measure how often a node POSTs; pi_sensor.py batches on --interval, so
        post rate is NOT frame rate. Substituting one for the other would put a
        confident number next to a sensor that never produced it — the exact failure
        contracts.SensorNode.measured_fps is written to prevent, and a CLAUDE.md
        MEASURED-NUMBERS-ONLY violation. It stays None unless the node itself reports
        a counted rate.
        """
        prev = self._nodes.get(node_id)
        node = SensorNode(
            node_id=node_id,
            kind=kind,
            online=True,
            last_seen=_now(),
            measured_fps=measured_fps if measured_fps is not None
            else (prev.measured_fps if prev else None),
        )
        self._nodes[node_id] = node
        key = _node_key(node)
        if self._sent_nodes.get(node_id) != key:
            self._sent_nodes[node_id] = key
            self._queue_event(NodeStatusEvent(
                seq=self._next_seq(), sent_at_utc=_now(), node=node))

    async def watchdog_tick(self) -> None:
        """
        Re-evaluate staleness. Emits ONLY when a node's online flag actually flips.

        WHY THIS IS NOT AN EXCEPTION TO "BROADCAST ON CHANGE, NOT ON A TIMER":
        the rule forbids re-sending unchanged content on a schedule. A node going
        silent is a genuine change in the picture, but nothing arrives to announce it —
        the whole signal is an ABSENCE, and an absence can only be noticed by looking.
        So the timer drives DETECTION and the fingerprint still gates EMISSION. A
        server sitting next to a dead Pi sends exactly one event: the flip.

        This matters more than it sounds. "The queue went quiet" and "the sensor died"
        produce an identical empty map. Without this tick the console cannot tell an
        operator which one they are looking at, and one of those means stand down while
        the other means the tool is blind.
        """
        async with self._lock:
            now = _now()
            for node_id, node in list(self._nodes.items()):
                if node.kind == "file":
                    continue  # a replayed scene does not go stale; it is a file
                stale = (
                    node.last_seen is None
                    or (now - node.last_seen).total_seconds() > self._node_stale_after_s
                )
                if node.online == (not stale):
                    continue
                flipped = SensorNode(
                    node_id=node.node_id, kind=node.kind, online=not stale,
                    last_seen=node.last_seen, measured_fps=node.measured_fps)
                self._nodes[node_id] = flipped
                self._sent_nodes[node_id] = _node_key(flipped)
                self._queue_event(NodeStatusEvent(
                    seq=self._next_seq(), sent_at_utc=now, node=flipped))

    # ------------------------------------------------------------------- pipeline
    async def replace_tracks(self, tracks: list, *, reason: str) -> None:
        """Swap the AIS picture, then re-run the pipeline. main.py's replay hook.

        WHY THIS EXISTS: load_scene() reads ais_tracks.jsonl ONCE at construction, so
        the AIS side of this server was frozen at startup. Contacts could arrive over
        time (POST /ingest/contacts) but claims could not, which means the one thing a
        replay is for -- watching a claim go stale, a vessel go quiet, a track loiter --
        could not happen. criterion 2 ranks BEHAVIOUR, and behaviour needs a clock.

        WHY IT IS A METHOD AND NOT A ROUTE: the replay driver runs in this process, in
        this event loop. An HTTP route would serialise every AisTrack to JSON and parse
        it back for no reason, and would open a way to inject claims from off-machine,
        which is a thing a shore station should not accept.

        The lock is the same one every other mutation takes, so a replay tick cannot
        interleave with an ingest batch and leave the pipeline reading half of each.
        """
        async with self._lock:
            if self._demo.scene is not None:
                self._demo.scene["tracks"] = tracks
        await self.recompute(reason=reason)

    async def recompute(self, *, reason: str) -> None:
        """
        Run the pipeline, then emit ONLY what changed.

        THE SEQUENCE IS LANE D'S, NOT THIS FILE'S. `self._demo.recompute()` calls
        run_pipeline.run_scene(), which sequences association -> consistency -> verdict
        -> prioritizer -> evidence. This method adds no stage and skips none.

        WHY THE CPU WORK IS OUTSIDE THE LOCK: see the class docstring. A hundreds-of-
        milliseconds burn under an asyncio.Lock stalls every WebSocket send.
        """
        # Snapshot the inputs the pipeline needs, then release. Nothing below this
        # line touches self.* until the result is installed.
        await asyncio.to_thread(self._demo.recompute)
        result = self._demo.result or {}

        # REBUILD THE TYPED VIEW FROM THE DICTS run_scene RETURNED.
        #
        # run_scene emits model_dump(mode="json") because that is what lane D's page
        # eats. The stream layer is contract-typed and cannot accept bare dicts —
        # CLAUDE.md forbids passing one across a module boundary. Re-VALIDATING the
        # dicts is the honest way to bridge that: it is one source of truth (the
        # pipeline ran once) and the validation itself is a free contract check. The
        # alternative — running the pipeline a second time to get objects — would be
        # two answers to one question, and they could disagree.
        records: list[EvidenceRecord] = []
        for raw in result.get("records", []):
            try:
                records.append(EvidenceRecord.model_validate(raw))
            except ValidationError as exc:
                # Loud, not silent. A record the contract rejects is a pipeline bug and
                # must not be quietly dropped from the operator's picture — a missing
                # contact reads as an empty sea.
                print(f"[server] REJECTED a record from run_scene: {exc}",
                      file=sys.stderr)

        # PSEUDONYMISE BEFORE ANYTHING OUTBOUND TOUCHES IT.
        #
        # THE HOLE THIS CLOSES, STATED PLAINLY: run_scene() returns
        # record.model_dump() with REAL claimed_mmsi, claimed_name, claimed_imo and
        # claimed_callsign. Both /api/state and the WebSocket served those verbatim.
        # On the synthetic demo scene that is harmless because the identities are
        # already 999-prefixed — which is exactly what makes it dangerous: it is
        # INVISIBLE until the day someone points the server at the real Fehmarn golden
        # window, and then a real named vessel appears on a projector under a SPOOF
        # badge. CLAUDE.md calls that non-negotiable and it would have happened by
        # default, not by mistake.
        #
        # record_to_dict() is used rather than a local scrub for one specific reason:
        # it runs assert_no_real_identities() over the SERIALISED payload, so every
        # record that reaches the console is verified, not merely processed. A scrub
        # that silently missed a field would raise here instead of shipping.
        public: list[EvidenceRecord] = []
        for rec in records:
            if self._allow_real:
                public.append(rec)
                continue
            try:
                public.append(EvidenceRecord.model_validate(
                    evidence_mod.record_to_dict(rec, anonymiser=self._anon)["record"]))
            except Exception as exc:
                # DROP THE RECORD, DO NOT SHIP IT RAW. A record that cannot be
                # pseudonymised is a record that must not be displayed; the fallback
                # for a failed scrub can never be "show it anyway".
                print(f"[server] WITHHELD record {rec.record_id} — pseudonymisation "
                      f"failed: {type(exc).__name__}: {exc}", file=sys.stderr)

        contacts_in_records = {
            r.eo_contact.contact_id for r in records if r.eo_contact is not None}
        source_contacts = (
            self._demo.live_contacts if self._demo.mode == "live"
            else (self._demo.scene or {}).get("contacts", []))
        # Seen but not yet adjudicated. Without this the map is blank for every fresh
        # detection until a verdict exists, and an operator reads a blank map as an
        # empty sea rather than as a pipeline that has not caught up.
        unresolved = [c for c in source_contacts
                      if c.contact_id not in contacts_in_records]

        async with self._lock:
            self._records = records
            self._public_records = public
            self._records_by_id = {r.record_id: r for r in records}
            self._unresolved = unresolved
            # PUBLIC records go to the stream. EoContact carries no identity by
            # construction, so unresolved contacts need no scrub.
            self._emit_changes(public, unresolved, reason=reason)

    def _emit_changes(
        self,
        records: list[EvidenceRecord],
        unresolved: list[EoContact],
        *,
        reason: str,
    ) -> None:
        """
        Diff the new picture against what subscribers were last told, and emit the
        difference. Caller holds the lock.

        ORDER OF EMISSION IS DELIBERATE: contacts, then verdicts, then the queue.
        A console that receives a queue_update naming a verdict it has never seen has
        to render a rank with no card behind it. Emitting the evidence before the
        ranking that cites it means every event the client applies references
        something it already holds.
        """
        now = _now()

        # --- 1. contacts (including still-unadjudicated ones) -------------------
        for rec in records:
            c = rec.eo_contact
            if c is None:
                continue
            key = _contact_key(c)
            if self._sent_contacts.get(c.contact_id) == key:
                continue
            self._sent_contacts[c.contact_id] = key
            # THE WALL, ON THE WIRE. eo_contact and ais_track are two fields, never a
            # merged "vessel". Which side is None is still the finding — and a merged
            # payload would be the easiest thing here to write and would delete
            # criterion 3 from the product without raising anything.
            # association=None DESPITE the associator having run. run_scene() does
            # not surface the Association objects it built, and fabricating one here
            # would be this server inventing a pairing quality it did not compute —
            # the one number a console must never guess, because assoc_ambiguous is a
            # defer-to-human trigger. None is the honest value; the pairing's facts
            # still reach the operator through the record's limitations, which
            # build_record() folds them into. Filed to lane D in requests.md.
            self._queue_event(ContactUpdateEvent(
                seq=self._next_seq(), sent_at_utc=now,
                association=None,
                eo_contact=c, ais_track=rec.ais_track,
                node_id=self._node_for_contact(c)))

        for c in unresolved:
            key = _contact_key(c)
            if self._sent_contacts.get(c.contact_id) == key:
                continue
            self._sent_contacts[c.contact_id] = key
            self._queue_event(ContactUpdateEvent(
                seq=self._next_seq(), sent_at_utc=now,
                association=None, eo_contact=c, ais_track=None,
                node_id=self._node_for_contact(c)))

        # --- 2. verdicts, with the comparisons that produced them ---------------
        for rec in records:
            v = rec.verdict
            key = _verdict_key(v)
            if self._sent_verdicts.get(v.verdict_id) == key:
                continue
            self._sent_verdicts[v.verdict_id] = key
            # The mismatches ride along rather than being fetched separately: the
            # console must be able to show CLAIMED versus OBSERVED at the instant the
            # SPOOF label lands. A label on screen with no evidence beside it during a
            # round trip is a bare accusation, which is the output criterion 4 forbids.
            self._queue_event(VerdictUpdateEvent(
                seq=self._next_seq(), sent_at_utc=now,
                verdict=v, mismatches=list(rec.mismatches)))

        # --- 3. the ranked queue — criterion 2 on screen ------------------------
        queue = [r.priority for r in records if r.priority is not None]
        queue.sort(key=lambda p: p.rank)
        qkey = _queue_key(queue)
        if qkey != self._sent_queue:
            self._sent_queue = qkey
            # WHOLE LIST, NEVER A DELTA. A ranking is a total order; patching entry 3
            # in isolation leaves the console displaying an order the prioritiser never
            # produced, and "send the boat here" stops being defensible.
            self._queue_event(QueueUpdateEvent(
                seq=self._next_seq(), sent_at_utc=now, queue=queue))

    def _node_for_contact(self, contact: EoContact) -> str | None:
        """
        Transport-level provenance: which box produced this observation.

        Deliberately NOT stored on EoContact — the contact is what the camera saw, and
        which sensor it was plugged into is not an observed property of the vessel.
        The mapping is by ingest batch; a contact that arrived with the replayed scene
        is attributed to the scene file, which is itself a provenance statement (those
        contacts were PROJECTED from AIS, not observed).
        """
        return self._contact_node.get(contact.contact_id, "scene-file")

    # =====================================================================
    # SCRIPTED INJECTION — the demo happens when the operator presses the key,
    # not when the data happens to arrive.
    #
    # WHY THIS IS NOT CHEATING, AND WHY THE LABEL IS NOT COSMETIC.
    # A four-minute pitch cannot wait for a spoofer to appear in recorded traffic. The
    # dishonest version of this is a scene quietly rigged so the "live" moment always
    # lands; the honest version is a scene the operator visibly triggers and the tool
    # visibly labels. So the label goes INSIDE the contract object, not beside it:
    #
    #     contact_id  "inj-c-0004"
    #     frame_ref   "injected://spoof/<the original frame_ref>"
    #
    # frame_ref is a real EoContact field and it is copied into
    # EvidenceRecord.frame_refs by evidence.build_record(). That means the word
    # INJECTED survives into the EXPORTED CASE FILE at /evidence/{id} — a judge
    # downloading the evidence sees it without being told, and there is no code path
    # in this server that can produce an injected contact whose provenance is absent.
    # A UI-only badge would be a promise; this is a property.
    #
    # WHAT THE INJECTION DOES NOT TOUCH: bearing, range, their uncertainties, and the
    # camera pose. Association is geometry-only, so leaving the geometry pristine is
    # what guarantees the injected contact pairs with the SAME AIS claim the original
    # did. Only the attributes under comparison are changed, which is precisely what a
    # spoof IS — a claim and an observation that disagree about what the hull is while
    # agreeing about where it is.
    # =====================================================================

    INJECTION_NODE = "demo-injection"

    def _rebuild_scene_contacts(self) -> None:
        """Apply the current injection to the scene. Caller holds the lock.

        Always rebuilt from `_scene_pristine`, never from the last result, so applying
        two injections in a row cannot compound and `reset` is exact."""
        inj = self._injection
        if inj is None:
            contacts = list(self._scene_pristine)
        else:
            suppressed = set(inj["suppressed"])
            contacts = [c for c in self._scene_pristine
                        if c.contact_id not in suppressed] + list(inj["injected"])
        if self._demo.scene is not None:
            self._demo.scene["contacts"] = contacts

    def _pick_injection_target(self, contact_id: str | None) -> EvidenceRecord:
        """
        Choose which matched contact the scenario acts on.

        DEFAULT: the matched record with the WORST current rank. That is a demo
        decision stated out loud rather than hidden — injecting into the contact that
        is already first would move nothing, and criterion 2's claim is that the order
        MEANS something, which is only visible when the order changes.

        The chosen id is returned to the caller so a rehearsed demo can PIN it. A
        default that depends on the current ranking is reproducible within a run and
        not across changes to the scene; pinning removes that dependency entirely.
        """
        matched = [r for r in self._records
                   if r.ais_track is not None and r.eo_contact is not None]
        if not matched:
            raise ValueError(
                "no matched contact to inject into — the scene has no record carrying "
                "both an AIS claim and a camera contact, and a spoof is by definition a "
                "disagreement between the two.")
        if contact_id:
            for r in matched:
                if r.eo_contact.contact_id == contact_id:
                    return r
            raise ValueError(
                f"contact_id {contact_id!r} is not a matched contact in the current "
                f"picture. Matched: "
                f"{sorted(r.eo_contact.contact_id for r in matched)}")
        return max(matched, key=lambda r: (r.priority.rank if r.priority else 0))

    def _build_spoof(self, rec: EvidenceRecord) -> tuple[EoContact, str]:
        """
        The identity-spoof scenario: same hull, same place, wrong ship.

        Length is driven to one sixth of the claim. Not an arbitrary number — the
        point is to land far outside `consistency`'s tolerance on a dimension whose
        uncertainty is honestly small, so the resulting sigma is a real measurement of
        a real disagreement rather than a threshold nudged until it fires. The class is
        moved to a NON-CONFUSABLE one so the two dimensions corroborate, which is what
        verdict.py requires before a class disagreement contributes to SPOOF at all.
        """
        c, t = rec.eo_contact, rec.ais_track
        claimed_len = t.claimed_length_m or (c.observed_length_m or 60.0)
        spoof_len = round(claimed_len / 6.0, 1)
        # A small hull observed where a large one is claimed. Pick a class that is not
        # confusable with the claim so the check is a real disagreement, not a coin toss.
        claimed_class = t.claimed_ship_type
        observed_class = "small_craft" if claimed_class in (
            "tanker", "cargo", "passenger", "naval") else "tanker"
        injected = c.model_copy(update={
            "contact_id": f"inj-{c.contact_id}",
            "frame_ref": f"injected://spoof/{c.frame_ref}",
            "observed_length_m": spoof_len,
            "observed_length_uncertainty_m": max(2.0, spoof_len * 0.12),
            "observed_class": observed_class,
            "observed_class_confidence": 0.93,
        })
        note = (f"claimed {claimed_class or 'unknown'} {claimed_len:.0f} m; injected "
                f"observation {observed_class} {spoof_len:.0f} m at the SAME bearing "
                f"and range")
        return injected, note

    def _build_dark(self, rec: EvidenceRecord) -> tuple[EoContact, str]:
        """
        The dark-vessel scenario: a hull where nothing is claimed.

        Built by displacing an existing contact's BEARING by 4 degrees — far enough
        that association finds no claim within gate, near enough that it is plainly in
        the camera's field of view. Nothing else changes, so the contact remains a
        physically coherent observation rather than a shape invented to trip a rule.
        """
        c = rec.eo_contact
        injected = c.model_copy(update={
            "contact_id": f"inj-dark-{c.contact_id}",
            "frame_ref": f"injected://dark/{c.frame_ref}",
            "observed_bearing_deg_true": (c.observed_bearing_deg_true + 4.0) % 360.0,
        })
        return injected, ("a hull observed 4 degrees off an existing contact, where no "
                          "transponder is claiming anything")

    async def inject(
        self,
        scenario: str,
        *,
        operator_id: str,
        contact_id: str | None = None,
        contacts: list[EoContact] | None = None,
    ) -> dict[str, Any]:
        """
        Run a prepared scenario on command. Returns what it did and how long it took.

        PINS THE SOURCE TO RECORDED. A scripted moment that a live node can overwrite
        two seconds later is not scripted. Pinning is also the honest state to be in:
        the scene is the T1 guaranteed path, and the injection is a statement about
        that scene, not about a camera.
        """
        t0 = time.perf_counter()
        scenario = (scenario or "spoof").strip().lower()

        # RECORDED first, so the pipeline reads scene["contacts"] — the list the
        # transform below rewrites. Doing this after would race the next ingest.
        if scenario != "reset":
            self.source.switch("RECORDED", operator_id=operator_id,
                               reason=f"demo injection: {scenario}")

        async with self._lock:
            self._demo.mode = "replay"
            if scenario == "reset":
                self._injection = None
                self._rebuild_scene_contacts()
                summary = {"scenario": "reset", "injected": [], "suppressed": [],
                           "note": "scene restored to the state it was loaded in"}
            else:
                if contacts:
                    # A fully prepared file. Labelled on arrival — a caller cannot
                    # supply an injected contact that does not say so, because the
                    # label is applied here rather than trusted from the payload.
                    injected = [
                        c.model_copy(update={
                            "contact_id": c.contact_id if c.contact_id.startswith("inj-")
                            else f"inj-{c.contact_id}",
                            "frame_ref": c.frame_ref if c.frame_ref.startswith("injected://")
                            else f"injected://{scenario}/{c.frame_ref}",
                        }) for c in contacts]
                    suppressed: list[str] = []
                    note = f"{len(injected)} contact(s) supplied by the caller"
                    target_id = None
                else:
                    rec = self._pick_injection_target(contact_id)
                    target_id = rec.eo_contact.contact_id
                    if scenario == "spoof":
                        one, note = self._build_spoof(rec)
                        suppressed = [target_id]
                    elif scenario == "dark":
                        one, note = self._build_dark(rec)
                        suppressed = []
                    else:
                        raise ValueError(
                            f"unknown scenario {scenario!r}. Known: spoof, dark, reset "
                            f"— or POST a 'contacts' list for a prepared file.")
                    injected = [one]

                self._injection = {
                    "scenario": scenario, "injected": injected,
                    "suppressed": suppressed, "note": note,
                    "target_contact_id": target_id,
                    "operator_id": operator_id, "at": _now().isoformat(),
                }
                self._rebuild_scene_contacts()
                # Provenance for the strip: the injection is a SOURCE, and it is a
                # `file` source because that is what it is — contacts from disk, not
                # from a lens. Registering it means no contact on screen lacks a node.
                for c in injected:
                    self._contact_node[c.contact_id] = self.INJECTION_NODE
                self._note_node(self.INJECTION_NODE, "file", measured_fps=None)
                summary = {"scenario": scenario,
                           "injected": [c.contact_id for c in injected],
                           "suppressed": suppressed, "note": note,
                           "target_contact_id": target_id}

        t1 = time.perf_counter()
        await self.recompute(reason=f"inject:{scenario}")
        t2 = time.perf_counter()
        summary["apply_ms"] = round((t1 - t0) * 1000, 1)
        summary["recompute_ms"] = round((t2 - t1) * 1000, 1)
        summary["server_seq"] = self._seq
        return summary

    @property
    def injection_state(self) -> dict[str, Any] | None:
        """What is injected right now, for /api/state and /health. None when clean."""
        inj = self._injection
        if inj is None:
            return None
        return {k: v for k, v in inj.items() if k != "injected"} | {
            "injected": [c.contact_id for c in inj["injected"]]}

    # -------------------------------------------------------------------- ingest
    async def ingest(
        self,
        contacts: list[EoContact],
        *,
        node_id: str,
        kind: SensorKind,
        measured_fps: float | None,
    ) -> None:
        """Accept observations from an edge node and re-run the pipeline against the
        same AIS claims. Mode flips to live, exactly as app.py does."""
        async with self._lock:
            # LIVENESS IS RECORDED FOR EVERY NODE, AUTHORITY OR NOT. A demoted node is
            # not a silent one: the console must be able to show the Pi coming back to
            # life while the Mac camera stays live, because "posting but not selected"
            # and "offline" are different sentences and only one of them is a fault.
            self._note_node(node_id, kind, measured_fps=measured_fps)
            if contacts:
                self._last_by_kind[kind] = contacts
                self._last_seen_by_kind[kind] = _now()

            promoted = self.source.accepts(kind, node_id)
            if not promoted:
                self.source.note_demoted(node_id, kind)
                return  # buffered above; not in the picture. No recompute, no events.

            self._demo.live_contacts = contacts
            self._demo.mode = "live"
            for c in contacts:
                self._contact_node[c.contact_id] = node_id
        await self.recompute(reason=f"ingest:{node_id}")

    async def note_heartbeat(self, *, node_id: str, kind: SensorKind,
                             measured_fps: float | None) -> None:
        """
        A node saying 'I am alive and I see nothing'.

        WHY THIS IS NOT JUST AN EMPTY ingest(). An empty ingest would set
        live_contacts=[] and trigger a full pipeline recompute once per heartbeat, for
        every node, forever. Worse, it would let a DEMOTED node's silence blank the
        active source's picture. This path touches liveness and nothing else.

        WHY IT HAS TO EXIST AT ALL: the node only posts when it HAS contacts, so a
        healthy sensor watching an empty sea posts nothing and the watchdog marks it
        dead. The console would then show a failed sensor and an empty map — the exact
        ambiguity SensorNode was designed to resolve, reintroduced from the sensor end.
        """
        async with self._lock:
            self._note_node(node_id, kind, measured_fps=measured_fps)

    async def set_source(self, to: str, *, operator_id: str = "operator",
                         reason: str | None = None) -> dict[str, Any]:
        """
        Switch the authoritative source. Does NOT restart anything and does NOT clear
        the queue — `self._records`, `self._public_records`, `self._actions` and the
        audit trail are untouched by every line below.

        Two timings are returned separately and on purpose: `switch_ms` is the authority
        change, `recompute_ms` is the pipeline re-running against the new source's
        buffered contacts. Reporting one combined number would hide which half is the
        cost, and the answer (the recompute) is the half nobody can shorten by switching
        faster.
        """
        t0 = time.perf_counter()
        ev = self.source.switch(to, operator_id=operator_id, reason=reason)
        kind = self.source.active_kind

        async with self._lock:
            if kind == "file":
                # RECORDED. State.recompute() reads scene["contacts"] whenever mode is
                # not "live", so the scene comes back with no reload from disk.
                self._demo.mode = "replay"
            else:
                buffered = self._last_by_kind.get(kind, [])
                self._demo.mode = "live"
                self._demo.live_contacts = buffered
                for c in buffered:
                    self._contact_node.setdefault(c.contact_id, f"{kind}-buffered")
            records_before = len(self._records)

        t1 = time.perf_counter()
        await self.recompute(reason=f"source_switch:{ev.to_source}")
        t2 = time.perf_counter()

        async with self._lock:
            records_after = len(self._records)
            buffered_n = len(self._last_by_kind.get(kind, []))
            last_seen = self._last_seen_by_kind.get(kind)

        return {
            "ok": True,
            "switch": ev.to_dict(),
            "switch_ms": round((t1 - t0) * 1000.0, 3),
            "recompute_ms": round((t2 - t1) * 1000.0, 1),
            "total_ms": round((t2 - t0) * 1000.0, 1),
            "buffered_contacts": buffered_n,
            "source_last_post_utc": last_seen.isoformat() if last_seen else None,
            "records_before": records_before,
            "records_after": records_after,
            "server_seq": self._seq,
            "status": self.source.status(),
        }

    # ------------------------------------------------------------- operator audit
    def _append_audit(self, action: OperatorAction, *, contact_known: bool) -> int:
        """
        Append one operator decision to the audit trail. Returns the line number.

        THIS IS A DELIVERABLE, NOT A LOG. It is the artefact that makes "decision
        support, never automated enforcement" CHECKABLE rather than merely asserted:
        every line names a human and a time, and there is no code path anywhere in this
        server that writes a decision of its own. A judge can open the file and see
        that every entry has an operator_id.

        THREE PROPERTIES, AND WHY EACH ONE IS NOT OPTIONAL:

          APPEND-ONLY. Opened "a", never "w", never rewritten. A file that can be
          edited after the fact is not an audit trail. In particular a DISMISS is
          RECORDED, not deleted — "what the operator chose not to chase" is exactly
          what an after-action review asks for, and it is the entry a system designed
          to flatter itself would drop.

          FLUSHED AND FSYNCED. Without the fsync the line lives in the OS page cache,
          and a demo laptop that loses power between the ack and the flush leaves the
          operator believing a decision is on the record when it is not. The ack this
          server returns is only honest if the bytes are on the disk before it is sent.

          THE CONTRACT OBJECT IS NESTED, NOT FLATTENED. Server-added facts
          (recorded_at_utc, server_seq, contact_known) sit OUTSIDE the "action" key, so
          a reader can tell at a glance what came from contracts.OperatorAction and
          what this transport layer added on top. It is the same separation
          evidence.record_to_dict() uses for its disclosure block, for the same reason.

        contact_known IS RECORDED RATHER THAN ENFORCED. An action naming a contact the
        server no longer holds is still a decision a human made, and refusing it would
        delete it from the trail. It is written with the flag set, so the anomaly is
        visible to a reviewer instead of being resolved by this file's opinion.
        """
        line = {
            "action": action.model_dump(mode="json"),
            "recorded_at_utc": _now().isoformat(),
            "server_seq": self._seq,
            "contact_known": contact_known,
            "pipeline_version": self.pipeline_version,
            "server_version": SERVER_VERSION,
        }
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        # Counted incrementally from a count taken once at startup, NOT by re-reading
        # the file. Re-reading is O(file) per action and runs on the event loop; it
        # would make the console stutter more the longer the demo has been running,
        # which is precisely backwards.
        self._audit_lines += 1
        return self._audit_lines

    async def record_action(self, action: OperatorAction) -> OperatorActionAckEvent:
        """
        Record a human decision and acknowledge it.

        `accepted` MEANS "WRITTEN TO THE RECORD", AND NOTHING ELSE. contracts.py is
        explicit that it must never be read as "the asset was tasked" — this system has
        no mechanism to task anything and no field to report an outcome. So `accepted`
        tracks the disk write and only the disk write: if the append raised, the
        operator is told immediately, because an operator who believes a dispatch
        decision is on the record when it is not will be contradicted later by the
        audit trail, in front of the people reviewing it.
        """
        async with self._lock:
            known = (
                action.contact_id in self._contact_node
                or any(r.eo_contact is not None
                       and r.eo_contact.contact_id == action.contact_id
                       for r in self._records)
            )
            try:
                line_no = self._append_audit(action, contact_known=known)
                accepted, detail = True, (
                    f"appended to {self._audit_path.name} line {line_no}"
                    + ("" if known else
                       "; WARNING contact_id not in the current picture — recorded "
                       "with contact_known=false rather than discarded"))
            except OSError as exc:
                # Do NOT swallow this. A silent failure here is the one bug in this
                # file that produces a confidently wrong audit trail.
                accepted, detail = False, f"AUDIT WRITE FAILED: {type(exc).__name__}: {exc}"
                print(f"[server] {detail}", file=sys.stderr)

            if accepted:
                self._actions.append(action)
            ack = OperatorActionAckEvent(
                seq=self._next_seq(), sent_at_utc=_now(),
                action=action, accepted=accepted, detail=detail)
            self._queue_event(ack)
            return ack

    # ------------------------------------------------------------------- evidence
    def evidence_payload(self, record_id: str, *, allow_real: bool) -> dict[str, Any]:
        """
        The case file for one record, pseudonymised by default.

        DEFAULT PSEUDONYMISED BECAUSE THIS IS AN HTTP ENDPOINT. evidence.py's rule is
        "set allow_real_identities=True only for the operator-facing local path;
        everything that leaves the machine takes the default" — and a URL is a thing
        that leaves the machine the moment anyone opens it on the projector. Real
        identities require BOTH the server flag and the query parameter, so no single
        mistake exposes a real named vessel through this route.

        `association` is not passed, so the payload carries no pairing_basis block:
        run_pipeline.run_scene() does not surface the Association objects it built.
        That is a REAL GAP in the evidence returned here, stated rather than hidden —
        build_record() already folds the pairing's facts into the rationale and the
        limitations, so the argument survives, but the machine-readable pairing block
        does not. Filed to lane D in 99_scratch/requests.md.
        """
        record = self._records_by_id.get(record_id)
        if record is None:
            # Second chance on verdict_id: the console displays verdicts, and an
            # operator reading an id off the screen should not have to know which of
            # the two identifiers this route wanted.
            for r in self._records:
                if r.verdict.verdict_id == record_id:
                    record = r
                    break
        if record is None:
            raise KeyError(record_id)
        # ONE SHARED ANONYMISER — the same instance recompute() uses.
        #
        # WHY THIS ARGUMENT IS LOAD-BEARING AND NOT TIDINESS. record_to_dict() falls
        # back to `anonymiser or Anonymiser()`, and Anonymiser numbers by FIRST-SEEN
        # order. Omitting it therefore built a fresh instance per HTTP request, which
        # allocated index 0 to whichever hull that request happened to ask about: every
        # case file downloaded from this route came back as 999000001 / VESSEL 001 /
        # IMO-SYNTH-001, so two different hulls exported one after the other carried the
        # SAME synthetic identity — sixteen hulls merged into one in the exported
        # evidence.
        #
        # OBSERVED vs CLAIMED, applied to our own output: the console shares
        # self._anon and would be displaying that hull as (say) 999000005 at the very
        # moment this route returned 999000001 for it. The case file and the screen
        # disagreed about WHO THE VESSEL WAS, with nothing raising anywhere, because
        # both values look equally synthetic. That is the failure mode evidence.py's
        # class docstring names in its first paragraph ("a dossier in which the same
        # hull is 999000003 on page 2 and 999000007 on page 5 is not a case file").
        #
        # WHAT WOULD CHANGE THE ANSWER: nothing here is a judgement call — evidence.py
        # owns the numbering and states the requirement; this call site simply has to
        # honour it. When allow_real is true the argument is ignored by record_to_dict,
        # so passing it is safe on both branches.
        return evidence_mod.record_to_dict(
            record, anonymiser=self._anon, association=None,
            allow_real_identities=allow_real)

    # ---------------------------------------------------------------- /api/state
    async def api_state(self) -> dict[str, Any]:
        """
        Lane D's payload, unchanged, so 04_demo/web/index.html works against this
        server exactly as it does against app.py. The shape is produced by lane D's
        own State.recompute(); this method only adds the freshness stamp app.py adds.
        """
        async with self._lock:
            if self._demo.result is None:
                need_run = True
            else:
                need_run = False
                body = dict(self._demo.result)
        if need_run:
            await self.recompute(reason="api_state:cold")
            async with self._lock:
                body = dict(self._demo.result or {})
        # OVERWRITE lane D's raw records with the pseudonymised ones. The rest of the
        # payload — pose, infrastructure, asset, counts, scoring, caveat — is lane D's
        # untouched and carries no vessel identity.
        async with self._lock:
            body["records"] = [r.model_dump(mode="json") for r in self._public_records]
            body["identities"] = ("REAL" if self._allow_real else "PSEUDONYMISED")
            # Stated on the poll path too. The console can derive "an injection is
            # active" from any contact's frame_ref, but a scene whose injected contact
            # has scrolled out of view would then look clean, and "clean" is the one
            # thing this must never say when it is not true.
            body["injection"] = self.injection_state
            # LANE F: the live source rides on the payload the console already polls.
            # A separate endpoint for the badge could fail on its own and leave the
            # screen confidently naming a source that stopped being live.
            body["source"] = self.source.status()
        body["last_update"] = self._demo.last_update
        body["server_seq"] = self._seq
        return body


# ================================================================================
# 3. THE VIDEO STREAM.
#
# REWRITTEN 2026-08-29 WHEN THE RASPBERRY PI LEFT THE RIG. The EO sensor is now a video
# THIS MACHINE decodes — a file on disk, or a network stream off the internet — and the
# console has to be able to show it. Four ways in, in order of how much they can be
# trusted:
#
#   RELAY        A sensor node POSTs the JPEG it computed its contacts from to
#                /ingest/frame, and /stream hands that straight on. This is the only
#                option where the imagery under the detection boxes is GUARANTEED to be
#                the imagery the boxes came from — see _FrameRelay for why that is a
#                correctness property and not a nicety. Preferred whenever available;
#                it is chosen automatically while a node is publishing.
#   --video      Decode a file path or a URL here (http/https/rtsp, and HLS .m3u8
#                counts). Files are paced to their own frame rate and looped; live
#                sources are neither. This is the source that replaced the Pi.
#   --camera N   A lens on this machine. Same code path as --video: cv2.VideoCapture
#                takes an int, a path or a URL and does not care which.
#   --mjpeg-url  Straight passthrough of ANY multipart/x-mixed-replace source. The
#                server does not parse the stream, so it cannot be wrong about it.
#
# Configured none of these it returns 503 with a stated reason. It NEVER returns a
# placeholder or a frozen last frame: a still image on a watch screen is indistinguish-
# able from a live one showing calm water, and that confusion is the whole failure mode
# SensorNode.last_seen exists to prevent.
#
# ONE CAMERA, ONE CONSUMER. macOS will not usually open the built-in camera twice, so
# --camera on the server AND a node on the same index is a contest one of them loses.
# A file or a network URL has no such limit — which is a further reason the relay is
# the right default and --video the right flag to reach for.
# ================================================================================

def _mjpeg_passthrough(url: str, *, chunk: int = 8192) -> Iterator[bytes]:
    """
    Relay an upstream MJPEG stream byte for byte.

    NOT RE-ENCODED AND NOT RE-FRAMED on purpose: the server has no opinion about the
    upstream's boundary string, its headers or its frame rate, so it cannot introduce a
    disagreement with them. urllib rather than a new HTTP dependency, because this
    server already carries one dependency the project did not want (see the module
    docstring) and a second one for a byte copy is not defensible.

    A sync generator: Starlette iterates it in a threadpool, so the blocking read does
    not stall the event loop that is also feeding the WebSockets.
    """
    try:
        with urllib.request.urlopen(url, timeout=5.0) as up:
            while True:
                buf = up.read(chunk)
                if not buf:
                    return
                yield buf
    except (urllib.error.URLError, OSError) as exc:
        # The client sees the stream end. Stated in the log rather than swallowed, so
        # "the video stopped" has a cause somewhere instead of being a mystery on stage.
        print(f"[server] /stream passthrough ended: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return


def _mjpeg_part(payload: bytes) -> bytes:
    """One multipart/x-mixed-replace part. One definition, so the two producers below
    cannot disagree about the wire format."""
    return (
        f"--{MJPEG_BOUNDARY}\r\n"
        f"Content-Type: image/jpeg\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n"
    ).encode("ascii") + payload + b"\r\n"


def parse_video_source(spec: "str | int | None") -> "str | int | None":
    """
    '0' -> 0 (camera index).  Anything else stays a string: a path or a URL.

    ONE PARSER, called by every entry point, because the alternative is main.py and
    server.py each deciding what "0" means and disagreeing on the one run where it
    matters. cv2.VideoCapture takes an int OR a str and treats a file path and a URL
    identically, which is why a camera, a clip and an internet stream need one code
    path here and not three.
    """
    if spec is None:
        return None
    if isinstance(spec, int):
        return spec
    s = str(spec).strip()
    if not s:
        return None
    return int(s) if s.isdigit() else s


def _is_replayable_file(source: "str | int") -> bool:
    """A local file can be paced and looped. A camera or a network stream cannot be
    either: there is no 'again' and no native frame rate to honour."""
    return isinstance(source, str) and Path(source).expanduser().exists()


def _mjpeg_decode(source: "str | int", *, quality: int = 80,
                  loop: bool = True, live_timeout_s: float = 10.0,
                  yield_to=None) -> Iterator[bytes]:
    """
    Decode ANY cv2 source and serve it as MJPEG: camera index, file path, or URL.

    cv2 is imported INSIDE the function, deliberately. A top-level import would make
    the whole shore station refuse to start on any machine without opencv — including
    a teammate's laptop that only needs the console — for a feature that is optional.
    The same lazy-import discipline lane A enforces on the geo stack, for the same
    reason: reading the picture must not require the ability to produce it.

    THREE BEHAVIOURS THAT ARE NOT OBVIOUS AND ARE EACH THERE FOR A MEASURED REASON.

    PACING (files only). A file read as fast as the loop can turn plays a 30 s clip in
    about two seconds and then ends. The browser shows a smear and then a dead frame.
    So a file is paced to its own CAP_PROP_FPS. A camera and a network stream are NOT
    paced: they already arrive at their own rate, and sleeping on top of that only adds
    latency to a live picture.

    LOOPING (files only). The demo runs longer than the clip. At EOF the position is
    rewound; if the container refuses to seek, the capture is reopened.

    ENDING RATHER THAN FREEZING (live sources). If a live source stops delivering, this
    generator RETURNS after live_timeout_s and the console reports the stream stopped.
    It never re-yields the last frame. An MJPEG <img> keeps painting the final part it
    received, so a producer that quietly stops leaves a still image on a watch screen —
    and a still of calm water is indistinguishable from a live view of calm water. That
    confusion is the whole reason SensorNode.last_seen exists; the video pane is not
    allowed to reintroduce it.
    """
    import cv2  # noqa: PLC0415 — see docstring

    # yield_to() is checked once per frame. When it goes true a better source has
    # appeared — in practice a sensor node that has started publishing the frames it
    # detected on — and this generator RETURNS so the caller can switch to it inside the
    # same HTTP response. Without the hand-off the browser would sit on an
    # unsynchronised decode for the rest of the session and only pick up the relay if
    # someone reloaded the page.
    replayable = _is_replayable_file(source)
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"[server] /stream: source {source!r} would not open. A file path must "
              f"exist; a URL must be one ffmpeg can read (http/https/rtsp, and an "
              f"HLS .m3u8 counts).", file=sys.stderr)
        return

    # Ask for the shallowest buffer the backend will give us. On a live source a deep
    # buffer means the operator is watching the past, and a bearing computed from a
    # frame thirty seconds old is a wrong bearing, not a late one.
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:                                      # noqa: BLE001
        pass

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    period = (1.0 / fps) if (replayable and 0.0 < fps <= 120.0) else 0.0
    next_at = time.monotonic()
    last_frame_at = time.monotonic()

    try:
        while True:
            ok, frame = cap.read()

            if not ok:
                if replayable and loop:
                    # Rewind. Reopen if the container will not seek — some MP4s and
                    # most transport streams will not.
                    if not cap.set(cv2.CAP_PROP_POS_FRAMES, 0):
                        cap.release()
                        cap = cv2.VideoCapture(source)
                        if not cap.isOpened():
                            return
                    continue
                if replayable:
                    return                       # a clip that was asked not to loop
                # A live source hiccuped. Reconnect, but only for a bounded time.
                if time.monotonic() - last_frame_at > live_timeout_s:
                    print(f"[server] /stream: {source!r} delivered no frame for "
                          f"{live_timeout_s:.0f}s — ending the stream rather than "
                          f"freezing the last one.", file=sys.stderr)
                    return
                time.sleep(0.5)
                cap.release()
                cap = cv2.VideoCapture(source)
                continue

            last_frame_at = time.monotonic()
            if yield_to is not None and yield_to():
                return
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                continue
            yield _mjpeg_part(buf.tobytes())

            if period:
                next_at += period
                slack = next_at - time.monotonic()
                if slack > 0:
                    time.sleep(slack)
                else:
                    next_at = time.monotonic()   # fell behind; do not accumulate debt
    finally:
        cap.release()


class _FrameRelay:
    """
    The latest JPEG a sensor node published, and nothing else.

    WHY THIS EXISTS, AND IT IS A CORRECTNESS FIX RATHER THAN A FEATURE.

    OBSERVED  — the console draws its detection boxes as an SVG overlay ON TOP of
                /stream (index.html, renderSensor: "boxes are the detector's output").
    CLAIMED   — that the box and the hull under it are the same observation.
    THE MISMATCH — when the server decodes the video AND the node decodes the same
                video separately, there are two decoders holding two independent
                positions in one file. The boxes come from frame N and are painted over
                frame M, and on a looping clip N and M drift apart without bound.
    WHY IT MATTERS — a box drawn over the wrong frame is a watch screen asserting a
                vessel is somewhere it is not. That is not a rendering defect; it is the
                tool making a false observation, and it would survive every functional
                test because both halves work perfectly on their own.
    THE FIX   — ONE decoder. The node that computed the contacts publishes the frame it
                computed them FROM, and the server relays that. Imagery and boxes are
                then the same observation with the same timestamp.
    WHAT WOULD CHANGE THE ANSWER — a genuinely live source (a lens, or a broadcast
                stream both ends open independently) is close enough in wall-clock time
                that the drift is sub-second. The relay is still preferred there; the
                unbounded-drift argument is specific to seekable files.

    NEVER SERVES A STALE FRAME. If the node stops publishing, frames() RETURNS and the
    console reports the stream as stopped. See _mjpeg_decode on why a frozen frame is
    worse than no frame.
    """

    def __init__(self, stale_after_s: float = 5.0) -> None:
        self.stale_after_s = stale_after_s
        self._jpeg: bytes | None = None
        self._seq = 0
        self._at = 0.0
        self._node_id: str | None = None
        self._frame_ref: str | None = None
        self._cv = threading.Condition()

    def publish(self, jpeg: bytes, *, node_id: str | None = None,
                frame_ref: str | None = None) -> int:
        with self._cv:
            self._jpeg = jpeg
            self._seq += 1
            self._at = time.monotonic()
            self._node_id = node_id
            self._frame_ref = frame_ref
            self._cv.notify_all()
            return self._seq

    def fresh(self) -> bool:
        with self._cv:
            return (self._jpeg is not None
                    and (time.monotonic() - self._at) <= self.stale_after_s)

    def status(self) -> dict[str, Any]:
        with self._cv:
            age = (time.monotonic() - self._at) if self._jpeg is not None else None
            return {
                "frames_published": self._seq,
                "node_id": self._node_id,
                "frame_ref": self._frame_ref,
                "age_s": round(age, 2) if age is not None else None,
                "fresh": self._jpeg is not None and age is not None
                         and age <= self.stale_after_s,
            }

    def frames(self) -> Iterator[bytes]:
        """Yield each newly published frame. Blocks between them, in a threadpool."""
        sent = -1
        while True:
            with self._cv:
                if self._seq == sent or self._jpeg is None:
                    # Wake at the staleness deadline even if nothing arrives, so the
                    # "publisher stopped" branch below is reachable.
                    self._cv.wait(timeout=self.stale_after_s)
                if self._jpeg is None:
                    return
                if (time.monotonic() - self._at) > self.stale_after_s:
                    print("[server] /stream: the node stopped publishing frames — "
                          "ending the stream rather than freezing the last one.",
                          file=sys.stderr)
                    return
                if self._seq == sent:
                    continue
                sent, payload = self._seq, self._jpeg
            yield _mjpeg_part(payload)


# ================================================================================
# 4. THE HTTP / WEBSOCKET SURFACE.
# ================================================================================

def create_app(
    *,
    scene_dir: Path,
    web_dir: Path,
    audit_path: Path,
    mjpeg_url: str | None,
    camera_index: int | None,
    allow_real_identities: bool,
    video_source: "str | int | None" = None,
    frame_stale_after_s: float = 5.0,
    node_stale_after_s: float,
    initial_source: str = DEFAULT_SOURCE,
    startup_tasks: "Sequence[Any]" = (),
    ais_coverage_confidence: float | None = None,
):
    """
    Build the ASGI app.

    A factory rather than a module-level `app = FastAPI()` so that every setting above
    is an explicit argument. A module-level app would have to read globals or the
    environment, and a demo whose behaviour depends on an environment variable someone
    exported two hours ago is a demo with an invisible input.
    """
    from contextlib import asynccontextmanager

    from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket
    from fastapi.responses import (
        FileResponse, JSONResponse, PlainTextResponse, StreamingResponse)
    from fastapi.staticfiles import StaticFiles
    from starlette.websockets import WebSocketDisconnect

    # ---- MAKE FASTAPI'S OWN TYPES RESOLVABLE FROM MODULE SCOPE ------------------
    # `from __future__ import annotations` makes every annotation in this file a
    # STRING, and FastAPI resolves those strings against this MODULE's globals. The
    # fastapi imports above are deliberately local to this function, so `Request` and
    # `WebSocket` were not in module globals and did not resolve — FastAPI then treated
    # `request: Request` as a missing QUERY PARAMETER and answered 422 to every POST on
    # /ingest/contacts, /api/contacts and /ingest/frame. The sensor node could never
    # deliver an observation. Two lines fix it without giving up the lazy import: the
    # names are published only here, inside the function that already requires fastapi,
    # so a teammate without the web stack can still import this module for the console.
    globals()["Request"] = Request
    globals()["WebSocket"] = WebSocket

    # ONE video source value, resolved once, here.
    #
    # `camera_index` predates video sources and callers still pass it. Rather than
    # carry two settings that can disagree — and they would, on exactly the run where
    # someone passed both — it is folded into `video_source` at the boundary. After
    # this line there is a single value and a single spelling for "what /stream
    # decodes when no node is publishing frames".
    video_source = parse_video_source(
        video_source if video_source is not None else camera_index)

    # The relay is always constructed, never conditionally. A node may start publishing
    # at any point in the run; building the relay only when it was configured in
    # advance would mean the one thing an operator does mid-demo (start a sensor)
    # silently could not take effect.
    relay = _FrameRelay(stale_after_s=frame_stale_after_s)

    def _video_description() -> str:
        """
        What /health tells the console. The exact string "none configured" is a
        CONTRACT: index.html's initVideo() compares against it to decide whether a 503
        on /stream is a configuration statement or a fault, and reports the wrong one
        if this drifts.
        """
        if relay.fresh():
            st = relay.status()
            return f"relayed from node {st['node_id'] or 'unknown'}"
        if mjpeg_url:
            return "passthrough"
        if video_source is not None:
            return (f"decoding camera {video_source}" if isinstance(video_source, int)
                    else f"decoding {video_source}")
        return "none configured"

    def _video_sync() -> str:
        """
        Whether the imagery under the detection boxes is the imagery the boxes were
        computed from. Published because the console overlays one on the other, and an
        operator is entitled to know when the two are only approximately the same
        picture. See _FrameRelay for why this distinction is not pedantry.
        """
        have_imagery = relay.fresh() or video_source is not None or bool(mjpeg_url)
        if not have_imagery:
            return "no imagery"

        # THE WORST CASE FIRST, BECAUSE IT IS THE ONE THAT LIES QUIETLY.
        # The video pane and the promoted contacts are independent: /stream shows
        # whatever imagery exists, while source_switch decides whose CONTACTS reach the
        # picture. Run a clip in the pane while RECORDED is the authority and the
        # console draws boxes from the recorded scene on top of unrelated imagery —
        # every box in the wrong place, nothing on screen saying so, and both halves
        # working exactly as designed. Said out loud here so the console can say it too.
        if backend.source.active == "RECORDED":
            # ...unless the imagery IS this scene's own rendering. make_scene_video.py
            # writes scene.mp4 INTO the scene directory and draws every hull at the
            # exact bbox the scene's contact carries, so the boxes do belong to the
            # frame beneath them. Checked by path rather than by a flag: a flag can be
            # passed for a video that is nothing of the kind, and this claim is one the
            # operator will trust.
            try:
                own = (video_source is not None
                       and isinstance(video_source, str)
                       and Path(video_source).resolve().parent == scene_dir.resolve())
            except OSError:
                own = False
            if own:
                return ("scene rendering: this imagery was rendered from the very "
                        "contacts drawn over it. Synthetic — no camera observed it.")
            return ("UNRELATED IMAGERY: the contacts on screen come from the RECORDED "
                    "scene, not from this video. The boxes do NOT belong to the frame "
                    "beneath them. Switch the source to VIDEO_STREAM to make the "
                    "picture one observation.")
        if relay.fresh():
            return "frame-synchronised: the node published the frame it detected on"
        return ("reference imagery: decoded independently of the detector, so the "
                "boxes are NOT guaranteed to belong to the frame beneath them")

    backend = ConsoleBackend(scene_dir, audit_path=audit_path,
                             node_stale_after_s=node_stale_after_s,
                             allow_real_identities=allow_real_identities,
                             initial_source=initial_source)
    # Set BEFORE the lifespan's startup recompute, so the first picture a browser sees
    # was computed under the same coverage assumption as every later one.
    if ais_coverage_confidence is not None:
        backend._demo.ais_coverage_confidence = float(ais_coverage_confidence)

    @asynccontextmanager
    async def lifespan(_app):
        # Run the pipeline ONCE at startup so the first browser to connect gets a
        # populated ConsoleState rather than an empty page it would read as an empty
        # sea. Then start the staleness watchdog.
        await backend.recompute(reason="startup")
        tasks = [asyncio.create_task(_watchdog(backend))]
        # Caller-supplied background work (main.py's AIS replay driver). Started HERE,
        # inside the running loop, rather than before uvicorn: a task created on a
        # different loop never runs and fails silently, which on stage looks exactly
        # like an AIS feed that has no data.
        for factory in startup_tasks:
            tasks.append(asyncio.create_task(factory(backend)))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception as exc:      # noqa: BLE001
                    print(f"[server] task {t!r}: {type(exc).__name__}: {exc}",
                          file=sys.stderr)

    app = FastAPI(title="Maritime Approaches — shore station",
                  version=SERVER_VERSION, lifespan=lifespan)

    # ---------------------------------------------------------------- GET /
    @app.get("/", include_in_schema=False)
    async def index():
        page = web_dir / "index.html"
        if not page.exists():
            # Say WHICH path was tried. "404" on the console route at 09:00 with a
            # judge waiting is a minute of guessing; naming the path is a second.
            raise HTTPException(
                status_code=503,
                detail=f"no console page at {page}. Pass --web <dir containing "
                       f"index.html>.")
        return FileResponse(page, media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------- GET /api/state
    @app.get("/api/state", include_in_schema=False)
    async def api_state():
        """
        COMPATIBILITY, NOT A SECOND PRODUCT. 04_demo/web/index.html polls this every
        two seconds and is the rehearsed console. Serving it unchanged is what makes
        app.py an interchangeable fallback rather than a different demo.
        """
        return JSONResponse(await backend.api_state(),
                            headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ WS /ws
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        """
        The push stream.

        PROTOCOL, IN THREE LINES, BECAUSE AN UNDOCUMENTED ONE GETS GUESSED AT:
          1. The FIRST message is a ConsoleState — the whole picture. It has
             `last_seq` and no `event` key.
          2. Every later message is a StreamEvent — it has an `event` key and a `seq`.
          3. The client applies the snapshot, discards any event with
             seq <= last_seq, and treats a gap in seq as "I missed one, reconnect".

        The snapshot is taken and the subscription registered UNDER ONE LOCK
        ACQUISITION ORDER — subscribe first, snapshot second. Reversed, an event
        emitted between the snapshot and the subscription would be lost with no gap in
        the client's seq to reveal it: the client would sit on a picture that is
        quietly wrong, which is the exact failure the seq counter exists to make
        impossible. Subscribing first can only cause a DUPLICATE, and a duplicate is
        detectable (seq <= last_seq) and discardable. Prefer the recoverable error.
        """
        await sock.accept()
        queue = backend.subscribe()
        try:
            await sock.send_bytes(await backend.snapshot_json())
            while True:
                raw = await queue.get()
                if raw == b"":
                    # Overflow sentinel — this client fell too far behind. Close so it
                    # reconnects and gets a fresh, consistent snapshot.
                    await sock.close(code=1011, reason="subscriber queue overflow")
                    return
                await sock.send_bytes(raw)
        except WebSocketDisconnect:
            pass
        except (RuntimeError, OSError):
            # The socket went away mid-send. Nothing to recover; the client reconnects.
            pass
        finally:
            backend.unsubscribe(queue)

    # ------------------------------------------- POST /ingest/contacts (+ alias)
    async def _ingest(request: Request):
        """
        Observations in from an edge node.

        VALIDATION HAPPENS HERE, ON THE SHORE STATION, NOT ON THE PI. The sensor stays
        a dumb, fast, dependency-light thing that emits JSON — it has no pydantic and
        cannot import contracts.py. Enforcing the contract at the point of consumption
        also means a malformed sensor cannot corrupt the picture: it gets a 400 and the
        previous state stands.

        TWO BODY SHAPES ACCEPTED, AND THE REASON IS COMPATIBILITY, NOT INDECISION.
        pi_sensor.py already posts a bare JSON list and its default path is
        /api/contacts. Requiring a new envelope would break a sensor that is written,
        for no gain. So:
            [ {...}, {...} ]                                  <- pi_sensor, unchanged
            {"contacts": [...], "node_id": ..., "kind": ...,
             "measured_fps": ...}                             <- richer node
        node_id defaults to the ?node_id= query parameter, then to "edge-unknown" —
        never to a fabricated identity, because a contact attributed to the wrong
        sensor is worse provenance than one attributed to an admittedly unknown one.
        """
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(400, f"body is not JSON: {type(exc).__name__}: {exc}")

        if isinstance(payload, list):
            items, meta = payload, {}
        elif isinstance(payload, dict):
            items, meta = payload.get("contacts", []), payload
        else:
            raise HTTPException(400, "expected a JSON list or an object with "
                                     "'contacts'")

        try:
            contacts = [EoContact(**c) for c in items]
        except ValidationError as exc:
            # The full pydantic error, not a summary: the sensor author needs the field
            # name and the reason, and a truncated message costs a round trip.
            raise HTTPException(400, json.loads(exc.json()))
        except TypeError as exc:
            raise HTTPException(400, f"malformed contact: {exc}")

        node_id = str(meta.get("node_id")
                      or request.query_params.get("node_id")
                      or "edge-unknown")
        # DEFAULT CHANGED 2026-08-29, and it is load-bearing rather than cosmetic.
        # A node that does not declare its kind used to be ASSUMED to be the Pi. With
        # EDGE_PI retired from the selectable sources, that assumption would make an
        # undeclared node permanently unpromotable: it POSTs 200 OK for ever, both ends
        # look healthy, and nothing it sees ever reaches the picture. The default is now
        # the kind the rig actually runs.
        kind: SensorKind = meta.get("kind") or "video_stream"
        if kind not in ("edge_pi", "mac_camera", "video_stream", "file"):
            raise HTTPException(400, f"unknown sensor kind {kind!r}")
        fps = meta.get("measured_fps")
        fps_f = float(fps) if fps is not None else None

        # A HEARTBEAT is a node saying "alive, nothing in frame". It must not blank the
        # picture and must not spin the pipeline once per node per interval.
        if meta.get("heartbeat") and not contacts:
            await backend.note_heartbeat(node_id=node_id, kind=kind, measured_fps=fps_f)
            return {"ok": True, "heartbeat": True, "accepted": 0, "node_id": node_id,
                    "promoted": False, "active_source": backend.source.active,
                    "server_seq": backend.seq}

        await backend.ingest(contacts, node_id=node_id, kind=kind, measured_fps=fps_f)
        # `promoted` tells the node whether its contacts reached the picture. A sensor
        # posting 200 OK into a source that is not selected otherwise has no way to know
        # it is being ignored, and would look healthy on both ends while contributing
        # nothing.
        return {"ok": True, "accepted": len(contacts), "node_id": node_id,
                "promoted": backend.source.accepts(kind, node_id),
                "active_source": backend.source.active,
                "server_seq": backend.seq}

    @app.post("/ingest/contacts")
    async def ingest_contacts(request: Request):
        return await _ingest(request)

    @app.post("/api/contacts", include_in_schema=False)
    async def ingest_contacts_alias(request: Request):
        """pi_sensor.py's hardcoded default path. Same handler, no second behaviour."""
        return await _ingest(request)

    # ---------------------------------------------------- POST /ingest/frame
    @app.post("/ingest/frame")
    async def ingest_frame(request: Request,
                           node_id: str | None = Query(default=None),
                           frame_ref: str | None = Query(default=None)):
        """
        The sensor node publishes the JPEG it computed its contacts from.

        RAW BYTES, NOT MULTIPART, NOT BASE64 IN JSON. The node is deliberately a
        dependency-light thing posting with urllib; base64 would inflate every frame by
        a third for no gain, and a multipart encoder is a library the node does not
        have. Content-Type is image/jpeg and the body is the file.

        Bounded by construction: the relay holds exactly ONE frame. There is no queue to
        grow, so a fast node cannot exhaust memory on a slow console, and a viewer that
        falls behind sees the newest frame rather than an ever-older one.
        """
        data = await request.body()
        if not data:
            raise HTTPException(400, "empty body: POST the JPEG bytes as the body")
        # Cheap sanity check rather than a decode. The server has no opinion about the
        # image, but a node posting JSON to this route by mistake should be told so
        # here, not by a browser rendering a broken image on stage.
        if not data.startswith(b"\xff\xd8"):
            raise HTTPException(400, "body is not a JPEG (no SOI marker). This route "
                                     "takes raw image/jpeg bytes.")
        seq = relay.publish(data, node_id=node_id, frame_ref=frame_ref)
        return {"ok": True, "frame_seq": seq, "bytes": len(data)}

    # ------------------------------------------------------------ GET /stream
    @app.get("/stream")
    async def stream():
        """
        PRECEDENCE, AND THE ORDER IS THE ARGUMENT.

        1. RELAY, whenever a node is currently publishing. It is the only source whose
           imagery is guaranteed to be the imagery the detection boxes were computed
           from, so it wins over anything this server could decode for itself.
        2. --mjpeg-url passthrough.
        3. --video / --camera decoded here.
        4. 503, stating which flag would fix it.

        The precedence is evaluated per REQUEST, not once at startup: a node started
        halfway through a run takes over the pane on the browser's next reconnect, and
        /health says which source is live at any moment.
        """
        def _frames() -> Iterator[bytes]:
            """
            One response, whichever source is currently best.

            The precedence is re-evaluated CONTINUOUSLY rather than once at connect
            time. A node that starts thirty seconds into a run takes the pane over
            without the operator reloading anything; a node that dies hands the pane
            back to the server's own decode instead of leaving a dead pane. Both
            transitions happen inside the same <img>, which is the only place the
            operator is looking.
            """
            empty_rounds = 0
            while True:
                if relay.fresh():
                    empty_rounds = 0
                    yield from relay.frames()      # returns when the node goes quiet
                    continue                       # ...then fall through and decode
                if mjpeg_url:
                    yield from _mjpeg_passthrough(mjpeg_url)
                    return                         # a passthrough that ends, ends
                if video_source is None:
                    return
                served = 0
                for part in _mjpeg_decode(video_source, yield_to=relay.fresh):
                    served += 1
                    yield part
                if served:
                    empty_rounds = 0
                    continue
                # Produced nothing: the source is unopenable, not merely quiet. Two
                # rounds, then stop, so a bad path cannot become a hot reopen loop that
                # burns a core for the rest of the demo.
                empty_rounds += 1
                if empty_rounds >= 2:
                    print(f"[server] /stream: {video_source!r} produced no frames "
                          f"twice — giving up rather than reopening in a loop.",
                          file=sys.stderr)
                    return
                time.sleep(0.5)

        if relay.fresh() or mjpeg_url or video_source is not None:
            return StreamingResponse(
                _frames(),
                media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        return PlainTextResponse(
            "No video source configured, and no sensor node is publishing frames.\n"
            "Give the shore station something to show, in order of preference:\n"
            "  --video <file|url>   decode a clip or a network stream here, and run\n"
            "                       the sensor node on the SAME source so the boxes\n"
            "                       and the imagery agree;\n"
            "  --camera <index>     a lens on this machine;\n"
            "  --mjpeg-url <url>    relay an existing multipart/x-mixed-replace feed.\n"
            "A node started with --publish-frames takes the pane over automatically.",
            status_code=503)

    # ------------------------------------------------------- GET/POST /source
    @app.get("/source")
    async def get_source():
        """Which sensor is live. Cheap enough for the console to poll on its own."""
        return JSONResponse(backend.source.status())

    @app.post("/source")
    async def post_source(body: dict = Body(...)):
        """
        Switch the live source. VIDEO_STREAM | MAC_CAMERA | RECORDED.

        Does not restart the server and does not clear the queue — the evidence records,
        the operator audit trail and the event sequence all survive, and the response
        returns records_before/records_after so a caller can VERIFY that rather than
        take it on trust.

        Deliberately not gated on the old source being declared offline. An operator who
        can see the video has frozen must not have to wait out a staleness threshold
        before they are allowed to act; detection is for the strip, the switch is for
        the operator.
        """
        target = body.get("source") or body.get("to")
        if not target:
            raise HTTPException(
                400, "body needs {'source': 'VIDEO_STREAM|MAC_CAMERA|RECORDED'}")
        try:
            normalise_source(target)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        result = await backend.set_source(
            target, operator_id=str(body.get("operator_id") or "operator"),
            reason=body.get("reason"))
        return JSONResponse(result)

    # ------------------------------------------------------ POST /demo/inject
    @app.post("/demo/inject")
    async def demo_inject(body: dict = Body(default={})):
        """
        Fire a prepared scenario on command.

            POST /demo/inject {"scenario": "spoof"}            <- the key moment
            POST /demo/inject {"scenario": "spoof",
                               "contact_id": "c-0004"}         <- pinned, rehearsable
            POST /demo/inject {"scenario": "dark"}
            POST /demo/inject {"scenario": "reset"}            <- back to clean
            POST /demo/inject {"scenario": "prepared",
                               "contacts": [ ...EoContact... ]}<- a prepared file

        THE OPERATOR IS NAMED, exactly as on /action. An injection changes what is on
        the screen, so it belongs to somebody; an unattributed one is a change to the
        evidence with no author. It is NOT written to the operator audit trail, and
        that is deliberate rather than an oversight: that file records DECISIONS ABOUT
        VESSELS, and mixing demo stagecraft into it would corrupt the one artefact
        whose value is that everything in it is a human judgement about a contact.
        """
        scenario = str(body.get("scenario") or "spoof")
        operator = str(body.get("operator_id") or "demo").strip() or "demo"
        raw = body.get("contacts")
        contacts = None
        if raw:
            try:
                contacts = [EoContact(**c) for c in raw]
            except ValidationError as exc:
                raise HTTPException(400, json.loads(exc.json()))
        try:
            out = await backend.inject(
                scenario, operator_id=operator,
                contact_id=body.get("contact_id"), contacts=contacts)
        except ValueError as exc:
            # A scenario that cannot run must say WHY in the response. "500" three
            # minutes into a pitch is unrecoverable; "no matched contact to inject
            # into" is fixable in one command.
            raise HTTPException(409, str(exc))
        return out

    @app.get("/demo/inject", include_in_schema=False)
    async def demo_inject_state():
        """What is injected right now. GET so it can be checked from a browser bar."""
        return {"injection": backend.injection_state}

    # ------------------------------------------------------------ POST /action
    @app.post("/action")
    async def action(body: dict = Body(...)):
        """
        A HUMAN decision, recorded. Never executed — there is nothing here to execute
        with, and contracts.OperatorActionAckEvent has no field for an outcome for
        exactly that reason.
        """
        try:
            act = OperatorAction(**body)
        except ValidationError as exc:
            raise HTTPException(400, json.loads(exc.json()))
        except TypeError as exc:
            raise HTTPException(400, f"malformed action: {exc}")
        ack = await backend.record_action(act)
        # The ack goes on the WebSocket to every console AND comes back on this
        # request. The poster is usually the same browser, but not always — a second
        # operator watching must see that a decision was taken, and a decision visible
        # to only the person who made it is not an audit trail.
        return JSONResponse(json.loads(_EVENTS.dump_json(ack)),
                            status_code=200 if ack.accepted else 500)

    # ------------------------------------------------------ GET /evidence/{id}
    @app.get("/evidence/{record_id}")
    async def get_evidence(record_id: str, allow_real: int = Query(0)):
        if allow_real and not allow_real_identities:
            # TWO independent gates, so no single mistake publishes a real vessel name
            # over HTTP. CLAUDE.md's rule against naming a real vessel is
            # non-negotiable and this is the route most likely to leak one.
            raise HTTPException(
                403,
                "allow_real=1 refused: this server was not started with "
                "--allow-real-identities. Real identities over HTTP require both.")
        try:
            payload = backend.evidence_payload(record_id, allow_real=bool(allow_real))
        except KeyError:
            raise HTTPException(404, f"no evidence record {record_id!r}")
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ GET /health
    @app.get("/health")
    async def health():
        """
        Enough to answer "is the picture I am looking at real?" without opening a
        terminal. measured_fps is echoed exactly as the node reported it, including
        null — a null here means NOBODY COUNTED, and printing a plausible number in
        its place is the failure CLAUDE.md's measured-numbers rule names.
        """
        state = await backend.api_state()
        return {
            "status": "ok",
            "server_version": SERVER_VERSION,
            "pipeline_version": backend.pipeline_version,
            "uptime_s": round(backend.uptime_s, 1),
            "event_seq": backend.seq,
            "subscribers": backend.subscriber_count,
            # Published so the console applies THIS server's staleness rule rather than
            # inventing its own. Two components disagreeing about when a sensor is dead
            # is worse than either answer, because nothing on screen says they differ.
            "node_stale_after_s": node_stale_after_s,
            "source": {
                "mode": state.get("mode"),
                "scene": str(backend.scene_dir),
                "video": _video_description(),
                "video_sync": _video_sync(),
                "frame_relay": relay.status(),
                "last_update": state.get("last_update"),
            },
            "counts": state.get("counts", {}),
            "injection": backend.injection_state,
            # LANE F's ACTIVE-SOURCE AUTHORITY, UNDER ITS OWN KEY.
            #
            # This was a second `"source":` in the same dict literal. Python keeps the
            # last one silently, so the transport/scene block above (mode, scene, video,
            # last_update) was constructed on every /health request and then discarded —
            # dead code that cost a real feature.
            #
            # THE INFERENCE CHAIN, because the symptom pointed away from the cause:
            # OBSERVED  — 03_src/web/index.html:1068 reads `health?.source?.video`.
            # CLAIMED   — the block above promises /health reports which video source is
            #             configured; initVideo()'s own comment says a 503 on /stream is
            #             "a CONFIGURATION statement, not a failure".
            # THE MISMATCH — SourceSwitch.status() has no `video` key, so that read
            #             yielded undefined, the console fell through to "unknown", and
            #             "unknown" is not "none configured". The page therefore took the
            #             video-IS-configured branch, un-hid the <img>, hit /stream's 503
            #             and rendered "VIDEO SOURCE STOPPED — the stream was configured
            #             (unknown) and is not delivering frames."
            # WHY IT MATTERS — on the DEFAULT invocation (no --mjpeg-url, no --camera),
            #             i.e. the T1 guaranteed path, the operator console accused
            #             itself of a broken sensor when nothing was wrong. That is the
            #             precise confusion this project exists to remove.
            # CONFIDENCE — high, and structural rather than inferred: the duplicate key
            #             is verifiable by AST and the consumer is one grep.
            # WHAT WOULD CHANGE THE ANSWER — if any client read `/health` -> source ->
            #             active_source. None does; the console reads only
            #             node_stale_after_s and source.video, and the failover checks
            #             assert on the backend object, not on this route. /api/state
            #             still carries the switch status under `source` for the SOURCE
            #             badge, so lane F's console contract is untouched.
            "active_source": backend.source.status(),
            "nodes": [n.model_dump(mode="json") for n in backend.nodes],
            "audit_log": {
                "path": str(audit_path),
                "exists": audit_path.exists(),
                "lines": backend.audit_lines,
            },
            "identities": "REAL PERMITTED" if allow_real_identities
            else "pseudonymised on every route that leaves this machine",
        }

    # STATIC ASSETS. The console prefers a LOCAL copy of Leaflet at
    # /assets/leaflet.js and falls back to the CDN only if it is absent — so one curl
    # on a good link makes the demo immune to venue wifi, and nothing changes if that
    # curl never happens. Mounted last so it cannot shadow a named route above.
    if web_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(web_dir)), name="assets")

    return app


async def _watchdog(backend: ConsoleBackend) -> None:
    """Drive detection of node staleness. Emission is still gated on a flip — see
    ConsoleBackend.watchdog_tick for why that is not a timer-driven broadcast."""
    while True:
        await asyncio.sleep(WATCHDOG_TICK_S)
        try:
            await backend.watchdog_tick()
        except Exception as exc:  # never let the watchdog kill the server
            print(f"[server] watchdog: {type(exc).__name__}: {exc}", file=sys.stderr)


# ================================================================================
# 5. CLI.
# ================================================================================

def _require_web_stack() -> None:
    """
    Fail with an instruction, not a traceback.

    MEASURED 2026-08-29: fastapi, uvicorn, starlette and websockets are NOT in
    .venv/lib/python3.12/site-packages (only pydantic and pydantic_core are). The venv
    is --system-site-packages so they may live in the framework Python; that was not
    verifiable from the agent shell, so this check is the measurement.

    THE FALLBACK IS NAMED HERE ON PURPOSE. At 09:00 on Sunday the useful output of a
    missing dependency is not "ModuleNotFoundError: No module named 'fastapi'" — it is
    the command that fixes it AND the command that works without fixing it. app.py is
    stdlib, has already been run end to end, and serves the same page.
    """
    missing = []
    for mod in ("fastapi", "uvicorn"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if not missing:
        return
    exe = sys.executable
    raise SystemExit(
        "\n".join([
            "",
            "=" * 78,
            f"  MISSING: {', '.join(missing)}",
            "=" * 78,
            "  This server needs a WebSocket, and the only stdlib route to one is",
            "  hand-rolling RFC 6455 — which LIBRARY-FIRST forbids. So it takes the",
            "  dependency that 04_demo/app.py deliberately avoids.",
            "",
            "  FIX IT:",
            f"    {exe} -m pip install 'fastapi>=0.115' 'uvicorn[standard]>=0.30'",
            "",
            "  OR RUN THE REHEARSED FALLBACK — stdlib, zero dependencies, same page,",
            "  same /api/state payload, already proven end to end on this Mac:",
            f"    {exe} 04_demo/app.py --scene <scene dir>",
            "",
            "  What you lose in the fallback: WebSocket push (it polls every 2 s),",
            "  the operator audit trail at 04_demo/operator_audit.jsonl, /evidence,",
            "  /health and /stream. The pipeline and every verdict are identical.",
            "=" * 78,
            "",
        ]))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Shore station: operator console backend + audit trail.")
    p.add_argument("--scene", required=True, type=Path,
                   help="Scene directory written by 04_demo/make_synthetic_eo.py")
    p.add_argument("--host", default="127.0.0.1",
                   help="127.0.0.1 by default. Use 0.0.0.0 to let the Pi reach it.")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--web", type=Path, default=_SRC / "web",
                   help="Directory containing index.html. Defaults to lane E's "
                        "WebSocket console at 03_src/web. Point it at 04_demo/web "
                        "for lane D's polling page.")
    p.add_argument("--audit", type=Path, default=DEFAULT_AUDIT_PATH,
                   help="Operator audit trail (JSONL, append-only). This is a "
                        "judging-criterion-4 deliverable, not a log file.")
    p.add_argument("--mjpeg-url", default=None,
                   help="Upstream multipart/x-mixed-replace source to relay on "
                        "/stream (e.g. mjpg-streamer on the Pi). pi_sensor.py itself "
                        "serves no video.")
    p.add_argument("--camera", type=int, default=None,
                   help="Local camera index to encode on /stream when no --mjpeg-url "
                        "is given. Shorthand for --video <index>.")
    p.add_argument("--video", default=None,
                   help="EO SOURCE THAT REPLACED THE PI. A video file on disk, or a "
                        "network stream URL (http/https/rtsp; an HLS .m3u8 counts), or "
                        "a bare camera index. Files are paced to their own frame rate "
                        "and looped so the clip outlasts the pitch. Run the sensor "
                        "node on the SAME source with --publish-frames so the boxes "
                        "and the imagery are one observation.")
    p.add_argument("--node-stale-after", type=float, default=NODE_STALE_AFTER_S,
                   help="Seconds of silence after which a node is believed offline. "
                        "This is the online/offline policy threshold contracts.py "
                        "deliberately leaves to this lane.")
    p.add_argument("--initial-source", default=DEFAULT_SOURCE,
                   choices=sorted(LABEL_TO_KIND),
                   help="which sensor is live at startup. Defaults to RECORDED because "
                        "it is the only source that cannot fail: a cold start with no "
                        "camera then shows the scene rather than an empty screen that "
                        "is indistinguishable from a broken tool.")
    p.add_argument("--allow-real-identities", action="store_true",
                   help="DANGEROUS. Permits ?allow_real=1 on /evidence. Off by "
                        "default; leave it off for anything a judge or a camera can "
                        "see.")
    args = p.parse_args(argv)

    _require_web_stack()
    import uvicorn  # noqa: PLC0415 — only after the guard has produced a good message

    # Refuse rather than silently prefer one. Two configured video sources means the
    # operator does not know which one they are watching, and "which camera is this" is
    # a question a case file has to be able to answer.
    if sum(x is not None for x in (args.mjpeg_url, args.camera, args.video)) > 1:
        raise SystemExit("--mjpeg-url, --camera and --video are mutually exclusive: "
                         "pick the source the evidence should name.")

    app = create_app(
        scene_dir=args.scene,
        web_dir=args.web,
        audit_path=args.audit,
        mjpeg_url=args.mjpeg_url,
        camera_index=args.camera,
        video_source=args.video,
        allow_real_identities=args.allow_real_identities,
        node_stale_after_s=args.node_stale_after,
        initial_source=args.initial_source,
    )

    print("=" * 74)
    print(f"  shore station  ->  http://{args.host}:{args.port}")
    print("=" * 74)
    print(f"  scene       : {args.scene}")
    print(f"  console     : {args.web / 'index.html'}")
    print(f"  audit trail : {args.audit}   (criterion 4 deliverable)")
    print(f"  live source : {args.initial_source}   "
          f"(switch: POST /source {{'source':'MAC_CAMERA'}})")
    print(f"  node timeout: {args.node_stale_after:.0f} s of silence -> offline")
    print(f"  sensor node : POST http://{args.host}:{args.port}/api/contacts")
    print(f"                 (alias of /ingest/contacts — the node's default)")
    print(f"  frames      : POST http://{args.host}:{args.port}/ingest/frame")
    print(f"                 (raw image/jpeg; a node publishing here takes /stream)")
    print(f"  video       : " + (f"relay {args.mjpeg_url}" if args.mjpeg_url
                                 else f"decoding {args.video}" if args.video
                                 else f"local camera {args.camera}"
                                 if args.camera is not None
                                 else "none configured -> /stream returns 503 until a "
                                      "node publishes frames"))
    print(f"  identities  : " + ("REAL PERMITTED on /evidence?allow_real=1"
                                 if args.allow_real_identities
                                 else "pseudonymised on every outbound route"))
    if args.host == "127.0.0.1":
        print("  NOTE: bound to localhost. Fine now that the sensor node runs on "
              "this machine; use --host 0.0.0.0 only if a node posts from another "
              "device on the network.")
    print("  ctrl-c to stop")

    # log_level="warning": uvicorn's default access log prints a line per request, and
    # a polling console buries the one message that matters underneath it.
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
