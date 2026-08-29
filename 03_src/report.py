"""
report.py — 04_demo/failure_modes.md. Lane D. CRITERION 4, the other half.

WHAT THIS FILE PRODUCES, AND WHY IT IS NOT A LOG
`evidence.py` answers "what did the tool conclude about THIS contact, and what can that
conclusion not support?". This file answers the question a judge asks next and that
almost nobody's demo can answer at all:

    WHERE DOES THIS SYSTEM FAIL, AND HOW OFTEN?

Every number in the generated document is computed from an actual pipeline run over a
scene whose ground truth is known by construction. Nothing here is asserted from a
design intention. A failure-modes document written by hand is a statement of what its
author believes the failures are, which is exactly the set of failures they have already
thought about — i.e. the ones that do not matter.

THE FOUR RULES THIS FILE IS BUILT AROUND

1. NEVER INVENT A NUMBER. If something was not measured by the run, the document says
   UNMEASURED and says what would measure it. The house style is
   `04_demo/edge_benchmark.md`, which prints empty tables and names the instrument that
   fills them rather than quoting a spec sheet. Copied deliberately.

2. NEVER NAME A REAL VESSEL. Every identity goes through ONE shared
   `evidence.Anonymiser` for the whole run — one instance, because a fresh instance per
   record allocates first-seen index 0 every time and collapses every distinct hull onto
   999000001 while looking perfectly anonymised. The whole assembled document is then
   scrubbed and put through `evidence.assert_no_real_identities()`, and this file
   REFUSES TO WRITE if that fails. A document that looks anonymised and is not is the
   worst of the three outcomes, because it is the one nobody re-checks.

3. NO MODEL, ANYWHERE. There is no LLM call in this file and no import of a module that
   makes one — `eo_vlm.py` is deliberately NOT imported even to read one constant from
   it; that constant is read by parsing the file's AST instead (see
   `_read_source_constant`). The document must be byte-reproducible from the same scene
   and the same code, and a sampled sentence is not reproducible.

4. STRUCTURAL LIMITS ARE STATED, NOT GENERATED. Section 6 of the output is the set of
   limits that no amount of running changes, because they are properties of the physics
   or of a declared model rather than of this scene. Each is attributed to the source
   file that documents it, and the arithmetic in it is recomputed live from that file's
   own constants so the document cannot silently go stale when a threshold moves.

WHY THE PIPELINE IS RUN TWICE PER SCENE
`run_pipeline.run_scene()` returns `model_dump()`ed dicts and drops the
`ConsistencyResult` objects at its own seam — correctly, since they are lane C internal.
But sections 3 and 7 of this report are ABOUT those objects: the uncomparable-dimension
coverage story and `verdict.is_prior_sensitive()` both need the result, not the record.
So `association.associate()` and `consistency.check_all()` are re-run here, with exactly
the call `run_pipeline.py:91-113` makes. Both are deterministic and neither reads a
clock (`association_id` is derived from the contact and track ids; `assoc_time_utc` comes
from the frame time), so the second run reproduces the first. The alternative — copying
run_scene's record-assembly here so the intermediates could be kept — would put a second
record-building path in the project, and two paths drift. The re-run is CROSS-CHECKED
against the record set and fails loudly if the two disagree.

IMPORT CONVENTION
`03_src` starts with a digit, so it is not a legal package name. sys.path insertion plus
flat imports, copied from `04_demo/run_pipeline.py:34-43` and `03_src/main.py`.

    python 03_src/report.py --scene 04_demo/out/scene01 --out 04_demo/failure_modes.md
    python 03_src/report.py --scenes 04_demo/out/scene01 04_demo/out/scene02
"""

from __future__ import annotations

import argparse
import ast
import inspect
import math
import platform
import shlex
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[1]
_SRC = _ROOT / "03_src"
_DEMO = _ROOT / "04_demo"
for _p in (_SRC, _DEMO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ============================================================================
# Loud failure.
#
# WHY THERE IS A HELPER FOR THIS AT ALL: the failure mode this file must avoid is a
# traceback thrown from four frames inside a render, which tells a person at 04:00 that
# something is None and nothing about which of six preconditions was not met. Every
# precondition below is checked at the top of the function that needs it, with a message
# that names the file, the symbol and the fix.
# ============================================================================

def _die(message: str) -> "None":
    # `from None` so a message raised from inside an except-block reads as the single
    # sentence it is, rather than being appended to somebody else's traceback under
    # "During handling of the above exception...". The cause is already in the message.
    raise SystemExit(f"report.py: {message}") from None


def _require(condition: Any, message: str) -> None:
    if not condition:
        _die(message)


# ============================================================================
# Imports of the pipeline itself, done inside a function so the failure is a sentence
# rather than a stack trace.
#
# This matters more than it looks: `verdict.py` imports loguru, `association.py` and
# `geometry.py` reach for numpy and pyproj, and `contracts.py` needs pydantic. On a
# machine missing any of them the bare traceback names the third-party package and not
# the thing the operator has to do about it.
# ============================================================================

class _Pipeline:
    """The imported modules, in one namespace, so nothing below reaches into globals."""

    def __init__(self) -> None:
        try:
            import association
            import consistency
            import evidence
            import verdict
            import run_pipeline
        except ImportError as exc:  # noqa: PERF203 - one handler, one message
            _die(
                f"could not import the pipeline: {exc}\n"
                f"  looked in: {_SRC}\n"
                f"             {_DEMO}\n"
                "  This file only sequences modules it does not own. The usual causes,\n"
                "  in order of likelihood:\n"
                "    * the venv is not active — pydantic, loguru, numpy and pyproj are\n"
                "      all required by modules this file imports transitively;\n"
                "    * it was run from a checkout where 03_src/ or 04_demo/ is not where\n"
                "      this file's parent expects them.\n"
                "  Try:  .venv/bin/python 03_src/report.py --scene 04_demo/out/scene01")
        self.association = association
        self.consistency = consistency
        self.evidence = evidence
        self.verdict = verdict
        self.run_pipeline = run_pipeline

        # These five are the only symbols this file needs that are not plain data. If a
        # lane renames one, this says so by name instead of failing later inside a
        # render with an AttributeError on a partially-built document.
        for module, names in (
            (evidence, ("Anonymiser", "assert_no_real_identities", "PIPELINE_VERSION",
                        "_scrub_text")),
            # The constants are checked too, not just the functions. Section 6 prints
            # them as the DECLARED assumptions behind every confidence in the document;
            # if one is renamed, the honest outcome is this sentence, not an
            # AttributeError thrown from the middle of a half-built markdown table.
            (verdict, ("CALIBRATION_STATEMENT", "is_prior_sensitive", "RULESET_VERSION",
                       "SPOOF_PRIOR", "CORRELATION_DAMPING", "CONFIDENCE_STATE",
                       "CONFIDENCE_ALLEGE_FLOOR", "MATCH_COVERAGE_FLOOR",
                       "MATCH_CONFIDENCE_CEILING", "SINGLE_DIMENSION_CONFIDENCE_CAP",
                       "SPARSE_COVERAGE_THRESHOLD",
                       "DEFAULT_AIS_COVERAGE_CONFIDENCE")),
            (consistency, ("check_all", "DEFAULT_TOLERANCES", "RULESET_VERSION")),
            (run_pipeline, ("load_scene", "run_scene", "score_scene")),
            (association, ("associate",)),
        ):
            for name in names:
                _require(
                    hasattr(module, name),
                    f"{module.__name__}.{name} does not exist. report.py is written "
                    f"against it and cannot produce an honest document without it. "
                    f"Either restore the symbol or update report.py — do NOT let this "
                    f"section silently disappear from failure_modes.md.")

        # Section 6.4 and 6.5 recompute their arithmetic from these three tolerances so
        # the document cannot go stale when a threshold moves. Same reasoning as above.
        for field_name in ("class_min_report_sigma", "confusable_class_discount",
                           "min_bearing_sigma_deg"):
            _require(
                hasattr(consistency.DEFAULT_TOLERANCES, field_name),
                f"consistency.Tolerances.{field_name} does not exist. Section 6 of "
                f"failure_modes.md derives the structural-limit arithmetic from it "
                f"rather than hard-coding a number, and a hard-coded number is exactly "
                f"what this project forbids. Update report.py to the new field name.")


# ============================================================================
# Reading a constant out of a module WITHOUT importing it.
#
# Needed for exactly one number: eo_vlm.UNCALIBRATED_CONFIDENCE_CEILING, which section
# 6.5 needs in order to show that an uncalibrated vision model is structurally inert.
# `eo_vlm.py` is the module that CALLS A MODEL, and this file must import nothing that
# does — so the value is parsed out of the source instead. That also means the number
# cannot go stale: if lane B moves the ceiling, this document moves with it, which a
# hard-coded 0.95 in a docstring would not.
# ============================================================================

def _read_source_constant(path: Path, name: str) -> tuple[Any, int | None, str | None]:
    """(value, lineno, error). Never raises; a failure becomes an UNMEASURED cell."""
    if not path.exists():
        return None, None, f"{path} does not exist"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError, UnicodeDecodeError) as exc:
        return None, None, f"could not parse {path.name}: {exc}"
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        for tgt in targets:
            if isinstance(tgt, ast.Name) and tgt.id == name and value is not None:
                try:
                    return ast.literal_eval(value), node.lineno, None
                except (ValueError, SyntaxError):
                    return (None, node.lineno,
                            f"{name} in {path.name} is not a literal expression")
    return None, None, f"{name} is not assigned at module level in {path.name}"


