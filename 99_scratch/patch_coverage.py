"""Make ais_coverage_confidence an EXPLICIT, STATED input instead of a silent default.

WHY. failure_modes.md, generated from a real run, reports 8 of 8 verdicts deferred with
`low_confidence`, and 4 of 8 additionally carrying `sparse_ais_coverage`. Its own §6.2
diagnosed the cause: run_scene() never passes an ais_coverage_confidence, so verdict.py
falls back to DEFAULT_AIS_COVERAGE_CONFIDENCE = 0.70, which sits BELOW
SPARSE_COVERAGE_THRESHOLD = 0.75. Every dark verdict therefore defers on coverage
grounds automatically, in every scene, forever -- and that is a property of an unstated
default, not of the data.

That matters beyond tidiness: criterion 2 is the ranked queue, and a queue in which
every entry is deferred recommends nothing. The prioritiser is being handed a scene
where the defer flag is constant, so it cannot separate anything by it.

The default is NOT changed here. Changing a threshold to make a demo look better is the
exact move this project forbids. What changes is that the number becomes an argument
with a stated basis, so the run can declare what it actually knows about coverage and
failure_modes.md can print which value was used -- which it already does."""
import sys, pathlib

ROOT = pathlib.Path(sys.argv[1])
P: dict[str, list[tuple[str, str, str]]] = {}
def rep(f, old, new, label): P.setdefault(f, []).append((old, new, label))

# ------------------------------------------------------------ run_pipeline.run_scene
rep("04_demo/run_pipeline.py",
'''def run_scene(
    tracks: list[AisTrack],
    contacts: list[EoContact],
    pose: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:''',
'''def run_scene(
    tracks: list[AisTrack],
    contacts: list[EoContact],
    pose: dict[str, Any],
    *,
    now: datetime | None = None,
    ais_coverage_confidence: float | None = None,
) -> dict[str, Any]:''', "run_scene signature")

rep("04_demo/run_pipeline.py",
'''    decided = verdict_mod.decide_all(associations, results_by_assoc, decided_at_utc=now)
    verdicts = [v for v, _explanation in decided]''',
'''    # HOW CONFIDENT ARE WE THAT AIS COVERAGE HERE IS COMPLETE? It gates the DARK path:
    # below verdict.SPARSE_COVERAGE_THRESHOLD every dark verdict raises
    # `sparse_ais_coverage` and defers, because an AIS gap caused by a receiver hole is
    # indistinguishable from one caused by a switched-off transponder, and calling the
    # second when it was the first is the accusation this project must not make.
    #
    # Passing None keeps verdict.py's own conservative default (0.70), which is BELOW
    # the threshold -- so a caller that says nothing gets universal deferral on the dark
    # path. That is the right default for real Baltic data and the wrong one for a
    # synthetic scene whose coverage is complete by construction, and until now there
    # was no way to say which you had. There is now, and the value travels into the
    # evidence record so a reader can see what was assumed.
    #
    # MEASURE IT, do not pick it: ais_trajectory.detect_gaps() over a slice bounded by
    # the camera's own field of view, with explained_by_area_exit and
    # explained_by_sparse_coverage both reported. A number chosen to make a demo pass
    # is worse than the conservative default it replaced.
    kw: dict[str, Any] = {"decided_at_utc": now}
    if ais_coverage_confidence is not None:
        kw["ais_coverage_confidence"] = float(ais_coverage_confidence)
    decided = verdict_mod.decide_all(associations, results_by_assoc, **kw)
    verdicts = [v for v, _explanation in decided]''', "run_scene decide_all")

rep("04_demo/run_pipeline.py",
'''    return {
        "pose": pose,
        "counts": {''',
'''    return {
        "pose": pose,
        # Stated on every result so the console, the case file and failure_modes.md all
        # report the SAME assumption. An assumption that lives only in a default is one
        # nobody can audit.
        "ais_coverage_confidence": (
            float(ais_coverage_confidence) if ais_coverage_confidence is not None
            else verdict_mod.DEFAULT_AIS_COVERAGE_CONFIDENCE),
        "ais_coverage_confidence_basis": (
            "explicitly supplied by the caller" if ais_coverage_confidence is not None
            else "verdict.DEFAULT_AIS_COVERAGE_CONFIDENCE — conservative default, "
                 "BELOW SPARSE_COVERAGE_THRESHOLD, so every dark verdict defers"),
        "counts": {''', "run_scene return")

