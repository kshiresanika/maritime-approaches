#!/usr/bin/env python3
"""99_scratch/patch_video_source_8.py — PART 5: the console. New flags in the
'no video' hint, and a standing caveat when the imagery is not what produced the
boxes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def patch(rel, pairs):
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    for old, new in pairs:
        n = src.count(old)
        if n != 1:
            raise SystemExit(f"ANCHOR FAILED in {rel}: matched {n}, expected 1\n---\n{old[:240]}\n---")
        src = src.replace(old, new)
    p.write_text(src, encoding="utf-8")
    print(f"  patched {rel} ({len(pairs)} edits)")


patch("03_src/web/index.html", [

# ------------------------------------------------ a place to say what the imagery is
('''        <div id="sensorFoot"></div>''',
 '''        <div id="videoSync" class="hidden"></div>
        <div id="sensorFoot"></div>'''),

# ---------------------------------------------------------------- the hint and caveat
('''  if (src === "none configured") {
    img.classList.add("hidden");
    $("frameEdge").classList.remove("hidden");
    no.innerHTML = "<strong>NO VIDEO SOURCE CONFIGURED</strong>"
      + "The edge sensor posts contacts, it does not serve video. Detection boxes below "
      + "are live and real; the imagery behind them is not available on this run.<br><br>"
      + "<span class='hint'>start with --mjpeg-url &lt;mjpg-streamer url&gt; or --camera 0</span>";
    return;
  }
  no.textContent = "";
  img.classList.remove("hidden");''',

 '''  if (src === "none configured") {
    img.classList.add("hidden");
    $("frameEdge").classList.remove("hidden");
    no.innerHTML = "<strong>NO VIDEO SOURCE CONFIGURED</strong>"
      + "No sensor node is publishing frames and this server was not given a video to "
      + "decode. Detection boxes below are live and real; the imagery behind them is "
      + "not available on this run.<br><br>"
      + "<span class='hint'>python 03_src/main.py --video &lt;file|url&gt; --loop"
      + "&nbsp;&nbsp;·&nbsp;&nbsp;--camera 0&nbsp;&nbsp;·&nbsp;&nbsp;"
      + "server.py --mjpeg-url &lt;multipart feed&gt;</span>";
    return;
  }

  /* WHAT THE IMAGERY IS, STATED ON THE PICTURE ITSELF.
     The boxes are an overlay: nothing in the geometry forces them to belong to the
     frame underneath. When they do — a node published the frame it detected on — the
     caveat stays out of the way. When they do not, the screen has to say so, because
     an operator reading a box as a position on THIS frame is the single most damaging
     thing this pane could cause them to believe. /health computes the sentence; the
     console does not second-guess it. */
  const sync = health?.source?.video_sync || "";
  const vs = $("videoSync");
  if (sync && !sync.startsWith("frame-synchronised")) {
    vs.textContent = sync;
    vs.classList.remove("hidden");
    vs.classList.toggle("warn", sync.startsWith("UNRELATED"));
  } else {
    vs.classList.add("hidden");
  }

  no.textContent = "";
  img.classList.remove("hidden");'''),

# ------------------------------------------------------------------------- styling
('''#sensorFoot{''',
 '''#videoSync{
  /* Sits under the badges, above the footer. Amber for "not guaranteed", red for
     "unrelated" — the two are different claims and must not look the same. Contrast
     is checked against --panel at AAA like every other text colour on this console. */
  position:absolute; left:10px; right:10px; top:44px; z-index:5;
  font:600 12px/1.45 ui-monospace,Menlo,monospace; letter-spacing:.02em;
  padding:7px 10px; border-radius:6px;
  background:rgba(0,0,0,.82); color:#ffd166; border:1px solid #ffd166;
}
#videoSync.warn{ color:#ff8f8f; border-color:#ff8f8f; }
#sensorFoot{'''),
])
print("PART 5 done — console explains the imagery.")
