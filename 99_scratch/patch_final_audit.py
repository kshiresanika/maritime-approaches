"""Final compliance audit patch. Anchored; aborts on any missing or ambiguous anchor.

Fixes, in order of how badly they could go wrong on stage:
  03_src/web/index.html   the demoed console never says the scene is synthetic, and
                          positively renders "injected —" over a wholly constructed
                          scene. Check 6 of the audit, and the worst of the set.
  03_src/web/index.html   the CC-BY attribution block sits at y=1080 on a 1080 px
                          viewport, below a body with overflow:hidden. Measured
                          scrollHeight 1110 > innerHeight 1080. The licence condition
                          is discharged into a region no viewer can reach.
  03_src/web/index.html   header claims 14/14 contrast checks; the script prints 18.
  04_demo/web/index.html  same synthetic-provenance gap as the primary console.
  04_demo/FAILOVER.md     a P0 blocker that no longer exists still heads the document.
"""
import sys, pathlib

ROOT = pathlib.Path(sys.argv[1])
P: dict[str, list[tuple[str, str, str]]] = {}
def rep(f, old, new, label): P.setdefault(f, []).append((old, new, label))


# ===================================================== 03_src/web/index.html  (E-1)
# PROVENANCE. make_synthetic_eo.py stamps EVERY contact `synthetic://` -- including the
# deliberately injected faults -- while both consoles recognise only `injected://`,
# which server.py's live-injection route stamps. Consequence measured by rendering
# scene01: zero INJECTED badges, and the header chip reading "injected —" over a scene
# in which no camera observed anything at all.
#
# THE FIX IS AT THE UI LAYER, NOT THE HARNESS, AND THAT IS DELIBERATE.
# Stamping the fault kind into frame_ref would put the answer key inside the data, which
# make_synthetic_eo.py's own header forbids for a good reason: a downstream lane could
# read it by accident, score 100%, and nobody would find out until the demo. So the
# console declares the provenance it CAN honestly declare -- "no camera observed this"
# -- and never reveals WHICH contact carries the planted fault.
rep("03_src/web/index.html",
'''function isInjected(rec) {
  const f = rec?.eo_contact?.frame_ref || "";
  if (f.startsWith("injected://")) return true;
  return (rec?.frame_refs || []).some(x => String(x).startsWith("injected://"));
}''',
'''function isInjected(rec) {
  const f = rec?.eo_contact?.frame_ref || "";
  if (f.startsWith("injected://")) return true;
  return (rec?.frame_refs || []).some(x => String(x).startsWith("injected://"));
}
/* SYNTHETIC PROVENANCE — a SEPARATE class from an injection, and both must show.
   `injected://` is stamped by server.py when an operator plants a fault live.
   `synthetic://` is stamped by 04_demo/make_synthetic_eo.py on EVERY contact in a
   replay scene, because the whole electro-optical stream is forward-projected from AIS
   through a declared camera pose. No camera observed any of it.
   Recognising only the first is how this console came to render "injected —" over a
   scene that was constructed end to end. A screen that says nothing about provenance
   is read as a screen reporting observations. */
function isSynthetic(rec) {
  const f = rec?.eo_contact?.frame_ref || "";
  if (f.startsWith("synthetic://")) return true;
  return (rec?.frame_refs || []).some(x => String(x).startsWith("synthetic://"));
}''',
    "console: isSynthetic")

# The header chip. Was driven by injection alone; now states provenance for the scene.
rep("03_src/web/index.html",
'''  const anyInjected = [...S.records.values()].some(isInjected);
  const chip = $("cInject");
  chip.classList.toggle("on", anyInjected || !!S.injection);
  $("tInject").textContent = S.injection?.scenario
    || (anyInjected ? injectedScenario([...S.records.values()].find(isInjected)) : "—");''',
'''  const anyInjected = [...S.records.values()].some(isInjected);
  const anySynthetic = [...S.records.values()].some(isSynthetic);
  const chip = $("cInject");
  chip.classList.toggle("on", anyInjected || anySynthetic || !!S.injection);
  /* THREE STATES, AND THE THIRD IS THE ONE THAT WAS MISSING. An em-dash here used to
     mean "nothing injected", which a viewer reads as "these are observations". It now
     only ever appears when the scene really is camera-observed. */
  $("tInject").textContent = S.injection?.scenario
    ? "LIVE INJECTION · " + S.injection.scenario
    : anyInjected ? injectedScenario([...S.records.values()].find(isInjected))
    : anySynthetic ? "SYNTHETIC EO — no camera observed this scene"
    : "none — contacts are camera-observed";''',
    "console: provenance chip")

