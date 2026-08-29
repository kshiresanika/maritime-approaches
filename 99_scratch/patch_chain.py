"""Patch 2 of 2. Fixes, in dependency order:
  verdict.py       -- the log-odds/sigma unit error at the class dimension
  run_pipeline.py  -- the decide_all call site (arity, kwarg, return shape)
  pi_sensor.py     -- detection_confidence (HTTP 400 P0), --scale, is-not-None
  edge_client.py   -- staleness threshold divergence
  app.py           -- real identities on /api/state
  eo_vlm.py        -- documentation asserting a safety property from stale arithmetic
Anchored; aborts on any missing or ambiguous anchor."""
import sys, pathlib

ROOT = pathlib.Path(sys.argv[1])
PATCHES: dict[str, list[tuple[str, str, str]]] = {}

def rep(f, old, new, label):
    PATCHES.setdefault(f, []).append((old, new, label))


# ======================================================================= verdict.py
rep("03_src/verdict.py",
'''def _p_given_honest(significance: float) -> float:
    """Recover P(a discrepancy at least this large | the claim is honest) from the
    significance consistency.py already computed.

    consistency.py built each significance by mapping a tail probability through the
    normal quantile, so this inverts that exactly: p = 2 * Phi(-sigma). Doing it here
    rather than re-deriving from the raw deltas means there is ONE definition of what
    a sigma means in lane C, and verdict.py cannot silently disagree with the module
    that produced the evidence.

    math.erfc is used rather than scipy.stats so this function stays importable even
    if the scipy install is still missing — the rest of the file needs no scipy at
    all, and a verdict module that cannot run is worse than one with a plain erfc."""
    if significance <= 0.0:
        return 1.0
    # 2 * Phi(-x) == erfc(x / sqrt(2))
    return max(math.erfc(significance / math.sqrt(2.0)), 1e-300)''',
'''# The class dimension does not live on the same scale as the others, and this set is
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
    return max(math.erfc(significance / math.sqrt(2.0)), 1e-300)''',
    "p_given_honest scale")

rep("03_src/verdict.py",
"        p_honest = _p_given_honest(m.significance)",
"        p_honest = _p_given_honest(m.significance, m.dimension)",
    "p_given_honest call site")


# ================================================================== run_pipeline.py
rep("04_demo/run_pipeline.py",
'''    mismatches_by_assoc = consistency.check_all(
        associations, tracks_by_id, contacts_by_id,
        camera=(pose["lat_deg"], pose["lon_deg"]))

    verdicts = verdict_mod.decide_all(
        associations, mismatches_by_assoc, tracks_by_id, contacts_by_id, now=now)''',
'''    # check_all returns dict[association_id -> ConsistencyResult]. The RESULT, not a
    # bare mismatch list: verdict.py caps MATCH confidence by evidential coverage, which
    # it computes from agreements + mismatches + uncomparable. Hand it only the
    # mismatches and every honest vessel reads coverage 0.0 and comes back UNKNOWN.
    results_by_assoc = consistency.check_all(
        associations, tracks_by_id, contacts_by_id,
        camera=(pose["lat_deg"], pose["lon_deg"]))

    # decide_all takes (associations, results) and returns
    # list[tuple[Verdict, VerdictExplanation]]. The explanation half is lane C internal
    # and is DROPPED HERE DELIBERATELY -- verdict.py:302 states it must not cross into
    # lane D, and discarding it at the seam is what enforces that. Everything lane D
    # legitimately needs from it comes back through limitation_strings() below, as
    # list[str], which is what EvidenceRecord.limitations is declared as.
    decided = verdict_mod.decide_all(associations, results_by_assoc, decided_at_utc=now)
    verdicts = [v for v, _explanation in decided]''',
    "run_pipeline consistency+verdict call")

rep("04_demo/run_pipeline.py",
'''        records.append(evidence_mod.build_record(
            verdict=v, association=a,
            mismatches=mismatches_by_assoc.get(v.association_id, []),
            track=tracks_by_assoc.get(v.association_id),
            contact=contacts_by_assoc.get(v.association_id),
            priority=p, behaviour_notes=notes.get(v.verdict_id), now=now))''',
'''        result = results_by_assoc.get(v.association_id)
        records.append(evidence_mod.build_record(
            verdict=v, association=a,
            mismatches=result.mismatches if result else [],
            track=tracks_by_assoc.get(v.association_id),
            contact=contacts_by_assoc.get(v.association_id),
            priority=p, behaviour_notes=notes.get(v.verdict_id),
            # CRITERION 4'S HONESTY CHANNEL, previously severed. Without this the record
            # declares it has no stated calibration basis -- which was true, because
            # limitation_strings() existed and nothing called it. It is what carries
            # "these dimensions could NOT be checked" into the case file. A report
            # listing four agreeing dimensions while silently having compared two is not
            # evidence-grade, it is a misleading one.
            consistency_limitations=verdict_mod.limitation_strings(result, v),
            now=now))''',
    "run_pipeline evidence call")


