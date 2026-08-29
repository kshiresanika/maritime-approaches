"""
tests/test_contracts.py — the guardrails, as executable assertions.

WHY THIS FILE EXISTS
CLAUDE.md's rules are prose, and prose does not survive 03:00. These tests turn the
non-negotiable ones into failures:

  * the CLAIMED / OBSERVED wall (criterion 3 cannot exist without it)
  * the verdict carries a confidence and an explicit defer-to-human flag
  * the LLM writes the rationale, never the verdict — asserted structurally
  * units and ranges are constrained, so a radians-for-degrees bug fails loudly
  * evidence is immutable

These are contract tests, not algorithm tests. They assert nothing about whether a
verdict is CORRECT — only that the shape a lane hands across a boundary is the shape
the next lane expects.
"""

import ast
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

import fixtures as fx
from contracts import (
    AisTrack,
    Association,
    ConsoleState,
    ContactUpdateEvent,
    EoContact,
    EvidenceRecord,
    Mismatch,
    NodeStatusEvent,
    OperatorAction,
    OperatorActionAckEvent,
    PriorityScore,
    QueueUpdateEvent,
    SensorNode,
    StreamEvent,
    Verdict,
    VerdictUpdateEvent,
)

# The batch-pipeline contracts (sections 1-7 of contracts.py).
PIPELINE_MODELS = [
    AisTrack, EoContact, Association, Mismatch, Verdict, PriorityScore, EvidenceRecord
]

# The live-console contracts (section 8). Listed separately so it is obvious which
# additions are new, then folded into ALL_MODELS so the frozen / extra="forbid"
# guarantees are enforced on them too rather than only on the older models.
CONSOLE_MODELS = [
    SensorNode, OperatorAction, ContactUpdateEvent, VerdictUpdateEvent,
    QueueUpdateEvent, NodeStatusEvent, OperatorActionAckEvent, ConsoleState,
]

ALL_MODELS = PIPELINE_MODELS + CONSOLE_MODELS

# Parsing the wire format is the CONSUMER's job, never contracts.py's — the edge node
# imports contracts.py and must not need a web stack. This is the one line lane E
# writes in its own module; the tests do the same rather than reaching for a helper
# that does not exist.
STREAM_EVENTS = TypeAdapter(StreamEvent)


# --------------------------------------------------------------------------------
# The CLAIMED / OBSERVED wall. Criterion 3 rests entirely on this.
# --------------------------------------------------------------------------------

def test_ais_track_carries_only_claimed_attributes():
    """No field on AisTrack may describe an observation."""
    for name in AisTrack.model_fields:
        assert not name.startswith("observed_"), (
            f"AisTrack.{name} looks like an observation. AIS claims; it does not "
            f"observe. Collapsing the two removes the mismatch that IS criterion 3."
        )


def test_eo_contact_carries_only_observed_attributes():
    """No field on EoContact may carry an identity or a claim."""
    forbidden = ("claimed_", "mmsi", "imo", "callsign", "name", "ship_type")
    for name in EoContact.model_fields:
        assert not name.startswith("claimed_"), f"EoContact.{name} is a claim."
        # observed_class is legitimate; a bare identity field is not.
        assert name not in ("mmsi", "imo", "callsign", "claimed_name"), (
            f"EoContact.{name}: a camera cannot observe an identity. Identity comes "
            f"only from AIS, and only as a CLAIM."
        )
    assert "claimed_mmsi" not in EoContact.model_fields


def test_observed_value_cannot_be_smuggled_onto_an_ais_track():
    """extra='forbid' means a typo or a smuggled field fails at the boundary rather
    than vanishing silently into an ignored attribute."""
    with pytest.raises(ValidationError):
        fx.make_ais_track(observed_class="tanker")


def test_claimed_value_cannot_be_smuggled_onto_an_eo_contact():
    with pytest.raises(ValidationError):
        fx.make_eo_contact(claimed_mmsi="999123456")


def test_mismatch_is_the_only_place_claim_and_observation_meet():
    """A Mismatch must carry BOTH literal values and BOTH source field names, so the
    report can quote the comparison without reaching into module internals."""
    m = fx.make_mismatch()
    for field in ("claimed_value", "claimed_field", "observed_value",
                  "observed_field", "unit", "significance"):
        assert field in Mismatch.model_fields
        assert getattr(m, field) is not None
    assert m.claimed_value != m.observed_value


