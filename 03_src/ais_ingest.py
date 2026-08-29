"""
ais_ingest.py — the CLAIMED half of the pipeline. Lane A owns this file.

WHY THIS FILE EXISTS
Criterion 1 needs "AIS in". Criterion 3 needs the AIS side of claimed-vs-observed.
Both come from here, and nowhere else. Every object this module emits is an
`AisTrack` from contracts.py, which carries `claimed_*` fields only. Nothing this
module produces is a fact about the world; it is a record of what a transponder
asserted. That distinction is the whole of criterion 3, so it is enforced by the
type, not by discipline.

ONE INTERFACE, TWO SOURCES
    AisSource            abstract: .tracks() -> Iterator[AisTrack], plus .stats
      DmaCsvSource       recorded bulk CSV from the Danish Maritime Authority (T1)
      NmeaLiveSource     live NMEA 0183 / AIVDM over TCP, decoded by pyais (T2)

The demo runner does not care which one it holds. T2 is a source swap, not a
pipeline change — that is the point of the abstraction, and it is the only reason
this abstraction exists.

LIBRARY-FIRST (03_src/LIBRARIES.md)
  * pandas decodes the bulk CSV. No hand-rolled chunking or CSV parsing.
  * pyais decodes NMEA/AIVDM. There is NO sentence parser, no six-bit ASCII
    unpacking and no message-type dispatch in this file, and there must never be.
  * pyais.AISTracker performs the static/dynamic merge on the live path. A type 1
    position report carries no ship name, type or dimensions; a type 5 carries no
    position. Merging them by MMSI over time is exactly what AISTracker does, so we
    do not re-implement it.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
No association, no verdicts, no anomaly scoring, no geodesy. It reads a source and
emits claims. If you want to know whether a claim is true, that is lanes B and C.

--------------------------------------------------------------------------------
FIVE PROPERTIES OF THE REAL DATA THAT CHANGE THE OUTPUT
Measured on 02_data/slices/aisdk-2026-08-25_fehmarn_belt.csv, 1,198,373 rows.
Each one is a silent-corruption risk: none of them raises, all of them change a
verdict. This is the inference chain for why the code below looks like it does.

1. DUPLICATE RECEPTIONS — 63.5% of rows.
   OBSERVED: 1,198,373 raw rows collapse to 436,221 distinct (Timestamp, MMSI)
   pairs. Several DMA basestations hear the same transmission and DMA writes one
   row per reception.
   WHY IT MATTERS: unhandled, every vessel appears to report ~2.75x more often than
   it does. A "this vessel has gone quiet" test calibrated on inflated rates fires
   late or not at all, and lane C's association sees three identical candidates for
   one hull, which reads as ambiguity where there is none.
   WHAT WOULD CHANGE THE ANSWER: a source with a single receiver. Then dedupe is a
   no-op and the counter in IngestStats reports zero, which is how you would know.

2. AIDS TO NAVIGATION ARE NOT VESSELS — 16 of 337 MMSIs, 10,972 rows.
   OBSERVED: "Type of mobile" == "AtoN" (buoys, beacons, offshore structures).
   WHY IT MATTERS: an AtoN broadcasts a position and has no hull to photograph. Left
   in, it is a permanent claim with no possible observation — indistinguishable from
   a position spoof to anything downstream. Excluded by default; the count is
   reported so the exclusion is visible rather than assumed.

3. THE SENTINELS ARE ALREADY DECODED IN THE CSV, AND RAW ON THE WIRE.
   OBSERVED: this CSV contains zero occurrences of heading 511, SOG 102.3 or COG
   360.0 — DMA converted them to empty fields. Verified with pyais 3.2.1 that a live
   AIVDM sentence delivers them RAW: speed=102.3, course=360.0, heading=511,
   turn=TurnRate.NO_TI_DEFAULT.
   WHY IT MATTERS: contracts.py requires 511 to become None at ingest. A heading of
   511 degrees survives no sanity check, but a heading of 360.0 does — and a course
   of 360 compared against an observed 000 is a 360-degree delta that a naive
   shortest-arc helper reports as 0. Both paths null the sentinels; the CSV path
   also counts them, so if a future DMA day stops pre-cleaning, the number moves
   instead of the data silently changing meaning.

4. "Unknown" IS A STRING, NOT A NULL — 295,344 IMO rows, 17,370 Callsign rows.
   WHY IT MATTERS: pandas leaves it as the literal text "Unknown". Passed through,
   the evidence record claims an IMO number of "Unknown" — a claim the transponder
   never made, printed on something described as evidence-grade.

5. TIMESTAMPS ARE dd/mm/yyyy AND PANDAS ASSUMES mm/dd/yyyy.
   WHY IT MATTERS: this is the landmine recorded in 02_data/AIS_CSV_SCHEMA.md. It
   raises nothing; it silently swaps every date where the day is <= 12. An explicit
   format string is passed at the single place timestamps are parsed. Do not remove
   it, and do not add a second parse site.

--------------------------------------------------------------------------------
KNOWN LIMITATION, STATED HERE BECAUSE IT REACHES THE VERDICT
`track_id` is a surrogate derived from (source, MMSI). If two hulls broadcast the
SAME MMSI — the textbook identity spoof — they collapse into one track_id here, and
their positions interleave into one impossible trajectory. Lane A cannot separate
them: doing so needs kinematic track-breaking (teleport and implied-speed tests),
which lives with the kinematic spoof logic in lane C. Downstream must not read
"one track_id" as "one vessel". Filed to lane C in 99_scratch/requests.md.
"""

from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Sequence

import pandas as pd

from contracts import AisTrack, Severity, VesselClass

# --------------------------------------------------------------------------------
# Constants settled FROM THE FILE and FROM pyais, not from documentation.
# --------------------------------------------------------------------------------

#: The DMA timestamp format. Day first. See landmine 5 above. One parse site only.
DMA_TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M:%S"

#: Identity of a single reception. Two rows agreeing on all four are the same
#: transmission heard by two basestations, not two reports. See landmine 1.
DEDUPE_KEY: tuple[str, ...] = ("Timestamp", "MMSI", "Latitude", "Longitude")

#: Strings the DMA CSV uses where a value is absent. Compared case-folded. Note that
#: "Undefined" is the ship-type spelling and "Unknown" the identity spelling — both
#: mean the transponder asserted nothing, which contracts.py represents as None.
NULL_STRINGS: frozenset[str] = frozenset({"", "unknown", "undefined", "n/a", "na", "none"})

#: ITU-R M.1371 "not available" encodings, as they arrive on a live wire.
SOG_UNAVAILABLE_KN = 102.3
COG_UNAVAILABLE_DEG = 360.0
HEADING_UNAVAILABLE_DEG = 511
#: pyais returns turn already converted to degrees/minute, EXCEPT these three, which
#: stay as the raw sentinel: +-127 = "turning faster than 5deg/30s, rate unknown",
#: -128 = "no turn information". All three are "rate not available" -> None.
ROT_SENTINELS: frozenset[float] = frozenset({127.0, -127.0, -128.0})

#: Rows that are not vessels. An AtoN is a buoy or beacon. See landmine 2.
NON_VESSEL_MOBILE_TYPES: frozenset[str] = frozenset({"AtoN", "Base Station"})

PIPELINE_SOURCE_KIND_CSV = "dma_csv"
PIPELINE_SOURCE_KIND_NMEA = "nmea_tcp"

# --------------------------------------------------------------------------------
# Ship type -> VesselClass.
#
# POLICY: CONSERVATIVE. An AIS type with no honest equivalent in the VesselClass
# vocabulary maps to "unknown" — meaning "a claim was made, but it is not comparable
# to a camera silhouette" — rather than to whichever class it most resembles.
#
# CAUSE AND EFFECT: consistency.py raises a class mismatch when the claimed class
# and the observed class disagree. If this table guessed that HSC means "passenger",
# every high-speed craft the VLM read as something else would produce a SPOOF whose
# real origin is a mapping choice made here, in lane A, invisible to the report. The
# cost is lost signal on the ~165k rows carrying a service-craft type. The benefit is
# that no SPOOF verdict can originate in this table.
#
# CONTRACT FOR LANE C: "unknown" on either side means NOT COMPARABLE. It must
# suppress the class comparison, never satisfy it and never fail it. Filed in
# 99_scratch/requests.md.
#
# None (absent from this table's values) means the transponder asserted nothing at
# all, which is a different finding from asserting something uninformative.
# --------------------------------------------------------------------------------

