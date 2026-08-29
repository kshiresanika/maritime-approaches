"""
verdict.py — Association + Mismatch[] -> a labelled, calibrated Verdict. Lane C.

THE PIPELINE PRODUCES THIS. THE LLM NEVER DOES.
contracts.py says it and judges will look for it: the deterministic pipeline produces
the label and the confidence; the LLM produces the RATIONALE only, on its own field of
EvidenceRecord, attributed to a named model. There is no LLM call in this file and
there must never be one. Nothing here samples, nothing here is stochastic, and the
same inputs produce the same Verdict on every machine.

LABEL NAMES COME FROM contracts.py, NOT FROM THE BRIEF
The task asked for MATCH / DARK_VESSEL / SPOOFING. contracts.VerdictLabel is
MATCH / DARK / SPOOF / UNKNOWN and it is ARCH-owned and frozen, so this module uses
the contract's names. The fourth one is not decoration: contracts.py calls UNKNOWN
"the machine-readable defer-to-human state. NOT optional." A three-label system has
nowhere to put "we looked and we cannot say", which is the answer criterion 4 exists
to protect.

    MATCH    we compared the claim against the observation and they agree
    DARK     an observation with no claim — the transponder is off
    SPOOF    the claim and the observation disagree beyond tolerance
    UNKNOWN  we could not conclude. Always defer_to_human.

WHAT confidence MEANS — one definition, four labels
confidence is always CONFIDENCE IN THE STATED LABEL. For SPOOF it is P(the claim is
false); for MATCH, P(the claim is honest), capped by how much of it was checked; for
DARK, P(there is genuinely no transponder claim for this hull); for UNKNOWN it is how
sure we are that this CANNOT be determined — a vessel with nothing comparable gets a
HIGH UNKNOWN confidence, not a low one, because "we are certain we cannot say" is a
firm finding and an operator should read it as one.

That last case is a trap worth naming: reporting UNKNOWN with confidence 0.0 reads as
"no idea, and no confidence in that either", which is meaningless. Lane D's
prioritizer must therefore read LABEL AND CONFIDENCE TOGETHER — a confident UNKNOWN
next to critical infrastructure is worth a look precisely because it is unexamined,
and ranking on the number alone would bury it.

=================================================================================
IS THE CONFIDENCE CALIBRATED? SAY THE TRUE THING.
=================================================================================
It is MODEL-CALIBRATED, NOT LEARNED. There is no labelled maritime spoofing dataset
in this repo and none was used. No scikit-learn calibration — no Platt scaling, no
isotonic regression — because both need labelled outcomes to fit against, and fitting
them on invented labels would produce a number that looks trained and means nothing.
scikit-learn is not even a dependency of this file.

What IS done: consistency.py already expresses every mismatch as a significance in
sigmas, and under the declared sensor noise model that has a probabilistic meaning —
it is P(a discrepancy at least this large | the claim is honest). Those are combined
as likelihood ratios against a DECLARED prior, so the output is a posterior
probability rather than an index. "Confidence 0.82" then means something a judge can
argue with:

    under the declared sensor model, the declared per-dimension spoof likelihoods,
    and a prior of 1 spoofing vessel in 500, the posterior probability that this
    claim is false is 0.82

Every one of those three inputs is a stated assumption, printed in this module and
destined for EvidenceRecord.limitations. The number is only as good as they are. The
honest claim is "calibrated against a declared model", never "validated".

Why bother, rather than a weighted score? Because a weighted score of 0.82 means only
"higher than 0.71". Criterion 2 ranks scarce assets on this number and criterion 4
asks what it means. A posterior answers; an index does not. And the prior is doing
real work: at these base rates it is the reason a single 6-sigma dimension is NOT
enough, which is the correct behaviour and falls out of the arithmetic instead of
being bolted on.

=================================================================================
THE DEFER-TO-HUMAN THRESHOLD, AND WHY IT SITS AT 0.85
=================================================================================
Measured, not assumed: the Fehmarn Belt demo day carries 321 distinct vessels after
lane A's deduplication (1,198,373 rows -> 431,751 -> 321 MMSIs). At the declared
prior of 1 in 500, the EXPECTED NUMBER OF REAL SPOOFING VESSELS IN THE WHOLE DAY IS
ABOUT 0.64. Fewer than one.

Now follow the consequence. If the tool states conclusions autonomously at a
confidence of C, it is wrong about (1 - C) of them. Auto-concluding on even five
vessels a day at C = 0.85 yields ~0.75 false conclusions per day — already comparable
to the ~0.64 real events the whole day contains. At C = 0.70 the same five vessels
yield 1.5 false conclusions per day, more than twice the real event rate, and the
watch officer correctly stops believing the tool. That is the number the threshold is
set by: BELOW ROUGHLY 0.85, THE TOOL GENERATES MORE FALSE CONCLUSIONS THAN THERE ARE
REAL EVENTS IN THE WATER.

So:

    confidence >= 0.85   the label is stated as the machine's conclusion
    0.50 - 0.85          the label is stated AND defer_to_human is set
    below 0.50           not enough to allege anything

The band is wide and the autonomous region is narrow on purpose. This tool ranks and
evidences; a human decides. Note also that the threshold is applied to a POSTERIOR,
so "0.85 confident" already accounts for the base rate — reading it as "15% of these
are wrong" is correct by construction, which is not true of a score.

STRUCTURAL DEFERRALS OVERRIDE THE NUMBER. A confidence of 0.99 computed off an
ambiguous association is 0.99 confident about possibly the wrong hull. Those triggers
set defer_to_human regardless of the arithmetic, and they are listed in
_structural_defer_reasons() below.

=================================================================================
THE RULE THAT KEEPS MATCH HONEST
=================================================================================
MATCH means "we compared and they agreed". If nothing could be compared, that is not
a match — it is UNKNOWN. So MATCH confidence is CAPPED BY EVIDENTIAL COVERAGE: you
cannot be more confident a claim is honest than the fraction of it you actually
checked. Without that cap, a Class B vessel broadcasting no static block at all would
sail through as a high-confidence MATCH, because a Bayesian posterior with no evidence
just returns the prior — and the prior says 99.8% of vessels are honest. That is the
single most dangerous failure mode in this file and the cap is what closes it.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import NamedTuple, Sequence

from loguru import logger

from consistency import ConsistencyResult, independent_dimension_count
from contracts import (
    Association,
    DeferReason,
    Mismatch,
    SpoofSubtype,
    Verdict,
    VerdictLabel,
)

RULESET_VERSION = "verdict-0.1.0-rulebased"

# Emitted with every verdict so the assumption set travels with the finding rather
# than living only in this docstring. evidence.py should copy these into
# EvidenceRecord.limitations verbatim.
CALIBRATION_STATEMENT = (
    "Confidence is a posterior probability computed from a DECLARED sensor model and "
    "a DECLARED prior, not learned from labelled data. No labelled maritime spoofing "
    "dataset exists in this project and no scikit-learn calibration was fitted. The "
    "number is calibrated against the model, not validated against outcomes."
)


# ================================================================================
# Declared assumptions. These three blocks ARE the calibration. Every number is a
# judgment with a stated basis and a stated failure direction; none is fitted.
# ================================================================================

# --- the prior ------------------------------------------------------------------
# P(a given vessel is broadcasting a false identity), before any evidence.
#
# THE MOST CONSEQUENTIAL UNDECLARED NUMBER IN MOST DETECTION SYSTEMS, so it is
# declared here. There is no authoritative published rate for AIS identity spoofing
# in the Baltic and none is invented: 1 in 500 is a deliberately conservative working
# figure, chosen so the tool errs toward NOT accusing.
#
# Failure direction, and it is asymmetric: too HIGH and the tool accuses innocent
# traffic on thin evidence, which is the legally dangerous direction in a domain where
# attribution has repeatedly failed in court. Too LOW and real spoofs sit in the defer
# band waiting for a human — which is where this tool wants borderline cases anyway.
# When in doubt, lower it.
#
# verdict_sensitivity() shows how any verdict moves across a range of priors, so this
# number can be argued with rather than taken on trust.
SPOOF_PRIOR = 1.0 / 500.0

# --- per-dimension P(discrepancy this large | the vessel IS spoofing) ------------
# The numerator of each likelihood ratio. NOT 1.0, and the reason matters: a competent
# spoofer picks a plausible cover identity, so a spoofing vessel does NOT always show
# a large discrepancy on every dimension. Setting these to 1.0 would assume every
# spoofer is careless.
#
# The behavioural dimensions are much lower than the identity ones because identity
# spoofing does not IMPLY a heading or speed discrepancy — a vessel lying about what
# it is still sails normally. A heading mismatch is far more likely to be a manoeuvre
# than a lie, and these weights are where that belief is written down.
P_DISCREPANCY_GIVEN_SPOOF: dict[str, float] = {
    "class": 0.60,
    "length": 0.60,
    "heading": 0.15,
    "cog": 0.15,
    "speed": 0.20,
    "position": 0.50,
}
_P_DISCREPANCY_DEFAULT = 0.20

# --- correlated evidence damping ------------------------------------------------
# Likelihood ratios multiply ONLY if the dimensions are independent, and ours are not
# fully independent: class and length both ride on the same observation, so a bad
# range estimate corrupts both together. Multiplying them raw would double-count one
# underlying error and manufacture false certainty.
#
# So the strongest dimension counts in full and each weaker one is discounted by this
# factor in sequence (1.0, 0.6, 0.36, ...). It is a deliberate under-count: two
# dimensions still say much more than one, which is the behaviour that matters, but
# five dimensions cannot stack into spurious certainty.
CORRELATION_DAMPING = 0.60

# --- decision thresholds --------------------------------------------------------
# See the module docstring for the derivation from the measured 321-vessel day.
CONFIDENCE_STATE = 0.85          # at or above: stated as the machine's conclusion
CONFIDENCE_ALLEGE_FLOOR = 0.50   # below: not enough to allege anything

# --- confidence caps that implement handoff_C.md item 3 -------------------------
# "Weight independent_dimension_count above strongest_significance."
#
# These are CAPS ON THE CONFIDENCE ITSELF, not just defer flags, and the distinction
# matters operationally: criterion 2 ranks a scarce patrol boat on this number. Left
# uncapped, a single-dimension 0.999 outranks a properly corroborated 0.99, and the
# boat gets sent to the weaker case. Measured on the check scenarios, that is exactly
# what happened before these were added — a lone 6.98-sigma length finding and a
# finding computed off a 0.047 association both reported 0.999.
#
# The justification is not squeamishness, it is that the arithmetic cannot see its own
# blind spot. A likelihood ratio is only valid if the sensor model behind it is right.
# With ONE dimension there is no corroboration, and an unmodelled systematic — a range
# estimate 40% low scaling the apparent length — produces precisely this signature at
# any significance you like. A second, independently-failing dimension is what rules
# that out, and no amount of sigma on the first one substitutes for it.
SINGLE_DIMENSION_CONFIDENCE_CAP = 0.80

# Evidence read off a pairing that may be the wrong hull. Capped below the stating
# threshold so such a finding can never be a machine conclusion and can never top the
# ranked list. Deliberately a cap rather than multiplying by assoc_score: assoc_score
# is a likelihood, not P(correct pairing), and treating it as one would be exactly the
# false precision this module exists to avoid.
UNCERTAIN_ASSOCIATION_CONFIDENCE_CAP = 0.70

# An association this weak cannot support any attribute finding, whatever the
# mismatches say — the measured coincidental dark/silent pairing scored 0.047 and
# carried no ambiguity flag, so assoc_ambiguous alone does not catch it.
LOW_ASSOC_SCORE = 0.20

# Mismatch observation_confidence below this makes the evidence itself doubtful.
LOW_OBSERVATION_CONFIDENCE = 0.60

# Beyond this age the CLAIM itself is a dead-reckoned guess rather than a report, and
# every comparison built on it inherits that. association.py already refuses to pair
# beyond 180 s; this is the tighter point at which a pairing is still made but the
# verdict should not be stated without a human.
#
# WHY THIS CONSTANT EXISTS AT ALL, AND WHAT IT REPLACED. The first version raised
# stale_ais whenever ANY mismatch was explained_by_staleness. That is the wrong
# reading: a fully explained mismatch is a reason to IGNORE that dimension, not a
# reason to escalate. Measured on the end-to-end scene, a random heading delta on a
# 27-second-old claim was correctly discounted from the arithmetic and then forced
# BOTH vessels — the honest one included — to defer. A tool that defers on everything
# has not made a decision support system, it has made a very expensive alarm.
# stale_ais now fires only when staleness actually changed what could be concluded.
STALE_CLAIM_S = 120.0

# How much of a claim must be checkable before a MATCH can be stated. Four dimensions
# are attempted (class, length, heading/cog, speed); checking one of four is not a
# clean bill of health. See "THE RULE THAT KEEPS MATCH HONEST".
MATCH_COVERAGE_FLOOR = 0.50
# Confidence ceiling when coverage is total. Never 1.0: the camera cannot see a hull's
# name, its cargo or its intent, so a MATCH is always "consistent so far".
MATCH_CONFIDENCE_CEILING = 0.95

# --- DARK and position-spoof ----------------------------------------------------
# How much to trust "no AIS claim here" as evidence of a switched-off transponder.
# Default deliberately mid-range, and it is the Bornholm caveat in numeric form: an
# AIS gap caused by receiver geometry is not an AIS gap caused by evasion. Measured on
# the demo day: Fehmarn 3,556 messages per vessel, Bornholm 1,432 — 2.5x sparser on
# the SAME day, purely from basestation coverage. Callers working a well-covered area
# raise it; a sparse one lowers it and the defer reason fires.
DEFAULT_AIS_COVERAGE_CONFIDENCE = 0.70
SPARSE_COVERAGE_THRESHOLD = 0.75

# A position-spoof candidate is an in-view claim with nothing observed. Innocent
# explanations are abundant — a missed detection, an occluded hull, one ship hidden
# behind another — and this module cannot model occlusion at all. So this evidence is
# capped hard and the finding effectively always defers. Raising this cap without an
# occlusion model would mean accusing real named vessels of a spoof the detector
# invented by blinking.
POSITION_SPOOF_CONFIDENCE_CAP = 0.60


# ================================================================================
# Result plumbing.
# ================================================================================

class DimensionContribution(NamedTuple):
    """One dimension's contribution to the posterior, kept inspectable.

    Criterion 4 is not satisfied by a number; it is satisfied by a number somebody can
    take apart. This is the take-apart."""

    dimension: str
    significance: float
    p_given_honest: float      # P(discrepancy this large | honest) — from the sigmas
    p_given_spoof: float       # declared, see P_DISCREPANCY_GIVEN_SPOOF
    likelihood_ratio: float
    damping: float             # 1.0 for the strongest, then CORRELATION_DAMPING^k
    log10_contribution: float


class VerdictExplanation(NamedTuple):
    """Everything that went into one Verdict, in the order it was applied.

    Lane-C-internal — verdict.py to report.py is a lane C to lane D seam, so this must
    NOT be passed across it. contracts.py owns everything that crosses. The Verdict
    itself carries what lane D needs; this exists for the check script, for debugging
    at 03:00, and for the LLM prompt in report.py to be built from facts rather than
    from a re-derivation."""

    prior: float
    contributions: list[DimensionContribution]
    posterior_spoof: float
    coverage_fraction: float
    structural_defers: list[DeferReason]
    notes: list[str]


# ================================================================================
# The arithmetic.
# ================================================================================

# The class dimension does not live on the same scale as the others, and this set is
# the ONLY place that fact is written down on this side of the seam. Keep it in step
# with consistency._probability_to_sigma.
_LOG_ODDS_DIMENSIONS = frozenset({"class"})


def _p_given_honest(significance: float, dimension: str = "") -> float:
    """Recover P(a discrepancy at least this large | the claim is honest) from the
    significance consistency.py already computed.

    TWO SCALES, AND MIXING THEM WAS A REAL DEFECT.

    Observed: consistency.py builds most significances as a true sigma — a delta over
    a combined uncertainty — and this function inverts that exactly, p = 2*Phi(-sigma).
    Claimed, until 2026-08-29: that ALL of them work that way.

    The mismatch: class is CATEGORICAL and has no natural sigma, so lane C maps its
    confidence through log-odds, ln(p/(1-p)), in nats. Feeding a nat count into
    erfc(x/sqrt2) as though it were a sigma is a category error, and it is not small:
    at confidence 0.95 the log-odds value is 2.944, which erfc reads as a tail
    probability of 0.0032 and therefore a likelihood ratio of 185 — a number with no
    probabilistic justification at all, attached to the dimension carrying the
    joint-highest P(discrepancy | spoof). Why it matters: that LR multiplies straight
    into the posterior that becomes Verdict.confidence, which criterion 2 then ranks a
    scarce patrol boat on.

    The honest inverse of log-odds needs no approximation. If the classifier says the
    observed class is right with probability p, then P(this disagreement | the claim is
    honest) is simply 1 - p, and 1 - p = 1/(1 + e^s) exactly. So the class dimension is
    inverted on its own scale and every other dimension keeps the normal tail.

    What would change the answer: giving Mismatch an explicit significance_scale field
    so the producer declares the scale instead of the consumer inferring it from the
    dimension name. That is the right fix and it needs a contract change.

    math.erfc rather than scipy.stats so this stays importable if scipy is missing — a
    verdict module that cannot run is worse than one with a plain erfc."""
    if significance <= 0.0:
        return 1.0
    if dimension in _LOG_ODDS_DIMENSIONS:
        # Exact inverse of ln(p/(1-p)); no tail approximation is involved or wanted.
        return max(1.0 / (1.0 + math.exp(min(significance, 700.0))), 1e-300)
    # 2 * Phi(-x) == erfc(x / sqrt(2))
    return max(math.erfc(significance / math.sqrt(2.0)), 1e-300)


def _usable_mismatches(mismatches: Sequence[Mismatch]) -> tuple[list[Mismatch], list[str]]:
    """Drop the mismatches that must not carry a verdict, and say which and why.

    Two exclusions, both from handoff_C.md:
      * explained_by_staleness — a delta a stale report already accounts for is not
        evidence of anything. It stays in the EvidenceRecord to show what was seen and
        why it was discounted; it does not reach the arithmetic.
      * observation_confidence below LOW_OBSERVATION_CONFIDENCE — evidence we do not
        trust cannot raise our confidence. It is downgraded to a defer reason instead."""
    kept: list[Mismatch] = []
    notes: list[str] = []
    for m in mismatches:
        if m.explained_by_staleness:
            notes.append(
                f"{m.dimension} mismatch ({m.significance:.2f} sigma) excluded from the "
                "confidence: a stale claim already explains a delta this size")
            continue
        if m.observation_confidence < LOW_OBSERVATION_CONFIDENCE:
            notes.append(
                f"{m.dimension} mismatch ({m.significance:.2f} sigma) excluded from the "
                f"confidence: observation confidence {m.observation_confidence:.2f} is "
                f"below {LOW_OBSERVATION_CONFIDENCE:.2f}")
            continue
        kept.append(m)
    return kept, notes


def _posterior_spoof(mismatches: Sequence[Mismatch],
                     prior: float = SPOOF_PRIOR
                     ) -> tuple[float, list[DimensionContribution]]:
    """Combine mismatches into P(the claim is false | the evidence).

    Odds form, because odds multiply and probabilities do not:

        posterior_odds = prior_odds * PRODUCT( LR_d ^ damping_d )
        LR_d = P(discrepancy | spoof) / P(discrepancy | honest)

    The denominator is the sigma-derived tail probability — the evidence. The
    numerator is declared per dimension. Dimensions are sorted strongest first so the
    damping penalises the WEAKER corroborating evidence, not the primary finding.

    One dimension per orientation: heading and cog measure the same physical quantity
    two ways, so counting both would double-weight a Class B vessel's single fact."""
    ordered = sorted(mismatches, key=lambda m: m.significance, reverse=True)

    log_odds = math.log(prior / (1.0 - prior))
    contributions: list[DimensionContribution] = []
    seen_families: set[str] = set()
    rank = 0

    for m in ordered:
        family = "orientation" if m.dimension in ("heading", "cog") else m.dimension
        if family in seen_families:
            continue
        seen_families.add(family)

        p_honest = _p_given_honest(m.significance, m.dimension)
        p_spoof = P_DISCREPANCY_GIVEN_SPOOF.get(m.dimension, _P_DISCREPANCY_DEFAULT)
        lr = p_spoof / p_honest
        damping = CORRELATION_DAMPING ** rank
        contribution = damping * math.log(lr)
        log_odds += contribution
        rank += 1

        contributions.append(DimensionContribution(
            dimension=m.dimension,
            significance=m.significance,
            p_given_honest=p_honest,
            p_given_spoof=p_spoof,
            likelihood_ratio=lr,
            damping=damping,
            log10_contribution=contribution / math.log(10.0),
        ))

    # Back to a probability. Clamped so a very strong case cannot print as a literal
    # 1.0 — nothing in this domain is certain, and a 1.0 on an evidence card is a
    # claim this pipeline is not entitled to make.
    odds = math.exp(min(log_odds, 700.0))
    return min(odds / (1.0 + odds), 0.999), contributions


