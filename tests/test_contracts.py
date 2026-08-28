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

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import fixtures as fx
from contracts import (
    AisTrack,
    Association,
    EoContact,
    EvidenceRecord,
    Mismatch,
    PriorityScore,
    Verdict,
)

ALL_MODELS = [
    AisTrack, EoContact, Association, Mismatch, Verdict, PriorityScore, EvidenceRecord
]


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