DMA_SHIP_TYPE_TO_CLASS: dict[str, VesselClass | None] = {
    # Direct, unambiguous equivalences.
    "cargo": "cargo",
    "tanker": "tanker",
    "fishing": "fishing",
    "passenger": "passenger",
    "tug": "tug",
    "towing": "tug",              # AIS 31/32 — a towing operation is a tug
    "military": "naval",
    "sailing": "small_craft",
    "pleasure": "small_craft",
    # Claim present, not comparable to a silhouette class. See policy above.
    "hsc": "unknown",
    "pilot": "unknown",
    "sar": "unknown",
    "port tender": "unknown",
    "law enforcement": "unknown",
    "medical transport": "unknown",
    "anti-pollution": "unknown",
    "dredging": "unknown",
    "diving": "unknown",
    "wig": "unknown",
    "spare": "unknown",
    "reserved": "unknown",
    "other": "unknown",
    # No claim at all -> None. Listed explicitly so it is a decision, not a fallthrough.
    "undefined": None,
    "not available": None,
}

#: ITU-R M.1371 numeric ship-type ranges, for the live path where pyais hands over a
#: ShipType enum (an int). Same conservative policy as the table above; the ranges
#: are the standard's, not ours. First matching range wins.
AIS_SHIP_TYPE_CODE_RANGES: Sequence[tuple[int, int, VesselClass | None]] = (
    (0, 0, None),             # 0 = not available
    (20, 29, "unknown"),      # WIG
    (30, 30, "fishing"),
    (31, 32, "tug"),          # towing
    (33, 34, "unknown"),      # dredging / diving ops
    (35, 35, "naval"),        # military ops
    (36, 37, "small_craft"),  # sailing / pleasure craft
    (40, 49, "unknown"),      # high-speed craft
    (50, 59, "unknown"),      # pilot, SAR, tug*, tender, law enforcement, medical
    (60, 69, "passenger"),
    (70, 79, "cargo"),
    (80, 89, "tanker"),
    (90, 99, "unknown"),      # other
)
# *AIS 52 is "Tug" and sits inside the 50-59 service block. It is broken out below
# rather than inside the range table, because a range cannot express one exception.
AIS_SHIP_TYPE_CODE_EXCEPTIONS: dict[int, VesselClass | None] = {52: "tug"}


# --------------------------------------------------------------------------------
# Ingest diagnostics.
#
# WHY THIS IS NOT A dict: CLAUDE.md forbids bare dicts across module boundaries, and
# these numbers are the evidence that ingest did what it says. Criterion 4 asks for
# honest limits; "we dropped 761,196 rows" is a limit, and it has to be countable
# rather than asserted. This object never crosses into another lane's module — it is
# lane A reporting on itself — so it lives here rather than in contracts.py.
# --------------------------------------------------------------------------------