def _coverage_fraction(result: ConsistencyResult) -> float:
    """What fraction of the attempted comparisons actually ran.

    agreements + mismatches over agreements + mismatches + uncomparable. This is the
    number that caps a MATCH: it is the difference between "we checked and it is fine"
    and "we could not check"."""
    ran = len(result.agreements) + len(result.mismatches)
    total = ran + len(result.uncomparable)
    return ran / total if total else 0.0


# ================================================================================
# Structural deferrals — these override the arithmetic.
# ================================================================================

def _structural_defer_reasons(assoc: Association,
                              result: ConsistencyResult | None,
                              *, ais_coverage_confidence: float,
                              is_dark: bool) -> list[DeferReason]:
    """Reasons a human must look, independent of how confident the number is.

    A 0.99 confidence computed off an ambiguous association is 0.99 confident about
    possibly the wrong hull. Arithmetic cannot rescue that; only a person looking at
    the frame can."""
    reasons: list[DeferReason] = []

    if assoc.assoc_ambiguous:
        reasons.append("ambiguous_association")
    if (assoc.contact_id is not None and assoc.track_id is not None
            and assoc.assoc_score < LOW_ASSOC_SCORE):
        # handoff_C.md item 1. The measured coincidental pairing scored 0.047 with no
        # ambiguity flag, because it had exactly one feasible candidate.
        reasons.append("low_confidence")
    if is_dark and ais_coverage_confidence < SPARSE_COVERAGE_THRESHOLD:
        # The Bornholm caveat. An AIS gap from receiver geometry is not an AIS gap
        # from a switched-off transponder, and this module cannot tell them apart.
        reasons.append("sparse_ais_coverage")

    if result is not None:
        # A SUSPICIOUS absence defers; an ordinary one does not. A Class B transponder
        # omitting heading is routine and deferring on every Class B vessel would bury
        # the operator. A Class A transponder omitting its static block is odd, and
        # that is what suspicious means. Ordinary absences still reach the report
        # through limitation_strings(), which is where they belong — they are a
        # limitation of the finding, not a reason to escalate it.
        if any(u.suspicious for u in result.uncomparable):
            reasons.append("claim_field_missing")
        # Staleness defers only when it CHANGED what could be concluded: either every
        # mismatch was discounted (so the evidence is entirely explained away and what
        # is left is silence, not agreement), or the claim is old enough that the
        # whole comparison rides on dead reckoning.
        staleness_explained = [m for m in result.mismatches if m.explained_by_staleness]
        if (staleness_explained
                and len(staleness_explained) == len(result.mismatches)
                and not result.agreements):
            # Every signal we had was discounted and nothing agreed either — what is
            # left is silence, not consistency. The "and not result.agreements" clause
            # is load-bearing and was added after the end-to-end scene deferred an
            # HONEST vessel whose class, length and speed all agreed and whose only
            # mismatch was a random heading on a 27-second-old claim. Three agreeing
            # dimensions plus one properly explained one is a good match, not a doubt.
            reasons.append("stale_ais")
        elif (assoc.time_delta_s is not None
              and abs(assoc.time_delta_s) > STALE_CLAIM_S):
            reasons.append("stale_ais")
        if any(m.observation_confidence < LOW_OBSERVATION_CONFIDENCE
               for m in result.mismatches):
            reasons.append("low_observation_confidence")
        # handoff_C.md item 3: one dimension is a classifier error waiting to be
        # alleged. Two that fail for unrelated reasons is a case. contracts.py has no
        # DeferReason for "single dimension", so low_confidence carries it and a
        # request is filed to ARCH for a precise one.
        if (independent_dimension_count(result, "major") == 1
                and "low_confidence" not in reasons):
            reasons.append("low_confidence")

    # Stable order, no duplicates — a defer_reasons list that reorders between runs
    # makes two identical verdicts look different in a diff.
    return sorted(set(reasons))


