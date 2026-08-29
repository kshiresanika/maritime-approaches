"""
verdict.py — MATCH / DARK / SPOOF / UNKNOWN, with a calibrated confidence. Lane C/D.

WHERE THIS SITS
association.py said what pairs. consistency.py said how far apart the pair is, in
sigmas, one dimension at a time. This module is the only place that turns those numbers
into a word an operator will act on, and the only place that decides to hand the
decision back to a human.

THE DETERMINISTIC / LLM SPLIT, WHICH JUDGES WILL LOOK FOR
Everything here is arithmetic and rules. No model is called, and contracts.Verdict has
deliberately nowhere to put model-generated text — there is a test asserting that. The
LLM writes the rationale sentence onto EvidenceRecord.rationale_text, attributed to a
named model, and it writes nothing else. A system where the model produces the verdict
cannot state why it decided, cannot be version-pinned, and cannot be defended when a
vessel is released and someone asks how the conclusion was reached.

THE HARDEST DISTINCTION IN THIS FILE, AND THE ONE MOST TEAMS GET WRONG
An in-view AIS claim with no observation has two completely different causes:

    (a) the vessel is broadcasting a position it is not at  -> POSITION SPOOF
    (b) the detector missed it, or a nearer hull occluded it -> NOTHING. A gap.

They look identical from the claim's side. Calling every (b) a spoof floods the operator
and destroys the tool's credibility on first contact with reality.

The discriminator is CORROBORATION, and it is geometric rather than statistical: a real
position spoof displaces the claim while leaving the hull where it was, so it produces
TWO orphans — an unmatched claim over here, and an unmatched contact over there. A
missed detection produces only ONE, the unmatched claim, with no spare hull anywhere.
So: an unmatched in-view claim is only ever a SPOOF candidate when there is an unmatched
contact within a plausible displacement of it. Otherwise it is a gap, it is labelled
UNKNOWN, and it defers.

That single rule is what keeps the false-positive controls in the D0 harness silent, and
it is the thing to demo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from contracts import (
    AisTrack,
    Association,
    DeferReason,
    EoContact,
    Mismatch,
    SpoofSubtype,
    Verdict,
    VerdictLabel,
)

RULESET_VERSION = "verdict/0.1.0"


@dataclass(frozen=True)
class VerdictPolicy:
    """
    Every threshold that turns a number into a word. One place, version-pinned, printed
    into the evidence record. Thresholds move during a hackathon; a verdict that cannot
    say which thresholds produced it is not reproducible and therefore not evidence.
    """

    # A pair needs at least one mismatch this significant before SPOOF is on the table.
    spoof_sigma: float = 3.0
    # ...or this many DIFFERENT dimensions disagreeing at that significance. See decide().
    corroborating_dimensions: int = 2
    # ...and the mismatch must be at least this severe. A pile of "minor" findings is a
    # noisy sensor, not a deception.
    spoof_requires_severity: tuple[str, ...] = ("major", "critical")

    # Below this, hand it to a human whatever the label says.
    defer_below_confidence: float = 0.55

    # An observation this weak cannot carry any accusation.
    min_observation_confidence: float = 0.35

    # Orphan-pair corroboration for the position-spoof case, in metres. Wider than a
    # typical spoof offset because the displaced claim and the real hull are measured by
    # different means with different errors.
    orphan_pair_max_gap_m: float = 4000.0

    # DARK confidence shaping.
    dark_min_track_frames: int = 3
    # HARD precondition for the DARK label itself, not merely a confidence penalty.
    # Below this, the output is UNKNOWN: a blob is not a vessel.
    dark_requires_frames: int = 3
    # An area with almost no AIS traffic makes DARK far weaker: a gap there is likelier
    # to be receiver geometry than evasion. Measured on the real data — the Bornholm
    # slice has 2.5x sparser tracks than Fehmarn purely from basestation coverage.
    sparse_coverage_track_count: int = 5


DEFAULT_POLICY = VerdictPolicy()

_SUBTYPE_BY_DIMENSION: dict[str, SpoofSubtype] = {
    "position": "position",
    "length": "identity",
    "class": "identity",
    "width": "identity",
    "heading": "identity",
    "cog": "kinematic",
    "speed": "kinematic",
}


@dataclass
class SceneContext:
    """
    Facts about the whole scene that a single association cannot know.

    WHY A CONTEXT OBJECT RATHER THAN GLOBALS: the same association must produce the same
    verdict when replayed, and a verdict that depends on hidden state cannot be
    reproduced from an evidence record. Everything that influences a decision is either
    on the Association, on this object, or in the policy — and all three are recorded.
    """

    n_tracks_in_view: int = 0
    unmatched_contact_positions: dict[str, tuple[float, float] | None] = None
    orphan_corroboration: dict[str, str] = None   # track_id -> corroborating contact_id

    def __post_init__(self) -> None:
        if self.unmatched_contact_positions is None:
            self.unmatched_contact_positions = {}
        if self.orphan_corroboration is None:
            self.orphan_corroboration = {}

    @property
    def sparse_coverage(self) -> bool:
        return self.n_tracks_in_view < DEFAULT_POLICY.sparse_coverage_track_count


# ============================================================================
# Confidence. Deterministic, bounded, and explainable in one sentence each.
# ============================================================================

def _sigma_to_confidence(sigma: float) -> float:
    """
    Map a peak significance onto 0-1, saturating rather than reaching 1.0.

    WHY IT NEVER REACHES 1.0: the tool has no access to ground truth and cannot exclude
    a systematic error it does not know about — a mis-surveyed pose rotates every bearing
    in a run by the same amount and would look exactly like a confident finding. A
    verdict that claims certainty is claiming something the architecture cannot support.
    The asymptote is 0.97, and that ceiling is a statement about the method, not a
    tuning artefact.
    """
    return round(0.97 * (1.0 - math.exp(-max(sigma, 0.0) / 4.0)), 4)


def _dark_confidence(
    contact: EoContact, assoc: Association, ctx: SceneContext, policy: VerdictPolicy
) -> tuple[float, list[DeferReason]]:
    """
    How sure are we that this hull really has no transponder?

    Starts high and is knocked down by every innocent explanation, which is the correct
    direction: the burden is on the tool to rule out the boring causes, not on the
    operator to guess them.
    """
    reasons: list[DeferReason] = []
    conf = 0.85

    if contact.track_length_frames < policy.dark_min_track_frames:
        # One or two frames could be a wave crest, a gull, a wake, a compression
        # artefact. This is the single largest source of DARK false positives.
        conf *= 0.45
        reasons.append("single_frame_contact")

    if ctx.sparse_coverage:
        # An AIS gap in a thin-coverage area is likelier to be the receiver than the
        # vessel. Measured, not assumed — see the Bornholm/Fehmarn comparison.
        conf *= 0.6
        reasons.append("sparse_ais_coverage")

    if assoc.candidate_count == 0 and ctx.n_tracks_in_view == 0:
        # Nothing claimed anything anywhere in view. That is a data problem, not a
        # fleet of dark vessels.
        conf *= 0.4
        reasons.append("sparse_ais_coverage")

    obs_conf = _observation_confidence(contact)
    if obs_conf < policy.min_observation_confidence:
        conf *= 0.5
        reasons.append("low_observation_confidence")

    if contact.observed_range_m is None:
        # Without range, a bearing alone cannot exclude a claim that is simply further
        # along the same line of sight.
        conf *= 0.75
        reasons.append("no_range_estimate")

    return round(min(conf, 0.97), 4), reasons


def _observation_confidence(contact: EoContact) -> float:
    """Local copy of consistency.py's factor, so verdict.py can judge a DARK contact
    that has no Mismatch to carry the number for it."""
    frames = min(contact.track_length_frames, 10) / 10.0
    track_factor = 0.35 + 0.65 * frames
    range_factor = 1.0
    if contact.observed_range_m and contact.range_uncertainty_m:
        frac = contact.range_uncertainty_m / max(contact.observed_range_m, 1.0)
        range_factor = max(0.3, 1.0 - min(frac, 0.7))
    class_factor = 0.6 + 0.4 * (contact.observed_class_confidence or 1.0)
    return round(min(1.0, track_factor * range_factor * class_factor), 4)


# ============================================================================
# The decision, for one association.
# ============================================================================

def decide(
    assoc: Association,
    mismatches: Sequence[Mismatch],
    *,
    track: AisTrack | None,
    contact: EoContact | None,
    ctx: SceneContext,
    policy: VerdictPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> Verdict:
    decided_at = now or datetime.now(timezone.utc)
    reasons: list[DeferReason] = []
    subtype: SpoofSubtype | None = None
    label: VerdictLabel
    confidence: float

    # ---- Case 1: an observation with no claim. The DARK case. -------------------
    if contact is not None and track is None:
        confidence, reasons = _dark_confidence(contact, assoc, ctx, policy)

        # THE CLUTTER GATE, added after the D0 harness scored two clutter blobs as DARK.
        #
        # "DARK" asserts something specific: a VESSEL is present and is not transmitting.
        # A blob seen in a single frame does not support the first half of that sentence.
        # It could be a wave crest, a wake, a gull, a compression artefact, or a reflection
        # off a window. Labelling it DARK is not a small over-reach — it is the tool
        # asserting the existence of a ship it cannot show you, and it is the single
        # fastest way to lose a watch officer's trust.
        #
        # So persistence is a PRECONDITION for the label, not merely a penalty on its
        # confidence. Below the threshold the honest output is UNKNOWN: something was
        # detected, it could not be confirmed as a vessel, a human should look.
        if (contact.track_length_frames < policy.dark_requires_frames
                or _observation_confidence(contact) < policy.min_observation_confidence):
            label = "UNKNOWN"
            confidence = min(confidence, 0.30)
            if "single_frame_contact" not in reasons:
                reasons.append("single_frame_contact")
            reasons.append("low_observation_confidence")
        else:
            label = "DARK"

    # ---- Case 2: a claim with no observation. --------------------------------------
    # See the module docstring. Only a corroborated orphan is a spoof candidate.
    elif track is not None and contact is None:
        corroborating = ctx.orphan_corroboration.get(assoc.track_id or "")
        if corroborating is not None:
            label = "SPOOF"
            subtype = "position"
            # Deliberately capped well below what a paired mismatch can reach. This
            # conclusion rests on a geometric coincidence between two orphans, which is
            # suggestive, not measured.
            confidence = 0.62
            reasons.append("low_confidence")
        else:
            # THE CONTROL CASE. A missed detection, an occlusion, a vessel behind
            # another hull, a claim at the edge of the frustum. Nothing was observed,
            # so nothing can be concluded, and saying so is the correct output.
            label = "UNKNOWN"
            confidence = 0.25
            reasons.append("low_confidence")

    # ---- Case 3: a matched pair. MATCH or SPOOF. -----------------------------------
    elif track is not None and contact is not None:
        # Staleness-explained findings are excluded from the decision but NOT from the
        # record: they still travel into the evidence with their flag attached, so an
        # operator can see what the machine chose to disregard and disagree with it.
        actionable = [m for m in mismatches if not m.explained_by_staleness]
        strong = [
            m for m in actionable
            if m.significance >= policy.spoof_sigma
            and m.severity in policy.spoof_requires_severity
        ]
        # CORROBORATION ACROSS DIMENSIONS.
        # A single "minor" disagreement is a noisy sensor. TWO independent dimensions
        # disagreeing on the same hull is a different animal: the failure modes that
        # produce a bad length estimate (range error) and a bad class call (silhouette
        # ambiguity) are unrelated, so their coincidence is far less likely than either
        # alone. Two corroborating minors are therefore allowed to reach SPOOF where one
        # cannot -- which is also what stops class-alone, the weakest evidence in the
        # system, from ever convicting on its own.
        corroborating = [m for m in actionable if m.significance >= policy.spoof_sigma]
        dimensions = {m.dimension for m in corroborating}
        peak = max((m.significance for m in actionable), default=0.0)

        if strong or len(dimensions) >= policy.corroborating_dimensions:
            if not strong:
                strong = corroborating
            label = "SPOOF"
            worst = max(strong, key=lambda m: m.significance)
            subtype = _SUBTYPE_BY_DIMENSION.get(worst.dimension, "identity")
            confidence = _sigma_to_confidence(worst.significance)
            # Weakest link: a 24-sigma length mismatch measured off a contact we barely
            # trust is not a 24-sigma finding about the world.
            confidence *= min(1.0, worst.observation_confidence / 0.8)
            confidence = round(confidence, 4)
            if worst.observation_confidence < policy.min_observation_confidence:
                reasons.append("low_observation_confidence")
        else:
            label = "MATCH"
            # Agreement across more dimensions is stronger agreement, but a MATCH is
            # never more than moderately confident: absence of detected disagreement is
            # not proof of honesty, and the operator should not read it that way.
            n_checked = max(len(actionable), 0)
            confidence = round(min(0.90, 0.62 + 0.05 * n_checked - 0.03 * peak), 4)
            confidence = max(confidence, 0.30)

        if contact.track_length_frames < 2:
            reasons.append("single_frame_contact")
        if contact.observed_range_m is None:
            reasons.append("no_range_estimate")
        if track.claimed_ship_type in (None, "unknown"):
            reasons.append("claim_field_missing")
        if any(m.explained_by_staleness for m in mismatches):
            reasons.append("stale_ais")

    # ---- Case 4: neither side. Should be unreachable. -------------------------------
    else:
        label = "UNKNOWN"
        confidence = 0.0
        reasons.append("low_confidence")

    # ---- Cross-cutting rules that apply to every label above. ----------------------
    if assoc.assoc_ambiguous:
        # A mismatch computed off an ambiguous pairing is not evidence — it may be a
        # measurement of the wrong vessel entirely. contracts.py names this exact case.
        confidence = round(confidence * 0.5, 4)
        reasons.append("ambiguous_association")

    defer = bool(
        confidence < policy.defer_below_confidence
        or label == "UNKNOWN"
        or "ambiguous_association" in reasons
    )

    # Deduplicate while preserving order, so the report reads cleanly and two runs of
    # the same scene produce byte-identical reason lists.
    seen: set[str] = set()
    ordered: list[DeferReason] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            ordered.append(r)

    return Verdict(
        verdict_id=f"v-{assoc.association_id}",
        association_id=assoc.association_id,
        label=label,
        confidence=max(0.0, min(1.0, confidence)),
        mismatch_ids=[m.mismatch_id for m in mismatches],
        decided_at_utc=decided_at,
        spoof_subtype=subtype,
        defer_to_human=defer,
        defer_reasons=ordered if defer else [],
        ruleset_version=f"{RULESET_VERSION}+{policy.spoof_sigma}s",
    )


# ============================================================================
# Scene-level entry point.
# ============================================================================

def build_scene_context(
    associations: Sequence[Association],
    tracks_by_id: dict[str, AisTrack],
    contacts_by_id: dict[str, EoContact],
    *,
    policy: VerdictPolicy = DEFAULT_POLICY,
) -> SceneContext:
    """
    Work out, once per scene, the things no single association can see.

    The important product is orphan_corroboration: for each unmatched CLAIM, is there an
    unmatched CONTACT close enough to be the same physical vessel seen at its real
    position? Matching is done in the observation's own frame — bearing and range from
    the camera — rather than in lat/lon, because converting an orphan contact to a
    position requires the very range estimate whose weakness is the reason it is an
    orphan. Comparing displacement magnitudes avoids compounding that error twice.
    """
    ctx = SceneContext()
    ctx.n_tracks_in_view = sum(1 for a in associations if a.track_id is not None)

    orphan_tracks = [a for a in associations if a.track_id and not a.contact_id]
    orphan_contacts = [a for a in associations if a.contact_id and not a.track_id]

    for ta in orphan_tracks:
        track = tracks_by_id.get(ta.track_id or "")
        if track is None:
            continue
        best: tuple[float, str] | None = None
        for ca in orphan_contacts:
            contact = contacts_by_id.get(ca.contact_id or "")
            if contact is None or contact.observed_range_m is None:
                continue
            # Cheap planar separation between the claim and the orphan hull, using the
            # association's own reported spatial gap where available.
            gap = ca.spatial_gap_m
            if gap is None:
                gap = contact.observed_range_m
            if gap <= policy.orphan_pair_max_gap_m:
                if best is None or gap < best[0]:
                    best = (gap, ca.contact_id or "")
        if best is not None:
            ctx.orphan_corroboration[ta.track_id or ""] = best[1]

    return ctx


def decide_all(
    associations: Sequence[Association],
    mismatches_by_assoc: dict[str, list[Mismatch]],
    tracks_by_id: dict[str, AisTrack],
    contacts_by_id: dict[str, EoContact],
    *,
    policy: VerdictPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> list[Verdict]:
    """One Verdict per Association. Never fewer: an association with nothing to say
    still produces a MATCH or an UNKNOWN, because a contact that silently vanishes
    between stages is how a real vessel gets dropped from a watch picture."""
    ctx = build_scene_context(associations, tracks_by_id, contacts_by_id, policy=policy)
    out: list[Verdict] = []
    for assoc in associations:
        out.append(decide(
            assoc,
            mismatches_by_assoc.get(assoc.association_id, []),
            track=tracks_by_id.get(assoc.track_id) if assoc.track_id else None,
            contact=contacts_by_id.get(assoc.contact_id) if assoc.contact_id else None,
            ctx=ctx, policy=policy, now=now,
        ))
    return out