# --------------------------------------------------------------------------------
# The LLM/verdict separation — asserted structurally, not by convention.
# --------------------------------------------------------------------------------

def test_verdict_has_nowhere_to_put_model_generated_text():
    """CLAUDE.md: the deterministic pipeline produces the VERDICT and CONFIDENCE; the
    LLM produces the RATIONALE only. If a rationale field ever appears on Verdict,
    that separation has been quietly broken — and judges are told to look for it."""
    for banned in ("rationale", "rationale_text", "explanation", "narrative",
                   "llm_text", "summary"):
        assert banned not in Verdict.model_fields, (
            f"Verdict.{banned} would let model-generated text ride on the "
            f"deterministic verdict."
        )


def test_rationale_lives_on_the_evidence_record_with_its_model_named():
    assert "rationale_text" in EvidenceRecord.model_fields
    assert "rationale_model" in EvidenceRecord.model_fields


# --------------------------------------------------------------------------------
# Confidence and defer-to-human — the ethical floor and criterion 4.
# --------------------------------------------------------------------------------

def test_every_verdict_carries_confidence_and_an_explicit_defer_flag():
    assert "confidence" in Verdict.model_fields
    assert "defer_to_human" in Verdict.model_fields
    # Neither may be optional: a verdict without them is exactly what the brief says
    # not to ship.
    assert Verdict.model_fields["defer_to_human"].is_required()
    assert Verdict.model_fields["confidence"].is_required()


def test_unknown_is_a_representable_verdict_label():
    """UNKNOWN is the machine-readable 'I do not know'. Without it, a pipeline under
    pressure is forced to guess between MATCH and SPOOF."""
    v = fx.make_verdict(label="UNKNOWN", confidence=0.31, defer_to_human=True,
                        defer_reasons=["ambiguous_association"], spoof_subtype=None)
    assert v.label == "UNKNOWN"
    assert v.defer_to_human is True
    assert "ambiguous_association" in v.defer_reasons


def test_confidence_is_a_unit_interval_not_a_percentage():
    fx.make_verdict(confidence=0.0)
    fx.make_verdict(confidence=1.0)
    with pytest.raises(ValidationError):
        fx.make_verdict(confidence=79.0)      # someone typed a percentage
    with pytest.raises(ValidationError):
        fx.make_verdict(confidence=-0.1)


def test_verdict_label_vocabulary_is_closed():
    with pytest.raises(ValidationError):
        fx.make_verdict(label="SUSPICIOUS")   # not one of ours


def test_defer_reasons_vocabulary_is_closed():
    with pytest.raises(ValidationError):
        fx.make_verdict(defer_reasons=["felt_wrong"])


# --------------------------------------------------------------------------------
# Units and ranges. A radians-for-degrees bug must fail here, not on stage.
# --------------------------------------------------------------------------------

def test_bearings_are_degrees_and_wrap_is_rejected():
    fx.make_eo_contact(observed_bearing_deg_true=0.0)
    fx.make_eo_contact(observed_bearing_deg_true=359.9)
    with pytest.raises(ValidationError):
        fx.make_eo_contact(observed_bearing_deg_true=360.0)   # wrap belongs at 0
    with pytest.raises(ValidationError):
        fx.make_eo_contact(observed_bearing_deg_true=-1.0)

    # HONEST LIMIT OF THIS TEST: a radians value such as 3.14 is a perfectly valid
    # bearing of 3.14 degrees, so no range constraint can catch a radians-for-degrees
    # bug. Nothing here will save you. The only defences are the _deg_true naming
    # convention and review — which is exactly why every angular field in
    # contracts.py carries its unit in the name.
    fx.make_eo_contact(observed_bearing_deg_true=3.14)


def test_latitude_and_longitude_bounds():
    with pytest.raises(ValidationError):
        fx.make_ais_track(claimed_lat_deg=91.0)
    with pytest.raises(ValidationError):
        fx.make_ais_track(claimed_lon_deg=-181.0)


def test_relative_bearing_is_a_separate_field_from_true_bearing():
    """Sharing one field is how a silent 180-degree error survives to the demo."""
    assert "observed_bearing_deg_true" in EoContact.model_fields
    assert "observed_bearing_rel_deg" in EoContact.model_fields


