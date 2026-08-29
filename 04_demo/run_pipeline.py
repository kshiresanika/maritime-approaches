"""
run_pipeline.py — end to end: contacts + claims in, ranked evidence out.

This is the whole product in one call, and it is deliberately thin. Every decision lives
in the module that owns it; this file only sequences them and hands the result to
whoever asked -- the CLI, or app.py serving a browser.

    EoContact + AisTrack
        -> association.associate()      which observation is which claim
        -> consistency.check_all()      how far apart is each matched pair, in sigmas
        -> verdict.decide_all()         MATCH / DARK / SPOOF / UNKNOWN + confidence
        -> prioritizer.rank_all()       which one is worth the one patrol boat
        -> evidence.build_record()      the case file
        -> score_scene()                and how often were we WRONG

That last step is the one that matters most and the one nobody builds. A pipeline that
cannot state its own false-positive rate is not evidence-grade, it is a demo. Because
the D0 harness knows the ground truth by construction, this file can print the number
out loud -- including, specifically, how often the two controls stayed silent when they
were supposed to.

    python 04_demo/run_pipeline.py --scene 04_demo/out/scene01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parents[1] / "03_src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import association                    # noqa: E402
import consistency                    # noqa: E402
import evidence as evidence_mod       # noqa: E402
import prioritizer                    # noqa: E402
import verdict as verdict_mod         # noqa: E402
from contracts import AisTrack, EoContact  # noqa: E402


# ============================================================================
# Loading.
# ============================================================================

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_scene(scene_dir: Path) -> dict[str, Any]:
    """
    Read a scene written by make_synthetic_eo.py.

    NOTE ON ground_truth.jsonl: it is loaded HERE, in the runner, and never passed into
    any module under 03_src. The scoring function below is the only consumer. If a
    pipeline module ever needs the answer key to work, the pipeline is not measuring
    anything.
    """
    manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
    tracks = [AisTrack(**r) for r in _read_jsonl(scene_dir / "ais_tracks.jsonl")]
    contacts = [EoContact(**r) for r in _read_jsonl(scene_dir / "eo_contacts.jsonl")]
    truth = _read_jsonl(scene_dir / "ground_truth.jsonl")
    return {"manifest": manifest, "tracks": tracks, "contacts": contacts,
            "ground_truth": truth}


# ============================================================================
# The pipeline.
# ============================================================================

def run_scene(
    tracks: list[AisTrack],
    contacts: list[EoContact],
    pose: dict[str, Any],
    *,
    now: datetime | None = None,
    ais_coverage_confidence: float | None = None,
) -> dict[str, Any]:
    """Run every stage. Returns plain dicts, ready for JSON or a template."""
    associations = association.associate(
        contacts, tracks,
        camera_lat_deg=pose["lat_deg"],
        camera_lon_deg=pose["lon_deg"],
        boresight_deg_true=pose["boresight_deg_true"],
        fov_half_angle_deg=pose.get("fov_half_angle_deg", pose["hfov_deg"] / 2.0),
        max_range_m=pose["max_range_m"],
    )

    tracks_by_id = {t.track_id: t for t in tracks}
    contacts_by_id = {c.contact_id: c for c in contacts}

    # The camera position is passed in so consistency.py can run the CROSS-RANGE
    # position test rather than the pooled one. Without it the check falls back to
    # comparing a total gap against a sigma dominated by monocular range error, which
    # the harness showed cannot detect a real position spoof.
    # check_all returns dict[association_id -> ConsistencyResult]. The RESULT, not a
    # bare mismatch list: verdict.py caps MATCH confidence by evidential coverage, which
    # it computes from agreements + mismatches + uncomparable. Hand it only the
    # mismatches and every honest vessel reads coverage 0.0 and comes back UNKNOWN.
    results_by_assoc = consistency.check_all(
        associations, tracks_by_id, contacts_by_id,
        camera=(pose["lat_deg"], pose["lon_deg"]))

    # decide_all takes (associations, results) and returns
    # list[tuple[Verdict, VerdictExplanation]]. The explanation half is lane C internal
    # and is DROPPED HERE DELIBERATELY -- verdict.py:302 states it must not cross into
    # lane D, and discarding it at the seam is what enforces that. Everything lane D
    # legitimately needs from it comes back through limitation_strings() below, as
    # list[str], which is what EvidenceRecord.limitations is declared as.
    # HOW CONFIDENT ARE WE THAT AIS COVERAGE HERE IS COMPLETE? It gates the DARK path:
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
    verdicts = [v for v, _explanation in decided]

    assoc_by_id = {a.association_id: a for a in associations}
    tracks_by_assoc = {
        a.association_id: (tracks_by_id.get(a.track_id) if a.track_id else None)
        for a in associations}
    contacts_by_assoc = {
        a.association_id: (contacts_by_id.get(a.contact_id) if a.contact_id else None)
        for a in associations}

    priorities, notes = prioritizer.rank_all(
        verdicts, tracks_by_assoc, contacts_by_assoc)
    prio_by_verdict = {p.verdict_id: p for p in priorities}
    verdict_by_id = {v.verdict_id: v for v in verdicts}

    records = []
    for p in priorities:                       # already in rank order
        v = verdict_by_id[p.verdict_id]
        a = assoc_by_id[v.association_id]
        result = results_by_assoc.get(v.association_id)
        records.append(evidence_mod.build_record(
            verdict=v, association=a,
            mismatches=result.mismatches if result else [],
            track=tracks_by_assoc.get(v.association_id),
            contact=contacts_by_assoc.get(v.association_id),
            priority=p, behaviour_notes=notes.get(v.verdict_id),
            # CRITERION 4'S HONESTY CHANNEL, previously severed. Without this the record
            # declares it has no stated calibration basis -- which was true, because
            # limitation_strings() existed and nothing called it. It is what carries
            # "these dimensions could NOT be checked" into the case file. A report
            # listing four agreeing dimensions while silently having compared two is not
            # evidence-grade, it is a misleading one.
            consistency_limitations=verdict_mod.limitation_strings(result, v),
            now=now))

    return {
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
        "counts": {
            "tracks": len(tracks), "contacts": len(contacts),
            "associations": len(associations),
            "matched": sum(1 for a in associations if a.track_id and a.contact_id),
            "dark_candidates": sum(1 for a in associations if a.contact_id and not a.track_id),
            "unobserved_claims": sum(1 for a in associations if a.track_id and not a.contact_id),
        },
        "records": [r.model_dump(mode="json") for r in records],
    }


# ============================================================================
# Scoring. The number nobody else will have.
# ============================================================================

def score_scene(result: dict[str, Any], truth: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compare the verdicts against the injected faults.

    TWO DIFFERENT QUESTIONS, KEPT APART ON PURPOSE:

      detection  -- of the faults we injected, how many did the pipeline label
                    correctly? Optimistic by nature: we chose faults it could see.

      controls   -- of the cases that were deliberately NOT faults (coverage holes,
                    missed detections, clutter), how many did it wrongly flag? This is
                    the number that decides whether the tool is usable, because a watch
                    officer abandons a tool that cries wolf long before they notice it
                    missed something.

    Quote both. Quoting only the first is how demos mislead.
    """
    by_contact: dict[str, dict] = {}
    by_track: dict[str, dict] = {}
    for rec in result["records"]:
        v = rec["verdict"]
        c = (rec.get("eo_contact") or {}).get("contact_id")
        t = (rec.get("ais_track") or {}).get("track_id")
        if c:
            by_contact[c] = v
        if t:
            by_track[t] = v

    detect_hit = detect_total = 0
    control_fired = control_total = control_labelled = 0
    rows = []

    for inj in truth:
        kind = inj["kind"]
        v = None
        if inj.get("contact_id"):
            v = by_contact.get(inj["contact_id"])
        if v is None and inj.get("track_id"):
            v = by_track.get(inj["track_id"])
        got = v["label"] if v else "NO_RECORD"
        got_sub = v.get("spoof_subtype") if v else None
        conf = v.get("confidence") if v else None

        if inj.get("must_not_fire"):
            control_total += 1
            # WHAT COUNTS AS "FIRING", AND WHY THE DEFINITION IS STATED RATHER THAN
            # ASSUMED. A control fails when the tool would put the case in front of an
            # operator as an ACTIONABLE finding. A verdict carrying defer_to_human is
            # not that -- it is the tool saying "I cannot tell, a human should look",
            # which is the behaviour criterion 4 asks for.
            #
            # Both numbers are reported. Counting only the actionable ones is the
            # defensible measure; counting raw labels as well is what stops that
            # definition from being self-serving. If the two diverge a lot, say so.
            labelled = got in ("DARK", "SPOOF")
            deferred = bool(v.get("defer_to_human")) if v else False
            fired = labelled and not deferred
            if fired:
                control_fired += 1
            if labelled:
                control_labelled += 1
            rows.append({"kind": kind, "expected": "silence", "got": got,
                         "deferred": deferred, "ok": not fired, "confidence": conf})
        else:
            detect_total += 1
            ok = got == inj["expected_label"]
            if ok and inj.get("expected_spoof_subtype"):
                ok = got_sub == inj["expected_spoof_subtype"]
            if ok:
                detect_hit += 1
            rows.append({"kind": kind, "expected": inj["expected_label"],
                         "got": got, "subtype": got_sub, "ok": ok, "confidence": conf})

    return {
        "detection": {"hit": detect_hit, "total": detect_total,
                      "rate": round(detect_hit / detect_total, 3) if detect_total else None},
        "controls": {"false_positives": control_fired,
                     "labelled_but_deferred": control_labelled - control_fired,
                     "total": control_total,
                     "false_positive_rate": round(control_fired / control_total, 3)
                     if control_total else None},
        "rows": rows,
    }


