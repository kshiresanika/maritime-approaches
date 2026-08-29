"""Patch consistency.py: restore ConsistencyResult (the three-way outcome), fix the
LLM->observation_confidence bypass, restore class-check inertness, fix the pooled
position literals. Every replacement is anchored on unique surrounding text and the
script ABORTS if any anchor is missing or ambiguous -- a silent partial patch on a
1877-line dependency chain is worse than no patch."""
import io, re, sys, pathlib

p = pathlib.Path(sys.argv[1])
s = p.read_text()
edits = []

def rep(old, new, label):
    edits.append((old, new, label))

# ---------------------------------------------------------------- 1. new tolerance
rep(
"""    # At or above this confidence, a NON-confusable class disagreement is promoted to
    # major severity on domain grounds. See check_class.
    class_major_confidence: float = 0.85
""",
'''    # At or above this confidence, a NON-confusable class disagreement is promoted to
    # major severity on domain grounds. See check_class.
    class_major_confidence: float = 0.85

    # THE CLASS DIMENSION HAS ITS OWN REPORTING FLOOR, AND IT IS NOT COSMETIC.
    #
    # Observed: class significance is LOG-ODDS (see _probability_to_sigma), every other
    # dimension is a true sigma. Claimed: the project guarantee that an UNCALIBRATED
    # vision model is structurally inert -- eo_vlm caps every confidence at 0.95, which
    # under the OLD normal-quantile mapping was 1.645, below the 2.0 floor, so the class
    # check could not fire until agreement was measured on real crops.
    #
    # Why the mismatch matters: swapping to log-odds moved 0.95 to ln(0.95/0.05) = 2.944,
    # ABOVE 2.0. The guarantee died silently, in another lane's file, because lane B and
    # lane C deliberately do not import each other. Measured consequence: a length
    # finding at 3.0 sigma alone gives posterior 0.308 (MATCH); the same finding plus one
    # model class token gives 0.911 (SPOOF) -- past the 0.85 stating threshold. A model
    # was one token away from writing a verdict, which this project forbids outright.
    #
    # 3.5 nats is DERIVED, not chosen, and it restores BOTH broken guarantees at once:
    #   * the uncalibrated ceiling 0.95 -> 2.944 < 3.5           -> inert, as designed
    #   * a confusable pair maxes at ln(0.999/0.001)*0.5 = 3.453 < 3.5
    #                                                            -> can NEVER fire, ever
    #   * firing needs p >= 1/(1+e^-3.5) = 0.9707, i.e. MEASURED agreement, not a default
    # What would change the answer: measuring the model's agreement rate on labelled
    # crops. That is the only thing that should ever move this number.
    class_min_report_sigma: float = 3.5
''', "class_min_report_sigma")

