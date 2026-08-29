"""Two ADDITIVE hooks on server.py so 03_src/main.py can drive an AIS replay without
reaching into lane E's internals. Nothing existing changes behaviour: startup_tasks
defaults to (), and replace_tracks is new. Backup + disclosure per FILE OWNERSHIP."""
import sys, pathlib
p = pathlib.Path(sys.argv[1]) / "03_src/server.py"
s = p.read_text()
E = []

# ---- hook 1: a public way to swap the AIS picture --------------------------------
E.append((
'''    async def recompute(self, *, reason: str) -> None:''',
'''    async def replace_tracks(self, tracks: list, *, reason: str) -> None:
        """Swap the AIS picture, then re-run the pipeline. main.py's replay hook.

        WHY THIS EXISTS: load_scene() reads ais_tracks.jsonl ONCE at construction, so
        the AIS side of this server was frozen at startup. Contacts could arrive over
        time (POST /ingest/contacts) but claims could not, which means the one thing a
        replay is for -- watching a claim go stale, a vessel go quiet, a track loiter --
        could not happen. criterion 2 ranks BEHAVIOUR, and behaviour needs a clock.

        WHY IT IS A METHOD AND NOT A ROUTE: the replay driver runs in this process, in
        this event loop. An HTTP route would serialise every AisTrack to JSON and parse
        it back for no reason, and would open a way to inject claims from off-machine,
        which is a thing a shore station should not accept.

        The lock is the same one every other mutation takes, so a replay tick cannot
        interleave with an ingest batch and leave the pipeline reading half of each.
        """
        async with self._lock:
            if self._demo.scene is not None:
                self._demo.scene["tracks"] = tracks
        await self.recompute(reason=reason)

    async def recompute(self, *, reason: str) -> None:'''))

# ---- hook 2: let a caller start its own background tasks in the lifespan ---------
E.append((
'''    allow_real_identities: bool,
    node_stale_after_s: float,
    initial_source: str = DEFAULT_SOURCE,
):''',
'''    allow_real_identities: bool,
    node_stale_after_s: float,
    initial_source: str = DEFAULT_SOURCE,
    startup_tasks: "Sequence[Any]" = (),
):'''))

E.append((
'''        await backend.recompute(reason="startup")
        task = asyncio.create_task(_watchdog(backend))
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass''',
'''        await backend.recompute(reason="startup")
        tasks = [asyncio.create_task(_watchdog(backend))]
        # Caller-supplied background work (main.py's AIS replay driver). Started HERE,
        # inside the running loop, rather than before uvicorn: a task created on a
        # different loop never runs and fails silently, which on stage looks exactly
        # like an AIS feed that has no data.
        for factory in startup_tasks:
            tasks.append(asyncio.create_task(factory(backend)))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception as exc:      # noqa: BLE001
                    print(f"[server] task {t!r}: {type(exc).__name__}: {exc}",
                          file=sys.stderr)'''))

for old, new in E:
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"ABORT: anchor matched {n} times:\n{old[:90]}")
    s = s.replace(old, new)

# `Sequence` / `Any` must be importable for the annotation (it is a string, but keep
# the import honest rather than relying on it never being evaluated).
if "from typing import" in s and "Sequence" not in s.split("from typing import")[1][:120]:
    head, rest = s.split("from typing import", 1)
    line, tail = rest.split("\n", 1)
    if "Sequence" not in line:
        s = head + "from typing import" + line.rstrip() + ", Sequence\n" + tail

p.write_text(s)
print(f"OK: {len(E)} additive hooks applied to server.py")
