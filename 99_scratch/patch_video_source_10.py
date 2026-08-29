#!/usr/bin/env python3
"""
99_scratch/patch_video_source_10.py — PART 7: the video must not depend on the node.

THE DEFECT, OBSERVED ON THE FIRST REAL RUN (2026-08-29 18:27).
  OBSERVED  — `main.py --video <clip>` opened the console with an empty pane reading
              NO VIDEO SOURCE CONFIGURED, while the terminal showed
              "[node] hfov_deg is 0 in the pose file".
  CLAIMED   — passing --video puts that video on the console.
  THE MISMATCH — main.py deliberately did NOT hand the video to create_app, on the
              argument that one decoder is better than two. That argument is right, but
              it made the picture depend ENTIRELY on the node: the node refused to start
              over an unrelated pose problem, nothing published a frame, and the server
              had nothing to fall back on. One correct decision (single decoder) became
              a single point of failure for a second, independent thing (the imagery).
  WHY IT MATTERS — the operator was told there was no video source when they had
              explicitly configured one. The tool misreported its own configuration,
              and the real fault (a pose file full of zeros) was one line further up
              the terminal where nobody was looking.
  THE FIX   — the server ALSO gets the video, and /stream is now ONE generator that
              serves the relay when a node is publishing and decodes for itself when
              one is not, SWITCHING BETWEEN THEM MID-STREAM. The single-decoder
              guarantee is preserved where it matters — the moment a node publishes,
              the decode stops and the relay takes over inside the same HTTP response,
              so no browser is left watching an unsynchronised copy.
  CONFIDENCE — high. The switch is driven by relay.fresh(), the same predicate /health
              reports, so what the console says and what /stream serves cannot disagree.
"""
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


patch("03_src/server.py", [

# ---- 1. the decoder learns to stand down --------------------------------------
('''def _mjpeg_decode(source: "str | int", *, quality: int = 80,
                  loop: bool = True, live_timeout_s: float = 10.0) -> Iterator[bytes]:''',
 '''def _mjpeg_decode(source: "str | int", *, quality: int = 80,
                  loop: bool = True, live_timeout_s: float = 10.0,
                  yield_to=None) -> Iterator[bytes]:'''),

('''    replayable = _is_replayable_file(source)
    cap = cv2.VideoCapture(source)''',
 '''    # yield_to() is checked once per frame. When it goes true a better source has
    # appeared — in practice a sensor node that has started publishing the frames it
    # detected on — and this generator RETURNS so the caller can switch to it inside the
    # same HTTP response. Without the hand-off the browser would sit on an
    # unsynchronised decode for the rest of the session and only pick up the relay if
    # someone reloaded the page.
    replayable = _is_replayable_file(source)
    cap = cv2.VideoCapture(source)'''),

('''            last_frame_at = time.monotonic()
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), quality])''',
 '''            last_frame_at = time.monotonic()
            if yield_to is not None and yield_to():
                return
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), quality])'''),

# ---- 2. /stream becomes one generator that can switch --------------------------
('''        if relay.fresh():
            return StreamingResponse(
                relay.frames(),
                media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        if mjpeg_url:
            return StreamingResponse(
                _mjpeg_passthrough(mjpeg_url),
                media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        if video_source is not None:
            return StreamingResponse(
                _mjpeg_decode(video_source),
                media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        return PlainTextResponse(''',

 '''        def _frames() -> Iterator[bytes]:
            """
            One response, whichever source is currently best.

            The precedence is re-evaluated CONTINUOUSLY rather than once at connect
            time. A node that starts thirty seconds into a run takes the pane over
            without the operator reloading anything; a node that dies hands the pane
            back to the server's own decode instead of leaving a dead pane. Both
            transitions happen inside the same <img>, which is the only place the
            operator is looking.
            """
            empty_rounds = 0
            while True:
                if relay.fresh():
                    empty_rounds = 0
                    yield from relay.frames()      # returns when the node goes quiet
                    continue                       # ...then fall through and decode
                if mjpeg_url:
                    yield from _mjpeg_passthrough(mjpeg_url)
                    return                         # a passthrough that ends, ends
                if video_source is None:
                    return
                served = 0
                for part in _mjpeg_decode(video_source, yield_to=relay.fresh):
                    served += 1
                    yield part
                if served:
                    empty_rounds = 0
                    continue
                # Produced nothing: the source is unopenable, not merely quiet. Two
                # rounds, then stop, so a bad path cannot become a hot reopen loop that
                # burns a core for the rest of the demo.
                empty_rounds += 1
                if empty_rounds >= 2:
                    print(f"[server] /stream: {video_source!r} produced no frames "
                          f"twice — giving up rather than reopening in a loop.",
                          file=sys.stderr)
                    return
                time.sleep(0.5)

        if relay.fresh() or mjpeg_url or video_source is not None:
            return StreamingResponse(
                _frames(),
                media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        return PlainTextResponse('''),
])

# ------------------------------------------------------------------------ main.py
patch("03_src/main.py", [
('''        mjpeg_url=None,
        camera_index=None,          # /stream stays honest; the node owns the camera''',
 '''        mjpeg_url=None,
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
        video_source=(args.video if args.video is not None else None)'''),
])
print("PART 7 done — the picture no longer depends on the node.")