# The scene caveat. app.py builds it, server.py forwards it on /api/state, this console
# fetched it into S.stage and rendered it NOWHERE. The 04_demo console has always shown
# it; the primary console is the one that is actually demoed (server.py --web defaults
# to 03_src/web), so the honesty statement was being dropped on the floor on the only
# screen a judge sees.
rep("03_src/web/index.html",
'''<div id="grid">
  <div id="linkBanner" role="status" aria-live="polite"></div>''',
'''<div id="sceneCaveat" role="note"></div>

<div id="grid">
  <div id="linkBanner" role="status" aria-live="polite"></div>''',
    "console: caveat element")

rep("03_src/web/index.html",
'''.hint{color:var(--muted);font-size:var(--f-micro);letter-spacing:.04em}''',
'''.hint{color:var(--muted);font-size:var(--f-micro);letter-spacing:.04em}

/* ---- scene provenance bar -------------------------------------------------
   A row of #app, not of #grid: #grid's rows are proportional (42fr/58fr) and an extra
   child would land in one of them and deform the panes. As an implicit auto row of
   #app it takes its own height and #grid's minmax(0,1fr) yields exactly that much, so
   the page still totals 100vh and nothing is pushed below the fold. */
#sceneCaveat{
  display:none;background:#2a2410;border-bottom:2px solid var(--defer);
  color:var(--fg);padding:6px clamp(10px,1vw,18px);
  font-size:var(--f-label);font-weight:700;line-height:1.35;
}
#sceneCaveat.on{display:block}
#sceneCaveat b{color:var(--defer);letter-spacing:.06em}

/* ---- licence attribution --------------------------------------------------
   MEASURED DEFECT THIS FIXES: as a sibling AFTER #app (height:100vh) under a body with
   overflow:hidden, this block rendered at y=1080 in a 1080 px viewport -- present in
   the DOM, reachable by no viewer, and breaking the invariant this file's own header
   asserts (scrollHeight <= innerHeight). CC-BY 4.0 attribution is a CONDITION OF USE;
   discharging it into an unreachable region does not discharge it. It is now the last
   auto row INSIDE #app. */
#attribBlock{
  padding:5px clamp(10px,1vw,18px);font-size:var(--f-micro);line-height:1.45;
  color:var(--muted);border-top:1px solid var(--rule);background:var(--panel);
}
#attribBlock a{color:var(--accent)}''',
    "console: caveat + attribution styles")

rep("03_src/web/index.html",
'''  document.body.classList.toggle("degraded", !live && S.transport !== "poll");''',
'''  document.body.classList.toggle("degraded", !live && S.transport !== "poll");

  /* Rendered on every tick rather than once at load: /api/state is re-applied by the
     poll fallback, and a provenance statement that can be cleared by a refresh is not
     a provenance statement. */
  const cav = $("sceneCaveat");
  if (S.stage && S.stage.caveat) {
    cav.replaceChildren();
    cav.appendChild(el("b", null, "SCENE PROVENANCE — "));
    cav.appendChild(el("span", null, S.stage.caveat));
    cav.classList.add("on");
  } else { cav.classList.remove("on"); }''',
    "console: caveat render")

