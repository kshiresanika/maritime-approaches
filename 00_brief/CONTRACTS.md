# CONTRACTS — read this before you write a line of code

The five-minute version of `03_src/contracts.py`. If you read nothing else, read
section 1.

---

## 1. The wall between CLAIMED and OBSERVED

```
   AIS transponder                          Camera
        |                                     |
        v                                     v
   +----------+                        +-------------+
   | AisTrack |                        |  EoContact  |
   | claimed_*|                        | observed_*  |
   |  ONLY    |                        |    ONLY     |
   +----------+                        +-------------+
        \                                    /
         \          +--------------+        /
          +-------->| Association  |<------+     which side is None IS the finding
                    +--------------+
                           |
                           v
                    +--------------+
                    |   Mismatch   |   one claimed field vs one observed field,
                    | claimed_value|   both literal values carried, plus the unit
                    |observed_value|
                    +--------------+
                           |
                           v
                    +--------------+
                    |   Verdict    |   MATCH | DARK | SPOOF | UNKNOWN
                    | + confidence |   DETERMINISTIC. Never LLM-written.
                    |+defer_to_human|
                    +--------------+
                           |
                +----------+----------+
                v                     v
        +---------------+     +-----------------+
        | PriorityScore |     | EvidenceRecord  |
        | rank + weights|     | + rationale_text|  <- the LLM writes ONLY this
        +---------------+     +-----------------+
```

**An observed value never lives on `AisTrack`. A claim never lives on `EoContact`.**
`EoContact` has no MMSI, no name, no IMO — a camera cannot observe an identity.

This is not tidiness. It is criterion 3. A vessel with its transponder switched off is
easy: an observation with no claim. A vessel **broadcasting a false identity** is only
detectable if the claim and the observation are held apart long enough to be compared.
Merge them into one "vessel" object and the spoofing detector cannot exist — and it
will not fail loudly, it will simply never fire.

`extra="forbid"` on every model means a smuggled field raises immediately instead of
disappearing into an ignored attribute.

---

## 2. Unit conventions — fixed, and encoded in every field name

| Quantity | Rule | Suffix |
|---|---|---|
| Angles | **Degrees**, never radians, at any boundary | `_deg` |
| Bearings, course, heading | **Degrees TRUE**, 0–360 clockwise from true north. Magnetic never appears; apply declination at ingest | `_deg_true` |
| Relative bearing | **Separate field**, signed −180..180 from boresight | `_rel_deg` |
| Angular difference | **Signed shortest arc**, −180..180. 359° vs 1° is **+2°** | — |
| Distance | **Metres** internally | `_m` |
| Distance, human-facing | **Nautical miles** — only in `EvidenceRecord.range_nm` | `_nm` |
| Speed | **m/s** internally; knots only where AIS gives them | `_ms` / `_kn` |
| Position | WGS84 signed decimal degrees, float. N and E positive | `_lat_deg` / `_lon_deg` |
| Time | **Timezone-aware UTC.** Naive datetimes are rejected at the boundary | `_utc` |
| Confidence, score | Unitless float **0.0–1.0**. Never a percentage | — |
| Identifiers | **Strings** — MMSI, IMO included. Leading zeros are significant | — |

**`None` means "not available".** It never means zero, and it never means "no
mismatch". Absent SOG is `None`; a stopped vessel is `0.0`. A missing claim is itself
a finding and belongs in `EvidenceRecord.limitations`, not silently in the pass pile.

No range constraint can catch a radians-for-degrees bug — 3.14 is a valid bearing.
The field-name convention and review are the only defences. That is why every angular
field carries its unit in its name.

---

## 3. The seven objects

