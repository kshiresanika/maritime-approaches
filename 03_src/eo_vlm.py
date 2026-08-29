"""
eo_vlm.py — the vision-language OBSERVER. Lane B (EO + Geometry).

WHAT THIS FILE ANSWERS
    "What kind of thing does that patch of pixels look like, and how sure is the model?"

Nothing else. It does not decide, rank, verdict, accuse or recommend. It populates two
fields on EoContact — `observed_class` and `observed_class_confidence` — and writes a
provenance record. `consistency.py` compares that observation against the AIS claim,
`verdict.py` decides, `prioritizer.py` ranks. This module is upstream of all of them and
knows about none of them.

01_research/ais_spoofing_methods.md: "A vision-language model can describe an observed
vessel's apparent class and relative size from a frame... This is a legitimate and
well-scoped use — it produces an OBSERVATION, which the deterministic pipeline then
compares against the AIS CLAIM. The model is not deciding anything." This file is that
sentence, implemented, with the "not deciding anything" part ENFORCED rather than hoped
for — see THE HARD RULE below.

--------------------------------------------------------------------------------
THE HARD RULE, AND WHY A PROMPT IS NOT ENOUGH
--------------------------------------------------------------------------------

The model produces an observation. It never decides.

A prompt asking it to behave is a request, not a guarantee. Language models comply with
that request most of the time, which is exactly what makes the failures dangerous: the
one response in fifty that says "this is likely a spoofed tanker" arrives looking
identical to the forty-nine that behaved, and it lands in an evidence record that a
judge — or a court — reads as the system's finding.

So every response passes `_validate_observation()`, which REJECTS, not sanitises:

  * decision and conclusion language  (spoof, dark, suspicious, illegal, threat,
    intercept, recommend, should, alert, verdict, deceptive, ...)
  * any absolute size in metres or feet  (see the circularity trap below)
  * any identity claim  (MMSI, IMO, callsign, 9-digit numbers, quoted proper nouns)
  * any key not in the fixed schema

A rejected response yields `observed_class=None` with a `refusal_reason`, and the raw
text is KEPT in `rejected_text` so the rejection itself is auditable. Silently stripping
the offending words would hide a model that is trying to decide, and that is precisely
the thing an operator needs to know about.

--------------------------------------------------------------------------------
THE CIRCULARITY TRAP — why this module never emits metres
--------------------------------------------------------------------------------

consistency.py's `check_length` states it and puts the guarantee on us:

    "observed_length_m must be derived from pixel extent multiplied by an INDEPENDENTLY
     MEASURED RANGE... If range were instead derived from apparent size and an ASSUMED
     length, then observed_length collapses to the assumed length, this check compares a
     number to itself, and it can never fire. Lane B owns that guarantee; this module
     cannot verify it and has to trust it."

A VLM asked how long a ship is will answer confidently, in metres, from its prior about
what ships of that apparent type are. That number is not a measurement of THIS hull — it
is the model's average of all hulls that look like it. Feed it to `check_length` and the
strongest spoof signal in the system silently becomes a comparison between the AIS claim
and a stereotype.

Therefore: **this module never writes `observed_length_m`, and the validator rejects any
response containing an absolute length.** Length in metres comes from geometry.py —
pixel extent times a waterline-derived range — or it stays None.

What the model MAY report about size is strictly dimensionless and strictly about the
image: what fraction of the crop the hull occupies, and its length-to-height ratio. Those
are observations of pixels, not recalled facts about ship classes.

--------------------------------------------------------------------------------
MEASURED: WHAT CONFIDENCE IS ACTUALLY WORTH, given lane C's arithmetic
--------------------------------------------------------------------------------

Computed by running consistency.py's own `_probability_to_sigma` against its own
thresholds (`min_class_confidence=0.60`, `class_min_report_sigma=3.5` nats,
`confusable_class_discount=0.5`):

        VLM confidence   significance (nats)   does a class mismatch fire?
             0.90                2.197                   no
             0.95                2.944                   no  <- the ceiling
             0.97                3.476                   no
             0.9707              3.500                   threshold
             0.98                3.892                   YES
             0.99                4.595                   YES

    MINIMUM CONFIDENCE FOR A CLASS MISMATCH TO FIRE AT ALL: 0.9707

    These are LOG-ODDS, ln(p/(1-p)), in nats -- not sigmas. Lane C replaced the normal
    quantile on 2026-08-29 because it made the class check mathematically incapable of
    ever firing. This table previously still carried the old sigma figures (0.95 ->
    1.645, threshold 0.9772) against a 2.0 floor, which was the arithmetic of a mapping
    that no longer existed.

And the result that should shape the prompt more than any other:

    A CONFUSABLE PAIR CAN NEVER FIRE. Maximum reachable significance is 6.9068 nats
    (consistency._probability_to_sigma clamps p at 0.999, NOT 0.9999); halved by the
    confusable discount that is 3.4534, against the 3.5 nat class floor -- a margin of
    0.0466. Thin, but a hard structural bound rather than a tuning coincidence: it holds
    for every input the function accepts. cargo-vs-tanker, tug-vs-small_craft, fishing-vs-small_craft,
    tug-vs-fishing and passenger-vs-cargo therefore CANNOT produce a class mismatch at
    any confidence whatsoever.

That is lane C being correct — those silhouettes genuinely are confusable — and it means
effort spent teaching this model to tell a cargo ship from a tanker buys the pipeline
NOTHING. What buys something is separating the groups that are not confusable:
large-commercial vs small-craft vs passenger vs naval. The prompt is built around that,
and the scorer reports agreement on those distinctions separately.

--------------------------------------------------------------------------------
UNCALIBRATED CONFIDENCE IS CAPPED BELOW THE FIRING THRESHOLD
--------------------------------------------------------------------------------

A model's self-reported confidence is not a probability. It is a token sequence that
correlates loosely with correctness and is, for vision models on out-of-distribution
imagery, systematically overconfident.

So until an agreement rate has been MEASURED on hand-labelled crops, every confidence is
capped at `UNCALIBRATED_CONFIDENCE_CEILING = 0.95`, which yields ln(0.95/0.05) = 2.944
nats — below lane C's 3.5 nat class floor. The observation still flows through the pipeline, still appears in the
evidence record, and still cannot raise a class mismatch on its own.

This is the same pattern as geometry.py's `yaw_uncertainty_deg=180.0` for an unsurveyed
pose: make the uncalibrated state STRUCTURALLY inert rather than merely documented.

To lift the cap you must supply a calibration file produced by `--score` against
hand-labelled crops. The ceiling it writes is the MEASURED accuracy, because a
classifier may not claim more confidence than its demonstrated hit rate. There is no
flag to raise the ceiling by hand; the only way through is data.

--------------------------------------------------------------------------------
BUDGET
--------------------------------------------------------------------------------

Qwen3-VL-30B-A3B costs 2 concurrency units of the plan's 4, so AT MOST TWO calls may be
in flight. Enforced by a module-level `BoundedSemaphore(2)`, not by convention.

    WARNING: that budget is per ACCOUNT, not per module. If report.py (lane D) calls the
    same endpoint at the same time, it must acquire THIS semaphore — `from eo_vlm import
    FEATHERLESS_UNITS` — or the account exceeds 4 units and both lanes start taking 429s
    during the demo.

Context is 131K on the primary model and 32K on the fallback. One cropped vessel per
call keeps a request far inside either, which is why crops are per-contact rather than
whole frames — the chunking is for traceability, so each observation cites one bounded
image, not because context runs out.

--------------------------------------------------------------------------------
IMPORT CONVENTION
--------------------------------------------------------------------------------
Flat imports with 03_src on sys.path — LIBRARIES.md.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np

from contracts import EoContact

MODULE_VERSION = "eo_vlm/0.1.0"
PROMPT_VERSION = "observe-vessel/v1"

# --------------------------------------------------------------------------------
# Endpoint and credentials. LIBRARIES.md: project-scoped names, outside the repo, in
# ~/.config/edth-hamburg/edth-hamburg-2026.env. A 401 here almost always means the env
# file was not sourced, NOT that the key is wrong.
# --------------------------------------------------------------------------------

FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"
ENV_KEY = "EDTH_FEATHERLESS_KEY"
ENV_VISION_MODEL = "EDTH_FEATHERLESS_VISION_MODEL"
DEFAULT_VISION_MODEL = "Qwen/Qwen3-VL-30B-A3B-Instruct"

# 2 of the plan's 4 units. Module-level so every caller in this process shares one
# budget. See the BUDGET note in the module docstring before importing this elsewhere.
FEATHERLESS_CONCURRENCY_UNITS = 2
FEATHERLESS_UNITS = threading.BoundedSemaphore(FEATHERLESS_CONCURRENCY_UNITS)

# --------------------------------------------------------------------------------
# Vocabulary. MUST match contracts.VesselClass exactly — a value outside this set is a
# validation failure, not a coercion opportunity. "unknown" is a first-class answer:
# consistency.py suppresses the class check on it, so an honest "I cannot tell" costs
# the pipeline nothing and a guess costs it a false SPOOF.
# --------------------------------------------------------------------------------

VESSEL_CLASSES = ("cargo", "tanker", "fishing", "passenger", "tug", "naval",
                  "small_craft", "unknown")

# Pairs consistency.py discounts by 0.5, which as measured above means they can NEVER
# reach the 2.0 sigma floor. Mirrored here ONLY so the scorer can report on them; lane C
# remains the authority and this module never imports it.
CONFUSABLE_PAIRS = (frozenset({"cargo", "tanker"}), frozenset({"tug", "small_craft"}),
                    frozenset({"fishing", "small_craft"}), frozenset({"tug", "fishing"}),
                    frozenset({"passenger", "cargo"}))

# The coarse groups that CAN fire against each other. Agreement is reported separately
# at this granularity because this is the granularity the pipeline can actually use.
CLASS_GROUP = {"cargo": "large_commercial", "tanker": "large_commercial",
               "passenger": "passenger", "naval": "naval",
               "fishing": "small_craft", "tug": "small_craft",
               "small_craft": "small_craft", "unknown": "unknown"}

# See the UNCALIBRATED CONFIDENCE section. MEASURED under lane C's current log-odds
# mapping: 0.95 -> ln(0.95/0.05) = 2.944 nats, below lane C's 3.5 nat class floor. So an
# uncalibrated model is structurally inert -- it cannot raise a class mismatch, and
# therefore cannot influence a verdict -- until agreement is MEASURED on real crops.
# The older comment here said "1.645 sigma, below the 2.0 floor", which was arithmetic
# from a mapping lane C had already replaced. Both halves were wrong; the conclusion
# happened to survive only because lane C's floor was raised to restore it.
UNCALIBRATED_CONFIDENCE_CEILING = 0.95

# --------------------------------------------------------------------------------
# Crop parameters.
#
# WHY CROP AT ALL: a vessel 20 px wide in a 1920x1080 frame survives the model's own
# downsampling as almost nothing. Session 1 measured exactly this failure mode for the
# detector; it is worse for a VLM, which will still answer, fluently, about a smear.
# --------------------------------------------------------------------------------

CROP_MARGIN_FRACTION = 0.25     # context around the hull: waterline, wake, horizon
MIN_CROP_LONG_EDGE_PX = 48      # below this, refuse. Upscaling adds no information.
UPSCALE_TARGET_LONG_EDGE_PX = 448
JPEG_QUALITY = 90

REQUEST_TIMEOUT_S = 60.0
MAX_ATTEMPTS = 3
MAX_COMPLETION_TOKENS = 300     # the schema below is small; more room invites prose.


# --------------------------------------------------------------------------------
# The observation. NOT a contracts.py type: this is the raw model output plus its
# provenance, and most of it has nowhere to live on EoContact. It is written to a
# sidecar JSONL so that `observed_class` on a contact can always be traced back to the
# model, prompt version, crop and raw text that produced it — which EoContact alone
# cannot express, because it has no field naming the model.
# --------------------------------------------------------------------------------

@dataclass(frozen=True)
class VlmObservation:
    contact_id: str
    observed_class: str | None
    class_confidence_raw: float | None       # what the model said
    class_confidence_effective: float | None # after ceiling/calibration. USE THIS ONE.

    # Dimensionless, about the IMAGE. Never metres. See the circularity trap.
    hull_fraction_of_crop: float | None = None
    length_to_height_ratio: float | None = None
    superstructure_position: str | None = None
    visibility: str | None = None

    model_id: str = ""
    prompt_version: str = PROMPT_VERSION
    crop_ref: str = ""
    crop_px: tuple[int, int] | None = None
    upscaled_from_px: tuple[int, int] | None = None
    latency_ms: float | None = None
    observed_at_utc: str = ""

    refusal_reason: str | None = None
    rejected_text: str | None = None
    raw_response: str | None = None
    calibration_ref: str | None = None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------
# The prompt.
#
# DESIGN PRINCIPLE: constrain the OUTPUT SURFACE, not just the instructions. Every field
# below is a number or a member of a fixed enum. There is no free-text field anywhere in
# the schema, so the model has nowhere to put "this looks like a spoofed tanker" even if
# it wants to. Instructions ask; a closed schema prevents.
#
# The one thing a closed schema cannot stop is the model ignoring the schema entirely
# and returning prose, which is what `_validate_observation` is for.
# --------------------------------------------------------------------------------

SUPERSTRUCTURE_POSITIONS = ("aft", "forward", "midships", "none_visible", "indeterminate")
VISIBILITY_STATES = ("clear", "hazy", "backlit", "partially_occluded", "too_small",
                     "motion_blurred")
VISIBLE_FEATURES = ("bridge_aft", "bridge_forward", "bridge_midships", "deck_cranes",
                    "containers", "manifold_piping", "tall_superstructure",
                    "open_working_deck", "gantry", "mast_array", "low_freeboard",
                    "high_freeboard", "hull_only", "wake_visible", "none_discernible")

_SYSTEM_PROMPT = """You are an image-description component inside a maritime sensor \
pipeline. You describe what is visible in a photograph. You do not interpret, assess, \
conclude, or advise.

