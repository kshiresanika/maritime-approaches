#!/usr/bin/env python3
"""
99_scratch/patch_video_source_9.py — PART 6: the console has to KEEP LOOKING.

THE DEFECT THIS CLOSES, found by reading the startup order rather than by seeing it.
  OBSERVED  — initVideo() runs once, on load.
  CLAIMED   — the pane shows the sensor's imagery.
  THE MISMATCH — main.py starts the node and the server together, and the browser opens
              as soon as /health answers. The node needs a second or two to open its
              source, learn a background and publish a first frame. The browser
              therefore asks BEFORE any frame exists, gets "none configured", prints
              NO VIDEO SOURCE CONFIGURED, and never asks again.
  WHY IT MATTERS — on the new default path (--video) the console would tell the
              operator there is no video while a perfectly healthy node publishes
              frames behind it. That is the tool accusing itself of a fault that does
              not exist, which is the precise confusion this project exists to remove.
  THE FIX   — re-check on a slow timer while there is nothing to show, and again a few
              seconds after a stream ends. A node that starts late, dies and comes back,
              or is started by hand halfway through a run now re-attaches the pane on
              its own.
  COST      — one HEAD-sized fetch every 3 s while the pane is empty, and none at all
              once it is showing frames.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
p = ROOT / "03_src/web/index.html"
s = p.read_text(encoding="utf-8")

OLD_SIG = "async function initVideo() {"
NEW_SIG = """let VIDEO_RETRY = null;

/* Re-arm the check. Single-shot timer, cleared first, so a burst of img errors cannot
   stack up a dozen pollers all racing to set img.src. */
function retryVideo(ms) {
  if (VIDEO_RETRY) clearTimeout(VIDEO_RETRY);
  VIDEO_RETRY = setTimeout(() => { VIDEO_RETRY = null; initVideo(); }, ms);
}

async function initVideo() {"""
assert s.count(OLD_SIG) == 1, "anchor: initVideo signature"
s = s.replace(OLD_SIG, NEW_SIG)

OLD_NONE = """      + "server.py --mjpeg-url &lt;multipart feed&gt;</span>";
    return;
  }"""
NEW_NONE = """      + "server.py --mjpeg-url &lt;multipart feed&gt;</span>";
    /* KEEP LOOKING. A node that has not published its first frame yet is
       indistinguishable, at this instant, from no node at all — and on the --video
       path the first of those is the normal case for the first second or two. */
    retryVideo(3000);
    return;
  }"""
assert s.count(OLD_NONE) == 1, "anchor: none-configured branch"
s = s.replace(OLD_NONE, NEW_NONE)

OLD_ERR = """  img.onerror = () => {
    img.classList.add("hidden");
    no.innerHTML = "<strong>VIDEO SOURCE STOPPED</strong>"
      + "The stream was configured (" + src + ") and is not delivering frames. "
      + "Detection boxes continue from the pipeline.";
  };
  img.src = "/stream";"""
NEW_ERR = """  img.onerror = () => {
    img.classList.add("hidden");
    $("videoSync").classList.add("hidden");
    no.innerHTML = "<strong>VIDEO SOURCE STOPPED</strong>"
      + "The stream was configured (" + src + ") and is not delivering frames. "
      + "Detection boxes continue from the pipeline.<br><br>"
      + "<span class='hint'>re-checking every 3 s — a node that comes back "
      + "re-attaches on its own</span>";
    retryVideo(3000);
  };
  /* Cache-bust. Reusing the same URL after a stream ended can be served from the
     browser's copy of the dead response, which looks exactly like a source that is
     still down. */
  img.src = "/stream?t=" + Date.now();"""
assert s.count(OLD_ERR) == 1, "anchor: img.onerror branch"
s = s.replace(OLD_ERR, NEW_ERR)

p.write_text(s, encoding="utf-8")
print("  patched 03_src/web/index.html (3 edits) — the console keeps looking")
