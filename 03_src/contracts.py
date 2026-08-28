"""
contracts.py — the seam between every lane. ARCH-owned.

WHY THIS FILE EXISTS
CLAUDE.md: "All cross-module types come from 03_src/contracts.py. Never pass a bare
dict across a module boundary." Four people are about to write four modules in
parallel. Without a shared vocabulary they each invent their own contact shape, and
association.py inherits the job of reconciling them at 04:00. This file is the
vocabulary.

THE ONE IDEA THAT MATTERS
There is a hard wall between what AIS CLAIMS and what the camera OBSERVES.

    AisTrack   carries claimed_* fields ONLY.  Nothing observed may be written to it.
    EoContact  carries observed_* fields ONLY. It has no MMSI, no name, no identity.
    Mismatch   is the ONLY place the two meet, one claimed field against one observed
               field, carrying both literal values so the report can quote them.

That wall is judging criterion 3. A vessel with its transponder off is easy — an EO
contact with no AIS track. A vessel broadcasting a false identity is only detectable
if the claim and the observation are held apart long enough to be compared. Collapse
them into one "vessel" object and the spoofing detector cannot exist.

ZERO LOGIC BY DESIGN
No methods, no validators, no computation, no defaults that encode a policy. Only
types, units, and declarative constraints. Logic lives in the lane that owns it:
association.py decides what pairs, consistency.py decides what mismatches, verdict.py
decides the label and confidence, prioritizer.py decides the ranking. If you find
yourself wanting to add a function here, you want it in your own module.

UNIT CONVENTIONS — fixed project-wide, encoded in every field name
  * Angles are DEGREES, never radians, at every boundary. Suffix _deg.
  * All bearings/courses/headings are degrees TRUE, 0-360 clockwise from true north.
    Suffix _deg_true. Magnetic is never used; if a rig reports magnetic, declination
    is applied at ingest.
  * Relative bearing is a SEPARATE field (_rel_deg, signed -180..180 from boresight).
    True and relative never share a field. This is how the silent 180-degree error
    gets caught.
  * Distances are METRES internally (_m). Nautical miles appear only in
    EvidenceRecord.range_nm, for human display. One conversion point.
  * Speeds are METRES PER SECOND internally (_ms). Knots only where AIS hands them
    over (claimed_sog_kn) and in display.
  * Positions are WGS84 signed decimal degrees, floats. N and E positive.
  * Times are timezone-aware UTC. AwareDatetime rejects naive datetimes at the
    boundary, because the DMA CSV is dd/mm/yyyy and a naive datetime is how a
    parsing bug travels silently into a verdict.
  * Confidences and scores are unitless floats 0.0-1.0, higher = better/more urgent.
    Never percentages.
  * Identifiers are STRINGS, including MMSI and IMO. Leading zeros are significant
    and these are not quantities.
  * None means "not available". It never means zero, and it never means "no
    mismatch". A missing claim is itself a finding.

FROZEN BY DESIGN
Every model is frozen. Evidence that can be mutated after the fact is not evidence,
and a Verdict that changes under a PriorityScore's feet is a bug nobody will find in
48 hours. Build a new object instead of mutating one.
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------------
# Controlled vocabularies.
#
# WHY Literal rather than free strings: observed_class and claimed_ship_type are
# compared against each other. If one lane writes "Tanker" and another writes
# "tanker", the class-mismatch check becomes a string-matching lottery and criterion
# 3 fails silently — it will not error, it will simply never fire. Fixing the
# vocabulary here makes that impossible.
# --------------------------------------------------------------------------------

VesselClass = Literal[
    "cargo", "tanker", "fishing", "passenger", "tug", "naval", "small_craft", "unknown"
]

VerdictLabel = Literal[
    "MATCH",    # claim and observation agree within tolerance
    "DARK",     # observation with no corresponding claim (transponder off)
    "SPOOF",    # claim and observation disagree beyond tolerance
    "UNKNOWN",  # the machine-readable defer-to-human state. NOT optional.
]

SpoofSubtype = Literal[
    "identity",   # false MMSI / name / ship type
    "position",   # broadcasting a position the vessel is not at
    "kinematic",  # impossible speeds, teleports, tracks through land
]

MismatchDimension = Literal[
    "class", "length", "width", "heading", "cog", "speed", "position"
]

MismatchUnit = Literal["m", "deg_true", "m_s", "kn", "class_label", "none"]

Severity = Literal["minor", "major", "critical"]

DeferReason = Literal[
    "ambiguous_association",   # a rival pairing scored comparably
    "no_range_estimate",       # monocular range unavailable, so size checks are weak
    "low_observation_confidence",
    "stale_ais",               # the claim is old enough to explain the delta
    "single_frame_contact",    # no motion, so speed/heading cannot be observed
    "claim_field_missing",     # AIS never broadcast the field being compared
    "sparse_ais_coverage",     # gap may be receiver geometry, not evasion
    "low_confidence",
]


class _Contract(BaseModel):
    """
    Shared config. extra="forbid" so a typo'd field name fails loudly at the boundary
    instead of vanishing; frozen=True so no downstream lane can quietly rewrite
    another lane's output.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------------