You will be shown a cropped photograph that may contain a vessel on water.

Return ONE JSON object and nothing else. No prose before or after it.

Schema, all fields required:
{
  "apparent_class": one of %(classes)s,
  "confidence": number 0.0-1.0,
  "hull_fraction_of_crop": number 0.0-1.0,
  "length_to_height_ratio": number,
  "superstructure_position": one of %(super)s,
  "visibility": one of %(vis)s,
  "visible_features": array of zero or more of %(feat)s
}

Rules you must follow:

1. "unknown" is a CORRECT and PREFERRED answer. If the silhouette is small, blurred, \
backlit, partly out of frame, or simply ambiguous, answer "unknown". A wrong confident \
class is far more costly to this system than an honest "unknown". You are not being \
scored on how often you commit.

2. "confidence" is the probability you would be judged correct if a marine surveyor \
examined this exact image. It is not how plausible your answer sounds. Most real \
photographs of distant vessels do not support high confidence.

3. NEVER state a size in metres, feet or any absolute unit, and never estimate the \
vessel's real-world length. "length_to_height_ratio" is a ratio measured in PIXELS in \
this image, and "hull_fraction_of_crop" is the fraction of the crop's width the hull \
spans. Both describe the picture, not the ship.

4. NEVER identify a specific vessel, operator, flag or nationality. Do not report names, \
MMSI, IMO numbers or callsigns even if they are legible in the image.