@dataclass
class IngestStats:
    """Counters describing exactly what ingest saw, kept and discarded."""

    source_kind: str = ""
    source_id: str = ""

    rows_read: int = 0
    rows_outside_time_window: int = 0
    rows_outside_bbox: int = 0
    rows_non_vessel: int = 0            # AtoN / base station — landmine 2
    rows_duplicate_reception: int = 0   # landmine 1
    rows_missing_position: int = 0
    rows_unparseable_timestamp: int = 0
    tracks_emitted: int = 0

    #: How many times each ITU sentinel had to be nulled. On the 2026-08-25 DMA CSV
    #: every one of these is expected to be 0 because DMA pre-cleans them. A non-zero
    #: value is not an error — it is the signal that a source stopped pre-cleaning.
    sentinels_nulled: dict[str, int] = field(default_factory=dict)

    #: Ship-type strings/codes absent from the mapping tables, with counts. Non-empty
    #: means the vocabulary moved and DMA_SHIP_TYPE_TO_CLASS needs a line. These are
    #: emitted as claimed_ship_type=None, never guessed.
    unmapped_ship_types: dict[str, int] = field(default_factory=dict)

    #: Distinct claimed MMSIs seen. A count, not the values — this object gets logged.
    distinct_mmsi: set[str] = field(default_factory=set)

    def note_sentinel(self, name: str) -> None:
        self.sentinels_nulled[name] = self.sentinels_nulled.get(name, 0) + 1

    def note_unmapped(self, raw: str) -> None:
        self.unmapped_ship_types[raw] = self.unmapped_ship_types.get(raw, 0) + 1

    def summary(self) -> str:
        """One block, safe to paste into STATUS.md or a handoff."""
        lines = [
            f"source      : {self.source_kind} :: {self.source_id}",
            f"rows read   : {self.rows_read:,}",
            f"  dropped, outside time window : {self.rows_outside_time_window:,}",
            f"  dropped, outside bbox        : {self.rows_outside_bbox:,}",
            f"  dropped, not a vessel (AtoN) : {self.rows_non_vessel:,}",
            f"  dropped, duplicate reception : {self.rows_duplicate_reception:,}",
            f"  dropped, no position         : {self.rows_missing_position:,}",
            f"  dropped, bad timestamp       : {self.rows_unparseable_timestamp:,}",
            f"tracks out  : {self.tracks_emitted:,}",
            f"distinct claimed MMSI : {len(self.distinct_mmsi):,}",
            f"sentinels nulled      : {self.sentinels_nulled or 'none'}",
            f"unmapped ship types   : {self.unmapped_ship_types or 'none'}",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------------
# Field-level cleaning. Pure functions: no pandas, no pydantic, no I/O.
# Kept pure so they can be unit-tested against literal values, which is the only way
# a "None vs 0 vs 511" bug gets caught before it reaches a verdict.
# --------------------------------------------------------------------------------

def clean_string(value: Any) -> str | None:
    """
    Text field -> str, or None when the transponder asserted nothing.

    Handles landmine 4: DMA writes the literal text "Unknown"/"Undefined" rather than
    an empty field, so a naive read produces a claimed IMO of "Unknown" printed on an
    evidence record. None here means "no claim", which is itself a finding.
    """
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    if text.casefold() in NULL_STRINGS:
        return None
    return text


def clean_float(value: Any, *, sentinel: float | None = None,
                sentinels: frozenset[float] | None = None,
                stats: IngestStats | None = None, name: str = "") -> float | None:
    """
    Numeric field -> float, or None for absent/sentinel values.

    `sentinel`/`sentinels` are the ITU "not available" encodings (landmine 3). They
    are counted rather than silently dropped, because a source that stops pre-cleaning
    them should show up as a number moving, not as data quietly changing meaning.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN — pd.isna without importing for one check
        return None
    hit = (sentinel is not None and number == sentinel) or (
        sentinels is not None and number in sentinels)
    if hit:
        if stats is not None and name:
            stats.note_sentinel(name)
        return None
    return number


def vessel_class_from_dma_string(raw: Any, stats: IngestStats | None = None) -> VesselClass | None:
    """
    DMA "Ship type" text -> VesselClass, under the conservative policy above.

    Returns None when the transponder asserted nothing OR when the string is one this
    table has never seen. An unseen string is recorded in stats.unmapped_ship_types so
    it surfaces as a number instead of becoming a guess.
    """
    text = clean_string(raw)
    if text is None:
        return None
    key = text.casefold()
    if key in DMA_SHIP_TYPE_TO_CLASS:
        return DMA_SHIP_TYPE_TO_CLASS[key]
    if stats is not None:
        stats.note_unmapped(text)
    return None


def vessel_class_from_ais_code(code: Any, stats: IngestStats | None = None) -> VesselClass | None:
    """ITU-R M.1371 numeric ship type -> VesselClass, same conservative policy."""
    if code is None:
        return None
    try:
        number = int(code)
    except (TypeError, ValueError):
        return None
    if number in AIS_SHIP_TYPE_CODE_EXCEPTIONS:
        return AIS_SHIP_TYPE_CODE_EXCEPTIONS[number]
    for low, high, mapped in AIS_SHIP_TYPE_CODE_RANGES:
        if low <= number <= high:
            return mapped
    if stats is not None:
        stats.note_unmapped(str(number))
    return None


def normalise_mmsi(value: Any) -> str | None:
    """
    MMSI -> a 9-character STRING.

    contracts.py: identifiers are strings because leading zeros are significant and
    an MMSI is not a quantity. pandas reads the column as int64 and pyais hands over
    a plain int, both of which destroy a leading zero — so zero-padding happens here,
    once, rather than in four downstream modules with four different opinions.
    """
    if value is None:
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        text = str(value).strip()
        return text or None
    if number <= 0:
        return None
    return f"{number:09d}"


def positive_or_none(value: float | None) -> float | None:
    """
    Zero -> None for fields where AIS encodes "not available" AS zero.

    WHY THIS EXISTS SEPARATELY FROM clean_float: contracts.py is explicit that None
    means "not available", never zero. On the live path, message 5/24 dimensions and
    the IMO field default to 0 when the transponder has nothing to say. A stored
    claimed_size_a_m of 0.0 is a claim that the bow is at the antenna — which
    consistency.py would compare against an observed length and find a mismatch of
    the vessel's entire hull. The CSV path does not need this: DMA leaves those
    fields empty rather than zero.
    """
    if value is None or value == 0.0:
        return None
    return value


def make_track_id(source_id: str, mmsi: str) -> str:
    """
    Surrogate track identifier. Deterministic for a given (source, MMSI).

    WHY NOT THE MMSI ITSELF: contracts.py forbids it, because an MMSI is spoofable
    and non-unique, so using it as a join key means a forged identity silently
    inherits the real vessel's history.

    WHY A HASH RATHER THAN A COUNTER: the same source re-read produces the same ids,
    so an evidence record written at 02:00 still resolves after a re-run at 04:00.
    A counter would renumber on any change of filter.

    LIMITATION (repeated from the module docstring because it reaches the verdict):
    two hulls sharing one MMSI collapse into ONE track_id here. Separating them is
    kinematic track-breaking and belongs with lane C's kinematic spoof logic.
    """
    digest = hashlib.sha1(f"{source_id}|{mmsi}".encode("utf-8")).hexdigest()
    return f"AIS-{digest[:8]}"


# --------------------------------------------------------------------------------
# Frame-level cleaning. Shared by the ingest path and by 02_data/make_golden_window.py
# so the golden window on disk is cleaned by exactly the code that cleans the stream.
# If these diverge, the demo runs on data the pipeline never saw.
# --------------------------------------------------------------------------------

def assert_csv_dialect(path: Path) -> None:
    """
    Refuse to guess the delimiter. Fail loudly instead.

    WHY: AIS_CSV_SCHEMA.md landmine 1 — the DMA README prints coordinates as
    "57,8794" with a Danish decimal comma. Measured on aisdk-2026-08-25: the real
    file is comma-delimited with a '.' decimal, so that README line was locale prose,
    not the format. But a future DMA export could differ, and getting it wrong does
    not crash — it silently shifts every position, which is the worst possible
    failure for something whose output is called evidence.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        header = handle.readline()
    if header.count(";") > header.count(","):
        raise ValueError(
            f"{path.name}: header looks semicolon-delimited, which implies a decimal "
            "comma. This reader is configured for ',' + '.' as measured on the "
            "2026-08-25 export. Re-check the dialect before changing this."
        )


def normalise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Strip whitespace and the leading '# ' from the first column header."""
    frame = frame.copy()
    frame.columns = [str(c).strip().lstrip("# ") for c in frame.columns]
    return frame


def add_report_time(frame: pd.DataFrame, stats: IngestStats | None = None) -> pd.DataFrame:
    """
    Parse the DMA timestamp column into a tz-aware UTC column, ONCE, with an
    EXPLICIT format. See landmine 5 — this is the only parse site in the project.
    Unparseable rows become NaT and are counted, then dropped by filter_frame.
    """
    frame = frame.copy()
    frame["report_time_utc"] = pd.to_datetime(
        frame["Timestamp"], format=DMA_TIMESTAMP_FORMAT, utc=True, errors="coerce")
    if stats is not None:
        stats.rows_unparseable_timestamp += int(frame["report_time_utc"].isna().sum())
    return frame


def filter_frame(
    frame: pd.DataFrame,
    *,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    include_non_vessels: bool = False,
    stats: IngestStats | None = None,
) -> pd.DataFrame:
    """
    Apply the window, the box and the not-a-vessel exclusion, counting every drop.

    `bbox` is (lat_min, lat_max, lon_min, lon_max) — the same convention as
    02_data/subset_ais.py, deliberately, so the two scripts cannot disagree about
    which corner is which.
    """
    if "report_time_utc" not in frame.columns:
        raise KeyError("filter_frame requires add_report_time to have run first")

    keep = frame["report_time_utc"].notna()

    if start_utc is not None or end_utc is not None:
        in_window = keep.copy()
        if start_utc is not None:
            in_window &= frame["report_time_utc"] >= start_utc
        if end_utc is not None:
            in_window &= frame["report_time_utc"] < end_utc
        if stats is not None:
            stats.rows_outside_time_window += int((keep & ~in_window).sum())
        keep &= in_window

    lat = pd.to_numeric(frame["Latitude"], errors="coerce")
    lon = pd.to_numeric(frame["Longitude"], errors="coerce")
    # A raw AIS "position not available" is lat 91 / lon 181. contracts.py constrains
    # latitude to +-90, so such a row would raise a ValidationError deep inside a
    # generator. Drop it here, where it can be counted.
    has_position = lat.between(-90.0, 90.0) & lon.between(-180.0, 180.0)
    if stats is not None:
        stats.rows_missing_position += int((keep & ~has_position).sum())
    keep &= has_position

    if bbox is not None:
        lat_min, lat_max, lon_min, lon_max = bbox
        in_box = lat.between(lat_min, lat_max) & lon.between(lon_min, lon_max)
        if stats is not None:
            stats.rows_outside_bbox += int((keep & ~in_box).sum())
        keep &= in_box

    if not include_non_vessels and "Type of mobile" in frame.columns:
        # Landmine 2. An AtoN has a position and no hull, so it can never be matched
        # to a camera contact and would masquerade as a permanent position spoof.
        is_vessel = ~frame["Type of mobile"].astype(str).str.strip().isin(
            NON_VESSEL_MOBILE_TYPES)
        if stats is not None:
            stats.rows_non_vessel += int((keep & ~is_vessel).sum())
        keep &= is_vessel

    return frame[keep]


def drop_duplicate_receptions(
    frame: pd.DataFrame, seen: set[int] | None = None, stats: IngestStats | None = None
) -> pd.DataFrame:
    """
    Collapse the same transmission heard by several basestations into one report.
    Landmine 1: 63.5% of rows on the measured day.

    `seen` carries dedupe state ACROSS chunks. Pass a set to dedupe a whole file;
    pass None to dedupe within this frame only.

    MEMORY NOTE, stated because it is a real limit: `seen` holds one int per surviving
    row. That is fine for a filtered window (432k rows on the whole Fehmarn day) and
    is NOT fine for an unfiltered 32.6M-row day. Filter first, or dedupe per chunk.
    """
    before = len(frame)
    if seen is None:
        frame = frame.drop_duplicates(subset=list(DEDUPE_KEY))
    else:
        keys = [hash(t) for t in zip(*(frame[c] for c in DEDUPE_KEY))]
        mask = []
        for key in keys:
            fresh = key not in seen
            if fresh:
                seen.add(key)
            mask.append(fresh)
        frame = frame[pd.Series(mask, index=frame.index)]
    if stats is not None:
        stats.rows_duplicate_reception += before - len(frame)
    return frame


def iter_records(frame: pd.DataFrame) -> Iterator[dict[str, Any]]:
    """
    Row-wise iteration that keeps memory flat and preserves column names containing
    spaces ("Type of mobile"), which itertuples() renames and to_dict() materialises
    all at once.
    """
    columns = list(frame.columns)
    arrays = [frame[c].to_numpy(dtype=object) for c in columns]
    for values in zip(*arrays):
        yield dict(zip(columns, values))


def row_to_track_fields(
    record: dict[str, Any], source_id: str, stats: IngestStats | None = None
) -> dict[str, Any] | None:
    """
    One cleaned DMA CSV row -> the keyword arguments for AisTrack.

    Returns None when the row cannot support a claim at all (no MMSI). Deliberately
    returns a plain dict rather than an AisTrack so it can be tested without pydantic
    installed, and so the mapping is inspectable field by field.

    EVERY value produced here is a CLAIM. Nothing observed enters this dict.
    """
    mmsi = normalise_mmsi(record.get("MMSI"))
    if mmsi is None:
        return None
    if stats is not None:
        stats.distinct_mmsi.add(mmsi)

    return {
        "track_id": make_track_id(source_id, mmsi),
        "claimed_mmsi": mmsi,
        "report_time_utc": record["report_time_utc"].to_pydatetime(),
        "claimed_lat_deg": float(record["Latitude"]),
        "claimed_lon_deg": float(record["Longitude"]),
        # Sentinels are counted rather than silently nulled — see landmine 3. On the
        # 2026-08-25 export all three counters are expected to stay at zero.
        "claimed_sog_kn": clean_float(
            record.get("SOG"), sentinel=SOG_UNAVAILABLE_KN, stats=stats, name="sog_102.3"),
        "claimed_cog_deg_true": clean_float(
            record.get("COG"), sentinel=COG_UNAVAILABLE_DEG, stats=stats, name="cog_360"),
        "claimed_heading_deg_true": clean_float(
            record.get("Heading"), sentinel=float(HEADING_UNAVAILABLE_DEG),
            stats=stats, name="heading_511"),
        "claimed_rot_deg_per_min": clean_float(record.get("ROT")),
        "claimed_ship_type": vessel_class_from_dma_string(record.get("Ship type"), stats),
        # Length/Width and A-D are INDEPENDENT claims in the CSV. A+B should equal
        # Length and C+D should equal Width; keeping both means a dimension spoof has
        # to keep two fields consistent, not one. Never derive one from the other here.
        "claimed_length_m": clean_float(record.get("Length")),
        "claimed_width_m": clean_float(record.get("Width")),
        "claimed_size_a_m": clean_float(record.get("A")),
        "claimed_size_b_m": clean_float(record.get("B")),
        "claimed_size_c_m": clean_float(record.get("C")),
        "claimed_size_d_m": clean_float(record.get("D")),
        "claimed_name": clean_string(record.get("Name")),
        "claimed_imo": clean_string(record.get("IMO")),
        "claimed_callsign": clean_string(record.get("Callsign")),
        "claimed_nav_status": clean_string(record.get("Navigational status")),
        "mobile_class": clean_string(record.get("Type of mobile")),
    }


# --------------------------------------------------------------------------------
# The one interface.
#
# T2 (live on the Elbe) must be a SOURCE SWAP, not a pipeline change. If the demo
# runner has to know which source it holds, then the recorded fallback and the live
# path are two different programs, and the rehearsed fallback stops being a fallback.
# That is the only justification for this abstraction, and it is sufficient.
# --------------------------------------------------------------------------------

class AisSource(abc.ABC):
    """A source of CLAIMS. Yields AisTrack, one per position report."""

    def __init__(self, source_kind: str, source_id: str) -> None:
        self.source_id = source_id
        self.stats = IngestStats(source_kind=source_kind, source_id=source_id)

    @abc.abstractmethod
    def tracks(self) -> Iterator[AisTrack]:
        """Yield AisTrack objects. Consumers must not assume the stream terminates."""
        raise NotImplementedError

    def __iter__(self) -> Iterator[AisTrack]:
        return self.tracks()


class DmaCsvSource(AisSource):
    """
    Recorded Danish Maritime Authority bulk AIS (CSV). The T1 path.

    LICENCE — Danish act no. 596 of 24 June 2005 on the further use of public sector
    information. DMA guarantees no correctness and accepts no liability. AIS may NOT
    be combined with other datasets to identify individuals without Danish Data
    Protection Agency authorisation. Redistribution and attribution are NOT addressed
    by the published policy and DMA is separately authorised to sell AIS
    commercially — so "free to download" is not "openly licensed". Full text and the
    consequences for committing data to this repo: 02_data/INVENTORY.md.

    The CSV is already decoded: DMA runs the AIS decoder, so pyais is NOT used on this
    path and there is nothing here to hand-roll.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        dedupe: bool = True,
        include_non_vessels: bool = False,
        chunk_rows: int = 500_000,
    ) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        assert_csv_dialect(self.path)
        super().__init__(PIPELINE_SOURCE_KIND_CSV, self.path.stem)
        self.start_utc = start_utc
        self.end_utc = end_utc
        self.bbox = bbox
        self.dedupe = dedupe
        self.include_non_vessels = include_non_vessels
        self.chunk_rows = chunk_rows

    def frames(self) -> Iterator[pd.DataFrame]:
        """
        Cleaned, filtered, deduplicated frames — the stage before AisTrack.

        Exposed because 02_data/make_golden_window.py needs the cleaned FRAME to write
        a CSV, and building it from a different code path is how the demo ends up
        running on data the pipeline never cleaned.
        """
        seen: set[int] | None = set() if self.dedupe else None
        reader = pd.read_csv(
            self.path,
            sep=",", decimal=".",          # settled by assert_csv_dialect, not guessed
            chunksize=self.chunk_rows,
            low_memory=False,
        )
        for chunk in reader:
            self.stats.rows_read += len(chunk)
            chunk = normalise_columns(chunk)
            chunk = add_report_time(chunk, self.stats)
            chunk = filter_frame(
                chunk,
                start_utc=self.start_utc,
                end_utc=self.end_utc,
                bbox=self.bbox,
                include_non_vessels=self.include_non_vessels,
                stats=self.stats,
            )
            if self.dedupe:
                chunk = drop_duplicate_receptions(chunk, seen, self.stats)
            if len(chunk):
                yield chunk

    def tracks(self) -> Iterator[AisTrack]:
        for frame in self.frames():
            for record in iter_records(frame):
                fields = row_to_track_fields(record, self.source_id, self.stats)
                if fields is None:
                    continue
                self.stats.tracks_emitted += 1
                yield AisTrack(**fields)