def test_naive_datetimes_are_rejected_at_every_boundary():
    """The DMA CSV is dd/mm/yyyy local-to-basestation. A naive datetime is how a
    parsing bug travels undetected into a verdict."""
    naive = datetime(2026, 8, 25, 14, 2, 11)
    with pytest.raises(ValidationError):
        fx.make_ais_track(report_time_utc=naive)
    with pytest.raises(ValidationError):
        fx.make_eo_contact(frame_time_utc=naive)
    aware = naive.replace(tzinfo=timezone.utc)
    assert fx.make_ais_track(report_time_utc=aware).report_time_utc.tzinfo is not None


def test_identifiers_are_strings_not_integers():
    """Leading zeros are significant; an MMSI is not a quantity."""
    t = fx.make_ais_track()
    assert isinstance(t.claimed_mmsi, str)
    assert AisTrack.model_fields["claimed_mmsi"].annotation is str


def test_priority_rank_is_one_based():
    fx.make_priority_score(rank=1)
    with pytest.raises(ValidationError):
        fx.make_priority_score(rank=0)


# --------------------------------------------------------------------------------
# Criterion 2 — the ranking must be inspectable, not a bare scalar.
# --------------------------------------------------------------------------------

def test_priority_score_exposes_its_components_and_weights():
    """'Send the boat here' is only defensible if the weighting is visible."""
    p = fx.make_priority_score()
    assert p.component_scores, "a bare score is not an argument"
    assert p.component_weights, "the weighting IS the argument"
    assert set(p.component_scores) == set(p.component_weights)
    assert abs(sum(p.component_weights.values()) - 1.0) < 1e-9


def test_infrastructure_proximity_is_a_first_class_field():
    """The brief's named scenario is loitering over a cable route."""
    assert "nearest_infrastructure_m" in PriorityScore.model_fields
    assert "nearest_infrastructure_id" in PriorityScore.model_fields


# --------------------------------------------------------------------------------
# Association — which side is None IS the finding.
# --------------------------------------------------------------------------------

def test_association_allows_a_missing_ais_track_for_the_dark_case():
    a = fx.make_association(track_id=None)
    assert a.track_id is None and a.contact_id is not None


def test_association_allows_a_missing_eo_contact_for_the_position_spoof_case():
    a = fx.make_association(contact_id=None)
    assert a.contact_id is None and a.track_id is not None


def test_association_carries_the_ambiguity_signals_that_gate_deferral():
    for field in ("assoc_ambiguous", "runner_up_score", "candidate_count",
                  "time_delta_s"):
        assert field in Association.model_fields


# --------------------------------------------------------------------------------
# Evidence must be immutable and self-contained.
# --------------------------------------------------------------------------------

@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_all_contracts_are_frozen(model):
    assert model.model_config.get("frozen") is True


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_all_contracts_forbid_extra_fields(model):
    assert model.model_config.get("extra") == "forbid"


def test_a_verdict_cannot_be_edited_after_the_fact():
    v = fx.make_verdict()
    with pytest.raises(ValidationError):
        v.confidence = 0.99


def test_evidence_record_embeds_snapshots_not_references():
    """A record pointing at a mutable verdict is not evidence. Attribution here is
    legally fragile; the record must stand on its own after the fact."""
    rec = fx.make_evidence_record()
    assert isinstance(rec.verdict, Verdict)
    assert isinstance(rec.mismatches[0], Mismatch)
    assert isinstance(rec.ais_track, AisTrack)
    assert isinstance(rec.eo_contact, EoContact)


def test_evidence_record_has_the_four_things_criterion_four_demands():
    """Identity, track, timestamp, rationale — plus honest limits."""
    rec = fx.make_evidence_record()
    assert rec.identity_claimed.get("mmsi")          # identity (CLAIMED, labelled)
    assert rec.frame_refs                            # the imagery it rests on
    assert rec.created_at_utc.tzinfo is not None     # timestamp
    assert "rationale_text" in EvidenceRecord.model_fields
    assert rec.limitations, "honest limits are required, not decorative"


def test_range_is_converted_to_nautical_miles_exactly_once():
    """Metres internally, NM only for the operator. One conversion point."""
    rec = fx.make_evidence_record()
    assert rec.eo_contact is not None
    expected_nm = round(rec.eo_contact.observed_range_m / 1852.0, 2)
    assert rec.range_nm == pytest.approx(expected_nm)