| Object | Owner | Carries |
|---|---|---|
| **AisTrack** | A | Claimed identity, position, kinematics, dimensions. `mobile_class` too — a blank ship type is *normal* on Class B and *suspicious* on Class A |
| **EoContact** | B | Bearing (+ uncertainty), rough range, apparent class + confidence, apparent size, `frame_ref`, `camera_pose_ref`, `track_length_frames` |
| **Association** | C | The pairing. `track_id=None` → **DARK**. `contact_id=None` → **position spoof**. Plus `assoc_ambiguous`, `time_delta_s`, `candidate_count` |
| **Mismatch** | C | One claimed field vs one observed field: both values, both field names, `unit`, `delta`, `tolerance`, `significance`, `explained_by_staleness` |
| **Verdict** | C | `MATCH \| DARK \| SPOOF \| UNKNOWN` + `confidence` + `defer_to_human` + `defer_reasons` + `ruleset_version` |
| **PriorityScore** | D | `rank`, `score`, and **`component_scores` + `component_weights`** — the weighting *is* the argument |
| **EvidenceRecord** | D | Embedded snapshots of everything above, `identity_claimed`, `frame_refs`, `rationale_text` + `rationale_model`, `limitations` |

### Three things worth knowing

**`UNKNOWN` is not a failure state, it is the product.** It is the machine-readable
"I do not know". Without it, a pipeline under pressure has to guess between MATCH and
SPOOF, and a confident wrong answer is worse than an honest abstention. Attribution in
this domain is legally fragile — cases have failed and suspect vessels have been
released. `UNKNOWN` + `defer_reasons` is what keeps our output defensible.

**`significance`, not `delta`, is what makes a spoof.** A 200 m length discrepancy is
nothing if the range estimate carries a 180 m error bar. `significance` is the delta
over the combined uncertainty, in sigmas. It is what separates a spoof from a fuzzy
monocular estimate, and it is what a judge will ask about.

**`explained_by_staleness` must be checked before the verdict.** A position delta
consistent with `time_delta_s × claimed_sog_kn` is a stale AIS report, not deception.
Skip this and the demo calls latency a spoof.

Every model is **frozen**. Build a new object rather than mutating one — a record
pointing at a mutable verdict is not evidence.

---

## 4. Start now, without waiting for real data

`tests/fixtures.py` emits contract-shaped synthetic tracks and contacts:

```python
from fixtures import (
    make_ais_track, make_eo_contact,
    scenario_match, scenario_dark, scenario_identity_spoof,
    scenario_ambiguous_association,
    make_association, make_mismatch, make_verdict,
    make_priority_score, make_evidence_record,
)

track, contact = scenario_identity_spoof()   # claims "fishing, 40 m"; camera sees
                                             # a tanker at ~250 m. Criterion 3.
```

Deterministic — same seed, same vessels, every run. Every MMSI is `999`-prefixed
(never issued to a real vessel) and every name starts with `SYNTH-`, so nothing built
on fixtures can accidentally accuse a real ship. Coordinates sit in the Fehmarn Belt
box, so fixture output plots on the same map as real data.

**Lanes C and D are not blocked on A and B.** If C is idle waiting for data, that is a
bug in the fixtures — tell ARCH.

Run the guardrails: `pytest tests/test_contracts.py` — 49 tests, currently green.
They assert the claimed/observed wall, the closed vocabularies, the unit ranges,
frozen-ness, JSON round-tripping, and — structurally — that `Verdict` has nowhere to
put model-generated text.

---

## 5. Changing a contract

`contracts.py` is **ARCH-owned**. A field change breaks four modules at once.

Append to `99_scratch/requests.md`:

```
### <UTC timestamp> | <your lane> -> ARCH | 03_src/contracts.py
WANT:   the concrete change
WHY:    what breaks without it — cause and effect, not preference
IMPACT: which modules change if it lands
STATUS: OPEN
```

Then **say it out loud**. A contracts request is an interrupt, not a queue item —
four modules stall behind it.

Adding an **optional** field with a `None` default is cheap and safe. Renaming or
retyping an existing field is not: it breaks every lane silently at import time. Bias
towards adding.

---

## 6. What is deliberately not here

`claimed_draught_m`, `claimed_destination`, `claimed_eta`, `claimed_cargo_type` exist
in the DMA CSV but are not in `AisTrack`. They serve route-deviation and loading
checks, which are not on the critical path for any of the four criteria. If your lane
needs one, file a request — adding an optional field is a two-minute change.
