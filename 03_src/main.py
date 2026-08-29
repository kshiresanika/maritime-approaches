#!/usr/bin/env python3
"""
main.py — ONE COMMAND. Lane ARCH.

    python 03_src/main.py

Starts, in one process, in this order:

    1. PREFLIGHT      every dependency, the pose, the scene, the port — each failure
                      reported with the command that fixes it, never a traceback.
    2. AIS REPLAY     a clock over recorded Danish AIS (or the synthetic scenario),
                      advancing the claimed picture in scene time.
    3. EDGE INGEST    POST /ingest/contacts, bound where the Pi can actually reach it.
                      Optionally also runs the Mac camera as a second sensor node.
    4. FUSION         associate -> consistency -> verdict -> prioritise -> evidence,
                      re-run whenever either side of the picture changes.
    5. SERVER         HTTP + WebSocket console, ranked queue, evidence, audit trail.
    6. BROWSER        opened once the server answers /health.

WHY THIS FILE EXISTS, and it is not convenience.

Before it, the demo was four terminals: a scene build, a server, a sensor node, and a
browser. On a three-minute clock four terminals is not a workflow, it is four
opportunities to type the wrong flag in front of judges — and the flags matter. The
one that matters most is `--host`: server.py defaults to 127.0.0.1, which is loopback,
which the Pi cannot reach from any network, and the failure presents at the Pi as a
timeout indistinguishable from a cable fault. This file defaults to 0.0.0.0 and prints
the LAN address the Pi should post to, so the single most expensive misconfiguration
in the rig cannot be made by omission.

WHAT THIS FILE DOES NOT DO, deliberately.

It contains NO pipeline logic. It does not associate, score, threshold, rank or decide
anything. It starts things and it reports what it started. Every number on the console
comes from the same run_pipeline.run_scene() sequence that the harness scores itself
against — server.py's orchestration rule ("the server carries; the pipeline decides")
applies here with more force, because this is the file where a convenience shortcut
would be least visible.

THE ONE THING IT OWNS: the replay clock. That is a genuinely new capability, argued
for in _AisReplay below.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parent
_ROOT = _SRC.parent
_DEMO = _ROOT / "04_demo"
for _p in (str(_SRC), str(_DEMO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_SCENE = _DEMO / "out" / "scene01"
DEFAULT_AIS = _DEMO / "demo_scenario.csv"
REAL_FEHMARN = _ROOT / "02_data" / "slices" / "aisdk-2026-08-25_fehmarn_belt.csv"


# ================================================================================
# 1. PREFLIGHT
#
# Every check answers one question: will this stop the demo, and what is the exact
# command that unblocks it? A preflight that says "error" has done nothing useful;
# on Sunday morning the useful output is the line you paste.
# ================================================================================

class Preflight:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str, str]] = []   # name, ok, detail, fix
        self.fatal = False

    def check(self, name: str, ok: bool, detail: str = "", fix: str = "",
              fatal: bool = True) -> bool:
        self.rows.append((name, ok, detail, fix if not ok else ""))
        if not ok and fatal:
            self.fatal = True
        return ok

    def imports(self, mods: dict[str, tuple[str, bool]]) -> None:
        """mods: name -> (pip install line, is_fatal)."""
        for mod, (fix, fatal) in mods.items():
            try:
                m = __import__(mod)
                v = getattr(m, "__version__", "")
                self.check(f"import {mod}", True, str(v), fatal=fatal)
            except Exception as exc:                       # noqa: BLE001
                self.check(f"import {mod}", False, type(exc).__name__, fix, fatal)

    def report(self) -> None:
        w = max(len(r[0]) for r in self.rows)
        print("\n" + "=" * 78)
        print("  PREFLIGHT")
        print("=" * 78)
        for name, ok, detail, fix in self.rows:
            print(f"  [{'OK ' if ok else 'FAIL'}] {name:<{w}}  {detail}")
            if fix:
                print(f"         -> {fix}")
        print("=" * 78)


def preflight(args) -> Preflight:
    pf = Preflight()
    exe = sys.executable

    # ---- dependencies -----------------------------------------------------------
    # server.py's own guard checks fastapi and uvicorn only. MEASURED: verdict.py needs
    # loguru and association.py needs numpy, pyproj and scipy, all imported at module
    # top level on the startup path -- so a missing loguru raises ModuleNotFoundError
    # BEFORE any guard runs, and the operator gets a traceback pointing at a file they
    # have no reason to suspect. Everything that can stop the import chain is checked
    # here, together, before anything is started.
    pf.imports({
        "pydantic":  (f"{exe} -m pip install 'pydantic>=2.9'", True),
        "numpy":     (f"{exe} -m pip install numpy", True),
        "scipy":     (f"{exe} -m pip install scipy", True),
        "pyproj":    (f"{exe} -m pip install pyproj", True),
        "loguru":    (f"{exe} -m pip install loguru", True),
        "pandas":    (f"{exe} -m pip install pandas", True),
        "fastapi":   (f"{exe} -m pip install 'fastapi>=0.115'   "
                      f"# or fall back: {exe} 04_demo/app.py --scene <dir>", True),
        "uvicorn":   (f"{exe} -m pip install 'uvicorn[standard]>=0.30'", True),
        "cv2":       (f"{exe} -m pip install opencv-python   "
                      f"# only needed for --camera", False),
    })

    # ---- the seam that was broken on 2026-08-29 ---------------------------------
    # verdict.py imported two names consistency.py did not define, which made
    # run_pipeline, server AND app unimportable -- all three entry points, including
    # the fallback. It is cheap to check and it is the failure that cost the most.
    try:
        import consistency, verdict                        # noqa: F401
        need = ("ConsistencyResult", "independent_dimension_count")
        missing = [n for n in need if not hasattr(consistency, n)]
        pf.check("pipeline seam consistency<->verdict", not missing,
                 "imports clean" if not missing else f"consistency lacks {missing}",
                 "consistency.py and verdict.py are from different designs. "
                 "See 99_scratch/patch_consistency.py.")
    except Exception as exc:                               # noqa: BLE001
        pf.check("pipeline seam consistency<->verdict", False,
                 f"{type(exc).__name__}: {exc}",
                 "the pipeline cannot import; nothing downstream will start")

    # ---- the scene --------------------------------------------------------------
    scene = Path(args.scene)
    need = ["scene_manifest.json", "ais_tracks.jsonl", "eo_contacts.jsonl"]
    have = scene.is_dir() and all((scene / f).exists() for f in need)
    pf.check("scene directory", have, str(scene),
             f"{exe} 04_demo/make_synthetic_eo.py --out {scene}")

    # STALENESS IS A REAL FAILURE HERE, not a warning. detection_confidence became a
    # REQUIRED field of EoContact on 2026-08-29; a scene written before that raises a
    # ValidationError at load, several frames into startup, with a message about a
    # field the operator never typed.
    if have:
        try:
            first = json.loads((scene / "eo_contacts.jsonl")
                               .read_text(encoding="utf-8").splitlines()[0])
            pf.check("scene freshness", "detection_confidence" in first,
                     "eo_contacts carry detection_confidence"
                     if "detection_confidence" in first else "STALE",
                     f"{exe} 04_demo/make_synthetic_eo.py --out {scene}   "
                     f"# regenerate: detection_confidence is required since 2026-08-29")
        except Exception as exc:                           # noqa: BLE001
            pf.check("scene freshness", False, f"{type(exc).__name__}: {exc}",
                     f"{exe} 04_demo/make_synthetic_eo.py --out {scene}")

    # ---- the AIS source ---------------------------------------------------------
    ais = Path(args.ais) if args.ais else None
    if ais is not None:
        pf.check("AIS replay source", ais.exists(),
                 f"{ais}  ({ais.stat().st_size / 1e6:.0f} MB)" if ais.exists() else str(ais),
                 "pass --ais <csv>, or --no-replay to run the static scene")

    # ---- the camera pose, if a local node is being started ----------------------
    # Both sensor nodes refuse to start on hfov_deg == 0.0 BY DESIGN: a zero HFOV
    # silently makes every bearing meaningless rather than raising, and a bearing is
    # the only measurement this system is actually good at. Catch it here so the
    # refusal is a preflight line and not a subprocess that dies unseen.
    if args.camera is not None:
        pose_p = Path(args.pose)
        okp = pose_p.exists()
        detail, fix = str(pose_p), "survey the camera pose (two known bearings at "
        if okp:
            try:
                pose = json.loads(pose_p.read_text(encoding="utf-8"))
                hf, hm = pose.get("hfov_deg", 0.0), pose.get("height_m", 0.0)
                okp = hf > 0.0 and hm > 0.0
                detail = f"hfov {hf} deg, height {hm} m"
            except Exception as exc:                       # noqa: BLE001
                okp, detail = False, f"{type(exc).__name__}: {exc}"
        pf.check("camera pose", okp, detail,
                 fix + "opposite frame edges) and set hfov_deg + height_m; "
                       "the sensor node refuses a zero HFOV on purpose")

    # ---- the port ---------------------------------------------------------------
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        free = s.connect_ex(("127.0.0.1", args.port)) != 0
    pf.check(f"port {args.port}", free, "free" if free else "IN USE",
             f"lsof -ti tcp:{args.port} | xargs kill    # or pass --port 8001")

    return pf


# ================================================================================
# 2. THE AIS REPLAY CLOCK
# ================================================================================

def _lan_ip() -> str:
    """This machine's address on the LAN the Pi is on.

    Not 127.0.0.1 and not gethostbyname(hostname) -- the latter returns 127.0.0.1 on a
    default macOS install and would print exactly the address that does not work. A UDP
    connect to a routable address sends nothing but makes the kernel choose the
    outbound interface, which is the address the Pi will actually see."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 1))      # TEST-NET-1: routed nowhere, never dialled
            return s.getsockname()[0]
    except Exception:                        # noqa: BLE001
        return "127.0.0.1"