def test_evidence_record_round_trips_through_json():
    """It has to survive being written to disk and read back, or it is not a case
    file."""
    rec = fx.make_evidence_record()
    restored = EvidenceRecord.model_validate_json(rec.model_dump_json())
    assert restored == rec


# --------------------------------------------------------------------------------
# Fixtures — lane C must be able to start before A and B deliver.
# --------------------------------------------------------------------------------

def test_fixtures_are_deterministic():
    """An intermittently failing test at 03:00 is worse than no test."""
    assert fx.make_ais_track(seed=7) == fx.make_ais_track(seed=7)
    assert fx.make_eo_contact(seed=7) == fx.make_eo_contact(seed=7)


def test_fixture_identities_are_synthetic_and_cannot_collide_with_a_real_vessel():
    """constraints.md item 2 is non-negotiable. 999-prefixed MMSIs are not issued."""
    for t in fx.make_ais_population(n=15):
        assert t.claimed_mmsi.startswith("999")
        assert t.claimed_name.startswith("SYNTH-")


def test_scenario_match_agrees_on_class_and_length():
    track, contact = fx.scenario_match()
    assert track.claimed_ship_type == contact.observed_class
    assert abs(track.claimed_length_m - contact.observed_length_m) < 20.0


def test_scenario_dark_has_an_observation_and_no_claim():
    contact = fx.scenario_dark()
    assert isinstance(contact, EoContact)
    assert contact.observed_class is not None


def test_scenario_identity_spoof_disagrees_on_two_independent_dimensions():
    """One flagged dimension is arguable. Two is a case. Criterion 3 lives here."""
    track, contact = fx.scenario_identity_spoof()
    assert track.claimed_ship_type != contact.observed_class
    assert track.claimed_length_m < 100.0
    assert contact.observed_length_m > 200.0
    # And the discrepancy must be large relative to the observation error, or it is
    # not evidence of anything.
    sigma = contact.observed_length_uncertainty_m
    assert (contact.observed_length_m - track.claimed_length_m) / sigma > 3.0


def test_scenario_ambiguous_association_offers_two_comparable_candidates():
    contact, candidates = fx.scenario_ambiguous_association()
    assert len(candidates) == 2
    assert candidates[0].claimed_ship_type == candidates[1].claimed_ship_type
    assert abs(candidates[0].claimed_lat_deg - candidates[1].claimed_lat_deg) < 0.01


def test_lane_c_can_build_a_full_chain_from_fixtures_alone():
    """The point of the whole file: fusion and evidence can be developed before lane
    A parses a single CSV row or lane B detects a single vessel."""
    contact, candidates = fx.scenario_ambiguous_association()
    assoc = fx.make_association(contact_id=contact.contact_id,
                                track_id=candidates[0].track_id,
                                assoc_ambiguous=True, runner_up_score=0.83)
    verdict = fx.make_verdict(association_id=assoc.association_id, label="UNKNOWN",
                              confidence=0.34, defer_to_human=True,
                              defer_reasons=["ambiguous_association"],
                              spoof_subtype=None)
    rec = fx.make_evidence_record(verdict=verdict, eo_contact=contact,
                                  ais_track=candidates[0])
    assert rec.verdict.defer_to_human is True
    assert rec.verdict.label == "UNKNOWN"


# ================================================================================
# THE LIVE-CONSOLE LAYER — contracts.py section 8.
#
# These are still contract tests. They assert nothing about whether the console is
# CORRECT; only that the shape lane E puts on the wire is the shape the browser and
# the edge node can consume, and that the guarantees sections 1-7 depend on are not
# quietly dropped the moment the data leaves Python.
# ================================================================================

_CONSOLE_T0 = fx.T0


def _node(**overrides) -> SensorNode:
    base = dict(node_id="node-pi-01", kind="edge_pi", online=True,
                last_seen=_CONSOLE_T0, measured_fps=28.4)
    base.update(overrides)
    return SensorNode(**base)


def _action(**overrides) -> OperatorAction:
    base = dict(action="DISPATCH", contact_id="eo-0001", operator_id="watch-officer-2",
                action_time_utc=_CONSOLE_T0 + timedelta(seconds=42),
                note="Cross-checked against the cable route overlay.")
    base.update(overrides)
    return OperatorAction(**base)