5. NEVER assess intent, legality, risk or suspicion, and never recommend an action. You \
are not told what this image is for and you must not guess. Describing is your whole job.

Classes: "cargo" general cargo/container/bulk. "tanker" liquid cargo, manifold piping, \
flush deck. "fishing" trawler/seiner, gantries or booms, working deck aft. "passenger" \
ferry/cruise, tall multi-deck superstructure with window rows. "tug" small, high bow, \
low working deck aft, disproportionately large superstructure. "naval" grey hull, mast \
arrays, no commercial deck equipment. "small_craft" leisure/RIB/launch under roughly a \
tenth the visual bulk of a commercial hull. "unknown" anything else, or not sure.""" % {
    "classes": json.dumps(list(VESSEL_CLASSES)),
    "super": json.dumps(list(SUPERSTRUCTURE_POSITIONS)),
    "vis": json.dumps(list(VISIBILITY_STATES)),
    "feat": json.dumps(list(VISIBLE_FEATURES)),
}

_USER_PROMPT = ("Describe this crop using the schema. Return only the JSON object. "
                "Answer \"unknown\" if you are not sure.")


# --------------------------------------------------------------------------------
# Cropping and encoding.
# --------------------------------------------------------------------------------

def crop_for_contact(frame_bgr: np.ndarray, bbox_px: Sequence[int],
                     margin_fraction: float = CROP_MARGIN_FRACTION):
    """Cut the contact out of the frame with a margin of context.

    Returns (crop_bgr, meta) or (None, meta) when the box is too small to carry any
    silhouette information. The margin is deliberate: waterline, wake and horizon are
    part of what distinguishes a working deck from a container stack, and a box cropped
    tight to the hull removes the very cues the model needs.
    """
    x1, y1, x2, y2 = (int(v) for v in bbox_px)
    h, w = frame_bgr.shape[:2]
    bw, bh = max(x2 - x1, 1), max(y2 - y1, 1)
    mx, my = int(bw * margin_fraction), int(bh * margin_fraction)
    cx1, cy1 = max(0, x1 - mx), max(0, y1 - my)
    cx2, cy2 = min(w, x2 + mx), min(h, y2 + my)
    crop = frame_bgr[cy1:cy2, cx1:cx2]
    meta = {"crop_px": (int(cx2 - cx1), int(cy2 - cy1)),
            "bbox_long_edge_px": int(max(bw, bh))}

    if crop.size == 0 or max(bw, bh) < MIN_CROP_LONG_EDGE_PX:
        # REFUSE rather than upscale. A 20 px vessel carries no silhouette; the model
        # will still answer, fluently, and that answer is a hallucination with a
        # confidence attached. Session 1 measured this exact scale problem for the
        # detector. It is worse here, because the failure is articulate.
        meta["refusal_reason"] = "crop_too_small"
        return None, meta
    return crop, meta


def encode_crop_base64(crop_bgr: np.ndarray) -> tuple[str, dict]:
    """JPEG-encode and base64 a crop, upscaling small ones to the model's patch scale.

    Upscaling adds NO information and is recorded in `upscaled_from_px` for that reason.
    It is done because a very small image can be tokenised into almost nothing, not
    because it improves the evidence.
    """
    meta = {}
    long_edge = max(crop_bgr.shape[:2])
    if long_edge < UPSCALE_TARGET_LONG_EDGE_PX:
        scale = UPSCALE_TARGET_LONG_EDGE_PX / long_edge
        meta["upscaled_from_px"] = (int(crop_bgr.shape[1]), int(crop_bgr.shape[0]))
        crop_bgr = cv2.resize(crop_bgr, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".jpg", crop_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise RuntimeError("cv2 could not JPEG-encode the crop")
    return base64.b64encode(buf.tobytes()).decode("ascii"), meta


# --------------------------------------------------------------------------------
# THE ENFORCEMENT LAYER. See THE HARD RULE in the module docstring.
#
# Two independent nets:
#   1. SCHEMA. Every value must be a number in range or a member of a fixed enum. A
#      conforming response cannot contain a conclusion because there is no field to
#      hold one.
#   2. TEXT SCAN. Run against the RAW response, catching the case where the model
#      ignores the schema and writes prose, or wraps conforming JSON in a paragraph of
#      analysis.
#
# Both REJECT. Neither sanitises. A model that tries to decide must be visible.
# --------------------------------------------------------------------------------

_DECISION_WORDS = re.compile(
    r"\b(spoof\w*|dark\s+vessel|suspicio\w*|suspect\w*|illegal\w*|smuggl\w*|threat\w*|"
    r"intercept\w*|boarding|recommend\w*|advis\w*|alert\w*|verdict|deceptive|deception|"
    r"evasi\w*|evading|anomal\w*|likely\s+(a\s+)?(lie|fake)|falsif\w*|fraud\w*|patrol\w*|"
    r"urgen\w*|investigat\w*|should\s+be\s+\w+|appears\s+to\s+be\s+hiding)\b", re.I)

_ABSOLUTE_SIZE = re.compile(
    r"\d+(\.\d+)?\s*(m|metre|meter|metres|meters|km|kilometre\w*|ft|foot|feet|yards?|"
    r"nm|nautical)\b", re.I)

_IDENTITY_CLAIM = re.compile(
    r"\b(mmsi|imo\s*(number|no\.?)?|call\s?sign|registry|registered\s+in|flagged)\b"
    # A bare 9-digit run is an MMSI. The lookbehind excludes a decimal fraction:
    # a confidence of 0.123456789 is nine digits after a dot and is NOT an identity.
    # Caught by a false-positive test, not by inspection.
    r"|(?<![\d.])\d{9}(?!\d)", re.I)

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


def _scan_text(raw: str) -> str | None:
    """Return a refusal reason if the raw text breaks the hard rule, else None."""
    if _DECISION_WORDS.search(raw):
        return "model_attempted_a_decision"
    if _ABSOLUTE_SIZE.search(raw):
        return "model_asserted_an_absolute_size"   # the circularity trap
    if _IDENTITY_CLAIM.search(raw):
        return "model_asserted_an_identity"
    return None


def _validate_observation(raw: str) -> tuple[dict | None, str | None, list[str]]:
    """Parse and validate. Returns (payload, refusal_reason, notes)."""
    notes: list[str] = []
    reason = _scan_text(raw)
    if reason:
        return None, reason, notes

    text = _FENCE.sub("", raw.strip())
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None, "response_was_not_json", notes
        try:
            payload = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None, "response_was_not_json", notes
        notes.append("response carried text around the JSON object")

    if not isinstance(payload, dict):
        return None, "response_was_not_an_object", notes

    required = {"apparent_class", "confidence", "hull_fraction_of_crop",
                "length_to_height_ratio", "superstructure_position", "visibility",
                "visible_features"}
    if set(payload) != required:
        # Extra keys are a rejection, not a warning: an unexpected key is the model
        # inventing an output channel, and that is exactly what a closed schema exists
        # to prevent.
        missing, extra = sorted(required - set(payload)), sorted(set(payload) - required)
        notes.append("missing=%s extra=%s" % (missing, extra))
        return None, "schema_mismatch", notes

    if payload["apparent_class"] not in VESSEL_CLASSES:
        return None, "class_outside_vocabulary", notes
    if payload["superstructure_position"] not in SUPERSTRUCTURE_POSITIONS:
        return None, "superstructure_outside_vocabulary", notes
    if payload["visibility"] not in VISIBILITY_STATES:
        return None, "visibility_outside_vocabulary", notes
    if not isinstance(payload["visible_features"], list) or \
            any(f not in VISIBLE_FEATURES for f in payload["visible_features"]):
        return None, "feature_outside_vocabulary", notes

    try:
        conf = float(payload["confidence"])
        frac = float(payload["hull_fraction_of_crop"])
        ratio = float(payload["length_to_height_ratio"])
    except (TypeError, ValueError):
        return None, "non_numeric_field", notes
    if not (0.0 <= conf <= 1.0) or not (0.0 <= frac <= 1.0) or not (0.0 < ratio < 100.0):
        return None, "numeric_field_out_of_range", notes

    payload["confidence"], payload["hull_fraction_of_crop"] = conf, frac
    payload["length_to_height_ratio"] = ratio
    return payload, None, notes


# --------------------------------------------------------------------------------
# Calibration. A classifier may not claim more confidence than its measured hit rate.
# --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Calibration:
    ceiling: float
    n_labelled: int
    overall_agreement: float
    source: str
    created_at_utc: str
    per_class: dict = field(default_factory=dict)

    @staticmethod
    def load(path) -> "Calibration":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        known = {f for f in Calibration.__dataclass_fields__}
        return Calibration(**{k: v for k, v in raw.items() if k in known})


def effective_confidence(raw_confidence: float | None,
                         calibration: Calibration | None) -> float | None:
    """Cap a self-reported confidence at what has actually been demonstrated.

    With no calibration the ceiling is UNCALIBRATED_CONFIDENCE_CEILING (0.95), which
    lane C's arithmetic turns into 2.944 nats — below its 3.5 nat class floor. The observation
    therefore reaches the evidence record and CANNOT raise a class mismatch on its own.

    There is deliberately no argument to raise the ceiling by hand. The only way past it
    is a calibration file produced by `--score` on hand-labelled crops, and the ceiling
    it carries is the MEASURED agreement rate.
    """
    if raw_confidence is None:
        return None
    ceiling = calibration.ceiling if calibration else UNCALIBRATED_CONFIDENCE_CEILING
    return round(min(float(raw_confidence), float(ceiling)), 4)


# --------------------------------------------------------------------------------
# The API call. openai SDK against Featherless via base_url — LIBRARIES.md forbids
# hand-rolled HTTP against a chat endpoint.
# --------------------------------------------------------------------------------

def build_client(api_key: str | None = None):
    """Lazy import so this module loads (and the scorer runs) without openai present."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The openai SDK is not installed. `python -m pip install openai`. Do NOT "
            "substitute requests against the chat endpoint — LIBRARIES.md forbids "
            "hand-rolled HTTP here, and the SDK is what handles retries, streaming and "
            "the message content-part shapes.") from exc
    key = api_key or os.environ.get(ENV_KEY)
    if not key:
        raise RuntimeError(
            f"{ENV_KEY} is not set. Run: source ~/.config/edth-hamburg/edth-hamburg-2026.env"
            "\nA 401 or a missing key here is almost always an unsourced env file rather "
            "than a bad key. Never use a bare FEATHERLESS_API_KEY — it is one global slot "
            "and the last project sourced wins.")
    return OpenAI(base_url=FEATHERLESS_BASE_URL, api_key=key, timeout=REQUEST_TIMEOUT_S)


