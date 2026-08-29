"""04_demo/web/index.html layout + chip wording. Anchored; aborts on a bad anchor.

MEASURED DEFECT: `main` is sized `calc(100vh - 46px)`, a hardcoded guess at the header
height. The header actually renders 107.5 px at 1920x1080 because its chip row wraps,
so the page is 1142 px tall in a 1080 px viewport and the bottom of the evidence pane
is below the fold. Replacing the guess with a flex column makes `main` take whatever is
actually left, at any header height and any viewport -- which is the property the
hardcoded number was pretending to have.
"""
import sys, pathlib

ROOT = pathlib.Path(sys.argv[1])
P: dict[str, list[tuple[str, str, str]]] = {}
def rep(f, old, new, label): P.setdefault(f, []).append((old, new, label))

# The page becomes a flex column: header takes its natural height, main takes the rest.
rep("04_demo/web/index.html",
'''  body{margin:0;background:var(--bg);color:var(--ink);
       font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}''',
'''  /* FLEX COLUMN, NOT A HARDCODED HEADER HEIGHT. `main` was sized calc(100vh - 46px);
     the header renders 107.5 px at 1920x1080 because the chip row wraps, so the page
     came out 1142 px tall in a 1080 px viewport and the bottom of the evidence pane
     sat below the fold. Flex takes whatever is actually left, at any header height. */
  body{margin:0;background:var(--bg);color:var(--ink);
       display:flex;flex-direction:column;height:100vh;overflow:hidden;
       font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}''',
    "demo console: flex body")

rep("04_demo/web/index.html",
'''  main{display:grid;grid-template-columns:minmax(420px,1fr) minmax(380px,520px);
       gap:1px;background:var(--line);height:calc(100vh - 46px)}''',
'''  main{display:grid;grid-template-columns:minmax(420px,1fr) minmax(380px,520px);
       gap:1px;background:var(--line);flex:1;min-height:0}''',
    "demo console: main fills remainder")

rep("04_demo/web/index.html",
'''  header{display:flex;align-items:baseline;gap:16px;padding:10px 16px;
         border-bottom:1px solid var(--line);background:var(--panel)}''',
'''  header{display:flex;align-items:baseline;gap:16px;padding:10px 16px;flex:none;
         flex-wrap:wrap;border-bottom:1px solid var(--line);background:var(--panel)}''',
    "demo console: header does not shrink")

# The provenance sentence belongs in the caveat line, which has room for it. The chip
# carries the short form -- a chip that wraps to three lines is what deformed the layout.
rep("04_demo/web/index.html",
'''         :anySynth?'SYNTHETIC EO — no camera observed this scene'
         :'none — contacts are camera-observed');
  el.textContent='PROVENANCE '+scen;''',
'''         :anySynth?'SYNTHETIC EO'
         :'camera-observed');
  el.textContent='PROVENANCE '+scen;
  /* The full sentence goes on the caveat line, which has the width for it. A chip long
     enough to wrap the header is what pushed this page below the fold. */
  el.title=anySynth&&!any
    ? 'Every contact in this scene was forward-projected from AIS through a declared '
      +'camera pose. No camera observed any of it.'
    : 'which sensor produced the contacts on this screen';''',
    "demo console: short chip, full sentence in title")

fail = []
for rel, edits in P.items():
    p = ROOT / rel
    if not p.exists(): fail.append(f"{rel}: FILE MISSING"); continue
    s = p.read_text()
    for old, new, label in edits:
        n = s.count(old)
        if n != 1: fail.append(f"{rel}: anchor {n}x (need 1) -- {label}"); continue
        s = s.replace(old, new)
    if not fail: p.write_text(s)

if fail:
    print("ABORTED — no file written:")
    for f in fail: print("  ", f)
    sys.exit(1)
print("applied:")
for rel, edits in P.items():
    for _, _, label in edits: print(f"   {rel}: {label}")
