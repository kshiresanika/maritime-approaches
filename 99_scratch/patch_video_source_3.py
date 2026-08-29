#!/usr/bin/env python3
"""
99_scratch/patch_video_source_3.py — PART 2b: wire the decoder and the relay into the
HTTP surface, and make /health tell the console the truth about which one is running.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def patch(rel: str, pairs: list[tuple[str, str]]) -> None:
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    for old, new in pairs:
        n = src.count(old)
        if n != 1:
            raise SystemExit(f"ANCHOR FAILED in {rel}: matched {n} times, expected 1\n"
                             f"---\n{old[:240]}\n---")
        src = src.replace(old, new)
    p.write_text(src, encoding="utf-8")
    print(f"  patched {rel} ({len(pairs)} edits)")


patch("03_src/server.py", [

# ------------------------------- 1. resolve the source and build the relay
("""    from starlette.websockets import WebSocketDisconnect

    backend = ConsoleBackend(scene_dir, audit_path=audit_path,""",
 '''    from starlette.websockets import WebSocketDisconnect

    # ONE video source value, resolved once, here.
    #
    # `camera_index` predates video sources and callers still pass it. Rather than
    # carry two settings that can disagree — and they would, on exactly the run where
    # someone passed both — it is folded into `video_source` at the boundary. After
    # this line there is a single value and a single spelling for "what /stream
    # decodes when no node is publishing frames".
    video_source = parse_video_source(
        video_source if video_source is not None else camera_index)

    # The relay is always constructed, never conditionally. A node may start publishing
    # at any point in the run; building the relay only when it was configured in
    # advance would mean the one thing an operator does mid-demo (start a sensor)
    # silently could not take effect.
    relay = _FrameRelay(stale_after_s=frame_stale_after_s)

    def _video_description() -> str:
        """
        What /health tells the console. The exact string "none configured" is a
        CONTRACT: index.html's initVideo() compares against it to decide whether a 503
        on /stream is a configuration statement or a fault, and reports the wrong one
        if this drifts.
        """
        if relay.fresh():
            st = relay.status()
            return f"relayed from node {st['node_id'] or 'unknown'}"
        if mjpeg_url:
            return "passthrough"
        if video_source is not None:
            return (f"decoding camera {video_source}" if isinstance(video_source, int)
                    else f"decoding {video_source}")
        return "none configured"

    def _video_sync() -> str:
        """
        Whether the imagery under the detection boxes is the imagery the boxes were
        computed from. Published because the console overlays one on the other, and an
        operator is entitled to know when the two are only approximately the same
        picture. See _FrameRelay for why this distinction is not pedantry.
        """
        if relay.fresh():
            return "frame-synchronised: the node published the frame it detected on"
        if video_source is not None or mjpeg_url:
            return ("reference imagery: decoded independently of the detector, so the "
                    "boxes are NOT guaranteed to belong to the frame beneath them")
        return "no imagery"

    backend = ConsoleBackend(scene_dir, audit_path=audit_path,'''),

# --------------------------------------------- 2. /stream, and the new ingest route
('''    # ------------------------------------------------------------ GET /stream
    @app.get("/stream")
    async def stream():
        if mjpeg_url:
            return StreamingResponse(
                _mjpeg_passthrough(mjpeg_url),
                media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        if camera_index is not None:
            return StreamingResponse(
                _mjpeg_local_camera(camera_index),
                media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        return PlainTextResponse(
            "No video source configured. pi_sensor.py posts contacts, it does not "
            "serve video. Start the server with --mjpeg-url <url of an "
            "mjpg-streamer/motion feed> or --camera <index>.",
            status_code=503)''',

 '''    # ---------------------------------------------------- POST /ingest/frame
    @app.post("/ingest/frame")
    async def ingest_frame(request: Request,
                           node_id: str | None = Query(default=None),
                           frame_ref: str | None = Query(default=None)):
        """
        The sensor node publishes the JPEG it computed its contacts from.

        RAW BYTES, NOT MULTIPART, NOT BASE64 IN JSON. The node is deliberately a
        dependency-light thing posting with urllib; base64 would inflate every frame by
        a third for no gain, and a multipart encoder is a library the node does not
        have. Content-Type is image/jpeg and the body is the file.

        Bounded by construction: the relay holds exactly ONE frame. There is no queue to
        grow, so a fast node cannot exhaust memory on a slow console, and a viewer that
        falls behind sees the newest frame rather than an ever-older one.
        """
        data = await request.body()
        if not data:
            raise HTTPException(400, "empty body: POST the JPEG bytes as the body")
        # Cheap sanity check rather than a decode. The server has no opinion about the
        # image, but a node posting JSON to this route by mistake should be told so
        # here, not by a browser rendering a broken image on stage.
        if not data.startswith(b"\\xff\\xd8"):
            raise HTTPException(400, "body is not a JPEG (no SOI marker). This route "
                                     "takes raw image/jpeg bytes.")
        seq = relay.publish(data, node_id=node_id, frame_ref=frame_ref)
        return {"ok": True, "frame_seq": seq, "bytes": len(data)}

    # ------------------------------------------------------------ GET /stream
    @app.get("/stream")
    async def stream():
        """
        PRECEDENCE, AND THE ORDER IS THE ARGUMENT.

        1. RELAY, whenever a node is currently publishing. It is the only source whose
           imagery is guaranteed to be the imagery the detection boxes were computed
           from, so it wins over anything this server could decode for itself.
        2. --mjpeg-url passthrough.
        3. --video / --camera decoded here.
        4. 503, stating which flag would fix it.

        The precedence is evaluated per REQUEST, not once at startup: a node started
        halfway through a run takes over the pane on the browser's next reconnect, and
        /health says which source is live at any moment.
        """
        if relay.fresh():
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
        return PlainTextResponse(
            "No video source configured, and no sensor node is publishing frames.\\n"
            "Give the shore station something to show, in order of preference:\\n"
            "  --video <file|url>   decode a clip or a network stream here, and run\\n"
            "                       the sensor node on the SAME source so the boxes\\n"
            "                       and the imagery agree;\\n"
            "  --camera <index>     a lens on this machine;\\n"
            "  --mjpeg-url <url>    relay an existing multipart/x-mixed-replace feed.\\n"
            "A node started with --publish-frames takes the pane over automatically.",
            status_code=503)'''),

# ---------------------------------------------------- 3. /health video description
('''                "video": ("passthrough" if mjpeg_url else
                          f"local camera {camera_index}" if camera_index is not None
                          else "none configured"),''',
 '''                "video": _video_description(),
                "video_sync": _video_sync(),
                "frame_relay": relay.status(),'''),
])
print("PART 2b done — routes wired.")
