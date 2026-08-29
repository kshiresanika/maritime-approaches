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

# Annotated is required for the discriminated union in section 8: the
# discriminator is attached as Field metadata on the union type itself.
from typing import Annotated, Literal

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

    detection_confidence: float = Field(
        ge=0.0, le=1.0,
        description="The EO detector's confidence that this box IS A VESSEL. Distinct "
                    "from observed_class_confidence, which is confidence in WHICH class "
                    "it is. YOLO answers the first question and cannot answer the "
                    "second; merging them mis-calibrates DARK confidence silently, with "
                    "no exception and no visible symptom.")

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


# ================================================================================
# 8. THE LIVE-CONSOLE LAYER.  Added 2026-08-29.  EXTENSION ONLY — nothing in
#    sections 1-7 was renamed, retyped or reordered by this addition.
#
# WHY A SECTION 8 EXISTS AT ALL
# Sections 1-7 describe ONE BATCH PASS: frames and claims go in, an EvidenceRecord
# comes out. The operator console is a second consumer of those same objects with a
# structurally different need — it is a LONG-LIVED VIEW that has to survive a browser
# refresh at 14:03 on Sunday with a judge standing behind the laptop. Three properties
# follow from that, and they are the only reason these models exist:
#
#   * ORDERING. A WebSocket delivers what it delivers. A dropped or reordered event
#     must be DETECTABLE, not merely unlikely. Every event carries a monotonic `seq`,
#     so the console can say "I last saw 412" and know whether it missed 413. Without
#     it, a lost queue_update leaves a stale ranking on screen and nothing anywhere
#     raises — the operator is looking at a ranking the pipeline no longer holds.
#   * RECOVERY. A refresh must not produce an empty page. ConsoleState is the entire
#     picture in one message, and it carries the `last_seq` it corresponds to, so the
#     client resumes the stream at a known point rather than guessing and silently
#     double-applying or skipping events.
#   * PROVENANCE OF THE PICTURE. A contact on screen with no sensor behind it is an
#     unattributable claim, and criterion 4 is about attributable ones. SensorNode
#     says which sensor produced what and whether that sensor was alive at the time,
#     so "the queue went quiet" can be distinguished from "the Pi died" — those are
#     opposite findings and they look identical on a map.
#
# THE CLAIMED/OBSERVED WALL STILL HOLDS HERE, and it is load-bearing.
# No event on this stream merges a claim into an observation. `contact_update` carries
# `ais_track` and `eo_contact` as TWO SEPARATE OPTIONAL FIELDS for exactly the reason
# section 1 gives, and which side is None is still the finding. A single merged
# "vessel" payload would be the easiest thing in the world to write for a UI — and it
# would delete criterion 3 from the product without producing a single error.
#
# STILL ZERO LOGIC, AND STILL NO WEB FRAMEWORK.
# No serialisation helpers, no dispatch tables, no FastAPI import — not even a
# TypeAdapter instance. This file must stay importable by `pi_sensor.py` on a
# Raspberry Pi that has no web stack installed at all; one FastAPI import here and the
# edge node stops booting for a reason that will take an hour to find at 03:00.
# To parse an inbound event, the console lane writes ONE line in its OWN module:
#
#     from pydantic import TypeAdapter
#     _EVENTS = TypeAdapter(StreamEvent)          # lane E's module, not this file
#     event = _EVENTS.validate_json(raw_ws_message)
#
# That line belongs to lane E because the transport is lane E's choice.
#
# ISO TIMESTAMPS FOR THE BROWSER
# `sent_at_utc` follows the project's `_utc` convention and is an AwareDatetime, which
# pydantic serialises to ISO-8601 in `model_dump_json()`. That is the wire format the
# browser reads with `new Date(...)`. The naive-datetime rejection from section 1
# therefore also protects the console: a naive timestamp would reach the browser
# without a zone and be silently interpreted as LOCAL time, shifting every event on
# the timeline by two hours in Hamburg in August. It fails at the boundary instead.
# ================================================================================

