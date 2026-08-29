"""
tests/fixtures.py — synthetic AisTrack and EoContact generators.

WHY THIS FILE EXISTS
Lane C (Fusion + Verdict) is on the hook for criterion 3, the discriminator. If C
waits for lane A to deliver parsed AIS and lane B to deliver real detections, C
starts hours late and the hardest part of the build gets the least time. These
generators are contract-shaped fake data so C can write association.py,
consistency.py and verdict.py from minute one, and lane D can render an evidence
card before any verdict logic exists at all.

If lane C is ever idle waiting for data, that is an ARCH bug in this file, not an
A or B bug.

DETERMINISTIC ON PURPOSE
Every generator takes a seed and defaults to one. A test that fails intermittently
at 03:00 is worse than no test. Same seed, same vessels, every run, on every
machine.

SYNTHETIC IDENTITIES ONLY
Every MMSI here is in the 999xxxxxx range, which is not issued to real vessels, and
every name starts with "SYNTH-". constraints.md item 2 is non-negotiable: a demo
that labels a real named tanker a saboteur is defamatory. Nothing in this file can
collide with a real ship, so nothing built on it can accidentally accuse one.

Coordinates sit in the Fehmarn Belt box (54.40-54.80 N, 11.00-11.80 E) because that
is the chosen T1 demo area — so fixture output plots on the same map as real data.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import sys
from pathlib import Path

# Lane note: 03_src cannot be imported as a package because its name starts with a
# digit. conftest.py puts it on sys.path so `import contracts` works; this block
# repeats that so fixtures.py is also usable from a plain REPL or a scratch script.
_SRC = Path(__file__).resolve().parent.parent / "03_src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from contracts import (  # noqa: E402
    AisTrack,
    Association,
    EoContact,
    EvidenceRecord,
    Mismatch,
    PriorityScore,
    Verdict,
)

# Fehmarn Belt — the T1 demo area.
LAT_MIN, LAT_MAX = 54.40, 54.80
LON_MIN, LON_MAX = 11.00, 11.80

# A fixed instant so every fixture in a run shares one clock. Timezone-aware, because
# the contracts reject naive datetimes at the boundary.
T0 = datetime(2026, 8, 25, 14, 2, 11, tzinfo=timezone.utc)

_SHIP_CLASSES = ("cargo", "tanker", "fishing", "passenger", "tug", "small_craft")


def _synthetic_mmsi(rng: random.Random) -> str:
    """999-prefixed MMSIs are not issued to real vessels. Returned as a string
    because leading zeros are significant and an MMSI is an identifier, not a
    quantity."""
    return f"999{rng.randint(100000, 999999)}"


def make_ais_track(seed: int = 0, **overrides) -> AisTrack:
    """One CLAIMED track. Pass any field as a keyword to override it."""
    rng = random.Random(seed)
    mmsi = _synthetic_mmsi(rng)
    ship_type = rng.choice(_SHIP_CLASSES)
    length = round(rng.uniform(25.0, 300.0), 1)

    base = dict(
        track_id=f"ais-{seed:04d}",
        claimed_mmsi=mmsi,
        report_time_utc=T0 + timedelta(seconds=rng.randint(-30, 30)),
        claimed_lat_deg=round(rng.uniform(LAT_MIN, LAT_MAX), 6),
        claimed_lon_deg=round(rng.uniform(LON_MIN, LON_MAX), 6),
        claimed_sog_kn=round(rng.uniform(0.0, 18.0), 1),
        claimed_cog_deg_true=round(rng.uniform(0.0, 359.9), 1),
        claimed_heading_deg_true=round(rng.uniform(0.0, 359.9), 1),
        claimed_ship_type=ship_type,
        claimed_length_m=length,
        claimed_width_m=round(length / rng.uniform(5.0, 8.0), 1),
        claimed_name=f"SYNTH-{mmsi[-4:]}",
        claimed_imo=f"9{rng.randint(100000, 999999)}",
        claimed_callsign=f"SY{rng.randint(1000, 9999)}",
        claimed_nav_status="Under way using engine",
        mobile_class="Class A",
    )
    base.update(overrides)
    return AisTrack(**base)


def make_eo_contact(seed: int = 0, **overrides) -> EoContact:
    """One OBSERVED contact. No identity fields exist on this object by design."""
    rng = random.Random(seed + 10_000)
    observed_length = round(rng.uniform(25.0, 300.0), 1)
    rng_m = round(rng.uniform(800.0, 6000.0), 1)

    base = dict(
        contact_id=f"eo-{seed:04d}",
        frame_time_utc=T0,
        frame_ref=f"04_demo/frames/frame_{seed:05d}.jpg",
        bbox_px=(320, 210, 760, 340),
        observed_bearing_deg_true=round(rng.uniform(0.0, 359.9), 1),
        bearing_uncertainty_deg=round(rng.uniform(0.3, 2.5), 2),
        detection_confidence=round(rng.uniform(0.45, 0.97), 2),
        observed_range_m=rng_m,
        # Range error grows with range; ~12% is an honest placeholder for a
        # monocular estimate and keeps significance calculations meaningful.
        range_uncertainty_m=round(rng_m * 0.12, 1),
        observed_class=rng.choice(_SHIP_CLASSES),
        observed_class_confidence=round(rng.uniform(0.55, 0.96), 2),
        observed_length_m=observed_length,
        observed_length_uncertainty_m=round(observed_length * 0.18, 1),
        observed_heading_deg_true=round(rng.uniform(0.0, 359.9), 1),
        observed_speed_ms=round(rng.uniform(0.0, 9.0), 2),
        track_length_frames=rng.randint(3, 40),
        camera_pose_ref="pose-fehmarn-shore-001",
    )
    base.update(overrides)
    return EoContact(**base)


def make_ais_population(n: int = 25, seed: int = 0) -> list[AisTrack]:
    """A crowd of claims. Lane C needs a populated gate to test that association
    picks the right candidate and flags ambiguity when two score alike."""
    return [make_ais_track(seed=seed * 1000 + i) for i in range(n)]


def make_eo_population(n: int = 8, seed: int = 0) -> list[EoContact]:
    """Fewer observations than claims — one camera sees a slice of the traffic."""
    return [make_eo_contact(seed=seed * 1000 + i) for i in range(n)]


# --------------------------------------------------------------------------------
# Scenarios — the three cases from 01_research/ais_spoofing_methods.md, in ascending
# difficulty. These are what lane C should develop against.
# --------------------------------------------------------------------------------

def scenario_match(seed: int = 1) -> tuple[AisTrack, EoContact]:
    """
    The boring, essential case. Claim and observation agree: same class, similar
    length, similar heading. If the pipeline cannot produce MATCH here, every SPOOF
    it produces elsewhere is worthless — a detector that flags everything has
    detected nothing.
    """
    track = make_ais_track(
        seed=seed,
        claimed_ship_type="cargo",
        claimed_length_m=180.0,
        claimed_width_m=27.0,
        claimed_heading_deg_true=93.0,
        claimed_sog_kn=11.0,
    )
    contact = make_eo_contact(
        seed=seed,
        observed_class="cargo",
        observed_length_m=172.0,          # within the 18% length uncertainty
        observed_length_uncertainty_m=31.0,
        observed_heading_deg_true=95.5,   # 2.5 deg apart, well inside tolerance
        observed_speed_ms=5.6,            # ~10.9 kn
        observed_class_confidence=0.91,
    )
    return track, contact


def scenario_dark(seed: int = 2) -> EoContact:
    """
    Case 1: transponder off. An observation with NO claim to pair with. Criterion 1,
    and the case most teams will build. Deliberately returns a contact alone — there
    is no AisTrack, and that absence is the finding.
    """
    return make_eo_contact(
        seed=seed,
        observed_class="small_craft",
        observed_length_m=34.0,
        observed_length_uncertainty_m=6.1,
        observed_class_confidence=0.78,
        track_length_frames=22,
    )


def scenario_identity_spoof(seed: int = 3) -> tuple[AisTrack, EoContact]:
    """
    Case 2: false identity. THIS IS CRITERION 3 AND WHERE THE MARKS ARE.

    The claim says fishing vessel, 40 m. The camera sees a tanker silhouette at
    roughly 250 m. Both cannot be true. The pipeline must catch the mismatch on two
    independent dimensions — class AND length — because a single-dimension flag is
    much easier to argue away, and criterion 4 says the output has to survive being
    argued with.

    Note the observation confidence is high (0.88) and the length uncertainty is
    tight relative to the discrepancy. That is on purpose: this fixture should
    produce a CONFIDENT spoof verdict. Lane C should also build a low-confidence
    variant by overriding those two fields, and check the verdict flips to UNKNOWN
    with defer_to_human=True. If it does not, the defer threshold is not wired up.
    """
    track = make_ais_track(
        seed=seed,
        claimed_ship_type="fishing",
        claimed_length_m=40.0,
        claimed_width_m=8.0,
        claimed_sog_kn=12.5,              # fast for a 40 m fishing vessel
        claimed_nav_status="Engaged in fishing",
        mobile_class="Class A",
    )
    contact = make_eo_contact(
        seed=seed,
        observed_class="tanker",
        observed_class_confidence=0.88,
        observed_length_m=248.0,
        observed_length_uncertainty_m=31.0,
        observed_range_m=3900.0,
        range_uncertainty_m=470.0,
        track_length_frames=31,
    )
    return track, contact


def scenario_ambiguous_association(seed: int = 4) -> tuple[EoContact, list[AisTrack]]:
    """
    Two claims sitting close enough that the pairing is genuinely uncertain.

    This is the case that must NOT produce a confident verdict. A mismatch computed
    off the wrong pairing is not weak evidence, it is wrong evidence — and pointing
    a boarding team at the wrong hull is the failure mode this whole tool exists to
    avoid. Expected outcome: assoc_ambiguous=True, and a verdict of UNKNOWN with
    "ambiguous_association" in defer_reasons.
    """
    contact = make_eo_contact(seed=seed, observed_class="cargo", observed_length_m=150.0)
    near = make_ais_track(seed=seed, claimed_ship_type="cargo", claimed_length_m=148.0,
                          claimed_lat_deg=54.601, claimed_lon_deg=11.402)
    rival = make_ais_track(seed=seed + 1, claimed_ship_type="cargo",
                           claimed_length_m=155.0,
                           claimed_lat_deg=54.6015, claimed_lon_deg=11.4025)
    return contact, [near, rival]


# --------------------------------------------------------------------------------
# Downstream fixtures — so lane D can build the evidence card and the prioritiser
# before lane C has written a line of verdict logic.
# --------------------------------------------------------------------------------

def make_association(seed: int = 1, **overrides) -> Association:
    base = dict(
        association_id=f"assoc-{seed:04d}",
        assoc_time_utc=T0,
        contact_id=f"eo-{seed:04d}",
        track_id=f"ais-{seed:04d}",
        assoc_score=0.87,
        assoc_ambiguous=False,
        candidate_count=3,
        runner_up_score=0.41,
        time_delta_s=-7.0,
        spatial_gap_m=180.0,
    )
    base.update(overrides)
    return Association(**base)


def make_mismatch(seed: int = 1, **overrides) -> Mismatch:
    """Defaults to the class mismatch from scenario_identity_spoof — the criterion 3
    exemplar, and the one the pitch will quote."""
    base = dict(
        mismatch_id=f"mm-{seed:04d}",
        association_id=f"assoc-{seed:04d}",
        dimension="class",
        claimed_value="fishing",
        claimed_field="claimed_ship_type",
        observed_value="tanker",
        observed_field="observed_class",
        unit="class_label",
        delta=None,              # categorical: no numeric delta exists
        tolerance=None,
        significance=4.2,
        explained_by_staleness=False,
        severity="critical",
        observation_confidence=0.88,
    )
    base.update(overrides)
    return Mismatch(**base)


def make_verdict(seed: int = 1, **overrides) -> Verdict:
    base = dict(
        verdict_id=f"vd-{seed:04d}",
        association_id=f"assoc-{seed:04d}",
        label="SPOOF",
        confidence=0.79,
        mismatch_ids=[f"mm-{seed:04d}"],
        decided_at_utc=T0,
        spoof_subtype="identity",
        defer_to_human=True,
        defer_reasons=["no_range_estimate"],
        ruleset_version="0.1.0-fixture",
    )
    base.update(overrides)
    return Verdict(**base)


def make_priority_score(seed: int = 1, **overrides) -> PriorityScore:
    base = dict(
        verdict_id=f"vd-{seed:04d}",
        score=0.81,
        rank=1,
        component_scores={
            "verdict_severity": 0.90,
            "infrastructure_proximity": 0.85,
            "behaviour_anomaly": 0.60,
            "confidence": 0.79,
            "recency": 0.95,
        },
        component_weights={
            "verdict_severity": 0.25,
            "infrastructure_proximity": 0.35,
            "behaviour_anomaly": 0.15,
            "confidence": 0.15,
            "recency": 0.10,
        },
        nearest_infrastructure_m=2100.0,
        nearest_infrastructure_id="SYNTH-CABLE-SEG-07",
        nearest_asset_km=18.4,
        time_to_intercept_min=47.0,
        scarce_asset_recommended=True,
    )
    base.update(overrides)
    return PriorityScore(**base)


def make_evidence_record(seed: int = 1, **overrides) -> EvidenceRecord:
    """A complete, self-contained case file. Lane D can render this today."""
    track, contact = scenario_identity_spoof(seed=seed)
    base = dict(
        record_id=f"ev-{seed:04d}",
        created_at_utc=T0,
        verdict=make_verdict(seed=seed),
        mismatches=[make_mismatch(seed=seed)],
        ais_track=track,
        eo_contact=contact,
        priority=make_priority_score(seed=seed),
        identity_claimed={
            "mmsi": track.claimed_mmsi,
            "imo": track.claimed_imo,
            "name": track.claimed_name,
            "callsign": track.claimed_callsign,
        },
        bearing_deg_true=contact.observed_bearing_deg_true,
        range_nm=round((contact.observed_range_m or 0.0) / 1852.0, 2),
        frame_refs=[contact.frame_ref],
        rationale_text=None,     # filled by the LLM at report time, never here
        rationale_model=None,
        limitations=[
            "Monocular range estimate; length comparison inherits ~12% range error.",
            "Vessel class from a single-frame VLM description, confidence 0.88.",
        ],
        pipeline_version="0.1.0-fixture",
    )
    base.update(overrides)
    return EvidenceRecord(**base)