# ===================================================================== pi_sensor.py
rep("04_demo/pi_sensor.py",
'''def to_contact(det: dict, *, horizon_y: float, pose: dict, frame_w: int, frame_h: int,
               scale: float, frame_time: datetime, frame_ref: str) -> dict:''',
'''def _classical_detection_confidence(area_px: float, frames: int,
                                    min_area_px: int) -> float:
    """How much this detector believes the box is a vessel at all. NEVER 1.0.

    detection_confidence became a REQUIRED field of EoContact on 2026-08-29 and this
    function did not have one to give, so every POST from this node was rejected with
    HTTP 400 and the classical fallback -- the escape route for COCO 'boat' failing to
    fire on a printed silhouette -- delivered nothing at all.

    The tempting fix is to hardcode 1.0. That is worse than the bug: it tells lane C
    every blob is a certain vessel, and detection_confidence feeds the DARK path, where
    "a vessel is present and not transmitting" rests on the first half of that sentence
    being true. A constant maximum would inflate every DARK confidence with a number
    nobody measured, which breaks MEASURED NUMBERS ONLY silently and in the confident
    direction.

    A background-subtraction detector has exactly two pieces of evidence and no learned
    score, so the value is built from those two and nothing else:
      * how far above the noise floor the region is (area against min_area_px) -- a
        blob at the floor is indistinguishable from the 6-22 px clutter the harness
        deliberately plants in the same band;
      * how many frames it survived -- a wave crest does not persist.
    Saturating, floored at 0.30 and capped at 0.90: this detector is never certain and
    the number must not be able to say otherwise."""
    a = min(1.0, area_px / (4.0 * max(min_area_px, 1)))
    f = min(1.0, frames / 15.0)
    return round(0.30 + 0.60 * (0.5 * a + 0.5 * f), 4)


def to_contact(det: dict, *, horizon_y: float, pose: dict, frame_w: int, frame_h: int,
               scale: float, frame_time: datetime, frame_ref: str,
               min_area_px: int = 120) -> dict:''',
    "pi_sensor confidence helper")

rep("04_demo/pi_sensor.py",
'''    length_m = length_sigma = None
    if rng_m is not None:
        f = focal_length_px(frame_w, hfov)
        length_m = det["w"] * rng_m / f
        # Length error inherits range error plus a couple of pixels of box error.
        length_sigma = max(1.0, length_m * math.sqrt(
            (rng_sigma / rng_m) ** 2 + (2.0 / max(det["w"], 1)) ** 2))
''',
'''    length_m = length_sigma = None
    if rng_m is not None:
        f = focal_length_px(frame_w, hfov)
        length_m = det["w"] * rng_m / f
        # Length error inherits range error plus a couple of pixels of box error.
        length_sigma = max(1.0, length_m * math.sqrt(
            (rng_sigma / rng_m) ** 2 + (2.0 / max(det["w"], 1)) ** 2))

    # SCALE IS NOW APPLIED. It was accepted, documented and never read, which meant the
    # tabletop rig emitted TABLE-metres labelled as sea-metres: a 0.4 m model at
    # --scale 20 reported an 0.4 m vessel, so every length check compared a claimed
    # 180 m hull against 0.4 m and returned a colossal, confident, meaningless spoof.
    # Silent corruption, the dd/mm/yyyy class -- no crash, just wrong numbers.
    #
    # Ranges and lengths are LINEAR in the scale factor and scale together. Bearings do
    # NOT: an angle is dimensionless, so scaling it would be wrong. Uncertainties scale
    # with the quantity they qualify, which keeps every sigma ratio -- and therefore
    # every significance lane C computes -- invariant under the choice of scale. That
    # invariance is the property that makes a tabletop rehearsal predict the real run.
    #
    # Default is 1.0, so a real coastal camera is unaffected.
    if scale != 1.0:
        if rng_m is not None:
            rng_m *= scale
            rng_sigma *= scale
        if length_m is not None:
            length_m *= scale
            length_sigma *= scale
''', "pi_sensor scale")