SensorKind = Literal[
    # RETIRED 2026-08-29. The Raspberry Pi node is no longer part of the rig; the EO
    # source is now a video this machine decodes. THE MEMBER IS KEPT, NOT DELETED,
    # because operator_audit.jsonl and every evidence record written before today
    # carries this value. A Literal that can no longer parse its own audit trail turns
    # a historical case file into a validation error, which is precisely the opposite
    # of what criterion 4 asks of us. Nothing emits it any more: source_switch.py no
    # longer offers it as a selectable source.
    "edge_pi",
    "mac_camera",    # a lens on THIS machine — the live-lens path
    "video_stream",  # imagery this machine DECODES and detects on, now: a video file
                     # on disk, or a network stream (HTTP/HLS/RTSP). This is the EO
                     # source that replaced the Pi.
    "file",          # a recorded SCENE replayed from disk — contacts already computed,
                     # not imagery being detected on now. The T1 guaranteed path.
                     #
                     # 'file' vs 'video_stream' is the difference between REPLAYING a
                     # finished observation and MAKING one. They are kept apart on
                     # purpose: collapsing them would let a projection be presented on
                     # a watch screen as an observation, which is the one substitution
                     # this whole tool exists to prevent.
]

OperatorActionType = Literal[
    "DISPATCH",  # the operator RECORDED a decision to send an asset. The system does
                 # not send anything and has no channel to. Decision support, never
                 # automated enforcement — this vocabulary is where that rule is
                 # visible in the data.
    "WATCH",     # keep it on screen, do not spend the asset yet
    "DISMISS",   # judged not worth the asset. Recorded, not deleted: a dismissal is
                 # part of the case file, and "what the operator chose not to chase"
                 # is exactly what an after-action review asks for.
    "EXPORT",    # emit the evidence record. The one action with an artefact.
]

StreamEventType = Literal[
    "contact_update",
    "verdict_update",
    "queue_update",
    "node_status",
    "operator_action_ack",
]


class SensorNode(_Contract):
    """
    One sensor feeding the picture, and whether it was alive.

    WHY `online` AND `last_seen` ARE BOTH HERE and neither is derived from the other:
    `online` is the shore station's current belief; `last_seen` is the evidence for
    that belief. An operator who sees "online" alone cannot tell a healthy node from
    a stale flag nobody cleared. Showing both makes the staleness visible, and a
    quiet queue stops being ambiguous — a node last seen 40 seconds ago that reports
    online is a genuinely empty sea, the same node last seen 11 minutes ago is a dead
    sensor being mistaken for one.
    """

    node_id: str
    kind: SensorKind
    online: bool = Field(
        description="The shore station's CURRENT belief about reachability. Not "
                    "derived from last_seen here — deriving it is lane E's policy "
                    "call, and a threshold in this file would be logic.")
    last_seen: AwareDatetime | None = Field(
        default=None,
        description="Time of the most recent message from this node. None means NEVER "
                    "HEARD FROM — a node that is configured but has not reported once. "
                    "Substituting 'now' for an unheard node would make a dead sensor "
                    "look healthy on the strip, which is the one thing this field is "
                    "for.")
    measured_fps: float | None = Field(
        default=None, ge=0.0,
        description="MEASURED, never nominal, never configured. None until something "
                    "actually counted frames. CLAUDE.md: measured numbers only — a "
                    "declared 30 next to a node delivering 4 is how a demo gets "
                    "questioned on stage and has no answer.")


class OperatorAction(_Contract):
    """
    What a HUMAN decided. Recorded, never executed.

    This object is the audit trail that makes 'decision support, not automated
    enforcement' checkable rather than merely stated: every entry names an operator
    and a time, and there is deliberately no field anywhere in this file for a machine
    to record a decision of its own. If it happened, a person chose it.
    """

    action: OperatorActionType
    contact_id: str = Field(
        description="The id the console displayed and the operator acted on. Must be "
                    "traceable back to an EoContact / Association, or the action "
                    "cannot be tied to the evidence that prompted it and the audit "
                    "trail has a hole in the middle of it.")
    operator_id: str = Field(
        description="WHO decided. An unattributed dispatch order is not a decision "
                    "record, it is an anonymous one.")
    action_time_utc: AwareDatetime = Field(
        description="WHEN they decided — which is not when the verdict was computed. "
                    "The gap between Verdict.decided_at_utc and this is operator "
                    "reaction time, and an after-action review asks for it.")
    note: str | None = Field(
        default=None,
        description="Free text from the operator. Human-written by definition — the "
                    "LLM has no route to this field, exactly as it has no route to a "
                    "Verdict.")


