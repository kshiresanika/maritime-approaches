"""
99_scratch/lane_c_prioritizer_check.py — self-check for prioritizer.py. CRITERION 2.

WHAT IS BEING DEMONSTRATED
  A  20 synthetic contacts -> one ranked list, with the top 3 broken out factor by
     factor. That breakdown IS criterion 2: a ranked list without reasons fails it.
  B  THE INVARIANT: for every score, sum(components x weights) == score. Exactly.
  C  THE ORDERING GUARANTEE: an honest vessel loitering over a cable stays visible and
     recommendable, but never outranks a SPOOF or DARK contact at the same asset.
  D  A confident MATCH earns NO urgency from its confidence.
  E  ASSET AVAILABILITY: with no hull free, the ranking survives and every tasking
     recommendation is withdrawn.
  F  DWELL beats the single-report speed proxy, and the three innocent explanations
     (area exit, coverage hole, declared moored) discount it.
  G  DETERMINISM: same scene twice, same order.

SYNTHETIC IDENTITIES ONLY — every track is built through fixtures.make_ais_track, so
MMSIs stay in the unissued 999xxxxxx range and names stay SYNTH-prefixed. The
infrastructure positions are ILLUSTRATIVE, not surveyed: publishing real cable routes
would be a targeting aid.

Run:  source .venv/bin/activate && python 99_scratch/lane_c_prioritizer_check.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "03_src", _ROOT / "tests", Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import prioritizer as P                                    # noqa: E402
from contracts import Verdict                              # noqa: E402
from fixtures import T0, make_ais_track                    # noqa: E402

_failures: list[str] = []


def _check(ok: bool, msg: str) -> None:
    if not ok:
        _failures.append(msg)


@dataclass(frozen=True)
class FakeLoiter:
    """Duck-typed stand-in for ais_trajectory.BehaviourEvent.

    prioritizer._loiter_from_events reads BehaviourEvent by attribute name and never
    imports ais_trajectory — that keeps movingpandas/geopandas out of a module which
    otherwise needs only the standard library, and it is what lets this check run
    without the geo stack installed. Field names match lane A's dataclass exactly, so
    the real object works unchanged."""

    kind: str = "loiter"
    duration_s: float = 3600.0
    radius_m: float | None = 180.0
    claimed_nav_statuses: tuple[str, ...] = ()
    explained_by_area_exit: bool = False
    explained_by_sparse_coverage: bool = False


def _verdict(vid: str, label: str, conf: float, *, defer: bool = False,
             subtype: str | None = None) -> Verdict:
    return Verdict(
        verdict_id=vid, association_id=f"assoc-{vid}", label=label, confidence=conf,
        mismatch_ids=[], decided_at_utc=T0, spoof_subtype=subtype,
        defer_to_human=defer, defer_reasons=["low_confidence"] if defer else [],
        ruleset_version="check")


# --------------------------------------------------------------------------------
# The 20-contact scene.
#
# Composition is deliberate, not random: a scene of twenty plausible spoofers would
# flatter the ranker enormously. Real traffic in the Fehmarn Belt is overwhelmingly
# innocent, so this is mostly innocent, and the ranker's job is to find the few that
# are not — and to put them above the many that merely LOOK busy.
# --------------------------------------------------------------------------------

_CABLE_A = (54.5800, 11.3200)     # INFRA-A in prioritizer.DEMO_INFRASTRUCTURE
_CABLE_B = (54.6100, 11.2600)     # INFRA-B


def build_scene():
    """20 contacts. Returns (verdicts, tracks_by_assoc, loiter_by_assoc, expectations)."""
    rows: list[tuple[Verdict, tuple[float, float], float, object | None, str]] = []

    def add(vid, label, conf, lat, lon, sog, *, defer=False, subtype=None,
            loiter=None, why=""):
        rows.append((_verdict(vid, label, conf, defer=defer, subtype=subtype),
                     (lat, lon), sog, loiter, why))

    # --- the cases that should surface -------------------------------------------
    add("V01-spoof-on-cable", "SPOOF", 0.97, _CABLE_A[0] + 0.0008, _CABLE_A[1], 0.4,
        subtype="identity", loiter=FakeLoiter(duration_s=5400.0, radius_m=120.0),
        why="identity spoof, stopped 90 min on the cable — the brief's scenario")
    add("V02-dark-on-cable", "DARK", 0.90, _CABLE_A[0] - 0.0010, _CABLE_A[1] + 0.0004,
        0.6, loiter=FakeLoiter(duration_s=2700.0, radius_m=200.0),
        why="dark vessel loitering on the same cable")
    add("V03-spoof-deferred-cable", "SPOOF", 0.72, _CABLE_A[0] + 0.0012,
        _CABLE_A[1] + 0.0009, 0.5, defer=True, subtype="identity",
        loiter=FakeLoiter(duration_s=3000.0),
        why="same picture, evidence not strong enough to state")
    add("V04-honest-loiter-cable", "MATCH", 0.95, _CABLE_A[0], _CABLE_A[1], 0.3,
        loiter=FakeLoiter(duration_s=4800.0, radius_m=150.0),
        why="AIS entirely honest, but stationary over the cable for 80 min")
    add("V05-spoof-far", "SPOOF", 0.96, 54.7600, 11.7300, 16.0, subtype="identity",
        why="strong spoof, but 20+ km away and opening the range")
    add("V06-dark-midfield", "DARK", 0.84, 54.6300, 11.3900, 8.0,
        why="dark contact under way, nothing near it")
    add("V07-unknown-on-cable", "UNKNOWN", 0.95, _CABLE_B[0] + 0.0006, _CABLE_B[1],
        1.1, defer=True, loiter=FakeLoiter(duration_s=2100.0),
        why="unexamined vessel beside INFRA-B — worth a look BECAUSE unexamined")

    # --- innocent traffic that must NOT crowd the top -----------------------------
    add("V08-ferry-transit", "MATCH", 0.94, 54.5600, 11.2400, 15.5, why="ferry, on passage")
    add("V09-cargo-transit", "MATCH", 0.93, 54.6000, 11.3500, 12.0, why="cargo, on passage")
    add("V10-tanker-transit", "MATCH", 0.92, 54.5300, 11.3800, 11.0, why="tanker, on passage")
    add("V11-moored-declared", "MATCH", 0.91, _CABLE_B[0], _CABLE_B[1] + 0.0005, 0.1,
        loiter=FakeLoiter(duration_s=7200.0, claimed_nav_statuses=("At anchor",)),
        why="anchored beside INFRA-B and SAYS SO — discounted, not ignored")
    add("V12-coverage-hole-stop", "MATCH", 0.90, 54.7100, 11.6000, 0.5,
        loiter=FakeLoiter(duration_s=3600.0, explained_by_sparse_coverage=True),
        why="apparent stop inside an AIS coverage hole")
    add("V13-area-edge-stop", "MATCH", 0.89, 54.4200, 11.0500, 0.4,
        loiter=FakeLoiter(duration_s=3600.0, explained_by_area_exit=True),
        why="apparent stop at the edge of the analysed box")
    add("V14-fishing-slow", "MATCH", 0.88, 54.6700, 11.5200, 2.2,
        why="fishing vessel working, slow but nowhere near infrastructure")
    for i, (lat, lon, sog) in enumerate(
            [(54.50, 11.15, 13.0), (54.52, 11.55, 14.0), (54.66, 11.20, 10.5),
             (54.48, 11.62, 9.0), (54.72, 11.45, 12.5), (54.44, 11.28, 11.5)], start=15):
        add(f"V{i}-transit", "MATCH", 0.90, lat, lon, sog, why="routine transit")

    verdicts = [r[0] for r in rows]
    tracks, loiters, why = {}, {}, {}
    for v, (lat, lon), sog, loiter, reason in rows:
        tracks[v.association_id] = make_ais_track(
            seed=abs(hash(v.verdict_id)) % 9999,
            claimed_lat_deg=lat, claimed_lon_deg=lon, claimed_sog_kn=sog,
            claimed_nav_status=("At anchor" if loiter is not None
                                and loiter.claimed_nav_statuses else
                                "Under way using engine"))
        if loiter is not None:
            loiters[v.association_id] = [loiter]
        why[v.verdict_id] = reason
    return verdicts, tracks, loiters, why


def main() -> int:
    verdicts, tracks, loiters, why = build_scene()
    contacts = {k: None for k in tracks}

    scores, notes = P.rank_all(verdicts, tracks, contacts,
                               loiter_events_by_assoc=loiters)

    print("=" * 78)
    print(f"A. RANKED LIST — {len(scores)} contacts, one patrol boat")
    print(f"   weights: " + ", ".join(f"{k} {v:.2f}"
                                      for k, v in P.DEFAULT_WEIGHTS.as_dict().items()))
    print("=" * 78)
    print(f"  {'#':>2}  {'contact':<26} {'score':>6}  {'label':<8} {'infra_m':>8}  "
          f"{'tti':>5}  boat")
    print("  " + "-" * 74)
    for ps in scores:
        v = next(x for x in verdicts if x.verdict_id == ps.verdict_id)
        infra = f"{ps.nearest_infrastructure_m:.0f}" if ps.nearest_infrastructure_m is not None else "-"
        tti = f"{ps.time_to_intercept_min:.0f}" if ps.time_to_intercept_min is not None else "-"
        print(f"  {ps.rank:>2}  {ps.verdict_id:<26} {ps.score:>6.4f}  {v.label:<8} "
              f"{infra:>8}  {tti:>5}  {'YES' if ps.scarce_asset_recommended else '.'}")

    print()
    print("=" * 78)
    print("   TOP 3 — WHY, factor by factor. This is criterion 2.")
    print("=" * 78)
    for ps in scores[:3]:
        print()
        print(P.explain(ps, notes.get(ps.verdict_id, [])))
        print(f"  scene note            : {why[ps.verdict_id]}")

    # ---- B. the invariant -------------------------------------------------------
    bad = [ps.verdict_id for ps in scores if not P.breakdown_sums_to_score(ps)]
    print()
    print("=" * 78)
    print(f"B. INVARIANT — components x weights == score, for all {len(scores)}: "
          f"{'OK' if not bad else 'BROKEN for ' + ', '.join(bad)}")
    _check(not bad, f"the published breakdown does not sum to the score for: {bad}")

    # ---- C. the ordering guarantee ----------------------------------------------
    by_id = {ps.verdict_id: ps for ps in scores}
    honest = by_id["V04-honest-loiter-cable"]
    findings_same_cable = [by_id["V01-spoof-on-cable"], by_id["V02-dark-on-cable"],
                           by_id["V03-spoof-deferred-cable"]]
    print()
    print("C. ORDERING GUARANTEE — a finding at the same cable outranks the honest loiterer")
    print(f"   honest loiterer         rank {honest.rank}  score {honest.score:.4f}  "
          f"recommended={honest.scarce_asset_recommended}")
    for f in findings_same_cable:
        ok = f.rank < honest.rank
        print(f"   {f.verdict_id:<26} rank {f.rank}  score {f.score:.4f}  "
              f"{'OK' if ok else 'VIOLATION'}")
        _check(ok, f"{f.verdict_id} (a finding at the same cable) ranked BELOW the "
                   f"honest loiterer — it has everything the honest vessel has plus a "
                   f"documented discrepancy")
    _check(honest.rank <= 8,
           f"the honest loiterer fell to rank {honest.rank} — loitering over a cable "
           "is the brief's named scenario and must stay visible even when the AIS is "
           "truthful")

    # ---- D. a confident MATCH earns no urgency from confidence -------------------
    print()
    print("D. CONFIDENCE — a confident MATCH must earn NO urgency from being confidently fine")
    ferry = by_id["V08-ferry-transit"]
    print(f"   V08 ferry, MATCH at 0.94: confidence component = "
          f"{ferry.component_scores['confidence']:.4f}")
    _check(ferry.component_scores["confidence"] == 0.0,
           f"a MATCH contributed {ferry.component_scores['confidence']} of confidence "
           "to its own priority — it is being credited for being harmless")
    unk = by_id["V07-unknown-on-cable"]
    print(f"   V07 UNKNOWN at 0.95 (deferred): confidence component = "
          f"{unk.component_scores['confidence']:.4f}  (half-weighted, then defer-damped)")
    _check(unk.component_scores["confidence"] > 0.0,
           "an UNKNOWN beside infrastructure earned zero confidence weight — an "
           "unexamined vessel is worth a look precisely because it is unexamined")

    # ---- E. asset availability ---------------------------------------------------
    busy = P.Asset(available=False)
    s_busy, _ = P.rank_all(verdicts, tracks, contacts, assets=[busy],
                           loiter_events_by_assoc=loiters)
    n_rec = sum(1 for ps in s_busy if ps.scarce_asset_recommended)
    print()
    print("E. ASSET AVAILABILITY — the only hull is alongside")
    print(f"   contacts still ranked: {len(s_busy)}   tasking recommendations: {n_rec}")
    _check(len(s_busy) == len(scores),
           "contacts disappeared from the ranking when the boat became unavailable — "
           "the ordering is advice and must survive the boat being busy")
    _check(n_rec == 0,
           f"{n_rec} contacts were still recommended for a boat that cannot sail")

    soon = P.Asset(available=True, available_in_min=45.0)
    s_soon, _ = P.rank_all(verdicts, tracks, contacts, assets=[soon],
                           loiter_events_by_assoc=loiters)
    top_now = scores[0].time_to_intercept_min
    top_soon = next(ps for ps in s_soon if ps.verdict_id == scores[0].verdict_id
                    ).time_to_intercept_min
    print(f"   boat free in 45 min: top contact's time-to-intercept "
          f"{top_now:.0f} -> {top_soon:.0f} min")
    _check(top_soon > top_now,
           "a 45-minute wait did not lengthen the time to intercept — availability is "
           "not reaching the actionability term")

    # ---- F. dwell discounts ------------------------------------------------------
    print()
    print("F. DWELL — the three innocent explanations must discount a stop")
    base = by_id["V01-spoof-on-cable"].component_scores["behaviour_anomaly"]
    for vid, label in (("V11-moored-declared", "declared at anchor"),
                       ("V12-coverage-hole-stop", "AIS coverage hole"),
                       ("V13-area-edge-stop", "edge of analysed area")):
        b = by_id[vid].component_scores["behaviour_anomaly"]
        print(f"   {label:<24} behaviour {b:.4f}   (undiscounted reference {base:.4f})")
        _check(b < base,
               f"{vid} ({label}) scored behaviour {b} — an innocent explanation did "
               "not discount the stop")

    # ---- G. determinism ----------------------------------------------------------
    again, _ = P.rank_all(verdicts, tracks, contacts, loiter_events_by_assoc=loiters)
    same = [p.verdict_id for p in scores] == [p.verdict_id for p in again]
    print()
    print(f"G. DETERMINISM — same scene twice, same order: {same}")
    _check(same, "the ranking is not stable between runs")

    print()
    print("=" * 78)
    if _failures:
        print("FAIL")
        for f in _failures:
            print("  -", f)
        return 1
    print("PASS — ranked list, breakdowns, invariant, ordering guarantee, availability")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
