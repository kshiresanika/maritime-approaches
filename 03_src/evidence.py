"""
evidence.py — the case file. Lane D. CRITERION 4.

WHAT AN EVIDENCE RECORD IS FOR
Not an alarm. Attribution in this domain is legally fragile: case after case has failed
to prove intent and suspect vessels have been released. So the output of this pipeline
is the BEGINNING OF A CASE FILE — something that still means what it meant when a lawyer
reads it three months later, with every number traceable to the measurement that
produced it and every gap stated rather than smoothed over.

THREE PROPERTIES THAT MAKE IT EVIDENCE RATHER THAN OUTPUT

1. SNAPSHOTS, NOT REFERENCES. contracts.EvidenceRecord embeds the Verdict, the
   Mismatches, the AisTrack and the EoContact by value. A record pointing at a mutable
   verdict is not a record. Every model in contracts.py is frozen for the same reason.

2. IDENTITY IS ALWAYS LABELLED CLAIMED. identity_claimed is a separate block and its
   name is doing legal work. The system never asserts that a vessel IS anything — it
   reports that a transponder BROADCAST a name, and that the camera observed something
   inconsistent with it. Those are different sentences and only one of them is
   defensible.

3. LIMITATIONS ARE MANDATORY AND GENERATED, NOT WRITTEN. Seeded automatically from the
   verdict's defer reasons and from every None that mattered. A limitations section a
   human has to remember to fill in is a limitations section that is empty at 04:00.

THE RATIONALE, AND WHY IT HAS TWO IMPLEMENTATIONS
CLAUDE.md: the deterministic pipeline produces the verdict and the confidence; the LLM
produces the rationale only. Verdict has nowhere to put model text, and there is a test
asserting that.

render_rationale() below is a DETERMINISTIC template — no model, no network, no API key.
It produces a complete, quotable sentence from the numbers alone. That matters for three
reasons: the demo works with the wifi off; the whole project can run with no AI in it at
all; and a templated sentence cannot hallucinate a fact that is not in the record, which
is a real property of evidence rather than a compromise.

An LLM rationale is a presentation upgrade layered on top, written into the same field
and attributed by name. If it is unavailable, the record is still complete.
"""

from __future__ import annotations

from datetime import datetime, timezone

from contracts import (
    AisTrack,
    Association,
    EoContact,
    EvidenceRecord,
    Mismatch,
    PriorityScore,
    Verdict,
)

PIPELINE_VERSION = "maritime-approaches/0.1.0"
M_PER_NM = 1852.0

_DEFER_TEXT: dict[str, str] = {
    "ambiguous_association": "A rival AIS track scored comparably in the pairing step, "
                             "so this comparison may be against the wrong vessel.",
    "no_range_estimate": "No range estimate was available, so size-based checks are "
                         "weak and the position check is bearing-only.",
    "low_observation_confidence": "The observation itself is weak; the disagreement may "
                                  "be in the sensor rather than in the vessel.",
    "stale_ais": "At least one difference is consistent with the AIS report simply "
                 "being old, and has been discounted.",
    "single_frame_contact": "The contact was seen in too few frames to support any "
                            "motion-derived comparison, or to rule out sea clutter.",
    "claim_field_missing": "The vessel never broadcast the field being compared, so "
                           "silence here is not evidence of concealment.",
    "sparse_ais_coverage": "AIS coverage in this sector is thin. An apparent gap may be "
                           "receiver geometry rather than a switched-off transponder.",
    "low_confidence": "Confidence is below the threshold at which this tool will make a "
                      "recommendation.",
}


def render_rationale(
    verdict: Verdict,
    mismatches: list[Mismatch],
    track: AisTrack | None,
    contact: EoContact | None,
    priority: PriorityScore | None,
    behaviour_notes: list[str] | None = None,
) -> str:
    """
    A complete rationale, from arithmetic only. No model is called.

    Style rule, and it is a hard one: this function may state only what is present in
    the objects passed to it. No inference about intent, no adjectives that imply
    motive, and never a sentence asserting that a vessel IS something.
    """
    parts: list[str] = []
    ident = "an unidentified contact"
    if track is not None:
        ident = f"a contact broadcasting MMSI {track.claimed_mmsi}"
        if track.claimed_ship_type:
            ident += f", claiming ship type '{track.claimed_ship_type}'"

    if verdict.label == "DARK":
        b = f"{contact.observed_bearing_deg_true:.1f}" if contact else "?"
        u = f"{contact.bearing_uncertainty_deg:.1f}" if contact else "?"
        r = (f"{contact.observed_range_m/M_PER_NM:.2f} NM"
             if contact and contact.observed_range_m else "range unknown")
        parts.append(
            f"The camera observed a vessel-sized contact on bearing {b} deg true "
            f"(1-sigma {u} deg) at {r}, and no AIS track in view corresponds to it.")
        parts.append(
            "This is consistent with a vessel not transmitting AIS. It is equally "
            "consistent with a small craft under no AIS obligation, a vessel outside "
            "the ingested area, or a false detection.")

    elif verdict.label == "SPOOF":
        parts.append(f"The camera observed {ident}, and the observation disagrees with "
                     f"the broadcast in a way measurement error does not explain.")
        for m in sorted(mismatches, key=lambda x: -x.significance)[:3]:
            if m.explained_by_staleness:
                continue
            unit = "" if m.unit in ("class_label", "none") else f" {m.unit}"
            parts.append(
                f"On {m.dimension}: AIS claimed {m.claimed_value}{unit} "
                f"({m.claimed_field}); the camera measured {m.observed_value}{unit} "
                f"({m.observed_field}). That is {m.significance:.1f} sigma against a "
                f"tolerance of {m.tolerance}{unit}. Severity {m.severity}.")
        if verdict.spoof_subtype:
            parts.append(f"Classified as a {verdict.spoof_subtype} discrepancy.")

    elif verdict.label == "MATCH":
        parts.append(f"The camera observed {ident} and every comparable dimension "
                     f"agrees within tolerance. No action indicated.")
    else:
        parts.append(f"Insufficient evidence to characterise {ident}. "
                     f"This record exists so the contact is not silently dropped.")

    if behaviour_notes:
        parts.append("Behaviour: " + "; ".join(behaviour_notes) + ".")

    if priority is not None:
        drivers = sorted(priority.component_scores.items(),
                         key=lambda kv: -kv[1] * priority.component_weights.get(kv[0], 0))
        top = ", ".join(f"{k} {v:.2f}" for k, v in drivers[:3])
        parts.append(
            f"Priority {priority.score:.2f} (rank {priority.rank}), driven by {top}.")
        if priority.time_to_intercept_min is not None:
            parts.append(
                f"Estimated {priority.time_to_intercept_min:.0f} min for the patrol "
                f"asset to reach it, at {priority.nearest_asset_km:.1f} km.")

    parts.append(
        f"Confidence {verdict.confidence:.2f}. "
        + ("DEFERRED TO A HUMAN OPERATOR — this tool does not recommend action on this "
           "contact." if verdict.defer_to_human
           else "Above the tool's recommendation threshold.")
    )
    return " ".join(parts)


