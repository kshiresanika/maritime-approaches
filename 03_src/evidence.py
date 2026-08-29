"""
evidence.py — the case file. Lane D. CRITERION 4.

WHAT AN EVIDENCE RECORD IS FOR
Not an alarm. Attribution in this domain is legally fragile: case after case has failed
to prove intent and suspect vessels have been released. So the output of this pipeline
is the BEGINNING OF A CASE FILE — something that still means what it meant when a lawyer
reads it three months later, with every number traceable to the measurement that
produced it and every gap stated rather than smoothed over.

FIVE PROPERTIES THAT MAKE IT EVIDENCE RATHER THAN OUTPUT

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

4. THE PAIRING IS PART OF THE EVIDENCE, NOT A PRECONDITION OF IT.  <- ADDED THIS SESSION
   Every mismatch in this record is a comparison between one AIS claim and one camera
   contact, and that comparison is only meaningful if the two are the same hull. The
   first question a defence lawyer asks is not "how big was the disagreement" — it is
   "how do you know that camera contact is my client's transponder?" contracts.py has
   no field for the Association, so the answer is folded into the rationale (always,
   one sentence) and into limitations (only when the pairing actually limits the
   conclusion). A record that cannot answer that question is an assertion, not evidence.

5. IDENTITIES ARE PSEUDONYMISED ON THE WAY OUT, NOT ON THE WAY IN. <- ADDED THIS SESSION
   See "THE ANONYMISATION BOUNDARY" below.

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

THE ANONYMISATION BOUNDARY — decided by Amol, 2026-08-29
The tension is real and it does not have a single answer:
  * A watch officer NEEDS the true MMSI. Pointing a patrol boat at "vessel 999000004"
    is not decision support. On the T2 live path identities are real by definition.
  * A demo, a slide or a repo must NEVER put a real hull's name beside the word SPOOF.
    That is defamatory, it is CLAUDE.md constraint 2, and the DMA licence separately
    forbids combining this data with others to identify individuals.
Resolution: THE RECORD HOLDS THE TRUTH; THE RENDERERS PSEUDONYMISE BY DEFAULT.
build_record() stores whatever it was given. to_json() and render_markdown() replace
every identity with a 999-prefixed synthetic one unless explicitly passed
allow_real_identities=True, which also stamps a loud banner into the output itself. This
mirrors 04_demo/make_synthetic_eo.assert_pseudonymised() and reuses the numbering scheme
of ais_ingest.anonymise_mmsi(), so a vessel carries the same synthetic id in the AIS CSV,
in the scene and in the case file.

The scrub is a SUBSTRING PASS OVER EVERY STRING IN THE DUMP, not a field whitelist.
Reason, and it is the failure this file exists to prevent: render_rationale() writes
"a contact broadcasting MMSI 219000123" into free text, and lane A's track_id is a
surrogate over (source, MMSI) so it embeds the MMSI too. Pseudonymising the structured
identity fields while leaving those two alone produces a document that LOOKS anonymised
and is not — the worst of the three possible outcomes, because it is the one nobody
re-checks.

HONESTY REQUIRED BY LANE A'S RULING: this is PSEUDONYMISATION, not anonymisation.
Position, time, course, speed and dimensions are untouched and a determined reader can
re-identify a vessel from its track. The rendered output says so in its own footer. The
pitch must not claim otherwise.

WHAT THIS MODULE DELIBERATELY DOES NOT IMPORT
Only contracts.py. Not verdict.py, not consistency.py, not association.py.
Reason, measured this session: verdict.py line 122 does
`from consistency import ConsistencyResult, independent_dimension_count` and the
consistency.py on disk (and at HEAD) defines NEITHER — check_pair() returns a bare
list[Mismatch]. verdict.py therefore cannot be imported at all right now. Had evidence.py
imported it at module level to reach limitation_strings(), the case-file layer would be
dead for a reason that has nothing to do with the case file. Instead the caller — which
holds both objects anyway — passes the strings in. That also preserves lane C's seam:
ConsistencyResult never crosses into lane D, only list[str] does. See handoff_C.md 6b.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

# MID 999 is unassigned to any country, so a 999-prefixed MMSI is obviously fake to
# anyone who reads AIS. Chosen over a hash in ais_ingest.anonymise_mmsi() because a
# hashed MMSI still LOOKS like a real MMSI and someone will eventually paste one into a
# vessel database. Kept identical here so the two modules cannot drift apart.
SYNTHETIC_MID = "999"

# Below this the pairing is weak enough to be worth stating as a limitation. Mirrors
# verdict.LOW_ASSOC_SCORE. It is duplicated rather than imported ONLY because importing
# verdict.py currently fails (see the module docstring); if that import is ever fixed,
# delete this constant and import it, because two copies of a threshold drift.
LOW_ASSOC_SCORE = 0.20

# A rival pairing within this margin of the winner is worth stating even when
# association.py did not raise assoc_ambiguous — the flag is a decision, the margin is
# the evidence behind it, and a lawyer reads the second one.
THIN_PAIRING_MARGIN = 0.15


# ============================================================================
# Defer-reason prose.
#
# WHY A DICT AND NOT A FORMATTED ENUM NAME: "low_observation_confidence" is a symbol a
# watch officer has to be trained to read. Criterion 4 asks the output to say WHY it
# defers in terms of what to go and check, not merely THAT it does.
# ============================================================================

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
    # Requested by lane C (99_scratch/requests.md, OPEN against contracts.py). Wording
    # is here already so that if ARCH adds the enum members the prose does not lag.
    "single_dimension_evidence": "The case rests on a single measured dimension. One "
                                 "unmodelled systematic — a range estimate 40% low "
                                 "scaling the apparent length — reproduces exactly this "
                                 "signature, and only a second, unrelated dimension "
                                 "rules that out.",
    "uncorroborated_pairing": "The comparison was computed off a weak or contested "
                              "pairing, so the evidence may belong to a different hull.",
}


def _defer_text(reason: str) -> str:
    """
    Prose for one defer reason, with a LOUD fallback rather than a silent drop.

    The previous version filtered with `if r in _DEFER_TEXT`, which meant a defer reason
    this module had not been taught simply vanished from the case file. That is the
    exact criterion-4 failure mode: the pipeline correctly decided to defer, and the
    operator was never told why. An ugly sentence in the report is recoverable; a
    missing one is not.
    """
    known = _DEFER_TEXT.get(reason)
    if known is not None:
        return known
    return (f"Deferred for reason '{reason}', which this reporting module has no plain-"
            f"language description for. Treat as unexplained and escalate.")


# ============================================================================
# Pseudonymisation.
# ============================================================================

class Anonymiser:
    """
    Real identity -> a recognisably synthetic one, stable across a whole run.

    ONE INSTANCE PER RUN, SHARED BY EVERY RECORD. That is the point: a dossier in which
    the same hull is 999000003 on page 2 and 999000007 on page 5 is not a case file, it
    is a pile of unrelated pages. Numbering is by first-seen order, 1-based, matching
    ais_ingest.anonymise_mmsi(mmsi, order) so the same vessel carries the same synthetic
    id in the AIS CSV, in the synthetic scene and here. Pass `order` explicitly to pin
    the numbering to lane A's if the two must be cross-referenced by eye.

    ALREADY-PSEUDONYMISED INPUT PASSES THROUGH UNCHANGED. The golden window emits
    999-prefixed MMSIs already; renumbering those would silently break every
    cross-reference back to 02_data/golden/*_anon.csv, and it would do so invisibly,
    because both the old and the new value look equally synthetic.

    This is PSEUDONYMISATION, not anonymisation — see the module docstring. The map
    below reverses it and must never leave the machine.
    """

    def __init__(self, order: Sequence[str] | None = None) -> None:
        self._order: list[str] = list(order) if order else []

    # -- numbering ---------------------------------------------------------

    def _index(self, mmsi: str) -> int:
        """First-seen 0-based index for a real MMSI, allocating on first sight."""
        if mmsi not in self._order:
            self._order.append(mmsi)
        return self._order.index(mmsi)

    def mmsi(self, real: str | None) -> str | None:
        if real is None:
            return None
        if real.startswith(SYNTHETIC_MID):
            return real
        return f"{SYNTHETIC_MID}{self._index(real) + 1:06d}"

    def name(self, real: str | None, mmsi: str | None) -> str | None:
        if real is None:
            return None
        if mmsi is None:
            return "VESSEL (name withheld)"
        return f"VESSEL {self._index(mmsi) + 1:03d}"

    def imo(self, real: str | None, mmsi: str | None) -> str | None:
        if real is None:
            return None
        # Deliberately NOT seven digits. A real IMO number carries a check digit, so a
        # plausible-looking fake one can be validated by a third party and pointed at an
        # innocent hull. "IMO-SYNTH-004" cannot be mistaken for an IMO number.
        n = self._index(mmsi) + 1 if mmsi else 0
        return f"IMO-SYNTH-{n:03d}"

    def callsign(self, real: str | None, mmsi: str | None) -> str | None:
        if real is None:
            return None
        n = self._index(mmsi) + 1 if mmsi else 0
        return f"SY{n:04d}"

    # -- substitution table ------------------------------------------------

    def substitutions(self, record: EvidenceRecord) -> dict[str, str]:
        """
        Every real identity string in this record, mapped to its synthetic replacement.

        Returned as a plain dict so the caller can inspect, log or assert on it. Empty
        for a DARK contact, which has no claimed identity by construction — a camera
        cannot observe an identity, so there is nothing to protect.
        """
        subs: dict[str, str] = {}
        track = record.ais_track
        mmsi = track.claimed_mmsi if track else record.identity_claimed.get("claimed_mmsi")
        if not mmsi:
            return subs

        def add(real: Any, fake: Any) -> None:
            # Guard against mapping a value onto itself (already-pseudonymised input)
            # and against empty strings, which would match everywhere.
            if isinstance(real, str) and real and isinstance(fake, str) and real != fake:
                subs[real] = fake

        add(mmsi, self.mmsi(mmsi))
        if track is not None:
            add(track.claimed_name, self.name(track.claimed_name, mmsi))
            add(track.claimed_imo, self.imo(track.claimed_imo, mmsi))
            add(track.claimed_callsign, self.callsign(track.claimed_callsign, mmsi))
        for key, fake_fn in (("claimed_name", self.name),
                             ("claimed_imo", self.imo),
                             ("claimed_callsign", self.callsign)):
            raw = record.identity_claimed.get(key)
            add(raw, fake_fn(raw, mmsi))
        return subs

    def mapping(self) -> dict[str, str]:
        """
        The re-identification key: synthetic MMSI -> real MMSI.

        THIS IS THE SENSITIVE HALF AND IT MUST STAY ON THE MACHINE. It is what makes
        this pseudonymisation rather than anonymisation, and it is what a watch officer
        needs in order to actually dispatch a boat. Never write it next to the case
        files it reverses.
        """
        return {f"{SYNTHETIC_MID}{i + 1:06d}": real
                for i, real in enumerate(self._order)}


def _scrub_text(text: str, subs: dict[str, str]) -> str:
    """
    Replace every real identifier inside one free-text string.

    LONGEST FIRST, because a callsign can be a substring of a name and replacing the
    short one first corrupts the long one. WORD-BOUNDED for short tokens, because a
    three-character callsign like "OU2" would otherwise fire inside unrelated words and
    inside numbers; a 9-digit MMSI is unambiguous enough to replace plainly.
    """
    for real in sorted(subs, key=len, reverse=True):
        fake = subs[real]
        if len(real) >= 6:
            text = text.replace(real, fake)
        else:
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(real)}(?![A-Za-z0-9])",
                          fake, text)
    return text


def _scrub(node: Any, subs: dict[str, str]) -> Any:
    """Recursively apply _scrub_text to every string leaf of a JSON-able structure."""
    if isinstance(node, str):
        return _scrub_text(node, subs)
    if isinstance(node, dict):
        return {k: _scrub(v, subs) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub(v, subs) for v in node]
    return node


def assert_no_real_identities(payload: str, subs: dict[str, str]) -> None:
    """
    Last line of defence: refuse to emit a document that still contains a real id.

    A HARD STOP AND NOT A WARNING, for the same reason as
    make_synthetic_eo.assert_pseudonymised(): the failure this catches is a document
    that looks anonymised and is not, and nobody re-reads those.
    """
    leaked = sorted({real for real in subs if real in payload})
    if leaked:
        raise SystemExit(
            "REFUSING TO EMIT EVIDENCE: the rendered document still contains "
            f"{len(leaked)} real identifier(s) after pseudonymisation "
            f"(first few: {leaked[:5]}). This is a bug in _scrub_text, not in the "
            "data. Do not hand-edit the output — fix the scrub.")


# ============================================================================
# Rationale.
# ============================================================================

def _pairing_sentence(assoc: Association | None) -> str | None:
    """
    One sentence answering "how do you know that contact is that transponder?"

    ALWAYS EMITTED FOR A MATCHED PAIR, including a clean one. A case file that mentions
    the pairing only when it is weak teaches its reader that silence means strong, and
    that is a habit that will eventually be wrong.
    """
    if assoc is None or assoc.contact_id is None or assoc.track_id is None:
        return None
    bits = [f"The camera contact was paired to this AIS track at score "
            f"{assoc.assoc_score:.3f}"]
    if assoc.candidate_count <= 1:
        bits.append("as the only AIS claim inside the camera's field of view (an "
                    "uncontested pairing, which is not the same as a corroborated one)")
    elif assoc.runner_up_score is not None:
        bits.append(f"ahead of {assoc.candidate_count - 1} other candidate(s), the best "
                    f"of which scored {assoc.runner_up_score:.3f}")
    else:
        bits.append(f"against {assoc.candidate_count} candidate(s)")
    geo: list[str] = []
    if assoc.spatial_gap_m is not None:
        geo.append(f"{assoc.spatial_gap_m:.0f} m between the projected AIS position and "
                   f"the observed line of bearing")
    if assoc.time_delta_s is not None:
        geo.append(f"{assoc.time_delta_s:.1f} s between the AIS report and the frame")
    if geo:
        bits.append("with " + " and ".join(geo))
    return ", ".join(bits) + "."


def render_rationale(
    verdict: Verdict,
    mismatches: list[Mismatch],
    track: AisTrack | None,
    contact: EoContact | None,
    priority: PriorityScore | None,
    behaviour_notes: list[str] | None = None,
    association: Association | None = None,
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
        r = (f"{contact.observed_range_m / M_PER_NM:.2f} NM"
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

    # The pairing goes AFTER the finding and BEFORE the priority: it is the load-bearing
    # premise of everything above it, and the reader needs it before being told what to
    # do about the finding.
    pairing = _pairing_sentence(association)
    if pairing:
        parts.append(pairing)

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


# ============================================================================
# Limitations.
# ============================================================================

def _pairing_limitations(assoc: Association | None) -> list[str]:
    """
    Only the pairing facts that LIMIT the conclusion.

    Deliberately not a mirror of _pairing_sentence(). Rationale = what we know;
    limitations = what we cannot stand behind. Putting a clean pairing in the
    limitations list dilutes it, and a limitations section that is mostly reassurance is
    one an operator learns to skip.
    """
    if assoc is None:
        return ["No pairing record accompanied this finding, so the evidence that the "
                "camera contact and the AIS track are the same hull cannot be shown."]
    out: list[str] = []
    if assoc.contact_id is None or assoc.track_id is None:
        return out  # DARK or unobserved-claim: there is no pairing to qualify.

    if assoc.assoc_ambiguous:
        out.append(
            f"The pairing was flagged ambiguous: {assoc.candidate_count} AIS claims "
            f"competed for this contact"
            + (f" and the runner-up scored {assoc.runner_up_score:.3f} against the "
               f"winner's {assoc.assoc_score:.3f}"
               if assoc.runner_up_score is not None else "")
            + ". Every comparison in this record may be against the wrong vessel.")
    elif (assoc.runner_up_score is not None
          and assoc.assoc_score - assoc.runner_up_score < THIN_PAIRING_MARGIN):
        out.append(
            f"The pairing was not flagged ambiguous, but the margin over the runner-up "
            f"was thin ({assoc.assoc_score:.3f} vs {assoc.runner_up_score:.3f}). Stated "
            f"because the flag is a decision and the margin is the evidence behind it.")

    if assoc.assoc_score < LOW_ASSOC_SCORE:
        out.append(
            f"The pairing score ({assoc.assoc_score:.3f}) is below the level at which "
            f"this tool treats a pairing as established. A coincidental alignment of an "
            f"unrelated contact and an unrelated claim can reach this score.")

    if assoc.candidate_count <= 1:
        out.append(
            "Only one AIS claim was in view, so nothing competed with this pairing. An "
            "uncontested pairing is not a corroborated one: if the true transmitter was "
            "outside the ingested area, this contact would have been paired to this "
            "claim regardless.")
    return out


def build_limitations(
    verdict: Verdict,
    track: AisTrack | None,
    contact: EoContact | None,
    association: Association | None = None,
    consistency_limitations: Sequence[str] | None = None,
) -> list[str]:
    """
    The honest-limits half of criterion 4, generated rather than remembered.

    Seeded from four sources, in order of how badly their absence would mislead:
      1. lane C's verdict.limitation_strings() — the calibration statement and the
         checks that could not run. THE ONLY CHANNEL for those (handoff_C.md 6b), and
         passed in rather than fetched, because ConsistencyResult must not cross the
         lane seam and because verdict.py cannot currently be imported at all.
      2. the pairing, when it limits the conclusion.
      3. the verdict's own defer reasons.
      4. every None that changed what could be concluded.
    The last entry is unconditional and it is the most important line in the record.
    """
    out: list[str] = []

    if consistency_limitations is None:
        # FAIL LOUD, NOT SILENT. Without lane C's strings the record has no calibration
        # statement, and a confidence number with no statement of how it was calibrated
        # is the single easiest thing in this document for a lawyer to take apart.
        out.append(
            "The consistency layer's limitation statement did not reach this record, so "
            "the calibration basis of the confidence below is NOT DECLARED here and any "
            "checks that could not be run are NOT LISTED. Treat the confidence as "
            "uncalibrated until this is supplied.")
    else:
        out.extend(consistency_limitations)

    out.extend(_pairing_limitations(association))
    out.extend(_defer_text(r) for r in verdict.defer_reasons)

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
        if contact.track_length_frames <= 1:
            out.append("The contact appears in a single frame, so nothing "
                       "motion-derived (speed, heading, persistence) could be observed, "
                       "and sea clutter cannot be ruled out by persistence.")
    if track is not None:
        if track.claimed_length_m is None:
            out.append("The vessel never broadcast a length, so length could not be "
                       "compared. Absence of a claim is not evidence of deception.")
        if track.claimed_ship_type == "unknown":
            out.append("The broadcast ship type maps to 'unknown' — a claim was made "
                       "but it is not comparable to a camera silhouette. This covers "
                       "roughly 30% of real traffic in the measured slice and is not "
                       "itself suspicious.")
        if track.mobile_class and "B" in track.mobile_class:
            out.append("Class B transponder: sparse or absent static data is normal on "
                       "Class B and must not be weighted as concealment.")

    out.append(
        "This system produces decision support, not a determination of intent or "
        "wrongdoing. Identity fields are CLAIMS made by a transponder, never assertions "
        "by this tool. A human decides.")

    # De-duplicate while preserving order: lane C and this module can legitimately reach
    # the same limitation by different routes, and the same sentence twice reads as
    # padding.
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


# ============================================================================
# Assembly.
# ============================================================================

def build_record(
    *,
    verdict: Verdict,
    association: Association,
    mismatches: list[Mismatch],
    track: AisTrack | None,
    contact: EoContact | None,
    priority: PriorityScore | None = None,
    behaviour_notes: list[str] | None = None,
    consistency_limitations: Sequence[str] | None = None,
    rationale_text: str | None = None,
    rationale_model: str | None = None,
    now: datetime | None = None,
) -> EvidenceRecord:
    """
    Assemble one record. Sub-objects go in BY VALUE — see the module docstring.

    `association` is now READ, not merely accepted. It was previously a parameter that
    went nowhere: the pairing quality — the premise every mismatch rests on — was
    silently discarded. contracts.EvidenceRecord has no field for it, so its facts are
    folded into the rationale and the limitations, which do have fields.

    `consistency_limitations` is what lane C's verdict.limitation_strings(result,
    verdict) returns. The CALLER computes it, because it is the only party holding both
    the ConsistencyResult and the Verdict, and because a list[str] crosses the lane seam
    where a ConsistencyResult must not. Omitting it is allowed and is reported in the
    record itself as a stated gap — never silently.

    rationale_text defaults to the deterministic template. Pass an LLM-written one plus
    its model name to override; passing text without a model name is refused, because a
    rationale from an unnamed model is not evidence-grade and contracts.py says so.
    """
    if rationale_text is not None and rationale_model is None:
        raise ValueError(
            "rationale_text was supplied without rationale_model. A rationale from an "
            "unnamed source cannot go into an evidence record.")

    if verdict.association_id != association.association_id:
        # A record assembled from a verdict and a pairing that do not belong together is
        # not a wrong answer, it is a fabricated one — the mismatches would describe a
        # different hull than the pairing evidence does. Fail at build time; there is no
        # honest way to render this.
        raise ValueError(
            f"Verdict {verdict.verdict_id} was decided on association "
            f"{verdict.association_id} but association {association.association_id} was "
            f"passed. Refusing to build a record from mismatched provenance.")

    if rationale_text is None:
        rationale_text = render_rationale(
            verdict, mismatches, track, contact, priority, behaviour_notes, association)
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
        limitations=build_limitations(
            verdict, track, contact, association, consistency_limitations),
        pipeline_version=PIPELINE_VERSION,
    )


# ============================================================================
# Structured output.
# ============================================================================

def _disclosure_block(subs: dict[str, str], allow_real: bool) -> dict[str, Any]:
    """
    The block that stops the pitch from overclaiming.

    Lane A's ruling: position, time, course, speed and dimensions are UNTOUCHED, so a
    determined reader can re-identify a vessel from its track. Calling that
    "anonymised" on a slide would be a false statement about the tool's own privacy
    properties, made inside a document whose entire purpose is being accurate.
    """
    if allow_real:
        return {
            "identities": "REAL — NOT PSEUDONYMISED",
            "warning": "THIS DOCUMENT NAMES REAL VESSELS. It must not be committed, "
                       "shown publicly, put on a slide, or shared outside the "
                       "operating authority. Generated with allow_real_identities=True.",
        }
    return {
        "identities": "PSEUDONYMISED",
        "method": f"MMSI, name, IMO and callsign replaced with {SYNTHETIC_MID}-prefixed "
                  f"synthetic values. MID {SYNTHETIC_MID} is unassigned to any country, "
                  f"so these are recognisably fake.",
        "identifiers_replaced": len(subs),
        "NOT_anonymised": "Position, timestamp, course, speed and dimensions are "
                          "UNCHANGED. This is pseudonymisation, not anonymisation: a "
                          "determined reader with access to AIS history can re-identify "
                          "a vessel from its track. Do not describe this output as "
                          "anonymised.",
        "reidentification_key": "Held locally by the operator. Never shipped with this "
                                "document.",
    }


def record_to_dict(
    record: EvidenceRecord,
    *,
    anonymiser: Anonymiser | None = None,
    association: Association | None = None,
    allow_real_identities: bool = False,
) -> dict[str, Any]:
    """
    The machine-readable case file.

    The body is pydantic's own model_dump(mode="json") — canonical, complete, and not a
    hand-written serialiser that can drift from contracts.py. Two blocks are added
    outside it, and they are deliberately OUTSIDE rather than merged in, so a consumer
    can tell at a glance what came from the typed contract and what this reporting layer
    added on top:

      pairing_basis  — the Association, which EvidenceRecord has no field for.
      disclosure     — what was pseudonymised, what was NOT, and the standing caveat.

    Set allow_real_identities=True only for the operator-facing local path. Everything
    that leaves the machine takes the default.
    """
    payload: dict[str, Any] = {"record": record.model_dump(mode="json")}

    if association is not None:
        payload["pairing_basis"] = {
            **association.model_dump(mode="json"),
            "_note": "Not part of contracts.EvidenceRecord. Included because every "
                     "mismatch in this record is a comparison between one AIS claim and "
                     "one camera contact, and is only meaningful if they are the same "
                     "hull. This block is that argument.",
        }

    subs: dict[str, str] = {}
    if not allow_real_identities:
        anonymiser = anonymiser or Anonymiser()
        subs = anonymiser.substitutions(record)
        payload = _scrub(payload, subs)

    payload["disclosure"] = _disclosure_block(subs, allow_real_identities)

    if not allow_real_identities:
        # Verify on the SERIALISED form, not the object graph: what leaves the machine
        # is the string, so the string is what must be checked.
        assert_no_real_identities(json.dumps(payload, ensure_ascii=False), subs)
    return payload


def to_json(
    record: EvidenceRecord,
    *,
    anonymiser: Anonymiser | None = None,
    association: Association | None = None,
    allow_real_identities: bool = False,
    indent: int = 2,
) -> str:
    """
    Pseudonymised JSON case file as a string.

    sort_keys is left False on purpose: field order in contracts.py is meaningful to a
    human reader, and sorting alphabetically puts 'bearing_deg_true' above 'verdict'.
    """
    return json.dumps(
        record_to_dict(record, anonymiser=anonymiser, association=association,
                       allow_real_identities=allow_real_identities),
        indent=indent, ensure_ascii=False)


# ============================================================================
# Rendered view.
#
# AUDIENCE: a watch officer and, later, a lawyer. Neither reads JSON, and neither has
# been trained on this tool's vocabulary. Every number that carries the case is
# therefore given twice — as the figure, and as what the figure means.
# ============================================================================

_SIGMA_GLOSS = (
    "**sigma** — how many times larger a disagreement is than the measurement error "
    "that could innocently explain it. Around 1 sigma is routine scatter. 3 sigma "
    "happens by chance in roughly 1 honest observation in 370. It measures how hard a "
    "difference is to explain away. It is not a probability of guilt."
)

_VERDICT_GLOSS: dict[str, str] = {
    "MATCH": "What the transponder claims and what the camera sees agree within "
             "measurement error. No action indicated.",
    "DARK": "The camera sees a vessel-sized contact and no transponder in view accounts "
            "for it. This is not proof a transponder was switched off — see limitations.",
    "SPOOF": "What the transponder claims and what the camera sees disagree by more "
             "than measurement error can explain. This is a discrepancy, NOT a finding "
             "of intent.",
    "UNKNOWN": "The tool looked and cannot say. This record exists so the contact is "
               "not silently dropped.",
}


def _fmt(value: Any, unit: str = "", places: int = 1) -> str:
    """One formatter, so 'not available' is spelled the same way everywhere. A report
    that says 'None' in one row and '-' in another teaches its reader that the two mean
    different things."""
    if value is None:
        return "not available"
    if isinstance(value, float):
        return f"{value:.{places}f}{unit}"
    return f"{value}{unit}"


def render_markdown(
    record: EvidenceRecord,
    *,
    anonymiser: Anonymiser | None = None,
    association: Association | None = None,
    allow_real_identities: bool = False,
) -> str:
    """
    The human-readable case file. Same facts as the JSON, no extra ones.

    STRUCTURE IS THE ARGUMENT, and it is ordered the way a reader has to be convinced
    rather than the way the pipeline computed it:
      1. the verdict, with its confidence and the defer flag, stated first and once
      2. what the camera SAW          (observation, no identity — a camera cannot see one)
      3. what the transponder CLAIMED (claims, labelled as claims, every time)
      4. where the two disagree       (the table; this is criterion 3)
      5. why they are the same hull   (the pairing; the premise of section 4)
      6. what to do about it          (priority; criterion 2)
      7. the rationale, attributed to whatever wrote it
      8. what this record cannot support (limitations; criterion 4's other half)
    Sections 1 and 2 are never merged. That separation is the whole architecture.
    """
    v = record.verdict
    subs: dict[str, str] = {}
    if not allow_real_identities:
        anonymiser = anonymiser or Anonymiser()
        subs = anonymiser.substitutions(record)

    def s(text: str | None) -> str:
        """Scrub one string on its way into the document."""
        if text is None:
            return "not available"
        return _scrub_text(text, subs) if subs else text

    L: list[str] = []
    a = L.append

    # -- 0. header and verdict --------------------------------------------
    a(f"# Evidence Record `{record.record_id}`")
    a("")
    if allow_real_identities:
        a("> ## !! REAL IDENTITIES — DO NOT DISTRIBUTE")
        a("> This document names real vessels. It must not be committed, shown "
          "publicly, or put on a slide.")
        a("")
    a(f"**Verdict: {v.label}** &middot; confidence **{v.confidence:.2f}** &middot; "
      + ("**DEFERRED TO A HUMAN OPERATOR**" if v.defer_to_human
         else "above the recommendation threshold"))
    a("")
    a(f"*{_VERDICT_GLOSS.get(v.label, '')}*")
    if v.spoof_subtype:
        a("")
        a(f"Discrepancy type: **{v.spoof_subtype}**.")
    a("")
    a(f"Record created {record.created_at_utc.isoformat()} &middot; "
      f"decided {v.decided_at_utc.isoformat()} &middot; "
      f"pipeline `{record.pipeline_version}` &middot; ruleset `{v.ruleset_version}`")
    a("")

    if v.defer_to_human:
        a("> **This tool is not making a recommendation on this contact.** "
          "It is presenting what it measured and why it is not sure. "
          "The reasons are listed under *Limitations* below.")
        a("")

    # -- 1. what the camera saw -------------------------------------------
    a("## 1. What the camera observed")
    a("")
    c = record.eo_contact
    if c is None:
        a("No camera contact. This record concerns an AIS claim for which nothing was "
          "observed — which, inside the camera's field of view, is itself a finding.")
    else:
        a(f"- **Time of frame:** {c.frame_time_utc.isoformat()}")
        a(f"- **Bearing:** {_fmt(record.bearing_deg_true, ' degrees true')} "
          f"(1-sigma {_fmt(c.bearing_uncertainty_deg, ' degrees')})")
        a(f"- **Range:** {_fmt(record.range_nm, ' NM', 2)}"
          + (f" (1-sigma {_fmt(c.range_uncertainty_m, ' m', 0)})"
             if c.range_uncertainty_m is not None else ""))
        a(f"- **Observed silhouette class:** {c.observed_class or 'not classified'}"
          + (f" (classifier confidence {c.observed_class_confidence:.2f})"
             if c.observed_class_confidence is not None else ""))
        a(f"- **Observed length:** {_fmt(c.observed_length_m, ' m', 0)}"
          + (f" (1-sigma {_fmt(c.observed_length_uncertainty_m, ' m', 0)})"
             if c.observed_length_uncertainty_m is not None else ""))
        a(f"- **Detection confidence:** {c.detection_confidence:.2f} &middot; "
          f"**seen in {c.track_length_frames} frame(s)**")
        a(f"- **Frame reference:** `{s(c.frame_ref)}` &middot; "
          f"camera pose `{s(c.camera_pose_ref)}`")
        a("")
        a("*The camera observes no identity. There is deliberately no MMSI, name or IMO "
          "in this section — a camera cannot see one.*")
    a("")

    # -- 2. what AIS claimed ----------------------------------------------
    a("## 2. What the transponder claimed")
    a("")
    t = record.ais_track
    if t is None:
        a("**No AIS claim.** No transponder in view accounts for the contact above.")
    else:
        a("Every value in this section is a **claim broadcast by a transponder**. "
          "This tool does not assert that any of it is true.")
        a("")
        a("| Claimed field | Value |")
        a("|---|---|")
        a(f"| MMSI | `{s(record.identity_claimed.get('claimed_mmsi'))}` |")
        a(f"| Name | {s(record.identity_claimed.get('claimed_name'))} |")
        a(f"| IMO | {s(record.identity_claimed.get('claimed_imo'))} |")
        a(f"| Callsign | {s(record.identity_claimed.get('claimed_callsign'))} |")
        a(f"| Ship type | {t.claimed_ship_type or 'no claim made'} |")
        a(f"| Length | {_fmt(t.claimed_length_m, ' m', 0)} |")
        a(f"| Position | {t.claimed_lat_deg:.5f}, {t.claimed_lon_deg:.5f} |")
        a(f"| Speed over ground | {_fmt(t.claimed_sog_kn, ' kn', 1)} |")
        a(f"| Course over ground | {_fmt(t.claimed_cog_deg_true, ' degrees true')} |")
        a(f"| Heading | {_fmt(t.claimed_heading_deg_true, ' degrees true')} |")
        a(f"| Navigational status | {t.claimed_nav_status or 'no claim made'} |")
        a(f"| Transponder class | {t.mobile_class or 'not recorded'} |")
        a(f"| Report time | {t.report_time_utc.isoformat()} |")
    a("")

    # -- 3. the disagreement ----------------------------------------------
    a("## 3. Where the claim and the observation disagree")
    a("")
    if not record.mismatches:
        a("No dimension disagreed beyond tolerance. "
          "Note that this is not the same as *every dimension agreed* — see "
          "*Limitations* for which comparisons could not be run at all.")
    else:
        a("| Dimension | AIS claimed | Camera observed | Difference | Tolerance | "
          "Significance | Severity | Counted? |")
        a("|---|---|---|---|---|---|---|---|")
        for m in sorted(record.mismatches, key=lambda x: -x.significance):
            unit = "" if m.unit in ("class_label", "none") else f" {m.unit}"
            counted = ("no — explained by a stale AIS report"
                       if m.explained_by_staleness else "yes")
            a(f"| {m.dimension} | {m.claimed_value}{unit} | {m.observed_value}{unit} "
              f"| {_fmt(m.delta, unit)} | {_fmt(m.tolerance, unit)} "
              f"| {m.significance:.2f} sigma | {m.severity} | {counted} |")
        a("")
        a("Provenance of each row — which exact field made the claim, and which "
          "measurement contradicted it:")
        a("")
        for m in sorted(record.mismatches, key=lambda x: -x.significance):
            a(f"- **{m.dimension}**: claim from `{m.claimed_field}`, observation from "
              f"`{m.observed_field}`, observation quality "
              f"{m.observation_confidence:.2f} &middot; `{m.mismatch_id}`")
        a("")
        a(_SIGMA_GLOSS)
    a("")

    # -- 4. the pairing ---------------------------------------------------
    a("## 4. Why these are believed to be the same vessel")
    a("")
    if association is None:
        a("*The pairing record did not accompany this document.* Section 3 compares an "
          "AIS claim against a camera observation, and that comparison only means "
          "something if the two are the same hull. Without the pairing record that "
          "premise cannot be shown here.")
    elif association.contact_id is None or association.track_id is None:
        a("There is no pairing to show: this record concerns "
          + ("a camera contact with no corresponding AIS claim."
             if association.track_id is None
             else "an AIS claim with nothing observed where it should have been."))
    else:
        a(f"- **Pairing score:** {association.assoc_score:.3f}")
        a(f"- **Competing AIS claims considered:** {association.candidate_count}")
        a(f"- **Best rival score:** {_fmt(association.runner_up_score, '', 3)}")
        a("- **Flagged ambiguous:** "
          + ("YES — treat section 3 with caution" if association.assoc_ambiguous
             else "no"))
        a(f"- **Gap between projected AIS position and observed bearing line:** "
          f"{_fmt(association.spatial_gap_m, ' m', 0)}")
        a(f"- **Time between the AIS report and the frame:** "
          f"{_fmt(association.time_delta_s, ' s', 1)}")
        a(f"- **Pairing record:** `{association.association_id}`, decided "
          f"{association.assoc_time_utc.isoformat()}")
    a("")

    # -- 5. priority ------------------------------------------------------
    a("## 5. Priority for a scarce asset")
    a("")
    p = record.priority
    if p is None:
        a("This contact was not ranked.")
    else:
        a(f"**Rank {p.rank}** &middot; score **{p.score:.3f}** &middot; "
          + ("**a scarce asset is recommended**" if p.scarce_asset_recommended
             else "no asset recommended"))
        a("")
        a("| Component | Score | Weight | Contribution |")
        a("|---|---|---|---|")
        for k in sorted(p.component_scores,
                        key=lambda k: -p.component_scores[k]
                        * p.component_weights.get(k, 0.0)):
            w = p.component_weights.get(k, 0.0)
            a(f"| {k} | {p.component_scores[k]:.3f} | {w:.3f} | "
              f"{p.component_scores[k] * w:.3f} |")
        a("")
        a("*The contributions sum to the score. If they do not, the ranking is not "
          "inspectable and this table should not be trusted.*")
        a("")
        if p.nearest_infrastructure_m is not None:
            a(f"- **Nearest critical infrastructure:** "
              f"{p.nearest_infrastructure_id or 'unnamed'} at "
              f"{p.nearest_infrastructure_m / 1000.0:.2f} km")
        if p.nearest_asset_km is not None:
            a(f"- **Nearest patrol asset:** {p.nearest_asset_km:.1f} km away")
        if p.time_to_intercept_min is not None:
            a(f"- **Estimated time to intercept:** {p.time_to_intercept_min:.0f} min")
    a("")

    # -- 6. rationale -----------------------------------------------------
    a("## 6. Rationale")
    a("")
    a(s(record.rationale_text))
    a("")
    a(f"*Written by: `{record.rationale_model}`. The verdict, the confidence and the "
      f"priority above were produced by the deterministic pipeline. A language model, "
      f"where one is used, writes this paragraph and nothing else.*")
    a("")

    # -- 7. limitations ---------------------------------------------------
    a("## 7. Limitations — what this record cannot support")
    a("")
    for lim in record.limitations:
        a(f"- {s(lim)}")
    a("")

    # -- 8. footer --------------------------------------------------------
    a("---")
    a("")
    if allow_real_identities:
        a("**Identities: REAL.** This document must not be distributed.")
    else:
        a(f"**Identities: pseudonymised.** MMSI, name, IMO and callsign were replaced "
          f"with {SYNTHETIC_MID}-prefixed synthetic values ({len(subs)} identifier(s) "
          f"replaced); MID {SYNTHETIC_MID} is unassigned to any country, so they are "
          f"recognisably fake. **Position, time, course, speed and dimensions are "
          f"unchanged — this is pseudonymisation, not anonymisation,** and a reader "
          f"with AIS history could re-identify the vessel from its track. The "
          f"re-identification key is held locally and is not part of this document.")
    a("")
    a("**Decision support, not enforcement.** This tool ranks and evidences; a human "
      "decides. Nothing above is a determination of intent or of wrongdoing.")

    text = "\n".join(L)
    if not allow_real_identities:
        assert_no_real_identities(text, subs)
    return text


def write_case_file(
    record: EvidenceRecord,
    out_dir: str | Path,
    *,
    anonymiser: Anonymiser | None = None,
    association: Association | None = None,
    allow_real_identities: bool = False,
) -> tuple[Path, Path]:
    """
    Write `<record_id>.json` and `<record_id>.md` side by side. Returns both paths.

    Both views come from the SAME record and the SAME anonymiser, so the JSON a machine
    ingests and the Markdown a human reads cannot disagree — including on which
    synthetic identity belongs to which hull.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    anonymiser = anonymiser or Anonymiser()
    json_path = out / f"{record.record_id}.json"
    md_path = out / f"{record.record_id}.md"
    json_path.write_text(
        to_json(record, anonymiser=anonymiser, association=association,
                allow_real_identities=allow_real_identities), encoding="utf-8")
    md_path.write_text(
        render_markdown(record, anonymiser=anonymiser, association=association,
                        allow_real_identities=allow_real_identities), encoding="utf-8")
    return json_path, md_path


def write_dossier(
    records: Iterable[EvidenceRecord],
    out_dir: str | Path,
    *,
    associations: dict[str, Association] | None = None,
    allow_real_identities: bool = False,
) -> list[tuple[Path, Path]]:
    """
    Write a whole run, sharing ONE anonymiser across every record.

    That sharing is the entire reason this function exists rather than a loop at the
    call site: a dossier in which the same hull is 999000003 on page 2 and 999000007 on
    page 5 destroys the only thing a case file is for, which is following one vessel
    through the evidence.

    `associations` is keyed by association_id, which is what Verdict carries.
    """
    anon = Anonymiser()
    associations = associations or {}
    return [
        write_case_file(
            r, out_dir, anonymiser=anon,
            association=associations.get(r.verdict.association_id),
            allow_real_identities=allow_real_identities)
        for r in records
    ]