def _console_state(**overrides) -> ConsoleState:
    rec = fx.make_evidence_record()
    base = dict(
        state_time_utc=_CONSOLE_T0,
        last_seq=412,
        pipeline_version="0.1.0-fixture",
        nodes=[_node(), _node(node_id="node-mac-01", kind="mac_camera",
                              online=False, last_seen=None, measured_fps=None)],
        records=[rec],
        unresolved_contacts=[fx.make_eo_contact(seed=77)],
        recent_actions=[_action()],
    )
    base.update(overrides)
    return ConsoleState(**base)


def _one_of_each_event() -> list:
    """One instance of every StreamEvent variant, with distinct seq values so an
    ordering assertion means something."""
    return [
        ContactUpdateEvent(
            seq=1, sent_at_utc=_CONSOLE_T0,
            eo_contact=fx.make_eo_contact(), ais_track=None,
            association=fx.make_association(track_id=None), node_id="node-pi-01"),
        VerdictUpdateEvent(
            seq=2, sent_at_utc=_CONSOLE_T0,
            verdict=fx.make_verdict(), mismatches=[fx.make_mismatch()]),
        QueueUpdateEvent(
            seq=3, sent_at_utc=_CONSOLE_T0, queue=[fx.make_priority_score()]),
        NodeStatusEvent(seq=4, sent_at_utc=_CONSOLE_T0, node=_node()),
        OperatorActionAckEvent(
            seq=5, sent_at_utc=_CONSOLE_T0, action=_action(), accepted=True),
    ]


# --------------------------------------------------------------------------------
# contracts.py must stay a vocabulary, not a module. Enforced structurally, because
# "we agreed not to put logic there" does not survive 03:00.
# --------------------------------------------------------------------------------

def test_contracts_module_contains_no_logic_at_all():
    """
    ZERO LOGIC BY DESIGN, asserted rather than trusted.

    A helper added here is invisible to review because it looks harmless — and it
    binds every lane to one lane's policy. A single `def is_spoof(...)` in this file
    would put verdict policy in ARCH's file, where lane C cannot change it without
    breaking lanes A, B and D at import time.
    """
    src = (Path(__file__).resolve().parent.parent / "03_src" / "contracts.py").read_text()
    tree = ast.parse(src)
    offenders = [
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert not offenders, (
        f"contracts.py defines functions {offenders}. It is a vocabulary, not a "
        f"module. Logic belongs in the lane that owns the decision."
    )
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Lambda)], (
        "a lambda in contracts.py is logic wearing a smaller hat"
    )


def test_contracts_module_imports_no_web_framework():
    """
    NO FastAPI, starlette, uvicorn, flask or websockets in contracts.py.

    CAUSE AND EFFECT: pi_sensor.py — the edge node — imports contracts.py on a
    Raspberry Pi that has no web stack installed. One transport import here and the
    sensor stops booting with an ImportError pointing at a file nobody suspects,
    while the shore station keeps working. That is an hour lost at 03:00 to a
    one-line mistake.
    """
    src = (Path(__file__).resolve().parent.parent / "03_src" / "contracts.py").read_text()
    tree = ast.parse(src)
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    banned = {"fastapi", "starlette", "uvicorn", "flask", "websockets", "aiohttp",
              "requests", "httpx"}
    assert not (imported & banned), f"contracts.py imports a transport: {imported & banned}"
    assert imported <= {"typing", "pydantic"}, (
        f"contracts.py grew a dependency: {imported - {'typing', 'pydantic'}}. Every "
        f"lane and the edge node import this file; each new dependency is a new way "
        f"for all of them to fail to start at once."
    )


# --------------------------------------------------------------------------------
# SensorNode — provenance of the picture.
# --------------------------------------------------------------------------------

def test_sensor_node_round_trips_through_json():
    n = _node()
    assert SensorNode.model_validate_json(n.model_dump_json()) == n


def test_sensor_node_kind_vocabulary_is_closed():
    with pytest.raises(ValidationError):
        _node(kind="drone")          # not one of ours; F must not invent a kind