# Queue row + evidence pane. A badge on the row and a line in the limitations, so the
# fact travels with the contact and not only with the scene.
rep("03_src/web/index.html",
'''    if (isInjected(rec))
      ident.appendChild(el("span", "badge inj", "injected · " + injectedScenario(rec)));
    if (v.defer_to_human) ident.appendChild(el("span", "badge defer", "defer to human"));''',
'''    if (isInjected(rec))
      ident.appendChild(el("span", "badge inj", "injected · " + injectedScenario(rec)));
    else if (isSynthetic(rec))
      ident.appendChild(el("span", "badge inj", "synthetic · no camera"));
    if (v.defer_to_human) ident.appendChild(el("span", "badge defer", "defer to human"));''',
    "console: queue synthetic badge")

rep("03_src/web/index.html",
'''  if (isInjected(rec))
    head.appendChild(el("span", "badge inj", "injected · " + injectedScenario(rec)));''',
'''  if (isInjected(rec))
    head.appendChild(el("span", "badge inj", "injected · " + injectedScenario(rec)));
  else if (isSynthetic(rec))
    head.appendChild(el("span", "badge inj", "synthetic · no camera"));''',
    "console: evidence synthetic badge")

rep("03_src/web/index.html",
'''  for (const line of whatWouldChange(rec)) ul.appendChild(el("li", null, line));''',
'''  if (!isInjected(rec) && isSynthetic(rec)) {
    /* Stated FIRST, above the real limitations, for the same reason the injected line
       is: a projected contact that reads like a detection is the most misleading thing
       this pane can present. It does NOT say which contact carries a planted fault --
       that is in ground_truth.jsonl, which nothing in 03_src may read. */
    const li = el("li", null,
      "THIS CONTACT WAS NOT OBSERVED BY A CAMERA. It was forward-projected from the "
      + "AIS claim through a declared camera pose and perturbed with a measurement-error "
      + "model (04_demo/make_synthetic_eo.py). Its bearing, range, class and their "
      + "uncertainties are the harness's. Any rate derived from this scene measures the "
      + "decision logic against a model of a camera, never against the sea.");
    li.style.color = "var(--unknown)";
    li.style.fontWeight = "700";
    ul.appendChild(li);
  }
  for (const line of whatWouldChange(rec)) ul.appendChild(el("li", null, line));''',
    "console: evidence synthetic limitation")

# Detection boxes: the dashed outline already marks an injection; a synthetic contact
# earns it too, so no box on this screen is read as a camera detection.
rep("03_src/web/index.html",
'''      drawn.push([rec.eo_contact, rec.verdict?.label || "UNKNOWN", rec.record_id,
                  isInjected(rec)]);''',
'''      drawn.push([rec.eo_contact, rec.verdict?.label || "UNKNOWN", rec.record_id,
                  isInjected(rec) || isSynthetic(rec)]);''',
    "console: synthetic bbox dash")

rep("03_src/web/index.html",
'''    t.textContent = (inj ? "INJ " : "") + (label ? label + " " : "") + c.contact_id;''',
'''    t.textContent = (inj ? "NOT CAMERA-OBSERVED " : "") + (label ? label + " " : "")
                  + c.contact_id;''',
    "console: bbox label wording")

# ---- the attribution block: into #app, out of the unreachable region.
rep("03_src/web/index.html",
'''</div>
</div>

<script>
"use strict";
/* ===========================================================================
   0. STATE''',
'''</div>

<div id="attribBlock">
  <!-- ATTRIBUTION BLOCK. CC-BY 4.0 attribution is a CONDITION OF USE, not a courtesy,
       and it was recorded in three internal files while appearing on no surface a
       reader ever sees. The plan was to satisfy it on a pitch slide; 05_pitch/ is
       empty. It is discharged here instead, beside the map it applies to.
       IT LIVES INSIDE #app: as a sibling after it, it rendered at y=1080 in a 1080 px
       viewport under body{overflow:hidden} and no viewer could reach it. -->
  Map rendering: <a href="https://leafletjs.com/">Leaflet</a> 1.9.4 (BSD-2-Clause).
  &nbsp;·&nbsp; AIS: Danish Maritime Authority, act 596/2005 — redistribution not
  addressed; identities pseudonymised on every outbound route.
  &nbsp;·&nbsp; Vessel detection (YOLO path): Ultralytics AGPL-3.0-or-later; COCO
  annotations CC BY 4.0 © COCO Consortium.
  &nbsp;·&nbsp; Coastline (EEA, CC BY 4.0) is registered in 02_data/DATASETS.md and is
  NOT in use on this build — no land-crossing check has run.
</div>
</div>

<script>
"use strict";
/* ===========================================================================
   0. STATE''',
    "console: attribution into #app")