# ------------------------------------------------- 2. the three-way outcome types
rep(
"""DEFAULT_TOLERANCES = Tolerances()
""",
'''DEFAULT_TOLERANCES = Tolerances()


# ============================================================================
# THE THREE-WAY OUTCOME. This is the load-bearing type in the module.
#
# A check has THREE possible results, not two, and collapsing them is the single
# most dangerous simplification available here:
#
#   Mismatch     -- we compared, and they disagree by more than the tolerance.
#   Agreement    -- we compared, and they agree. Positive evidence of honesty.
#   Uncomparable -- we could not compare. NOT evidence of anything.
#
# Why the distinction is not academic: verdict.py caps MATCH confidence by
# EVIDENTIAL COVERAGE = (agreements + mismatches) / (all three). That cap is what
# stops a Class B vessel broadcasting no static block at all from sailing through
# as a high-confidence MATCH -- a Bayesian posterior with no evidence just returns
# the prior, and the prior says 99.8% of vessels are honest.
#
# Return "no mismatch" for both of the last two and that cap reads 1.0 for a
# vessel nothing was ever checked against. The tool then reports high confidence
# in a comparison it never made. That is the exact false precision criterion 4
# exists to prevent, and it fails silently and in the reassuring direction.
# ============================================================================

@dataclass(frozen=True)
class Agreement:
    """A comparison that RAN and came back consistent."""
    dimension: MismatchDimension
    # Carries the numbers, because "the length agreed" without the two values is an
    # assertion. Also the channel by which a CATEGORICAL difference that did not reach
    # significance gets annotated -- claimed "fishing" beside observed "tug" with no
    # note at all reads to an operator as the tool having missed it.
    note: str


@dataclass(frozen=True)
class Uncomparable:
    """A comparison that could NOT run, and why."""
    dimension: MismatchDimension
    reason: str
    # Which side was absent. A missing OBSERVATION is a sensor limit; a missing CLAIM
    # is a gap in what the vessel broadcast, and only the second can ever be suspicious.
    claim_missing: bool = False
    # An absence that is itself unusual. Set by check_pair, never by a single check --
    # no one dimension can tell "this vessel omitted a field" from "this vessel omitted
    # the entire static block", and only the second is a weak positive indicator.
    suspicious: bool = False


@dataclass(frozen=True)
class ConsistencyResult:
    """Everything lane C learned about one matched pair. verdict.py's input."""
    association_id: str
    mismatches: list[Mismatch]
    agreements: list[Agreement]
    uncomparable: list[Uncomparable]


CheckOutcome = "Mismatch | Agreement | Uncomparable"

_SEVERITY_ORDER = {"minor": 0, "major": 1, "critical": 2}


def independent_dimension_count(result: ConsistencyResult,
                                min_severity: Severity = "major") -> int:
    """How many INDEPENDENT dimensions disagree at or above min_severity.

    This is the number handoff_C.md says to weight above strongest_significance, and
    the reason is the arithmetic's blind spot: a likelihood ratio is only valid if the
    sensor model behind it is right, and with ONE dimension there is nothing to catch
    an unmodelled systematic. A range estimate 40% low scales apparent length and
    produces a large, confident, wrong length finding at any sigma you like. A second
    independently-failing dimension is what rules that out; more sigma on the first is
    not a substitute.

    heading and cog collapse to one family, matching verdict._posterior_spoof exactly.
    They measure the same physical quantity two ways, so counting both double-weights
    a Class B vessel's single fact. The two collapses MUST stay identical or the count
    that gates a verdict disagrees with the count that computes its confidence.

    Staleness-explained mismatches are excluded here as well as in the posterior --
    a delta a stale report already accounts for is not evidence of anything, and it
    must not be able to buy corroboration it could not buy confidence with.
    """
    floor = _SEVERITY_ORDER[min_severity]
    families: set[str] = set()
    for m in result.mismatches:
        if m.explained_by_staleness:
            continue
        if _SEVERITY_ORDER[m.severity] < floor:
            continue
        families.add("orientation" if m.dimension in ("heading", "cog") else m.dimension)
    return len(families)
''', "outcome types")

# ------------------------------------------- 3. the LLM -> observation_confidence bypass
rep(
"""    class_factor = contact.observed_class_confidence or 1.0
    class_factor = 0.6 + 0.4 * class_factor

    return round(min(1.0, track_factor * range_factor * class_factor), 4)""",
'''    # THE CLASS FACTOR IS CLAMPED, AND THIS IS A SAFETY FIX, NOT A TUNING CHOICE.
    #
    # Observed: observation_confidence is stamped on EVERY Mismatch, including the
    # purely geometric ones. Claimed: no model output can influence a verdict.
    # The mismatch: verdict._usable_mismatches DROPS any mismatch whose
    # observation_confidence is below 0.60. With the old 0.6 + 0.4*conf, a vision model
    # returning confidence 0.05 pulled a clean 9-frame length finding from 0.935 to
    # 0.561 -- under the floor -- and SILENTLY DELETED an independent geometric
    # measurement from the posterior. Measured: conf 0.10 -> 0.598 (dropped),
    # conf 0.30 -> 0.673 (kept). A model could not raise a verdict but it could erase
    # the evidence for one, which is the same violation wearing the other sign.
    #
    # This path was ungated by all three of eo_vlm's guards: it fires on
    # observed_class "unknown", it fires when the class check is suppressed, and it
    # fires at confidence 0.0.
    #
    # Clamped at 0.9 the class factor can still express "this observation is a bit
    # weaker", which is its legitimate job, but it can no longer move a mismatch across
    # an admission threshold on its own: 0.935 * 0.9/0.974 = 0.864, far above 0.60.
    # What would change the answer: giving the class factor its own field so it applies
    # to the class Mismatch alone. That is the better fix and it needs a contract change.
    class_factor = contact.observed_class_confidence or 1.0
    class_factor = max(0.9, 0.6 + 0.4 * class_factor)

    return round(min(1.0, track_factor * range_factor * class_factor), 4)''',
    "observation_confidence clamp")