def test_a_node_never_heard_from_has_no_last_seen_rather_than_a_fake_one():
    """None means NOT AVAILABLE, project-wide. Substituting 'now' for a node that has
    never reported would render a dead sensor as healthy on the status strip — which
    is the single thing this field exists to prevent."""
    n = _node(last_seen=None, online=False)
    assert n.last_seen is None
    assert SensorNode.model_fields["last_seen"].default is None


def test_measured_fps_defaults_to_none_because_measured_numbers_only():
    """CLAUDE.md: never report a benchmark you did not run. A nominal 30 sitting in a
    default would be a number nobody measured, displayed to a judge as if someone
    had."""
    assert SensorNode.model_fields["measured_fps"].default is None
    with pytest.raises(ValidationError):
        _node(measured_fps=-1.0)


def test_online_and_last_seen_are_independent_fields():
    """Deriving one from the other needs a staleness threshold, and a threshold is
    policy — which belongs to lane E, not to contracts.py."""
    stale_but_flagged_online = _node(online=True,
                                     last_seen=_CONSOLE_T0 - timedelta(minutes=11))
    assert stale_but_flagged_online.online is True
    assert stale_but_flagged_online.last_seen < _CONSOLE_T0


# --------------------------------------------------------------------------------
# OperatorAction — decision support, never automated enforcement.
# --------------------------------------------------------------------------------

def test_operator_action_round_trips_through_json():
    a = _action()
    assert OperatorAction.model_validate_json(a.model_dump_json()) == a


def test_operator_action_vocabulary_is_closed():
    with pytest.raises(ValidationError):
        _action(action="INTERCEPT")   # the system does not intercept anything
    with pytest.raises(ValidationError):
        _action(action="dispatch")    # case is part of the vocabulary


def test_every_action_names_a_human_and_a_time():
    """An unattributed dispatch order is not a decision record. Both fields are
    required, so an anonymous action cannot be constructed at all."""
    assert OperatorAction.model_fields["operator_id"].is_required()
    assert OperatorAction.model_fields["action_time_utc"].is_required()
    with pytest.raises(ValidationError):
        OperatorAction(action="DISPATCH", contact_id="eo-0001",
                       action_time_utc=_CONSOLE_T0)      # no operator_id


def test_an_action_timestamp_must_be_timezone_aware():
    with pytest.raises(ValidationError):
        _action(action_time_utc=datetime(2026, 8, 25, 14, 2, 11))


def test_the_ack_confirms_recording_and_has_nowhere_to_report_an_outcome():
    """Decision support, never automated enforcement — asserted structurally, the same
    way the LLM/verdict split is. If an 'executed' or 'asset_tasked' field ever
    appears here, the tool has quietly become something the brief forbids."""
    for banned in ("executed", "asset_tasked", "dispatched", "outcome", "result"):
        assert banned not in OperatorActionAckEvent.model_fields, (
            f"OperatorActionAckEvent.{banned} would imply the system acted. It "
            f"records a human's decision and nothing else."
        )
    assert "accepted" in OperatorActionAckEvent.model_fields


# --------------------------------------------------------------------------------
# StreamEvent — the discriminated union.
# --------------------------------------------------------------------------------

@pytest.mark.parametrize("event", _one_of_each_event(),
                         ids=lambda e: e.event)
def test_every_stream_event_round_trips_through_the_discriminated_union(event):
    """Serialise, then parse back through the union exactly as the console does. This
    is the real round trip: `type(event).model_validate_json` would prove only that
    the model can read its own output, not that the tag routes it correctly."""
    restored = STREAM_EVENTS.validate_json(event.model_dump_json())
    assert restored == event
    assert type(restored) is type(event)


@pytest.mark.parametrize("event", _one_of_each_event(), ids=lambda e: e.event)
def test_every_stream_event_carries_a_monotonic_seq_and_an_iso_timestamp(event):
    """Ordering and recovery both rest on these two fields being present on EVERY
    variant, not on most of them. A single event type without a seq creates a gap the
    console cannot distinguish from a dropped message."""
    assert isinstance(event.seq, int) and event.seq >= 0
    assert event.sent_at_utc.tzinfo is not None