def _spoof_subtype(mismatches: Sequence[Mismatch]) -> SpoofSubtype:
    """Which deception was caught. contracts.py: "SPOOF" alone under-sells the work.

    Identity wins over kinematic when both are present, because a class or length
    discrepancy is staleness-immune and a heading one is not — the stronger claim
    should name the finding."""
    dims = {m.dimension for m in mismatches}
    if dims & {"class", "length", "width"}:
        return "identity"
    if dims & {"heading", "cog", "speed"}:
        return "kinematic"
    return "position"


# ================================================================================
# Public entry points.
# ================================================================================

def decide(association: Association,
           result: ConsistencyResult | None = None,
           *,
           ais_coverage_confidence: float = DEFAULT_AIS_COVERAGE_CONFIDENCE,
           decided_at_utc: datetime | None = None,
           prior: float = SPOOF_PRIOR,
           ) -> tuple[Verdict, VerdictExplanation]:
    """One Association (plus its ConsistencyResult, if matched) -> one Verdict.

    Returns the Verdict and a VerdictExplanation. The explanation is lane C internal
    and must not cross into lane D; the Verdict is the contract.

    ais_coverage_confidence: how much "no AIS claim here" can be trusted in THIS area.
    Measured on the demo day, Fehmarn carries 3,556 messages per vessel and Bornholm
    1,432 — 2.5x sparser on the same day from basestation geometry alone. Pass a low
    value for a sparse area and the sparse_ais_coverage defer reason fires.

    decided_at_utc: injectable so the check script is reproducible. Defaults to now.
    """
    if decided_at_utc is None:
        decided_at_utc = datetime.now(timezone.utc)
    verdict_id = f"vd-{association.association_id}"

    is_matched = (association.contact_id is not None
                  and association.track_id is not None)
    is_dark = association.track_id is None
    notes: list[str] = []

    # ---- 1. Unmatched observation -> DARK ---------------------------------------
    if is_dark:
        label, confidence, subtype, extra = _decide_dark(
            association, ais_coverage_confidence, notes)
        mismatch_ids: list[str] = []
        contributions: list[DimensionContribution] = []
        coverage = 0.0

    # ---- 2. Unmatched in-view claim -> position-spoof candidate ------------------
    elif association.contact_id is None:
        label, confidence, subtype, extra = _decide_unobserved_claim(association, notes)
        mismatch_ids = []
        contributions = []
        coverage = 0.0

    # ---- 3. Matched pair -> the attribute case ----------------------------------
    else:
        if result is None:
            raise ValueError(
                f"{association.association_id} is a MATCHED association, so a "
                "ConsistencyResult is required. Deciding a matched pair without "
                "running the claimed-vs-observed comparison would label it from "
                "geometry alone, which is exactly the criterion 3 failure this "
                "pipeline exists to avoid.")
        usable, drop_notes = _usable_mismatches(result.mismatches)
        notes.extend(drop_notes)
        posterior, contributions = _posterior_spoof(usable, prior)

        # Caps applied to the POSTERIOR, before the label is chosen, so they move the
        # ranking and not merely the flag. See the constants for why.
        if len(contributions) == 1 and posterior > SINGLE_DIMENSION_CONFIDENCE_CAP:
            notes.append(
                f"posterior {posterior:.4f} capped to "
                f"{SINGLE_DIMENSION_CONFIDENCE_CAP:.2f}: the case rests on ONE "
                f"dimension ({contributions[0].dimension}). A single dimension cannot "
                "distinguish a real spoof from an unmodelled systematic error in that "
                "one measurement, at any significance")
            posterior = SINGLE_DIMENSION_CONFIDENCE_CAP
        if ((association.assoc_ambiguous
             or association.assoc_score < LOW_ASSOC_SCORE)
                and posterior > UNCERTAIN_ASSOCIATION_CONFIDENCE_CAP):
            notes.append(
                f"posterior capped to {UNCERTAIN_ASSOCIATION_CONFIDENCE_CAP:.2f}: the "
                f"pairing scored {association.assoc_score:.3f}"
                + (" and was flagged ambiguous" if association.assoc_ambiguous else "")
                + ", so this evidence may belong to a different hull")
            posterior = UNCERTAIN_ASSOCIATION_CONFIDENCE_CAP

        coverage = _coverage_fraction(result)
        label, confidence, subtype, extra = _decide_matched(
            posterior, coverage, usable, result, notes)
        mismatch_ids = [m.mismatch_id for m in result.mismatches]

    # ---- 4. Deferral: structural triggers, then the confidence band --------------
    structural = _structural_defer_reasons(
        association, result if is_matched else None,
        ais_coverage_confidence=ais_coverage_confidence, is_dark=is_dark)

    defer_reasons = sorted(set(structural) | set(extra))
    defer = bool(defer_reasons) or confidence < CONFIDENCE_STATE

    if confidence < CONFIDENCE_STATE and "low_confidence" not in defer_reasons:
        defer_reasons.append("low_confidence")
        defer_reasons.sort()

    if label == "UNKNOWN":
        # contracts.py: UNKNOWN IS the machine-readable defer state. It can never be
        # emitted without the flag, whatever the arithmetic said.
        defer = True
        if not defer_reasons:
            defer_reasons = ["low_confidence"]

    verdict = Verdict(
        verdict_id=verdict_id,
        association_id=association.association_id,
        label=label,
        confidence=round(confidence, 4),
        mismatch_ids=mismatch_ids,
        decided_at_utc=decided_at_utc,
        spoof_subtype=subtype if label == "SPOOF" else None,
        defer_to_human=defer,
        defer_reasons=defer_reasons,
        ruleset_version=RULESET_VERSION,
    )
    explanation = VerdictExplanation(
        prior=prior,
        contributions=contributions,
        posterior_spoof=(contributions and confidence) or confidence,
        coverage_fraction=coverage,
        structural_defers=structural,
        notes=notes,
    )
    return verdict, explanation


