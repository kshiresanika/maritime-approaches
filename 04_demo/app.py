"""
app.py — the operator screen. Python standard library only.

WHY STDLIB AND NOT FASTAPI
The demo has to run with the wifi off, on a machine nobody has time to debug on Sunday
morning, in a venue whose network is contested by every other team. `python -m
http.server` semantics have shipped with Python for twenty years and cannot fail to
install. FastAPI would be nicer to write and is one `pip install` away from being the
reason the demo does not start.

The page it serves has NO CDN dependencies either -- no Leaflet, no React, no fonts. The
plan view is hand-drawn SVG. A demo that needs the internet to draw a map is a demo that
does not run in a basement.

TWO MODES, ONE SERVER
  replay -- reads a scene written by make_synthetic_eo.py, runs the full pipeline, and
            serves the result. This is D0, and it is the guaranteed path.
  live   -- the Raspberry Pi sensor POSTs observed contacts to /api/contacts as it sees
            them; the server re-runs the pipeline against the same AIS claims and the
            page updates. This is D1.

Both modes call exactly the same run_scene(). The pipeline cannot tell whether its
contacts came from a projection or from a camera, which is the entire point of the
EoContact contract.

    python 04_demo/app.py --scene 04_demo/out/scene01
    open http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parents[0] / "03_src"
for p in (str(_SRC), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_pipeline                          # noqa: E402
from contracts import EoContact              # noqa: E402
from prioritizer import DEMO_INFRASTRUCTURE, Asset  # noqa: E402

WEB = _HERE / "web"


class State:
    """
    Everything the server knows, behind one lock.

    A lock rather than a queue because the Pi posts far more often than the page polls,
    and the page only ever wants the CURRENT picture -- a backlog of stale frames is
    worse than useless on a watch screen.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.scene: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None
        self.live_contacts: list[EoContact] = []
        self.mode = "replay"
        self.last_update: str = ""

    def recompute(self) -> None:
        assert self.scene is not None
        contacts = self.live_contacts if self.mode == "live" else self.scene["contacts"]
        result = run_pipeline.run_scene(
            self.scene["tracks"], contacts, self.scene["manifest"]["pose"])
        if self.scene["ground_truth"] and self.mode == "replay":
            result["scoring"] = run_pipeline.score_scene(
                result, self.scene["ground_truth"])
        result["mode"] = self.mode
        result["infrastructure"] = [
            {"id": i.infra_id, "name": i.name, "lat": i.lat_deg, "lon": i.lon_deg,
             "kind": i.kind} for i in DEMO_INFRASTRUCTURE]
        a = Asset()
        result["asset"] = {"id": a.asset_id, "lat": a.lat_deg, "lon": a.lon_deg,
                           "speed_kn": a.speed_kn}
        result["caveat"] = (
            "AIS is real recorded traffic (or a labelled synthetic scenario). "
            "EO contacts in replay mode are SYNTHETIC -- projected from AIS, not "
            "observed by a camera. Anomalies were injected deliberately.")
        self.result = result
        self.last_update = datetime.now(timezone.utc).isoformat()


STATE = State()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def log_message(self, fmt: str, *args) -> None:
        # The default logger prints one line per poll and buries anything useful.
        if "api/contacts" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            with STATE.lock:
                if STATE.result is None:
                    STATE.recompute()
                body = dict(STATE.result or {})
                body["last_update"] = STATE.last_update
            self._json(200, body)
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"

        if path == "/api/contacts":
            # THE PI SENSOR ENDPOINT.
            # Validation happens HERE, on the shore station, not on the Pi. The sensor
            # stays a dumb, fast, dependency-light thing that emits JSON; the contract
            # is enforced at the boundary where it is consumed. That also means a
            # malformed sensor cannot corrupt the picture -- it gets a 400 and the
            # previous state stands.
            try:
                payload = json.loads(raw.decode("utf-8"))
                items = payload if isinstance(payload, list) else payload.get("contacts", [])
                contacts = [EoContact(**c) for c in items]
            except Exception as exc:
                self._json(400, {"error": f"{type(exc).__name__}: {exc}"})
                return
            with STATE.lock:
                STATE.live_contacts = contacts
                STATE.mode = "live"
                STATE.recompute()
            self._json(200, {"ok": True, "accepted": len(contacts)})

        elif path == "/api/mode":
            try:
                mode = json.loads(raw.decode("utf-8")).get("mode", "replay")
            except Exception:
                mode = "replay"
            with STATE.lock:
                STATE.mode = "live" if mode == "live" else "replay"
                STATE.recompute()
            self._json(200, {"ok": True, "mode": STATE.mode})
        else:
            self._json(404, {"error": "not found"})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Serve the operator screen.")
    p.add_argument("--scene", required=True, type=Path)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

    STATE.scene = run_pipeline.load_scene(args.scene)
    STATE.recompute()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    c = (STATE.result or {}).get("counts", {})
    print("=" * 70)
    print(f"  operator screen  ->  http://{args.host}:{args.port}")
    print("=" * 70)
    print(f"  scene    : {args.scene}")
    print(f"  loaded   : {c.get('tracks')} claims, {c.get('contacts')} contacts")
    print(f"  pi posts : POST http://{args.host}:{args.port}/api/contacts")
    print("  ctrl-c to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
