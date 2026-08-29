"""
test_ais_ingest.py — lane A's regression net.

Every test here corresponds to a SILENT failure: something that produces a wrong
verdict without raising anything. That is the selection criterion. A bug that
crashes gets found in thirty seconds at a hackathon; a bug that shifts a position or
invents a claim gets found by a judge.

The five data landmines are documented at the top of 03_src/ais_ingest.py. Each one
has a test below, named after it.
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from ais_ingest import (
    DMA_SHIP_TYPE_TO_CLASS,
    DmaCsvSource,
    IngestStats,
    add_report_time,
    clean_float,
    clean_string,
    drop_duplicate_receptions,
    filter_frame,
    iter_records,
    make_track_id,
    normalise_columns,
    normalise_mmsi,
    positive_or_none,
    row_to_track_fields,
    vessel_class_from_ais_code,
    vessel_class_from_dma_string,
)
from contracts import AisTrack

# --------------------------------------------------------------------------------
# Landmine 5 — dd/mm/yyyy vs pandas' mm/dd/yyyy default.
# --------------------------------------------------------------------------------

def test_landmine_5_day_first_timestamps_are_not_month_swapped():
    """
    03/08/2026 is 3 AUGUST. pandas' default inference reads it as 8 March and raises
    NOTHING. This is the single most dangerous line in the ingest path: every date
    with a day <= 12 is silently wrong, which is roughly the first twelve days of
    every month.
    """
    frame = normalise_columns(pd.DataFrame({"# Timestamp": ["03/08/2026 06:07:08"]}))
    parsed = add_report_time(frame)["report_time_utc"].iloc[0]
    assert (parsed.day, parsed.month) == (3, 8)
    assert parsed.tzinfo is not None, "contracts.py rejects naive datetimes"


def test_unparseable_timestamps_are_counted_not_raised():
    stats = IngestStats()
    frame = normalise_columns(pd.DataFrame({"# Timestamp": ["not a date"]}))
    out = add_report_time(frame, stats)
    assert out["report_time_utc"].isna().all()
    assert stats.rows_unparseable_timestamp == 1


# --------------------------------------------------------------------------------
# Landmine 1 — the same transmission heard by several basestations.
# --------------------------------------------------------------------------------

def _frame(rows):
    cols = ["Timestamp", "MMSI", "Latitude", "Longitude", "Type of mobile"]
    return pd.DataFrame(rows, columns=cols)


def test_landmine_1_duplicate_receptions_collapse_to_one_report():
    frame = _frame([
        ["25/08/2026 00:00:00", 999043000, 54.600000, 11.300000, "Class A"],
        ["25/08/2026 00:00:00", 999043000, 54.600000, 11.300000, "Class A"],
        ["25/08/2026 00:00:01", 999043000, 54.600100, 11.300100, "Class A"],
    ])
    stats = IngestStats()
    out = drop_duplicate_receptions(frame, set(), stats)
    assert len(out) == 2
    assert stats.rows_duplicate_reception == 1


def test_dedupe_state_carries_across_chunks():
    """
    A duplicate split across two pandas chunks must still be caught. Without shared
    state the dedupe silently degrades to per-chunk, which is invisible in the output
    and only shows up as an inflated message rate.
    """
    row = ["25/08/2026 00:00:00", 999043000, 54.6, 11.3, "Class A"]
    seen: set[int] = set()
    stats = IngestStats()
    first = drop_duplicate_receptions(_frame([row]), seen, stats)
    second = drop_duplicate_receptions(_frame([row]), seen, stats)
    assert len(first) == 1 and len(second) == 0
    assert stats.rows_duplicate_reception == 1


# --------------------------------------------------------------------------------
# Landmine 2 — aids to navigation are not vessels.
# --------------------------------------------------------------------------------

def test_landmine_2_aton_rows_are_excluded_and_counted():
    frame = add_report_time(_frame([
        ["25/08/2026 10:00:00", 999043000, 54.6, 11.3, "Class A"],
        ["25/08/2026 10:00:00", 992111111, 54.6, 11.3, "AtoN"],
    ]))
    stats = IngestStats()
    out = filter_frame(frame, stats=stats)
    assert len(out) == 1
    assert stats.rows_non_vessel == 1


def test_aton_can_be_kept_deliberately():
    frame = add_report_time(_frame([
        ["25/08/2026 10:00:00", 992111111, 54.6, 11.3, "AtoN"]]))
    assert len(filter_frame(frame, include_non_vessels=True)) == 1


# --------------------------------------------------------------------------------
# Landmine 3 — ITU "not available" sentinels.
# --------------------------------------------------------------------------------

@pytest.mark.parametrize("value,sentinel,name", [
    (102.3, 102.3, "sog"), (360.0, 360.0, "cog"), (511.0, 511.0, "heading")])
def test_landmine_3_sentinels_become_none_and_are_counted(value, sentinel, name):
    stats = IngestStats()
    assert clean_float(value, sentinel=sentinel, stats=stats, name=name) is None
    assert stats.sentinels_nulled[name] == 1


def test_a_real_value_equal_to_zero_survives():
    """
    contracts.py: None means "not available", never zero. A vessel genuinely stopped
    has SOG 0.0 and that is a CLAIM, not a missing field. Collapsing the two would
    make every moored vessel look like a transponder with nothing to say.
    """
    assert clean_float(0.0, sentinel=102.3) == 0.0


def test_zero_dimensions_and_zero_imo_become_none_on_the_live_path():
    """AIS message 5 encodes "no dimension" and "no IMO" AS zero, not as absent."""
    assert positive_or_none(0.0) is None
    assert positive_or_none(100.0) == 100.0


# --------------------------------------------------------------------------------
# Landmine 4 — "Unknown" is a string, not a null.
# --------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["Unknown", "Undefined", "", "  ", "unknown", None])
def test_landmine_4_absent_value_spellings_become_none(raw):
    assert clean_string(raw) is None


def test_a_real_identity_survives_cleaning():
    assert clean_string("  MALAIKA ") == "MALAIKA"
    assert clean_string("9279123") == "9279123"


# --------------------------------------------------------------------------------
# Identifiers.
# --------------------------------------------------------------------------------

def test_mmsi_is_a_zero_padded_string():
    """
    contracts.py: identifiers are STRINGS and leading zeros are significant. pandas
    reads MMSI as int64, which destroys the leading zero of a 8-digit-looking MMSI.
    """
    assert normalise_mmsi(219000001) == "219000001"
    assert normalise_mmsi(2190001) == "002190001"
    assert normalise_mmsi(0) is None


def test_track_id_is_stable_and_is_not_the_mmsi():
    """
    An MMSI cannot be the join key: it is spoofable and non-unique, so a forged
    identity would inherit the real vessel's track history. The id must also be
    stable across re-runs or an evidence record stops resolving after a restart.
    """
    a = make_track_id("slice_x", "219000001")
    assert a == make_track_id("slice_x", "219000001")
    assert "219000001" not in a
    assert a != make_track_id("slice_y", "219000001")


# --------------------------------------------------------------------------------
# The ship-type mapping policy — conservative by design.
# --------------------------------------------------------------------------------

def test_unambiguous_types_map_directly():
    assert vessel_class_from_dma_string("Cargo") == "cargo"
    assert vessel_class_from_dma_string("Tanker") == "tanker"
    assert vessel_class_from_dma_string("Towing") == "tug"      # AIS 31/32
    assert vessel_class_from_ais_code(70) == "cargo"
    assert vessel_class_from_ais_code(52) == "tug"              # inside the 50s block


def test_ambiguous_types_map_to_unknown_never_to_a_guess():
    """
    THE POLICY, AS A TEST. "unknown" means "a claim was made, and it is not
    comparable to a camera silhouette". If HSC mapped to "passenger", every
    high-speed craft the VLM read as something else would raise a SPOOF whose real
    origin is this table — a defamatory verdict manufactured in lane A and invisible
    to the report.
    """
    for raw in ("HSC", "Pilot", "SAR", "Law enforcement", "Dredging", "Other"):
        assert vessel_class_from_dma_string(raw) == "unknown"
    assert vessel_class_from_ais_code(45) == "unknown"          # HSC


def test_no_claim_at_all_is_none_not_unknown():
    """
    "Undefined" and "unknown" are DIFFERENT findings: one transponder said nothing,
    the other said something uninformative. Collapsing them loses the distinction
    that tells lane C whether a blank is suspicious.
    """
    assert vessel_class_from_dma_string("Undefined") is None
    assert vessel_class_from_ais_code(0) is None


def test_an_unseen_ship_type_is_counted_not_guessed():
    stats = IngestStats()
    assert vessel_class_from_dma_string("Hovercraft Of Theseus", stats) is None
    assert stats.unmapped_ship_types == {"Hovercraft Of Theseus": 1}


# --------------------------------------------------------------------------------
# The claimed/observed wall, and the full row -> AisTrack mapping.
# --------------------------------------------------------------------------------

DMA_ROW = {
    "Timestamp": "25/08/2026 10:15:00", "Type of mobile": "Class A",
    "MMSI": 219000001, "Latitude": 54.629105, "Longitude": 11.348278,
    "Navigational status": "Under way using engine", "ROT": 0.0, "SOG": 12.4,
    "COG": 105.6, "Heading": 129.0, "IMO": "9279123", "Callsign": "OZ1234",
    "Name": "MALAIKA", "Ship type": "Cargo", "Cargo type": "", "Width": 22.0,
    "Length": 98.0, "Type of position fixing device": "GPS", "Draught": 5.4,
    "Destination": "ROSTOCK", "ETA": "", "Data source type": "AIS",
    "A": 18.0, "B": 80.0, "C": 11.0, "D": 11.0,
}


def _fields():
    frame = add_report_time(pd.DataFrame([DMA_ROW]))
    return row_to_track_fields(next(iter_records(frame)), "test_source")


def test_row_maps_onto_a_valid_aistrack():
    track = AisTrack(**_fields())
    assert track.claimed_mmsi == "219000001"
    assert track.claimed_ship_type == "cargo"
    assert track.claimed_length_m == 98.0
    assert track.report_time_utc == datetime(2026, 8, 25, 10, 15, tzinfo=timezone.utc)


def test_length_and_size_a_b_stay_independent_claims():
    """
    AIS_CSV_SCHEMA.md: A+B is the hull length about the GPS antenna, so it
    cross-checks the declared Length from a SECOND field — a dimension spoof has to
    keep two fields consistent, not one. Deriving Length from A+B here would destroy
    that check silently, leaving a cross-check that always passes.
    """
    f = _fields()
    assert f["claimed_length_m"] == 98.0
    assert f["claimed_size_a_m"] + f["claimed_size_b_m"] == 98.0
    assert f["claimed_length_m"] is not None and f["claimed_size_a_m"] is not None


def test_every_emitted_field_is_a_claim():
    """
    The structural guardrail for criterion 3, mirroring the one ARCH put on Verdict.
    If an observed_* field ever appears on AisTrack, the claimed/observed wall has
    been breached and the spoofing detector stops being able to exist.
    """
    for name in _fields():
        assert not name.startswith("observed_"), name
    assert not any(f.startswith("observed_") for f in AisTrack.model_fields)


def test_missing_mmsi_yields_no_track_rather_than_a_blank_one():
    row = dict(DMA_ROW, MMSI=0)
    frame = add_report_time(pd.DataFrame([row]))
    assert row_to_track_fields(next(iter_records(frame)), "test_source") is None


# ================================================================================
# Plausibility check — claim against itself.
#
# Same selection criterion as everything above: each test is a way to produce a
# WRONG NUMBER rather than an exception. The two that matter most are the anchor
# rule (get it wrong and 96% of the output is GPS noise) and the wall test (get it
# wrong and lane C weights a verdict with an uncertainty that does not exist).
# ================================================================================

import importlib.util          # noqa: E402
from datetime import timedelta   # noqa: E402

import ais_ingest                # noqa: E402
from ais_ingest import (         # noqa: E402
    POSITION_IMPLAUSIBLE,
    PlausibilityChecker,
    PlausibilityMismatch,
    anonymise_mmsi,
)

#: Only the distance-based checks need pyproj. Applied per test rather than as a
#: module-level importorskip, which would skip the 24 pandas-only tests above too —
#: a green-looking run that silently tested nothing is worse than a red one.
requires_pyproj = pytest.mark.skipif(
    importlib.util.find_spec("pyproj") is None,
    reason="pyproj not installed; distance-based plausibility checks skipped")

PT0 = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)


def _track(seconds: float, lat: float, lon: float, *, mmsi: str = "219000001",
           sog: float | None = 6.0) -> AisTrack:
    return AisTrack(
        track_id=f"AIS-{mmsi[-4:]}", claimed_mmsi=mmsi,
        report_time_utc=PT0 + timedelta(seconds=seconds),
        claimed_lat_deg=lat, claimed_lon_deg=lon, claimed_sog_kn=sog)


def test_first_report_of_a_vessel_produces_nothing():
    """There is nothing to compare against. It must not be an error either."""
    checker = PlausibilityChecker()
    assert checker.check(_track(0, 54.60, 11.30)) == []
    assert checker.stats.pairs_skipped_first_report == 1


@requires_pyproj
def test_zero_interval_does_not_divide_by_zero():
    """
    956 consecutive pairs on the Fehmarn day share a timestamp exactly — two
    basestations decoding one transmission. dt = 0 makes implied speed infinite, so
    the naive implementation raises ZeroDivisionError partway through a day of AIS,
    or silently emits 956 phantom teleports.
    """
    checker = PlausibilityChecker()
    checker.check(_track(0, 54.600000, 11.300000))
    findings = checker.check(_track(0, 54.600010, 11.300010))   # ~1.4 m apart
    assert findings == [], "sub-metre disagreement is basestation rounding"
    assert checker.stats.zero_interval_pairs == 1


@requires_pyproj
def test_zero_interval_with_a_large_jump_is_reported():
    """
    21 same-second pairs on the Fehmarn day differ by more than 100 m. That is not
    rounding — a hull cannot be in two places at once. The leading explanation is two
    vessels sharing one MMSI, which is the identity-spoof case criterion 3 calls the
    discriminator.
    """
    checker = PlausibilityChecker()
    checker.check(_track(0, 54.6000, 11.3000))
    findings = checker.check(_track(0, 54.6050, 11.3000))       # ~556 m apart
    assert len(findings) == 1
    assert findings[0].kind == "zero_interval_jump"
    assert findings[0].dt_s == 0
    assert findings[0].implied_value == "instantaneous", "never a float here"


@requires_pyproj
def test_short_intervals_are_skipped_without_moving_the_anchor():
    """
    THE ANCHOR RULE, AND THE WHOLE POINT OF THIS CHECK.

    At 1 Hz reporting a consumer GPS's own scatter exceeds the vessel's movement:
    measured on the Fehmarn day, 564 of 705 naive "impossible speeds" are over
    dt <= 2 s with a median jump of 57 m — 111 knots, and also an ordinary GPS fix.

    But merely DISCARDING short intervals would mean a 1 Hz vessel is never checked
    at all. The anchor must therefore stay put so the NEXT report is compared against
    a base far enough back. This test is the difference between checking a 1 Hz
    vessel every ten seconds and never checking it.
    """
    checker = PlausibilityChecker(min_interval_s=10.0)
    checker.check(_track(0, 54.6000, 11.3000))
    for second in (1, 2, 3):                       # all inside the floor
        assert checker.check(_track(second, 54.6000 + second * 1e-5, 11.3000)) == []
    assert checker.stats.pairs_below_min_interval == 3
    # 12 s after the ANCHOR (not after the previous report), 3.3 km away.
    findings = checker.check(_track(12, 54.6300, 11.3000))
    assert len(findings) == 1, "the anchor must still be the t=0 report"


@requires_pyproj
def test_a_plausible_transit_produces_nothing():
    """12 knots over a minute. The check must not fire on ordinary navigation."""
    checker = PlausibilityChecker()
    checker.check(_track(0, 54.6000, 11.3000))
    assert checker.check(_track(60, 54.6033, 11.3000)) == []    # ~367 m, ~11.9 kn


@requires_pyproj
def test_impossible_speed_and_teleport_are_distinguished_by_jump_size():
    """
    Both are impossible; they are not equally interesting. A 900 m jump in 15 s is a
    bad fix. A 5 km jump is a track that moved somewhere else entirely, which is why
    it is `critical` and the other is `major`.
    """
    near = PlausibilityChecker()
    near.check(_track(0, 54.6000, 11.3000))
    a = near.check(_track(15, 54.6080, 11.3000))[0]             # ~890 m in 15 s
    assert a.kind == "impossible_speed" and a.severity == "major"

    far = PlausibilityChecker()
    far.check(_track(0, 54.6000, 11.3000))
    b = far.check(_track(60, 54.7000, 11.3000))[0]              # ~11 km in 60 s
    assert b.kind == "teleport" and b.severity == "critical"


@requires_pyproj
def test_a_finding_is_claimed_against_implied_never_against_observed():
    """
    THE STRUCTURAL GUARDRAIL, mirroring ARCH's test that Verdict has nowhere to put
    model-generated text.

    A plausibility finding compares a claim with what the claim IMPLIES. Nothing
    observed it. If an `observed_*` field or an `observation_confidence` ever appears
    here, a consumer will weight a verdict using an EO uncertainty that was never
    computed — silently, with no exception.
    """
    checker = PlausibilityChecker()
    checker.check(_track(0, 54.6000, 11.3000))
    finding = checker.check(_track(60, 54.7000, 11.3000))[0]

    assert finding.dimension == POSITION_IMPLAUSIBLE
    assert finding.comparison == "claimed_vs_implied"
    assert finding.association_id is None, "nothing was paired; there is no association"
    names = set(PlausibilityMismatch.__dataclass_fields__)
    assert not any(n.startswith("observed_") for n in names), names
    assert "observation_confidence" not in names
    assert finding.implied_field.startswith("implied_")


@requires_pyproj
def test_findings_carry_the_thresholds_that_produced_them():
    checker = PlausibilityChecker(max_speed_kn=50.0, min_interval_s=10.0)
    checker.check(_track(0, 54.6000, 11.3000))
    finding = checker.check(_track(60, 54.7000, 11.3000))[0]
    assert finding.detector["max_speed_kn"] == 50.0
    assert finding.detector["min_interval_s"] == 10.0
    assert finding.detector["coastline"] is None
    assert finding.ruleset_version == ais_ingest.PLAUSIBILITY_RULESET_VERSION


@requires_pyproj
def test_findings_are_frozen():
    checker = PlausibilityChecker()
    checker.check(_track(0, 54.6000, 11.3000))
    finding = checker.check(_track(60, 54.7000, 11.3000))[0]
    with pytest.raises(Exception):
        finding.severity = "minor"


@requires_pyproj
def test_vessels_may_be_interleaved():
    """
    Anchors are keyed by MMSI, so a globally time-ordered stream — which is what both
    DmaCsvSource and a live feed produce — needs no sorting. If this broke, every
    vessel would be compared against a different vessel's last position and the
    output would be nonsense rather than an error.
    """
    checker = PlausibilityChecker()
    checker.check(_track(0, 54.6000, 11.3000, mmsi="219000001"))
    checker.check(_track(1, 54.4000, 11.7000, mmsi="219000002"))
    assert checker.check(_track(60, 54.6033, 11.3000, mmsi="219000001")) == []
    assert checker.check(_track(61, 54.4033, 11.7000, mmsi="219000002")) == []


def test_land_check_is_unavailable_and_says_so_rather_than_passing_silently():
    """
    Criterion 4: a check that did not run must be visible. A land check that quietly
    returns "no crossings" because no coastline was loaded is worse than one that
    fails, because the evidence card then claims a clean result nobody computed.
    """
    checker = PlausibilityChecker(land_mask=None)
    assert checker.stats.land_check_available is False
    assert "no coastline" in checker.stats.land_check_unavailable_reason
    assert "land check" in checker.stats.summary()
    assert "UNAVAILABLE" in checker.stats.summary()


def test_anonymised_mmsi_is_recognisably_synthetic_and_hides_the_original():
    """
    These findings sit next to the words "impossible" and "teleport", which makes
    them the closest thing in the pipeline to an accusation — and the leading cause
    is a cheap transponder, not deception. MID 999 is unassigned to any country, so
    the replacement cannot be mistaken for a real vessel.
    """
    order = ["219000001", "244000002", "265000003"]
    out = anonymise_mmsi("244000002", order)
    assert out == "999000002"
    assert "244000002" not in out


def test_reading_ais_still_does_not_require_the_geo_stack():
    """
    ais_ingest must remain importable and usable with pandas alone — pyproj, shapely
    and geopandas are imported lazily, inside the functions that need them. A
    teammate with a broken geopandas install at 03:00 must still be able to read AIS.
    """
    import ast
    from pathlib import Path
    source = Path(ais_ingest.__file__).read_text()
    tree = ast.parse(source)
    top_level = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in getattr(node, "names", [])
    } | {
        node.module.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for banned in ("geopandas", "shapely", "pyproj", "movingpandas", "pyais"):
        assert banned not in top_level, f"{banned} must stay a lazy import"