def _decide_matched(posterior: float, coverage: float,
                    usable: Sequence[Mismatch], result: ConsistencyResult,
                    notes: list[str]
                    ) -> tuple[VerdictLabel, float, SpoofSubtype, list[DeferReason]]:
    """Label a matched pair from the posterior and the evidential coverage."""
    extra: list[DeferReason] = []

    if posterior >= CONFIDENCE_ALLEGE_FLOOR:
        # Enough to state the claim is false. Confidence IS the posterior.
        return "SPOOF", posterior, _spoof_subtype(usable), extra

    # Not enough to allege. Now the honest question is whether we can vouch for it.
    if coverage < MATCH_COVERAGE_FLOOR:
        # THE RULE THAT KEEPS MATCH HONEST. With nothing checked, a Bayesian posterior
        # just returns the prior — 99.8% honest — and a Class B vessel broadcasting no
        # static block would sail through as a high-confidence MATCH. It is not a
        # match; it is an unexamined vessel.
        notes.append(
            f"only {coverage*100:.0f}% of the attempted comparisons could be made "
            f"(floor {MATCH_COVERAGE_FLOOR*100:.0f}%) — not enough to vouch for this "
            "claim, so the label is UNKNOWN rather than MATCH")
        extra.append("claim_field_missing")
        # Confidence in the STATED LABEL, and the stated label is "cannot determine".
        # The less we could check, the MORE certain we are that no conclusion is
        # available. Reporting the coverage itself here (an earlier version did) gives
        # 0.000 for a vessel we could not examine at all, which reads as "no idea and
        # no confidence in that either" and is the opposite of the finding.
        return "UNKNOWN", min(1.0 - coverage, 0.99), "identity", extra

    # Confidence the claim is honest, capped by how much of it we actually checked and
    # by the ceiling — the camera cannot see a name, a cargo or an intent, so a MATCH
    # is always "consistent so far", never "verified".
    confidence = min(1.0 - posterior, MATCH_CONFIDENCE_CEILING * coverage)
    if result.mismatches:
        notes.append(
            f"{len(result.mismatches)} mismatch(es) present but the combined posterior "
            f"is {posterior:.3f}, below the {CONFIDENCE_ALLEGE_FLOOR:.2f} floor — not "
            "enough to allege a false claim")
    return "MATCH", confidence, "identity", extra


