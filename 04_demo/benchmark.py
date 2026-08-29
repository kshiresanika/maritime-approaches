#!/usr/bin/env python3
"""
benchmark.py — the instrument. Lane ARCH.

    python 04_demo/benchmark.py all --emit 04_demo/benchmark.md

Fills every table in 04_demo/benchmark.md with MEASURED numbers. It measures; it does
not estimate, and where a number cannot be measured it writes UNMEASURED and says what
would measure it. That is the same rule 04_demo/edge_benchmark.md follows.

FIVE NUMBERS, AND ONE OF THEM IS THE PITCH.

    scenarios   detection rate per scenario (identity / position / AIS-off) + the
                false-positive rate from the deliberate controls
    latency     capture -> VISIBLE re-rank, decomposed, single-clock          <-- THIS
    switch      source-switch time (lane F, F3)
    fps         the Pi's measured frame rate, as the node itself reported it

=================================================================================
THE CLOCK PROBLEM, BECAUSE IT IS THE WHOLE DIFFICULTY OF THE HEADLINE NUMBER
=================================================================================
The obvious implementation of "capture to visible re-rank" is

    browser_paint_time  -  EoContact.frame_time_utc

and it is WRONG, silently, in an unknown direction and by an unknown amount. Those two
timestamps come from two different machines. frame_time_utc is stamped on the Pi;
the paint is stamped on the Mac. Their clocks differ by whatever NTP has managed, and
at a hackathon venue -- contested wifi, a Pi that may have booted without a network,
possibly no NTP at all -- the skew can be seconds. Seconds is larger than the quantity
being measured. The subtraction would return a confident, precise, meaningless number,
and it could as easily be negative.

So this file never subtracts two clocks. Two measurements are taken instead.

  (A) THE HEADLINE, SINGLE CLOCK, NOTHING ESTIMATED.
      Run the sensor on the Mac (MAC_CAMERA, or this file's own probe contact). The
      capture stamp, the server and the browser are then all on ONE clock, and

          t_painted  -  frame_time_utc

      is a true end-to-end measurement with no skew term and no half-RTT term. This is
      the number to say out loud, and the sentence that goes with it is "measured on a
      single clock, so there is no clock-skew term in it".

  (B) THE PI, DECOMPOSED INTO LEGS THAT ARE EACH ON ONE CLOCK.
          leg 1  capture -> POST sent          Pi monotonic clock       (node reports)
          leg 2  Pi -> Mac network             HALF the POST round trip (Pi clock)
          leg 3  POST sent -> WS event out     this file's clock        (measured here)
          leg 4  WS received -> pixels on glass  browser performance.now()
      Leg 2 is the only leg spanning two machines and it is the only ESTIMATE in the
      whole document. It is labelled as one everywhere it appears. Half-RTT assumes a
      symmetric path; on a switched LAN that is close, and it is stated rather than
      hidden.

WHY leg 4 USES TWO requestAnimationFrames AND NOT ONE.
One rAF callback runs BEFORE the frame containing your DOM writes is composited -- it
means "the browser has agreed to paint", not "the pixels are on the glass". The second
rAF fires on the frame after, i.e. once the first has been presented. The claim in the
pitch is VISIBLE re-rank, so the measurement has to be visible, not scheduled. This
costs one frame of honesty, about 16 ms at 60 Hz, and it is the right direction to err.

WHAT THIS FILE MEASURES THAT IS NOT LATENCY, and why it is here rather than in a second
script: a benchmark that reports only speed invites the reader to assume correctness.
Detection rate and false-positive rate are the numbers that decide whether the tool is
usable at all, and a watch officer abandons a tool that cries wolf long before they
notice it missed something. They belong on the same page.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT / "03_src"), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_URL = "http://127.0.0.1:8000"
COLLECTOR_PORT = 8899

# The three scenarios the brief names, mapped onto the harness's fault kinds. identity
# is TWO kinds because a vessel can lie about what it is in two independent ways, and
# the pipeline treats them differently -- length is physical extent and cannot be
# argued with, class rides on a classifier and is deliberately capped. Averaging them
# into one "identity" number would hide that the strong half is carrying the weak one.
SCENARIOS: dict[str, dict[str, int]] = {
    "identity_length": {"length_spoof": 3},
    "identity_class":  {"class_spoof": 3},
    "position":        {"position_spoof": 3},
    "ais_off":         {"dark": 3},
}
# Controls ride along in EVERY trial. They are not a scenario -- they are the reason
# the detection numbers mean anything. A scene with no controls cannot measure a
# false-positive rate, and a detection rate without one is a marketing number.
CONTROLS = {"missed_detection": 2, "coverage_hole": 2}


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval. Used rather than hits/n +- 1.96*sqrt(p(1-p)/n) because the
    normal approximation is badly wrong at the ends, and the ends are exactly where a
    small hackathon sample lands: 12/12 gives a normal CI of [1.0, 1.0], asserting
    certainty from twelve trials. Wilson gives [0.76, 1.00], which is the truth."""
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile. No interpolation: interpolating invents a latency that
    was never observed, and p95 is quoted precisely because the reader wants a value
    the system actually produced."""
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(math.ceil(q * len(s))) - 1))
    return s[k]


def summarise(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    return {"n": len(values), "p50": pct(values, 0.50), "p95": pct(values, 0.95),
            "worst": max(values), "best": min(values),
            "mean": statistics.fmean(values)}


# ================================================================================
# 1. SCENARIOS — detection rate and false positives
# ================================================================================

def run_scenarios(args) -> dict:
    """Build a fresh scene per trial per scenario, run the real pipeline, score it.

    A NEW SEED PER TRIAL, and it matters: with one fixed scene this reports whether the
    pipeline handles ONE arrangement of vessels, which is a number that cannot
    generalise and will be quoted as though it can. Re-drawing the geometry, the noise,
    the victims and the spoof factors each trial is what turns 'it caught the spoof'
    into 'it catches this class of spoof at this rate, with this interval'.
    """
    import numpy as np
    import make_synthetic_eo as harness
    import run_pipeline

    # Signatures below were READ from 04_demo/make_synthetic_eo.py, not guessed:
    #   load_truth_states(csv_path, scene_time, window_s) -> (tracks, stats)   :301
    #   auto_pose(tracks, *, max_range_m, hfov_deg, yaw_uncertainty_deg,
    #             image_width_px, image_height_px) -> ScenePose               :367
    #   build_scene(...) -> {"tracks","contacts","injections",...}            :686
    #   ScenePose.to_dict()                                                    :194
    #   Injection.to_dict()                                                    :273
    scene_time = datetime(2026, 8, 25, 10, 30, tzinfo=timezone.utc)
    tracks, ais_stats = harness.load_truth_states(
        Path(args.ais), scene_time, args.window_s)
    if not tracks:
        raise SystemExit(
            f"No AIS within +/-{args.window_s:.0f}s of {scene_time.isoformat()} in "
            f"{args.ais}. Pass --window-s wider, or a different --ais.")

    # HARD STOP, not a warning. Every scene built here is scored and the numbers go on
    # a slide; a real hull inside that chain is the one thing this project forbids
    # outright. The harness already owns the check -- calling it here rather than
    # re-implementing keeps one definition of "pseudonymised".
    harness.assert_pseudonymised(tracks, allow_real=False)

    out: dict[str, dict] = {}
    for name, plan in SCENARIOS.items():
        hits = total = 0
        ctrl_actionable = ctrl_labelled = ctrl_total = 0
        misses: list[dict] = []
        confidences: list[float] = []
        for trial in range(args.trials):
            seed = args.seed + trial
            rng = np.random.default_rng(seed)
            full_plan = dict(plan)
            full_plan.update(CONTROLS)
            pose = harness.auto_pose(
                tracks, max_range_m=args.max_range, hfov_deg=args.hfov,
                yaw_uncertainty_deg=args.yaw_sigma,
                image_width_px=1920, image_height_px=1080)
            try:
                scene = harness.build_scene(
                    tracks=tracks, pose=pose, scene_time=scene_time,
                    rng=rng, noise=harness.NoiseModel(), plan=full_plan,
                    keep_size_consistent=not args.lazy_spoofer)
            except SystemExit as exc:
                # build_scene raises SystemExit when the pose and the traffic disagree.
                # Skipping the trial rather than dying is right, but it must be VISIBLE:
                # a silently shrinking denominator would inflate every rate below.
                print(f"  [{name}] trial {trial} skipped: {exc}", file=sys.stderr)
                continue
            result = run_pipeline.run_scene(
                scene["tracks"], scene["contacts"], pose.to_dict())
            # build_scene returns Injection OBJECTS; score_scene reads dicts. to_dict()
            # is the harness's own serialisation, the same one write_scene uses for
            # ground_truth.jsonl -- so a scored-in-memory scene and a scored-from-disk
            # scene cannot diverge.
            truth = [i.to_dict() for i in scene["injections"]]
            score = run_pipeline.score_scene(result, truth)

            d = score["detection"]
            c = score["controls"]
            hits += int(d["hit"])
            total += int(d["total"])
            ctrl_actionable += int(c["false_positives"])
            ctrl_labelled += int(c["labelled_but_deferred"])
            ctrl_total += int(c["total"])
            for row in score.get("rows", []):
                if row.get("expected") != "silence" and not row.get("ok"):
                    misses.append({"trial": trial, "kind": row.get("kind"),
                                   "got": row.get("got"),
                                   "deferred": row.get("deferred")})
                if row.get("confidence") is not None and row.get("ok"):
                    confidences.append(float(row["confidence"]))

        lo, hi = wilson(hits, total)
        out[name] = {
            "trials": args.trials, "hits": hits, "total": total,
            "rate": hits / total if total else float("nan"),
            "ci95": [lo, hi],
            "control_total": ctrl_total,
            "control_actionable_fp": ctrl_actionable,
            "control_labelled_fp": ctrl_labelled,
            "misses": misses[:12],
            "confidence": summarise(confidences),
        }
        print(f"  {name:<18} {hits}/{total} = "
              f"{(hits/total if total else float('nan')):.1%}  "
              f"CI95 [{lo:.2f}, {hi:.2f}]   "
              f"controls: {ctrl_actionable} actionable FP / {ctrl_total}")
    return out


# ================================================================================
# 2. LATENCY — capture to VISIBLE re-rank
# ================================================================================

class PaintCollector:
    """A one-route HTTP listener the console posts paint timings to.

    DELIBERATELY NOT A ROUTE ON server.py. The shore station is the thing under
    measurement; adding an instrument endpoint to it means the measured system and the
    measuring system are the same process, and the first question a judge asks about a
    latency number is whether the measurement perturbed it. Keeping the collector out
    of the server also means nothing has to be removed before the demo -- server.py is
    byte-identical whether or not anyone is benchmarking.
    """

    def __init__(self, port: int = COLLECTOR_PORT) -> None:
        self.rows: list[dict] = []
        collector = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):                                   # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                try:
                    collector.rows.append(json.loads(self.rfile.read(n) or b"{}"))
                except Exception:                                # noqa: BLE001
                    pass
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

            def do_OPTIONS(self):                                # noqa: N802
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "content-type")
                self.end_headers()

            def log_message(self, *a):                           # noqa: N802
                pass

        self.httpd = HTTPServer(("127.0.0.1", port), H)
        Thread(target=self.httpd.serve_forever, daemon=True).start()

    def by_seq(self) -> dict[int, dict]:
        return {int(r["seq"]): r for r in self.rows if "seq" in r}

    def stop(self) -> None:
        self.httpd.shutdown()


def post_json(url: str, body: dict, timeout: float = 10.0) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:400]}


def get_json(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def probe_contact(pose: dict, *, i: int) -> dict:
    """One synthetic EO contact, stamped with THIS machine's clock at 'capture'.

    Using a probe rather than waiting for the camera to happen to see something is what
    makes the measurement repeatable and lets it run n times in a row. The contact is
    geometrically ordinary -- it must pass association, or it measures the rejection
    path instead of the pipeline. frame_time_utc is the capture instant and is the
    START of the headline interval."""
    now = datetime.now(timezone.utc)
    return {
        "contact_id": f"bench-{uuid.uuid4().hex[:8]}-{i}",
        "frame_time_utc": now.isoformat(),
        "frame_ref": "benchmark-probe",
        "bbox_px": [900, 520, 1010, 560],
        "detection_confidence": 0.71,
        "observed_bearing_deg_true": float(pose.get("boresight_deg_true", 180.0)),
        "bearing_uncertainty_deg": 2.5,
        "observed_range_m": 3200.0,
        "range_uncertainty_m": 800.0,
        "track_length_frames": 9,
        "camera_pose_ref": pose.get("pose_ref", "bench"),
    }


def run_latency(args) -> dict:
    """POST a contact, wait for the queue to re-rank on the wire, then for the browser
    to paint it. Every interval below is measured between two readings of ONE clock."""
    try:
        from websockets.sync.client import connect as ws_connect
    except Exception as exc:                                     # noqa: BLE001
        raise SystemExit(
            f"websockets is required for the latency measurement ({exc}).\n"
            f"  {sys.executable} -m pip install websockets\n"
            "It ships with uvicorn[standard], so if the server runs, this should too.")

    health = get_json(f"{args.url}/health")
    state = get_json(f"{args.url}/api/state")
    pose = state.get("pose", {})
    ws_url = args.url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"

    collector = PaintCollector(args.collector_port) if args.expect_browser else None
    if collector is not None:
        print(f"  paint collector on 127.0.0.1:{args.collector_port} — open\n"
              f"    {args.url}/?bench=1&collector="
              f"http://127.0.0.1:{args.collector_port}/paint\n"
              f"  in a browser NOW, then press Enter.")
        try:
            input()
        except EOFError:
            pass

    server_leg: list[float] = []
    e2e: list[float] = []
    paint_leg: list[float] = []
    rows: list[dict] = []

    with ws_connect(ws_url, open_timeout=10) as ws:
        snapshot = json.loads(ws.recv())            # ConsoleState first, by contract
        last_seq = int(snapshot.get("last_seq", 0))

        for i in range(args.n):
            c = probe_contact(pose, i=i)
            capture_epoch_ms = datetime.fromisoformat(
                c["frame_time_utc"]).timestamp() * 1000.0
            t_post = time.perf_counter()
            code, _ = post_json(f"{args.url}/ingest/contacts", {"contacts": [c]})
            if code != 200:
                print(f"  ingest returned {code}; aborting", file=sys.stderr)
                break

            # Wait for the QUEUE to change, not merely for the contact to be accepted.
            # The claim is that the RANKING re-ranked; a contact_update only says the
            # picture changed. Waiting for the wrong event would report a number
            # smaller than the one being claimed, which is the flattering direction and
            # therefore the one to be careful about.
            # Drain until the QUEUE re-ranks. Every frame is examined; none is
            # discarded unread. An earlier draft consumed one frame before this loop
            # "to prime it", which could swallow the very queue_update being timed and
            # would have shown up as an intermittent timeout on a working system --
            # the worst kind of measurement bug, because it looks like a slow server.
            seq_seen = None
            deadline = time.perf_counter() + args.timeout
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                try:
                    raw = ws.recv(timeout=remaining)
                except TimeoutError:
                    break
                ev = json.loads(raw)
                if ev.get("event") == "queue_update" and int(ev.get("seq", 0)) > last_seq:
                    t_ws = time.perf_counter()
                    seq_seen = int(ev["seq"])
                    last_seq = seq_seen
                    server_leg.append((t_ws - t_post) * 1000.0)
                    break

            if seq_seen is None:
                print(f"  trial {i}: no queue_update within {args.timeout}s "
                      f"— BROADCAST-ON-CHANGE may have suppressed it (identical "
                      f"ranking). Vary the probe or check the fingerprint gate.",
                      file=sys.stderr)
                continue

            row = {"i": i, "seq": seq_seen,
                   "server_ms": server_leg[-1] if server_leg else None}

            if collector is not None:
                # Give the browser a moment; it posts after its second rAF.
                t_wait = time.perf_counter() + args.paint_timeout
                painted = None
                while time.perf_counter() < t_wait:
                    painted = collector.by_seq().get(seq_seen)
                    if painted:
                        break
                    time.sleep(0.02)
                if painted:
                    paint_leg.append(float(painted["ws_to_paint_ms"]))
                    total = float(painted["t_paint_epoch_ms"]) - capture_epoch_ms
                    e2e.append(total)
                    row.update({"ws_to_paint_ms": painted["ws_to_paint_ms"],
                                "capture_to_paint_ms": total})
                else:
                    row["ws_to_paint_ms"] = None
            rows.append(row)
            time.sleep(args.gap)

    if collector is not None:
        collector.stop()

    res = {
        "url": args.url,
        "n_requested": args.n,
        "server_version": health.get("server_version"),
        "pipeline_version": health.get("pipeline_version"),
        "counts": health.get("counts"),
        "post_to_queue_event_ms": summarise(server_leg),
        "ws_to_painted_ms": summarise(paint_leg),
        "capture_to_visible_rerank_ms": summarise(e2e),
        "single_clock": True,
        "rows": rows,
    }
    print(f"  POST -> queue_update on the wire : {res['post_to_queue_event_ms']}")
    print(f"  WS   -> pixels on glass          : {res['ws_to_painted_ms']}")
    print(f"  CAPTURE -> VISIBLE RE-RANK       : {res['capture_to_visible_rerank_ms']}")
    return res


# ================================================================================
# 3. SOURCE SWITCH (lane F, F3)
# ================================================================================

def run_switch(args) -> dict:
    """Time POST /source to the console being able to show the new authority.

    TWO NUMBERS, KEPT APART, because lane F already measured one of them and it is not
    the one that matters to an operator:
      switch_call_ms   the SourceSwitch object changing its mind. Measured at ~0.003 ms
                       in the container. It is essentially free and it is not the cost.
      switch_to_event_ms  POST returning AND a stream event carrying the new source
                       reaching a subscriber -- which includes the pipeline recompute
                       that lane F recorded as SKIPPED because pyproj was absent.
    The second is what the operator experiences and it is the one to quote."""
    try:
        from websockets.sync.client import connect as ws_connect
    except Exception as exc:                                     # noqa: BLE001
        raise SystemExit(f"websockets required: {exc}")

    ws_url = args.url.replace("http://", "ws://") + "/ws"
    order = ["MAC_CAMERA", "RECORDED", "EDGE_PI", "RECORDED"]
    call_ms: list[float] = []
    event_ms: list[float] = []
    with ws_connect(ws_url, open_timeout=10) as ws:
        snap = json.loads(ws.recv())
        last_seq = int(snap.get("last_seq", 0))
        for i in range(args.n):
            target = order[i % len(order)]
            t0 = time.perf_counter()
            code, _ = post_json(f"{args.url}/source",
                                {"source": target, "operator_id": "benchmark"})
            t1 = time.perf_counter()
            if code != 200:
                print(f"  /source returned {code} for {target}", file=sys.stderr)
                continue
            call_ms.append((t1 - t0) * 1000.0)
            deadline = time.perf_counter() + args.timeout
            while time.perf_counter() < deadline:
                try:
                    raw = ws.recv(timeout=max(0.01, deadline - time.perf_counter()))
                except TimeoutError:
                    break
                ev = json.loads(raw)
                if int(ev.get("seq", 0)) > last_seq:
                    last_seq = int(ev["seq"])
                    event_ms.append((time.perf_counter() - t0) * 1000.0)
                    break
            time.sleep(args.gap)
    res = {"n": args.n, "switch_call_ms": summarise(call_ms),
           "switch_to_event_ms": summarise(event_ms)}
    print(f"  POST /source returns      : {res['switch_call_ms']}")
    print(f"  ...and reaches the console: {res['switch_to_event_ms']}")
    return res


# ================================================================================
# 4. PI FPS — read it, never assume it
# ================================================================================

def run_fps(args) -> dict:
    """The node's own measured_fps, straight off /health.

    NOT computed here and not defaulted. contracts.py leaves SensorNode.measured_fps
    nullable precisely so that 'nobody counted' is representable, and printing a
    plausible 30 next to a node delivering 4 is the failure MEASURED NUMBERS ONLY
    exists to prevent. A null here is reported as UNMEASURED, which is the truth."""
    health = get_json(f"{args.url}/health")
    nodes = health.get("nodes", [])
    rows = [{"node_id": n.get("node_id"), "kind": n.get("kind"),
             "online": n.get("online"), "measured_fps": n.get("measured_fps"),
             "last_seen": n.get("last_seen")} for n in nodes]
    for r in rows:
        fps = r["measured_fps"]
        print(f"  {r['node_id']:<20} {r['kind']:<12} "
              f"online={r['online']}  fps="
              f"{'UNMEASURED (node reported null)' if fps is None else f'{fps:.2f}'}")
    if not rows:
        print("  no nodes have ever posted. Start a sensor node, then re-run.")
    return {"nodes": rows, "node_stale_after_s": health.get("node_stale_after_s")}


# ================================================================================
# 5. CLI
# ================================================================================

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="benchmark.py",
                                description="Measure the rig. Never estimate it.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(q):
        q.add_argument("--url", default=DEFAULT_URL)
        q.add_argument("--timeout", type=float, default=15.0)
        q.add_argument("--gap", type=float, default=0.4)
        q.add_argument("--json", default=None, help="also write raw results here")

    s = sub.add_parser("scenarios", help="detection rate + false positives")
    s.add_argument("--ais", default=str(_HERE / "demo_scenario.csv"))
    s.add_argument("--trials", type=int, default=25)
    s.add_argument("--seed", type=int, default=20260830)
    s.add_argument("--window-s", type=float, default=120.0)
    s.add_argument("--hfov", type=float, default=60.0)
    s.add_argument("--yaw-sigma", type=float, default=2.0)
    s.add_argument("--max-range", type=float, default=8000.0)
    s.add_argument("--lazy-spoofer", action="store_true")
    s.add_argument("--json", default=None)

    l = sub.add_parser("latency", help="capture -> visible re-rank")
    common(l)
    l.add_argument("--n", type=int, default=30)
    l.add_argument("--expect-browser", action="store_true",
                   help="wait for a console opened with ?bench=1 and measure the "
                        "paint leg. WITHOUT THIS the headline is NOT end-to-end -- it "
                        "stops at the WebSocket and must be quoted as such.")
    l.add_argument("--collector-port", type=int, default=COLLECTOR_PORT)
    l.add_argument("--paint-timeout", type=float, default=5.0)

    w = sub.add_parser("switch", help="source-switch time (F3)")
    common(w)
    w.add_argument("--n", type=int, default=12)

    f = sub.add_parser("fps", help="Pi frame rate as the node reported it")
    common(f)

    a = sub.add_parser("all", help="everything, then emit the markdown block")
    common(a)
    a.add_argument("--ais", default=str(_HERE / "demo_scenario.csv"))
    a.add_argument("--trials", type=int, default=25)
    a.add_argument("--seed", type=int, default=20260830)
    a.add_argument("--window-s", type=float, default=120.0)
    a.add_argument("--hfov", type=float, default=60.0)
    a.add_argument("--yaw-sigma", type=float, default=2.0)
    a.add_argument("--max-range", type=float, default=8000.0)
    a.add_argument("--lazy-spoofer", action="store_true")
    a.add_argument("--n", type=int, default=30)
    a.add_argument("--expect-browser", action="store_true")
    a.add_argument("--collector-port", type=int, default=COLLECTOR_PORT)
    a.add_argument("--paint-timeout", type=float, default=5.0)
    a.add_argument("--emit", default=None, help="write the filled markdown here")

    args = p.parse_args(argv)
    out: dict = {"measured_at_utc": datetime.now(timezone.utc).isoformat(),
                 "python": sys.version.split()[0],
                 "argv": " ".join(sys.argv)}

    if args.cmd in ("scenarios", "all"):
        print("\n== SCENARIOS ==")
        out["scenarios"] = run_scenarios(args)
    if args.cmd in ("latency", "all"):
        print("\n== LATENCY ==")
        out["latency"] = run_latency(args)
    if args.cmd in ("switch", "all"):
        print("\n== SOURCE SWITCH ==")
        out["switch"] = run_switch(args)
    if args.cmd in ("fps", "all"):
        print("\n== NODES ==")
        out["fps"] = run_fps(args)

    dest = getattr(args, "json", None)
    if dest:
        Path(dest).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nraw results -> {dest}")
    if getattr(args, "emit", None):
        Path(args.emit).write_text(render_markdown(out), encoding="utf-8")
        print(f"benchmark.md -> {args.emit}")
    return 0


def _fmt(d: dict | None, key: str, unit: str = "ms") -> str:
    if not d or d.get("n", 0) == 0:
        return "**UNMEASURED**"
    v = d.get(key)
    return f"{v:.1f} {unit}" if isinstance(v, (int, float)) else "**UNMEASURED**"


def render_markdown(out: dict) -> str:
    """Emit the document with the measured values in place. Anything absent stays
    UNMEASURED -- this function never fills a gap with a plausible number."""
    L: list[str] = []
    A = L.append
    A("# benchmark.md — the integrated rig, measured\n")
    A(f"Generated by `04_demo/benchmark.py` at **{out.get('measured_at_utc')}** "
      f"on python {out.get('python')}.\n")
    A(f"Command: `{out.get('argv')}`\n")
    A("Every number below was produced by running the system. Empty cells say "
      "**UNMEASURED** and mean exactly that.\n")

    A("\n## 1. Headline — capture to VISIBLE re-rank\n")
    lat = (out.get("latency") or {})
    e2e = lat.get("capture_to_visible_rerank_ms")
    A("| interval | p50 | p95 | worst | n |")
    A("|---|---|---|---|---|")
    for label, key in (("capture -> pixels on glass (SINGLE CLOCK)",
                        "capture_to_visible_rerank_ms"),
                       ("POST -> queue_update on the wire", "post_to_queue_event_ms"),
                       ("WS received -> painted (2x rAF)", "ws_to_painted_ms")):
        d = lat.get(key)
        A(f"| {label} | {_fmt(d,'p50')} | {_fmt(d,'p95')} | {_fmt(d,'worst')} | "
          f"{(d or {}).get('n', 0)} |")
    if not e2e or e2e.get("n", 0) == 0:
        A("\n> The end-to-end row is UNMEASURED. It requires a console opened with "
          "`?bench=1` and `benchmark.py latency --expect-browser`. Without the browser "
          "the chain stops at the WebSocket, and that shorter number must NOT be "
          "quoted as capture-to-visible.\n")
    else:
        A(f"\n> **Say this: {e2e['p50']:.0f} ms median, {e2e['p95']:.0f} ms at p95, "
          f"from shutter to a re-ranked queue on the glass.** Measured on ONE clock — "
          f"sensor, server and browser were the same machine — so there is no "
          f"clock-skew term and nothing in it is estimated.\n")

    A("\n## 2. Detection rate per scenario\n")
    sc = out.get("scenarios") or {}
    if not sc:
        A("**UNMEASURED** — run `benchmark.py scenarios`.")
    else:
        A("| scenario | caught | trials | rate | 95% CI | actionable FP | controls |")
        A("|---|---|---|---|---|---|---|")
        for k, v in sc.items():
            lo, hi = v["ci95"]
            A(f"| {k} | {v['hits']} | {v['total']} | {v['rate']:.1%} | "
              f"[{lo:.2f}, {hi:.2f}] | {v['control_actionable_fp']} | "
              f"{v['control_total']} |")
        A("\nActionable FP = labelled DARK/SPOOF **and not** deferred. A deferred "
          "verdict is the tool saying 'a human should look', which is the designed "
          "behaviour, so counting it as a false positive would understate the tool — "
          "and counting only actionable ones without saying so would flatter it. Both "
          "are in the raw JSON.\n")

    A("\n## 3. Source switch (lane F, F3)\n")
    sw = out.get("switch") or {}
    A("| interval | p50 | p95 | worst | n |")
    A("|---|---|---|---|---|")
    for label, key in (("POST /source returns", "switch_call_ms"),
                       ("...and the console is told", "switch_to_event_ms")):
        d = sw.get(key)
        A(f"| {label} | {_fmt(d,'p50')} | {_fmt(d,'p95')} | {_fmt(d,'worst')} | "
          f"{(d or {}).get('n', 0)} |")

    A("\n## 4. Sensor nodes\n")
    fps = out.get("fps") or {}
    rows = fps.get("nodes") or []
    if not rows:
        A("**UNMEASURED** — no node has posted. Start one and re-run.")
    else:
        A("| node | kind | online | measured FPS |")
        A("|---|---|---|---|")
        for r in rows:
            v = r.get("measured_fps")
            A(f"| {r.get('node_id')} | {r.get('kind')} | {r.get('online')} | "
              f"{'**UNMEASURED** (node reported null)' if v is None else f'{v:.2f}'} |")
        A(f"\nStale after {fps.get('node_stale_after_s')} s of silence.\n")

    A("\n## 5. What is NOT in these numbers\n")
    A("- The Pi legs. Running the sensor on the Mac is what makes the headline "
      "single-clock; a Pi adds its own capture->POST time plus a network hop, and "
      "that hop is the one interval that spans two clocks. It is measurable only as "
      "half a round trip, which is an ESTIMATE and is labelled as one.")
    A("- Thermals and sustained frame rate: see `04_demo/edge_benchmark.md`.")
    A("- Any claim about real spoofing rates. The detection rates above are measured "
      "against faults this project injected, which it chose because they are visible. "
      "They are an upper bound on a real-world rate, not a prediction of one.")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
