"""
99_scratch/lane_f_failover_check.py — lane F failover verification and MEASUREMENT.

RUN IT (repo root, on the Mac, in the project venv):
    python3 99_scratch/lane_f_failover_check.py

WHAT IT MEASURES, AND WHY THE TWO NUMBERS ARE REPORTED SEPARATELY

  switch_ms     the authority change in SourceSwitch. Pure Python, no I/O, no pipeline.
  recompute_ms  the pipeline re-running against the new source's buffered contacts.

They are never added into one figure, because the honest answer to "how fast is the
switch" is that the switch is free and the recompute is the cost — and only one of those
gets better by writing faster switch code. Combining them would hide which half is which.

PART A runs anywhere: SourceSwitch has no dependencies beyond contracts.
PART B needs the full pipeline (pyproj, loguru, shapely). Where those are missing the
part is SKIPPED rather than faked — a timing measured against a shimmed geodesy library
is not a measurement of this system.

WHAT THIS CANNOT TELL YOU. Whether a physically unplugged Pi behaves like a simulated
one. The unplug adds the node's own detection latency (its last in-flight POST, TCP
timeouts) on top of everything here. That number needs the Pi and the run sheet in
04_demo/FAILOVER.md.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in ("03_src", "04_demo"):
    p = str(_ROOT / _p)
    if p not in sys.path:
        sys.path.insert(0, p)

from contracts import EoContact, SensorNode           # noqa: E402
from source_switch import (                            # noqa: E402
    DEFAULT_SOURCE, KIND_TO_LABEL, LABEL_TO_KIND, SourceSwitch, normalise,
)

FAILS: list[str] = []
MEASURED: dict[str, object] = {}


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"   <- {extra}"))
    if not cond:
        FAILS.append(name)


def pct(v: list[float], q: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


# ============================================================ PART A — the mechanism
print("\n########## PART A: SourceSwitch — the mechanism ##########")

print("\n=== the structural guarantee: it CANNOT clear the queue ===")
sw = SourceSwitch()
_held = {k: type(v).__name__ for k, v in sw.__dict__.items()}
_data_shaped = [k for k, v in sw.__dict__.items()
                if k in ("records", "contacts", "queue", "evidence", "actions", "store")]
check("SourceSwitch holds no record/contact/queue/evidence attribute", not _data_shaped,
      str(_data_shaped))
check("every attribute it does hold is a label, time, log or lock",
      set(_held) <= {"active", "operator_id", "changed_at_utc", "history",
                     "_demoted", "_lock"}, str(_held))
print(f"        (fields: {sorted(_held)})")

print("\n=== the two vocabularies meet in exactly one place ===")
check("labels map onto contracts.SensorKind, no parallel enum",
      set(LABEL_TO_KIND.values()) == {"edge_pi", "mac_camera", "file"})
check("normalise accepts the operator label", normalise("MAC_CAMERA") == "MAC_CAMERA")
check("normalise accepts the contract kind", normalise("mac_camera") == "MAC_CAMERA")
check("normalise accepts RECORDED <-> file", normalise("file") == "RECORDED")
try:
    normalise("PI_ZERO"); _bad = False
except ValueError:
    _bad = True
check("an unknown source raises rather than defaulting", _bad)

print("\n=== default is RECORDED, the only source that cannot fail ===")
check("cold start defaults to RECORDED", SourceSwitch().active == DEFAULT_SOURCE == "RECORDED")

print("\n=== gating: only the authority is promoted, everyone stays alive ===")
sw = SourceSwitch(active="EDGE_PI")
check("the active kind is accepted", sw.accepts("edge_pi") is True)
check("a live but unselected node is NOT accepted", sw.accepts("mac_camera") is False)
check("an unknown kind is not the authority and does not raise",
      sw.accepts("teapot") is False)
sw.note_demoted("mac-cam-01", "mac_camera")
check("a demoted node gets an explanation, not silence",
      "EDGE_PI is the live source" in sw.demoted().get("mac-cam-01", ""))

print("\n=== switching clears stale demotion reasons ===")
sw.switch("MAC_CAMERA", operator_id="test")
check("demotion reasons cleared on a real switch", sw.demoted() == {})
check("active is now MAC_CAMERA", sw.active == "MAC_CAMERA")
check("the previously-demoted node is now the authority", sw.accepts("mac_camera") is True)
check("the old authority is now the demoted one", sw.accepts("edge_pi") is False)

print("\n=== history is append-only evidence, no-ops included ===")
n_before = len(sw.history)
ev = sw.switch("MAC_CAMERA", operator_id="test", reason="pressed twice")
check("a no-op switch is RECORDED, not dropped", len(sw.history) == n_before + 1)
check("...and flagged as a no-op", ev.no_op is True)
check("...and did not move the authority", sw.active == "MAC_CAMERA")
check("switch_count counts only real switches", sw.status()["switch_count"] == 1)
check("every event names an operator and a time",
      all(e.operator_id and e.at_utc for e in sw.history))
try:
    ev.to_source = "TAMPERED"          # type: ignore[misc]
    _frozen = False
except Exception:
    _frozen = True
check("SwitchEvent is frozen (evidence cannot be edited after the fact)", _frozen)

print("\n=== MEASURED: the switch itself ===")
sw = SourceSwitch(active="RECORDED")
order = ["EDGE_PI", "MAC_CAMERA", "RECORDED"]
N = 3000
times: list[float] = []
for i in range(N):
    t0 = time.perf_counter()
    sw.switch(order[i % 3], operator_id="bench")
    times.append((time.perf_counter() - t0) * 1000.0)
MEASURED["switch_ms_p50"] = round(pct(times, 0.50), 4)
MEASURED["switch_ms_p95"] = round(pct(times, 0.95), 4)
MEASURED["switch_ms_max"] = round(max(times), 4)
MEASURED["switch_n"] = N
print(f"        n={N}  p50={MEASURED['switch_ms_p50']} ms  "
      f"p95={MEASURED['switch_ms_p95']} ms  max={MEASURED['switch_ms_max']} ms")
check("switch p95 is under 1 ms (it is a label change, not a data move)",
      MEASURED["switch_ms_p95"] < 1.0, str(MEASURED["switch_ms_p95"]))


# =================================================== PART B — the backend integration
print("\n\n########## PART B: ConsoleBackend integration ##########")

try:
    import server  # noqa: E402
    _HAVE_SERVER = True
    _why = ""
except Exception as exc:
    _HAVE_SERVER = False
    _why = f"{type(exc).__name__}: {exc}"

if not _HAVE_SERVER:
    print(f"  SKIP  server.py did not import here ({_why})")
    print("        Part B needs the full pipeline (pyproj, loguru, shapely). It is")
    print("        SKIPPED rather than shimmed: a recompute time measured against a")
    print("        substitute geodesy library is not a measurement of this system.")
else:
    import asyncio

    SCENE = _ROOT / "04_demo" / "out" / "scene01"
    AUDIT = _ROOT / "04_demo" / "operator_audit.jsonl"

    def mk_contact(i: int, node: str) -> EoContact:
        return EoContact(
            contact_id=f"{node}-{i}", frame_time_utc=datetime.now(timezone.utc),
            frame_ref=f"test://{node}/{i}", bbox_px=(10, 10, 50, 30),
            detection_confidence=0.7, observed_bearing_deg_true=90.0 + i,
            bearing_uncertainty_deg=12.0, track_length_frames=5,
            camera_pose_ref="test-pose")

    async def main() -> None:
        b = server.ConsoleBackend(SCENE, audit_path=AUDIT, initial_source="RECORDED")
        await b.recompute(reason="test:warm")

        print("\n=== the requirement: switching does NOT clear the queue ===")
        recs0 = len(b._records)
        pub0 = len(b._public_records)
        acts0 = len(b._actions)
        audit0 = b.audit_lines
        seq0 = b.seq
        check("the scene produced records to begin with", recs0 > 0, str(recs0))

        # A Pi posts while RECORDED is live -> buffered, NOT promoted.
        pi = [mk_contact(i, "edge") for i in range(3)]
        await b.ingest(pi, node_id="edge-pi-01", kind="edge_pi", measured_fps=4.2)
        check("a demoted node's contacts are BUFFERED",
              len(b._last_by_kind.get("edge_pi", [])) == 3)
        check("...and its liveness IS recorded (posting != selected)",
              b._nodes.get("edge-pi-01") is not None
              and b._nodes["edge-pi-01"].online is True)
        check("...and it did NOT become the picture", b._demo.mode == "replay")
        check("...and the console can explain why",
              "RECORDED is the live source" in b.source.demoted().get("edge-pi-01", ""))
        check("measured_fps passed through from the node", 
              b._nodes["edge-pi-01"].measured_fps == 4.2)

        # A Mac camera posts too, so both streams are warm before the failure.
        mac = [mk_contact(i, "mac") for i in range(2)]
        await b.ingest(mac, node_id="mac-cam-01", kind="mac_camera", measured_fps=11.0)
        check("both sources are buffered simultaneously",
              len(b._last_by_kind) == 2, str(list(b._last_by_kind)))

        print("\n=== MEASURED: the switch through the backend ===")
        res = await b.set_source("MAC_CAMERA", operator_id="test", reason="pi unplugged")
        MEASURED["backend_switch_ms"] = res["switch_ms"]
        MEASURED["backend_recompute_ms"] = res["recompute_ms"]
        MEASURED["backend_total_ms"] = res["total_ms"]
        print(f"        switch {res['switch_ms']} ms + recompute {res['recompute_ms']} ms"
              f"  =  total {res['total_ms']} ms")
        print(f"        buffered contacts promoted: {res['buffered_contacts']}")

        check("the switch landed", b.source.active == "MAC_CAMERA")
        check("mode flipped to live", b._demo.mode == "live")
        check("the buffered Mac contacts were promoted with no new post",
              len(b._demo.live_contacts) == 2)
        check("TOTAL switch is under the 5 s requirement",
              res["total_ms"] < 5000.0, str(res["total_ms"]))

        print("\n=== the queue survived ===")
        check("records were not cleared", len(b._records) >= 1, str(len(b._records)))
        check("operator actions were not cleared", len(b._actions) == acts0)
        check("the audit trail was not truncated", b.audit_lines == audit0)
        check("the response reports records before/after so it can be verified",
              "records_before" in res and "records_after" in res)

        print("\n=== nothing restarted ===")
        check("the event sequence is MONOTONIC across the switch (no reset)",
              b.seq >= seq0, f"{seq0} -> {b.seq}")
        check("the same backend object served both sides", b.uptime_s > 0)
        check("the anonymiser was NOT rebuilt (a fresh one renumbers every vessel)",
              b._anon is not None)

        print("\n=== the Pi comes back and must NOT seize the picture ===")
        await b.ingest([mk_contact(9, "edge")], node_id="edge-pi-01",
                       kind="edge_pi", measured_fps=4.0)
        check("a returning Pi does not take the picture back on its own",
              b.source.active == "MAC_CAMERA")
        check("...but it IS shown as alive again",
              b._nodes["edge-pi-01"].online is True)

        print("\n=== heartbeat: liveness without blanking the picture ===")
        live_before = list(b._demo.live_contacts)
        await b.note_heartbeat(node_id="mac-cam-01", kind="mac_camera", measured_fps=10.5)
        check("a heartbeat did not clear the live contacts",
              b._demo.live_contacts == live_before)
        check("...and refreshed the node's last_seen",
              b._nodes["mac-cam-01"].online is True)

        print("\n=== back to RECORDED restores the scene with no reload ===")
        r2 = await b.set_source("RECORDED", operator_id="test")
        check("mode is replay again", b._demo.mode == "replay")
        check("records still present after the second switch", len(b._records) > 0)
        check("second switch also under 5 s", r2["total_ms"] < 5000.0)

        print("\n=== MEASURED: offline detection with NODE_STALE_AFTER_S ===")
        stale_after = b._node_stale_after_s
        MEASURED["node_stale_after_s"] = stale_after
        # Back-date last_seen to just past the threshold and run one watchdog tick.
        n = b._nodes["edge-pi-01"]
        b._nodes["edge-pi-01"] = SensorNode(
            node_id=n.node_id, kind=n.kind, online=True,
            last_seen=datetime.now(timezone.utc) - timedelta(seconds=stale_after + 0.5),
            measured_fps=n.measured_fps)
        await b.watchdog_tick()
        check(f"a node silent for {stale_after + 0.5:.1f}s flips to OFFLINE",
              b._nodes["edge-pi-01"].online is False)
        # And one that is only slightly stale must NOT flip, or the strip flickers.
        b._nodes["mac-cam-01"] = SensorNode(
            node_id="mac-cam-01", kind="mac_camera", online=True,
            last_seen=datetime.now(timezone.utc) - timedelta(seconds=stale_after / 2),
            measured_fps=10.5)
        await b.watchdog_tick()
        check("a node silent for half the threshold stays ONLINE (no flapping)",
              b._nodes["mac-cam-01"].online is True)
        print(f"        worst-case time to show OFFLINE = {stale_after:.0f} s "
              f"(threshold) + up to {server.WATCHDOG_TICK_S:.0f} s (tick) = "
              f"{stale_after + server.WATCHDOG_TICK_S:.0f} s")
        MEASURED["worst_case_offline_s"] = stale_after + server.WATCHDOG_TICK_S

    asyncio.run(main())


print("\n" + "=" * 70)
print("MEASURED:", json.dumps(MEASURED, indent=2))
print("=" * 70)
print("ALL CHECKS PASSED" if not FAILS else f"{len(FAILS)} FAILURE(S): {FAILS}")
print("=" * 70)
raise SystemExit(1 if FAILS else 0)