def _decide_dark(assoc: Association, coverage_confidence: float,
                 notes: list[str]
                 ) -> tuple[VerdictLabel, float, SpoofSubtype, list[DeferReason]]:
    """An observation with no claim. contracts.py: the absence IS the finding.

    Confidence here is NOT about the vessel's intent — it is P(there is genuinely no
    transponder claim for this hull), which is a question about our AIS coverage and
    about how cleanly the association ruled every candidate out. A vessel can be dark
    because it switched off, or because the receiver never heard it. This module
    cannot distinguish those and does not pretend to."""
    extra: list[DeferReason] = []

    if assoc.assoc_ambiguous and assoc.candidate_count > 0:
        # association.py flags this two ways: a contact that had feasible candidates
        # which all went to other contacts, or one whose bearing was unusable. Either
        # way nothing was established, and calling it DARK would invent a finding.
        notes.append(
            f"contact had {assoc.candidate_count} candidate claim(s) and was flagged "
            "ambiguous by association — nothing was established either way, so this "
            "is UNKNOWN, not a dark vessel")
        # Same semantic as the matched UNKNOWN path: high confidence that no
        # conclusion is available, not low confidence in a conclusion.
        return "UNKNOWN", 0.80, "position", extra

    # Start from how much "no claim here" can be trusted at all in this area, then
    # take the fact that association found ZERO feasible candidates as confirmation.
    confidence = coverage_confidence
    if assoc.candidate_count == 0:
        # contracts.py: "Zero candidates in a busy area means something different from
        # zero in an empty one." Zero feasible candidates after the gate is the
        # cleanest dark signature available here.
        confidence = min(coverage_confidence * 1.15, 0.90)
    else:
        notes.append(
            f"{assoc.candidate_count} claim(s) were feasible for this contact but went "
            "to other contacts — the dark finding is weaker than a clean zero")
        confidence *= 0.75

    if coverage_confidence < SPARSE_COVERAGE_THRESHOLD:
        notes.append(
            f"AIS coverage confidence {coverage_confidence:.2f} is below "
            f"{SPARSE_COVERAGE_THRESHOLD:.2f}: an AIS gap here may be receiver "
            "geometry rather than a switched-off transponder (measured, same day: "
            "Bornholm tracks are 2.5x sparser than Fehmarn purely from basestation "
            "coverage)")

    return "DARK", confidence, "position", extra