# The old block, now duplicated, is removed. Anchored on its closing tag so the
# replacement cannot swallow anything after it.
rep("03_src/web/index.html",
'''<div id="attribBlock" style="padding:6px 12px;font-size:11px;line-height:1.5;
     color:var(--dim,#8fa3b8);border-top:1px solid var(--border,#586b82)">
  <!-- ATTRIBUTION BLOCK. CC-BY 4.0 attribution is a CONDITION OF USE, not a courtesy,
       and it was recorded in three internal files while appearing on no surface a
       reader ever sees. The plan was to satisfy it on a pitch slide; 05_pitch/ is
       empty. It is discharged here instead, beside the map it applies to. -->
  Map rendering: <a href="https://leafletjs.com/" style="color:inherit">Leaflet</a> 1.9.4
  (BSD-2-Clause). &nbsp;·&nbsp; Coastline: © European Environment Agency, CC BY 4.0,
  reprojected and buffered. &nbsp;·&nbsp; AIS: Danish Maritime Authority, act 596/2005 —
  redistribution not addressed; identities pseudonymised on every outbound route.
</div>
</body>''',
'''</body>''',
    "console: drop stray attribution block")

# ---- the contrast count. The script prints 14 palette rows AND 4 solid-badge pairs.
rep("03_src/web/index.html",
'''  7:1 for text (WCAG AAA), 3:1 for meaningful non-text. 14/14 pass. Two were changed''',
'''  7:1 for text (WCAG AAA), 3:1 for meaningful non-text. 18/18 pass -- 14 palette
  colours against all three grounds, plus the 4 inverted solid badges, which are a
  different pair entirely and were once missed for that reason. Two were changed''',
    "console: contrast count 14 -> 18")


# ===================================================== 04_demo/web/index.html  (E-2)
# The same provenance gap. This console is the fallback, and a fallback that is honest
# about less than the primary is a trap on the one day it is used.
rep("04_demo/web/index.html",
'''function isInjected(r){
  const f=(r&&r.eo_contact&&r.eo_contact.frame_ref)||'';
  if(String(f).startsWith('injected://')) return true;
  return ((r&&r.frame_refs)||[]).some(x=>String(x).startsWith('injected://'));
}''',
'''function isInjected(r){
  const f=(r&&r.eo_contact&&r.eo_contact.frame_ref)||'';
  if(String(f).startsWith('injected://')) return true;
  return ((r&&r.frame_refs)||[]).some(x=>String(x).startsWith('injected://'));
}
/* SYNTHETIC PROVENANCE — a separate class from an injection; see the note in
   03_src/web/index.html. `synthetic://` is stamped on EVERY contact of a replay scene
   by 04_demo/make_synthetic_eo.py: no camera observed any of it. Recognising only
   `injected://` made this chip read "INJECTED —" over a wholly constructed scene. */
function isSynthetic(r){
  const f=(r&&r.eo_contact&&r.eo_contact.frame_ref)||'';
  if(String(f).startsWith('synthetic://')) return true;
  return ((r&&r.frame_refs)||[]).some(x=>String(x).startsWith('synthetic://'));
}''',
    "demo console: isSynthetic")

rep("04_demo/web/index.html",
'''  const recs=DATA.records||[];
  const any=recs.some(isInjected);''',
'''  const recs=DATA.records||[];
  const any=recs.some(isInjected);
  const anySynth=recs.some(isSynthetic);''',
    "demo console: anySynth")