# --------------------------------------------------------------- 4. check_length
rep(
"""    if claimed is None or observed is None:
        return None          # no claim, or no measurement. Not a finding, an absence.
    if claimed <= 0.0:
        return None          # AIS encodes "not available" as zero on this field.""",
'''    if claimed is None or observed is None:
        # An absence, not a finding -- and NOT an agreement either. Which side is
        # missing changes what it means, so the reason says which.
        return Uncomparable(
            dimension="length",
            reason=("the camera produced no length estimate for this contact"
                    if observed is None
                    else "the AIS static block carried no claimed length"),
            claim_missing=claimed is None)
    if claimed <= 0.0:
        return Uncomparable(
            dimension="length",
            reason="AIS encodes 'length not available' as 0 on this field",
            claim_missing=True)''', "check_length preconditions")

rep(
"""    sig = abs(delta) / sigma
    if sig < tol.min_report_sigma:
        return None

    severity = _severity(sig, tol)
    # Domain override:""",
'''    sig = abs(delta) / sigma
    if sig < tol.min_report_sigma:
        return Agreement(
            dimension="length",
            note=(f"claimed {claimed:.0f} m against observed {observed:.0f} m: "
                  f"{sig:.2f} sigma, inside the {tol.min_report_sigma:.1f} sigma "
                  f"reporting floor"))

    severity = _severity(sig, tol)
    # Domain override:''', "check_length agreement")

# ---------------------------------------------------------------- 5. check_class
rep(
"""    if claimed is None or observed is None:
        return None
    if claimed == "unknown" or observed == "unknown":
        return None
    if claimed == observed:
        return None
    if conf is None or conf < tol.min_class_confidence:
        return None""",
'''    if claimed is None or observed is None:
        return Uncomparable(
            dimension="class",
            reason=("the camera reported no silhouette class" if observed is None
                    else "the AIS static block carried no claimed ship type"),
            claim_missing=claimed is None)
    if claimed == "unknown" or observed == "unknown":
        # Suppression 1. This is 29.6% of rows in the measured Fehmarn slice -- pilot
        # boats, SAR, dredgers, port tenders, law enforcement. Lane A maps them to
        # "unknown" precisely because they have no honest silhouette equivalent, and
        # comparing against it here would reintroduce the manufactured-spoof failure
        # one layer later. It is uncomparable, and it must be VISIBLE as uncomparable:
        # counting it as agreement would inflate coverage on a third of real traffic.
        return Uncomparable(
            dimension="class",
            reason=("AIS ship type is 'unknown' -- a claim was made but it has no "
                    "comparable silhouette (29.6% of measured traffic)"
                    if claimed == "unknown"
                    else "the camera could not classify the silhouette"),
            claim_missing=False)
    if claimed == observed:
        return Agreement(
            dimension="class",
            note=f"AIS claimed '{claimed}' and the silhouette read '{observed}'")
    if conf is None or conf < tol.min_class_confidence:
        # They DISAGREE, but the classifier is too unsure to support saying so. That is
        # not agreement and reporting it as such would be dishonest in the dangerous
        # direction.
        return Uncomparable(
            dimension="class",
            reason=(f"claimed '{claimed}' against observed '{observed}', but "
                    f"classifier confidence "
                    f"{'absent' if conf is None else format(conf, '.2f')} is below the "
                    f"{tol.min_class_confidence:.2f} floor -- too weak to support a "
                    f"finding either way"))''', "check_class preconditions")

rep(
"""    confusable = frozenset({claimed, observed}) in CONFUSABLE_CLASSES
    sig = _probability_to_sigma(conf)
    if confusable:
        sig *= tol.confusable_class_discount
    if sig < tol.min_report_sigma:
        return None

    # SEVERITY IS A DOMAIN JUDGEMENT""",
'''    confusable = frozenset({claimed, observed}) in CONFUSABLE_CLASSES
    sig = _probability_to_sigma(conf)
    if confusable:
        sig *= tol.confusable_class_discount
    # NOTE the dimension-specific floor. class significance is in NATS (log-odds) while
    # every other dimension is in sigmas, so the shared 2.0 floor does not mean the same
    # thing here. See Tolerances.class_min_report_sigma for the full derivation.
    if sig < tol.class_min_report_sigma:
        # A real categorical difference that did not reach significance. Recorded as an
        # ANNOTATED agreement, never as silence: claimed "fishing" beside observed "tug"
        # with nothing said reads as the tool having missed it.
        return Agreement(
            dimension="class",
            note=(f"AIS claimed '{claimed}', silhouette read '{observed}' at "
                  f"confidence {conf:.2f} -- {sig:.2f} nats"
                  + (", and these two silhouettes are visually confusable at range"
                     if confusable else "")
                  + f", below the {tol.class_min_report_sigma:.1f} floor. Recorded, "
                    f"not raised as a finding."))

    # SEVERITY IS A DOMAIN JUDGEMENT''', "check_class agreement")