rep("04_demo/pi_sensor.py",
'''        "observed_range_m": round(rng_m, 1) if rng_m else None,
        "range_uncertainty_m": round(rng_sigma, 1) if rng_sigma else None,''',
'''        # `is not None`, not truthiness: contracts.py is explicit that None means
        # "not available" and never means zero. A vessel measured at range 0.0 or with
        # a 0.0 sigma is a real measurement, and `if rng_m` would silently convert it
        # into an absence -- which lane C reads as "uncomparable" rather than "known".
        "observed_range_m": round(rng_m, 1) if rng_m is not None else None,
        "range_uncertainty_m": round(rng_sigma, 1) if rng_sigma is not None else None,''',
    "pi_sensor is-not-None")

rep("04_demo/pi_sensor.py",
'''        "observed_length_m": round(length_m, 2) if length_m else None,
        "observed_length_uncertainty_m": round(length_sigma, 2) if length_sigma else None,''',
'''        "observed_length_m": round(length_m, 2) if length_m is not None else None,
        "observed_length_uncertainty_m": (
            round(length_sigma, 2) if length_sigma is not None else None),
        "detection_confidence": _classical_detection_confidence(
            det.get("area", det["w"] * det["h"]), int(det["frames"]), min_area_px),''',
    "pi_sensor detection_confidence")


# =================================================================== edge_client.py
rep("03_src/edge_client.py",
"POLL_STALE_AFTER_S = 30.0",
'''# PINNED TO server.NODE_STALE_AFTER_S, and the pinning is prose, which is why it broke.
# Lane F moved the server side 30.0 -> 8.0 on 2026-08-29 (four missed 2 s heartbeats;
# at 30 s an unplugged Pi stayed green for half the pitch) and this constant was not
# moved with it. Consequence while they disagreed: a Pi silent for 10 s was OFFLINE to
# the server's watchdog and ONLINE to this poller, so reconcile() returned
# "UP BUT NOT POSTING" and sent the operator hunting a POST-path fault for 22 seconds
# during which the only true fact was that the node was briefly quiet. That is exactly
# the split-brain this module's own docstring says two thresholds cause.
#
# Not imported from server.py deliberately: that would pull FastAPI onto a module that
# must run without it. The durable fix is one constant in contracts.py, which both
# already import; until then this comment is the pin and it must be moved by hand.
POLL_STALE_AFTER_S = 8.0''', "edge_client staleness")


# ========================================================================== app.py
rep("04_demo/app.py",
'''import run_pipeline                          # noqa: E402
from contracts import EoContact              # noqa: E402''',
'''import run_pipeline                          # noqa: E402
import evidence as evidence_mod              # noqa: E402
from contracts import EoContact, EvidenceRecord  # noqa: E402''',
    "app.py imports")

rep("04_demo/app.py",
'''        self.mode = "replay"
        self.last_update: str = ""''',
'''        self.mode = "replay"
        self.last_update: str = ""
        # ONE anonymiser for the whole run, never one per record. Anonymiser numbers by
        # FIRST-SEEN order, so a fresh instance per record allocates index 0 every time
        # and every hull on screen collapses to 999000001 -- correctly anonymised and
        # sixteen vessels merged into one, which looks right and is not.
        self.anon = evidence_mod.Anonymiser()''', "app.py anonymiser")

rep("04_demo/app.py",
'''                body = dict(STATE.result or {})
                body["last_update"] = STATE.last_update''',
'''                body = dict(STATE.result or {})
                # PSEUDONYMISE BEFORE ANYTHING LEAVES THIS PROCESS.
                #
                # run_scene returns records carrying REAL claimed_mmsi / name / imo /
                # callsign, and this handler served them verbatim. server.py closed this
                # hole; app.py did not get the fix -- and app.py is the REHEARSED
                # FALLBACK, the thing you switch to when FastAPI fails at the venue. So
                # the failure mode was: dependency problem on the morning, fall back to
                # the safety net, and the safety net is the one that leaks.
                #
                # Invisible on the synthetic scene because those identities are already
                # 999-prefixed, which is precisely what makes it dangerous: it stays
                # quiet until someone points this at the real Fehmarn golden window and
                # a real named vessel appears on a projector under a SPOOF badge. That
                # is the project's one non-negotiable rule, and it is defamatory.
                #
                # record_to_dict runs assert_no_real_identities() over the SERIALISED
                # payload, so each record served is verified rather than merely
                # processed. A record that fails is dropped, not shipped -- silence is
                # the correct failure direction here.
                scrubbed = []
                for r in body.get("records", []):
                    try:
                        rec = EvidenceRecord.model_validate(r)
                        scrubbed.append(evidence_mod.record_to_dict(
                            rec, anonymiser=STATE.anon)["record"])
                    except Exception as exc:      # noqa: BLE001
                        scrubbed.append({"record_id": r.get("record_id", "?"),
                                         "error": f"withheld: {exc}"})
                body["records"] = scrubbed
                body["identities"] = "PSEUDONYMISED"
                body["last_update"] = STATE.last_update''', "app.py scrub")