class AisReplay:
    """A clock over recorded AIS. The one piece of new behaviour in this file.

    WHY A REPLAY IS NEEDED AT ALL, since the pipeline already runs on a static scene:
    run_pipeline.load_scene() reads ais_tracks.jsonl once, at construction. Contacts
    could arrive over time via POST /ingest/contacts, but claims could not. So the AIS
    half of the picture was frozen, and everything that only exists in TIME was
    unreachable: a report going stale, a vessel falling silent, a track loitering.
    Criterion 2 ranks BEHAVIOUR, and there is no behaviour without a clock. A demo on a
    frozen claim set can show a spoof but cannot show a vessel GOING dark, which is
    half the brief.

    HOW THE PICTURE IS BUILT AT EACH TICK, and the rule is deliberately the same one a
    watch officer's screen uses: for each MMSI, the LATEST report at or before the
    current scene time, provided that report is younger than --ais-horizon. Two
    consequences follow, and both are wanted:

      * a vessel that stops transmitting DROPS OUT of the claimed picture once its last
        report ages past the horizon. If the camera still sees a hull there, the
        association is now a contact with no claim -- which is the DARK path, arrived at
        by the same route a real one would be. It is not injected and it is not faked.
      * a claim that is merely OLD stays in, carrying its true report_time_utc, so
        assoc.time_delta_s is real and consistency.py's staleness suppression does its
        job on genuinely stale data rather than on a synthetic flag.

    HONEST LIMIT, and it belongs on the slide: an AIS gap in recorded DMA data is NOT
    proof a transponder was switched off. It can equally be a coverage hole -- lane A
    measured 3,265 gaps over 5 minutes across the Fehmarn slice, and the Bornholm slice
    is 2.5x sparser precisely because it sits at the edge of Danish basestation
    coverage. The horizon below decides what this tool calls silence; it does not
    decide what caused it, and lane C's explained_by_sparse_coverage suppressor is what
    keeps that distinction alive downstream.
    """

    def __init__(self, tracks: list, *, horizon_s: float, speed: float,
                 tick_s: float, loop: bool) -> None:
        # Sorted once. Every tick is then a walk of a cursor, not a re-scan -- at
        # 431k reports a per-tick sort would dominate the tick interval.
        self.tracks = sorted(tracks, key=lambda t: t.report_time_utc)
        self.horizon = timedelta(seconds=horizon_s)
        self.speed = speed
        self.tick_s = tick_s
        self.loop = loop
        self.t0 = self.tracks[0].report_time_utc
        self.t1 = self.tracks[-1].report_time_utc
        self.span_s = max((self.t1 - self.t0).total_seconds(), 1.0)
        self.scene_time = self.t0
        self.cursor = 0
        self.latest: dict[str, object] = {}      # mmsi -> most recent AisTrack so far
        self.ticks = 0

    def advance_to(self, scene_time: datetime) -> list:
        """Walk the cursor forward to scene_time and return the current claim set."""
        while self.cursor < len(self.tracks) and \
                self.tracks[self.cursor].report_time_utc <= scene_time:
            t = self.tracks[self.cursor]
            self.latest[t.claimed_mmsi] = t
            self.cursor += 1
        self.scene_time = scene_time
        cutoff = scene_time - self.horizon
        return [t for t in self.latest.values() if t.report_time_utc >= cutoff]

    def rewind(self) -> None:
        self.cursor = 0
        self.latest.clear()

    async def run(self, backend) -> None:
        """Drive the replay. Started inside the server's own event loop."""
        started_wall = time.monotonic()
        print(f"[replay] {len(self.tracks)} reports, "
              f"{len({t.claimed_mmsi for t in self.tracks})} vessels, "
              f"{self.span_s / 60:.1f} min of scene time at {self.speed:g}x "
              f"(horizon {self.horizon.total_seconds():.0f} s)")
        while True:
            elapsed = (time.monotonic() - started_wall) * self.speed
            if elapsed > self.span_s:
                if not self.loop:
                    print(f"[replay] end of window at {self.scene_time.isoformat()}; "
                          "holding the last picture")
                    return
                # Loop for a pitch that outlives the window. The rewind is explicit
                # rather than modular arithmetic on the cursor, because a cursor that
                # wraps without clearing `latest` keeps every vessel alive forever and
                # the DARK path silently stops firing on the second pass.
                started_wall = time.monotonic()
                self.rewind()
                elapsed = 0.0
            current = self.advance_to(self.t0 + timedelta(seconds=elapsed))
            try:
                await backend.replace_tracks(current, reason="ais_replay")
                self.ticks += 1
            except Exception as exc:                       # noqa: BLE001
                # A replay tick must never kill the server. A frozen claim picture is
                # bad; a dead console is worse, and the operator can still see the EO
                # side and the last good ranking.
                print(f"[replay] tick failed: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
            await asyncio.sleep(self.tick_s)


def load_ais(path: Path, *, pose: dict, radius_km: float,
             window: tuple[datetime, datetime] | None) -> list:
    """Read AIS for replay through LANE A's ingest. Never a second CSV reader.

    DmaCsvSource is what knows about this data: the dd/mm/yyyy timestamps that pandas
    would silently swap for every day <= 12, the 63.5% duplicate receptions from
    multiple basestations, the AtoN buoys that are claims with no possible observation,
    the ITU sentinels, and the "Unknown" strings that are not nulls. Reading the CSV
    here with pandas would reproduce every one of those bugs, quietly, in the file that
    feeds the demo."""
    from ais_ingest import DmaCsvSource

    # A bbox around the camera, in degrees. Vessels outside it cannot be observed by
    # this sensor at all, so carrying them costs parse time and buys an empty claim.
    # Longitude degrees shrink with latitude; at 54.6N the factor is ~0.58, and
    # ignoring it would make the box 1.7x too narrow east-west -- cutting off exactly
    # the along-strait traffic the Fehmarn corridor is made of.
    import math
    lat, lon = pose["lat_deg"], pose["lon_deg"]
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.05))
    bbox = (lat - dlat, lon - dlon, lat + dlat, lon + dlon)

    src = DmaCsvSource(path, bbox=bbox,
                       start_utc=window[0] if window else None,
                       end_utc=window[1] if window else None)
    tracks = list(src.tracks())
    print(f"[replay] {src.stats.summary()}")
    return tracks


