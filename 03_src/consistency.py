"""
consistency.py — does a matched pair actually AGREE? Lane C. Criterion 3.

WHAT THIS MODULE DECIDES, AND WHAT IT DELIBERATELY DOES NOT
association.py decided WHICH observation goes with WHICH claim. It never asked whether
the pairing was honest, on purpose: a vessel broadcasting a false identity is still
physically where AIS says it is, so it associates perfectly. If association had rejected
pairs that "looked wrong", the spoof would have been thrown away before anyone could
compare it.

This module asks the next question, one dimension at a time: the claim says 180 m, the
camera says 34 m — how many sigmas apart is that, and is there any innocent explanation?

It emits Mismatch objects. It does NOT emit a label. Whether a pile of mismatches means
SPOOF, or means the camera is having a bad day, is verdict.py's decision. Keeping the
two apart matters: a module that both measures disagreement and decides guilt can be
tuned, unconsciously, until the disagreements it measures are the ones that produce the
verdicts it wants.

THE RULE THAT GOVERNS EVERY CHECK IN THIS FILE
    Compare in SIGMAS, never in raw units.
A 200 m position gap at 8 km with a 2-degree pose error is noise. The same 200 m at
800 m is a lie. A tool that thresholds on metres will do both wrong, and it will do them
wrong quietly. Every check here divides the delta by the combined uncertainty of both
sides before it decides anything.

THE SECOND RULE
    Silence is a valid, and frequently correct, output.
Most of the logic below is about NOT producing a mismatch: the claim was never
broadcast, the class is "unknown", the contact is a single frame so it has no heading,
the position delta is exactly what staleness predicts. Each of those is a reason to say
nothing. Criterion 4 is not decoration on the end of the pipeline — it is most of the
code in this file.

NO AI IN THIS MODULE. Every number below comes from arithmetic on two measurements and
a stated uncertainty. That is deliberate: this is the module whose output ends up in a
case file, and "the claimed length exceeds the observed length by 24 sigma, at these
tolerances, with this ruleset version" is defensible in a way that a model score is not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from contracts import (
    AisTrack,
    Association,
    EoContact,
    Mismatch,
    MismatchDimension,
    Severity,
)

RULESET_VERSION = "consistency/0.1.0"

KN_TO_MS = 0.514444

# Visually confusable class pairs. Symmetric.
#
# WHY THIS EXISTS: a camera that reports "cargo" over a claimed tanker has not caught a
# spoofer, it has looked at two boxes with a superstructure aft from three kilometres
# away. Treating that as evidence would bury a watch officer in false identity
# mismatches on the most common vessel types in the Baltic. A tug reported over a
# claimed tanker is a different matter entirely — nothing about those two silhouettes
# is alike, so the same delta means far more.
CONFUSABLE_CLASSES: set[frozenset[str]] = {
    frozenset({"cargo", "tanker"}),
    frozenset({"tug", "small_craft"}),
    frozenset({"fishing", "small_craft"}),
    frozenset({"tug", "fishing"}),
    frozenset({"passenger", "cargo"}),
}


@dataclass(frozen=True)
class Tolerances:
    """
    Every threshold in one inspectable place, carried into the Mismatch so a report can
    print the number it was judged against. A "mismatch" without its tolerance is an
    unexplained assertion, which is the opposite of evidence-grade.
    """

    # Report nothing below this. Below ~2 sigma, disagreement is indistinguishable from
    # measurement error and reporting it manufactures noise the operator must triage.
    min_report_sigma: float = 2.0

    # Severity bands, in sigmas.
    major_sigma: float = 4.0
    critical_sigma: float = 8.0

    # AIS quantisation. A claim is an assertion, not a measurement, so it has no error
    # bar of its own — but it IS quantised, and pretending otherwise would divide by an
    # uncertainty that is too small and inflate every significance.
    claim_length_quantum_m: float = 1.0
    claim_heading_quantum_deg: float = 1.0
    claim_sog_quantum_ms: float = 0.05

    # Floors. Without these, a contact that reports an implausibly small uncertainty
    # turns every trivial delta into a hundred-sigma "critical" finding. The floor is
    # the honest statement that no monocular estimate is better than this.
    min_length_sigma_m: float = 3.0
    min_heading_sigma_deg: float = 8.0
    min_speed_sigma_ms: float = 0.8
    min_position_sigma_m: float = 25.0
    # Floor on the cross-range test. No pose is better than this indoors.
    min_bearing_sigma_deg: float = 1.0

    # Staleness. A position delta consistent with (time_delta * speed) is explained by
    # the AIS report being old, and must be flagged as such before it reaches a verdict.
    # 1.5 is slack for the vessel having manoeuvred rather than run straight.
    staleness_slack: float = 1.5

    # A class disagreement between visually confusable types is halved in significance.
    confusable_class_discount: float = 0.5

    # Below this class confidence, the observation cannot support any class mismatch.
    min_class_confidence: float = 0.60

    # At or above this confidence, a NON-confusable class disagreement is promoted to
    # major severity on domain grounds. See check_class.
    class_major_confidence: float = 0.85


DEFAULT_TOLERANCES = Tolerances()


# ============================================================================
# Small helpers.
# ============================================================================

try:
    # Lane B owns the angle convention. One definition, project-wide: a second copy of
    # this three-line function is how 359-vs-1 becomes -358 in one module and +2 in
    # another, and that discrepancy would surface as a spurious heading mismatch.
    from geometry import signed_delta_deg as shortest_arc_deg
except Exception:  # pragma: no cover - geometry.py pulls pyproj; keep this importable
    def shortest_arc_deg(a: float, b: float) -> float:
        """Fallback. Must stay identical to geometry.signed_delta_deg."""
        return (a - b + 180.0) % 360.0 - 180.0


def _severity(sig: float, tol: Tolerances) -> Severity:
    if sig >= tol.critical_sigma:
        return "critical"
    if sig >= tol.major_sigma:
        return "major"
    return "minor"


def _probability_to_sigma(p: float) -> float:
    """
    Turn a categorical confidence into a comparable sigma scale.

    WHY A CONVERSION IS NEEDED AT ALL. Every other dimension here is continuous and
    yields a natural "how many sigmas". Class is a label, so it has none — and the
    verdict and the ranking downstream compare dimensions against each other. A
    dimension on its own private scale would silently dominate or silently vanish.

    THE FIRST VERSION OF THIS FUNCTION WAS WRONG, AND THE HARNESS CAUGHT IT.
    It used the normal quantile, which maps 0.92 confidence to 1.41 sigma — below the
    2.0 reporting threshold. A class mismatch was therefore MATHEMATICALLY UNREACHABLE
    at any confidence a real classifier emits: the check ran on every pair, cost time,
    and could never once fire. That is exactly the class of dead logic that looks like
    diligence and is actually nothing, and it is only visible because the D0 harness
    injects a known class spoof and checks whether it comes back.

    The replacement is LOG-ODDS, which is the natural scale for evidence: ln(p/(1-p))
    is how much a belief has moved, in nats, and it is what a Bayesian update actually
    adds. 0.90 -> 2.20, 0.95 -> 2.94, 0.99 -> 4.60. Still conservative — a class
    disagreement will not reach the critical band on its own, which is correct, because
    silhouette classification remains the weakest evidence in the system.
    """
    p = min(max(p, 0.5), 0.999)
    return round(math.log(p / (1.0 - p)), 4)


def _observation_confidence(contact: EoContact) -> float:
    """
    How much this observation can be trusted at all, 0-1. Carried onto every Mismatch.

    Three multiplicative factors, because they compound rather than trade off:
      * track length — a single-frame contact could be a wave crest.
      * range quality — a range with 60% fractional error supports very little.
      * class confidence, when a class was reported.
    """
    frames = min(contact.track_length_frames, 10) / 10.0
    track_factor = 0.35 + 0.65 * frames

    range_factor = 1.0
    if contact.observed_range_m and contact.range_uncertainty_m:
        frac = contact.range_uncertainty_m / max(contact.observed_range_m, 1.0)
        range_factor = max(0.3, 1.0 - min(frac, 0.7))

    class_factor = contact.observed_class_confidence or 1.0
    class_factor = 0.6 + 0.4 * class_factor

    return round(min(1.0, track_factor * range_factor * class_factor), 4)


def _mid(assoc: Association, dimension: str) -> str:
    return f"m-{assoc.association_id}-{dimension}"


# ============================================================================
# The individual checks. Each returns a Mismatch or None.
#
# Every one of them returns None more often than it returns a Mismatch, and the
# conditions under which it does are the honest-limits half of criterion 4.
# ============================================================================

def check_length(
    assoc: Association, track: AisTrack, contact: EoContact, tol: Tolerances
) -> Mismatch | None:
    """
    Claimed length against observed length.

    THIS IS THE STRONGEST SPOOF SIGNAL IN THE SYSTEM and it is worth understanding why.
    A vessel can lie about its name, its MMSI, its type and its destination for free —
    those are just characters in a message. It cannot lie about being 180 m long while
    being 34 m long, because the hull is in the frame. Identity is cheap to forge;
    physical extent is not.

    THE CIRCULARITY TRAP, stated because it is easy to fall into: observed_length_m must
    be derived from pixel extent multiplied by an INDEPENDENTLY MEASURED RANGE — from
    waterline depression angle, or a rangefinder. If range were instead derived from
    apparent size and an ASSUMED length, then observed_length collapses to the assumed
    length, this check compares a number to itself, and it can never fire. Lane B owns
    that guarantee; this module cannot verify it and has to trust it.
    """
    claimed = track.claimed_length_m
    observed = contact.observed_length_m
    if claimed is None or observed is None:
        return None          # no claim, or no measurement. Not a finding, an absence.
    if claimed <= 0.0:
        return None          # AIS encodes "not available" as zero on this field.

    obs_sigma = max(contact.observed_length_uncertainty_m or 0.0, tol.min_length_sigma_m)
    sigma = math.sqrt(obs_sigma ** 2 + tol.claim_length_quantum_m ** 2)
    delta = observed - claimed
    sig = abs(delta) / sigma
    if sig < tol.min_report_sigma:
        return None

    severity = _severity(sig, tol)
    # Domain override: an order-of-magnitude disagreement is critical regardless of what
    # the error bars say, because at that point the error bars are the thing in doubt.
    ratio = max(claimed, observed) / max(min(claimed, observed), 1.0)
    if ratio >= 3.0:
        severity = "critical"

    return Mismatch(
        mismatch_id=_mid(assoc, "length"),
        association_id=assoc.association_id,
        dimension="length",
        claimed_value=claimed,
        claimed_field="AisTrack.claimed_length_m (AIS msg 5 Length / Size A+B)",
        observed_value=round(observed, 1),
        observed_field="EoContact.observed_length_m (pixel extent x independent range)",
        unit="m",
        delta=round(delta, 1),
        tolerance=round(tol.min_report_sigma * sigma, 1),
        significance=round(sig, 2),
        explained_by_staleness=False,   # length does not change with time.
        severity=severity,
        observation_confidence=_observation_confidence(contact),
    )


def check_class(
    assoc: Association, track: AisTrack, contact: EoContact, tol: Tolerances
) -> Mismatch | None:
    """
    Claimed ship type against observed silhouette class.

    THREE SUPPRESSIONS, each of which exists because of a specific way this check goes
    wrong in the real data:

    1. claimed_ship_type of "unknown" is NOT a disagreement. Lane A maps 29.6% of the
       measured rows — pilot boats, SAR, dredgers, port tenders, law enforcement — onto
       "unknown" because they have no honest silhouette equivalent. Mapping them by
       resemblance instead would manufacture SPOOF verdicts against real named vessels
       inside the ingest layer, invisible to the report. Comparing against "unknown"
       here would reintroduce exactly that, one layer later.

    2. A low-confidence class cannot raise a mismatch at all. contracts.py says so in
       the field description and it is right: the weakest evidence in the system must
       not be able to trigger the strongest conclusion.

    3. Visually confusable pairs are discounted, not ignored. See CONFUSABLE_CLASSES.
    """
    claimed = track.claimed_ship_type
    observed = contact.observed_class
    conf = contact.observed_class_confidence

    if claimed is None or observed is None:
        return None
    if claimed == "unknown" or observed == "unknown":
        return None
    if claimed == observed:
        return None
    if conf is None or conf < tol.min_class_confidence:
        return None

    confusable = frozenset({claimed, observed}) in CONFUSABLE_CLASSES
    sig = _probability_to_sigma(conf)
    if confusable:
        sig *= tol.confusable_class_discount
    if sig < tol.min_report_sigma:
        return None

    # SEVERITY IS A DOMAIN JUDGEMENT, SIGNIFICANCE IS A STATISTIC, and for a categorical
    # dimension they must be set separately. Log-odds will essentially never carry a
    # class disagreement past 4 sigma, so a purely statistical severity would cap every
    # class finding at "minor" and it could never contribute to a verdict.
    #
    # But "the camera is 90% sure that is a tug, and AIS claims a 250 m tanker" is not a
    # subtle finding. Two silhouettes with nothing in common, at high confidence, is a
    # major finding whatever the sigma arithmetic says. Confusable pairs never get the
    # promotion — a tanker read as cargo stays minor no matter how confident the model.
    severity = _severity(sig, tol)
    if not confusable and conf >= tol.class_major_confidence:
        severity = "major"

    return Mismatch(
        mismatch_id=_mid(assoc, "class"),
        association_id=assoc.association_id,
        dimension="class",
        claimed_value=claimed,
        claimed_field="AisTrack.claimed_ship_type (AIS msg 5 Ship type)",
        observed_value=observed,
        observed_field="EoContact.observed_class (silhouette)",
        unit="class_label",
        delta=None,                      # categorical. contracts.py: None, not zero.
        tolerance=round(tol.min_report_sigma, 2),
        significance=round(sig, 2),
        explained_by_staleness=False,
        severity=severity,
        observation_confidence=_observation_confidence(contact),
    )


def check_heading(
    assoc: Association, track: AisTrack, contact: EoContact, tol: Tolerances
) -> Mismatch | None:
    """
    Claimed heading against observed hull axis.

    HEADING, NOT COURSE. A vessel can point one way and travel another — a ship holding
    station against a current is the obvious case, and it is exactly the behaviour a
    loitering vessel over a cable exhibits. contracts.py keeps the two fields apart for
    this reason. If claimed_heading is absent we fall back to COG and SAY SO in the
    provenance string, because a reader must be able to tell which comparison was made.

    Requires a multi-frame contact. A single frame has an apparent hull axis but no way
    to tell bow from stern, which makes a 180-degree error equally likely as a match.
    """
    if contact.track_length_frames < 2:
        return None
    observed = contact.observed_heading_deg_true
    if observed is None:
        return None

    claimed = track.claimed_heading_deg_true
    field = "AisTrack.claimed_heading_deg_true (AIS Heading)"
    if claimed is None:
        claimed = track.claimed_cog_deg_true
        field = "AisTrack.claimed_cog_deg_true (AIS COG — heading not broadcast)"
    if claimed is None:
        return None

    sigma = max(contact.bearing_uncertainty_deg, tol.min_heading_sigma_deg)
    sigma = math.sqrt(sigma ** 2 + tol.claim_heading_quantum_deg ** 2)
    delta = shortest_arc_deg(observed, claimed)
    sig = abs(delta) / sigma
    if sig < tol.min_report_sigma:
        return None

    # A near-180 disagreement is the classic bow/stern ambiguity, not a spoof. Cap its
    # severity and let the report say which it was, rather than calling it critical.
    ambiguous_reversal = abs(abs(delta) - 180.0) < 25.0
    severity = "minor" if ambiguous_reversal else _severity(sig, tol)

    return Mismatch(
        mismatch_id=_mid(assoc, "heading"),
        association_id=assoc.association_id,
        dimension="heading",
        claimed_value=round(claimed, 1),
        claimed_field=field,
        observed_value=round(observed, 1),
        observed_field="EoContact.observed_heading_deg_true (hull axis in frame)"
                       + (" — NOTE: near-180 disagreement, bow/stern ambiguity is the "
                          "likelier explanation" if ambiguous_reversal else ""),
        unit="deg_true",
        delta=round(delta, 1),
        tolerance=round(tol.min_report_sigma * sigma, 1),
        significance=round(sig, 2),
        explained_by_staleness=False,
        severity=severity,
        observation_confidence=_observation_confidence(contact),
    )


def check_speed(
    assoc: Association, track: AisTrack, contact: EoContact, tol: Tolerances
) -> Mismatch | None:
    """Claimed SOG against observed speed. Knots in, metres per second internally."""
    if contact.track_length_frames < 2:
        return None
    observed = contact.observed_speed_ms
    if observed is None or track.claimed_sog_kn is None:
        return None

    claimed_ms = track.claimed_sog_kn * KN_TO_MS
    sigma = max(tol.min_speed_sigma_ms, 0.25 * max(claimed_ms, observed))
    sigma = math.sqrt(sigma ** 2 + tol.claim_sog_quantum_ms ** 2)
    delta = observed - claimed_ms
    sig = abs(delta) / sigma
    if sig < tol.min_report_sigma:
        return None

    return Mismatch(
        mismatch_id=_mid(assoc, "speed"),
        association_id=assoc.association_id,
        dimension="speed",
        claimed_value=round(claimed_ms, 2),
        claimed_field="AisTrack.claimed_sog_kn (converted to m/s)",
        observed_value=round(observed, 2),
        observed_field="EoContact.observed_speed_ms (frame-to-frame displacement)",
        unit="m_s",
        delta=round(delta, 2),
        tolerance=round(tol.min_report_sigma * sigma, 2),
        significance=round(sig, 2),
        explained_by_staleness=False,
        severity=_severity(sig, tol),
        observation_confidence=_observation_confidence(contact),
    )


def great_circle_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Initial bearing from 1 to 2, degrees true.

    Spherical, not ellipsoidal, and deliberately so. Over the 10 km a coastal camera can
    see, the difference from a proper geodesic is under 0.02 degrees — two orders of
    magnitude below the 2-5 degree pose uncertainty that dominates every bearing in this
    system. Using pyproj here would add a dependency to buy precision that is invisible
    underneath the error we already carry, and this module stays importable anywhere.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def check_position(
    assoc: Association, track: AisTrack, contact: EoContact, tol: Tolerances,
    camera: tuple[float, float] | None = None,
) -> Mismatch | None:
    """
    Claimed position against where the camera actually saw the hull.

    THE DECOMPOSITION THAT MAKES THIS CHECK WORK AT ALL.
    The first version of this function compared the total spatial gap against the total
    position uncertainty, and the D0 harness proved it could not detect a real position
    spoof. The arithmetic, measured at 5 km:

        cross-range (bearing) sigma : 2.5 deg x 5000 m  =  218 m
        along-range (range) sigma   : 25% of 5000 m     = 1250 m
        combined                    :                     1269 m

        a 1000 m injected spoof -> 0.79 sigma combined  (invisible)
                                -> 4.58 sigma cross-range only (obvious)

    A monocular camera measures DIRECTION well and DISTANCE badly. Those two errors
    differ by a factor of six, so pooling them into one isotropic sigma throws away the
    good measurement to average it with the bad one. The signal was never weak; it was
    being hidden by the wrong error model.

    So this check now tests the CROSS-RANGE component only: the predicted bearing of the
    claim against the observed bearing of the hull, against the bearing uncertainty.

    WHAT THAT HONESTLY GIVES UP, and it belongs on the limitations slide: a vessel that
    displaces its claim ALONG the line of sight — directly toward or away from the
    camera — is not detectable by a single monocular sensor, at any offset, ever. The
    geometry forbids it. Detecting that needs a second camera on a different baseline,
    a radar, or the vessel to move. Saying so is worth more than a check that pretends
    otherwise.

    Falls back to the pooled test when no camera position is supplied, because a
    bearing-only comparison is impossible without knowing where the camera stood.
    """
    gap = assoc.spatial_gap_m
    if gap is None:
        return None

    stale = False
    if assoc.time_delta_s is not None and track.claimed_sog_kn is not None:
        could_have_travelled = (
            abs(assoc.time_delta_s) * track.claimed_sog_kn * KN_TO_MS * tol.staleness_slack
        )
        stale = gap <= could_have_travelled

    if camera is not None:
        # --- cross-range test: the measurement the sensor is actually good at --------
        claimed_brg = great_circle_bearing_deg(
            camera[0], camera[1], track.claimed_lat_deg, track.claimed_lon_deg)
        d_brg = shortest_arc_deg(contact.observed_bearing_deg_true, claimed_brg)
        sigma_deg = max(contact.bearing_uncertainty_deg, tol.min_bearing_sigma_deg)
        sig = abs(d_brg) / sigma_deg
        if sig < tol.min_report_sigma:
            return None

        # Report in metres as well as degrees: an operator thinks in distance, and the
        # cross-range metres are what a patrol boat would actually have to cover.
        rng = contact.observed_range_m
        cross_m = abs(math.radians(d_brg)) * rng if rng else None
        detail = (f"cross-range separation {cross_m:.0f} m" if cross_m
                  else "cross-range, range unknown")
        return Mismatch(
            mismatch_id=_mid(assoc, "position"),
            association_id=assoc.association_id,
            dimension="position",
            claimed_value=round(claimed_brg, 2),
            claimed_field="bearing to AisTrack.claimed_lat/lon_deg (dead-reckoned) "
                          "from camera_pose_ref",
            observed_value=round(contact.observed_bearing_deg_true, 2),
            observed_field=f"EoContact.observed_bearing_deg_true — {detail}. "
                           f"ALONG-range displacement is not testable with one "
                           f"monocular sensor and is NOT included in this figure.",
            unit="deg_true",
            delta=round(d_brg, 2),
            tolerance=round(tol.min_report_sigma * sigma_deg, 2),
            significance=round(sig, 2),
            explained_by_staleness=stale,
            severity="minor" if stale else _severity(sig, tol),
            observation_confidence=_observation_confidence(contact),
        )

    # --- fallback: pooled test. Weak, and labelled weak. -----------------------------
    rng = contact.observed_range_m
    lateral = (math.radians(contact.bearing_uncertainty_deg) * rng) if rng else None
    parts = [x for x in (lateral, contact.range_uncertainty_m) if x is not None]
    sigma = math.sqrt(sum(x * x for x in parts)) if parts else tol.min_position_sigma_m
    sigma = max(sigma, tol.min_position_sigma_m)
    sig = gap / sigma
    if sig < tol.min_report_sigma:
        return None
    return Mismatch(
        mismatch_id=_mid(assoc, "position"),
        association_id=assoc.association_id,
        dimension="position",
        claimed_value=round(gap, 1),
        claimed_field="AisTrack.claimed_lat/lon_deg (dead-reckoned to frame time)",
        observed_value=0.0,
        observed_field="EoContact bearing/range — POOLED test, no camera position "
                       "supplied. Dominated by monocular range error; weak by nature.",
        unit="m",
        delta=round(gap, 1),
        tolerance=round(tol.min_report_sigma * sigma, 1),
        significance=round(sig, 2),
        explained_by_staleness=stale,
        severity="minor" if stale else _severity(sig, tol),
        observation_confidence=_observation_confidence(contact),
    )


# check_position is NOT in this tuple: it takes the camera position and is called
# separately below. Keeping it out means the uniform checks stay uniform.
CHECKS = (check_length, check_class, check_heading, check_speed)


# ============================================================================
# Public entry point.
# ============================================================================

def check_pair(
    assoc: Association,
    track: AisTrack,
    contact: EoContact,
    *,
    tolerances: Tolerances = DEFAULT_TOLERANCES,
    camera: tuple[float, float] | None = None,
) -> list[Mismatch]:
    """
    Run every check on one matched pair. Order is stable so reports are reproducible.

    Returns [] for an honest vessel, which is the common case and not a failure.
    """
    out: list[Mismatch] = []
    for check in CHECKS:
        m = check(assoc, track, contact, tolerances)
        if m is not None:
            out.append(m)
    m = check_position(assoc, track, contact, tolerances, camera)
    if m is not None:
        out.append(m)
    return out


def check_all(
    associations: Sequence[Association],
    tracks_by_id: dict[str, AisTrack],
    contacts_by_id: dict[str, EoContact],
    *,
    tolerances: Tolerances = DEFAULT_TOLERANCES,
    camera: tuple[float, float] | None = None,
) -> dict[str, list[Mismatch]]:
    """
    Every matched association, keyed by association_id.

    Unmatched associations are skipped entirely and produce no key. There is nothing to
    compare when one side is missing — that is a DARK or an unobserved-claim case, and
    it is verdict.py that decides what an absence means. Inventing a Mismatch here to
    represent "nothing was seen" would put a number on a non-observation, which is
    precisely the kind of false precision criterion 4 exists to prevent.
    """
    out: dict[str, list[Mismatch]] = {}
    for assoc in associations:
        if assoc.contact_id is None or assoc.track_id is None:
            continue
        track = tracks_by_id.get(assoc.track_id)
        contact = contacts_by_id.get(assoc.contact_id)
        if track is None or contact is None:
            continue
        out[assoc.association_id] = check_pair(
            assoc, track, contact, tolerances=tolerances, camera=camera)
    return out