# -------------------------------------------------------------- 6. check_heading
rep(
"""    if contact.track_length_frames < 2:
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
        return None""",
'''    if contact.track_length_frames < 2:
        return Uncomparable(
            dimension="heading",
            reason=("single-frame contact: an apparent hull axis carries no bow/stern "
                    "discrimination, so a 180 degree error is as likely as a match"))
    observed = contact.observed_heading_deg_true
    if observed is None:
        return Uncomparable(
            dimension="heading",
            reason="no hull axis could be measured from the frames")

    claimed = track.claimed_heading_deg_true
    field = "AisTrack.claimed_heading_deg_true (AIS Heading)"
    if claimed is None:
        claimed = track.claimed_cog_deg_true
        field = "AisTrack.claimed_cog_deg_true (AIS COG — heading not broadcast)"
    if claimed is None:
        return Uncomparable(
            dimension="heading",
            reason="AIS broadcast neither a heading nor a course over ground",
            claim_missing=True)''', "check_heading preconditions")

rep(
"""    if sig < tol.min_report_sigma:
        return None

    # A near-180 disagreement""",
'''    if sig < tol.min_report_sigma:
        return Agreement(
            dimension="heading",
            note=(f"claimed {claimed:.0f} deg against observed {observed:.0f} deg: "
                  f"{sig:.2f} sigma, inside the reporting floor"))

    # A near-180 disagreement''', "check_heading agreement")

# ---------------------------------------------------------------- 7. check_speed
rep(
"""    if contact.track_length_frames < 2:
        return None
    observed = contact.observed_speed_ms
    if observed is None or track.claimed_sog_kn is None:
        return None""",
'''    if contact.track_length_frames < 2:
        return Uncomparable(
            dimension="speed",
            reason="single-frame contact: speed needs frame-to-frame displacement")
    observed = contact.observed_speed_ms
    if observed is None or track.claimed_sog_kn is None:
        return Uncomparable(
            dimension="speed",
            reason=("AIS broadcast no speed over ground" if track.claimed_sog_kn is None
                    else "no speed could be measured from the frames"),
            claim_missing=track.claimed_sog_kn is None)''', "check_speed preconditions")

rep(
"""    if sig < tol.min_report_sigma:
        return None

    return Mismatch(
        mismatch_id=_mid(assoc, "speed"),""",
'''    if sig < tol.min_report_sigma:
        return Agreement(
            dimension="speed",
            note=(f"claimed {claimed_ms:.2f} m/s against observed {observed:.2f} m/s: "
                  f"{sig:.2f} sigma, inside the reporting floor"))

    return Mismatch(
        mismatch_id=_mid(assoc, "speed"),''', "check_speed agreement")

# ------------------------------------------------------------- 8. check_position
rep(
"""    gap = assoc.spatial_gap_m
    if gap is None:
        return None""",
'''    gap = assoc.spatial_gap_m
    if gap is None:
        return Uncomparable(
            dimension="position",
            reason="association carried no spatial gap, so there is nothing to test")''',
    "check_position precondition")

rep(
"""        sig = abs(d_brg) / sigma_deg
        if sig < tol.min_report_sigma:
            return None""",
'''        sig = abs(d_brg) / sigma_deg
        if sig < tol.min_report_sigma:
            return Agreement(
                dimension="position",
                note=(f"claim bears {claimed_brg:.1f} deg, hull observed at "
                      f"{contact.observed_bearing_deg_true:.1f} deg: {sig:.2f} sigma "
                      f"cross-range. NOTE this says nothing about displacement ALONG "
                      f"the line of sight, which one monocular sensor cannot test."))''',
    "check_position cross-range agreement")

rep(
"""    sig = gap / sigma
    if sig < tol.min_report_sigma:
        return None
    return Mismatch(""",
'''    sig = gap / sigma
    if sig < tol.min_report_sigma:
        return Agreement(
            dimension="position",
            note=(f"claim and observation separated by {gap:.0f} m against a pooled "
                  f"{sigma:.0f} m uncertainty: {sig:.2f} sigma. WEAK -- no camera "
                  f"position was supplied, so this is the pooled test."))
    return Mismatch(''', "check_position pooled agreement")