@pytest.mark.parametrize("event", _one_of_each_event(), ids=lambda e: e.event)
def test_stream_event_json_is_parseable_by_the_browser(event):
    """
    The wire format the console's JavaScript actually receives.

    Checked here: it is valid JSON; the top level is an object carrying `event` as a
    string and `seq` as a number; `sent_at_utc` is ISO-8601 WITH AN EXPLICIT ZONE; and
    the payload contains no NaN or Infinity, which Python's json module will happily
    emit and JSON.parse rejects outright.

    HONEST LIMIT: no JavaScript engine runs in this test suite, so this proves the
    bytes are well-formed and zone-explicit, not that a specific browser rendered
    them. The zone check is the one that matters — a naive timestamp reaches the
    browser without an offset and is read as LOCAL time, shifting every event on the
    timeline by two hours in Hamburg in August, with nothing raising anywhere.
    """
    raw = event.model_dump_json()
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    assert isinstance(payload["event"], str)
    assert isinstance(payload["seq"], int)

    ts = payload["sent_at_utc"]
    assert isinstance(ts, str)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})",
                        ts), f"{ts!r} is not an ISO-8601 instant with an explicit zone"
    # datetime.fromisoformat only learned to accept a trailing 'Z' in Python 3.11;
    # normalising here keeps the test working on 3.10 as well as on the Mac.
    assert datetime.fromisoformat(ts.replace("Z", "+00:00")).tzinfo is not None

    assert "NaN" not in raw and "Infinity" not in raw, (
        "JSON.parse rejects NaN and Infinity outright — the console would fail to "
        "read the whole message, not just the offending number."
    )


def test_the_discriminator_rejects_an_unknown_event_type():
    """An unknown tag must fail at the boundary, where it is one line to find, rather
    than fall through to a default branch in the console's JavaScript and render
    nothing."""
    bad = json.dumps({"event": "vessel_boarded", "seq": 9,
                      "sent_at_utc": "2026-08-25T14:02:11Z"})
    with pytest.raises(ValidationError):
        STREAM_EVENTS.validate_json(bad)


def test_the_tag_is_authoritative_not_merely_a_hint():
    """
    WHY DISCRIMINATED AND NOT A PLAIN UNION: pydantic tries a plain union member by
    member and takes the first that validates. A node_status body wearing a
    queue_update tag would then be accepted as the wrong type — no error raised, the
    wrong panel updated. With the discriminator the tag decides, and a mismatched
    body fails.
    """
    mislabelled = json.dumps({"event": "queue_update", "seq": 9,
                              "sent_at_utc": "2026-08-25T14:02:11Z",
                              "node": _node().model_dump(mode="json")})
    with pytest.raises(ValidationError):
        STREAM_EVENTS.validate_json(mislabelled)


def test_the_claimed_observed_wall_survives_the_websocket():
    """
    Criterion 3 on the wire. `contact_update` carries the claim and the observation as
    two separate optional fields; it has no merged 'vessel' payload. A UI-shaped merge
    is the easiest thing in the world to write here and would delete the comparison
    that criterion 3 consists of — without raising anything.
    """
    for banned in ("vessel", "contact", "merged", "target"):
        assert banned not in ContactUpdateEvent.model_fields
    assert "ais_track" in ContactUpdateEvent.model_fields
    assert "eo_contact" in ContactUpdateEvent.model_fields

    # Which side is None is still the finding, all the way to the browser.
    dark = ContactUpdateEvent(seq=1, sent_at_utc=_CONSOLE_T0,
                              eo_contact=fx.make_eo_contact(), ais_track=None)
    assert dark.eo_contact is not None and dark.ais_track is None

    claim_only = ContactUpdateEvent(seq=2, sent_at_utc=_CONSOLE_T0,
                                    eo_contact=None, ais_track=fx.make_ais_track())
    assert claim_only.eo_contact is None and claim_only.ais_track is not None

    # And an identity still cannot be smuggled onto the observed side in transit.
    with pytest.raises(ValidationError):
        ContactUpdateEvent(seq=3, sent_at_utc=_CONSOLE_T0, claimed_mmsi="999123456")


def test_a_verdict_update_carries_the_comparisons_that_produced_it():
    """A SPOOF label with no mismatches beside it is a bare accusation, which is the
    output criterion 4 exists to forbid."""
    ev = VerdictUpdateEvent(seq=7, sent_at_utc=_CONSOLE_T0,
                            verdict=fx.make_verdict(), mismatches=[fx.make_mismatch()])
    assert ev.verdict.mismatch_ids
    assert {m.mismatch_id for m in ev.mismatches} >= set(ev.verdict.mismatch_ids)