# --------------------------------------------------------------------------------
# 8b. The stream events.
#
# One base class carries the two fields that make the stream recoverable; each
# variant adds only its payload. The `event` tag is the pydantic discriminator, so an
# unknown or mistyped tag fails at validation instead of falling through to a default
# branch in the console's JavaScript and rendering nothing.
# --------------------------------------------------------------------------------

class _StreamEventBase(_Contract):
    """Shared envelope. Never sent on its own — only its subclasses appear on the
    wire."""

    seq: int = Field(
        ge=0,
        description="MONOTONIC per connection, gap-free. The console compares it "
                    "against the last seq it applied; a gap means it missed an event "
                    "and must re-request ConsoleState rather than keep painting. "
                    "Without this a lost queue_update leaves a stale ranking on "
                    "screen and nothing anywhere raises.")
    sent_at_utc: AwareDatetime = Field(
        description="Server send time, ISO-8601 on the wire. Distinct from the "
                    "payload's own timestamps: sent_at_utc says when the console was "
                    "TOLD, the payload says when the thing HAPPENED. Conflating them "
                    "makes pipeline latency invisible.")


class ContactUpdateEvent(_StreamEventBase):
    """
    One contact's current state.

    THE WALL, ON THE WIRE. `ais_track` and `eo_contact` are separate optional fields,
    never a merged 'vessel'. Which side is None is still the finding:
        ais_track None   -> observation with no claim  -> the DARK case
        eo_contact None  -> claim with no observation  -> the position-spoof case
    A UI-shaped merged payload would be the easiest thing here to write and would
    silently remove the comparison that criterion 3 consists of.
    """

    event: Literal["contact_update"] = "contact_update"
    association: Association | None = Field(
        default=None,
        description="None before the associator has run on this contact. The console "
                    "must be able to draw a fresh detection before it is adjudicated, "
                    "or the map lags the sea by a pipeline pass.")
    eo_contact: EoContact | None = None
    ais_track: AisTrack | None = None
    node_id: str | None = Field(
        default=None,
        description="Which SensorNode produced the observation. Transport-level "
                    "provenance, deliberately kept OFF EoContact: the contact object "
                    "is what the camera saw, and which box it was plugged into is not "
                    "an observed property of the vessel.")


class VerdictUpdateEvent(_StreamEventBase):
    """
    A verdict, with the comparisons that produced it.

    The mismatches ride along on purpose: the console has to show the operator
    CLAIMED 'fishing, 40 m' versus OBSERVED 'tanker, 248 m' at the moment the verdict
    lands. Making it fetch them separately means the screen can display a SPOOF label
    with no evidence beside it during the round trip — a bare accusation, which is
    the exact output criterion 4 forbids.
    """

    event: Literal["verdict_update"] = "verdict_update"
    verdict: Verdict
    mismatches: list[Mismatch] = Field(
        default_factory=list,
        description="Embedded snapshots, matching Verdict.mismatch_ids. Empty is "
                    "legitimate for a MATCH and for a DARK contact — there was "
                    "nothing to compare against.")


class QueueUpdateEvent(_StreamEventBase):
    """
    The ranked queue — criterion 2 on screen.

    The WHOLE queue is sent, not a delta. A ranking is a total order: if entry 3 is
    patched in isolation, the console is showing an order the prioritiser never
    produced, and 'send the boat here' stops being defensible. Whole-list replacement
    makes that class of bug impossible rather than rare.
    """

    event: Literal["queue_update"] = "queue_update"
    queue: list[PriorityScore] = Field(
        default_factory=list,
        description="Each entry carries its own rank plus component_scores and "
                    "component_weights, so the console can show WHY one contact "
                    "outranks another without recomputing anything. The weighting is "
                    "the argument; it travels with the score.")