def parse_window(spec: str | None, day: str = "2026-08-25"
                 ) -> tuple[datetime, datetime] | None:
    """'10:00-11:00' -> a UTC pair.

    DMA timestamps are ASSUMED UTC and that assumption is still unverified (STATUS,
    2026-08-28). If they are local, every window here is 2 h off in August and so is
    every time_delta_s downstream. Recorded rather than hidden."""
    if not spec:
        return None
    a, b = spec.split("-")
    fmt = "%Y-%m-%d %H:%M"
    return (datetime.strptime(f"{day} {a.strip()}", fmt).replace(tzinfo=timezone.utc),
            datetime.strptime(f"{day} {b.strip()}", fmt).replace(tzinfo=timezone.utc))


# ================================================================================
# 3. EDGE INGEST — the local sensor node, when asked for
# ================================================================================

def start_local_node(args, port: int) -> subprocess.Popen | None:
    """Run THIS MACHINE'S video as a sensor node, posting to our own ingest route.

    One node, one bearing model, one set of geometry helpers, whatever the frame source
    is -- a lens, a clip on disk, or a network stream. Running a second, source-specific
    detector would mean the thing rehearsed is not the thing demonstrated.

    Posts to 127.0.0.1 deliberately even though the server binds 0.0.0.0: a local node
    has no reason to leave the machine, and routing it via the LAN address would make
    the demo depend on the venue network being up in order to talk to itself.

    DEFECT FIXED HERE, 2026-08-29, AND IT HAD NEVER WORKED.
      OBSERVED  -- `python 03_src/main.py --camera 0` started, printed the node command,
                   and then the console showed no live node at all.
      CLAIMED   -- this function launches the sensor node with the chosen camera.
      THE MISMATCH -- it passed `--camera`, and the node's flag was `--source`.
                   argparse rejected the unknown flag and the child exited immediately
                   with status 2. Because the child's output is piped and only relayed
                   by pump(), the argparse error went where nobody was looking.
      WHY IT MATTERS -- the live-sensor path was dead on the ONE flag that turns it on,
                   and it failed silently: no traceback in the parent, no node in the
                   console, nothing to distinguish it from a camera that sees nothing.
      THE FIX   -- pass `--source`, which the node has always accepted; the node now
                   also accepts `--camera` as an alias for the same dest, so neither
                   spelling can be wrong again.
      CONFIDENCE -- certain; it is one argparse definition and one string.

    THE NODE ALSO PUBLISHES ITS FRAMES. server.py then relays exactly the JPEG the
    detector ran on, so the console's boxes and its imagery are one observation. The
    server is deliberately NOT given the same video to decode for itself: two decoders
    on one file hold two independent positions in it and the boxes drift off the hulls.
    One decoder, and if it dies /stream honestly reports that the video stopped."""
    node = _DEMO / "pi_sensor.py"
    source = args.video if args.video is not None else str(args.camera)
    cmd = [sys.executable, str(node),
           "--source", str(source),
           "--pose", str(args.pose),
           "--scale", str(args.scale),
           "--node-id", str(args.node_id),
           "--publish-frames", f"http://127.0.0.1:{port}/ingest/frame",
           "--post", f"http://127.0.0.1:{port}/api/contacts"]
    if args.loop:
        cmd.append("--loop")
    print(f"[node] {' '.join(cmd)}")
    try:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
    except Exception as exc:                               # noqa: BLE001
        print(f"[node] could not start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def pump(proc: subprocess.Popen, tag: str) -> None:
    """Relay a child's output with a tag. Without this the node's messages -- including
    'refusing to start: hfov_deg is 0' -- go into a pipe nobody reads, and a node that
    refused to start is indistinguishable from one that is merely seeing nothing."""
    def _run():
        for line in proc.stdout:                           # type: ignore[union-attr]
            print(f"[{tag}] {line.rstrip()}")
    threading.Thread(target=_run, daemon=True).start()


# ================================================================================
# 4. BROWSER
# ================================================================================

def open_when_ready(url: str, health: str, timeout_s: float = 30.0) -> None:
    """Open the console only once the server ANSWERS.

    Opening immediately races uvicorn's bind and lands on a connection-refused page,
    which on stage reads as a broken tool. /health is polled rather than the page
    itself because the page renders before the pipeline has produced anything, and an
    empty ranked queue is indistinguishable from an empty sea."""
    def _run():
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(health, timeout=1.0) as r:
                    if r.status == 200:
                        webbrowser.open(url)
                        return
            except Exception:                              # noqa: BLE001
                time.sleep(0.4)
        print(f"[browser] server did not answer {health} in {timeout_s:.0f}s; "
              f"open {url} by hand", file=sys.stderr)
    threading.Thread(target=_run, daemon=True).start()


# ================================================================================
# 5. CLI
# ================================================================================

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Maritime approaches: one command, whole rig.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples
  python 03_src/main.py
      T1. Synthetic scenario with injected faults and ground truth. No hardware.

  python 03_src/main.py --camera 0 --scale 20
      T1 plus a lens on this machine as a live sensor node over the tabletop rig.

  python 03_src/main.py --video 02_data/clips/approach.mp4 --loop --scale 20
      T1 plus a VIDEO as the EO sensor -- the path that replaced the Raspberry Pi.
      The node detects on the clip and publishes the frames it detected on, so the
      console shows the video with its own boxes over it.

  python 03_src/main.py --video "https://host/live/stream.m3u8" --scale 1
      The same, on a network stream. CHECK THE FEED'S TERMS BEFORE SHOWING IT, and
      keep people out of frame: vessels are the subject, not persons.

  python 03_src/main.py --ais 02_data/slices/aisdk-2026-08-25_fehmarn_belt.csv \\
                        --window 10:00-11:00 --speed 60
      Real recorded Danish AIS, one hour of the Fehmarn Belt at 60x.
      Identities are pseudonymised on every outbound route.
""")
    p.add_argument("--scene", default=str(DEFAULT_SCENE),
                   help="scene directory from make_synthetic_eo.py")
    p.add_argument("--ais", default=str(DEFAULT_AIS),
                   help="AIS CSV to replay (DMA format). Default: the synthetic "
                        "scenario. Point it at 02_data/slices/*.csv for real traffic.")
    p.add_argument("--window", default=None,
                   help="UTC window within the AIS day, e.g. 10:00-11:00")
    p.add_argument("--speed", type=float, default=30.0,
                   help="replay rate. 30 = one minute of sea per two seconds.")
    p.add_argument("--tick", type=float, default=1.0,
                   help="seconds between replay ticks (wall clock)")
    p.add_argument("--ais-horizon", type=float, default=300.0,
                   help="a claim older than this leaves the picture. This is what the "
                        "tool calls silence; it does not decide what caused it.")
    p.add_argument("--radius-km", type=float, default=25.0,
                   help="bbox half-width around the camera when reading real AIS")
    p.add_argument("--no-loop", action="store_true",
                   help="stop at the end of the window instead of repeating")
    p.add_argument("--no-replay", action="store_true",
                   help="static scene only; do not start the clock")
    p.add_argument("--camera", type=int, default=None,
                   help="also run this local camera index as a sensor node")
    p.add_argument("--video", default=None,
                   help="THE EO SOURCE THAT REPLACED THE PI. A video file on disk, or "
                        "a network stream URL (http/https/rtsp; an HLS .m3u8 counts). "
                        "Runs the sensor node on it and shows it in the console. "
                        "Mutually exclusive with --camera.")
    p.add_argument("--loop", action="store_true",
                   help="rewind a video file at the end, so the clip outlasts the "
                        "pitch instead of the sensor going dark mid-sentence")
    p.add_argument("--node-id", default="sensor-01",
                   help="provenance. Appears on every contact and in the case file.")
    p.add_argument("--live", action="store_true",
                   help="PROMOTE THE LIVE SENSOR at startup instead of the recorded "
                        "scene. Off by default: the recorded scene carries the full "
                        "ranked picture with its injected faults, and a blob detector "
                        "on a short clip does not. The console can switch either way "
                        "at any time; this only chooses what is live when it opens.")
    p.add_argument("--scale", type=float, default=1.0,
                   help="metres of sea per metre of table (tabletop rig)")
    p.add_argument("--pose", default=str(_DEMO / "camera_pose_TABLETOP.json"),
                   help="camera pose for the local sensor node")
    p.add_argument("--host", default="0.0.0.0",
                   help="0.0.0.0 by default SO THE PI CAN REACH IT. server.py "
                        "defaults to loopback and that is the demo's most expensive "
                        "misconfiguration.")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--web", default=str(_SRC / "web"),
                   help="console directory. 03_src/web is the WebSocket console; "
                        "04_demo/web is the polling fallback page.")
    p.add_argument("--ais-coverage-confidence", type=float, default=None,
                   help="0-1. How complete is AIS coverage in this sector? Gates the "
                        "DARK path: below 0.75 every dark verdict defers, because a "
                        "receiver hole and a switched-off transponder look identical. "
                        "Omitted = verdict.py's conservative 0.70, which is BELOW that "
                        "threshold, so EVERY dark verdict defers. Correct for real "
                        "Baltic data; wrong for a synthetic scene whose coverage is "
                        "complete by construction. MEASURE it for a real sector "
                        "(ais_trajectory.detect_gaps) rather than picking one.")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--allow-real-identities", action="store_true",
                   help="DANGEROUS. Leave off for anything a judge or camera can see.")
    p.add_argument("--preflight-only", action="store_true")
    args = p.parse_args(argv)

    if args.video is not None and args.camera is not None:
        raise SystemExit("--video and --camera are mutually exclusive: one node, one "
                         "frame source, so the case file can name it.")

    print("=" * 78)
    print("  MARITIME APPROACHES — shore station, one command")
    print("=" * 78)

    pf = preflight(args)
    pf.report()
    if pf.fatal:
        print("\n  Preflight failed. Fix the FAIL lines above and re-run.\n"
              "  Nothing was started, so nothing is half-configured.\n")
        return 2
    if args.preflight_only:
        return 0

    import uvicorn
    from server import create_app

    # ---- build the replay before the server, so a bad AIS path fails fast --------
    startup_tasks = []
    replay = None
    if not args.no_replay:
        manifest = json.loads(
            (Path(args.scene) / "scene_manifest.json").read_text(encoding="utf-8"))
        pose = manifest["pose"]
        try:
            tracks = load_ais(Path(args.ais), pose=pose,
                              radius_km=args.radius_km,
                              window=parse_window(args.window))
        except Exception as exc:                           # noqa: BLE001
            print(f"[replay] could not read {args.ais}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            tracks = []
        if len(tracks) < 2:
            # Degrade to the static scene rather than refusing to start. A console
            # showing the loaded scene is a demo; a console that will not open is not.
            print("[replay] fewer than two reports in range — running the STATIC "
                  "scene. The claimed picture will not advance.", file=sys.stderr)
        else:
            replay = AisReplay(tracks, horizon_s=args.ais_horizon, speed=args.speed,
                               tick_s=args.tick, loop=not args.no_loop)
            startup_tasks.append(replay.run)

    app = create_app(
        scene_dir=Path(args.scene),
        web_dir=Path(args.web),
        audit_path=_DEMO / "operator_audit.jsonl",
        mjpeg_url=None,
        # THE SERVER GETS THE VIDEO TOO, and this reverses an earlier decision.
        #
        # It used to pass None here, reasoning that the node owns the frame source and
        # two decoders on one file drift apart. The reasoning was right; the
        # consequence was not. It made the PICTURE depend entirely on the NODE, so a
        # node that refused to start over something unrelated — an unfilled pose file,
        # on the first real run — left the console reporting "no video source
        # configured" about a source the operator had explicitly configured.
        #
        # server.py's /stream now serves the relay whenever a node is publishing and
        # decodes only when one is not, switching between them inside the same
        # response. So the single-decoder guarantee still holds while it matters, and
        # when the node is down there is still a picture instead of a false statement.
        #
        # A camera index is NOT passed: macOS will not usually open the built-in camera
        # twice, and the node needs it more than the pane does.
        camera_index=None,
        video_source=(args.video if args.video is not None else None),
        allow_real_identities=args.allow_real_identities,
        node_stale_after_s=8.0,
        # RECORDED UNLESS ASKED OTHERWISE. This reverses patch 7, which made --video
        # promote the live node automatically.
        #
        # WHY THAT WAS WRONG: the active source decides whose CONTACTS reach the
        # picture, not which imagery is shown. Promoting the node discarded the
        # recorded scene -- 8 contacts, 12 AIS claims, an injected fault plan producing
        # 3 SPOOF / 2 DARK / 2 UNKNOWN / 1 MATCH -- and replaced it with what a
        # background subtractor finds in a short clip: two blobs, no identities, two
        # DARK verdicts. Nothing errored. The picture was simply replaced by a much
        # poorer one that still looked plausible, which is the worst failure shape there
        # is.
        #
        # So the default is RECORDED again, for source_switch.py's original reason: it
        # is the only source that cannot fail. --live promotes the node when the live
        # path is actually what you want to show, and the operator can switch at any
        # time from the console.
        initial_source=("MAC_CAMERA" if (args.live and args.camera is not None)
                        else "VIDEO_STREAM" if (args.live and args.video is not None)
                        else "RECORDED"),
        startup_tasks=startup_tasks,
        # Handed to the factory rather than set on the backend afterwards, because the
        # lifespan runs one recompute at startup: set it later and the FIRST picture a
        # browser sees was computed under a different assumption from every later one,
        # with nothing on screen saying so.
        ais_coverage_confidence=args.ais_coverage_confidence,
    )

    node_proc = (start_local_node(args, args.port)
                 if (args.camera is not None or args.video is not None) else None)
    if node_proc is not None:
        pump(node_proc, "node")

    ip = _lan_ip()
    url = f"http://127.0.0.1:{args.port}/"
    print("\n" + "=" * 78)
    print(f"  CONSOLE      {url}")
    print(f"  SENSOR POSTS http://{ip}:{args.port}/api/contacts")
    print("=" * 78)
    print(f"  scene        {args.scene}")
    print(f"  AIS          " + ("static scene (no clock)" if replay is None else
                                f"{Path(args.ais).name} @ {args.speed:g}x, "
                                f"{len(replay.tracks)} reports"))
    print(f"  EO source    " + (
        f"video {args.video}{' (looping)' if args.loop else ''}, scale {args.scale:g}"
        if node_proc and args.video is not None else
        f"camera {args.camera}, scale {args.scale:g}"
        if node_proc else
        "none — RECORDED scene only. Pass --video <file|url> or --camera 0 to put a "
        "live sensor and a picture on the console."))
    print(f"  AIS coverage " + (
        f"{args.ais_coverage_confidence:.2f} (stated by you)"
        if args.ais_coverage_confidence is not None
        else "0.70 default — BELOW the 0.75 sparse threshold, so every DARK verdict "
             "will defer. Pass --ais-coverage-confidence if you know better."))
    print(f"  identities   " + ("REAL PERMITTED on /evidence?allow_real=1"
                                if args.allow_real_identities
                                else "pseudonymised on every outbound route"))
    print(f"  audit trail  {_DEMO / 'operator_audit.jsonl'}   (criterion 4)")
    print("=" * 78)
    print("  A sensor node on ANOTHER machine, if you ever want one:")
    print(f"    python3 04_demo/pi_sensor.py --source <file|url|index> \\")
    print(f"        --pose <pose.json> \\")
    print(f"        --post http://{ip}:{args.port}/api/contacts \\")
    print(f"        --publish-frames http://{ip}:{args.port}/ingest/frame")
    print(f"    (needs --host 0.0.0.0, which is this file's default)")
    print("  Ctrl-C to stop.\n")

    if not args.no_browser:
        open_when_ready(url, f"http://127.0.0.1:{args.port}/health")

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        if node_proc is not None:
            node_proc.terminate()
            try:
                node_proc.wait(timeout=3)
            except Exception:                              # noqa: BLE001
                node_proc.kill()
        print("\n[main] stopped. Audit trail kept at "
              f"{_DEMO / 'operator_audit.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