# 1. AisTrack — CLAIMED attributes only. Owned by lane A (AIS).
# --------------------------------------------------------------------------------

class AisTrack(_Contract):
    """What a transponder asserts. Every field is an assertion, not a fact."""

    track_id: str = Field(description="Internal, run-stable. NOT the MMSI: MMSI is "
                                      "itself spoofable and non-unique, so it cannot "
                                      "be a join key.")
    claimed_mmsi: str = Field(description="9 digits as a STRING; leading zeros matter.")
    report_time_utc: AwareDatetime

    claimed_lat_deg: float = Field(ge=-90.0, le=90.0)
    claimed_lon_deg: float = Field(ge=-180.0, le=180.0)

    # Marked "if available" in the DMA schema — genuinely often absent.
    claimed_sog_kn: float | None = Field(default=None, ge=0.0)
    claimed_cog_deg_true: float | None = Field(default=None, ge=0.0, lt=360.0)
    claimed_heading_deg_true: float | None = Field(
        default=None, ge=0.0, lt=360.0,
        description="Distinct from COG: a vessel can point one way and travel "
                    "another. Never collapse the two. Raw AIS 511 means "
                    "unavailable and must become None at ingest.")
    claimed_rot_deg_per_min: float | None = Field(default=None)

    # Static/voyage block. Class B transponders broadcast this sparsely or not at all.
    claimed_ship_type: VesselClass | None = Field(
        default=None,
        description="The primary claimed-vs-observed comparison. Lane A maps the raw "
                    "AIS string onto this vocabulary at ingest.")
    claimed_length_m: float | None = Field(default=None, ge=0.0)
    claimed_width_m: float | None = Field(default=None, ge=0.0)
    claimed_size_a_m: float | None = Field(default=None, ge=0.0)
    claimed_size_b_m: float | None = Field(default=None, ge=0.0)
    claimed_size_c_m: float | None = Field(default=None, ge=0.0)
    claimed_size_d_m: float | None = Field(default=None, ge=0.0)

    claimed_name: str | None = None
    claimed_imo: str | None = None
    claimed_callsign: str | None = None
    claimed_nav_status: str | None = None

    mobile_class: str | None = Field(
        default=None,
        description='"Class A" / "Class B". Carry it: a blank ship type is NORMAL on '
                    "Class B and SUSPICIOUS on Class A. Without it, missingness gets "
                    "mis-weighted.")


# --------------------------------------------------------------------------------
# 2. EoContact — OBSERVED attributes only. Owned by lane B (EO + Geometry).
# --------------------------------------------------------------------------------

class EoContact(_Contract):
    """
    What the camera sees. No MMSI, no name, no identity of any kind — that is the
    point. An identity on this object would let a claim masquerade as an observation.
    """

    contact_id: str
    frame_time_utc: AwareDatetime
    frame_ref: str = Field(description="Path or frame id. Criterion 4: evidence must "
                                       "point back at the image it came from.")
    bbox_px: tuple[int, int, int, int] = Field(
        description="(x_min, y_min, x_max, y_max), origin top-left.")

    observed_bearing_deg_true: float = Field(
        ge=0.0, lt=360.0,
        description="Criterion 1 requires bearing out. TRUE, not relative, not "
                    "magnetic.")
    bearing_uncertainty_deg: float = Field(
        ge=0.0,
        description="1-sigma half-width. Position mismatch is meaningless without it.")
    observed_bearing_rel_deg: float | None = Field(
        default=None, ge=-180.0, le=180.0,
        description="Relative to camera boresight. Separate field on purpose.")

    observed_range_m: float | None = Field(default=None, ge=0.0)
    range_uncertainty_m: float | None = Field(
        default=None, ge=0.0,
        description="Set whenever range is set. A monocular range with no error bar "
                    "cannot support a SPOOF claim.")
    observed_lat_deg: float | None = Field(default=None, ge=-90.0, le=90.0)
    observed_lon_deg: float | None = Field(default=None, ge=-180.0, le=180.0)

    observed_class: VesselClass | None = Field(
        default=None, description="Apparent silhouette class from the VLM.")
    observed_class_confidence: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Set whenever class is set. A low-confidence class must not raise "
                    "a SPOOF on its own.")

    observed_length_m: float | None = Field(default=None, ge=0.0)
    observed_length_uncertainty_m: float | None = Field(default=None, ge=0.0)
    observed_heading_deg_true: float | None = Field(default=None, ge=0.0, lt=360.0)
    observed_speed_ms: float | None = Field(
        default=None, ge=0.0, description="SI internally. Convert to knots once, at "
                                          "comparison time.")

    track_length_frames: int = Field(
        ge=1,
        description="A single-frame contact cannot support any motion-derived "
                    "mismatch. Downstream must know whether to trust speed/heading.")
    camera_pose_ref: str = Field(
        description="Every bearing is only as trustworthy as the pose that produced "
                    "it. Disputes resolve here.")