def _retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None)
    if status in (408, 409, 429, 500, 502, 503, 504):
        return True
    return bool(re.search(r"\b(429|timeout|timed out|rate.?limit|overloaded|"
                          r"temporarily|connection)\b", str(exc), re.I))


def _chat_with_image(client, model: str, image_b64: str, notes: list[str]) -> str:
    """One vision call. Holds a concurrency unit ONLY around the request itself."""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": _USER_PROMPT},
            {"type": "image_url",
             # detail="high" because the discriminating cues — deck cranes, manifold
             # piping, window rows — are small. "low" downsamples them away and the
             # model then guesses from overall shape.
             "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "high"}},
        ]},
    ]
    kwargs = dict(model=model, messages=messages,
                  # temperature 0: the same image must yield the same observation, or
                  # an evidence record cannot be reproduced from its inputs.
                  temperature=0.0,
                  max_tokens=MAX_COMPLETION_TOKENS,
                  response_format={"type": "json_object"})

    delay = 1.0
    for attempt in range(MAX_ATTEMPTS):
        try:
            with FEATHERLESS_UNITS:
                resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:                      # noqa: BLE001 - see below
            msg = str(exc)
            # Featherless is OpenAI-COMPATIBLE, not OpenAI. Two parameters are known to
            # vary between compatible servers; degrade rather than fail, and record it,
            # because "we asked for JSON mode and did not get it" changes how much the
            # parse can be trusted.
            if "response_format" in kwargs and "response_format" in msg:
                kwargs.pop("response_format")
                notes.append("server rejected response_format; retried without JSON mode")
                continue
            if "max_tokens" in kwargs and re.search(r"max_(completion_)?tokens", msg):
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                notes.append("server wanted max_completion_tokens instead of max_tokens")
                continue
            if _retryable(exc) and attempt < MAX_ATTEMPTS - 1:
                notes.append(f"retry {attempt + 1} after {type(exc).__name__}")
                time.sleep(delay)                     # sleep OUTSIDE the semaphore
                delay *= 2
                continue
            raise
    raise RuntimeError("exhausted retries without a response")