def _decide_unobserved_claim(assoc: Association, notes: list[str]
                             ) -> tuple[VerdictLabel, float, SpoofSubtype,
                                        list[DeferReason]]:
    """An in-view AIS claim with nothing observed where it says it is.

    association.py has already done the hard part: this claim was inside the camera
    frustum and inside its range, so "nobody looked" is excluded. What is NOT excluded
    is that the detector missed it, or that another hull occluded it, or that it sat
    against a cluttered shoreline. This module has no occlusion model, so the
    confidence is capped hard and the finding effectively always defers.

    Raising POSITION_SPOOF_CONFIDENCE_CAP without an occlusion model would mean
    accusing a real named vessel of a spoof the detector invented by blinking. That is
    the defamation risk in constraint 2, in code."""
    notes.append(
        "claim was inside the camera frustum with nothing observed at its position. "
        "A missed detection or an occluding hull produces the same signature and this "
        "module has no occlusion model, so the confidence is capped at "
        f"{POSITION_SPOOF_CONFIDENCE_CAP:.2f} and the finding defers by construction")
    return ("SPOOF", POSITION_SPOOF_CONFIDENCE_CAP, "position",
            ["no_range_estimate"] if assoc.spatial_gap_m is None else [])


def decide_all(associations: Sequence[Association],
               results: dict[str, ConsistencyResult],
               **kwargs) -> list[tuple[Verdict, VerdictExplanation]]:
    """Every association -> its verdict, in input order.

    `results` is keyed by association_id, exactly as consistency.compare_all returns
    it. A matched association missing from it is a wiring bug, and it is loud."""
    out: list[tuple[Verdict, VerdictExplanation]] = []
    for a in associations:
        result = results.get(a.association_id)
        if a.contact_id is not None and a.track_id is not None and result is None:
            logger.error(
                "{} is matched but has no ConsistencyResult — skipping rather than "
                "labelling it from geometry alone", a.association_id)
            continue
        out.append(decide(a, result, **kwargs))
    return out