# ============================================================================
# CLI.
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the full pipeline on a scene.")
    p.add_argument("--scene", required=True, type=Path)
    p.add_argument("--out", type=Path, default=None,
                   help="Where to write ranked.json. Default: <scene>/ranked.json")
    args = p.parse_args(argv)

    scene = load_scene(args.scene)
    result = run_scene(scene["tracks"], scene["contacts"], scene["manifest"]["pose"])
    scoring = score_scene(result, scene["ground_truth"]) if scene["ground_truth"] else None
    result["scoring"] = scoring
    result["source_manifest"] = scene["manifest"]

    out = args.out or (args.scene / "ranked.json")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    c = result["counts"]
    print("=" * 74)
    print("PIPELINE RUN")
    print("=" * 74)
    print(f"tracks {c['tracks']}  contacts {c['contacts']}  ->  "
          f"matched {c['matched']}, dark candidates {c['dark_candidates']}, "
          f"unobserved claims {c['unobserved_claims']}")
    print()
    print("TOP 5 BY PRIORITY:")
    for rec in result["records"][:5]:
        v, pr = rec["verdict"], rec["priority"]
        ident = (rec["identity_claimed"].get("claimed_mmsi") or "no AIS claim")
        flag = "  [DEFER]" if v["defer_to_human"] else ""
        rec_flag = "  <- SEND THE BOAT" if pr["scarce_asset_recommended"] else ""
        print(f"  {pr['rank']}. {v['label']:<8} conf {v['confidence']:.2f}  "
              f"prio {pr['score']:.2f}  {ident}{flag}{rec_flag}")
    if scoring:
        d, k = scoring["detection"], scoring["controls"]
        print()
        print(f"DETECTION : {d['hit']}/{d['total']} injected faults labelled correctly")
        print(f"CONTROLS  : {k['false_positives']}/{k['total']} ACTIONABLE false "
              f"positives on cases that must stay silent"
              + (f"  (+{k['labelled_but_deferred']} labelled but deferred to a human)"
                 if k['labelled_but_deferred'] else ""))
        print()
        print("  The second number is the one to quote. Anything can raise an alert.")
        for r in scoring["rows"]:
            mark = "ok " if r["ok"] else "XX "
            extra = "  [deferred]" if r.get("deferred") else ""
            print(f"    {mark}{r['kind']:<18} expected {str(r['expected']):<8} "
                  f"got {r['got']}{extra}")
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