# --------------------------------------------------------------------------------
# Observing.
# --------------------------------------------------------------------------------

def observe_crop(crop_bgr: np.ndarray, contact_id: str, *, client, model: str,
                 calibration: Calibration | None = None,
                 crop_ref: str = "", crop_meta: dict | None = None) -> VlmObservation:
    """One crop in, one validated observation out. Never raises for model misbehaviour —
    a model that breaks the hard rule produces a REFUSAL, which is itself evidence."""
    crop_meta = dict(crop_meta or {})
    notes: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    b64, enc_meta = encode_crop_base64(crop_bgr)
    crop_meta.update(enc_meta)

    started = time.perf_counter()
    try:
        raw = _chat_with_image(client, model, b64, notes)
    except Exception as exc:                                   # noqa: BLE001
        return VlmObservation(
            contact_id=contact_id, observed_class=None, class_confidence_raw=None,
            class_confidence_effective=None, model_id=model, crop_ref=crop_ref,
            crop_px=crop_meta.get("crop_px"),
            upscaled_from_px=crop_meta.get("upscaled_from_px"),
            latency_ms=(time.perf_counter() - started) * 1000.0, observed_at_utc=now,
            refusal_reason=f"call_failed:{type(exc).__name__}",
            rejected_text=str(exc)[:500], notes=notes)
    latency_ms = (time.perf_counter() - started) * 1000.0

    payload, reason, vnotes = _validate_observation(raw)
    notes += vnotes
    if payload is None:
        return VlmObservation(
            contact_id=contact_id, observed_class=None, class_confidence_raw=None,
            class_confidence_effective=None, model_id=model, crop_ref=crop_ref,
            crop_px=crop_meta.get("crop_px"),
            upscaled_from_px=crop_meta.get("upscaled_from_px"),
            latency_ms=latency_ms, observed_at_utc=now, refusal_reason=reason,
            rejected_text=raw[:2000], raw_response=raw[:2000], notes=notes)

    raw_conf = payload["confidence"]
    return VlmObservation(
        contact_id=contact_id,
        observed_class=payload["apparent_class"],
        class_confidence_raw=raw_conf,
        class_confidence_effective=effective_confidence(raw_conf, calibration),
        hull_fraction_of_crop=payload["hull_fraction_of_crop"],
        length_to_height_ratio=payload["length_to_height_ratio"],
        superstructure_position=payload["superstructure_position"],
        visibility=payload["visibility"],
        model_id=model, crop_ref=crop_ref, crop_px=crop_meta.get("crop_px"),
        upscaled_from_px=crop_meta.get("upscaled_from_px"),
        latency_ms=latency_ms, observed_at_utc=now, raw_response=raw[:2000],
        calibration_ref=(calibration.source if calibration else None),
        notes=notes + [f"visible_features={payload['visible_features']}"])