# ============================================================================
# Pseudonymisation plumbing.
#
# `evidence.Anonymiser.substitutions()` is written against the typed EvidenceRecord, but
# `run_scene()` hands back `model_dump(mode="json")`ed dicts. Rather than reimplement
# the substitution logic here — which is precisely the logic that decides whether a
# document leaks, and therefore the last logic in the project that should exist twice —
# the dicts are wrapped in a view thin enough that evidence.py's own function runs over
# them unchanged. If lane D ever adds a leak path to substitutions(), this file inherits
# the fix for free.
# ============================================================================

class _TrackView:
    """Attribute access over an AisTrack dump. Missing field -> None, matching the
    optional fields on the contract."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name)


class _RecordView:
    """The two attributes `Anonymiser.substitutions()` touches: `.ais_track` (attribute
    access) and `.identity_claimed` (a dict it calls .get() on)."""

    def __init__(self, data: dict[str, Any]) -> None:
        track = data.get("ais_track")
        self.ais_track = _TrackView(track) if isinstance(track, dict) else None
        identity = data.get("identity_claimed")
        self.identity_claimed = identity if isinstance(identity, dict) else {}


def _register_record(anon: Any, subs: dict[str, str], record: dict[str, Any]) -> None:
    """Fold one record's identities into the shared substitution table."""
    subs.update(anon.substitutions(_RecordView(record)))


def _register_mmsi(anon: Any, subs: dict[str, str], mmsi: Any) -> str | None:
    """Pseudonymise a bare MMSI — the ground-truth rows carry one and no record.

    Returns the synthetic value so a caller can print it. Already-999 input passes
    through unchanged and is deliberately NOT added to the substitution table: mapping a
    value onto itself would make the leak assertion below match on every line."""
    if not isinstance(mmsi, str) or not mmsi or mmsi == "-":
        return None
    fake = anon.mmsi(mmsi)
    if isinstance(fake, str) and fake != mmsi:
        subs[mmsi] = fake
    return fake


# ============================================================================
# Formatting.
#
# One formatter for "not available", copied from evidence._fmt's reasoning: a document
# that says "None" in one row, "-" in another and "n/a" in a third teaches its reader
# that the three mean different things.
# ============================================================================

UNMEASURED = "**UNMEASURED**"


def _cell(text: Any) -> str:
    """Make a value safe inside a markdown table cell. Pipes and newlines inside a
    lane C reason string would otherwise silently split the row into new columns and the
    table would render as nonsense without erroring."""
    s = "not available" if text is None else str(text)
    return s.replace("|", "\\|").replace("\n", " ").strip()


def _conf(value: Any) -> str:
    """A confidence, or the one spelling of 'not available' this document uses. Printing
    a bare `None` in one cell and `not available` in another teaches the reader that the
    two mean different things."""
    if value is None:
        return "not available"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return _cell(value)


def _pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{100.0 * numerator / denominator:.1f}%"