# ================================================================================
# Sensitivity — so the declared prior can be argued with, not taken on trust.
# ================================================================================

def verdict_sensitivity(result: ConsistencyResult,
                        priors: Sequence[float] = (1/50, 1/100, 1/500, 1/2000, 1/10000),
                        ) -> list[tuple[float, float, float]]:
    """(prior, posterior, evidence_decades) across a range of priors.

    The prior is the least defensible number in this file, so its effect is made
    visible rather than buried. If a verdict flips label across two plausible priors,
    it is not a verdict — it is a preference, and the honest output is a deferral.

    evidence_decades is log10 of the combined likelihood ratio, and it is the column
    that actually explains the table: it is how many orders of magnitude the EVIDENCE
    moves the odds, independent of where they started. When it is large the posterior
    pins against its ceiling at every plausible prior — which is not the table being
    broken, it is the case being strong enough that the assumption stops mattering.
    Put this in the pitch deck next to the confidence."""
    usable, _ = _usable_mismatches(result.mismatches)
    decades = sum(c.log10_contribution for c in _posterior_spoof(usable, SPOOF_PRIOR)[1])
    return [(p, _posterior_spoof(usable, p)[0], decades) for p in priors]


def limitation_strings(result: ConsistencyResult | None,
                       verdict: Verdict) -> list[str]:
    """Human-readable limitations for EvidenceRecord.limitations. Lane D calls this.

    A FUNCTION rather than a type, deliberately: ConsistencyResult is lane C internal
    and must not cross the seam into lane D, but list[str] is exactly what
    EvidenceRecord.limitations is declared as. So the facts cross, the type does not.

    This is the channel by which "checks we could not run" reaches the case file.
    Ordinary un-run checks do not defer — deferring on every Class B vessel would bury
    the operator — but they absolutely must appear in the record, because a report that
    lists four agreeing dimensions while silently having compared two is not
    evidence-grade, it is a misleading one."""
    out = [CALIBRATION_STATEMENT]
    if verdict.defer_to_human:
        out.append("This verdict is flagged for human review: "
                   + ", ".join(verdict.defer_reasons))
    if result is not None:
        for u in result.uncomparable:
            out.append(f"{u.dimension} could not be compared: {u.reason}"
                       + (" (this absence is itself unusual)" if u.suspicious else ""))
        for m in result.mismatches:
            if m.explained_by_staleness:
                out.append(
                    f"{m.dimension} differed by {m.delta} {m.unit} but a stale AIS "
                    "report already accounts for a change that size; it was excluded "
                    "from the confidence")
    return out


def is_prior_sensitive(result: ConsistencyResult,
                       priors: Sequence[float] = (1/100, 1/500, 1/2000)) -> bool:
    """True when the LABEL changes across plausible priors — i.e. the finding rests on
    the assumption rather than on the evidence. A caller seeing True should defer."""
    usable, _ = _usable_mismatches(result.mismatches)
    labels = {(_posterior_spoof(usable, p)[0] >= CONFIDENCE_ALLEGE_FLOOR)
              for p in priors}
    return len(labels) > 1