def observe_contact(frame_bgr: np.ndarray, contact: EoContact, *, client, model: str,
                    calibration: Calibration | None = None) -> VlmObservation:
    """Crop a contact out of its frame and observe it."""
    crop, meta = crop_for_contact(frame_bgr, contact.bbox_px)
    if crop is None:
        return VlmObservation(
            contact_id=contact.contact_id, observed_class=None,
            class_confidence_raw=None, class_confidence_effective=None,
            model_id=model, crop_ref=contact.frame_ref, crop_px=meta.get("crop_px"),
            observed_at_utc=datetime.now(timezone.utc).isoformat(),
            refusal_reason=meta.get("refusal_reason", "crop_unavailable"),
            notes=[f"bbox long edge {meta.get('bbox_long_edge_px')} px is below the "
                   f"{MIN_CROP_LONG_EDGE_PX} px floor; no silhouette to describe"])
    return observe_crop(crop, contact.contact_id, client=client, model=model,
                        calibration=calibration, crop_ref=contact.frame_ref,
                        crop_meta=meta)


def observe_contacts(pairs: Sequence[tuple[np.ndarray, EoContact]], *, client,
                     model: str, calibration: Calibration | None = None
                     ) -> list[VlmObservation]:
    """Batch. AT MOST TWO calls in flight — the model costs 2 of the plan's 4 units.

    The pool is sized to the budget rather than oversubscribed-and-throttled, because a
    thread blocked on the semaphore holds a connection open for nothing and the 429s it
    avoids are indistinguishable, in the logs, from the ones lane D is causing.
    """
    with ThreadPoolExecutor(max_workers=FEATHERLESS_CONCURRENCY_UNITS) as pool:
        return list(pool.map(
            lambda p: observe_contact(p[0], p[1], client=client, model=model,
                                      calibration=calibration), pairs))


def apply_to_contact(contact: EoContact, obs: VlmObservation) -> EoContact:
    """Fold an observation into an EoContact. THE ONLY PLACE THIS MODULE TOUCHES THE
    CONTRACT, and it writes exactly two fields.

    observed_length_m is NOT written and never will be. See the circularity trap: a
    VLM's metres are its prior about vessels of that apparent type, not a measurement of
    this hull, and check_length would then compare an AIS claim against a stereotype.
    Metres come from geometry.py — pixel extent times an independently measured range.

    A model that answered "unknown" gets "unknown" written; a model that failed or was
    REJECTED gets None. contracts.py distinguishes these: None means no observation was
    made, "unknown" means one was made and could not resolve a class. consistency.py
    suppresses the class check on both, so the distinction costs nothing downstream and
    is worth a great deal in the evidence record.
    """
    updates = {"observed_class": obs.observed_class,
               "observed_class_confidence": obs.class_confidence_effective}
    assert "observed_length_m" not in updates, "eo_vlm must never write a length"
    # Rebuild rather than model_copy: model_copy skips validation, and we WANT the
    # contract to check this module's output rather than trust it.
    return EoContact(**{**contact.model_dump(), **updates})


