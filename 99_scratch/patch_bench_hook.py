"""Append the paint-timing hook to 03_src/web/index.html.

STRICTLY INERT without ?bench=1. Its own <script> block, appended AFTER the console's,
so a fault in it cannot break the page that is about to be demoed: a separate block
has its own parse and its own error, and the console's boot() has already run.
Wraps applyEvent, never replaces it, and returns the original's value unchanged."""
import sys, pathlib

p = pathlib.Path(sys.argv[1]) / "03_src/web/index.html"
s = p.read_text(encoding="utf-8")

if "BENCH PAINT HOOK" in s:
    print("already present; nothing to do"); raise SystemExit(0)

HOOK = r'''
<script>
/* ============================ BENCH PAINT HOOK =============================
   Measures WS-frame-received -> pixels-on-glass, on the BROWSER's own clock, and
   posts the pair to 04_demo/benchmark.py's collector.

   INERT unless the page is opened with ?bench=1. No listener is attached, no
   function is wrapped, nothing is posted. The demo build and the benchmark build
   are the same file, so there is no "did we remember to take the instrument out"
   before going on stage.

   WHY TWO requestAnimationFrames.
   One rAF callback runs BEFORE the frame carrying your DOM writes is composited --
   it means "the browser agreed to paint", not "the pixels are on the glass". The
   second fires on the following frame, i.e. after the first has been presented.
   The claim in the pitch is a VISIBLE re-rank, so the measurement has to be
   visible. This costs about one frame (~16 ms at 60 Hz) and errs slow, which is
   the correct direction for a number being said out loud.

   WHY IT REPORTS ONLY seq AND TWO TIMES.
   It deliberately does NOT read the console's internal state. The benchmark knows
   which contact it injected and which seq came back, so it joins on seq alone.
   That keeps this hook independent of every field name in the page above it --
   a rename up there cannot silently corrupt a measurement down here.
   ========================================================================= */
(function () {
  var q = new URLSearchParams(location.search);
  if (q.get("bench") !== "1") return;

  var collector = q.get("collector") || "http://127.0.0.1:8899/paint";
  var orig = window.applyEvent;
  if (typeof orig !== "function") {
    console.warn("[bench] applyEvent is not global; paint leg cannot be measured. "
               + "The end-to-end number must be reported UNMEASURED, not estimated.");
    return;
  }
  console.info("[bench] paint hook armed ->", collector);

  window.applyEvent = function (ev) {
    var tWs = Date.now();
    var pWs = performance.now();
    var out = orig.apply(this, arguments);
    if (ev && ev.event === "queue_update" && ev.seq !== undefined) {
      var seq = ev.seq;
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          var body = JSON.stringify({
            seq: seq,
            t_ws_epoch_ms: tWs,
            t_paint_epoch_ms: Date.now(),
            ws_to_paint_ms: performance.now() - pWs
          });
          /* fetch with keepalive rather than sendBeacon: sendBeacon gives no way to
             see a failure, and a silently-dropped sample would shorten the measured
             tail without anything saying so. */
          try {
            fetch(collector, {method: "POST", body: body, keepalive: true,
                              headers: {"Content-Type": "application/json"}})
              .catch(function (e) { console.warn("[bench] post failed", e); });
          } catch (e) { console.warn("[bench] post threw", e); }
        });
      });
    }
    return out;
  };
})();
</script>
'''

anchor = "</body>"
n = s.count(anchor)
if n != 1:
    raise SystemExit(f"ABORT: found {n} </body> tags, want 1")
p.write_text(s.replace(anchor, HOOK + "</body>"), encoding="utf-8")
print(f"OK: paint hook appended to {p} (+{len(HOOK)} bytes)")