class NmeaLiveSource(AisSource):
    """
    Live NMEA 0183 / AIVDM over TCP, decoded by pyais. The T2 path.

    STATIC/DYNAMIC MERGE: a position report (type 1/2/3/18/19) carries no name, ship
    type or dimensions; a static report (type 5/24) carries no position. pyais's
    AISTracker keeps the merged per-MMSI state, so an AisTrack emitted here carries
    whatever static data has been heard SO FAR. Early in a connection that is
    nothing, and a vessel legitimately looks type-less for its first few minutes.
    Lane C must not read "no claimed ship type" on a fresh live track as evasion —
    it is the cold-start of the merge. mobile_class is the discriminator: a blank
    ship type is normal on Class B and suspicious on Class A.

    THREE DIFFERENCES FROM THE CSV PATH, each one a criterion 4 limitation:
      1. report_time_utc is the ARRIVAL wall clock, not a basestation timestamp. Raw
         AIVDM carries only the UTC second of the minute. Transport delay therefore
         lands inside Association.time_delta_s and can look like AIS staleness.
      2. claimed_length_m / claimed_width_m are DERIVED from A+B and C+D, because AIS
         message 5 has no length field — DMA derives its Length column the same way.
         Consequence: the Length-vs-(A+B) cross-check described in
         02_data/AIS_CSV_SCHEMA.md is a RECORDED-PATH-ONLY capability. On the live
         path those two fields agree by construction and prove nothing.
      3. claimed_nav_status uses the pyais enum name ("UnderWayUsingEngine"), while
         the CSV uses DMA prose ("Under way using engine"). Do not string-compare
         nav status across the two paths.
    """

    #: Only these carry a position, and AisTrack requires one.
    POSITION_MESSAGE_TYPES: frozenset[int] = frozenset({1, 2, 3, 18, 19})
    #: Class A transmits 1/2/3/5; Class B transmits 18/19/24.
    CLASS_B_MESSAGE_TYPES: frozenset[int] = frozenset({18, 19, 24})

    def __init__(self, host: str, port: int, *, track_ttl_s: int | None = 600) -> None:
        super().__init__(PIPELINE_SOURCE_KIND_NMEA, f"{host}:{port}")
        self.host = host
        self.port = port
        self.track_ttl_s = track_ttl_s

    def tracks(self) -> Iterator[AisTrack]:
        # Imported lazily so the recorded T1 path still runs where pyais is absent.
        # NEVER replace this with a hand-written AIVDM parser (CLAUDE.md, LIBRARIES.md).
        from pyais import AISTracker, TCPConnection

        # pyais 3.2.1: AISTracker.now() is documented as milliseconds but returns
        # time.time(), i.e. SECONDS, and ttl_in_seconds is compared against it.
        # Verified by reading tracker.py. Passing milliseconds here would make the TTL
        # ~1000x too long and tracks would never expire.
        tracker = AISTracker(ttl_in_seconds=self.track_ttl_s)

        with TCPConnection(self.host, port=self.port) as stream:
            for sentence in stream:
                message = sentence.decode()
                arrival = datetime.now(timezone.utc)
                self.stats.rows_read += 1
                tracker.update(message, ts_epoch_ms=arrival.timestamp())

                if int(getattr(message, "msg_type", 0)) not in self.POSITION_MESSAGE_TYPES:
                    continue  # static-only message: merged above, nothing to emit yet
                merged = tracker.get_track(int(message.mmsi))
                if merged is None:
                    continue
                track = self._merged_to_track(merged, message, arrival)
                if track is None:
                    continue
                self.stats.tracks_emitted += 1
                yield track

    def _merged_to_track(self, merged: Any, message: Any, arrival: datetime) -> AisTrack | None:
        """pyais AISTrack (merged static + dynamic) -> our AisTrack of CLAIMS."""
        mmsi = normalise_mmsi(merged.mmsi)
        if mmsi is None or merged.lat is None or merged.lon is None:
            self.stats.rows_missing_position += 1
            return None
        self.stats.distinct_mmsi.add(mmsi)

        lat = clean_float(merged.lat)
        lon = clean_float(merged.lon)
        if lat is None or lon is None or not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            self.stats.rows_missing_position += 1
            return None

        # Dimensions: AIS 5/24 gives distances from the GPS antenna to bow/stern/port/
        # starboard. Length and width are their sums — see limitation 2 above.
        a = positive_or_none(clean_float(merged.to_bow))
        b = positive_or_none(clean_float(merged.to_stern))
        c = positive_or_none(clean_float(merged.to_port))
        d = positive_or_none(clean_float(merged.to_starboard))
        length = a + b if (a and b) else None
        width = c + d if (c and d) else None

        status = getattr(merged, "status", None)
        msg_type = int(getattr(message, "msg_type", 0))

        return AisTrack(
            track_id=make_track_id(self.source_id, mmsi),
            claimed_mmsi=mmsi,
            report_time_utc=arrival,
            claimed_lat_deg=lat,
            claimed_lon_deg=lon,
            claimed_sog_kn=clean_float(
                merged.speed, sentinel=SOG_UNAVAILABLE_KN,
                stats=self.stats, name="sog_102.3"),
            claimed_cog_deg_true=clean_float(
                merged.course, sentinel=COG_UNAVAILABLE_DEG,
                stats=self.stats, name="cog_360"),
            claimed_heading_deg_true=clean_float(
                merged.heading, sentinel=float(HEADING_UNAVAILABLE_DEG),
                stats=self.stats, name="heading_511"),
            claimed_rot_deg_per_min=clean_float(
                merged.turn, sentinels=ROT_SENTINELS, stats=self.stats, name="rot_no_ti"),
            claimed_ship_type=vessel_class_from_ais_code(merged.ship_type, self.stats),
            claimed_length_m=length,
            claimed_width_m=width,
            claimed_size_a_m=a,
            claimed_size_b_m=b,
            claimed_size_c_m=c,
            claimed_size_d_m=d,
            claimed_name=clean_string(merged.shipname),
            claimed_imo=clean_string(merged.imo) if merged.imo else None,
            claimed_callsign=clean_string(merged.callsign),
            claimed_nav_status=getattr(status, "name", None) if status is not None else None,
            mobile_class="Class B" if msg_type in self.CLASS_B_MESSAGE_TYPES else "Class A",
        )


