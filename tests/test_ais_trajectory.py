"""
test_ais_trajectory.py — lane A's trajectory-analysis regression net.

Same selection criterion as test_ais_ingest.py: every test here corresponds to a
failure that produces a WRONG NUMBER rather than an exception. A loiter count that is
quietly 40% low because of a library edge case is worse than a crash, because it goes
on a slide.

These tests need movingpandas, geopandas and shapely. They are the reason
`ais_trajectory.py` is a separate module from `ais_ingest.py` — reading AIS must not
require the geo stack.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

mpd = pytest.importorskip("movingpandas")

from ais_trajectory import (
    BehaviourEvent,
    STATIONARY_NAV_STATUSES,
    add_kinematics,
    build_trajectories,
    detect_gaps,
    detect_loiter,
    distance_to_area_edge_m,
    implied_kinematics_table,
    pairwise_distance_m,
    split_on_gaps,
)

T0 = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)
BBOX = (54.40, 54.80, 11.00, 11.80)   # lat_min, lat_max, lon_min, lon_max


def points(rows):
    """rows: (track_id, seconds_from_T0, lat, lon, sog_kn, nav_status)"""
    return pd.DataFrame([
        {"track_id": r[0], "claimed_mmsi": "219000001",
         "t": T0 + timedelta(seconds=r[1]), "lat": r[2], "lon": r[3],
         "claimed_sog_kn": r[4], "claimed_nav_status": r[5],
         "claimed_ship_type": "cargo"}
        for r in rows
    ])


# --------------------------------------------------------------------------------
# The library-defaults contract. These are asserted rather than trusted, because a
# movingpandas minor version that changed either default would silently rescale every
# speed and every bearing in the pipeline.
# --------------------------------------------------------------------------------

def test_add_speed_default_is_metres_per_second_on_wgs84():
    """
    0.001 degrees of latitude is ~111.2 m. Over 60 s that is ~1.85 m/s. If
    movingpandas ever returned km/h here the value would be ~6.7 and every
    implied-vs-claimed speed comparison would fire on every vessel.
    """
    collection = add_kinematics(build_trajectories(points([
        ("A", 0, 54.600, 11.30, 0.1, "Under way using engine"),
        ("A", 60, 54.601, 11.30, 0.1, "Under way using engine"),
    ])))
    speed = implied_kinematics_table(collection)["implied_speed_ms"].iloc[-1]
    assert 1.7 < speed < 2.0, f"expected ~1.85 m/s, got {speed}"


def test_add_direction_default_is_degrees_true_clockwise_from_north():
    """Due east must be ~90, not ~1.57 radians and not counter-clockwise."""
    collection = add_kinematics(build_trajectories(points([
        ("A", 0, 54.600, 11.300, 5.0, "Under way using engine"),
        ("A", 60, 54.600, 11.310, 5.0, "Under way using engine"),
    ])))
    course = implied_kinematics_table(collection)["implied_course_deg_true"].iloc[-1]
    assert 88.0 < course < 92.0, f"expected ~90 deg true, got {course}"


def test_stop_detector_max_diameter_is_metres_not_degrees():
    """
    A vessel drifting ~220 m over an hour IS a stop at a 500 m diameter and is NOT
    one at 100 m. If max_diameter were interpreted as degrees, a 500 "unit" box would
    be tens of thousands of kilometres wide and every vessel on earth would be a stop.
    """
    rows = [("A", i * 300, 54.600 + i * 0.0003, 11.30, 0.1, "Under way using engine")
            for i in range(13)]           # 13 reports over 60 min, ~400 m total drift
    frame = points(rows)
    collection = add_kinematics(build_trajectories(frame))
    wide = detect_loiter(collection, frame, max_diameter_m=1000.0,
                         min_duration=timedelta(minutes=30))
    narrow = detect_loiter(collection, frame, max_diameter_m=50.0,
                           min_duration=timedelta(minutes=30))
    assert len(wide) == 1, "a 400 m drift over an hour is a stop within 1000 m"
    assert narrow == [], "…and is not a stop within 50 m"


# --------------------------------------------------------------------------------
# The two library traps that change counts silently.
# --------------------------------------------------------------------------------

def test_single_report_vessels_are_dropped_before_iteration():
    """
    THE CRASH THAT WOULD HAVE HAPPENED ON REAL DATA. TrajectoryCollection.__iter__
    raises ValueError for any trajectory with fewer than two points, and the Fehmarn
    slice contains vessels with exactly one report (INVENTORY.md §4.3 measures the
    minimum at 1; 6 such vessels exist). Without the min_points floor the analysis
    dies partway through, blaming movingpandas rather than the data.
    """
    frame = points([
        ("A", 0, 54.60, 11.30, 5.0, "Under way using engine"),
        ("A", 60, 54.61, 11.30, 5.0, "Under way using engine"),
        ("LONELY", 0, 54.70, 11.40, 5.0, "Under way using engine"),
    ])
    collection = build_trajectories(frame)
    assert len(collection) == 1
    assert [t.id for t in collection.trajectories] == ["A"]
    list(collection)          # must not raise


def test_gap_count_is_not_taken_from_splitter_segments():
    """
    THE UNDER-COUNT. ObservationGapSplitter drops any segment with fewer than two
    points (`if len(df) > 1`). A track with: 2 reports, a 20-min gap, ONE lone
    report, another 20-min gap, 2 reports — has THREE segments, one of which is a
    single point and is discarded. Counting segments-minus-one therefore reports 1
    gap where there are 2.

    The error is worst exactly where reporting is sparsest, which is the case
    criterion 4 exists to be honest about. detect_gaps reads the timedelta column
    instead and loses nothing.
    """
    frame = points([
        ("A", 0, 54.600, 11.30, 5.0, "Under way using engine"),
        ("A", 60, 54.601, 11.30, 5.0, "Under way using engine"),
        ("A", 1260, 54.610, 11.30, 5.0, "Under way using engine"),   # lone report
        ("A", 2460, 54.620, 11.30, 5.0, "Under way using engine"),
        ("A", 2520, 54.621, 11.30, 5.0, "Under way using engine"),
    ])
    collection = add_kinematics(build_trajectories(frame))
    events = detect_gaps(collection, frame, min_gap=timedelta(minutes=10), bbox=BBOX)
    assert len(events) == 2, "both silences must be reported"

    segments = split_on_gaps(collection, min_gap=timedelta(minutes=10))
    assert len(segments) == 2, "the lone-point segment is dropped by the library"
    # The demonstration: segments-minus-one would have said 1.
    assert len(segments) - 1 < len(events)


# --------------------------------------------------------------------------------
# Alternative explanations — the criterion 4 half.
# --------------------------------------------------------------------------------

def test_a_gap_at_the_area_edge_is_flagged_as_a_likely_exit():
    """
    A vessel that sails out of the bounding box stops appearing while transmitting
    perfectly. Scoring that as evasion sends a patrol boat to a ship that did
    nothing — the exact failure criterion 2 exists to prevent.
    """
    frame = points([
        ("A", 0, 54.60, 11.79, 12.0, "Under way using engine"),   # ~700 m from edge
        ("A", 60, 54.60, 11.795, 12.0, "Under way using engine"),
        ("A", 3660, 54.60, 11.60, 12.0, "Under way using engine"),
    ])
    collection = add_kinematics(build_trajectories(frame))
    event = detect_gaps(collection, frame, min_gap=timedelta(minutes=10), bbox=BBOX)[0]
    assert event.explained_by_area_exit is True
    assert event.is_explained is True


def test_a_gap_in_open_water_is_not_explained_away():
    frame = points([
        ("A", 0, 54.60, 11.40, 12.0, "Under way using engine"),   # middle of the box
        ("A", 60, 54.601, 11.40, 12.0, "Under way using engine"),
        ("A", 3660, 54.65, 11.45, 12.0, "Under way using engine"),
    ])
    collection = add_kinematics(build_trajectories(frame))
    event = detect_gaps(collection, frame, min_gap=timedelta(minutes=10), bbox=BBOX)[0]
    assert event.explained_by_area_exit is False
    assert event.is_explained is False, "still a candidate, still not a verdict"


def test_a_gap_carries_the_speed_it_implies():
    """
    Distance covered divided by silence duration. Neither a claim nor an observation —
    the minimum speed the vessel must have held to be in both places. Above any
    plausible hull speed, it is a kinematic finding with no camera involved.
    """
    frame = points([
        ("A", 0, 54.50, 11.40, 12.0, "Under way using engine"),
        ("A", 60, 54.501, 11.40, 12.0, "Under way using engine"),
        ("A", 1260, 54.70, 11.40, 12.0, "Under way using engine"),   # ~22 km in 20 min
    ])
    collection = add_kinematics(build_trajectories(frame))
    event = detect_gaps(collection, frame, min_gap=timedelta(minutes=10), bbox=BBOX)[0]
    assert event.gap_distance_m > 20_000
    assert event.implied_gap_speed_ms > 15, "≈33 kn — not a merchant vessel"


def test_a_loiter_event_carries_the_nav_status_that_might_explain_it():
    """
    The Fehmarn Belt in August 2026 is a construction site. A bare stop count is
    dominated by moored tugs and port tenders, and a ranked list full of harbour
    furniture is worthless. Lane D needs the declared status to discount them.
    """
    rows = [("A", i * 300, 54.600, 11.30, 0.0, "Moored") for i in range(13)]
    frame = points(rows)
    collection = add_kinematics(build_trajectories(frame))
    event = detect_loiter(collection, frame, min_duration=timedelta(minutes=30))[0]
    assert event.claimed_nav_statuses[0] == "Moored"
    assert set(event.claimed_nav_statuses) & STATIONARY_NAV_STATUSES


# --------------------------------------------------------------------------------
# Geometry helpers and the event type.
# --------------------------------------------------------------------------------

def test_distance_to_edge_is_in_metres_via_a_metric_crs():
    """
    Degrees are not distances: one degree of longitude is ~64 km at 54.6 N and one of
    latitude is ~111 km, so a margin expressed in degrees means two different
    distances depending on which edge is nearest.
    """
    centre = distance_to_area_edge_m([54.60], [11.40], BBOX).iloc[0]
    edge = distance_to_area_edge_m([54.60], [11.799], BBOX).iloc[0]
    assert 20_000 < centre < 30_000
    assert edge < 200


def test_pairwise_distance_pairs_positionally_not_by_index():
    """
    geopandas' default `align=True` joins two GeoSeries on their INDEX. A mismatched
    index yields NaN distances rather than an error, and every gap would silently
    report no movement.
    """
    d = pairwise_distance_m([54.60, 54.70], [11.30, 11.30],
                            [54.601, 54.701], [11.30, 11.30])
    assert d.notna().all()
    assert all(100 < x < 125 for x in d), f"0.001 deg lat is ~111 m, got {list(d)}"


def test_behaviour_event_is_frozen():
    """Evidence that can be edited after the fact is not evidence."""
    frame = points([("A", i * 300, 54.600, 11.30, 0.0, "Moored") for i in range(13)])
    collection = add_kinematics(build_trajectories(frame))
    event = detect_loiter(collection, frame, min_duration=timedelta(minutes=30))[0]
    with pytest.raises(Exception):
        event.duration_s = 1.0


def test_event_ids_are_stable_across_runs():
    """An evidence record written at 02:00 must still resolve after a re-run."""
    frame = points([("A", i * 300, 54.600, 11.30, 0.0, "Moored") for i in range(13)])
    first = detect_loiter(add_kinematics(build_trajectories(frame)), frame,
                          min_duration=timedelta(minutes=30))[0]
    second = detect_loiter(add_kinematics(build_trajectories(frame)), frame,
                           min_duration=timedelta(minutes=30))[0]
    assert first.event_id == second.event_id


def test_events_carry_the_thresholds_they_were_detected_under():
    """A count without its threshold is not a measurement — same reasoning as
    Verdict.ruleset_version."""
    frame = points([("A", i * 300, 54.600, 11.30, 0.0, "Moored") for i in range(13)])
    event = detect_loiter(add_kinematics(build_trajectories(frame)), frame,
                          max_diameter_m=500.0, min_duration=timedelta(minutes=30))[0]
    assert event.detector["max_diameter_m"] == 500.0
    assert event.detector["min_duration_s"] == 1800.0
    assert "movingpandas" in event.detector["library"]


def test_from_frames_track_ids_match_ais_ingest():
    """
    track_id is derived from (source_id, MMSI). If this module derived it differently
    the trajectory events would never join to the AisTrack objects lane C holds — with
    no error, just an empty association.
    """
    from ais_ingest import make_track_id
    from ais_trajectory import from_frames
    frame = pd.DataFrame({
        "MMSI": [219000001, 219000001],
        "report_time_utc": pd.to_datetime(["2026-08-25T10:00:00Z", "2026-08-25T10:01:00Z"]),
        "Latitude": [54.60, 54.61], "Longitude": [11.30, 11.30],
        "SOG": [5.0, 5.0], "Navigational status": ["Under way using engine"] * 2,
        "Ship type": ["Cargo"] * 2,
    })
    out = from_frames([frame], "slice_x")
    assert out["track_id"].iloc[0] == make_track_id("slice_x", "219000001")