# ------------------------------------------------------------------- app.State
rep("04_demo/app.py",
'''        self.mode = "replay"
        self.last_update: str = ""''',
'''        self.mode = "replay"
        self.last_update: str = ""
        # None = use verdict.py's conservative default. Set it only from something
        # measured for the sector; see run_pipeline.run_scene for why.
        self.ais_coverage_confidence: float | None = None''', "app.State field")

rep("04_demo/app.py",
'''        result = run_pipeline.run_scene(
            self.scene["tracks"], contacts, self.scene["manifest"]["pose"])''',
'''        result = run_pipeline.run_scene(
            self.scene["tracks"], contacts, self.scene["manifest"]["pose"],
            ais_coverage_confidence=self.ais_coverage_confidence)''',
    "app.State recompute")

# --------------------------------------------------------------------- server.py
# An explicit factory argument, matching create_app's own stated principle: "A factory
# rather than a module-level app = FastAPI() so that every setting above is an explicit
# argument. A module-level app would have to read globals or the environment, and a demo
# whose behaviour depends on an environment variable someone exported two hours ago is a
# demo with an invisible input."
rep("03_src/server.py",
'''    initial_source: str = DEFAULT_SOURCE,
    startup_tasks: "Sequence[Any]" = (),
):''',
'''    initial_source: str = DEFAULT_SOURCE,
    startup_tasks: "Sequence[Any]" = (),
    ais_coverage_confidence: float | None = None,
):''', "create_app signature")

rep("03_src/server.py",
'''    backend = ConsoleBackend(scene_dir, audit_path=audit_path,
                             node_stale_after_s=node_stale_after_s,
                             allow_real_identities=allow_real_identities,
                             initial_source=initial_source)''',
'''    backend = ConsoleBackend(scene_dir, audit_path=audit_path,
                             node_stale_after_s=node_stale_after_s,
                             allow_real_identities=allow_real_identities,
                             initial_source=initial_source)
    # Set BEFORE the lifespan's startup recompute, so the first picture a browser sees
    # was computed under the same coverage assumption as every later one.
    if ais_coverage_confidence is not None:
        backend._demo.ais_coverage_confidence = float(ais_coverage_confidence)''',
    "create_app wire")

# ---------------------------------------------------------------------- main.py
rep("03_src/main.py",
'''    p.add_argument("--no-browser", action="store_true")''',
'''    p.add_argument("--ais-coverage-confidence", type=float, default=None,
                   help="0-1. How complete is AIS coverage in this sector? Gates the "
                        "DARK path: below 0.75 every dark verdict defers, because a "
                        "receiver hole and a switched-off transponder look identical. "
                        "Omitted = verdict.py's conservative 0.70, which is BELOW that "
                        "threshold, so EVERY dark verdict defers. Correct for real "
                        "Baltic data; wrong for a synthetic scene whose coverage is "
                        "complete by construction. MEASURE it for a real sector "
                        "(ais_trajectory.detect_gaps) rather than picking one.")
    p.add_argument("--no-browser", action="store_true")''', "main.py flag")

rep("03_src/main.py",
'''        allow_real_identities=args.allow_real_identities,
        node_stale_after_s=8.0,
        initial_source="RECORDED",
        startup_tasks=startup_tasks,
    )''',
'''        allow_real_identities=args.allow_real_identities,
        node_stale_after_s=8.0,
        initial_source="RECORDED",
        startup_tasks=startup_tasks,
        # Handed to the factory rather than set on the backend afterwards, because the
        # lifespan runs one recompute at startup: set it later and the FIRST picture a
        # browser sees was computed under a different assumption from every later one,
        # with nothing on screen saying so.
        ais_coverage_confidence=args.ais_coverage_confidence,
    )''', "main.py wire")

rep("03_src/main.py",
'''    print(f"  identities   " + ("REAL PERMITTED on /evidence?allow_real=1"''',
'''    print(f"  AIS coverage " + (
        f"{args.ais_coverage_confidence:.2f} (stated by you)"
        if args.ais_coverage_confidence is not None
        else "0.70 default — BELOW the 0.75 sparse threshold, so every DARK verdict "
             "will defer. Pass --ais-coverage-confidence if you know better."))
    print(f"  identities   " + ("REAL PERMITTED on /evidence?allow_real=1"''',
    "main.py banner")

total = 0
for rel, edits in P.items():
    p = ROOT / rel
    s = p.read_text()
    for old, new, label in edits:
        n = s.count(old)
        if n != 1:
            raise SystemExit(f"ABORT {rel}: '{label}' matched {n} times, want 1")
        s = s.replace(old, new)
    p.write_text(s)
    total += len(edits)
    print(f"  {rel}: {len(edits)} edits")
print(f"OK: {total} edits")