# ======================================================================= eo_vlm.py
rep("03_src/eo_vlm.py",
'''def _sigma_for(p: float) -> float:
    """consistency.py's `_probability_to_sigma`, reproduced so this module can report
    what a ceiling implies WITHOUT importing lane C. If lane C changes its mapping this
    report goes stale — that is a deliberate trade against a cross-lane import."""
    import math
    p = min(max(p, 0.5), 0.9999)
    q = 1.0 - p
    if q <= 0.0:
        return 4.0
    t = math.sqrt(-2.0 * math.log(q))
    num = 2.515517 + 0.802853 * t + 0.010328 * t * t
    den = 1.0 + 1.432788 * t + 0.189269 * t * t + 0.001308 * t * t * t
    return max(0.0, t - num / den)''',
'''# Lane C's class-dimension reporting floor, mirrored. Lane C remains the authority and
# this module never imports it; this exists only so the scorer can tell the operator
# what a given ceiling implies. IF LANE C MOVES ITS FLOOR, MOVE THIS.
LANE_C_CLASS_FLOOR_NATS = 3.5


def _sigma_for(p: float) -> float:
    """consistency.py's `_probability_to_sigma`, reproduced so this module can report
    what a ceiling implies WITHOUT importing lane C.

    THIS FUNCTION WAS STALE AND THE STALENESS WAS DANGEROUS, because its output is
    printed to the operator as a safety property.

    Observed: it reproduced the NORMAL QUANTILE. Claimed by lane C: log-odds, since
    2026-08-29, when the normal quantile was found to make the class check
    mathematically incapable of ever firing. Why the mismatch mattered: this module
    told the operator that a 0.95 ceiling yields 1.645 and therefore "no class mismatch
    can fire", while lane C was actually computing ln(0.95/0.05) = 2.944 and firing
    against a 2.0 floor. The console asserted a guarantee the pipeline was not
    honouring -- the worst kind of wrong, because it is reassuring.

    Lane C has since put a dimension-specific floor at 3.5 nats, derived so that the
    0.95 uncalibrated ceiling (2.944) and every confusable pair (max 3.453) sit below
    it. With this function corrected, the report and the pipeline now agree, and the
    inertness claim is true again for the right reason rather than by accident.

    The docstring's original trade-off note stands and is worth keeping: reproducing
    lane C rather than importing it means this report CAN go stale. It did. The cost of
    the alternative -- a cross-lane import that pulls lane C onto every machine running
    the VLM -- was still judged higher."""
    import math
    p = min(max(p, 0.5), 0.999)
    return round(math.log(p / (1.0 - p)), 4)''', "eo_vlm sigma_for")

rep("03_src/eo_vlm.py",
"# See the UNCALIBRATED CONFIDENCE section. 0.95 -> 1.645 sigma, below the 2.0 floor.\nUNCALIBRATED_CONFIDENCE_CEILING = 0.95",
'''# See the UNCALIBRATED CONFIDENCE section. MEASURED under lane C's current log-odds
# mapping: 0.95 -> ln(0.95/0.05) = 2.944 nats, below lane C's 3.5 nat class floor. So an
# uncalibrated model is structurally inert -- it cannot raise a class mismatch, and
# therefore cannot influence a verdict -- until agreement is MEASURED on real crops.
# The older comment here said "1.645 sigma, below the 2.0 floor", which was arithmetic
# from a mapping lane C had already replaced. Both halves were wrong; the conclusion
# happened to survive only because lane C's floor was raised to restore it.
UNCALIBRATED_CONFIDENCE_CEILING = 0.95''', "eo_vlm ceiling comment")

rep("03_src/eo_vlm.py",
'''              f"  -> lane C significance {z:.3f} sigma against its 2.0 floor",''',
'''              f"  -> lane C significance {z:.3f} nats against its "
              f"{LANE_C_CLASS_FLOOR_NATS:.1f} nat class floor",''',
    "eo_vlm report line")


# ---------------------------------------------------------------------- apply
total = 0
for rel, edits in PATCHES.items():
    p = ROOT / rel
    s = p.read_text()
    for old, new, label in edits:
        n = s.count(old)
        if n != 1:
            raise SystemExit(f"ABORT {rel}: anchor '{label}' matched {n} times, want 1")
        s = s.replace(old, new)
    p.write_text(s)
    total += len(edits)
    print(f"  {rel}: {len(edits)} edits")
print(f"OK: {total} anchored edits across {len(PATCHES)} files")