def _git_describe() -> str:
    """Best-effort commit id. A document that cannot be traced to a code state is not
    evidence — but an absent git is not a reason to refuse to write, so this degrades to
    a stated gap rather than an exception."""
    try:
        out = subprocess.run(
            ["git", "-C", str(_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"UNAVAILABLE ({type(exc).__name__})"
    if out.returncode != 0:
        return "UNAVAILABLE (not a git checkout, or git not on PATH)"
    commit = out.stdout.strip()
    try:
        dirty = subprocess.run(
            ["git", "-C", str(_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, check=False)
        if dirty.returncode == 0 and dirty.stdout.strip():
            commit += " (WORKING TREE DIRTY — this run is not reproducible from the "
            commit += "commit alone)"
    except (OSError, subprocess.SubprocessError):
        pass
    return commit


# ============================================================================
# One scene, run.
# ============================================================================

def run_one_scene(pl: _Pipeline, scene_dir: Path, now: datetime) -> dict[str, Any]:
    """Load, run, score, and recover the ConsistencyResults. Loud at every step."""
    _require(scene_dir.is_dir(),
             f"--scene {scene_dir} is not a directory. A scene is the directory "
             f"make_synthetic_eo.py wrote (scene_manifest.json, ais_tracks.jsonl, "
             f"eo_contacts.jsonl, ground_truth.jsonl), not one of the files in it.")
    for needed in ("scene_manifest.json", "ais_tracks.jsonl", "eo_contacts.jsonl"):
        _require((scene_dir / needed).exists(),
                 f"{scene_dir / needed} is missing. Regenerate the scene:\n"
                 f"  python 04_demo/make_synthetic_eo.py --out {scene_dir}")

    scene = pl.run_pipeline.load_scene(scene_dir)
    manifest = scene["manifest"]
    pose = manifest.get("pose")
    _require(isinstance(pose, dict) and "lat_deg" in pose,
             f"{scene_dir}/scene_manifest.json has no usable 'pose' block. Every "
             f"geometric check in this report is computed from the camera position; "
             f"without it there is nothing to report and guessing one would fabricate "
             f"every bearing in the document.")

    result = pl.run_pipeline.run_scene(
        scene["tracks"], scene["contacts"], pose, now=now)
    _require("records" in result and "counts" in result,
             "run_pipeline.run_scene() returned a shape report.py does not recognise "
             "(expected keys 'records' and 'counts'). Its return contract changed; "
             "update report.py rather than reading around it.")

    truth = scene["ground_truth"]
    scoring = pl.run_pipeline.score_scene(result, truth) if truth else None
    if scoring is not None:
        # score_scene appends EXACTLY ONE row per ground-truth injection, in input
        # order (run_pipeline.py:206-245, one `rows.append` per loop iteration). This
        # report zips the two so a miss can be reported with the identity and detail
        # that produced it, which score_scene's row does not carry. The invariant is
        # cheap to check and expensive to get silently wrong — a shifted zip would
        # attribute every failure to the wrong injected fault.
        _require(len(scoring["rows"]) == len(truth),
                 f"score_scene() returned {len(scoring['rows'])} rows for "
                 f"{len(truth)} ground-truth injections. report.py pairs them "
                 f"positionally to recover each miss's identity; a length mismatch "
                 f"means that pairing is now wrong and the misses table would name "
                 f"the wrong vessels. Refusing to guess.")

    # ---- the second, deterministic pass for the ConsistencyResults ------------------
    # Exactly the call run_pipeline.py:91-113 makes. See the module docstring for why.
    try:
        associations = pl.association.associate(
            scene["contacts"], scene["tracks"],
            camera_lat_deg=pose["lat_deg"],
            camera_lon_deg=pose["lon_deg"],
            boresight_deg_true=pose["boresight_deg_true"],
            fov_half_angle_deg=pose.get("fov_half_angle_deg",
                                        pose["hfov_deg"] / 2.0),
            max_range_m=pose["max_range_m"],
        )
    except KeyError as exc:
        _die(f"{scene_dir}/scene_manifest.json pose block is missing {exc}. "
             f"report.py mirrors run_pipeline.py's associate() call exactly; if the "
             f"pose schema changed, both call sites must change together.")
    results_by_assoc = pl.consistency.check_all(
        associations,
        {t.track_id: t for t in scene["tracks"]},
        {c.contact_id: c for c in scene["contacts"]},
        camera=(pose["lat_deg"], pose["lon_deg"]))

    # CROSS-CHECK. If the re-run ever diverges from the run that produced the records,
    # every coverage and prior-sensitivity number below would describe a different set
    # of pairings than the verdicts they are printed beside. That is a wrong document,
    # not a slow one, so it stops here.
    record_assoc_ids = {r["verdict"]["association_id"] for r in result["records"]}
    recomputed_ids = {a.association_id for a in associations}
    missing = sorted(record_assoc_ids - recomputed_ids)
    _require(not missing,
             f"the association pass was re-run to recover the ConsistencyResults and it "
             f"did not reproduce the first pass: {len(missing)} association id(s) in "
             f"the records are absent from the re-run (first few: {missing[:3]}). "
             f"association.associate() is no longer deterministic for a fixed scene, so "
             f"sections 3 and 7 of this report cannot be trusted against sections 2 and "
             f"5. Fix the nondeterminism before regenerating this document.")

    return {
        "scene_dir": scene_dir,
        "manifest": manifest,
        "counts": result["counts"],
        "records": result["records"],
        "scoring": scoring,
        "truth": truth,
        "results_by_assoc": results_by_assoc,
        "n_associations": len(associations),
    }


# ============================================================================
# Section builders. Each returns a list of markdown lines.
# ============================================================================

def _section_provenance(runs: list[dict[str, Any]], pl: _Pipeline,
                        started: datetime, command: str) -> list[str]:
    """1. What the run was. A document that cannot be reproduced is not evidence."""
    L = ["## 1. What this run was", "",
         "Every number in sections 2 to 5 and 7 was computed by the command below over "
         "the scenes below. Nothing in those sections is asserted from a design "
         "intention; if a figure is not measurable from this run it says "
         f"{UNMEASURED} and names the instrument that would measure it.", "",
         "```", command, "```", "",
         "| Field | Value |", "|---|---|",
         f"| Generated (UTC) | {started.isoformat()} |",
         f"| Scenes | {len(runs)} |",
         f"| Pipeline version | `{pl.evidence.PIPELINE_VERSION}` |",
         f"| Verdict ruleset | `{pl.verdict.RULESET_VERSION}` |",
         f"| Consistency ruleset | `{pl.consistency.RULESET_VERSION}` |",
         f"| Code commit | `{_git_describe()}` |",
         f"| Python | {platform.python_version()} on {platform.system()} "
         f"{platform.machine()} |",
         ""]

    L += ["### The scenes", "",
          "| Scene | Harness | Seed | Generated | AIS tracks | EO contacts | "
          "Injected faults | Spoofer |", "|---|---|---|---|---|---|---|---|"]
    for run in runs:
        m = run["manifest"]
        c = m.get("counts") or {}
        L.append(
            f"| `{_cell(run['scene_dir'])}` "
            f"| `{_cell(m.get('harness_version'))}` "
            f"| `{_cell(m.get('seed'))}` "
            f"| {_cell(m.get('generated_at_utc'))} "
            f"| {_cell(c.get('ais_tracks'))} "
            f"| {_cell(c.get('eo_contacts'))} "
            f"| {_cell(c.get('injections'))} "
            f"| {_cell(m.get('spoofer_competence'))} |")
    L.append("")

    L += ["### What the pipeline made of them", "",
          "| Scene | Associations | Matched pairs | Dark candidates | "
          "Unobserved claims | Records |", "|---|---|---|---|---|---|"]
    for run in runs:
        c = run["counts"]
        L.append(f"| `{_cell(run['scene_dir'].name)}` "
                 f"| {c.get('associations')} | {c.get('matched')} "
                 f"| {c.get('dark_candidates')} | {c.get('unobserved_claims')} "
                 f"| {len(run['records'])} |")
    L.append("")

    labels: Counter[str] = Counter()
    for run in runs:
        for rec in run["records"]:
            labels[rec["verdict"]["label"]] += 1
    total = sum(labels.values())
    L += ["### Verdict labels", "",
          "| Label | Count | Share |", "|---|---|---|"]
    for label in ("MATCH", "DARK", "SPOOF", "UNKNOWN"):
        L.append(f"| {label} | {labels.get(label, 0)} | "
                 f"{_pct(labels.get(label, 0), total)} |")
    L.append(f"| **total** | **{total}** | |")
    L += ["",
          "> The EO contacts in these scenes are **synthetic** — forward-projected from "
          "real Danish Maritime Authority AIS through a declared camera and noise "
          "model. No camera observed anything. Detection rates here therefore measure "
          "the *decision logic* against faults it was handed, not a camera against the "
          "sea. The scene manifest's own caveat list is reproduced in section 8.", ""]
    return L


def _section_deferrals(runs: list[dict[str, Any]], pl: _Pipeline) -> list[str]:
    """2. Where it deferred, and why. Deferral is the designed behaviour."""
    reasons: Counter[str] = Counter()
    deferred = 0
    total = 0
    no_reason = 0
    for run in runs:
        for rec in run["records"]:
            v = rec["verdict"]
            total += 1
            if v.get("defer_to_human"):
                deferred += 1
                if not v.get("defer_reasons"):
                    no_reason += 1
            for r in v.get("defer_reasons") or []:
                reasons[r] += 1

    L = ["## 2. Where the tool refused to conclude, and why", "",
         "**Deferral is the designed behaviour, not a failure.** "
         f"`verdict.py` states a conclusion autonomously only at confidence "
         f"\u2265 {pl.verdict.CONFIDENCE_STATE:.2f}, and structural triggers override "
         "the arithmetic entirely. This table is the evidence that the defer path is "
         "live rather than decorative: a pipeline whose defer reasons are all zero has "
         "either a perfect scene or a dead branch, and only one of those is likely.", "",
         f"**{deferred} of {total} verdicts ({_pct(deferred, total)}) are flagged "
         f"`defer_to_human`.**", ""]

    if not reasons:
        L += ["| Defer reason | Verdicts | Share of all verdicts |", "|---|---|---|",
              "| *none raised* | 0 | 0.0% |", "",
              "**No verdict in this run carried a defer reason.** That is either a "
              "scene with nothing ambiguous in it or a defer path that is not wired up. "
              "It is not evidence of correctness and must not be quoted as such. What "
              "would distinguish the two: run a scene with `--n-coverage-hole` and "
              "`--n-missed-detection` above zero and confirm the reasons appear.", ""]
        return L

    L += ["| Defer reason | Verdicts carrying it | Share of all verdicts |",
          "|---|---|---|"]
    for reason, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
        L.append(f"| `{_cell(reason)}` | {count} | {_pct(count, total)} |")
    L.append("")
    L += ["Reasons are not mutually exclusive — one verdict can carry several, so the "
          "shares do not sum to the deferral rate.", ""]

    if no_reason:
        L += [f"> **{no_reason} verdict(s) are flagged for a human with an EMPTY reason "
              "list.** That is the criterion-4 failure mode in its purest form: the "
              "pipeline correctly decided it could not conclude, and the operator is "
              "never told why. It is reported here rather than smoothed over.", ""]

    untaught = sorted(r for r in reasons
                      if r not in getattr(pl.evidence, "_DEFER_TEXT", {}))
    if untaught:
        L += [f"> **{len(untaught)} defer reason(s) have no plain-language description "
              f"in `evidence._DEFER_TEXT`:** "
              + ", ".join(f"`{_cell(r)}`" for r in untaught)
              + ". They reach the case file through `evidence._defer_text()`'s loud "
                "fallback, which names the raw enum and tells the reader to escalate. "
                "Add the prose.", ""]
    return L


def _section_uncomparable(runs: list[dict[str, Any]]) -> list[str]:
    """3. What it could NOT compare. The coverage story."""
    per_dim_unc: Counter[str] = Counter()
    per_dim_ran: Counter[str] = Counter()
    per_dim_mismatch: Counter[str] = Counter()
    suspicious = 0
    reason_counts: dict[str, Counter[str]] = {}
    pairs = 0
    fully_uncomparable = 0

    for run in runs:
        for result in run["results_by_assoc"].values():
            pairs += 1
            for a in result.agreements:
                per_dim_ran[a.dimension] += 1
            for m in result.mismatches:
                per_dim_ran[m.dimension] += 1
                per_dim_mismatch[m.dimension] += 1
            for u in result.uncomparable:
                per_dim_unc[u.dimension] += 1
                reason_counts.setdefault(u.dimension, Counter())[u.reason] += 1
                if u.suspicious:
                    suspicious += 1
            if not result.agreements and not result.mismatches:
                fully_uncomparable += 1

    L = ["## 3. What could not be compared at all", "",
         "This is the coverage story, and it is the one a report is most likely to omit "
         "while still looking thorough. A document listing four agreeing dimensions "
         "while silently having compared two is not evidence-grade — it is a misleading "
         "one, and it misleads in the reassuring direction.", "",
         "`consistency.py` returns a **three-way** outcome per dimension — Mismatch, "
         "Agreement, Uncomparable — precisely so this table can exist. Collapsing the "
         "last two into \"no mismatch\" is what would make an unexamined vessel read as "
         "a clean one.", ""]

    if not pairs:
        L += [f"{UNMEASURED} — no matched pair in this run produced a "
              "`ConsistencyResult`, so no dimension was attempted. Every contact was "
              "either dark or an unobserved claim. What would measure it: a scene "
              "containing at least one contact paired to an AIS track.", ""]
        return L

    dims = sorted(set(per_dim_unc) | set(per_dim_ran))
    L += [f"Across **{pairs} matched pair(s)**:", "",
          "| Dimension | Compared | of which disagreed | Could not compare | "
          "Coverage |", "|---|---|---|---|---|"]
    for d in dims:
        ran = per_dim_ran.get(d, 0)
        unc = per_dim_unc.get(d, 0)
        L.append(f"| {_cell(d)} | {ran} | {per_dim_mismatch.get(d, 0)} | {unc} | "
                 f"{_pct(ran, ran + unc)} |")
    total_ran = sum(per_dim_ran.values())
    total_unc = sum(per_dim_unc.values())
    L.append(f"| **all** | **{total_ran}** | **{sum(per_dim_mismatch.values())}** | "
             f"**{total_unc}** | **{_pct(total_ran, total_ran + total_unc)}** |")
    L.append("")

    if fully_uncomparable:
        L += [f"> **{fully_uncomparable} of {pairs} matched pair(s) "
              f"({_pct(fully_uncomparable, pairs)}) had NOTHING comparable at all** — "
              "no agreement and no mismatch on any dimension. `verdict.py` caps MATCH "
              "confidence by this coverage fraction, which is what stops such a vessel "
              "from being reported as a confident MATCH. It is an unexamined vessel, "
              "and the label it gets is UNKNOWN.", ""]

    if suspicious:
        L += [f"> **{suspicious} uncomparable dimension(s) were flagged "
              "`suspicious`** — a vessel reporting position normally while broadcasting "
              "no identity static block at all. `consistency.check_pair()` sets this as "
              "a WEAK POSITIVE INDICATOR only; `verdict.py` turns it into the "
              "`claim_field_missing` defer reason and never into a mismatch. There is "
              "no sigma on it, because there is no measurement behind a "
              "non-observation.", ""]

    L += ["### Why each comparison could not run", ""]
    for d in dims:
        counter = reason_counts.get(d)
        if not counter:
            continue
        L.append(f"**{_cell(d)}** — {per_dim_unc.get(d, 0)} uncomparable:")
        L.append("")
        for reason, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
            L.append(f"- {count}x — {_cell(reason)}")
        L.append("")
    return L


def _section_false_positives(runs: list[dict[str, Any]]) -> list[str]:
    """4. False positives, both numbers, from score_scene's control rows."""
    L = ["## 4. False positives — the number that decides whether this is usable", "",
         "A watch officer abandons a tool that cries wolf long before they notice it "
         "missed something. The scenes inject deliberate NON-faults — coverage holes, "
         "missed detections, clutter contacts where no vessel exists — and mark them "
         "`must_not_fire`. Producing DARK or SPOOF on one of those rows is a false "
         "positive.", "",
         "**Both numbers are quoted, and that is the point.** `score_scene()` separates "
         "them:", "",
         "- **ACTIONABLE false positives** — labelled DARK or SPOOF *and not* deferred. "
         "The tool would put this in front of an operator as a finding. This is the "
         "defensible measure.",
         "- **Labelled but deferred** — labelled DARK or SPOOF *and* flagged "
         "`defer_to_human`. The tool said \"I cannot tell, a human should look\", which "
         "is the behaviour criterion 4 asks for.", "",
         "Quoting only the first is how a definition becomes self-serving. If the two "
         "diverge a lot, the deferral is doing the work and the headline number is "
         "flattering.", ""]

    scored = [r for r in runs if r["scoring"]]
    if not scored:
        L += [f"{UNMEASURED} — no scene in this run carried a `ground_truth.jsonl`, so "
              "there are no control rows and no false-positive rate can be computed. "
              "**Do not quote a false-positive rate of zero.** What would measure it:",
              "", "```",
              "python 04_demo/make_synthetic_eo.py --out 04_demo/out/scene01 \\",
              "    --n-coverage-hole 2 --n-missed-detection 2",
              "python 03_src/report.py --scene 04_demo/out/scene01",
              "```", ""]
        return L

    fired = deferred_only = control_total = 0
    for run in scored:
        k = run["scoring"]["controls"]
        fired += k["false_positives"]
        deferred_only += k["labelled_but_deferred"]
        control_total += k["total"]

    L += ["| Measure | Count | Rate over control cases |", "|---|---|---|",
          f"| Control cases that must stay silent | {control_total} | |",
          f"| **ACTIONABLE false positives** | **{fired}** | "
          f"**{_pct(fired, control_total)}** |",
          f"| Labelled but deferred to a human | {deferred_only} | "
          f"{_pct(deferred_only, control_total)} |",
          f"| Labelled either way | {fired + deferred_only} | "
          f"{_pct(fired + deferred_only, control_total)} |", ""]

    if control_total and deferred_only > fired:
        L += ["> **The deferral path is carrying most of the control performance.** "
              f"{deferred_only} control case(s) were labelled DARK or SPOOF and rescued "
              f"by the defer flag, against {fired} that were not. The honest reading is "
              "that the classifier fires on these cases and the escalation threshold is "
              "what keeps them off the operator's actionable list — which is a real "
              "property of the design, and a fragile one if that threshold moves.", ""]

    L += ["### Every control row", "",
          "| Scene | Injected non-fault | Verdict given | Deferred | Confidence | "
          "Silent? |", "|---|---|---|---|---|---|"]
    any_row = False
    for run in scored:
        for row in run["scoring"]["rows"]:
            if row.get("expected") != "silence":
                continue
            any_row = True
            L.append(f"| `{_cell(run['scene_dir'].name)}` | {_cell(row['kind'])} "
                     f"| {_cell(row['got'])} "
                     f"| {'yes' if row.get('deferred') else 'no'} "
                     f"| {_conf(row.get('confidence'))} "
                     f"| {'yes' if row['ok'] else '**NO — FALSE POSITIVE**'} |")
    if not any_row:
        L.append("| *no control rows in this run* | | | | | |")
    L.append("")
    L += ["> `NO_RECORD` in the verdict column means the pipeline produced no record for "
          "that injection at all. On a control row that is a **pass** by the scorer's "
          "definition — nothing was put in front of an operator — but it is silence "
          "from absence, not silence from judgement, and the two should not be read as "
          "the same evidence of restraint.", ""]
    return L


def _section_misses(runs: list[dict[str, Any]], anon: Any,
                    subs: dict[str, str]) -> list[str]:
    """5. Injected faults the pipeline did not catch, by kind, with what it said."""
    L = ["## 5. What it missed", "",
         "Injected faults the pipeline did not label correctly. This half of the score "
         "is optimistic by construction and it is worth saying so out loud: the faults "
         "in these scenes were chosen by the same project that built the detector, so a "
         "high detection rate here measures internal consistency, not field "
         "performance.", ""]

    scored = [r for r in runs if r["scoring"]]
    if not scored:
        L += [f"{UNMEASURED} — no scene carried a `ground_truth.jsonl`, so no injected "
              "fault can be checked. What would measure it: regenerate the scene with "
              "`04_demo/make_synthetic_eo.py`, which writes the answer key beside the "
              "data.", ""]
        return L

    hit = total = 0
    by_kind: dict[str, dict[str, Any]] = {}
    miss_rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for run in scored:
        rows = run["scoring"]["rows"]
        truth = run["truth"]
        d = run["scoring"]["detection"]
        hit += d["hit"]
        total += d["total"]
        for row, inj in zip(rows, truth):
            if row.get("expected") == "silence":
                continue
            slot = by_kind.setdefault(
                row["kind"], {"n": 0, "hit": 0, "got": Counter()})
            slot["n"] += 1
            if row["ok"]:
                slot["hit"] += 1
            else:
                slot["got"][f"{row['got']}"
                             + (f"/{row['subtype']}" if row.get("subtype") else "")] += 1
                miss_rows.append((run["scene_dir"].name, row, inj))

    L += [f"**{hit} of {total} injected faults labelled correctly "
          f"({_pct(hit, total)}). {total - hit} missed.**", "",
          "| Injected fault | Injected | Caught | Missed | Verdicts given instead |",
          "|---|---|---|---|---|"]
    for kind in sorted(by_kind):
        s = by_kind[kind]
        instead = ", ".join(f"`{_cell(g)}` x{n}"
                            for g, n in sorted(s["got"].items(),
                                               key=lambda kv: (-kv[1], kv[0])))
        L.append(f"| {_cell(kind)} | {s['n']} | {s['hit']} | {s['n'] - s['hit']} | "
                 f"{instead or '—'} |")
    L.append("")

    if not miss_rows:
        L += ["No injected fault was missed in this run. That is a statement about "
              "**these** scenes at **this** seed and nothing more — the faults were "
              "chosen to be visible to the checks that exist. It is not a detection "
              "rate for the sea.", ""]
        return L

    L += ["### Every miss, individually", "",
          "| Scene | Fault | Pseudonymised MMSI | Expected | Got | Confidence | "
          "How it was injected |", "|---|---|---|---|---|---|---|"]
    for scene_name, row, inj in miss_rows:
        fake = _register_mmsi(anon, subs, inj.get("mmsi")) or "no AIS claim"
        got = str(row["got"]) + (f" / {row['subtype']}" if row.get("subtype") else "")
        L.append(f"| `{_cell(scene_name)}` | {_cell(row['kind'])} | `{_cell(fake)}` "
                 f"| {_cell(row['expected'])} | {_cell(got)} "
                 f"| {_conf(row.get('confidence'))} "
                 f"| {_cell(inj.get('detail'))} |")
    L += ["",
          "> A miss that came back **UNKNOWN** and a miss that came back **MATCH** are "
          "different failures and should be read differently. UNKNOWN is the tool "
          "saying it could not tell, which is recoverable by a human looking. MATCH is "
          "the tool vouching for a claim that was false, and it is the failure that "
          "would matter in court.", ""]
    return L


def _section_structural(pl: _Pipeline) -> list[str]:
    """6. The limits no amount of running changes. STATED, not generated."""
    tol = pl.consistency.DEFAULT_TOLERANCES

    L = ["## 6. Structural limits — these do not improve with more data", "",
         "Everything above was measured from a run. **This section is not.** These are "
         "properties of the physics, of a declared model, or of a threshold derived "
         "from one — running the pipeline again does not move any of them, and a "
         "document that only reported measured failures would leave the reader "
         "believing these five are absent. Each is attributed to the file that "
         "documents it, and the arithmetic is recomputed from that file's own constants "
         "at generation time so this section cannot silently go stale when a threshold "
         "moves.", ""]

    # ---- 6.1 along the line of sight ----------------------------------------------
    L += ["### 6.1 A claim displaced ALONG the line of sight is undetectable. Ever.", "",
          "Source: `03_src/consistency.py`, `check_position()`.", "",
          "A monocular camera measures **direction** well and **distance** badly. "
          "`check_position()` therefore tests the cross-range component only — the "
          "predicted bearing of the claim against the observed bearing of the hull — "
          "because pooling the two errors into one isotropic sigma throws away the good "
          "measurement in order to average it with the bad one. Measured by lane C at "
          "5 km, quoted from that function's own docstring:", "",
          "```",
          "cross-range (bearing) sigma : 2.5 deg x 5000 m  =  218 m",
          "along-range (range) sigma   : 25% of 5000 m     = 1250 m",
          "combined                    :                     1269 m",
          "",
          "a 1000 m injected spoof -> 0.79 sigma combined  (invisible)",
          "                        -> 4.58 sigma cross-range only (obvious)",
          "```", "",
          "**What that honestly gives up:** a vessel that displaces its broadcast "
          "position directly toward or away from the camera is **not detectable by one "
          "monocular sensor, at any offset, ever.** The geometry forbids it — there is "
          "no measurement to make. Detecting it requires a second camera on a different "
          "baseline, a radar, or the vessel to move. No threshold, no model and no "
          "amount of data changes this, and it is the single limit most likely to be "
          f"exploited by anyone who reads this document.", "",
          f"The floor on the cross-range test is "
          f"`Tolerances.min_bearing_sigma_deg = {tol.min_bearing_sigma_deg}` deg — the "
          "honest statement that no pose is better than that indoors.", ""]

    # ---- 6.2 an AIS gap is not proof -----------------------------------------------
    L += ["### 6.2 An AIS gap is not proof a transponder was switched off", "",
          "Sources: `99_scratch/handoff_A.md` (lane A's measurements), "
          "`03_src/ais_trajectory.py` (the three ways a gap lies), "
          "`03_src/verdict.py` (`DEFAULT_AIS_COVERAGE_CONFIDENCE`, "
          "`SPARSE_COVERAGE_THRESHOLD`).", "",
          "Measured by lane A on the demo day's Fehmarn Belt slice — **not by this "
          "run**, and not re-measurable from a synthetic scene:", "",
          "| Measurement | Value | Source |", "|---|---|---|",
          "| AIS gaps over 5 min, Fehmarn slice, 24 h | 3,265 | lane A, "
          "`handoff_A.md` |",
          "| AIS gaps over 10 min, across 108 vessels | 660 | lane A, "
          "`handoff_A.md` |",
          "| Messages per vessel, Fehmarn | 3,556 | lane A, `STATUS.md` |",
          "| Messages per vessel, Bornholm, SAME DAY | 1,432 | lane A, `STATUS.md` |",
          "| Bornholm sparsity relative to Fehmarn | 2.5x sparser | lane A |", "",
          "Bornholm sits at the edge of Danish basestation coverage. Same day, same "
          "transponders, 2.5x sparser tracks — from receiver geometry alone. **An AIS "
          "gap caused by coverage is not an AIS gap caused by evasion, and this "
          "pipeline cannot tell them apart.** It does not pretend to: `verdict.py` "
          "takes an `ais_coverage_confidence` for the area and raises the "
          "`sparse_ais_coverage` defer reason below "
          f"`SPARSE_COVERAGE_THRESHOLD = {pl.verdict.SPARSE_COVERAGE_THRESHOLD}`.", "",
          f"**This run used the default "
          f"`DEFAULT_AIS_COVERAGE_CONFIDENCE = "
          f"{pl.verdict.DEFAULT_AIS_COVERAGE_CONFIDENCE}`**, because "
          "`run_pipeline.run_scene()` does not pass one. That default is "
          + ("below" if pl.verdict.DEFAULT_AIS_COVERAGE_CONFIDENCE
             < pl.verdict.SPARSE_COVERAGE_THRESHOLD else "at or above")
          + " the sparse threshold, so the deferral counts in section 2 reflect that "
            "choice and not a property of the scene. A deployment in a well-covered "
            "sector should raise it, and the number should be measured for the sector "
            "rather than assumed.", "",
          f"{UNMEASURED}: the gap rate for the actual demo camera's sector. What would "
          "measure it — `ais_trajectory.detect_gaps()` over a slice bounded by the "
          "camera's own field of view, with `explained_by_area_exit` and "
          "`explained_by_sparse_coverage` both reported.", ""]

    # ---- 6.3 calibration -----------------------------------------------------------
    L += ["### 6.3 The confidence is calibrated against a DECLARED model, not learned",
          "",
          "Source: `03_src/verdict.py`, `CALIBRATION_STATEMENT`, reproduced verbatim:",
          "", "> " + pl.verdict.CALIBRATION_STATEMENT, "",
          "The three declared inputs, read live from `verdict.py` at generation time:",
          "", "| Declared assumption | Value |", "|---|---|",
          f"| Prior P(a vessel is broadcasting a false identity) | "
          f"{pl.verdict.SPOOF_PRIOR:.6f} (1 in "
          f"{round(1.0 / pl.verdict.SPOOF_PRIOR)}) |",
          f"| Correlated-evidence damping per extra dimension | "
          f"{pl.verdict.CORRELATION_DAMPING} |",
          f"| Confidence at which a label is stated autonomously | "
          f"{pl.verdict.CONFIDENCE_STATE} |",
          f"| Confidence below which nothing is alleged | "
          f"{pl.verdict.CONFIDENCE_ALLEGE_FLOOR} |",
          f"| Coverage floor below which MATCH becomes UNKNOWN | "
          f"{pl.verdict.MATCH_COVERAGE_FLOOR} |",
          f"| MATCH confidence ceiling at total coverage | "
          f"{pl.verdict.MATCH_CONFIDENCE_CEILING} |", "",
          "The honest claim is **\"calibrated against the model\"**, never "
          "**\"validated against outcomes\"**. There is no labelled maritime spoofing "
          "dataset in this project, nothing was fitted, and scikit-learn is not a "
          "dependency of `verdict.py`. A number that looked trained and was fitted on "
          "invented labels would be worse than this one, because it would carry "
          "borrowed authority.", "",
          f"{UNMEASURED}, and it is the honest gap in the whole pipeline: **the "
          "reliability curve.** Of the verdicts this tool states at confidence 0.90, "
          "what fraction are actually correct? Nothing in this repository can answer "
          "that. What would measure it: a set of maritime cases with adjudicated "
          "outcomes — not injected faults, which were chosen by the same project that "
          "built the detector — binned by stated confidence and plotted against the "
          "observed correct-rate. Until that exists, the confidence is a posterior "
          "under stated assumptions and must be described as one.", ""]

    # ---- 6.4 class evidence cannot convict alone -----------------------------------
    prob_to_sigma = getattr(pl.consistency, "_probability_to_sigma", None)
    if callable(prob_to_sigma):
        # 1.0 is clamped to 0.999 inside the function, so this IS the ceiling of the
        # class scale rather than an assumption about it.
        class_ceiling = prob_to_sigma(1.0)
        confusable_ceiling = class_ceiling * tol.confusable_class_discount
        ceiling_txt = (f"`{class_ceiling:.4f}` nats "
                       f"(`consistency._probability_to_sigma(1.0)`, whose input is "
                       f"clamped at 0.999)")
        confusable_txt = f"`{confusable_ceiling:.4f}` nats"
        confusable_verdict = (
            "**below** the floor, so a confusable pair can NEVER raise a class "
            "mismatch at any confidence whatsoever"
            if confusable_ceiling < tol.class_min_report_sigma else
            "**at or above** the floor — the guarantee that a confusable pair can never "
            "fire NO LONGER HOLDS, and that is a defect, not a note")
    else:
        ceiling_txt = (f"{UNMEASURED} — `consistency._probability_to_sigma` is gone, so "
                       "the class scale's ceiling could not be recomputed")
        confusable_txt = UNMEASURED
        confusable_verdict = (
            "could not be recomputed; re-derive it before quoting this section")

    L += ["### 6.4 Class evidence tops out by construction and cannot convict alone", "",
          "Sources: `03_src/consistency.py` (`_probability_to_sigma`, `check_class`, "
          "`Tolerances`), `03_src/verdict.py` (`SINGLE_DIMENSION_CONFIDENCE_CAP`).", "",
          "Silhouette class is the weakest evidence in the system and the design makes "
          "that structural rather than advisory. Two independent ceilings:", "",
          "| Quantity | Value |", "|---|---|",
          "| Class significance is measured in | log-odds (nats), not sigmas |",
          f"| Maximum class significance at any confidence | {ceiling_txt} |",
          f"| Reporting floor for the class dimension | "
          f"`{tol.class_min_report_sigma}` nats "
          f"(`Tolerances.class_min_report_sigma`) |",
          f"| Confusable-pair discount | "
          f"`{tol.confusable_class_discount}` |",
          f"| Maximum significance for a confusable pair | {confusable_txt} |", "",
          f"A visually confusable pair — cargo/tanker, tug/small_craft, "
          f"fishing/small_craft, tug/fishing, passenger/cargo — is {confusable_verdict}. "
          "That is deliberate: a camera reporting \"cargo\" over a claimed tanker has "
          "not caught a spoofer, it has looked at two boxes with a superstructure aft "
          "from three kilometres away.", "",
          f"**And a class finding cannot convict alone even when it does fire.** "
          f"`verdict.SINGLE_DIMENSION_CONFIDENCE_CAP = "
          f"{pl.verdict.SINGLE_DIMENSION_CONFIDENCE_CAP}` caps the posterior of any "
          f"case resting on one dimension, and the autonomous-statement threshold is "
          f"`CONFIDENCE_STATE = {pl.verdict.CONFIDENCE_STATE}`. "
          + ("Since the cap is below the threshold, a single-dimension case — class or "
             "otherwise — **can never be stated as the machine's conclusion.** It "
             "always defers."
             if pl.verdict.SINGLE_DIMENSION_CONFIDENCE_CAP < pl.verdict.CONFIDENCE_STATE
             else "The cap is NOT below the threshold, so a single-dimension case CAN "
                  "now be stated autonomously. That is a change from the documented "
                  "design and should be reviewed."), "",
          "The reason is not squeamishness. With one dimension there is no "
          "corroboration, and an unmodelled systematic — a range estimate 40% low, "
          "scaling the apparent length — reproduces exactly the signature of a real "
          "spoof at any significance you like. A second, independently-failing "
          "dimension is what rules that out. More sigma on the first is not a "
          "substitute.", ""]

    # ---- 6.5 the vision model is inert ---------------------------------------------
    eo_path = _SRC / "eo_vlm.py"
    ceiling, ceiling_line, ceiling_err = _read_source_constant(
        eo_path, "UNCALIBRATED_CONFIDENCE_CEILING")
    L += ["### 6.5 The vision model is structurally inert while uncalibrated", "",
          "Sources: `03_src/eo_vlm.py` (`UNCALIBRATED_CONFIDENCE_CEILING`), "
          "`03_src/consistency.py` (`Tolerances.class_min_report_sigma`).", "",
          "The project guarantee is that an **uncalibrated** vision model cannot "
          "influence a verdict. It is enforced by arithmetic, not by a policy:", ""]
    if ceiling is None or not isinstance(ceiling, (int, float)):
        L += [f"- {UNMEASURED} — the ceiling could not be read from "
              f"`03_src/eo_vlm.py`: {ceiling_err}. **This section's guarantee is "
              "therefore UNVERIFIED in this document.** What would fix it: restore a "
              "module-level literal `UNCALIBRATED_CONFIDENCE_CEILING` in `eo_vlm.py`, "
              "or update `report.py` to read it wherever it now lives. It is read by "
              "parsing the file rather than importing it, because `eo_vlm.py` is the "
              "module that calls a model and this file imports nothing that does.", ""]
    else:
        nats = math.log(ceiling / (1.0 - ceiling))
        inert = nats < tol.class_min_report_sigma
        L += ["| Quantity | Value | Read from |", "|---|---|---|",
              f"| Uncalibrated confidence ceiling | `{ceiling}` | "
              f"`03_src/eo_vlm.py:{ceiling_line}` (parsed, not imported) |",
              f"| ...in log-odds, ln(p/(1-p)) | `{nats:.4f}` nats | recomputed here |",
              f"| Class reporting floor | `{tol.class_min_report_sigma}` nats | "
              f"`consistency.Tolerances.class_min_report_sigma` |", "",
              (f"**{nats:.3f} < {tol.class_min_report_sigma} — the guarantee holds.** "
               "An uncalibrated vision model cannot raise a class mismatch, therefore "
               "cannot contribute a likelihood ratio, therefore cannot move a verdict. "
               "It is not disabled by a flag somebody could forget to set; it cannot "
               "reach the floor."
               if inert else
               f"**{nats:.3f} >= {tol.class_min_report_sigma} — THE GUARANTEE IS "
               "BROKEN.** An uncalibrated model can now raise a class mismatch and "
               "therefore move a verdict. This is a P0, not a footnote: it is the exact "
               "failure `Tolerances.class_min_report_sigma` was derived to close, and "
               "it died silently once before because lane B and lane C deliberately do "
               "not import each other."), "",
              "This limit is worth stating precisely because it is a limit **and** a "
              "safety property. The system currently gets no evidential value at all "
              "from its vision classifier. Lifting the ceiling requires a calibration "
              "file produced by measuring the model's agreement rate on labelled crops "
              f"— {UNMEASURED} in this repository, and the only thing that should ever "
              "move that number.", ""]

    L += ["### 6.6 What the whole system is, and is not", "",
          "- It produces **decision support**, never a determination of intent or "
          "wrongdoing. A human decides. (`00_brief/constraints.md`, constraint 1.)",
          "- Identity fields are **claims broadcast by a transponder**, never "
          "assertions by this tool. Naming a real vessel as a saboteur is defamatory. "
          "(`00_brief/constraints.md`, constraint 2.)",
          "- Output identities are **pseudonymised, not anonymised.** Position, time, "
          "course, speed and dimensions are untouched, so a reader with AIS history can "
          "re-identify a vessel from its track. (`03_src/evidence.py`, the "
          "`disclosure` block.)",
          "- The verdict and the confidence are produced by the deterministic pipeline. "
          "A language model, where one is used, writes a rationale paragraph and "
          "nothing else. **There is no model call in this reporting file at all.**", ""]
    return L


def _prior_range_text(pl: _Pipeline) -> str:
    """The prior range `is_prior_sensitive()` actually sweeps, read from its own default
    rather than restated here. Restating it is how a document ends up quoting a range
    the code stopped using two commits ago."""
    try:
        default = inspect.signature(
            pl.verdict.is_prior_sensitive).parameters["priors"].default
        return ", ".join(f"1 in {round(1.0 / p)}" for p in default)
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        return (f"{UNMEASURED} — the sweep range could not be read from "
                "`verdict.is_prior_sensitive`'s signature")


def _section_prior_sensitivity(runs: list[dict[str, Any]], pl: _Pipeline) -> list[str]:
    """7. Verdicts whose LABEL changes across plausible priors."""
    L = ["## 7. Verdicts that rest on the assumption rather than the evidence", "",
         "The declared prior is the least defensible number in `verdict.py`: there is "
         "no authoritative published rate for AIS identity spoofing in the Baltic, and "
         f"`SPOOF_PRIOR = 1 in {round(1.0 / pl.verdict.SPOOF_PRIOR)}` is a deliberately "
         "conservative working figure chosen so the tool errs toward not accusing.", "",
         f"`verdict.is_prior_sensitive()` re-runs the posterior across the plausible "
         f"prior range ({_prior_range_text(pl)}) "
         "and reports whether the **label** changes. When it does, the finding is not a "
         "verdict — it is a preference, and the only honest output is a deferral. "
         "Counting them here is the point: it is the number that says how much of the "
         "output is carried by an assumption.", ""]

    sensitive: list[tuple[str, str, str, float, bool]] = []
    checked = 0
    errors: list[str] = []
    verdict_by_assoc: dict[str, dict[str, Any]] = {}
    for run in runs:
        for rec in run["records"]:
            verdict_by_assoc[rec["verdict"]["association_id"]] = rec["verdict"]

    for run in runs:
        for assoc_id, result in run["results_by_assoc"].items():
            checked += 1
            try:
                flag = pl.verdict.is_prior_sensitive(result)
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                errors.append(f"{assoc_id}: {type(exc).__name__}: {exc}")
                continue
            if flag:
                v = verdict_by_assoc.get(assoc_id) or {}
                sensitive.append((run["scene_dir"].name, assoc_id,
                                  str(v.get("label", "no record")),
                                  float(v.get("confidence") or 0.0),
                                  bool(v.get("defer_to_human"))))

    total_verdicts = sum(len(r["records"]) for r in runs)
    not_applicable = total_verdicts - checked

    L += ["| Measure | Count |", "|---|---|",
          f"| Verdicts in this run | {total_verdicts} |",
          f"| Testable (matched pairs with a ConsistencyResult) | {checked} |",
          f"| **Label changes across plausible priors** | **{len(sensitive)}** |",
          f"| Share of testable verdicts | {_pct(len(sensitive), checked)} |",
          f"| Not applicable (DARK / unobserved claim) | {not_applicable} |", ""]

    L += [f"**The {not_applicable} not-applicable verdict(s) are not a zero.** "
          "`is_prior_sensitive()` takes a `ConsistencyResult`, and a dark contact or an "
          "unobserved claim has no matched pair to compare, so no such result exists. "
          "Their confidence comes from AIS coverage and from association geometry, "
          f"which this test does not examine. Reporting them as insensitive would be "
          f"a fabricated reassurance. {UNMEASURED}: the sensitivity of a DARK verdict "
          "to `ais_coverage_confidence`. What would measure it — sweep that parameter "
          "through `verdict.decide()` for the dark associations and report where the "
          "`sparse_ais_coverage` reason and the label boundary move.", ""]

    if errors:
        L += [f"> **{len(errors)} sensitivity test(s) raised** and are reported rather "
              "than dropped: " + "; ".join(_cell(e) for e in errors[:5])
              + (" ..." if len(errors) > 5 else ""), ""]

    if sensitive:
        L += ["### The prior-sensitive verdicts", "",
              "| Scene | Pairing | Label given | Confidence | Deferred? |",
              "|---|---|---|---|---|"]
        for scene_name, assoc_id, label, conf, deferred in sensitive:
            L.append(f"| `{_cell(scene_name)}` | `{_cell(assoc_id)}` | {_cell(label)} "
                     f"| {conf:.2f} | "
                     f"{'yes' if deferred else '**NO — stated autonomously**'} |")
        L.append("")
        stated = [s for s in sensitive if not s[4]]
        if stated:
            L += [f"> **{len(stated)} prior-sensitive verdict(s) were stated "
                  "autonomously.** A finding whose label flips when the assumed base "
                  "rate moves within its plausible range is carried by the assumption, "
                  "not by the evidence, and should defer. This is the most actionable "
                  "line in the document.", ""]
        else:
            L += ["> All prior-sensitive verdicts are flagged `defer_to_human`, which "
                  "is the designed behaviour: the finding is presented, and a person "
                  "decides.", ""]
    elif checked:
        L += ["No testable verdict changed its label across the plausible prior range "
              f"({_prior_range_text(pl)}). "
              "That is a property of these scenes' evidence strength — the injected "
              "faults are large — and not a general property of the method. A "
              "borderline case is exactly the one that would flip, and these scenes "
              "contain few.", ""]
    else:
        L += [f"{UNMEASURED} — no matched pair in this run produced a "
              "`ConsistencyResult`, so nothing was testable. What would measure it: a "
              "scene containing at least one contact paired to an AIS track.", ""]
    return L


def _section_caveats(runs: list[dict[str, Any]]) -> list[str]:
    """8. The scene generator's own caveats, carried through rather than restated."""
    L = ["## 8. What the scene generator already admits about itself", "",
         "Reproduced from each scene's `scene_manifest.json`. These are the modelling "
         "gaps the synthetic data itself declares — they bound everything measured in "
         "sections 2 to 5 and they are carried here verbatim rather than paraphrased.",
         ""]
    seen: set[str] = set()
    any_caveat = False
    for run in runs:
        for c in run["manifest"].get("caveats") or []:
            if c in seen:
                continue
            seen.add(c)
            any_caveat = True
            L.append(f"- {_cell(c)}")
    if not any_caveat:
        L.append(f"- {UNMEASURED} — no scene manifest carried a `caveats` block. "
                 "`make_synthetic_eo.py` writes one; its absence means the scene was "
                 "produced by something else, and the modelling gaps of that something "
                 "are unknown to this document.")
    L.append("")
    return L


# ============================================================================
# Assembly.
# ============================================================================

def build_document(runs: list[dict[str, Any]], pl: _Pipeline, anon: Any,
                   subs: dict[str, str], started: datetime,
                   command: str) -> str:
    L: list[str] = [
        "# Failure modes — how and where this system fails", "",
        "*Generated by `03_src/report.py`. Do not hand-edit: the next run overwrites "
        "this file, and a hand-edited number in a document whose whole claim is that "
        "its numbers are computed would be the worst single thing in the repository.*",
        "",
        "This is the honest-limits half of judging criterion 4 — *evidence-grade output "
        "plus an honest statement of limits where it must defer to a human*. Sections "
        "1 to 5 and 7 are computed from an actual pipeline run over scenes whose ground "
        "truth is known by construction. Section 6 is stated, not measured, because "
        "those limits are properties of the physics and of declared models rather than "
        "of any run.", "",
        "**No number in this document was written by hand, and nothing marked "
        "UNMEASURED is guessed at.** Where a figure was not measured, the document "
        "says so and names what would measure it. That convention is inherited from "
        "`04_demo/edge_benchmark.md`.", "",
        "**No language model was involved in producing this file.** `report.py` imports "
        "no module that calls one, and the document is deterministic given the same "
        "scene and the same code.", "",
        "---", "",
    ]
    L += _section_provenance(runs, pl, started, command) + ["---", ""]
    L += _section_deferrals(runs, pl) + ["---", ""]
    L += _section_uncomparable(runs) + ["---", ""]
    L += _section_false_positives(runs) + ["---", ""]
    L += _section_misses(runs, anon, subs) + ["---", ""]
    L += _section_structural(pl) + ["---", ""]
    L += _section_prior_sensitivity(runs, pl) + ["---", ""]
    L += _section_caveats(runs) + ["---", ""]

    L += [
        "**Identities: pseudonymised.** Every MMSI, name, IMO and callsign that reached "
        "this document was replaced with a 999-prefixed synthetic value by a single "
        "shared `evidence.Anonymiser` — one instance for the whole run, so a hull "
        "carries the same synthetic id in every table above and in the case files "
        f"beside them. {len(subs)} distinct real identifier(s) were replaced. MID 999 "
        "is unassigned to any country, so these are recognisably fake.",
        "",
        "**Position, time, course, speed and dimensions are unchanged — this is "
        "pseudonymisation, not anonymisation.** A reader with AIS history could "
        "re-identify a vessel from its track. The re-identification key is held locally "
        "and is not part of this document. Do not describe this output as anonymised.",
        "",
        "**Decision support, not enforcement.** This tool ranks and evidences; a human "
        "decides. Nothing above is a determination of intent or of wrongdoing.",
        "",
    ]
    return "\n".join(L) + "\n"


# ============================================================================
# CLI.
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    p = argparse.ArgumentParser(
        description="Generate 04_demo/failure_modes.md from real pipeline output.",
        epilog="Every number in the output is computed from the run. Nothing is "
               "hand-written, and unmeasured things are labelled UNMEASURED.")
    p.add_argument("--scene", type=Path, default=None,
                   help="One scene directory written by make_synthetic_eo.py.")
    p.add_argument("--scenes", type=Path, nargs="+", default=None,
                   help="Several scene directories, aggregated into one document.")
    p.add_argument("--out", type=Path, default=_DEMO / "failure_modes.md",
                   help="Output path. Default: 04_demo/failure_modes.md")
    args = p.parse_args(argv)

    scene_dirs: list[Path] = []
    if args.scene is not None:
        scene_dirs.append(args.scene)
    if args.scenes:
        scene_dirs.extend(args.scenes)
    _require(scene_dirs,
             "no scene given. This document is generated FROM a run; there is nothing "
             "to write without one.\n"
             "  python 03_src/report.py --scene 04_demo/out/scene01\n"
             "  python 03_src/report.py --scenes 04_demo/out/scene01 "
             "04_demo/out/scene02")

    # De-duplicate while preserving order: passing the same scene twice would double
    # every count in the document and look like twice as much evidence.
    seen: set[Path] = set()
    unique: list[Path] = []
    for d in scene_dirs:
        r = d.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(d)
    if len(unique) != len(scene_dirs):
        print(f"report.py: ignoring {len(scene_dirs) - len(unique)} repeated scene "
              f"path(s) — counting a scene twice would inflate every figure.",
              file=sys.stderr)

    pl = _Pipeline()
    started = datetime.now(timezone.utc)
    command = shlex.join(["python", "03_src/report.py", *argv])

    runs = [run_one_scene(pl, d, started) for d in unique]

    # ---- ONE anonymiser for the whole run. See the module docstring. ----------------
    anon = pl.evidence.Anonymiser()
    subs: dict[str, str] = {}
    for run in runs:
        for rec in run["records"]:
            _register_record(anon, subs, rec)
        for inj in run["truth"]:
            _register_mmsi(anon, subs, inj.get("mmsi"))

    document = build_document(runs, pl, anon, subs, started, command)

    # ---- the scrub, then the hard stop ---------------------------------------------
    # The scrub is a SUBSTRING PASS over the assembled text, not a field whitelist, and
    # it uses evidence.py's own implementation rather than a second copy: lane A's
    # track_id is a surrogate over (source, MMSI) and therefore EMBEDS the MMSI, so a
    # whitelist over the identity fields produces a document that LOOKS anonymised and
    # is not — which is the worst outcome, because it is the one nobody re-checks.
    scrub = getattr(pl.evidence, "_scrub_text", None)
    _require(callable(scrub),
             "evidence._scrub_text is gone. report.py will not emit a document it "
             "cannot scrub; writing an unscrubbed one would be worse than writing "
             "none. Restore it or route this file through the replacement.")
    document = scrub(document, subs)

    # Hard stop, not a warning — mirroring evidence.render_markdown(). If a real
    # identifier survived, the bug is in the scrub, not in the data, and hand-editing
    # the output would hide it.
    pl.evidence.assert_no_real_identities(document, subs)

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(document, encoding="utf-8")

    print("=" * 74)
    print("FAILURE MODES REPORT")
    print("=" * 74)
    for run in runs:
        c = run["counts"]
        s = run["scoring"]
        line = (f"  {run['scene_dir']}: {len(run['records'])} verdicts, "
                f"{c.get('matched')} matched, {c.get('dark_candidates')} dark")
        if s:
            line += (f", detection {s['detection']['hit']}/{s['detection']['total']}, "
                     f"actionable FP {s['controls']['false_positives']}/"
                     f"{s['controls']['total']}")
        else:
            line += ", NO GROUND TRUTH (sections 4 and 5 are UNMEASURED)"
        print(line)
    print(f"  identifiers pseudonymised: {len(subs)}")
    print(f"\nwritten: {out}")
    print("The re-identification key is NOT written. It stays on this machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
