"""
prioritizer.py — "send the boat HERE, and here is why." Lane D. CRITERION 2.

WHY THIS IS THE MOST IMPORTANT FILE IN THE PROJECT
The brief says it outright: building a detector is easy, building something that says
which contact is worth a scarce asset is the actual problem. A flat list of alerts,
sorted by severity, is what every team will produce. It is also useless to the customer
in the brief — a watch officer with ONE patrol boat and three thousand transits. That
officer does not need to know which contact is most suspicious. They need to know which
contact is most worth the only boat they have, which is a different question with a
different answer.

THE INSIGHT THAT SEPARATES THE TWO
Suspicion is a property of a contact. Priority is a property of a contact AND an asset
AND a clock. A 9-sigma spoofer 40 nautical miles away, opening the range at 18 knots,
that will be in another country's waters before the boat is halfway there, ranks BELOW a
5-sigma loiterer sitting over a cable route 6 miles out. The first is more suspicious.
The second is the one you can actually do something about.

So the score has an ACTIONABILITY term, and that term is what makes this a tasking tool
rather than an alarm panel.

THE WEIGHTS ARE THE ARGUMENT
contracts.PriorityScore carries component_scores AND component_weights, on purpose. A
bare scalar is not a defensible recommendation. When a judge or an officer asks "why is
this one first", the answer must be a row of numbers that sum to the score, not an
appeal to a model. Every component below is 0-1, every weight is stated, and the whole
thing is inspectable in the evidence record.

NO AI. Weighted arithmetic over measured quantities. That is not a limitation here — a
tasking recommendation that cannot explain its own ordering will not survive contact
with the person who has to justify sending the boat.

=================================================================================
THE INVARIANT THIS FILE NOW GUARANTEES, AND THE BUG THAT MADE IT NECESSARY
=================================================================================
    sum(component_scores[k] * component_weights[k] for k in component_scores) == score

Exactly, to within float rounding, for every PriorityScore this module emits. There is
a test for it.

It did not hold before. Deferred verdicts were damped with a `score *= 0.65` applied
AFTER the weighted sum, so a deferred SPOOF displayed components summing to 0.9970
beside a score of 0.6481 — a 35% discrepancy in the one place the operator is being
asked to trust the arithmetic. The fix is not cosmetic: the damping now applies to the
two components that are actually weakened by a deferral (verdict_severity and
confidence, both derived from the finding) BEFORE summing. The situational components
— proximity, behaviour, actionability — are properties of the water and the clock, and
a deferral does not make a cable route further away.

=================================================================================
CONFIDENCE MEANS CONFIDENCE IN THE STATED LABEL, WHICH IS NOT THE SAME AS URGENCY
=================================================================================
verdict.py defines confidence as confidence in whatever label it stated. So a MATCH at
0.95 means "we are 95% sure this vessel is FINE". Feeding that straight into a priority
score credited an innocent vessel with 0.1425 of urgency for being confidently
harmless. The confidence component is now multiplied by how threat-relevant the label
is: SPOOF and DARK count in full, UNKNOWN counts half (an unexamined vessel beside a
cable is worth a look precisely because it is unexamined), MATCH counts zero.

=================================================================================
THE ORDERING GUARANTEE
=================================================================================
An honest vessel sitting stationary over a cable route MUST stay visible and
recommendable — that is the brief's named scenario, and dropping it because its AIS
happens to be truthful would miss the exact case we are here to catch. But a SPOOF or
DARK contact at comparable range to the same asset MUST outrank it, because it has
everything the honest vessel has PLUS a documented discrepancy. That is asserted as a
property in the check script, not tuned by hand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from contracts import AisTrack, EoContact, PriorityScore, Verdict

RULESET_VERSION = "prioritizer/0.1.0"

KN_TO_MS = 0.514444
M_PER_NM = 1852.0


@dataclass(frozen=True)
class Infrastructure:
    """
    A thing on the seabed or the surface worth protecting.

    NOTE ON THE COORDINATES SHIPPED WITH THIS PROJECT: they are ILLUSTRATIVE positions
    in the Fehmarn Belt, not surveyed asset locations. Publishing precise routes of real
    critical infrastructure would be an own goal — it is a targeting aid. The demo needs
    plausible geometry, not accurate geometry, and the distinction is stated on the
    slide as well as here.
    """

    infra_id: str
    name: str
    lat_deg: float
    lon_deg: float
    kind: str = "cable"
    weight: float = 1.0


DEMO_INFRASTRUCTURE: tuple[Infrastructure, ...] = (
    Infrastructure("INFRA-A", "Illustrative interconnector route (synthetic)",
                   54.5800, 11.3200, "cable", 1.0),
    Infrastructure("INFRA-B", "Illustrative fibre landing approach (synthetic)",
                   54.6100, 11.2600, "cable", 0.9),
    Infrastructure("INFRA-C", "Illustrative offshore wind array (synthetic)",
                   54.5500, 11.4100, "windfarm", 0.7),
)


@dataclass(frozen=True)
class Asset:
    """
    The one patrol boat. Scarce by definition — that is the whole problem.

    AVAILABILITY IS PART OF THE PROBLEM, NOT A DETAIL. A tasking tool that ranks
    contacts without knowing whether the boat can sail is ranking hypotheticals. If the
    only hull is alongside for a crew change for the next two hours, the honest output
    is not a different ordering — it is the SAME ordering with every recommendation
    withdrawn and the reason stated, so the watch officer knows the list is advisory
    until the boat is free.

    available_in_min lets a boat be committed-but-returning: the wait is added to the
    transit time rather than treated as a hard yes/no, because "free in 20 minutes" and
    "free in 4 hours" are different answers to the same question.
    """

    asset_id: str = "PATROL-1"
    lat_deg: float = 54.5900
    lon_deg: float = 11.3000
    speed_kn: float = 22.0
    available: bool = True
    available_in_min: float = 0.0


@dataclass(frozen=True)
class Weights:
    """
    Sums to 1.0. Change these and the tool argues for something different — which is
    exactly why they are data and not buried in an expression.
    """

    # INFRASTRUCTURE PROXIMITY IS THE HEAVIEST SINGLE WEIGHT, and that is a deliberate
    # reading of the brief: the named scenario is loitering over a cable route, and the
    # customer's asset is scarce precisely because the thing being protected is fixed
    # and known. An earlier version had verdict_severity at 0.32 above proximity at
    # 0.22, which ranks by SUSPICION rather than by CONSEQUENCE — and suspicion is what
    # every other team's flat alert list already sorts on.
    infrastructure_proximity: float = 0.30
    verdict_severity: float = 0.26
    behaviour_anomaly: float = 0.18
    actionability: float = 0.14
    confidence: float = 0.12

    def as_dict(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    def total(self) -> float:
        return sum(self.as_dict().values())


DEFAULT_WEIGHTS = Weights()

# Label weighting. DARK and SPOOF are both findings, but they are not equally
# actionable: a spoofer has been positively identified and can be boarded on a
# documented discrepancy, whereas a dark contact may turn out to be a fishing skiff with
# no AIS obligation at all. UNKNOWN scores low by construction — deferring to a human is
# not the same as recommending the boat.
_LABEL_BASE: dict[str, float] = {
    "SPOOF": 1.00,
    "DARK": 0.72,
    "MATCH": 0.05,
    "UNKNOWN": 0.20,
}

# How much a deferred verdict weakens the FINDING-DERIVED components. Applied before
# the weighted sum so the published breakdown still adds up — see the module docstring.
# Not applied to proximity, behaviour or actionability: a deferral does not move a
# cable route, slow the vessel down, or make the boat faster.
DEFER_EVIDENCE_DAMPING = 0.65

# How much the confidence in a label bears on URGENCY. verdict.py's confidence is
# confidence in the STATED label, so a confident MATCH is confidence that nothing is
# wrong. UNKNOWN counts half: verdict.py's own handoff note says a confident UNKNOWN
# beside critical infrastructure is worth a look precisely because it is unexamined,
# and zeroing it here would bury exactly those contacts.
_THREAT_RELEVANT_CONFIDENCE: dict[str, float] = {
    "SPOOF": 1.00,
    "DARK": 1.00,
    "UNKNOWN": 0.50,
    "MATCH": 0.00,
}

_SUBTYPE_BONUS: dict[str, float] = {
    "position": 0.00,   # already the strongest base
    "identity": 0.00,
    "kinematic": -0.05,
}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance. Local, so this module needs no pyproj and can run on a Pi."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _proximity_score(distance_m: float, *, half_value_m: float = 3000.0) -> float:
    """
    1.0 on top of the asset, decaying smoothly with distance.

    WHY A SMOOTH DECAY AND NOT A RADIUS: a hard "within 2 km" ring means a vessel at
    2001 m scores zero and one at 1999 m scores full, and an operator who has watched a
    contact drift across that line loses confidence in the whole tool. The half-value
    form also has an honest interpretation: 3 km is where the concern halves.
    """
    return round(1.0 / (1.0 + (max(distance_m, 0.0) / half_value_m) ** 2), 4)


def _loiter_from_events(events: Sequence[object] | None,
                        nearest_infra_m: float | None) -> tuple[float, list[str]]:
    """
    Dwell-based loiter, from lane A's ais_trajectory.BehaviourEvent objects.

    WHY THIS AND NOT THE SPEED PROXY BELOW. A single AIS report showing 0.4 kn is a
    snapshot: a vessel slowing to pick up a pilot looks identical to one that has been
    sitting over a cable for ninety minutes. Loitering is a SPAN, and only a span can
    distinguish them. ais_trajectory.detect_loiter already does this properly with
    movingpandas.TrajectoryStopDetector, so this consumes its output rather than
    reinventing dwell detection badly — LIBRARIES.md, and it is lane A's file.

    Duck-typed on purpose: importing ais_trajectory would drag movingpandas, geopandas
    and shapely into a module that otherwise needs nothing but the standard library,
    and lane A went to some trouble to keep that stack lazily imported. Any object with
    the BehaviourEvent field names works.

    The three discounts are the honest half. A stop that ran off the edge of the
    analysed area, one in a coverage hole, or one where the vessel DECLARED itself
    moored are all stops that innocent traffic produces constantly.
    """
    if not events:
        return 0.0, []

    best = 0.0
    notes: list[str] = []
    for e in events:
        if getattr(e, "kind", None) != "loiter":
            continue
        duration_s = float(getattr(e, "duration_s", 0.0) or 0.0)
        # Saturating in duration: 30 minutes is already a deliberate stop, and four
        # hours is not eight times more suspicious than thirty minutes.
        score = min(duration_s / 1800.0, 1.0) * 0.70

        radius_m = getattr(e, "radius_m", None)
        if radius_m is not None and radius_m < 250.0:
            # A tight stop is station-keeping. A loose one is drifting.
            score += 0.15
            notes.append(f"held station within {radius_m:.0f} m")

        statuses = tuple(getattr(e, "claimed_nav_statuses", ()) or ())
        declared_stationary = any(
            s in ("Moored", "At anchor", "Aground", "Not under command")
            for s in statuses)
        if declared_stationary:
            # Not innocent by definition — a vessel CAN anchor over a cable — but it
            # declared the stop, which is what honest traffic does.
            score *= 0.45
            notes.append(f"declared {statuses[0]!r} during the stop")
        else:
            notes.append(
                f"stopped for {duration_s/60.0:.0f} min WITHOUT declaring moored or "
                "anchored")

        if getattr(e, "explained_by_area_exit", False):
            score *= 0.30
            notes.append("stop touches the edge of the analysed area — may be a "
                         "transit out of the box, not a stop")
        if getattr(e, "explained_by_sparse_coverage", False):
            score *= 0.40
            notes.append("stop sits in an AIS coverage hole — the silence may be the "
                         "receiver, not the vessel")

        if nearest_infra_m is not None and nearest_infra_m < 2000.0:
            score = min(score * 1.35, 1.0)
            notes.append("and it did so within 2 km of protected infrastructure")

        best = max(best, min(score, 1.0))

    return round(best, 4), notes


def _behaviour_score(track: AisTrack | None, contact: EoContact | None,
                     nearest_infra_m: float | None,
                     loiter_events: Sequence[object] | None = None
                     ) -> tuple[float, list[str]]:
    """
    Behaviour, not identity. The brief's named scenario is loitering over a cable route,
    so slow-or-stopped near infrastructure is the signal with the most weight.

    Returns the score and the human-readable reasons behind it, because a behaviour
    score with no explanation is the least defensible number in the whole stack.
    """
    notes: list[str] = []
    score = 0.0

    # Dwell evidence, when lane A's trajectory pass has run. It supersedes the
    # single-report proxy rather than adding to it: they measure the same thing, and
    # adding them would double-count one stop.
    dwell_score, dwell_notes = _loiter_from_events(loiter_events, nearest_infra_m)

    speed_ms: float | None = None
    if track is not None and track.claimed_sog_kn is not None:
        speed_ms = track.claimed_sog_kn * KN_TO_MS
    elif contact is not None and contact.observed_speed_ms is not None:
        speed_ms = contact.observed_speed_ms

    if speed_ms is not None and speed_ms < 1.0:
        score += 0.45
        notes.append("near-stationary (< 2 kn)")
    elif speed_ms is not None and speed_ms < 2.6:
        score += 0.25
        notes.append("loitering speed (< 5 kn)")

    if nearest_infra_m is not None and nearest_infra_m < 2000.0:
        score += 0.35
        notes.append("within 2 km of protected infrastructure")
        if speed_ms is not None and speed_ms < 2.6:
            # The combination is the concern, not either half. A slow vessel in open
            # water is fishing; a slow vessel over a cable is the scenario in the brief.
            score += 0.20
            notes.append("slow AND close — the loitering-over-cable pattern")

    if track is not None and track.claimed_nav_status:
        st = track.claimed_nav_status.lower()
        if "anchor" in st or "moored" in st:
            # DISCOUNTS, it does not add.
            #
            # This line previously added 0.15, on the reasoning that an anchored vessel
            # over a cable is "either a serious problem or a completely routine one,
            # and the tool must not pretend to know which". The reasoning is right and
            # the arithmetic contradicted it: adding score IS pretending to know, in
            # the incriminating direction. It also disagreed with the dwell path, where
            # the same fact discounts by 0.45 — the same vessel scored differently
            # depending on whether lane A's trajectory pass had run, which is the kind
            # of inconsistency that produces a contradictory demo.
            #
            # Declaring the stop is what honest traffic does. It stays visible in the
            # notes and in the ranking; it does not accrue suspicion for being candid.
            score *= 0.45
            notes.append(f"claims nav status '{track.claimed_nav_status}' — declared "
                         "the stop, which is what honest traffic does; discounted, "
                         "not dismissed")

    proxy = min(score, 1.0)
    if dwell_score > 0.0:
        # DWELL GOVERNS. It does not compete with the proxy and it does not get
        # max()'d against it.
        #
        # An earlier version took the stronger of the two, which inverted the whole
        # point and the 20-contact scene caught it: a vessel that declared itself AT
        # ANCHOR had its dwell correctly discounted to 0.52, and then the single-report
        # proxy returned 1.00 and won. The naive instrument overrode the careful one,
        # so every innocent explanation lane A had computed — declared moored, coverage
        # hole, area exit — was silently discarded whenever the snapshot happened to
        # look bad.
        #
        # The hierarchy is not a preference, it is what the two things measure. A
        # single AIS report showing 0.3 kn is a snapshot; loitering is a span. When the
        # span is available it is strictly the better evidence, including when it says
        # the stop was innocent.
        return round(dwell_score, 4), dwell_notes
    if not notes:
        notes.append("no loiter signal: no dwell events supplied and the vessel is "
                     "under way (dwell detection needs lane A's trajectory pass)")
    return round(proxy, 4), notes


def _actionability(track: AisTrack | None, contact: EoContact | None,
                   assets: Sequence[Asset]
                   ) -> tuple[float, float | None, float | None, str | None]:
    """
    Can the boat actually get there before the situation is moot?

    Returns (score, distance_km, time_to_intercept_min, asset_id).

    THE TERM THAT MAKES THIS A TASKING TOOL. Interception time uses a simple closing
    speed, not a pursuit curve — good enough to separate "twenty minutes away" from "two
    hours away", which is the distinction that changes the decision. Anything more
    precise would be false precision on top of a position that already carries hundreds
    of metres of uncertainty.

    ASSET AVAILABILITY ENTERS HERE, as time rather than as a flag. An unavailable hull
    is excluded; a hull that frees up in 20 minutes has that wait ADDED to its transit,
    so "free soon and close" beats "free now and far" only when it actually should. If
    no hull is available at all the term is zero and every recommendation is withdrawn
    upstream — the ranking survives, the tasking claim does not.

    Straight-line transit over water. It ignores land, traffic separation schemes and
    sea state, so it is a floor on the real time, never an estimate of it. Stated on the
    evidence card rather than dressed up.
    """
    usable = [a for a in assets if a.available]
    if not usable:
        return 0.0, None, None, None
    lat = lon = None
    if track is not None:
        lat, lon = track.claimed_lat_deg, track.claimed_lon_deg
    elif contact is not None and contact.observed_lat_deg is not None:
        lat, lon = contact.observed_lat_deg, contact.observed_lon_deg

    if lat is None or lon is None:
        # A bearing-only contact cannot be tasked precisely, but it is not worthless —
        # the boat can still run down the bearing. Mid score, and the evidence record
        # will carry no_range_estimate.
        return 0.45, None, None, usable[0].asset_id

    best: tuple[float, float, float, str] | None = None   # (total_min, dist_m, _, id)
    for a in usable:
        dist_m = _haversine_m(a.lat_deg, a.lon_deg, lat, lon)
        closing_ms = a.speed_kn * KN_TO_MS
        if closing_ms <= 0:
            continue
        total_min = (dist_m / closing_ms) / 60.0 + max(a.available_in_min, 0.0)
        if best is None or total_min < best[0]:
            best = (total_min, dist_m, 0.0, a.asset_id)
    if best is None:
        return 0.0, None, None, None

    total_min, dist_m, _, asset_id = best
    # Full marks inside 20 minutes, decaying to near zero by two hours.
    score = 1.0 / (1.0 + (total_min / 25.0) ** 2)
    return (round(score, 4), round(dist_m / 1000.0, 2),
            round(total_min, 1) if total_min else None, asset_id)


def score_one(
    verdict: Verdict,
    *,
    track: AisTrack | None,
    contact: EoContact | None,
    asset: Asset | None = None,
    assets: Sequence[Asset] | None = None,
    infrastructure: Sequence[Infrastructure],
    weights: Weights = DEFAULT_WEIGHTS,
    loiter_events: Sequence[object] | None = None,
) -> tuple[PriorityScore, list[str]]:
    """Score one verdict. Returns the PriorityScore and the behaviour notes.

    `asset` (singular) is kept for the existing callers in 04_demo/. Pass `assets` to
    model a fleet, or one boat with availability.

    GUARANTEED: sum(component_scores[k] * component_weights[k]) == score.
    """
    fleet: Sequence[Asset] = (assets if assets is not None
                              else (asset,) if asset is not None
                              else (Asset(),))
    # --- nearest infrastructure -------------------------------------------------
    nearest_m: float | None = None
    nearest_id: str | None = None
    nearest_weight = 1.0
    lat = lon = None
    if track is not None:
        lat, lon = track.claimed_lat_deg, track.claimed_lon_deg
    elif contact is not None and contact.observed_lat_deg is not None:
        lat, lon = contact.observed_lat_deg, contact.observed_lon_deg
    if lat is not None and lon is not None:
        for infra in infrastructure:
            d = _haversine_m(lat, lon, infra.lat_deg, infra.lon_deg)
            if nearest_m is None or d < nearest_m:
                nearest_m, nearest_id, nearest_weight = d, infra.infra_id, infra.weight

    # --- components -------------------------------------------------------------
    base = _LABEL_BASE.get(verdict.label, 0.2)
    base += _SUBTYPE_BONUS.get(verdict.spoof_subtype or "", 0.0)
    severity_c = round(max(0.0, min(1.0, base)), 4)

    infra_c = (_proximity_score(nearest_m) * nearest_weight) if nearest_m is not None else 0.0
    behaviour_c, notes = _behaviour_score(track, contact, nearest_m, loiter_events)

    # Confidence in the STATED LABEL is not urgency. See the module docstring.
    confidence_c = round(
        verdict.confidence * _THREAT_RELEVANT_CONFIDENCE.get(verdict.label, 0.0), 4)

    action_c, dist_km, tti, tasked_asset = _actionability(track, contact, fleet)
    if tasked_asset is None:
        notes.append("NO PATROL ASSET AVAILABLE — the ranking stands, but no tasking "
                     "recommendation can be made until a hull is free")

    # A deferral weakens the FINDING, not the water. Damping the two finding-derived
    # components BEFORE the sum is what keeps the published breakdown adding up to the
    # published score; the old post-hoc `score *= 0.65` did not.
    if verdict.defer_to_human:
        severity_c = round(severity_c * DEFER_EVIDENCE_DAMPING, 4)
        confidence_c = round(confidence_c * DEFER_EVIDENCE_DAMPING, 4)

    components = {
        "verdict_severity": severity_c,
        "infrastructure_proximity": round(infra_c, 4),
        "behaviour_anomaly": behaviour_c,
        "confidence": confidence_c,
        "actionability": action_c,
    }
    w = weights.as_dict()
    # THE INVARIANT. Nothing may touch `score` after this line except rounding —
    # anything else makes the published breakdown a lie. Deferral is already inside
    # the components above.
    score = round(max(0.0, min(1.0, sum(components[k] * w[k] for k in components))), 4)

    # The tasking recommendation is kept separate from the raw score on purpose: the
    # score is a ranking, the recommendation is an assertion, and an operator should be
    # able to disagree with the second while still trusting the first.
    # TWO ROUTES TO A RECOMMENDATION, because there are two reasons to send a boat.
    #
    #   1. A finding: the tool caught a discrepancy it can evidence.
    #   2. BEHAVIOUR: a vessel whose AIS is entirely honest, sitting stationary over a
    #      cable route. Nothing about it is a "detection" -- every claim matches every
    #      observation -- and it is precisely the scenario named in the brief.
    #
    # A recommender gated on the verdict label alone would rank that vessel highly and
    # then decline to recommend it, which is incoherent: first on the list, and the tool
    # will not say why you should go. Behaviour earns its own route.
    recommend = bool(
        tasked_asset is not None            # a hull has to exist and be free
        and not verdict.defer_to_human
        and (
            (score >= 0.55 and verdict.label in ("SPOOF", "DARK"))
            or (score >= 0.55 and behaviour_c >= 0.60)
        )
    )

    return PriorityScore(
        verdict_id=verdict.verdict_id,
        score=score,
        rank=1,                        # filled in by rank_all
        component_scores=components,
        component_weights=w,
        nearest_infrastructure_m=round(nearest_m, 1) if nearest_m is not None else None,
        nearest_infrastructure_id=nearest_id,
        nearest_asset_km=dist_km,
        time_to_intercept_min=tti,
        scarce_asset_recommended=recommend,
    ), notes


def rank_all(
    verdicts: Sequence[Verdict],
    tracks_by_assoc: dict[str, AisTrack | None],
    contacts_by_assoc: dict[str, EoContact | None],
    *,
    asset: Asset = Asset(),
    assets: Sequence[Asset] | None = None,
    infrastructure: Sequence[Infrastructure] = DEMO_INFRASTRUCTURE,
    weights: Weights = DEFAULT_WEIGHTS,
    loiter_events_by_assoc: dict[str, Sequence[object]] | None = None,
) -> tuple[list[PriorityScore], dict[str, list[str]]]:
    """
    Rank the whole scene. Returns the scores in rank order plus the behaviour notes.

    Ties break on confidence, then on verdict_id, so two runs of the same scene produce
    the same order. A demo whose ranking shuffles between runs looks broken even when
    the arithmetic is identical.
    """
    loiter_events_by_assoc = loiter_events_by_assoc or {}
    scored: list[tuple[PriorityScore, Verdict, list[str]]] = []
    for v in verdicts:
        ps, notes = score_one(
            v, track=tracks_by_assoc.get(v.association_id),
            contact=contacts_by_assoc.get(v.association_id),
            asset=asset, assets=assets, infrastructure=infrastructure,
            weights=weights,
            loiter_events=loiter_events_by_assoc.get(v.association_id))
        scored.append((ps, v, notes))

    scored.sort(key=lambda t: (-t[0].score, -t[1].confidence, t[1].verdict_id))

    out: list[PriorityScore] = []
    notes_by_verdict: dict[str, list[str]] = {}
    for i, (ps, v, notes) in enumerate(scored, start=1):
        out.append(ps.model_copy(update={"rank": i}))
        notes_by_verdict[v.verdict_id] = notes
    return out, notes_by_verdict


# =================================================================================
# Verification helpers. These exist so the guarantees in the module docstring are
# checkable by anything that consumes this file, not just by the check script.
# =================================================================================

def breakdown_sums_to_score(ps: PriorityScore, tol: float = 5e-4) -> bool:
    """The invariant: the published breakdown adds up to the published score.

    Exposed rather than kept in a test because lane D renders these numbers on the
    evidence card, and an operator who adds up the column and gets a different total
    has found a reason to stop trusting the whole tool."""
    total = sum(ps.component_scores[k] * ps.component_weights[k]
                for k in ps.component_scores)
    return abs(total - ps.score) <= tol


def explain(ps: PriorityScore, notes: Sequence[str] = ()) -> str:
    """The 'and here is why' half of criterion 2, as text an operator can read.

    Ordered by CONTRIBUTION, not by weight — the question being answered is "why is
    this one first", and the answer is whichever term actually moved it, which is not
    always the heaviest one."""
    rows = sorted(
        ((k, ps.component_scores[k], ps.component_weights[k],
          ps.component_scores[k] * ps.component_weights[k])
         for k in ps.component_scores),
        key=lambda r: r[3], reverse=True)
    lines = [f"rank {ps.rank}  score {ps.score:.4f}  ({ps.verdict_id})"]
    for name, sc, wt, contrib in rows:
        share = (contrib / ps.score * 100.0) if ps.score > 0 else 0.0
        lines.append(f"    {name:<26} {sc:.3f} x {wt:.2f} = {contrib:.4f}"
                     f"  ({share:4.1f}% of the score)")
    lines.append(f"    {'':<26} {'':>5}   {'':>4}   {ps.score:.4f}  TOTAL")
    if ps.nearest_infrastructure_id:
        lines.append(f"  nearest asset at risk : {ps.nearest_infrastructure_id} "
                     f"at {ps.nearest_infrastructure_m:.0f} m")
    if ps.time_to_intercept_min is not None:
        lines.append(f"  patrol boat           : {ps.nearest_asset_km:.1f} km, "
                     f"{ps.time_to_intercept_min:.0f} min to intercept")
    lines.append(f"  send the boat         : {ps.scarce_asset_recommended}")
    for n in notes:
        lines.append(f"  - {n}")
    return "\n".join(lines)