def build_limitations(
    verdict: Verdict, track: AisTrack | None, contact: EoContact | None
) -> list[str]:
    """
    The honest-limits half of criterion 4, generated rather than remembered.

    Seeded from the verdict's defer reasons, then from every None that changed what
    could be concluded. The last entry is unconditional and it is the most important
    line in the whole record.
    """
    out = [_DEFER_TEXT[r] for r in verdict.defer_reasons if r in _DEFER_TEXT]

    if contact is not None:
        if contact.observed_range_m is None:
            out.append("Bearing-only contact: range was not measured, so position and "
                       "size comparisons carry only what bearing can support.")
        if contact.observed_class is None:
            out.append("No silhouette class was assigned, so no class comparison was "
                       "made.")
        if contact.observed_length_m is None:
            out.append("No observed length was measured, so the strongest available "
                       "identity check could not run.")
    if track is not None:
        if track.claimed_length_m is None:
            out.append("The vessel never broadcast a length, so length could not be "
                       "compared. Absence of a claim is not evidence of deception.")
        if track.mobile_class and "B" in track.mobile_class:
            out.append("Class B transponder: sparse or absent static data is normal on "
                       "Class B and must not be weighted as concealment.")

    out.append(
        "This system produces decision support, not a determination of intent or "
        "wrongdoing. Identity fields are CLAIMS made by a transponder, never assertions "
        "by this tool. A human decides.")
    return out


def build_record(
    *,
    verdict: Verdict,
    association: Association,
    mismatches: list[Mismatch],
    track: AisTrack | None,
    contact: EoContact | None,
    priority: PriorityScore | None = None,
    behaviour_notes: list[str] | None = None,
    rationale_text: str | None = None,
    rationale_model: str | None = None,
    now: datetime | None = None,
) -> EvidenceRecord:
    """
    Assemble one record. Sub-objects go in BY VALUE — see the module docstring.

    rationale_text defaults to the deterministic template. Pass an LLM-written one plus
    its model name to override; passing text without a model name is refused, because a
    rationale from an unnamed model is not evidence-grade and contracts.py says so.
    """
    if rationale_text is not None and rationale_model is None:
        raise ValueError(
            "rationale_text was supplied without rationale_model. A rationale from an "
            "unnamed source cannot go into an evidence record.")

    if rationale_text is None:
        rationale_text = render_rationale(
            verdict, mismatches, track, contact, priority, behaviour_notes)
        rationale_model = "deterministic-template (no model)"

    identity = {
        "claimed_mmsi": track.claimed_mmsi if track else None,
        "claimed_imo": track.claimed_imo if track else None,
        "claimed_name": track.claimed_name if track else None,
        "claimed_callsign": track.claimed_callsign if track else None,
    }

    range_nm = None
    if contact is not None and contact.observed_range_m is not None:
        # THE single metres-to-nautical-miles conversion in the project. Operators read
        # NM; everything internal is SI. One conversion point means one place for a
        # unit bug to live, and it is here.
        range_nm = round(contact.observed_range_m / M_PER_NM, 3)

    return EvidenceRecord(
        record_id=f"ev-{verdict.verdict_id}",
        created_at_utc=now or datetime.now(timezone.utc),
        verdict=verdict,
        mismatches=list(mismatches),
        ais_track=track,
        eo_contact=contact,
        priority=priority,
        identity_claimed=identity,
        bearing_deg_true=contact.observed_bearing_deg_true if contact else None,
        range_nm=range_nm,
        frame_refs=[contact.frame_ref] if contact else [],
        rationale_text=rationale_text,
        rationale_model=rationale_model,
        limitations=build_limitations(verdict, track, contact),
        pipeline_version=PIPELINE_VERSION,
    )