rep("04_demo/web/index.html",
'''  const on=any||!!DATA.injection;
  el.classList.toggle('on',on);
  const scen=(DATA.injection&&DATA.injection.scenario)
    ||(any?injectedScenario(recs.find(isInjected)):'—');
  el.textContent='INJECTED '+(on?scen:'—');''',
'''  const on=any||anySynth||!!DATA.injection;
  el.classList.toggle('on',on);
  /* An em-dash used to mean "nothing injected", which reads as "these are
     observations". It now appears only when the scene really is camera-observed. */
  const scen=(DATA.injection&&DATA.injection.scenario)
    ||(any?injectedScenario(recs.find(isInjected))
         :anySynth?'SYNTHETIC EO — no camera observed this scene'
         :'none — contacts are camera-observed');
  el.textContent='PROVENANCE '+scen;''',
    "demo console: provenance chip")

rep("04_demo/web/index.html",
'''      (isInjected(r)?`<span class="tag inj">INJECTED · ${injectedScenario(r)}</span> `:'')+''',
'''      (isInjected(r)?`<span class="tag inj">INJECTED · ${injectedScenario(r)}</span> `
                    :isSynthetic(r)?`<span class="tag inj">SYNTHETIC · NO CAMERA</span> `:'')+''',
    "demo console: row badge")

rep("04_demo/web/index.html",
'''  h+=`<h2 style="margin-top:12px">Limitations</h2>`+
     (isInjected(r)''',
'''  h+=`<h2 style="margin-top:12px">Limitations</h2>`+
     (!isInjected(r)&&isSynthetic(r)
       ?`<div class="lim" style="border-left-color:var(--unknown);color:var(--unknown);font-weight:700">`+
        `THIS CONTACT WAS NOT OBSERVED BY A CAMERA. It was forward-projected from the AIS `+
        `claim through a declared camera pose and perturbed with a measurement-error model. `+
        `Any rate derived from this scene measures the decision logic against a model of a `+
        `camera, never against the sea.</div>`
       :'')+
     (isInjected(r)''',
    "demo console: synthetic limitation")


# ===================================================== 04_demo/FAILOVER.md  (F-1)
# The P0 it opens with was fixed. consistency.py exports ConsistencyResult (line 201)
# and independent_dimension_count (line 214); verdict.py imports both cleanly. A stale
# blocker at the top of a run sheet is read as current and stops the run sheet being
# used at all.
rep("04_demo/FAILOVER.md",
'''## 0. BLOCKER ABOVE THIS ONE — read first

`03_src/verdict.py:122` imports `ConsistencyResult` and `independent_dimension_count`
from `03_src/consistency.py`. **Neither exists.** Measured:

```
import verdict       -> ImportError    import run_pipeline -> ImportError
import server        -> ImportError    import app          -> ImportError
```

Both consoles are dead, so none of the failover below can be exercised until lane C
reconciles those two modules. Filed P0 in `99_scratch/requests.md`. Everything in this
document is written and unit-verified; it is **not** end-to-end verified, for that reason.''',
'''## 0. BLOCKER ABOVE THIS ONE — CLEARED 2026-08-29

**RESOLVED.** `03_src/consistency.py` now exports `ConsistencyResult` (line 201) and
`independent_dimension_count` (line 214); `03_src/verdict.py:122` imports both. The
four `ImportError`s recorded here are gone.

**What that does and does not license.** The import chain is whole, so the failover
below *can* now be exercised. It has still not been exercised end to end — §5 lists the
two quantities that remain unmeasured and both need the Mac. Do not read a cleared
blocker as a passed test.''',
    "FAILOVER stale P0")


# ============================================================================ apply
fail = []
for rel, edits in P.items():
    p = ROOT / rel
    if not p.exists():
        fail.append(f"{rel}: FILE MISSING"); continue
    s = p.read_text()
    for old, new, label in edits:
        n = s.count(old)
        if n != 1:
            fail.append(f"{rel}: anchor {n}x (need 1) -- {label}"); continue
        s = s.replace(old, new)
    if not fail:
        p.write_text(s)

if fail:
    print("ABORTED — no file written:")
    for f in fail: print("  ", f)
    sys.exit(1)
print("applied:")
for rel, edits in P.items():
    for _, _, label in edits: print(f"   {rel}: {label}")
