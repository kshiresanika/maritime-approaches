"""
lane_e_server_check.py — static checks on 03_src/server.py.

WHY STATIC AND NOT pytest: the agent shell is a Linux VM. The project venv is a macOS
venv, so pydantic's compiled core will not import here and neither will fastapi. Every
check below therefore reads the SOURCE with `ast` and asserts a structural property.

That is a real limit, stated rather than hidden: these checks prove the file is
STRUCTURALLY correct — it imports what exists, it does not reimplement what it must
not, every route the brief asked for is present, no import is dead. They prove NOTHING
about runtime behaviour. The runtime evidence comes from the paste block in
99_scratch/handoff_E.md, run on the Mac.

    python3 99_scratch/lane_e_server_check.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "03_src" / "server.py"
CONTRACTS = ROOT / "03_src" / "contracts.py"

FAILURES: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILURES).append(f"{name}{' — ' + detail if detail else ''}")


src = SERVER.read_text(encoding="utf-8")
tree = ast.parse(src)
ctree = ast.parse(CONTRACTS.read_text(encoding="utf-8"))


def code_only(text: str) -> str:
    """
    The source with comments and docstrings removed.

    WHY THIS FUNCTION EXISTS — it was written in response to a real false positive, not
    in anticipation of one. The first version of checks 8 and 10 matched raw source and
    both FAILED: check 8 forbids opening the audit file "w", and the docstring
    explaining that rule contains those characters; check 10 forbids a merged "vessel"
    payload, and the comment explaining why contains that word. A file that documents a
    rule was reported as breaking it.

    The general lesson, written down rather than quietly fixed: a substring check over
    source cannot tell code from prose ABOUT code, so the better a file is commented
    the more false positives it produces — a check that punishes explanation. Those two
    failures were harmless because they were loud. The dangerous mirror image is a
    check that PASSES because the forbidden token happens to sit in a comment.

    SECOND DEFECT, FOUND THE SAME WAY: the first fix used a tokenize heuristic —
    "a STRING preceded by NEWLINE/NL/INDENT is a docstring". That silently ate every
    continuation line of an implicitly concatenated string inside parentheses, because
    tokenize emits NL for newlines within brackets. Checks 7 and 8 then failed for the
    opposite reason: the code they were looking for had been deleted by the stripper.
    Docstrings are now identified by AST POSITION, which is exact, instead of by what
    token happened to precede them.
    """
    import io
    import tokenize

    # Exact (line, col) of every docstring in the file, from the AST — not guessed.
    doc_positions = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                doc_positions.add((body[0].value.lineno, body[0].value.col_offset))

    out: list[str] = []
    last_line = 1
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and tok.start in doc_positions:
            continue
        if tok.start[0] != last_line:
            out.append("\n")
            last_line = tok.start[0]
        out.append(tok.string)
    return "".join(out)


CODE = code_only(src)

# ---------------------------------------------------------------------------- 1
# Every name imported from contracts must actually exist in contracts.py.
# WHY: a typo'd contract name is a NameError at request time — i.e. on stage, on the
# one route nobody exercised in rehearsal — not at import time.
contract_names = {
    n.name for node in ctree.body if isinstance(node, ast.ClassDef) for n in [node]
} | {
    t.id for node in ctree.body if isinstance(node, ast.Assign)
    for t in node.targets if isinstance(t, ast.Name)
} | {
    node.target.id for node in ctree.body
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
}
imported_from_contracts = {
    a.name for node in ast.walk(tree)
    if isinstance(node, ast.ImportFrom) and node.module == "contracts"
    for a in node.names
}
missing = sorted(imported_from_contracts - contract_names)
check("1. every contracts import exists", not missing,
      f"missing: {missing}" if missing else f"{len(imported_from_contracts)} names")

# ---------------------------------------------------------------------------- 2
# THE ORCHESTRATION RULE. server.py must not import the decision modules directly.
# WHY: importing them is how a "small" reimplementation starts. Going through
# run_pipeline keeps exactly one sequencing of the five stages in the repo.
FORBIDDEN = {"association", "consistency", "verdict", "prioritizer"}
top_imports = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        top_imports |= {a.name.split(".")[0] for a in node.names}
    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        top_imports.add(node.module.split(".")[0])
leaked = sorted(FORBIDDEN & top_imports)
check("2. no direct import of a decision module", not leaked,
      f"LEAKED: {leaked}" if leaked else "goes through run_pipeline only")

# ---------------------------------------------------------------------------- 3
# No decision arithmetic. The server must never compare a confidence or a significance
# against a threshold — that is a second opinion, and it will diverge from the
# pipeline's silently.
DECISION_ATTRS = {"significance", "assoc_score", "runner_up_score", "delta",
                  "tolerance"}
bad_cmp = []
for node in ast.walk(tree):
    if isinstance(node, ast.Compare):
        for side in [node.left, *node.comparators]:
            if isinstance(side, ast.Attribute) and side.attr in DECISION_ATTRS:
                bad_cmp.append(f"line {node.lineno}: .{side.attr}")
check("3. no verdict/priority arithmetic in the server", not bad_cmp,
      "; ".join(bad_cmp) if bad_cmp else "server carries, pipeline decides")

# ---------------------------------------------------------------------------- 4
# Every route the brief specified is present, with the right method.
REQUIRED = [
    ("get", "/"), ("websocket", "/ws"), ("post", "/ingest/contacts"),
    ("get", "/stream"), ("post", "/action"), ("get", "/evidence/{record_id}"),
    ("get", "/health"),
    ("post", "/api/contacts"),   # pi_sensor.py's hardcoded default path
    ("get", "/api/state"),       # lane D's console, so app.py stays interchangeable
]
found = set()
for node in ast.walk(tree):
    for dec in getattr(node, "decorator_list", []):
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                and dec.args and isinstance(dec.args[0], ast.Constant)):
            found.add((dec.func.attr, dec.args[0].value))
absent = [r for r in REQUIRED if r not in found]
check("4. every required route present", not absent,
      f"MISSING: {absent}" if absent else f"{len(found)} routes")

# ---------------------------------------------------------------------------- 5
# Broadcast on change: every _queue_event call site must be guarded by a fingerprint
# comparison, EXCEPT the two that are inherently events (an operator ack, a node flip).
# WHY: an unguarded emitter is how the queue starts reshuffling every second again.
emitters = {}
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        calls = [c for c in ast.walk(node)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                 and c.func.attr == "_queue_event"]
        if calls:
            body = ast.dump(node)
            guarded = ("_sent_" in body)
            emitters[node.name] = guarded
UNGUARDED_BY_DESIGN = {"record_action"}  # an ack is caused by a human, never by a poll
unguarded = sorted(n for n, g in emitters.items()
                   if not g and n not in UNGUARDED_BY_DESIGN)
check("5. every periodic emitter is fingerprint-guarded", not unguarded,
      f"UNGUARDED: {unguarded}" if unguarded
      else f"guarded: {sorted(n for n, g in emitters.items() if g)}")

# ---------------------------------------------------------------------------- 6
# seq allocation happens only where an event is queued, so wire order == seq order.
bad_seq = []
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        dump = ast.dump(node)
        if node.name == "_next_seq":
            continue  # the allocator itself, obviously
        if "_next_seq" in dump and "_queue_event" not in dump:
            bad_seq.append(node.name)
check("6. seq is allocated only alongside an emit", not bad_seq,
      f"ORPHAN seq allocation in: {bad_seq}" if bad_seq else "ordering guarantee holds")

# ---------------------------------------------------------------------------- 7
# Pseudonymisation is the default on the route that leaves the machine.
ev = [n for n in ast.walk(tree)
      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
      and n.name == "evidence_payload"]
ok7 = bool(ev) and "allow_real_identities" in ast.dump(ev[0])
gate = "--allow-real-identities" in CODE and "allow_real=1 refused" in CODE
check("7. /evidence pseudonymised by default, behind two gates", ok7 and gate,
      "server flag AND query param both required" if ok7 and gate else "GATE MISSING")

# ---------------------------------------------------------------------------- 8
# The audit trail is append-only and fsynced.
# NOTE: code_only() re-joins tokens without their original spacing, so every
# substring check against CODE must be whitespace-insensitive. Comparing against the
# formatted spelling is what made this check fail on a file that satisfied it.
_TIGHT = "".join(CODE.split())
ok8 = ('.open("a",encoding="utf-8")' in _TIGHT and "os.fsync" in _TIGHT
       and '"w"' not in _TIGHT)
check("8. audit trail append-only + fsynced", ok8,
      "opened 'a', flushed, fsynced; no 'w' anywhere in the file" if ok8
      else "NOT append-only or not fsynced")

# ---------------------------------------------------------------------------- 9
# No dead imports. A dead import in a file with an optional dependency is how a
# needless requirement gets shipped.
imported: dict[str, int] = {}
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for a in node.names:
            imported[(a.asname or a.name).split(".")[0]] = node.lineno
    elif isinstance(node, ast.ImportFrom):
        for a in node.names:
            imported[a.asname or a.name] = node.lineno
used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
used |= {n.value.id for n in ast.walk(tree)
         if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
used |= {s for s in ast.walk(tree) if isinstance(s, str)}
dead = sorted(k for k in imported if k not in used and k not in {"annotations"})
check("9. no dead imports", not dead, f"DEAD: {dead}" if dead else f"{len(imported)} imports, all used")

# ---------------------------------------------------------------------------- 10
# The claimed/observed wall survives on the wire: no merged vessel payload.
ok10 = ("eo_contact=c,ais_track=rec.ais_track" in CODE.replace(" ", "")
        and "class Vessel" not in CODE and "vessel" not in CODE.lower())
check("10. no merged vessel payload on the stream", ok10,
      "eo_contact and ais_track stay two fields" if ok10 else "WALL BREACHED")

# ===========================================================================
# CHECKS 11-16 — 03_src/web/index.html
# ===========================================================================
PAGE = ROOT / "03_src" / "web" / "index.html"
page = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
check("11. console page exists", bool(page), f"{len(page)} bytes" if page else "MISSING")

if page:
    import re as _re

    # 12. ONE FILE, NO BUILD STEP. Every <script> must be inline; a src= would mean a
    # second file to keep in sync and a second thing to forget on a USB stick at 03:00.
    srcs = _re.findall(r'<script[^>]+src=', page)
    check("12. every script is inline (no build step, no bundler)", not srcs,
          f"{len(_re.findall(r'<script>', page))} inline blocks, 0 external"
          if not srcs else f"external script tags: {srcs}")

    # 13. ONE frontend dependency family. Leaflet only, and only from sources the page
    # falls back through. Anything else is a dependency nobody agreed to.
    hosts = set(_re.findall(r'https://([a-z0-9.-]+)/', page))
    allowed = {"unpkg.com", "cdnjs.cloudflare.com", "tile.openstreetmap.org",
               "www.w3.org", "{s}.tile.openstreetmap.org"}
    extra = sorted(h for h in hosts if h not in allowed)
    check("13. no frontend dependency beyond Leaflet", not extra,
          f"hosts: {sorted(hosts)}" if not extra else f"UNAPPROVED: {extra}")

    # 14. THE IDENTITY GUARD. The page must refuse to print a non-pseudonymised MMSI
    # even if the server sends one. Verified live in Chromium — see handoff_E.md.
    guard = ("function renderIdent" in page and "raiseIdAlarm" in page
             and 'SYNTH_MID = "999"' in page and "REDACTED" in page)
    check("14. client-side identity guard present", guard,
          "renders REDACTED and raises a banner on a real MMSI" if guard else "MISSING")

    # 15. THE PAGE MUST NOT RE-RANK. Criterion 2's ordering is the prioritiser's. A
    # console that sorts by score would silently disagree with the pipeline the moment
    # a tie-break differed — and would look right while doing it.
    # NOTE: match the whole LINE, not up to the first ')'. The obvious regex
    # `\.sort\(([^)]*)\)` stops inside the comparator's own `(a, b)` and captures
    # nothing useful — it reported a false failure on a page that satisfies the rule.
    sorts = [ln.strip() for ln in page.splitlines() if ".sort(" in ln]
    bad_sort = [x for x in sorts if "score" in x]
    ranks_only = any("priority.rank" in x for x in sorts)
    check("15. queue orders on priority.rank only, never on score",
          ranks_only and not bad_sort,
          "the ranking is the prioritiser's" if ranks_only and not bad_sort
          else f"sorts: {sorts}")

    # 16. EVERY COLOUR IS MEASURED. Any hex in :root must appear in the contrast
    # script, so a colour cannot be added by eye and skip the WCAG gate.
    contrast_src = (ROOT / "99_scratch" / "lane_e_contrast.py").read_text(encoding="utf-8")
    root_block = page.split(":root{", 1)[1].split("}", 1)[0] if ":root{" in page else ""
    root_hexes = {h.lower() for h in _re.findall(r'#[0-9a-fA-F]{6}', root_block)}
    measured = {h.lower() for h in _re.findall(r'#[0-9a-fA-F]{6}', contrast_src)}
    unmeasured = sorted(root_hexes - measured)
    check("16. every :root colour was run through the contrast gate", not unmeasured,
          f"{len(root_hexes)} colours, all measured" if not unmeasured
          else f"UNMEASURED: {unmeasured}")

print("=" * 74)
print("LANE E — STATIC CHECKS ON 03_src/server.py + 03_src/web/index.html")
print("=" * 74)
for line in PASSES:
    print(f"  ok  {line}")
for line in FAILURES:
    print(f"  XX  {line}")
print("-" * 74)
print(f"{len(PASSES)} passed, {len(FAILURES)} failed")
print()
print("THESE CHECKS PROVE STRUCTURE, NOT BEHAVIOUR, and the two halves differ:")
print("  server.py   NOTHING has been executed — no pydantic, no fastapi in this VM.")
print("  index.html  WAS rendered in headless Chromium against a stub serving")
print("              scene01/ranked.json: 0 page errors, 8 rows, the identity guard")
print("              verified firing on a real MMSI. See 99_scratch/lane_e_console_render.png")
print("              and handoff_E.md section 8. The Leaflet map itself is still")
print("              UNVERIFIED — that container had no route to the CDN.")
print("Run the paste block in 99_scratch/handoff_E.md on the Mac for the rest.")
sys.exit(1 if FAILURES else 0)