# ================================================================================
# PLAUSIBILITY — does the claimed track contradict itself?
#
# WHY THIS LIVES IN ais_ingest AND NOT IN ais_trajectory
# It is a per-report check with one report of memory, so it works on the LIVE path
# too: T2 can flag a bad position fix as it arrives, before anything downstream sees
# it. ais_trajectory works on whole trajectories and cannot do that.
#
# THE DEPENDENCY RULE IS PRESERVED. shapely, geopandas and pyproj are imported
# LAZILY, inside the functions that need them — the same pattern NmeaLiveSource uses
# for pyais. Importing this module and reading a CSV still requires nothing but
# pandas. A teammate with a broken geopandas install can read AIS, run the speed and
# teleport checks, and only loses the land check.
#
# WHAT THIS IS NOT: it is not a spoof detector. Every finding here is a CLAIM
# CONTRADICTING ITSELF, and by far the most common cause of that is a cheap GPS on a
# small Class B craft, not deception. The output is a data-quality signal that lane C
# may promote to SpoofSubtype="kinematic" if — and only if — other evidence agrees.
# Saying otherwise about a real hull is defamatory.
#
# --------------------------------------------------------------------------------
# THE MEASUREMENT THAT SET THE DEFAULTS, and why the naive version is 36x wrong
#
# Fehmarn slice, 431,430 consecutive report pairs. Implied speed = geodesic distance
# between consecutive claimed positions, over the time between them.
#
#   implied speed > 50 kn, no other condition   ->   705 pairs, 146 vessels
#   ...of which 564 (80%) are over dt <= 2 s, with a MEDIAN JUMP OF 57 METRES.
#
# 57 m in 1 second is 111 knots. It is also the ordinary scatter of a consumer GPS
# fix. At 1 Hz reporting, position NOISE dominates position CHANGE, so a naive
# implied-speed check is measuring the receiver, not the vessel. Requiring a minimum
# sampling interval collapses the count:
#
#   min dt      pairs over 50 kn      vessels
#     0 s               705             146
#     2 s               345             106
#     5 s                50              34
#    10 s                19              16      <- the shipped default
#    30 s                 6               6
#
# 705 -> 19 is a factor of 37. Ship the naive version and 96% of what the operator
# sees is receiver noise, which is how a decision-support tool trains its user to
# ignore it. The defaults below are chosen from that table, and the thresholds travel
# on every finding so the number can never be quoted without them.
#
# THE ZERO-INTERVAL TRAP, measured: 956 consecutive pairs share a timestamp exactly.
# ais_ingest deduplicates on (Timestamp, MMSI, Latitude, Longitude), so two
# basestations that decoded the SAME transmission to slightly different coordinates
# both survive — 583 of the 956 differ by under 5 metres, which is rounding.
# dt = 0 makes implied speed infinite, so unhandled these are 956 phantom teleports,
# AND a ZeroDivisionError waiting for whichever consumer divides first.
#
# But 21 of them differ by MORE THAN 100 METRES in the same second, and that is not
# rounding — a hull cannot be in two places at once. The most likely explanation is
# the one lane A already flagged as a known limitation: TWO VESSELS SHARING ONE MMSI,
# which is precisely the identity spoof criterion 3 calls the discriminator. So
# zero-interval pairs are not discarded; they are split at a distance threshold and
# the large ones are reported as their own kind.
# ================================================================================

#: The `dimension` value these findings carry. Requested as an addition to
#: `contracts.MismatchDimension` in 99_scratch/requests.md — until ARCH lands it, the
#: closed Literal in contracts.py cannot hold this value, which is why the record
#: below is a local mirror rather than a real `contracts.Mismatch`.
POSITION_IMPLAUSIBLE = "position_implausible"

ImplausibilityKind = Literal[
    "impossible_speed",     # implied speed exceeds any plausible hull speed
    "teleport",             # impossible speed AND a large absolute jump
    "zero_interval_jump",   # two positions in the same second, far apart
    "crosses_land",         # the segment between two reports passes over land
]