def test_a_queue_update_sends_the_whole_ranking_not_a_delta():
    """A ranking is a total order. Patching one entry in isolation leaves the console
    displaying an order the prioritiser never produced, and 'send the boat here' stops
    being defensible."""
    assert QueueUpdateEvent.model_fields["queue"].annotation == list[PriorityScore]
    ev = QueueUpdateEvent(seq=8, sent_at_utc=_CONSOLE_T0,
                          queue=[fx.make_priority_score(rank=1),
                                 fx.make_priority_score(seed=2, rank=2, score=0.44)])
    assert [p.rank for p in ev.queue] == [1, 2]


def test_stream_events_are_frozen_like_every_other_contract():
    ev = NodeStatusEvent(seq=4, sent_at_utc=_CONSOLE_T0, node=_node())
    with pytest.raises(ValidationError):
        ev.seq = 5


# --------------------------------------------------------------------------------
# ConsoleState — a refresh mid-demo must recover, not repaint an empty page.
# --------------------------------------------------------------------------------

def test_console_state_round_trips_through_json():
    """It crosses the wire whole on first connect. If it cannot survive serialisation
    the recovery path does not exist."""
    st = _console_state()
    assert ConsoleState.model_validate_json(st.model_dump_json()) == st


def test_console_state_carries_the_seq_the_client_resumes_from():
    """Without last_seq the client either double-applies events already baked into the
    snapshot or skips ones that are not — and both look like a UI bug rather than a
    protocol bug, which is how an hour disappears."""
    assert ConsoleState.model_fields["last_seq"].is_required()
    st = _console_state(last_seq=412)
    assert st.last_seq == 412
    with pytest.raises(ValidationError):
        _console_state(last_seq=-1)


def test_console_state_holds_everything_the_page_redraws_from():
    """A refresh at minute three of a four-minute pitch must not show an empty page.
    Nodes, adjudicated records, contacts not yet adjudicated, and the operator's own
    decisions all have to come back."""
    st = _console_state()
    assert st.nodes and st.records and st.unresolved_contacts and st.recent_actions
    rec = st.records[0]
    assert isinstance(rec, EvidenceRecord)
    assert rec.verdict.label and rec.priority is not None


def test_console_state_does_not_duplicate_the_queue_into_a_second_ordered_field():
    """EvidenceRecord already embeds the PriorityScore, and the rank is on it. A second
    ordered list here could drift out of agreement with the records — and then the
    ranking on screen and the ranking in the case files disagree, with no way to tell
    which one the operator acted on."""
    assert "queue" not in ConsoleState.model_fields
    st = _console_state()
    ranks = [r.priority.rank for r in st.records if r.priority is not None]
    assert ranks == sorted(ranks) or len(ranks) <= 1


def test_console_state_distinguishes_no_sensors_from_offline_sensors():
    """An empty node list and a list of dead nodes are opposite findings that look
    identical on a map."""
    none_configured = _console_state(nodes=[])
    assert none_configured.nodes == []
    all_dead = _console_state(nodes=[_node(online=False, last_seen=None,
                                           measured_fps=None)])
    assert all_dead.nodes[0].online is False


# --------------------------------------------------------------------------------
# PriorityScore — the per-factor breakdown already exists. Keep it singular.
# --------------------------------------------------------------------------------

def test_priority_score_has_exactly_one_per_factor_source_of_truth():
    """
    Criterion 2 needs the console to show WHY one contact outranks another, and
    `component_scores` + `component_weights` already provide that per factor.

    A second per-factor dict — `factors`, `breakdown`, `reasons` — would be a second
    source of truth, and the failure is silent: the console renders one while the
    prioritiser keeps computing from the other, so the screen explains a ranking the
    pipeline never produced. That is worse than showing nothing, because it is
    convincing.
    """
    assert "component_scores" in PriorityScore.model_fields
    assert "component_weights" in PriorityScore.model_fields
    for duplicate in ("factors", "breakdown", "reasons", "contributions"):
        assert duplicate not in PriorityScore.model_fields, (
            f"PriorityScore.{duplicate} duplicates the component_scores breakdown. "
            f"One source of truth for the weighting, or the explanation on screen "
            f"can disagree with the score beside it."
        )