# ------------------------------- 9. pooled-position literals (claimed 412 m / observed 0)
rep(
'''        claimed_value=round(gap, 1),
        claimed_field="AisTrack.claimed_lat/lon_deg (dead-reckoned to frame time)",
        observed_value=0.0,''',
'''        # These two literals are a SEPARATION against its ideal, not a claim against an
        # observation -- there is no camera position here, so no bearing pair exists to
        # report. evidence.py renders the pair as "AIS claimed X; the camera measured Y",
        # so the field strings must carry that or the case file reads "AIS claimed
        # 412.0 m; the camera measured 0.0 m", which is not a true sentence.
        claimed_value=round(gap, 1),
        claimed_field="separation between AisTrack.claimed_lat/lon_deg (dead-reckoned "
                      "to frame time) and the EO position — POOLED, no camera position",
        observed_value=0.0,''', "pooled position literals")

# ----------------------------------------------- 10. check_pair / check_all rewrite
rep(
'''def check_pair(
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
    return out''',
'''def check_pair(
    assoc: Association,
    track: AisTrack,
    contact: EoContact,
    *,
    tolerances: Tolerances = DEFAULT_TOLERANCES,
    camera: tuple[float, float] | None = None,
) -> ConsistencyResult:
    """
    Run every check on one matched pair, sorted into the three-way outcome.

    Order is stable so reports are reproducible. An honest vessel comes back with an
    empty mismatch list and a full agreement list, which is the common case and is a
    POSITIVE result -- it is what licenses a high-confidence MATCH, and it is exactly
    what a bare list[Mismatch] threw away.
    """
    mismatches: list[Mismatch] = []
    agreements: list[Agreement] = []
    uncomparable: list[Uncomparable] = []

    outcomes = [check(assoc, track, contact, tolerances) for check in CHECKS]
    outcomes.append(check_position(assoc, track, contact, tolerances, camera))

    for o in outcomes:
        if isinstance(o, Mismatch):
            mismatches.append(o)
        elif isinstance(o, Agreement):
            agreements.append(o)
        elif isinstance(o, Uncomparable):
            uncomparable.append(o)
        # None is no longer a legal outcome. If a check ever returns one it is a bug in
        # that check, and dropping it silently would understate coverage -- so it is
        # deliberately NOT handled here.

    # ---- the one cross-dimension judgement: is the ABSENCE itself unusual? ----------
    #
    # No single check can make this call. "no claimed length" is ordinary on its own --
    # plenty of Class B vessels omit fields. But a vessel transmitting dynamic position
    # reports while broadcasting NO static block at all is a different sentence: it is
    # the cheapest way to be hard to identify while still looking compliant on a screen.
    #
    # This is a WEAK POSITIVE INDICATOR and nothing more. verdict.py turns it into a
    # defer reason, never into a mismatch and never into a spoof. It carries no sigma
    # because there is no measurement behind it -- putting a number on a non-observation
    # is precisely the false precision criterion 4 exists to prevent.
    identity_claims_absent = {
        u.dimension for u in uncomparable
        if u.claim_missing and u.dimension in ("length", "class")}
    if identity_claims_absent == {"length", "class"}:
        uncomparable = [
            (Uncomparable(dimension=u.dimension,
                          reason=u.reason + " — and NO identity field was broadcast at "
                                            "all, which is itself unusual for a vessel "
                                            "reporting position normally",
                          claim_missing=u.claim_missing,
                          suspicious=True)
             if (u.claim_missing and u.dimension in ("length", "class")) else u)
            for u in uncomparable]

    return ConsistencyResult(
        association_id=assoc.association_id,
        mismatches=mismatches,
        agreements=agreements,
        uncomparable=uncomparable,
    )''', "check_pair")

rep(
'''    camera: tuple[float, float] | None = None,
) -> dict[str, list[Mismatch]]:''',
'''    camera: tuple[float, float] | None = None,
) -> dict[str, ConsistencyResult]:''', "check_all signature")

rep(
'''    out: dict[str, list[Mismatch]] = {}
    for assoc in associations:''',
'''    out: dict[str, ConsistencyResult] = {}
    for assoc in associations:''', "check_all body")

# apply
for old, new, label in edits:
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"ABORT: anchor '{label}' matched {n} times, expected 1")
    s = s.replace(old, new)

# aliases + return-type annotations on the checks
s = s.replace(") -> Mismatch | None:", ") -> Mismatch | Agreement | Uncomparable:")

s += '''

# ============================================================================
# NAME ALIASES. handoff_C.md and lane D's run_pipeline refer to these under two
# different names depending on which session wrote the call site. Both names point at
# the same function object -- an alias, never a second implementation, because two
# entry points that drift apart is the failure this project has already hit twice.
# ============================================================================
compare = check_pair
compare_all = check_all
'''

p.write_text(s)
print(f"OK: {len(edits)} anchored edits applied to {p}")