#: No surface vessel in the Fehmarn slice claims more than 47.0 kn (measured), so 50
#: is above every declared speed in the data while still far below the ~110 kn that
#: 1 Hz GPS scatter manufactures.
DEFAULT_MAX_SPEED_KN = 50.0
#: Below this sampling interval, position noise dominates position change. See the
#: table above — this single parameter is the difference between 705 findings and 19.
DEFAULT_MIN_INTERVAL_S = 10.0
#: A jump smaller than this is within GPS scatter regardless of interval.
DEFAULT_MIN_JUMP_M = 100.0
#: An impossible speed over a jump this large is a teleport rather than a bad fix.
DEFAULT_TELEPORT_JUMP_M = 1000.0
#: Same-timestamp pairs further apart than this cannot be one hull. 583 of 956 such
#: pairs differ by under 5 m (basestation rounding); the tail is the interesting part.
DEFAULT_ZERO_INTERVAL_JUMP_M = 100.0
#: The coastline is 1:100,000, so its edge is good to roughly a hundred metres and a
#: vessel alongside a quay can fall inside the land polygon. Land is ERODED by this
#: much before testing, so only unambiguously overland tracks fire.
DEFAULT_LAND_EROSION_M = 250.0

PLAUSIBILITY_RULESET_VERSION = "lane-a-plausibility-1.0"


@dataclass(frozen=True)
class PlausibilityMismatch:
    """
    One way in which a claimed track contradicts itself.

    LOCAL MIRROR of the `contracts.Mismatch` extension requested in
    `99_scratch/requests.md`. Field names are the requested names so the swap is an
    import change.

    THE TWO FIELDS THAT FORCED A MIRROR RATHER THAN A REAL `Mismatch`:
      * `association_id` is None. `contracts.Mismatch` requires one, but there is no
        association here — nothing was observed, so nothing was paired. A synthetic
        id would put a fabricated association into an evidence record.
      * there is no `observation_confidence`, because nothing observed this. The
        comparison is claim against claim.

    `comparison="claimed_vs_implied"` is the discriminator that keeps the wall
    standing. An implied value is computed from consecutive CLAIMED POSITIONS: it is
    not an observation, and a consumer that reads it as one would weight a verdict
    using an EO uncertainty that does not exist.
    """

    mismatch_id: str
    track_id: str
    claimed_mmsi: str
    kind: ImplausibilityKind

    dimension: str = POSITION_IMPLAUSIBLE
    comparison: str = "claimed_vs_implied"
    association_id: None = None

    claimed_value: float | str = 0.0
    claimed_field: str = ""
    implied_value: float | str = 0.0
    implied_field: str = ""
    unit: str = "none"

    delta: float | None = None
    tolerance: float | None = None
    significance: float = 0.0
    severity: Severity = "minor"

    t_start_utc: datetime | None = None
    t_end_utc: datetime | None = None
    dt_s: float | None = None
    from_lat_deg: float | None = None
    from_lon_deg: float | None = None
    to_lat_deg: float | None = None
    to_lon_deg: float | None = None
    distance_m: float | None = None

    #: True when the sampling interval or jump size is small enough that receiver
    #: noise is a sufficient explanation. Never scored as a finding.
    explained_by_sampling_noise: bool = False
    #: The thresholds this finding was produced under. A count without its thresholds
    #: is not a measurement — same reasoning as `Verdict.ruleset_version`.
    detector: dict[str, Any] = field(default_factory=dict)
    ruleset_version: str = PLAUSIBILITY_RULESET_VERSION


#: Metric CRS for the land check. ETRS89 / UTM 32N covers Denmark and the Fehmarn
#: Belt; its units are metres, so the erosion buffer means what it says. Web Mercator
#: would be ~1.7x wrong at 54.6 N — it is a display projection, not a metric one.
LAND_METRIC_CRS = "EPSG:25832"


