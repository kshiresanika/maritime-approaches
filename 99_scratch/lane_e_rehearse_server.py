"""
lane_e_rehearse_server.py — time the SERVER half of demo recovery. Run on the Mac.

WHY THIS SCRIPT EXISTS. The console half of "kill the server and restart it" was
measured in the agent's container against a stdlib WebSocket stub (see
99_scratch/handoff_E.md section 13): worst-case client recovery 2.02 s, bounded by the
poll interval. The half that could NOT be measured there is how long server.py itself
takes to come back, because the container has no fastapi, no pyproj, no loguru and no
route to PyPI to install them. This script closes that gap with a number instead of an
estimate.

WHAT IT MEASURES, kept apart because they have different causes and different fixes:
  boot_ms       process start -> /health answers 200. Python import + scene load.
  pipeline_ms   /health answering -> /ws delivers a ConsoleState with records in it.
                This is run_scene() on the whole scene, and it is the half nobody can
                shorten by reconnecting faster.
  total_ms      the sum — what an operator experiences, added to the client's own
                recovery (bounded at ~2 s, already measured).

    source .venv/bin/activate
    python 99_scratch/lane_e_rehearse_server.py --scene 04_demo/out/scene01 --runs 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def one_run(scene: Path, port: int, python: str) -> dict:
    """Start server.py, time it to healthy, then time it to a populated ConsoleState."""
    import urllib.error
    import urllib.request

    import websockets  # ships with uvicorn[standard]

    proc = subprocess.Popen(
        [python, str(ROOT / "03_src" / "server.py"), "--scene", str(scene),
         "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=str(ROOT))
    t0 = time.perf_counter()
    boot = None
    try:
        # --- boot: first 200 from /health ---
        while time.perf_counter() - t0 < 60:
            if proc.poll() is not None:
                err = proc.stderr.read().decode()[-800:] if proc.stderr else ""
                raise SystemExit(f"server exited during boot.\n{err}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                    boot = time.perf_counter() - t0
                    break
            except (urllib.error.URLError, OSError):
                await asyncio.sleep(0.05)
        if boot is None:
            raise SystemExit("server never became healthy within 60 s")

        # --- pipeline: a ConsoleState carrying records ---
        t1 = time.perf_counter()
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
            snap = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        pipe = time.perf_counter() - t1
        return {
            "boot_ms": round(boot * 1000, 1),
            "pipeline_ms": round(pipe * 1000, 1),
            "total_ms": round((boot + pipe) * 1000, 1),
            "records": len(snap.get("records", [])),
            "last_seq": snap.get("last_seq"),
            "nodes": len(snap.get("nodes", [])),
        }
    finally:
        proc.kill()
        proc.wait()


async def main() -> int:
    p = argparse.ArgumentParser(description="Time server.py cold-start recovery.")
    p.add_argument("--scene", type=Path, default=ROOT / "04_demo" / "out" / "scene01")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--port", type=int, default=8123,
                   help="Deliberately NOT 8000 — this must not fight a demo server "
                        "you already have running.")
    args = p.parse_args()

    print("=" * 70)
    print("SERVER-SIDE RECOVERY — measured, one cold start per run")
    print("=" * 70)
    rows = []
    for i in range(args.runs):
        r = await one_run(args.scene, args.port, sys.executable)
        rows.append(r)
        print(f"  run {i + 1}: boot {r['boot_ms']:>7.1f} ms | pipeline "
              f"{r['pipeline_ms']:>7.1f} ms | total {r['total_ms']:>7.1f} ms  "
              f"({r['records']} records, last_seq {r['last_seq']}, {r['nodes']} nodes)")
        await asyncio.sleep(1.0)

    tot = [r["total_ms"] for r in rows]
    print("-" * 70)
    print(f"  median total {statistics.median(tot):.0f} ms | max {max(tot):.0f} ms")
    print()
    print("  ADD THE CLIENT HALF, already measured at 2.02 s worst case, to get what an")
    print("  operator sees. The client is bounded by its 2 s poll interval regardless of")
    print("  how far its backoff had escalated — see handoff_E.md section 13.")
    if statistics.median(tot) > 4000:
        print()
        print("  NOTE: over 4 s. On stage that is a visible gap. The pipeline half is the")
        print("  one to attack (a smaller scene), not the boot half.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