# --------------------------------------------------------------------------------
# 3. Association — the pairing. Owned by lane C (Fusion).
# --------------------------------------------------------------------------------

class Association(_Contract):
    """
    A proposed pairing. Either side may be None, and which side is None IS the
    finding:
        track_id None   -> observation with no claim  -> the DARK case
        contact_id None -> claim with no observation  -> the position-spoof case
    """

    association_id: str
    assoc_time_utc: AwareDatetime
    contact_id: str | None = None
    track_id: str | None = None

    assoc_score: float = Field(ge=0.0, le=1.0)
    assoc_ambiguous: bool = Field(
        description="True when a rival candidate scored comparably. A defer-to-human "
                    "trigger: a mismatch computed off an ambiguous pairing is not "
                    "evidence.")
    candidate_count: int = Field(
        ge=0,
        description="Zero candidates in a busy area means something different from "
                    "zero in an empty one. Feeds DARK confidence.")
    runner_up_score: float | None = Field(default=None, ge=0.0, le=1.0)

    time_delta_s: float | None = Field(
        default=None,
        description="Signed, ais minus eo. A 90-second-stale report explains an "
                    "apparent position mismatch. Without this you call staleness a "
                    "spoof.")
    spatial_gap_m: float | None = Field(default=None, ge=0.0)


# --------------------------------------------------------------------------------
# 4. Mismatch — one claimed field against one observed field. Owned by lane C.
# --------------------------------------------------------------------------------

class Mismatch(_Contract):
    """
    The atomic unit criterion 4 quotes. It carries BOTH literal values and BOTH
    source field names so a report sentence can be reconstructed mechanically,
    without reaching back into any module's internals.
    """

    mismatch_id: str
    association_id: str
    dimension: MismatchDimension

    claimed_value: float | str
    claimed_field: str = Field(description="Provenance: which AIS field made the claim.")
    observed_value: float | str
    observed_field: str = Field(description="Provenance on the observed side.")

    unit: MismatchUnit = Field(
        description="Carried per-mismatch so a report never prints a bare number, and "
                    "so a unit bug shows up in the output instead of hiding.")
    delta: float | None = Field(
        default=None,
        description="None for categorical dimensions. For angles: SIGNED SHORTEST ARC "
                    "in -180..180. 359 vs 1 is +2, not 358.")
    tolerance: float | None = Field(
        default=None,
        description="The threshold this delta was judged against. Without it, "
                    '"mismatch" is an unexplained assertion.')
    significance: float = Field(
        ge=0.0,
        description="Delta over combined uncertainty, in sigmas. THIS, not raw delta, "
                    "separates a spoof from a fuzzy range estimate.")

    explained_by_staleness: bool = Field(
        description="A position delta consistent with time_delta_s * SOG is not "
                    "evidence of anything. Flag it before it reaches the verdict.")
    severity: Severity
    observation_confidence: float = Field(ge=0.0, le=1.0)


# --------------------------------------------------------------------------------
# 5. Verdict — deterministic output. Owned by lane C (verdict.py).
# --------------------------------------------------------------------------------