class LandMask:
    """
    Land polygons, for "did this track cross land".

    DATA — EEA coastline for analysis (polygon), version 3.0, March 2017.
      * Licence: **CC-BY 4.0**, copyright holder European Environment Agency, "no
        limitations to public access". Attribution is required and is satisfied by
        citing the EEA on any slide that shows a land-crossing finding.
      * Scale: 1:100,000 minimum mapping unit. Native CRS **EPSG:3035**
        (ETRS89-extended / LAEA Europe) — NOT 4326, so it must be reprojected.
      * Lineage: a hybrid of EUHYDRO and GSHHG, cut at EUDEM altitude 0.
      * Download: https://sdi.eea.europa.eu/data/9faa6ea1-372a-4826-a3c7-fb5b05e31c52

    WHY NOT A COARSER COASTLINE, stated because the obvious shortcut is actively
    harmful: geopandas 1.1.4 removed its bundled Natural Earth dataset, and the only
    shapefile left on disk is a 1:110,000,000 fixture inside pyogrio's test folder.
    Its coastline error is kilometres. The Fehmarn Belt is about 18 km wide. Using it
    would report that every vessel near Rødbyhavn and Puttgarden had sailed overland
    — false positives aimed precisely at the vessels closest to the infrastructure
    this tool exists to protect. A wrong coastline is worse than no coastline,
    because no coastline is visibly missing and a wrong one is not.

    WHY LAND IS ERODED BEFORE TESTING: at 1:100,000 the shoreline is good to roughly
    a hundred metres, and harbours, quays and the Fehmarnbelt tunnel works are inside
    that error. A vessel moored alongside a pier legitimately sits within the land
    polygon. Eroding land inward by `erosion_m` means only unambiguously overland
    tracks fire, at the cost of missing a genuine short cut across a headland. That
    trade is deliberate: this is decision support, and a false accusation costs more
    than a missed one.
    """

    def __init__(self, path: str | Path, *,
                 erosion_m: float = DEFAULT_LAND_EROSION_M,
                 bbox: tuple[float, float, float, float] | None = None) -> None:
        # Lazy: geopandas and shapely are NOT dependencies of reading AIS.
        import geopandas as gpd
        from shapely import STRtree

        self.path = Path(path)
        self.erosion_m = erosion_m
        if not self.path.exists():
            raise FileNotFoundError(
                f"coastline not found: {self.path}. Land check cannot run. See the "
                "download URL in LandMask's docstring.")

        land = gpd.read_file(self.path)
        if land.crs is None:
            raise ValueError(
                f"{self.path.name} has no CRS. Refusing to guess — an unprojected "
                "shapefile silently treated as degrees puts the coastline in the "
                "wrong ocean.")
        # ETRS89 / UTM 32N: metres over Denmark, so the erosion buffer is metres.
        land = land.to_crs(LAND_METRIC_CRS)

        if bbox is not None:
            lat_min, lat_max, lon_min, lon_max = bbox
            from shapely.geometry import box as _box
            clip = gpd.GeoSeries(
                [_box(lon_min, lat_min, lon_max, lat_max)], crs="EPSG:4326"
            ).to_crs(LAND_METRIC_CRS).iloc[0].buffer(20_000)
            land = land[land.intersects(clip)]

        eroded = land.geometry.buffer(-erosion_m)
        self.geometries = [g for g in eroded if g is not None and not g.is_empty]
        # An STRtree turns "does this 30 m segment hit any land polygon" from a scan
        # over every polygon in Europe into an indexed lookup. Without it the check
        # is O(segments x polygons) and will not finish on a day of AIS.
        self.tree = STRtree(self.geometries)
        self.n_polygons = len(self.geometries)

        # always_xy=True means transform(lon, lat) -> (x, y). Without it pyproj uses
        # the CRS's own axis order, which for several EPSG codes is lat-then-lon, and
        # the coastline silently ends up in the wrong hemisphere.
        from pyproj import Transformer
        self._to_metric = Transformer.from_crs(
            "EPSG:4326", LAND_METRIC_CRS, always_xy=True)

    def crosses(self, lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> bool:
        """
        True when the straight segment between two positions passes over land.

        PERFORMANCE, because this is called once per evaluated pair — hundreds of
        thousands of times on a day of AIS. Building a geopandas GeoSeries per call
        to reproject two points costs roughly a millisecond, which is minutes of
        wall clock for one slice. A cached `pyproj.Transformer` built once in
        __init__ does the same conversion in microseconds. The STRtree then makes the
        land lookup an indexed query instead of a scan over every polygon in Europe;
        without both, this check does not finish.
        """
        from shapely.geometry import LineString

        xa, ya = self._to_metric.transform(lon_a, lat_a)
        xb, yb = self._to_metric.transform(lon_b, lat_b)
        segment = LineString([(xa, ya), (xb, yb)])
        for idx in self.tree.query(segment):
            if self.geometries[idx].intersects(segment):
                return True
        return False


@dataclass
class PlausibilityStats:
    """What the checker saw. Counted, so a threshold change is visible as a number."""

    #: Every non-first report. Includes those skipped below the interval floor.
    pairs_seen: int = 0
    #: Pairs that cleared the interval floor and had a distance computed. This is the
    #: population the finding counts are drawn from, and the one INVENTORY.md quotes.
    pairs_evaluated: int = 0
    pairs_skipped_first_report: int = 0
    pairs_below_min_interval: int = 0
    pairs_below_min_jump: int = 0
    zero_interval_pairs: int = 0
    findings_by_kind: dict[str, int] = field(default_factory=dict)
    land_check_available: bool = False
    land_check_unavailable_reason: str = "no coastline configured"

    def note(self, kind: str) -> None:
        self.findings_by_kind[kind] = self.findings_by_kind.get(kind, 0) + 1

    def summary(self) -> str:
        land = ("available" if self.land_check_available
                else f"UNAVAILABLE — {self.land_check_unavailable_reason}")
        lines = [
            f"pairs seen                   : {self.pairs_seen:,}",
            f"pairs evaluated              : {self.pairs_evaluated:,}",
            f"  first report of a vessel   : {self.pairs_skipped_first_report:,}",
            f"  below min sampling interval: {self.pairs_below_min_interval:,}",
            f"  below min jump             : {self.pairs_below_min_jump:,}",
            f"  same-timestamp pairs       : {self.zero_interval_pairs:,}",
            f"land check                   : {land}",
            f"findings                     : {self.findings_by_kind or 'none'}",
        ]
        return "\n".join(lines)


class PlausibilityChecker:
    """
    Streaming self-consistency check on a claimed track. One report of memory.

    WHY STREAMING AND NOT BATCH: it holds one anchor report per MMSI, so it works
    unchanged on `NmeaLiveSource`. T2 can flag a bad position fix as it arrives,
    before anything downstream has associated on it. A batch trajectory analysis
    cannot do that, which is why this lives here and not in `ais_trajectory.py`.

    THE ANCHOR RULE, and why it is not "compare consecutive reports".
    Reports arrive as often as every second, and over one second a consumer GPS's own
    scatter (tens of metres) exceeds the vessel's actual movement. Comparing
    consecutive reports therefore measures the receiver. But simply DISCARDING short
    intervals would mean a vessel reporting at 1 Hz is never checked at all.

    So the anchor is only advanced when a pair is actually evaluated. A report closer
    than `min_interval_s` to the anchor is skipped WITHOUT moving the anchor, so the
    next one is compared against a base that is now far enough back. A 1 Hz vessel is
    checked roughly every ten seconds instead of never.

    Measured on the Fehmarn day: 431,430 pairs, of which 151,998 fall inside the
    minimum interval and 278,934 are evaluated. Comparing consecutive reports with no
    interval floor yields 705 "impossible speeds"; 564 of them are over dt <= 2 s with
    a median jump of 57 m, which is a GPS fix, not a ship. The floor is the difference
    between an operator seeing 705 alerts that are 96% noise and seeing 41.

    NOT A SPOOF DETECTOR. Every finding is a claim contradicting itself, and the
    overwhelmingly most likely cause is a cheap transponder — 26 of the 41 findings on
    the Fehmarn day are on vessels claiming ship type "Sailing", all Class B. Lane C
    may promote a finding to `SpoofSubtype="kinematic"` only with corroboration.
    Calling one of these a spoof, about a real hull, would be defamatory.
    """

    def __init__(
        self,
        *,
        max_speed_kn: float = DEFAULT_MAX_SPEED_KN,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        min_jump_m: float = DEFAULT_MIN_JUMP_M,
        teleport_jump_m: float = DEFAULT_TELEPORT_JUMP_M,
        zero_interval_jump_m: float = DEFAULT_ZERO_INTERVAL_JUMP_M,
        land_mask: LandMask | None = None,
        land_max_interval_s: float = 60.0,
    ) -> None:
        self.max_speed_kn = max_speed_kn
        self.min_interval_s = min_interval_s
        self.min_jump_m = min_jump_m
        self.teleport_jump_m = teleport_jump_m
        self.zero_interval_jump_m = zero_interval_jump_m
        self.land_mask = land_mask
        #: Beyond this interval the straight line between two reports is NOT the path
        #: the vessel took, so an intersection with land says nothing. The straight
        #: segment across a three-hour silence crosses Lolland for every vessel that
        #: rounded it. Longer intervals are simply not land-checked, and that
        #: restriction is why the land finding means something when it does fire.
        self.land_max_interval_s = land_max_interval_s

        self._anchors: dict[str, AisTrack] = {}
        self._geod: Any = None
        self.stats = PlausibilityStats()
        if land_mask is not None:
            self.stats.land_check_available = True
            self.stats.land_check_unavailable_reason = ""

    @property
    def geod(self) -> Any:
        """
        `pyproj.Geod` on WGS84. LIBRARIES.md names this explicitly as the one thing to
        use for point-to-point range and bearing, and forbids hand-rolling haversine.
        Imported lazily so reading a CSV never needs the geo stack.
        """
        if self._geod is None:
            from pyproj import Geod
            self._geod = Geod(ellps="WGS84")
        return self._geod

    def _distance_m(self, a: AisTrack, b: AisTrack) -> float:
        _, _, distance = self.geod.inv(
            a.claimed_lon_deg, a.claimed_lat_deg,
            b.claimed_lon_deg, b.claimed_lat_deg)
        return abs(float(distance))

    def _finding(self, kind: ImplausibilityKind, anchor: AisTrack, track: AisTrack,
                 dt_s: float, distance_m: float, implied_kn: float | str,
                 claimed_value: float | str, claimed_field: str,
                 severity: Severity, significance: float,
                 unit: str = "kn") -> PlausibilityMismatch:
        digest = hashlib.sha1(
            f"{kind}|{track.track_id}|{track.report_time_utc.isoformat()}".encode()
        ).hexdigest()
        return PlausibilityMismatch(
            mismatch_id=f"PIM-{digest[:8]}",
            track_id=track.track_id,
            claimed_mmsi=track.claimed_mmsi,
            kind=kind,
            claimed_value=claimed_value,
            claimed_field=claimed_field,
            implied_value=implied_kn,
            implied_field="implied_speed_kn_from_consecutive_claimed_positions",
            unit=unit,
            delta=(implied_kn - claimed_value
                   if isinstance(implied_kn, float) and isinstance(claimed_value, float)
                   else None),
            tolerance=self.max_speed_kn,
            significance=significance,
            severity=severity,
            t_start_utc=anchor.report_time_utc,
            t_end_utc=track.report_time_utc,
            dt_s=dt_s,
            from_lat_deg=anchor.claimed_lat_deg,
            from_lon_deg=anchor.claimed_lon_deg,
            to_lat_deg=track.claimed_lat_deg,
            to_lon_deg=track.claimed_lon_deg,
            distance_m=distance_m,
            detector={
                "max_speed_kn": self.max_speed_kn,
                "min_interval_s": self.min_interval_s,
                "min_jump_m": self.min_jump_m,
                "teleport_jump_m": self.teleport_jump_m,
                "zero_interval_jump_m": self.zero_interval_jump_m,
                "land_erosion_m": (self.land_mask.erosion_m
                                   if self.land_mask else None),
                "coastline": (str(self.land_mask.path.name)
                              if self.land_mask else None),
            },
        )

    def check(self, track: AisTrack) -> list[PlausibilityMismatch]:
        """Check one report against this vessel's anchor. Returns 0..n findings."""
        mmsi = track.claimed_mmsi
        anchor = self._anchors.get(mmsi)
        if anchor is None:
            self._anchors[mmsi] = track
            self.stats.pairs_skipped_first_report += 1
            return []

        self.stats.pairs_seen += 1
        dt_s = (track.report_time_utc - anchor.report_time_utc).total_seconds()

        # --- same timestamp (or out of order) -------------------------------------
        # A hull cannot be in two places at once. Most of these are two basestations
        # decoding one transmission to coordinates that differ by rounding — 583 of
        # 956 such pairs on the Fehmarn day differ by under 5 m. The tail is not
        # rounding, and the leading explanation for it is TWO VESSELS SHARING ONE
        # MMSI, which is the identity-spoof case lane A already flagged as a known
        # limitation of track_id. The anchor is NOT advanced: the earlier report
        # stays the base so the vessel's real motion is still measured afterwards.
        if dt_s <= 0:
            self.stats.zero_interval_pairs += 1
            distance_m = self._distance_m(anchor, track)
            if distance_m <= self.zero_interval_jump_m:
                return []
            self.stats.note("zero_interval_jump")
            return [self._finding(
                "zero_interval_jump", anchor, track, dt_s, distance_m,
                implied_kn="instantaneous",
                claimed_value=track.claimed_sog_kn if track.claimed_sog_kn is not None
                else "not claimed",
                claimed_field="claimed_sog_kn",
                severity="major",
                significance=distance_m / max(self.zero_interval_jump_m, 1.0),
                unit="m")]

        # --- below the noise floor: skip WITHOUT advancing the anchor --------------
        if dt_s < self.min_interval_s:
            self.stats.pairs_below_min_interval += 1
            return []

        self.stats.pairs_evaluated += 1
        distance_m = self._distance_m(anchor, track)
        findings: list[PlausibilityMismatch] = []

        if distance_m < self.min_jump_m:
            self.stats.pairs_below_min_jump += 1
        else:
            implied_kn = (distance_m / dt_s) / 0.514444
            if implied_kn > self.max_speed_kn:
                kind: ImplausibilityKind = (
                    "teleport" if distance_m > self.teleport_jump_m
                    else "impossible_speed")
                self.stats.note(kind)
                findings.append(self._finding(
                    kind, anchor, track, dt_s, distance_m, implied_kn,
                    claimed_value=(track.claimed_sog_kn
                                   if track.claimed_sog_kn is not None
                                   else "not claimed"),
                    claimed_field="claimed_sog_kn",
                    severity="critical" if kind == "teleport" else "major",
                    significance=implied_kn / max(self.max_speed_kn, 1.0)))

        # --- land crossing --------------------------------------------------------
        # Only over a short interval, where the straight segment approximates the
        # actual path. See land_max_interval_s.
        if self.land_mask is not None and dt_s <= self.land_max_interval_s:
            if self.land_mask.crosses(
                    anchor.claimed_lat_deg, anchor.claimed_lon_deg,
                    track.claimed_lat_deg, track.claimed_lon_deg):
                self.stats.note("crosses_land")
                findings.append(self._finding(
                    "crosses_land", anchor, track, dt_s, distance_m,
                    implied_kn=(distance_m / dt_s) / 0.514444,
                    claimed_value="at sea",
                    claimed_field="claimed_lat_deg/claimed_lon_deg",
                    severity="critical", significance=1.0, unit="none"))

        self._anchors[mmsi] = track
        return findings

    def check_all(self, tracks: Iterator[AisTrack]) -> list[PlausibilityMismatch]:
        """
        Run over a whole source.

        Anchors are keyed by MMSI, so vessels may be INTERLEAVED — a globally
        time-ordered stream like `DmaCsvSource` (or a live feed) works unchanged, and
        no sort is needed. The one real requirement is that reports for a GIVEN
        vessel arrive in time order; out-of-order arrivals land in the dt <= 0 branch
        and are reported as zero-interval pairs rather than silently inverting a
        speed.
        """
        out: list[PlausibilityMismatch] = []
        for track in tracks:
            out.extend(self.check(track))
        return out


def anonymise_mmsi(mmsi: str, order: Sequence[str]) -> str:
    """
    Real MMSI -> a recognisably synthetic one, for anything a judge or a camera sees.

    MID 999 is unassigned to any country, so a 999-prefixed MMSI is obviously fake to
    anyone who reads AIS — chosen over a hash, because a hashed MMSI still LOOKS like
    a real MMSI and someone will eventually paste one into a vessel database. Same
    scheme as `02_data/make_golden_window.py`.

    Required here and not optional: these findings are the closest thing in the
    pipeline to an accusation, and every one of them is far more likely to be a cheap
    transponder than a deception. Naming a real hull beside the words "impossible" or
    "teleport" is exactly the defamation CLAUDE.md forbids.
    """
    return f"999{list(order).index(mmsi) + 1:06d}"


def plausibility_report(csv_path: str | Path, *, coastline: str | Path | None = None,
                        bbox: tuple[float, float, float, float] | None = None,
                        examples: int = 3) -> None:
    """
    Run the plausibility check over a slice and print the counts, the threshold
    sensitivity, and a few anonymised examples.

    A real report generator, not a smoke test — the numbers it prints go into
    02_data/INVENTORY.md and onto a slide, which is why every count is printed beside
    the thresholds that produced it.
    """
    source = DmaCsvSource(csv_path)
    tracks = list(source.tracks())
    order = sorted({t.claimed_mmsi for t in tracks})
    print(source.stats.summary())

    mask = None
    if coastline is not None:
        mask = LandMask(coastline, bbox=bbox)
        print(f"\ncoastline: {Path(coastline).name}, {mask.n_polygons} land polygons "
              f"after {mask.erosion_m:.0f} m erosion")
    else:
        print("\ncoastline: NONE CONFIGURED — the land check will not run, and its "
              "absence is reported rather than silently skipped.")

    checker = PlausibilityChecker(land_mask=mask)
    findings = checker.check_all(iter(tracks))
    print()
    print(checker.stats.summary())
    print(f"\nTOTAL FINDINGS: {len(findings):,} across "
          f"{len({f.claimed_mmsi for f in findings}):,} vessels")

    print("\n--- MINIMUM-INTERVAL SENSITIVITY ---")
    print("The single parameter that decides whether this tool is useful. Below the "
          "floor, GPS scatter dominates real movement.")
    print(f"{'min_interval_s':>15} {'findings':>10} {'vessels':>9}")
    for floor in (0.0, 2.0, 5.0, 10.0, 30.0, 60.0):
        probe = PlausibilityChecker(min_interval_s=floor)
        got = probe.check_all(iter(tracks))
        print(f"{floor:>15.0f} {len(got):>10,} "
              f"{len({f.claimed_mmsi for f in got}):>9,}")

    print(f"\n--- {examples} EXAMPLES (MMSI ANONYMISED) ---")
    for finding in sorted(findings, key=lambda f: -(f.distance_m or 0))[:examples]:
        print(f"\n[{finding.kind}] {finding.severity} | "
              f"MMSI {anonymise_mmsi(finding.claimed_mmsi, order)} | "
              f"{finding.mismatch_id}")
        print(f"  {finding.t_start_utc}  ({finding.from_lat_deg:.5f}, "
              f"{finding.from_lon_deg:.5f})")
        print(f"  {finding.t_end_utc}  ({finding.to_lat_deg:.5f}, "
              f"{finding.to_lon_deg:.5f})")
        print(f"  {finding.distance_m:,.0f} m in {finding.dt_s:.0f} s -> implied "
              f"{finding.implied_value} vs claimed {finding.claimed_value} "
              f"{finding.unit}")
    print("\nEvery finding above is a CLAIM CONTRADICTING ITSELF, not a spoof. The "
          "leading explanation is a cheap Class B transponder. Lane C may promote "
          "one to SpoofSubtype='kinematic' only with corroborating evidence.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("usage: python3 03_src/ais_ingest.py <slice.csv> [coastline.shp]")
    plausibility_report(sys.argv[1],
                        coastline=sys.argv[2] if len(sys.argv) > 2 else None,
                        bbox=(54.40, 54.80, 11.00, 11.80))
