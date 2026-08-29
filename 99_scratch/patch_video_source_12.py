#!/usr/bin/env python3
"""
99_scratch/patch_video_source_12.py — PART 9: the edge ingest route has NEVER worked.

FOUND BY 99_scratch/lane_video_e2e.py, the first test that posts to a route over a real
socket instead of through a stub.

  OBSERVED  — every POST to /ingest/contacts, /api/contacts and /ingest/frame returns
              422 {"loc": ["query", "request"], "msg": "Field required"}. Not some
              bodies. Every one.
  CLAIMED   — "pi_sensor.py already posts a bare JSON list and its default path is
              /api/contacts" (server.py, _ingest docstring). The sensor node's entire
              reason to exist is posting observations to this route.
  THE CAUSE — `from __future__ import annotations` at the top of this file turns every
              annotation into a STRING. FastAPI resolves those strings with
              typing.get_type_hints() against the function's MODULE globals. But
              `Request` and `WebSocket` are imported INSIDE create_app(), deliberately,
              so that a teammate without fastapi can still import this module. So the
              name `Request` does not exist at module scope, the string "Request" does
              not resolve, and FastAPI falls back to treating `request` as an ordinary
              QUERY PARAMETER — which is missing, hence 422 before the handler is ever
              entered.
  WHY IT WAS NOT CAUGHT — every check that exercised ingest called the backend object
              directly, or ran against a stubbed fastapi (lane_f_edge_check.py stubs it
              by name). Nothing had ever put an HTTP request on a socket and read the
              status code back. The two halves were each correct in isolation.
  WHY IT MATTERS — this is the claimed/observed wall's only doorway. With it shut, no
              observation from any sensor could reach the picture, on any source, ever.
              Together with main.py's --camera/--source mismatch that is two independent
              breaks on the same path, which is why nobody had seen past the first one.
  THE FIX   — publish the two names into module globals as soon as they are imported,
              so get_type_hints() can resolve them. The lazy import is preserved: a
              machine without fastapi still imports this module fine, because the
              assignment only runs inside create_app(), which already requires it.
  CONFIDENCE — certain, and verified both ways: 422 before, 200 after, over a real
              socket, in lane_video_e2e.py.
  WHAT WOULD CHANGE THE ANSWER — nothing about the diagnosis. A tidier long-term fix is
              to move the fastapi import to module scope behind _require_web_stack();
              that is a bigger change than this file should make during an event.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
p = ROOT / "03_src/server.py"
s = p.read_text(encoding="utf-8")

OLD = """    from starlette.websockets import WebSocketDisconnect

    # ONE video source value, resolved once, here."""
NEW = '''    from starlette.websockets import WebSocketDisconnect

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

    # ONE video source value, resolved once, here.'''

assert s.count(OLD) == 1, "anchor: create_app local imports"
p.write_text(s.replace(OLD, NEW), encoding="utf-8")
print("  patched 03_src/server.py (1 edit) — ingest routes can resolve their types")