class Verdict(_Contract):
    """
    THE PIPELINE PRODUCES THIS. THE LLM NEVER DOES.

    CLAUDE.md is explicit and judges will look for it: the deterministic pipeline
    produces the verdict and the confidence; the LLM produces the rationale only.
    The rationale lives on EvidenceRecord, in its own field, attributed to a named
    model. There is deliberately nowhere on this object to put model-generated text.
    """

    verdict_id: str
    association_id: str
    label: VerdictLabel
    confidence: float = Field(
        ge=0.0, le=1.0, description="Deterministically computed. Criterion 2 needs "
                                    '"confidence Y" beside the recommendation.')
    mismatch_ids: list[str] = Field(
        default_factory=list,
        description="Traceability: which specific comparisons produced this label.")
    decided_at_utc: AwareDatetime

    spoof_subtype: SpoofSubtype | None = Field(
        default=None,
        description='Set only when label == "SPOOF". Criterion 3 asks WHICH deception '
                    'was caught; "SPOOF" alone under-sells the work.')

    defer_to_human: bool = Field(
        description="Explicit escalation flag. The ethical floor and criterion 4.")
    defer_reasons: list[DeferReason] = Field(
        default_factory=list,
        description="Empty when not deferring. The report must say WHY it defers, not "
                    "merely that it does.")

    ruleset_version: str = Field(
        description="Thresholds move during a hackathon. A verdict without its "
                    "ruleset version is unreproducible.")


# --------------------------------------------------------------------------------
# 6. PriorityScore — criterion 2, the highest-weighted file. Owned by lane D.
# --------------------------------------------------------------------------------

class PriorityScore(_Contract):
    """
    "Send the boat HERE, and here is why." A bare scalar is not a defensible
    recommendation, which is why the components and their weights are both on the
    object: the weighting IS the argument, and it must be inspectable rather than
    buried inside the scorer.
    """

    verdict_id: str
    score: float = Field(ge=0.0, le=1.0, description="Higher = more urgent.")
    rank: int = Field(ge=1, description="1-based. Criterion 2 demands a RANKED list, "
                                        "not a flat alert list.")

    component_scores: dict[str, float] = Field(
        default_factory=dict,
        description="e.g. verdict_severity, infrastructure_proximity, "
                    "behaviour_anomaly, confidence, recency. Each 0-1.")
    component_weights: dict[str, float] = Field(
        default_factory=dict, description="Unitless, intended to sum to 1.0.")

    nearest_infrastructure_m: float | None = Field(
        default=None, ge=0.0,
        description="The brief's named scenario is loitering over a cable route. "
                    "Weight this heavily.")
    nearest_infrastructure_id: str | None = None
    nearest_asset_km: float | None = Field(default=None, ge=0.0)
    time_to_intercept_min: float | None = Field(default=None, ge=0.0)

    scarce_asset_recommended: bool = Field(
        description="The tasking recommendation, kept separable from the raw score.")


# --------------------------------------------------------------------------------
# 7. EvidenceRecord — criterion 4. Owned by lane D.
# --------------------------------------------------------------------------------

class EvidenceRecord(_Contract):
    """
    The beginning of a case file, not an alarm.

    Sub-objects are EMBEDDED SNAPSHOTS, not references. A record pointing at a
    mutable verdict is not evidence. Attribution in this domain is legally fragile —
    cases have failed and suspect vessels have been released — so the record must
    stand on its own after the fact.
    """

    record_id: str
    created_at_utc: AwareDatetime

    verdict: Verdict
    mismatches: list[Mismatch] = Field(
        default_factory=list, description="Empty is legitimate for a MATCH.")
    ais_track: AisTrack | None = Field(
        default=None, description="None for a dark vessel — there was no claim.")
    eo_contact: EoContact | None = Field(
        default=None, description="None for a pure position spoof — nothing was seen.")
    priority: PriorityScore | None = None

    identity_claimed: dict[str, str | None] = Field(
        default_factory=dict,
        description="MMSI / IMO / name / callsign in one printable block, explicitly "
                    "labelled CLAIMED. Never asserted as fact — naming a real vessel "
                    "as a saboteur is defamatory.")
    bearing_deg_true: float | None = Field(default=None, ge=0.0, lt=360.0)
    range_nm: float | None = Field(
        default=None, ge=0.0,
        description="THE single point where metres become nautical miles. Operators "
                    "read NM. Convert here and nowhere else.")
    frame_refs: list[str] = Field(default_factory=list)

    rationale_text: str | None = Field(
        default=None,
        description="LLM-GENERATED. Its own field, so the deterministic/LLM split in "
                    "the architecture diagram is visible in the data too.")
    rationale_model: str | None = Field(
        default=None,
        description="Set whenever rationale is set. A rationale from an unnamed model "
                    "is not evidence-grade.")

    limitations: list[str] = Field(
        default_factory=list,
        description='The "honest limits" half of criterion 4. Seed from '
                    "verdict.defer_reasons and from every None above that mattered.")
    pipeline_version: str