class NodeStatusEvent(_StreamEventBase):
    """A sensor came up, went down, or re-reported its measured rate."""

    event: Literal["node_status"] = "node_status"
    node: SensorNode


class OperatorActionAckEvent(_StreamEventBase):
    """
    Confirmation that an operator's decision was RECORDED. Never that it was carried
    out — nothing in this system carries anything out.

    `accepted=False` matters more than it looks: a dispatch order that silently failed
    to persist leaves the operator believing a decision is on the record when it is
    not, and the audit trail then disagrees with the person who was there.
    """

    event: Literal["operator_action_ack"] = "operator_action_ack"
    action: OperatorAction
    accepted: bool = Field(
        description="True = written to the record. NOT 'the asset was tasked'. There "
                    "is no field on this object for an outcome because the system has "
                    "no mechanism to produce one.")
    detail: str | None = Field(
        default=None,
        description="Why it was rejected, when it was. Empty on success.")


StreamEvent = Annotated[
    ContactUpdateEvent
    | VerdictUpdateEvent
    | QueueUpdateEvent
    | NodeStatusEvent
    | OperatorActionAckEvent,
    Field(discriminator="event"),
]
"""
The discriminated union pushed over the WebSocket.

WHY DISCRIMINATED AND NOT A PLAIN UNION: pydantic tries a plain union member by
member and accepts the first that validates, so an event whose tag says
`queue_update` but whose body looks like a node_status would be accepted as the
wrong type — no error, wrong panel updates. The discriminator makes the tag
authoritative: an unknown or mistyped tag raises at the boundary, where it is one
line to find, instead of surfacing as a console that mysteriously stops updating.

Parse with a TypeAdapter in the consuming module, not here — see the section 8
header for why this file must stay free of that.
"""


class ConsoleState(_Contract):
    """
    The full snapshot the console needs on first connect.

    WHY THIS EXISTS AT ALL: a browser refresh mid-demo is not a hypothetical — it is
    what happens when the display flickers, the laptop sleeps, or a judge asks to see
    it on the big screen. An event stream alone cannot recover from that, because the
    events that built the current picture are already gone. Without this object a
    refresh at minute three of a four-minute pitch shows an empty page, and the only
    recovery is re-running the pipeline on stage.

    `last_seq` is the join between snapshot and stream: the client applies this state,
    then ignores every buffered event with seq <= last_seq. Without it the client
    either double-applies events it already has baked into the snapshot or skips ones
    it does not — and both look like a UI bug rather than a protocol bug, which is
    how an hour disappears.

    WHY EvidenceRecord AND NOT A FLAT LIST OF VERDICTS: EvidenceRecord already embeds
    the verdict, its mismatches, the claim, the observation and the priority as
    immutable snapshots (section 7). Re-listing those here would create a second copy
    that can disagree with the first. One source of truth, already frozen.
    """

    state_time_utc: AwareDatetime
    last_seq: int = Field(
        ge=0,
        description="The seq of the last event folded into this snapshot. The client "
                    "resumes at last_seq + 1. See the class docstring for what breaks "
                    "without it.")
    pipeline_version: str = Field(
        description="So a stale browser tab left open from the previous run is "
                    "identifiable as stale rather than believed.")

    nodes: list[SensorNode] = Field(
        default_factory=list,
        description="Empty means no sensor is configured — which is a different "
                    "finding from a sensor that is configured and offline, and the "
                    "console must be able to say which.")
    records: list[EvidenceRecord] = Field(
        default_factory=list,
        description="Adjudicated contacts, each self-contained. The ranked queue is "
                    "reconstructible from record.priority.rank — deliberately not "
                    "duplicated into a second ordered field that could drift out of "
                    "agreement with it.")
    unresolved_contacts: list[EoContact] = Field(
        default_factory=list,
        description="Seen but not yet adjudicated. Without these the map is blank for "
                    "every fresh detection until a verdict exists, and the operator "
                    "reads an empty screen as an empty sea.")
    recent_actions: list[OperatorAction] = Field(
        default_factory=list,
        description="So a refresh does not lose 'I already dismissed that one' and "
                    "re-present a contact the operator has already judged.")