def write_observations(path, observations: Iterable[VlmObservation]) -> None:
    """Sidecar JSONL. EoContact has no field naming the model that classified it, so
    without this the provenance chain breaks exactly where criterion 4 needs it: which
    model, which prompt version, which crop, what it actually said."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for obs in observations:
            fh.write(json.dumps(asdict(obs), default=str) + "\n")


# --------------------------------------------------------------------------------
# Agreement rate against a hand-labelled sample.
#
# This is the only honest statement available about whether the model's class output is
# worth anything, and it is what sets the confidence ceiling.
# --------------------------------------------------------------------------------

def wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> float:
    """95% lower confidence bound on a proportion.

    WHY NOT THE RAW HIT RATE: 18 correct out of 20 is 90%, but the true accuracy could
    easily be 72%. Using the point estimate as a confidence ceiling would let a small
    lucky sample license high-confidence class mismatches against real named vessels.
    The Wilson lower bound makes a SMALL SAMPLE SELF-LIMITING — it yields a low ceiling
    until enough labels exist to earn a high one, which is the behaviour we want from a
    hackathon-sized dataset.

    Wilson rather than the normal approximation because the normal interval is badly
    wrong near p=1, which is precisely where a good classifier sits. No scipy: z is a
    constant and the rest is arithmetic, so this runs anywhere.
    """
    if n <= 0:
        return 0.0
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, centre - half)


def score_against_labels(rows: Sequence[dict], *, client, model: str,
                         labels_path: str) -> tuple[dict, list[VlmObservation]]:
    """Run the model over hand-labelled crops and report how often it agrees.

    `rows` are dicts with image_path and true_class.
    """
    observations, truths = [], []
    for row in rows:
        img = cv2.imread(row["image_path"])
        if img is None:
            raise RuntimeError(f"cv2 could not read {row['image_path']}")
        obs = observe_crop(img, contact_id=row.get("contact_id") or row["image_path"],
                           client=client, model=model, calibration=None,
                           crop_ref=row["image_path"],
                           crop_meta={"crop_px": (img.shape[1], img.shape[0])})
        observations.append(obs)
        truths.append(row["true_class"])

    scored = [(o, t) for o, t in zip(observations, truths) if t in VESSEL_CLASSES]
    answered = [(o, t) for o, t in scored if o.observed_class not in (None, "unknown")]
    refused = [o for o in observations if o.refusal_reason]

    exact = sum(1 for o, t in answered if o.observed_class == t)
    group = sum(1 for o, t in answered
                if CLASS_GROUP.get(o.observed_class) == CLASS_GROUP.get(t))
    n_ans = len(answered)

    # Calibration by confidence bin: does a stated 0.9 mean 90%?
    bins: dict[str, dict] = {}
    for lo in (0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99):
        hi = {0.0: 0.5, 0.5: 0.7, 0.7: 0.8, 0.8: 0.9, 0.9: 0.95, 0.95: 0.99, 0.99: 1.01}[lo]
        sel = [(o, t) for o, t in answered
               if o.class_confidence_raw is not None and lo <= o.class_confidence_raw < hi]
        if sel:
            bins[f"{lo:.2f}-{hi:.2f}"] = {
                "n": len(sel),
                "accuracy": round(sum(1 for o, t in sel if o.observed_class == t) / len(sel), 4)}

    # Which disagreements would lane C have suppressed anyway?
    confusable_errors = sum(1 for o, t in answered if o.observed_class != t
                            and frozenset({o.observed_class, t}) in CONFUSABLE_PAIRS)

    ceiling = wilson_lower_bound(exact, n_ans) if n_ans else 0.0
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": labels_path,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "n_crops": len(observations),
        "n_labelled": len(scored),
        "n_answered": n_ans,
        "n_abstained_unknown": len(scored) - n_ans,
        "n_refused_by_validator": len(refused),
        "refusal_reasons": sorted({o.refusal_reason for o in refused if o.refusal_reason}),
        "exact_agreement": round(exact / n_ans, 4) if n_ans else None,
        "group_agreement": round(group / n_ans, 4) if n_ans else None,
        "errors_that_are_confusable_pairs": confusable_errors,
        "calibration_bins": bins,
        "ceiling": round(ceiling, 4),
        "overall_agreement": round(exact / n_ans, 4) if n_ans else 0.0,
        "confusion": _confusion(answered),
    }
    return report, observations


def _confusion(answered: Sequence[tuple[VlmObservation, str]]) -> dict:
    out: dict[str, dict[str, int]] = {}
    for o, t in answered:
        out.setdefault(t, {}).setdefault(o.observed_class or "none", 0)
        out[t][o.observed_class or "none"] += 1
    return out


def format_score_report(rep: dict) -> str:
    """Print it the way it must be read: the ceiling first, and what it implies."""
    z = _sigma_for(rep["ceiling"])
    lines = ["=" * 74,
             "VLM CLASS AGREEMENT — MEASURED against hand labels",
             "=" * 74,
             f"labels        : {rep['source']}",
             f"model         : {rep['model']}  prompt {rep['prompt_version']}",
             f"crops         : {rep['n_crops']}  labelled {rep['n_labelled']}  "
             f"answered {rep['n_answered']}  abstained-unknown {rep['n_abstained_unknown']}",
             f"validator refusals : {rep['n_refused_by_validator']} "
             f"{rep['refusal_reasons'] or ''}",
             "-" * 74,
             f"EXACT class agreement : {_pct(rep['exact_agreement'])}",
             f"GROUP agreement       : {_pct(rep['group_agreement'])}   "
             "(large_commercial / passenger / naval / small_craft)",
             f"errors that are confusable pairs : {rep['errors_that_are_confusable_pairs']}"
             "  <- lane C would have suppressed these anyway",
             "-" * 74,
             "calibration — does a stated confidence mean what it says?",
             f"{'stated band':>14} {'n':>5} {'actual accuracy':>17}"]
    for band, b in sorted(rep["calibration_bins"].items()):
        lines.append(f"{band:>14} {b['n']:>5} {b['accuracy']:>17.0%}")
    lines += ["-" * 74,
              f"CONFIDENCE CEILING (Wilson 95% lower bound on exact agreement): "
              f"{rep['ceiling']:.4f}",
              f"  -> lane C significance {z:.3f} nats against its "
              f"{LANE_C_CLASS_FLOOR_NATS:.1f} nat class floor",
              f"  -> a class mismatch "
              f"{'CAN' if z >= LANE_C_CLASS_FLOOR_NATS else 'CANNOT'} fire with "
              f"this sample",
              ""]
    # BOTH comparisons below test NATS against lane C's NATS floor. They previously
    # tested against 2.0 -- the old SIGMA floor, from a mapping lane C had already
    # replaced -- so this report could tell an operator a class mismatch CAN fire while
    # consistency.check_class was still suppressing it. A console that asserts the
    # opposite of the pipeline is worse than a console that says nothing.
    if z < LANE_C_CLASS_FLOOR_NATS:
        # p required to clear the floor: 1/(1+e^-3.5) = 0.9707, NOT the old 0.9772.
        need = _n_needed_for_ceiling(_CLASS_FIRING_PROBABILITY)
        lines += [
            "THAT IS THE CORRECT OUTCOME, NOT A FAILURE. It means the class observation",
            "is too weak to accuse anyone on its own, which is what lane C intends:",
            "silhouette classification is the weakest evidence in the system and the",
            "arithmetic says so. The observation still reaches the evidence record, still",
            "discounts observation_confidence on every OTHER mismatch for this contact,",
            "and still describes the contact for a human reader.",
            "",
            f"To license class mismatches you would need about {need} labelled crops at",
            "100% agreement. The alternative lever — lowering min_report_sigma for the",
            "class dimension — lives in consistency.py and is LANE C's decision, not",
            "lane B's. File a request; do not edit their file.",
            ""]
    lines.append("=" * 74)
    return "\n".join(lines)


def _pct(v):
    return "n/a" if v is None else f"{v:.1%}"


# Lane C's class-dimension reporting floor, mirrored. Lane C remains the authority and
# this module never imports it; this exists only so the scorer can tell the operator
# what a given ceiling implies. IF LANE C MOVES ITS FLOOR, MOVE THIS.
LANE_C_CLASS_FLOOR_NATS = 3.5

# The confidence a class observation must reach before it can raise a mismatch at all,
# derived from the floor above rather than written down beside it: p = 1/(1+e^-3.5).
# Deriving it means the two cannot drift apart, which is how the previous pair (2.0 and
# 0.9772) survived a change to the mapping underneath them.
import math as _math
_CLASS_FIRING_PROBABILITY = 1.0 / (1.0 + _math.exp(-LANE_C_CLASS_FLOOR_NATS))


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
    return round(math.log(p / (1.0 - p)), 4)


def _n_needed_for_ceiling(target: float) -> int:
    """Smallest perfect-score sample whose Wilson lower bound reaches `target`."""
    n = 1
    while n < 100_000:
        if wilson_lower_bound(n, n) >= target:
            return n
        n += 1
    return -1


# --------------------------------------------------------------------------------
# CLI. Three jobs: cut crops to label, measure agreement, run the observer.
# --------------------------------------------------------------------------------

def _load_contacts(path) -> list[EoContact]:
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(EoContact.model_validate_json(line))
    return out


def _find_frame(frames_dir: Path, frame_ref: str) -> Path | None:
    """Resolve an EoContact.frame_ref to a file on disk.

    eo_detector writes either a path (image-folder source) or "clip.mov#000123"
    (video source). For the second, extract frames first:
        ffmpeg -i clip.mov -vsync 0 frames/clip_%06d.jpg
    and this will match on the six-digit index.
    """
    direct = Path(frame_ref)
    if direct.is_file():
        return direct
    cand = frames_dir / direct.name
    if cand.is_file():
        return cand
    if "#" in frame_ref:
        idx = frame_ref.rsplit("#", 1)[1]
        hits = sorted(frames_dir.glob(f"*{idx}*"))
        if hits:
            return hits[0]
    return None


def cmd_make_crops(args) -> int:
    contacts = _load_contacts(args.contacts)
    frames_dir, out_dir = Path(args.frames_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache: dict[str, np.ndarray] = {}
    written, skipped = [], []
    for c in contacts:
        fp = _find_frame(frames_dir, c.frame_ref)
        if fp is None:
            skipped.append((c.contact_id, "frame not found"))
            continue
        if str(fp) not in cache:
            img = cv2.imread(str(fp))
            if img is None:
                skipped.append((c.contact_id, "frame unreadable"))
                continue
            cache[str(fp)] = img
        crop, meta = crop_for_contact(cache[str(fp)], c.bbox_px)
        if crop is None:
            skipped.append((c.contact_id, meta.get("refusal_reason", "too small")))
            continue
        dest = out_dir / f"{c.contact_id}.jpg"
        cv2.imwrite(str(dest), crop, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        written.append((str(dest), c.contact_id))

    labels = out_dir / "labels_TEMPLATE.csv"
    with open(labels, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["image_path", "contact_id", "true_class"])
        for path, cid in written:
            w.writerow([path, cid, ""])
    print(f"{len(written)} crops -> {out_dir}")
    print(f"{len(skipped)} skipped: {skipped[:8]}{' ...' if len(skipped) > 8 else ''}")
    print(f"\nLabel template: {labels}")
    print("Fill true_class BY EYE, one of: " + ", ".join(VESSEL_CLASSES))
    print("Label what YOU can see in the crop, not what AIS says — an AIS-derived label")
    print("would make the agreement rate measure agreement with the claim we are trying")
    print("to test, which is circular and would read as 100% for a spoofed vessel.")
    print("Use 'unknown' freely; a crop you cannot classify is data, not a gap.")
    return 0


def cmd_score(args) -> int:
    rows = []
    with open(args.labels, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("true_class") or "").strip():
                row["true_class"] = row["true_class"].strip()
                rows.append(row)
    if not rows:
        raise SystemExit(f"No labelled rows in {args.labels}. Fill true_class first.")
    model = args.model or os.environ.get(ENV_VISION_MODEL, DEFAULT_VISION_MODEL)
    rep, obs = score_against_labels(rows, client=build_client(), model=model,
                                    labels_path=args.labels)
    print(format_score_report(rep))
    if args.out_calibration:
        with open(args.out_calibration, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
        print(f"calibration written: {args.out_calibration}")
    if args.out_observations:
        write_observations(args.out_observations, obs)
        print(f"observations written: {args.out_observations}")
    return 0


def cmd_observe(args) -> int:
    contacts = _load_contacts(args.contacts)
    frames_dir = Path(args.frames_dir)
    model = args.model or os.environ.get(ENV_VISION_MODEL, DEFAULT_VISION_MODEL)
    calibration = Calibration.load(args.calibration) if args.calibration else None
    if calibration is None:
        print(f"NO CALIBRATION FILE. Confidence capped at "
              f"{UNCALIBRATED_CONFIDENCE_CEILING} -> "
              f"{_sigma_for(UNCALIBRATED_CONFIDENCE_CEILING):.3f} nats "
              f"(floor {LANE_C_CLASS_FLOOR_NATS}). "
              "No class mismatch can fire. This is by design; run --score to earn a "
              "higher ceiling.")

    cache: dict[str, np.ndarray] = {}
    pairs, unresolved = [], []
    for c in contacts:
        fp = _find_frame(frames_dir, c.frame_ref)
        if fp is None:
            unresolved.append(c.contact_id)
            continue
        if str(fp) not in cache:
            img = cv2.imread(str(fp))
            if img is None:
                unresolved.append(c.contact_id)
                continue
            cache[str(fp)] = img
        pairs.append((cache[str(fp)], c))

    observations = observe_contacts(pairs, client=build_client(), model=model,
                                    calibration=calibration)
    by_id = {o.contact_id: o for o in observations}
    enriched = [apply_to_contact(c, by_id[c.contact_id]) if c.contact_id in by_id else c
                for c in contacts]

    if args.out_contacts:
        with open(args.out_contacts, "w", encoding="utf-8") as fh:
            for c in enriched:
                fh.write(c.model_dump_json() + "\n")
        print(f"contacts written: {args.out_contacts}")
    if args.out_observations:
        write_observations(args.out_observations, observations)
        print(f"observations written: {args.out_observations}")

    classed = sum(1 for o in observations if o.observed_class not in (None, "unknown"))
    unk = sum(1 for o in observations if o.observed_class == "unknown")
    ref = sum(1 for o in observations if o.refusal_reason)
    lat = [o.latency_ms for o in observations if o.latency_ms]
    print(f"\n{len(observations)} observed | {classed} classed | {unk} unknown | "
          f"{ref} refused | {len(unresolved)} frames unresolved")
    if ref:
        from collections import Counter
        print("refusals:", dict(Counter(o.refusal_reason for o in observations
                                        if o.refusal_reason)))
    if lat:
        lat.sort()
        print(f"latency ms: min {lat[0]:.0f} median {lat[len(lat)//2]:.0f} max {lat[-1]:.0f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="eo_vlm.py",
        description="Lane B: a VLM that OBSERVES a vessel's apparent class. It never decides.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("make-crops", help="cut crops from contacts + frames, for labelling")
    p.add_argument("--contacts", required=True, help="eo_detector <run>_contacts.jsonl")
    p.add_argument("--frames-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=cmd_make_crops)

    p = sub.add_parser("score", help="measure agreement against hand labels")
    p.add_argument("--labels", required=True, help="CSV: image_path,contact_id,true_class")
    p.add_argument("--model", default=None)
    p.add_argument("--out-calibration", default=None)
    p.add_argument("--out-observations", default=None)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("observe", help="run the observer over a contacts file")
    p.add_argument("--contacts", required=True)
    p.add_argument("--frames-dir", required=True)
    p.add_argument("--model", default=None)
    p.add_argument("--calibration", default=None,
                   help="calibration JSON from `score`. Without it, confidence is "
                        "capped below the firing threshold.")
    p.add_argument("--out-contacts", default=None)
    p.add_argument("--out-observations", default=None)
    p.set_defaults(func=cmd_observe)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
