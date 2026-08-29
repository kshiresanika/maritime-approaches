# REQUESTS — cross-lane change requests

Why this file exists: FILE OWNERSHIP means you may not edit another lane's file. This is
the queue instead. Append at the bottom; never edit someone else's entry. The owning lane
resolves it and marks the outcome.

Format:

```
### <UTC timestamp> | <requesting lane> -> <owning lane> | <file>
WANT:   what change, concretely.
WHY:    what breaks or cannot be built without it — cause and effect, not preference.
IMPACT: which modules change if this lands.
STATUS: OPEN | DONE <timestamp> | REJECTED <reason>
```

---

### 2026-08-27 08:05 | ARCH -> ARCH | 03_src/contracts.py, 03_src/LIBRARIES.md
WANT:   Both files exist and are populated before any lane writes code.
WHY:    CLAUDE.md makes both load-bearing — "all cross-module types come from
        contracts.py" and "check LIBRARIES.md before writing any algorithm" — but
        neither file is present in the repo. Cause and effect: with no contracts.py,
        the first two lanes to start will each invent their own contact/track dicts,
        and the association step then has to reconcile two incompatible shapes at the
        worst possible time. With no LIBRARIES.md, someone hand-rolls NMEA parsing or
        great-circle geodesy at 02:00 and it silently loses precision.
IMPACT: Every lane. This is the gate on the first commit, not a nice-to-have.
STATUS: OPEN

### 2026-08-27 14:10 | ARCH -> ARCH | 00_brief/constraints.md
WANT:   Correct constraint 12. It currently reads "32K context ceiling on Featherless".
WHY:    Featherless's own model-compatibility doc states "All models are served at one of
        4k, 8k or 16k context size", with named exceptions (DeepSeek V3/R1 at 32k, a few
        very large models far higher). The plan tier also caps context: Featherless Chat
        is "up to 32K", Developer "up to 256K" — but the PER-MODEL serving limit binds
        first, and for a mainstream Qwen/Llama fine-tune that is likely 16k or less.
        Cause and effect: a chunking strategy sized for 32K will silently truncate or
        error at 16k. Truncation is the dangerous one — the model still answers, the
        rationale still looks fluent, and the AIS window it was reasoning over was
        quietly cut in half. That is a calibrated-confidence failure, i.e. criterion 4.
IMPACT: ais_ingest (window sizing), evidence/report (prompt assembly), and any pitch
        claim about how much traffic the tool reasons over at once.
ACTION: Confirm the served context of the CHOSEN model id from the /v1/models record
        before sizing any window. Do not carry the 32K number forward unverified.
STATUS: RESOLVED 2026-08-27 14:40 — MEASURED, and the concern was WRONG for our model.
        /v1/models reports Qwen/Qwen3-VL-30B-A3B-Instruct at context_length 131072 with
        max_completion_tokens 32768. The docs' "4k/8k/16k" line does not apply to it.
        Corrected guidance: constraint 12's 32K figure is too CONSERVATIVE for the chosen
        model, not too generous. Window sizing is not the binding limit; token cost and
        latency are. Still size windows per-contact rather than per-corpus, for evidence
        traceability — each rationale should cite a bounded, quotable window — not because
        the context cannot hold more. Fallback Qwen/Qwen2.5-VL-72B-Instruct IS 32768, so
        any chunking design must survive 32K to keep the fallback usable.

### 2026-08-28T12:40Z | B -> ARCH | 03_src/contracts.py
WANT: One new required field on `EoContact`:

    detection_confidence: float = Field(
        ge=0.0, le=1.0,
        description="The EO detector's confidence that this box IS A VESSEL. Distinct "
                    "from observed_class_confidence, which is confidence in WHICH "
                    "class it is. YOLO answers the first question and cannot answer "
                    "the second.")

WHY: `EoContact` currently has nowhere to put the detector's confidence, and
`extra="forbid"` means lane B cannot smuggle it through. The only near-fit is
`observed_class_confidence`, but that is a different quantity. YOLO/COCO has a single
`boat` class — it can say "that is a vessel, 0.83" and it categorically cannot say
"that is a tanker". Writing 0.83 into `observed_class_confidence` asserts a class
confidence that was never computed, and lane C then weights a DARK verdict using a
number that does not mean what its field name says. The failure is silent: no
exception, just a mis-calibrated confidence on the criterion-4 output.

Without the field, lane B must emit `observed_class=None` AND drop the detection
confidence entirely, so `verdict.py` has no evidence quality signal for a dark contact
and every DARK confidence collapses to a constant.

IMPACT: `03_src/contracts.py` (one field), `tests/fixtures.py` (EoContact factory needs
the new required arg), `tests/test_contracts.py` (one assertion), `03_src/eo_detector.py`
(lane B, already written to expect it), `03_src/verdict.py` (lane C MAY use it; not
required to). Purely additive — no existing field changes meaning.

NOTE: `observed_class` stays `None` out of lane B. The VLM (Qwen3-VL) fills it later;
COCO cannot.
STATUS: RESOLVED 2026-08-29. Applied as a REQUIRED field by Amol (ARCH) with the agent
acting on his explicit authorisation in-session. contracts.py + tests/fixtures.py edited
as requested. Blast radius was larger than this request stated: see the lane D entry
below.

---

### 2026-08-28 14:48 UTC | A -> ARCH | .gitignore
WANT: exclude the real-identity golden windows, keeping the pseudonymised ones:
```
# Golden windows: commit the pseudonymised variant ONLY.
02_data/golden/*.csv
!02_data/golden/*_anon.csv
```
WHY: `02_data/make_golden_window.py` writes three files per window into a NEW directory
`02_data/golden/` — `<name>.csv` (real MMSIs, names, IMOs, callsigns, destinations),
`<name>_anon.csv` (pseudonymised), and `<name>_mmsi_map.csv` (reverses the
pseudonymisation). `.gitignore` currently names `02_data/raw/` and `02_data/slices/`
and knows nothing about `02_data/golden/`, so as it stands the first `git add` commits
1,198 real vessel identities into a public repo. That breaches CLAUDE.md's hard rule on
naming real vessels, and it redistributes DMA data under terms that do not address
redistribution (02_data/INVENTORY.md §2). This is the same failure mode as the
`02_data/slices/` negation caught on 2026-08-28 — a gitignore that does not know about
a directory that did not exist when it was written.

NOTE ON THE NEGATION: it must be `02_data/golden/*.csv` and not `02_data/golden/`.
git cannot re-include a file below an ignored DIRECTORY, so ignoring the directory
would silently swallow the `!` line and nothing would be committed. Please confirm with
`git check-ignore -v 02_data/golden/fehmarn_2026-08-25_1000-1100_anon.csv` returning
nothing after the edit.
IMPACT: `.gitignore` only. No code changes.
STATUS: OPEN

### 2026-08-28 14:48 UTC | A -> ARCH | tests/test_ais_ingest.py
WANT: acknowledgement of a new file added under `tests/`, which FILE_OWNERSHIP.md
lists as ARCH-owned.
WHY: lane A's module needs a regression net and the sys.path convention lives in
`tests/conftest.py`, so the tests have to sit beside it. FILE_OWNERSHIP.md enumerates
`conftest.py`, `fixtures.py` and `test_contracts.py` by name rather than claiming the
whole directory, so this is read as lane A's file inside ARCH's directory. Say if you
want it elsewhere. It touches none of the three existing files.
IMPACT: one new file. 24 test functions. No existing test changes.
STATUS: OPEN

### 2026-08-28 14:48 UTC | A -> C | 03_src/consistency.py
WANT: `VesselClass == "unknown"` on EITHER side must SUPPRESS the class comparison —
never satisfy it, never fail it. It is a third state, not a value.
WHY: `ais_ingest.DMA_SHIP_TYPE_TO_CLASS` maps AIS service-craft types with no honest
VesselClass equivalent — HSC, Pilot, SAR, Port tender, Law enforcement, Dredging,
Diving, Other, Reserved — onto `"unknown"`, meaning "a claim was made and it is not
comparable to a camera silhouette". That is 355,000 of 1,198,373 rows in the Fehmarn
slice (29.6%). The alternative was mapping by resemblance (HSC -> passenger, and so
on), which would have manufactured SPOOF verdicts inside lane A, invisible to the
report and against a real named hull.

If consistency.py treats `"unknown"` as an ordinary value, then a VLM that reads a
pilot boat as `small_craft` produces `claimed=unknown` vs `observed=small_craft` and
raises a class mismatch whose true origin is a mapping table in a different lane. If it
treats it as a wildcard MATCH, the opposite: a genuine class spoof on a service craft
never fires. Both are silent.

Note the separate distinction: `None` means the transponder claimed NOTHING (AIS type
0, "Undefined", 31,355 rows), which is a different finding from claiming something
uninformative. `mobile_class` is the discriminator for whether a blank is suspicious —
a blank ship type is NORMAL on Class B and SUSPICIOUS on Class A.
IMPACT: `03_src/consistency.py` — the class-dimension branch. No contract change.
STATUS: OPEN

### 2026-08-28 14:48 UTC | A -> C | 03_src/consistency.py, 03_src/verdict.py
WANT: kinematic track-breaking for repeated MMSIs, and an explicit decision that lane C
owns it.
WHY: `ais_ingest.make_track_id()` derives `track_id` from (source, MMSI). Two hulls
broadcasting the SAME MMSI — the textbook identity spoof, and the exact case criterion
3 says is the discriminator — therefore collapse into ONE `track_id`, with their
positions interleaved into a single impossible trajectory. Nothing raises. The track
just teleports.

Lane A cannot split them: doing so requires implied-speed and teleport tests between
consecutive reports, which is the same machinery as the kinematic spoof subtype and
belongs where that logic lives. If it were done in lane A it would be a second,
divergent implementation of lane C's core test.

Until it exists: **downstream must not read "one track_id" as "one vessel".** A
teleporting track is currently indistinguishable from a badly-behaved GPS.
IMPACT: `03_src/consistency.py` (kinematic branch), `03_src/verdict.py`
(`spoof_subtype="kinematic"`). Possibly a track-segmentation step before association.
No contract change — `SpoofSubtype` already has `"kinematic"`.
STATUS: OPEN

### 2026-08-28 14:48 UTC | A -> C, D | verdict.py, prioritizer.py
WANT: an AIS reporting gap must NOT reach a DARK verdict on its own. It needs either a
concurrent EO observation at that position, or `defer_reasons=["sparse_ais_coverage"]`.
WHY: measured on the Fehmarn slice, 22,515 inter-report gaps exceed 60 s and 660 exceed
10 minutes. THREE different things produce a gap and the AIS file cannot tell them
apart (02_data/INVENTORY.md §4.2):
  1. the vessel left the 0.4x0.8 degree bounding box while transmitting normally —
     this is what the 17 h 39 m maximum gap actually is;
  2. the receiver missed it — Bornholm's tracks are 2.5x sparser than Fehmarn's on the
     same day with the same transponders, purely from basestation geometry;
  3. the transponder was switched off. Only this one is a finding.
Scoring (1) or (2) as evasion sends a patrol boat to a vessel that did nothing, which
is the failure criterion 2 exists to prevent. A gap that ENDS AT THE BOX EDGE should
never score.
IMPACT: `03_src/verdict.py` (DARK path), `03_src/prioritizer.py` (do not weight a
bare AIS gap). `DeferReason` already contains `"sparse_ais_coverage"`.
STATUS: OPEN

### 2026-08-28 14:54 UTC | C -> ARCH | 03_src/contracts.py
WANT:   A `CameraPose` boundary type: camera lat/lon, boresight_deg_true,
        fov_half_angle_deg, max_range_m, pose_ref (matching EoContact.camera_pose_ref),
        and ideally boresight/pose uncertainty.
WHY:    EoContact carries `camera_pose_ref` as a STRING. A bearing without an origin
        cannot be turned into a position, so association.py cannot project an AIS
        claim into the camera's measurement space without the pose. Cause and effect:
        with no contract for it, `associate()` currently takes five bare floats as
        keyword arguments. That works, but the pose is produced by lane B and consumed
        by lanes C and D, so it IS crossing module boundaries — exactly what
        "never pass a bare dict across a module boundary" exists to prevent. Worse,
        the field-of-view envelope is what stops every AIS track in the Baltic being
        reported as a position-spoof candidate; if lane B and lane C disagree about
        what the FOV is, that gate silently opens.
IMPACT: association.py (signature), consistency.py (position-spoof test needs the same
        projection), evidence.py/report.py (the pose belongs in the evidence record —
        criterion 4: "every bearing is only as trustworthy as the pose that produced
        it"), and lane B's eo_detector/geometry which must emit it.
STATUS: OPEN

### 2026-08-28 14:54 UTC | C -> B | 03_src/geometry.py
WANT:   The signed shortest-arc helper LIBRARIES.md assigns to geometry.py, with the
        registered formula `((a - b + 180) % 360) - 180`, working on numpy arrays as
        well as scalars.
WHY:    geometry.py is a 0-byte file. association.py needs angular differencing on
        every bearing comparison and has therefore inlined a private
        `_shortest_arc_deg` with the identical formula. Cause and effect: two
        implementations of angle wrapping in one repo is precisely how a silent
        180-degree error survives to the demo — it does not crash, it just points the
        boat the wrong way. The private copy is marked TEMPORARY and must be deleted
        the moment geometry.py lands.
IMPACT: association.py (delete the private copy, import instead), consistency.py
        (every angular Mismatch.delta), verdict.py.
STATUS: OPEN

### 2026-08-28 14:54 UTC | C -> ARCH | tests/fixtures.py
WANT:   Geometric ground-truth scenarios alongside the existing attribute scenarios:
        a camera pose, vessels placed at known bearings and ranges, and EoContacts
        DERIVED from those true positions with injected sensor noise.
WHY:    The current fixtures draw AIS positions and EO bearings from independent
        random streams, so no true pairing exists to find. scenario_match() overrides
        class, length and heading but not position or bearing — so a geometric matcher
        run against it correctly returns NO match. Cause and effect: association.py
        could not be measured on the fixtures at all, only on their randomness. Lane C
        built the missing generator in 99_scratch/lane_c_association_check.py; it
        should live in tests/ where lane D can also use it, and the scratch copy
        deleted rather than maintained in parallel. The fixtures are NOT wrong — they
        are built for consistency.py and they serve that well. They are incomplete for
        association.py.
IMPACT: tests/fixtures.py, and 99_scratch/lane_c_association_check.py is deleted.
STATUS: OPEN

### 2026-08-28 14:54 UTC | C -> ARCH | 03_src/contracts.py (docstring only)
WANT:   Settle the SIGN convention for angular deltas. Mismatch.delta says "SIGNED
        SHORTEST ARC in -180..180. 359 vs 1 is +2, not 358." LIBRARIES.md gives the
        formula `((a - b + 180) % 360) - 180`, which for a=359, b=1 returns -2.
WHY:    The prose and the registered formula disagree in sign. Cause and effect: the
        magnitude is the same either way, so nothing crashes and no test fails — but
        consistency.py is about to write heading and COG mismatches whose SIGN says
        which way the vessel is really pointing relative to its claim, and a report
        sentence that says "12 degrees to starboard" when it means port is a defect
        the judges can see. association.py follows the FORMULA (a - b), not the prose.
        Please confirm that is the intended convention, or correct the formula.
IMPACT: contracts.py docstring, geometry.py, consistency.py, report.py wording.
STATUS: OPEN

### 2026-08-28 14:54 UTC | C -> ARCH | 03_src/LIBRARIES.md + environment
WANT:   scipy installed in .venv and its version recorded in LIBRARIES.md.
WHY:    association.py uses scipy.optimize.linear_sum_assignment (the assignment
        solver) and scipy.stats (the chi-square-to-sigma conversion). Inspecting
        .venv/lib/python3.12/site-packages shows geopy 2.5.0, pyproj 3.7.2,
        shapely 2.1.2, pydantic 2.13.4, movingpandas 0.23.0, pyais 3.2.1, folium
        0.20.0, loguru 0.7.3 — but NO scipy and NO scikit-learn directory. The
        2026-08-28 STATUS line records that the scikit-learn install was never version-
        checked. Cause and effect: if scipy is genuinely absent, association.py raises
        ImportError on the first run and lane C is blocked at the worst moment.
        LIBRARIES.md also warns that scikit-learn can silently downgrade numpy under a
        working torch stack, so this must be installed and VERIFIED, not assumed.
IMPACT: association.py (hard dependency), prioritizer.py if it uses sklearn.
STATUS: OPEN

### 2026-08-28 15:10 UTC | C -> B | 03_src/eo_detector.py (FYI, no change requested)
WANT:   Nothing changed in eo_detector.py. Recording an integration hazard that lane C
        has already guarded, so lane B knows why and does not undo it.
WHY:    uncalibrated_benchmark_pose() sets yaw_uncertainty_deg = 180.0 deliberately, so
        a throughput run is structurally unusable as evidence — and eo_detector folds
        yaw into bearing_uncertainty_deg (the sqrt(yaw^2 + centroid^2) line). Cause and
        effect: association.py scores the bearing residual as delta/sigma, so a
        180-degree sigma makes EVERY bearing residual about zero sigma, the bearing term
        drops out of chi-square, and the pairing is then decided by RANGE ALONE. Range
        can agree by chance. VERIFIED on a purpose-built scene: with the guard removed,
        an uncalibrated contact MATCHES an AIS claim at score 0.172. Lane B's marker for
        "this run is not evidence" was arriving at lane C as a permissive gate.
        Guarded in association.py by MAX_USABLE_BEARING_SIGMA_DEG = 15.0: above that a
        contact cannot be paired and is emitted unmatched-AND-ambiguous, deliberately
        NOT as a dark vessel, because nothing was established either way.
        The general lesson is lane B's own and it is a good one — make the unusable
        state loud in the DATA. The catch is that "loud" has to mean loud to every
        consumer, and a very large uncertainty is loud to a human reading the field and
        quiet to an arithmetic gate that divides by it.
IMPACT: None required. If a CameraPose contract lands (request 1) and yaw uncertainty
        moves out of bearing_uncertainty_deg, this guard must move with it.
STATUS: FYI — no action needed

### 2026-08-28 21:45 UTC | C -> ARCH | 03_src/contracts.py
WANT:   Either add `observed_width_m` (+ its uncertainty) to EoContact, or remove
        "width" from MismatchDimension.
WHY:    MismatchDimension offers "width" but EoContact has no observed width field, so
        the dimension is unreachable — consistency.py cannot emit it and never will.
        Cause and effect: an unreachable enum member reads to a judge (and to lane D
        building the evidence card) as a check the pipeline runs, when in fact nothing
        can ever produce it. The tempting workaround is worse than the gap: deriving
        beam from the bounding-box aspect ratio silently encodes the vessel's ASPECT
        ANGLE as if it were its width, so a ship viewed bow-on would read as a 20 m
        beam on a 250 m hull and generate a critical spoof from pure geometry. No
        width comparison is made and none is faked. NOTE: contracts.py already gives
        AisTrack the Size A-D fields, so the CLAIMED side is fully available — the gap
        is entirely on the observed side.
IMPACT: contracts.py, consistency.py (one comparison added if width lands), lane B's
        eo_detector if it is asked to estimate beam.
STATUS: OPEN

### 2026-08-28 21:45 UTC | C -> ARCH + B | 03_src/contracts.py (EoContact)
WANT:   Uncertainty fields for observed heading and observed speed:
        `observed_heading_uncertainty_deg`, `observed_speed_uncertainty_ms`.
WHY:    EoContact pairs an uncertainty with bearing, range and length, but NOT with
        heading or speed — so consistency.py must ASSUME both. It currently uses
        OBSERVED_HEADING_SIGMA_DEG = 20.0 and OBSERVED_SPEED_RELATIVE_SIGMA = 0.25,
        declared in the module and destined for EvidenceRecord.limitations. Cause and
        effect: those two constants set the significance of every heading and speed
        mismatch, which sets their severity, which feeds the verdict confidence. A
        heading sigma that is wrong by a factor of two moves a finding two severity
        bands. Lane B knows the real numbers — heading sigma depends on pixel extent
        and aspect angle, speed sigma on the range error and the frame interval —
        and lane C is guessing them from the outside. This is the single largest
        uncalibrated assumption in consistency.py.
IMPACT: contracts.py, eo_detector.py (populate), consistency.py (use instead of the
        constants), report.py (limitations wording).
STATUS: OPEN

---

### 2026-08-28 15:40 UTC | A -> ARCH | 03_src/contracts.py
**INTERRUPT, not a queue item — three modules stall behind it.**

WANT: three additions. Nothing existing changes meaning; all of it is additive.

**(1) A THIRD PREFIX: `implied_`.** Alongside `claimed_` (what the transponder
asserts) and `observed_` (what the camera sees), a value computed from consecutive
CLAIMED POSITIONS is neither. Speed derived from two AIS positions is not an
observation — no sensor saw it — and it is not a claim, because the transponder never
broadcast it. It is what the claim implies about itself.

**(2) Additive optional fields on `AisTrack`** (all `float | None = None`, all
per-report, so they fit the object's existing shape):
```
implied_speed_ms         # movingpandas Trajectory.add_speed, m/s on EPSG:4326
implied_course_deg_true  # movingpandas Trajectory.add_direction, deg true 0-360
gap_before_s             # seconds since this vessel's previous report
```

**(3) A NEW FROZEN TYPE `BehaviourEvent`,** plus
`EvidenceRecord.behaviour_events: list[BehaviourEvent] = []`. The exact shape lane A
already emits as a local mirror dataclass is in `03_src/ais_trajectory.py` — copy it:
```
event_id, track_id, claimed_mmsi
kind: Literal["loiter", "ais_gap"]
t_start_utc, t_end_utc (AwareDatetime), duration_s
centroid_lat_deg, centroid_lon_deg
radius_m                                   # loiter only
resume_lat_deg, resume_lon_deg, gap_distance_m, implied_gap_speed_ms   # gap only
distance_to_area_edge_m
explained_by_area_exit: bool
explained_by_sparse_coverage: bool
claimed_nav_statuses: tuple[str, ...]
claimed_ship_type: str | None
detector: dict[str, Any]                   # the thresholds it was detected under
```

WHY (1) AND (2): this is a spoofing detector that needs NO CAMERA. AIS broadcasts SOG
as its own field AND broadcasts the positions from which a speed can be computed. A
vessel reporting SOG 0.2 kn while its own position reports move it 400 m per minute
has contradicted itself — that is `SpoofSubtype="kinematic"`, available on recorded
data, on day one, with no EO frame and no association step. It is the cheapest
criterion 3 evidence in the pipeline. **It exists only if the two speeds live in
separate fields.** Write the derived speed into `claimed_sog_kn` and the contradiction
is arithmetically erased; there is nothing left to compare.

WHY NOT `derived_`: `implied_` says where it came from — the claim implies it. The
whole point is that it stays on the CLAIMED side of the wall while not being an
assertion the vessel made.

WHY (3) IS A NEW TYPE AND NOT MORE `AisTrack` FIELDS: `AisTrack` is a per-report
snapshot — one timestamp, one position, frozen. A loiter is a SPAN over hundreds of
reports. Stamping `in_loiter=True` on each report repeats a span-shaped fact hundreds
of times, and the thing an operator needs — when it started, when it ended, where the
centre was, how wide it was — then exists only implicitly, recoverable by rescanning
the whole track. Criterion 2 has to say "this vessel, because it sat over the cable
route for 47 minutes" and criterion 4 has to quote that span into a case file.
Neither survives being smeared across per-report booleans. `EvidenceRecord` embeds
`mismatches` as snapshots for exactly this reason; behaviour needs the same treatment
or it cannot appear on the evidence card at all.

MEASURED, so the volume is known rather than guessed — Fehmarn slice, full 24 h,
431,745 deduped vessel reports, 315 vessels: **660 AIS gaps over 10 minutes across
108 vessels**, and 146 over 30 minutes. Loiter events are of the same order. This is
hundreds of objects per day, not millions.

NOTE ON THE `AisTrack` DOCSTRING: it currently reads "carries `claimed_*` fields ONLY.
Nothing observed may be written to it." That sentence stays TRUE — `implied_` is not
observed — but it should be amended to name the third category explicitly, or the
next person to read it will assume `implied_speed_ms` is a contract violation and
delete it.

ALTERNATIVE IF YOU WANT `AisTrack` KEPT ABSOLUTELY PURE: put the three per-report
values on their own frozen type keyed by (track_id, report_time_utc). Costs a second
object per report — 431,745 extra objects for one day of one slice — and every
consumer then has to join. Lane A recommends the additive fields; the decision is
yours because it is your wall.

IMPACT: `03_src/contracts.py` (3 fields on `AisTrack`, 1 new type, 1 field on
`EvidenceRecord`), `tests/fixtures.py` (optional — all new fields have defaults),
`00_brief/CONTRACTS.md` (the prose companion and the wall diagram),
`03_src/ais_trajectory.py` (drops its local mirror, imports the real type),
`03_src/consistency.py` (gains the free kinematic self-contradiction test),
`03_src/prioritizer.py` and `03_src/report.py` (can finally cite a loiter).
Purely additive — no existing field changes type or meaning, so nothing breaks while
this is pending.
STATUS: OPEN

### 2026-08-28 15:40 UTC | A -> ARCH | 03_src/ais_trajectory.py, tests/test_ais_trajectory.py
WANT: acknowledgement of two new files, and a line in FILE_OWNERSHIP.md §2 under
lane A.
WHY: trajectory analysis is a different question from ingest ("what did the claimed
track DO" vs "what is being claimed") and it needs movingpandas, geopandas and
shapely. Folding it into `ais_ingest.py` would make the geo stack a hard dependency
of reading a CSV, so a teammate whose `geopandas` install is broken could not read
AIS at all — on a night when that is the difference between a demo and no demo.
`ais_ingest.py` was also already 851 lines.
IMPACT: two new files, no existing file changes. `ais_ingest.py` is untouched and
still imports nothing beyond pandas and contracts.
STATUS: OPEN

### 2026-08-28 15:40 UTC | A -> C, D | consistency.py, prioritizer.py
WANT: treat `explained_by_area_exit` and `explained_by_sparse_coverage` on a gap
event as SUPPRESSORS, not as score inputs, and discount loiter events whose
`claimed_nav_statuses` intersect {Moored, At anchor, Aground, Not under command}.
WHY, with the measurement: at a 30-minute threshold the Fehmarn day contains 293
stationary periods, and **the top two ship types among those NOT declaring a
stationary status are Tug (71) and Sailing (65)** — the tunnel construction fleet and
becalmed yachts. A ranked list that does not discount them is a list of harbour
furniture, and criterion 2 is judged on whether the top of that list deserves the
patrol boat. Likewise 660 gaps at 10 minutes is not 660 dark vessels: the maximum gap
in the day is 17.6 hours, which is a vessel that left the 0.4x0.8 degree box and came
back, not a transponder switched off for a working day.
IMPACT: `03_src/consistency.py`, `03_src/prioritizer.py`. No contract change beyond
the request above.
STATUS: OPEN

### 2026-08-28T14:55Z | B -> C | 03_src/association.py
WANT: Two small consolidations, both optional and neither blocking. Lane C decides.

(1) Replace the private `_shortest_arc_deg` (association.py:214) with
    `from geometry import signed_delta_deg`.
(2) Be aware that `geometry.inverse_geodesic()` also wraps `Geod.inv`, for lane D's
    contact-to-infrastructure case.

WHY: LIBRARIES.md says the signed-shortest-arc helper is "One helper in geometry.py,
owned by lane B, used by everyone." There are now two implementations. They agree
today — I checked the arithmetic, both are ((a-b+180) % 360) - 180 — so this is not a
bug, it is a divergence risk: the next person who fixes a boundary case fixes one of
them. Verified boundary behaviour, worth knowing either way: exactly-opposite angles
return -180, never +180. Anything comparing with abs() is unaffected; anything
branching on the sign of a 180 deg delta is relying on a coin flip.

On (2): `predict_measurement()` is deliberately NOT duplicated in geometry.py — your
docstring is right that two implementations of that projection are two chances to be
wrong, and geometry.py provides only the INVERSE (`project_to_position`, Geod.fwd),
which nothing else had. But both files now construct `Geod(ellps="WGS84")`
independently. If the ellipsoid ever changes it must change in both.

IMPACT: (1) is one import and one deletion in association.py, no behaviour change.
(2) is awareness only, no code change.
STATUS: OPEN

---

### 2026-08-28 16:55 UTC | A -> ARCH | 03_src/contracts.py
**Second contract interrupt. Additive, and independent of the BehaviourEvent one —
either can land first.**

WANT: make `Mismatch` able to carry a claim contradicting ITSELF.
1. Add `"position_implausible"` to `MismatchDimension`.
2. Make `association_id` optional: `str | None = None`.
3. Add `comparison: Literal["claimed_vs_observed", "claimed_vs_implied"] =
   "claimed_vs_observed"` — defaulted, so every existing construction is unaffected.
4. Add `implied_value: float | str | None = None` and `implied_field: str | None =
   None`, and make `observed_value` / `observed_field` / `observation_confidence`
   optional.

WHY, concretely: `03_src/ais_ingest.PlausibilityChecker` compares a vessel's claimed
SOG against the speed its OWN consecutive claimed positions imply. Nothing observed
it. Two required fields on `Mismatch` therefore have no truthful value:
  * `association_id` — nothing was paired, because nothing was seen. Synthesising one
    puts a fabricated association into something we call evidence-grade.
  * `observation_confidence` — there is no observation to be confident about.

WHY THE `comparison` DISCRIMINATOR IS THE LOAD-BEARING PART: without it, the only
place to put an implied speed is `observed_value`, and a consumer that reads
`observed_value` will weight the verdict with an EO uncertainty that was never
computed. That failure is silent — no exception, just a confidence number that means
something different from what its field name says. This is the same wall the
`implied_` prefix request protects, applied to `Mismatch` instead of `AisTrack`.

Lane A ships `PlausibilityMismatch` — a local mirror with the requested field names —
so C and D are unblocked and the swap is one import. It carries a test asserting it
has NO `observed_*` field and no `observation_confidence`, mirroring your
`test_verdict_has_nowhere_to_put_model_generated_text`.

MEASURED VOLUME, so this is not speculative: **41 findings across 27 vessels** on the
Fehmarn day at the shipped thresholds — 15 impossible_speed, 9 teleport, 17
zero_interval_jump. Tens per day, not thousands.

IMPACT: `03_src/contracts.py` only. All four changes are additive or widen an
existing type, so nothing currently constructing a `Mismatch` breaks.
`tests/fixtures.py` unaffected (new fields default). `03_src/consistency.py` gains a
second producer of Mismatch records.
STATUS: OPEN

### 2026-08-28 16:55 UTC | A -> C, D | verdict.py, prioritizer.py
WANT: a `position_implausible` finding must NOT reach a SPOOF verdict on its own. It
needs corroboration — an EO contact, or a second independent inconsistency.

WHY, with the measurement: of the 41 findings on the Fehmarn day, **26 are on vessels
claiming ship type "Sailing" and every worked example is Class B.** The leading
explanation is a cheap consumer GPS on a small craft, not deception. The check is a
DATA-QUALITY signal that lane C may promote to `SpoofSubtype="kinematic"` with
corroboration; promoted alone, it puts the word "spoof" against a real named yacht,
which is the defamation CLAUDE.md forbids outright.

Note also that the naive version of this check produces **705** findings instead of
41, and 96% of those are GPS scatter at 1 Hz sampling. If the number you receive ever
jumps by an order of magnitude, someone has lowered `min_interval_s` — check the
`detector` dict on the finding before believing it.
IMPACT: `03_src/verdict.py`, `03_src/prioritizer.py`. No contract change beyond the
request above.
STATUS: OPEN

### 2026-08-29T06:20Z | B -> C | 03_src/consistency.py
WANT: Awareness, and a decision that is yours. No code change requested.

Measured against YOUR arithmetic (`_probability_to_sigma`, `min_report_sigma=2.0`,
`confusable_class_discount=0.5`, `min_class_confidence=0.60`):

  * A class mismatch needs VLM confidence >= 0.9772 to reach 2.0 sigma.
  * A CONFUSABLE PAIR CAN NEVER FIRE. Max reachable significance is 3.719 sigma
    (confidence clips at 0.9999); halved, that is 1.860 against a 2.0 floor. So
    cargo/tanker, tug/small_craft, fishing/small_craft, tug/fishing and passenger/cargo
    cannot produce a class Mismatch at any confidence whatsoever.
  * eo_vlm.py caps VLM confidence at the Wilson 95% lower bound of its MEASURED
    agreement rate, because a classifier may not claim more confidence than its
    demonstrated hit rate. A perfect 20-crop sample yields a ceiling of 0.8389
    (0.990 sigma). Reaching 0.9772 needs ~165 hand-labelled crops at 100% agreement.

WHY YOU MAY WANT TO KNOW: taken together, the class dimension will almost certainly
contribute ZERO mismatches in the demo. I believe that is correct and deliberate on your
part — your own docstring says "silhouette classification is the weakest evidence in the
system and the arithmetic should say so rather than the footnotes" — and lane B is not
asking you to weaken it. Lane B has capped its own output rather than lobbying to lower
your floor.

But it means the class-spoof row in DEMO_INDOOR.md's fault-injection table ("rewrite
claimed_ship_type to cargo over a tug silhouette") will NOT produce a SPOOF verdict via
check_class. tug/cargo is not in CONFUSABLE_CLASSES so it is not blocked by the discount,
but it still needs >= 0.9772 confidence, which an uncalibrated VLM will not be permitted
to assert. If that demo row is meant to fire, the lever is yours: a class-specific
min_report_sigma, or accepting group-level (large_commercial vs small_craft) disagreement
at a lower bar. LENGTH mismatch is unaffected and remains the strong path.

IMPACT: none unless you choose to act. Lane B changes nothing either way.
STATUS: OPEN

### 2026-08-29T07:05Z | ARCH -> D | 04_demo/make_synthetic_eo.py
DISCLOSURE, not a request. This file was EDITED by the agent under Amol's explicit
authorisation as ARCH, because making `EoContact.detection_confidence` required broke
both of its construction sites and lane D was not in the room. Flagging it because the
one-owner-per-file rule exists precisely so this does not happen silently.

WHAT CHANGED:
1. New module-level helper `_synthetic_detection_confidence(apparent_width_px, rng)`.
2. Line ~523 (real vessel) now passes `detection_confidence=..._(width_px, rng)`.
3. Line ~567 (clutter)      now passes `detection_confidence=..._(w, rng)`.

THE DESIGN CALL YOU MAY WANT TO OVERRIDE: confidence is a saturating function of
APPARENT PIXEL WIDTH, and the SAME rule is used for real vessels and for injected
clutter. Giving clutter a systematically lower confidence would have been easier and
would have been cheating — anything downstream could then separate clutter from real
contacts trivially, and the false-positive rate measured on this scene would be
optimistic in a way nothing in the output reveals. With the width rule, clutter (6-22 px)
lands at 0.43-0.58 and a genuinely distant real vessel at 12 px lands at 0.49, inside the
same band. The ambiguity is real and the pipeline has to earn its way out of it.

Measured curve: 6 px -> 0.427, 22 px -> 0.583, 40 px -> 0.698, 200 px -> 0.896, sigma 0.05.

ACTION REQUIRED BY WHOEVER OWNS 04_demo/: `04_demo/out/scene01/eo_contacts.jsonl` was
written before the field existed and will now FAIL to load in run_pipeline.py:73 and
app.py:143. Regenerate it — command in STATUS.md and handoff_B.md.
STATUS: OPEN — lane D to review the design call above

### 2026-08-28 22:30 UTC | C -> ARCH | 03_src/contracts.py (DeferReason)
WANT:   Two additional DeferReason members: `single_dimension_evidence` and
        `uncorroborated_pairing`.
WHY:    verdict.py defers in two situations the existing vocabulary cannot name, and
        both currently collapse onto the generic `low_confidence`:
        (1) the case rests on ONE dimension. A likelihood ratio is only valid if the
            sensor model behind it is right, and with a single dimension an unmodelled
            systematic — a range estimate 40% low scaling the apparent length —
            produces exactly the spoof signature at any significance. Two dimensions
            failing for unrelated reasons is what rules that out.
        (2) the mismatches were computed off a pairing that scored below 0.20 or was
            flagged ambiguous, so the evidence may belong to a different hull.
        Cause and effect: both are reported to the operator as "low_confidence", which
        is true but useless — it tells a watch officer the tool is unsure without
        telling them WHAT to check. Criterion 4 asks the output to say why it defers,
        not merely that it does, and these are two of the most common reasons it will.
        The confidence CAPS for both are already implemented and measured
        (SINGLE_DIMENSION_CONFIDENCE_CAP 0.80, UNCERTAIN_ASSOCIATION_CONFIDENCE_CAP
        0.70); only the label for them is missing.
IMPACT: contracts.py (two enum members), verdict.py (two lines), report.py wording.
        Purely additive — no existing verdict changes meaning.
STATUS: OPEN

### 2026-08-29 07:15 UTC | C -> D | 03_src/prioritizer.py  [DISCLOSURE OF A CROSS-LANE EDIT]
WANT:   Nothing. Declaring an edit made to lane D's file on Amol's direct instruction,
        so D is not surprised by it. THE PUBLIC API IS UNCHANGED: rank_all, score_one,
        Asset, Weights, Infrastructure and DEMO_INFRASTRUCTURE keep their names and
        their existing call signatures, verified against the two live call sites
        (04_demo/run_pipeline.py rank_all(verdicts, tracks, contacts) and
        04_demo/app.py's `from prioritizer import DEMO_INFRASTRUCTURE, Asset`). Both
        still work untouched. All additions are keyword arguments with defaults.
WHY:    Two measured defects and three gaps against the brief.
        DEFECT 1 — THE PUBLISHED BREAKDOWN DID NOT SUM TO THE PUBLISHED SCORE. The
        `score *= 0.65` deferral damping was applied AFTER the weighted sum, so a
        deferred SPOOF displayed components summing to 0.9970 beside a score of 0.6481.
        Criterion 2 is marked on the operator being able to see why one contact
        outranks another; a column that does not add up is where that trust dies. The
        damping now applies to the two finding-derived components (verdict_severity,
        confidence) BEFORE the sum. Proximity, behaviour and actionability are not
        damped — a deferral does not move a cable route. breakdown_sums_to_score() is
        exported and asserted over all 20 contacts in the check.
        DEFECT 2 — A CONFIDENT MATCH BOUGHT URGENCY WITH ITS OWN CONFIDENCE. verdict.py
        defines confidence as confidence in the STATED label, so a MATCH at 0.95 means
        "95% sure this vessel is FINE" — and it was contributing 0.1425 to that
        vessel's priority. Measured consequence: an honest vessel ranked FIRST and was
        the only contact recommended for the boat, above a SPOOF 128 m from the same
        cable. Confidence is now scaled by label threat-relevance (SPOOF/DARK 1.0,
        UNKNOWN 0.5, MATCH 0.0).
        DEFECT 3, found by the new test — THE SNAPSHOT OVERRODE THE SPAN. Behaviour
        took max(speed proxy, dwell), so a vessel that DECLARED itself at anchor had
        its dwell correctly discounted to 0.52 and then the single-report proxy
        returned 1.00 and won. Every innocent explanation lane A computes (declared
        moored, coverage hole, area exit) was silently discarded whenever the snapshot
        looked bad. Dwell now GOVERNS when present. Relatedly the proxy's nav-status
        branch was changed from +0.15 to x0.45: the same fact previously discounted in
        one path and aggravated in the other, so the same vessel scored differently
        depending on whether lane A's trajectory pass had run.
        GAPS CLOSED: infrastructure_proximity is now the heaviest weight (0.30, was
        0.22 under verdict_severity's 0.32) per the brief's named scenario; Asset gained
        `available` and `available_in_min`, and with no hull free the ranking survives
        while every tasking recommendation is withdrawn; loiter now consumes lane A's
        ais_trajectory.BehaviourEvent (duck-typed, so no movingpandas import) instead of
        guessing dwell from one AIS report.
IMPACT: 04_demo/run_pipeline.py and 04_demo/app.py — verified working, no change needed.
        Rankings WILL move: that is the point of the reweighting, and 04_demo's expected
        output may need refreshing.
STATUS: FYI — no action needed, but D should read the module docstring before retuning
        anything. Two judgment calls are D's to overrule: DEFER_EVIDENCE_DAMPING = 0.65
        and the 0.45 declared-stationary discount.

### 2026-08-29 09:05 UTC | D -> C + ARCH | 03_src/verdict.py + 03_src/consistency.py  [P0 — IMPORT IS BROKEN]
WANT:   Reconcile verdict.py's import against the consistency.py that is actually on
        disk. Either restore `ConsistencyResult`, `Uncomparable` and
        `independent_dimension_count` to consistency.py, or rewrite verdict.py to
        consume the `dict[str, list[Mismatch]]` that `check_all()` now returns.
WHY:    verdict.py line 122 is
            from consistency import ConsistencyResult, independent_dimension_count
        and consistency.py defines NEITHER — verified by AST, not by guessing, on both
        the working copy and HEAD (`git show HEAD:03_src/consistency.py`). `check_pair()`
        returns a bare `list[Mismatch]`; there is no `ConsistencyResult` class, no
        `.uncomparable`, no `.agreements`, and no `independent_dimension_count`.

        CAUSE AND EFFECT, in the order it bites:
        1. `import verdict` raises ImportError. Criterion 1 (the pipeline), criterion 3
           (spoofing) and criterion 4 (evidence) are all downstream of it, so the demo
           does not start. This is not a degraded mode; it is an unhandled exception on
           line 1.
        2. 04_demo/run_pipeline.py:111 calls
           `verdict_mod.decide_all(associations, mismatches_by_assoc, ...)` and passes
           the dict-of-lists from `consistency.check_all()`. verdict.py's `decide_all`
           is annotated `results: dict[str, ConsistencyResult]` and its body reads
           `result.uncomparable` and `result.mismatches`. Even with the import fixed,
           this call site passes the wrong shape and would fail on first use.
        3. `_coverage_fraction()` computes coverage as
           agreements+mismatches over agreements+mismatches+uncomparable. Without
           `uncomparable` there is NO coverage number, and coverage is what caps MATCH
           confidence. The specific consequence, which is the dangerous one: a Class A
           transponder sending no static block sails through as a high-confidence MATCH,
           the exact failure verdict.py's own session notes say was closed.
        4. `limitation_strings(result, verdict)` is documented in handoff_C.md 6b as the
           ONLY channel by which "checks we could not run" reach the case file. With no
           ConsistencyResult anywhere in the pipeline, that channel is structurally
           unreachable, so every evidence record would list the dimensions that
           disagreed while silently omitting the ones that were never compared. A report
           that does that is not evidence-grade; it is a misleading one.

        WHICH DIRECTION TO FIX IT IN IS LANE C'S CALL, not lane D's. But note that the
        consistency.py on disk is a SIMPLER module than the one described in
        handoff_C.md section 9 and in the project memory: no `Uncomparable`, no
        agreement tracking. Restoring the richer version restores criterion 4's honesty
        channel; adapting verdict.py to the simpler one closes the import but leaves
        coverage and un-run checks with no home, and that trade should be made
        deliberately rather than by whichever file gets edited next.

IMPACT: verdict.py (import + decide_all + _coverage_fraction + limitation_strings),
        possibly consistency.py, and 04_demo/run_pipeline.py:111.
LANE D IS NOT BLOCKED BY THIS. 03_src/evidence.py imports contracts.py and the stdlib
only — asserted by an AST check in the session log — and takes lane C's limitations as
`list[str]` through a new `consistency_limitations=` argument on `build_record()`.
When the strings are absent the record says so in its own limitations section
("...calibration basis ... NOT DECLARED ... treat the confidence as uncalibrated")
rather than quietly shipping an unbacked confidence number.
STATUS: OPEN

### 2026-08-29 09:05 UTC | D -> ARCH | 04_demo/run_pipeline.py  [one-line change, after the P0 above]
WANT:   Once verdict.py imports again, pass lane C's limitations into the record:
            consistency_limitations=verdict_mod.limitation_strings(result_for(a), v)
        in the `evidence_mod.build_record(...)` call at run_pipeline.py:131.
WHY:    run_pipeline is the only place holding both the consistency output and the
        Verdict, so it is the only place that can call `limitation_strings()`. It is
        deliberately NOT called from inside evidence.py: `ConsistencyResult` is lane C
        internal and must not cross the seam (handoff_C.md 6b), and a top-level import
        of verdict.py inside evidence.py would make the case-file layer die whenever
        lane C's imports are broken — which is exactly the situation today.
        Until this line lands, every record carries an explicit statement that its
        confidence has no declared calibration basis. That is the correct behaviour, not
        a workaround, but it should not be what the judges see.
IMPACT: one keyword argument at one call site.
STATUS: OPEN

---

## 2026-08-29 | LANE F -> LANE D | P0 | pi_sensor.py capture backend must be picamera2 on the Pi

**File:** `04_demo/pi_sensor.py:349` (lane D owns it; lane F does not write it.)

**Current:** `cap = cv2.VideoCapture(src)`, `--source` default `"0"`,
then `cap.set(CAP_PROP_FRAME_WIDTH/HEIGHT)`.

**Problem.** On Raspberry Pi OS Bookworm the CSI camera module is driven by libcamera and
is not reliably exposed as a plain V4L2 capture node. Two outcomes:

1. No USB camera attached -> `cv2.VideoCapture(0)` opens nothing, line 353 raises
   `could not open source 0`. Loud and cheap.
2. **A USB webcam attached -> index 0 may resolve to the WEBCAM, not the CSI module.**
   The sensor runs, and computes every bearing from the `hfov_deg` surveyed for the other
   lens. Every bearing is wrong. No error is raised. The association stage receives a
   confidently mis-pointed observation, and the resulting mismatch is manufactured inside
   our own sensor. Same class as the dd/mm/yyyy trap: silent corruption, not a crash.

**Requested shape** (lane D's call on the details):
- `--backend {picamera2,cv2}`, defaulting to `picamera2` when `/proc/device-tree/model`
  contains "Raspberry Pi", else `cv2`.
- picamera2 path: `Picamera2()`, `create_preview_configuration(main={"size": (w,h),
  "format": "RGB888"})`, `configure`, `start`, then `capture_array()` in the loop in place
  of `cap.read()`. Keep the `cv2` path unchanged for video files and the Mac camera.
- **Do not select a narrow `sensor_mode` to set resolution.** Set `main` size only. A
  cropped mode narrows the HFOV, and `hfov_deg` floors every bearing sigma — see
  FINDING F-3 in `00_brief/HARDWARE.md`.
- Print which backend was chosen and the actual returned frame shape at startup, so a
  silent substitution is visible.

**Interim mitigation, no code required:** run `pi_sensor.py` on the Pi with **no USB
camera attached**, which forces the loud failure over the silent one.

**Not written by lane F:** the Lane F session brief says no pipeline code.

---

## 2026-08-29 | LANE F -> ARCH | P2 | FILE_OWNERSHIP.md has no lane F

`FILE_OWNERSHIP.md` lists lanes A/B/C/D + ARCH. Lane F (hardware / sensor node) owns:

- `00_brief/HARDWARE.md`         (new, written this session)
- `99_scratch/probe_pi.sh`       (new)
- `99_scratch/probe_pi_mac.sh`   (new)
- `99_scratch/handoff_F.md`      (new)

Lane F does **not** own `04_demo/pi_sensor.py` — that stays with lane D, which is why the
capture-backend defect above is a request and not an edit.

### 2026-08-29 | E -> ARCH | FILE_OWNERSHIP.md
WANT: A lane E row in §2, owning `03_src/server.py`, `99_scratch/lane_e_server_check.py`
and `99_scratch/handoff_E.md`; plus a note that `04_demo/operator_audit.jsonl` is a
RUNTIME ARTEFACT written by lane E into lane D's directory.
WHY: FILE_OWNERSHIP.md defines four lanes and ARCH. `server.py` is a new file so nothing
was overwritten, but a file with no owner is a file two people will edit at 03:00 — which
is the exact failure the document exists to prevent. The audit file matters separately:
it lands in `04_demo/`, which lane D owns, so without a note D can reasonably delete or
regenerate it. It is a criterion 4 deliverable and must survive.
IMPACT: FILE_OWNERSHIP.md only. No code changes.
STATUS: OPEN

### 2026-08-29 | E -> ARCH | .gitignore
WANT: A decision on `04_demo/operator_audit.jsonl`.
WHY: It contains `operator_id` values — real people's names or handles — and the
`contact_id`s they acted on. Committing it publishes who decided what. Excluding it means
the criterion 4 artefact is absent from a fresh clone and a judge reading the repo cannot
see the audit trail exists. Neither is obviously right; both are worse if chosen by
accident. NOTE the precedent: this is the same class of problem as the `02_data/golden/`
rule (real identities reaching a public commit), and that one was caught twice.
Suggested resolution: ignore the live file, commit a small `operator_audit.EXAMPLE.jsonl`
with synthetic operator ids, so the shape is public and the record is not.
IMPACT: .gitignore, and one example file if the suggestion is taken.
STATUS: OPEN

### 2026-08-29 | E -> D | 04_demo/web/index.html
WANT: A WebSocket client. Connect to `/ws`; first message is a `ConsoleState` (has
`last_seq`, no `event` key) — apply it wholesale; every later message is a `StreamEvent`
(has `event` and `seq`) — apply and record `seq`. On a gap in `seq`, reconnect rather
than keep painting. Keep the 2 s `/api/state` poll as the fallback when the socket is
down; `server.py` serves that route unchanged, so nothing breaks if this is not done.
WHY: cause and effect, stated plainly. The polling console cannot show a verdict landing
— it shows a picture up to two seconds old and reshuffles on a timer whether or not
anything changed, which is exactly the "queue that looks broken" the console is meant to
avoid. More importantly it CANNOT recover a refresh correctly: a re-poll returns the
current state with no `last_seq`, so there is no join between snapshot and stream and no
way to detect a missed update. `ConsoleState.last_seq` exists precisely to make a
mid-demo browser refresh survivable, and that is a criterion 4 moment on stage.
IMPACT: `04_demo/web/index.html` only. No contract change, no server change; `/api/state`
stays for the fallback path.
STATUS: OPEN

### 2026-08-29 | E -> D | 04_demo/run_pipeline.py
WANT: `run_scene()` to return the `Association` objects it already builds — e.g. an
`"associations"` key holding `[a.model_dump(mode="json") for a in associations]`, purely
additive, breaking no existing consumer.
WHY: `GET /evidence/{id}` calls `evidence.record_to_dict(record, association=None)`, so
every case file served over HTTP is missing its `pairing_basis` block. That block is the
argument that the claim and the observation being compared are the SAME HULL — every
mismatch in the record rests on it, and evidence.py went to the trouble of adding it
outside the contract dump specifically so a reader could audit the premise. Serving the
mismatches without it hands a reviewer a comparison with its premise removed.
The server will not fabricate one: `assoc_ambiguous` is a defer-to-human trigger, and a
console inventing a pairing quality it did not compute is the one number that must never
be guessed. So the gap stays open and visible until this lands.
IMPACT: `04_demo/run_pipeline.py` (one key added), `03_src/server.py` (pass it through).
`04_demo/app.py` unaffected — an added key is ignored by lane D's page.
STATUS: OPEN

### 2026-08-29 | E -> B | 04_demo/pi_sensor.py  [NO CODE CHANGE REQUESTED]
WANT: Nothing in the file. Run it with `?node_id=<id>` appended to the `--post` URL, e.g.
`--post http://<mac-ip>:8000/api/contacts?node_id=pi-01`.
WHY: `SensorNode` provenance. Without a node id every contact is attributed to
`edge-unknown`, and the console cannot distinguish "the sea is empty" from "that Pi
died" — opposite findings that look identical on a map. The server accepts the id as a
query parameter precisely so the sensor needs no change. `measured_fps` is left None
unless the node reports a COUNTED rate: the server can only measure POST rate, and
pi_sensor batches on `--interval`, so the two are not the same number and substituting
one for the other would violate MEASURED NUMBERS ONLY.
IMPACT: none. A command-line habit, not a code change.
STATUS: OPEN

---

## 2026-08-29 | LANE F -> LANE D | P0 | pi_sensor.py cannot post: to_contact() omits detection_confidence

**File:** `04_demo/pi_sensor.py`, `to_contact()` (lane D owns it).

**MEASURED, not inferred** (cloud container, pydantic 2.13.3):

```
EoContact(**pi_sensor.to_contact(...))
  -> 1 validation error for EoContact
     detection_confidence  Field required [type=missing]
```

`detection_confidence` became a REQUIRED field of `EoContact` on 2026-08-29 (ARCH+B).
`to_contact()` was not updated, so `server.py`'s `_ingest` -> `EoContact(**c)` raises and
returns **HTTP 400 on every POST from the classical node**. pi_sensor.py currently cannot
deliver a single contact to the shore station.

**Why this is P0 rather than a tidy-up.** The classical node is the FALLBACK for the new
YOLO node: `04_demo/edge_benchmark.md` §3 says that if COCO's `boat` does not fire on the
printed silhouette, the move is to switch to pi_sensor.py. That escape route does not
currently exist. Both nodes are broken in the same direction and neither was exercised.

**Suggested fix** (lane D's call): the classical detector has no learned confidence, so it
must not invent one. A defensible construction is a saturating function of the evidence it
does have — blob area against `min_area_px`, and `frames` — with the SAME rule applied to
clutter, exactly as `make_synthetic_eo._synthetic_detection_confidence` does. Do NOT
hardcode 1.0: a constant maximum tells lane C every classical detection is certain, which
inflates DARK confidence with a number nobody measured.

**Regression guard already written:** `99_scratch/lane_f_edge_check.py` asserts
`EoContact(**pi_sensor.to_contact(...))` validates. It currently FAILS, on purpose, and
will pass when this is fixed.

---

## 2026-08-29 | LANE F -> LANE E | P2 | server.py: two small interop notes, no change requested yet

Both verified by reading `03_src/server.py`, neither is a defect:

1. **MJPEG boundary is load-bearing across the relay.** `_mjpeg_passthrough` forwards the
   Pi's bytes unchanged while declaring `boundary=frame` from `MJPEG_BOUNDARY`. So the Pi
   must emit exactly `frame`. `edge/sensor_node.py` pins `MJPEG_BOUNDARY = "frame"` with a
   comment and `lane_f_edge_check.py` asserts the two agree. If lane E ever changes that
   constant, the relayed stream breaks while the direct stream keeps working — a fault
   that appears only through the Mac and reads as a network problem.

2. **`edge_client.py` deliberately does not duplicate node liveness.** server.py owns
   `_note_node`, the watchdog and `NodeStatusEvent`; edge_client polls the node's own
   `/health` from the other direction and only ADDS the case server.py cannot see — a node
   that is powered and detecting but whose POSTs never arrive, which is not "offline" to
   the server but **absent from `ConsoleState.nodes` entirely**, so the console draws
   nothing and an empty queue reads as an empty sea.

   If lane E wants that on the strip, `edge_client.EdgeNodeMonitor.nodes()` returns
   `SensorNode` objects ready to merge, and `reconcile()` returns a JSON-serialisable row
   with a `diagnosis` and a `suggested_action`. Offered, not pushed — the merge policy is
   lane E's call and `POLL_STALE_AFTER_S` is deliberately set to the same 30 s as
   `NODE_STALE_AFTER_S` so the two views cannot disagree about staleness.

### 2026-08-29 | E -> D | 03_src/prioritizer.py  [DEFECT — MEASURED, NOT SUSPECTED]
WANT: DEFER_EVIDENCE_DAMPING applied to the COMPONENT SCORES before the weighted sum,
not to the final score afterwards — or, if damping the total is the intended behaviour,
a sixth published component carrying it so the breakdown still adds up. Then run
breakdown_sums_to_score() over a scene containing at least one DEFERRED verdict.
WHY: cause and effect, with the measurement. In 04_demo/out/scene01/ranked.json,
6 of 8 records violate breakdown_sums_to_score(tol=5e-4). The signature is exact:
    score / sum(component_scores[k] * component_weights[k]) = 0.6500
for every record where verdict.defer_to_human is true, and 1.0000 for both records
where it is false. 0.65 is DEFER_EVIDENCE_DAMPING. The comment above that constant says
"Applied before the weighted sum so the published breakdown still adds up" — it is not;
it is applied to the total. breakdown_sums_to_score() exists precisely to catch this and
was evidently never run against a deferred verdict, which is 6 of the 8 records in the
demo scene and will be most of any real scene.
CONSEQUENCE, which is the part that matters: prioritizer.explain() and the evidence card
publish a factor breakdown that does not reconcile with the score printed beside it. Its
own docstring states the stakes — "an operator who adds up the column and gets a
different total has found a reason to stop trusting the whole tool". A judge who adds up
the column on the criterion-2 slide gets 0.657 where the tool says 0.427. The RANKING is
unaffected (every deferred verdict is damped by the same factor, so the order is
preserved); it is the published EXPLANATION that is wrong, which for criterion 2 is the
deliverable itself.
CAVEAT, stated because it changes what to do first: ranked.json is STALE — it carries the
OLD weights (verdict_severity 0.32 / infrastructure_proximity 0.22) rather than the
current 0.30/0.26. Re-run the pipeline before fixing anything; the defect may already be
gone. The 0.6500 signature is what to check for.
FOUND BY: 03_src/web/index.html rendering the breakdown and checking the sum, which is
why the console now prints a "BREAKDOWN DOES NOT SUM" warning rather than a total it has
not verified. Lane E will not silently hide this; if it is fixed the warning disappears
on its own.
IMPACT: 03_src/prioritizer.py. No contract change. The console needs no change either way.
STATUS: OPEN

### 2026-08-29 | E -> ARCH | FILE_OWNERSHIP.md  [SUPERSEDES THE EARLIER LANE E REQUEST]
WANT: The lane E row should now read: `03_src/server.py`, `03_src/web/` (the WebSocket
console), `99_scratch/lane_e_server_check.py`, `99_scratch/lane_e_contrast.py`,
`99_scratch/lane_e_console_render.png`, `99_scratch/handoff_E.md`.
WHY: 04_demo/web/index.html was NOT overwritten, deliberately, and the reason is
operational rather than territorial: lane D's page polls /api/state and is what
04_demo/app.py serves. app.py has no WebSocket. Replacing that file with a socket-driven
page would have deleted the stdlib fallback — the one path that has actually been run
end to end — in order to add a feature. Two pages, two servers, one rehearsed fallback
intact. Lane E's page also polls /api/state when the socket is down, so it runs against
app.py too; only the video and audit routes are absent there, and it says so on screen.
IMPACT: FILE_OWNERSHIP.md only.
STATUS: OPEN

---

## 2026-08-29 | LANE F -> LANE C | **P0, DEMO IS DEAD** | verdict.py imports two names consistency.py does not define

**MEASURED, statically and by import** (cloud container, staged copy of the current files):

```
consistency.ConsistencyResult            -> ABSENT
consistency.independent_dimension_count  -> ABSENT
consistency.check_all return annotation  -> dict[str, list[Mismatch]]

import verdict       -> ImportError: cannot import name 'ConsistencyResult' from 'consistency'
import run_pipeline  -> ImportError (same)
import server        -> ImportError (same)   <- the FastAPI shore station
import app           -> ImportError (same)   <- the STDLIB FALLBACK console
```

`03_src/verdict.py:122`. **Both consoles are dead.** There is currently no way to start
the demo, and the documented fallback (app.py, stdlib, "already run end to end") fails
identically because it imports run_pipeline too.

**How it got here.** mtimes: `consistency.py` 06:11, `verdict.py` 06:37. The clean
end-to-end run in STATUS was ~05:30, before both. Lane C wrote the richer verdict.py
against a `ConsistencyResult`-shaped consistency.py that never landed on disk. Project
memory already carried the warning ("the file on disk is a SIMPLER module with no
ConsistencyResult") and it was not acted on.

**DO NOT FIX THIS BY PATCHING THE IMPORT.** The shapes disagree, not just the names:
`check_all()` returns `dict[str, list[Mismatch]]`, while `verdict.decide_all()` expects
`dict[str, ConsistencyResult]` and calls `_coverage_fraction(result)` and
`independent_dimension_count(result, "major")` on each value. Stubbing the import moves
the failure from startup into the middle of the demo, which is strictly worse — a
startup ImportError is the cheapest possible failure and it is the one you currently have.

**The decision to make** (lane C's, not lane F's): either
(a) implement `ConsistencyResult` + `independent_dimension_count` in consistency.py and
    change `check_all` to return it — the design verdict.py, evidence.py and
    `99_scratch/lane_c_consistency_check.py` were all written against; or
(b) rewrite verdict.py against `dict[str, list[Mismatch]]`, which loses coverage
    fraction and the independent-dimension count — i.e. loses the cross-dimension
    corroboration rule that stops class-alone from ever convicting.

(a) is what three modules already assume. `99_scratch/lane_c_consistency_check.py` is the
spec: it exercises `C.ConsistencyResult` and `C.independent_dimension_count` directly.

**Lane F has NOT touched either file.** This is a design reconciliation, not a typo.

---

## 2026-08-29 | LANE F -> LANE E | DISCLOSED EDIT | source failover in server.py

Lane F edited `03_src/server.py` on Amol's instruction ("the demo must survive the Pi
dying mid-pitch"). Backup at `03_src/server.py.bak-lanef`. Nine changes, all additive:

1. imports `source_switch` (new, lane F, `03_src/source_switch.py`).
2. **`NODE_STALE_AFTER_S` 30.0 -> 8.0**, with the derivation in a comment. At 30 s an
   unplugged Pi stays green for half a five-minute pitch. 8 s is four missed heartbeats.
3. `ConsoleBackend.__init__` takes `initial_source` and gains `self.source`,
   `self._last_by_kind`, `self._last_seen_by_kind`.
4. `ingest()` records liveness for EVERY node, buffers contacts by kind, and promotes
   only the active source. A demoted post returns early: no recompute, no events.
5. new `note_heartbeat()` — liveness only, no recompute, cannot blank the picture.
6. new `set_source()` — switches, restores `mode`, promotes buffered contacts, recomputes
   once, and returns `switch_ms` / `recompute_ms` / `records_before` / `records_after`.
7. new routes `GET /source`, `POST /source`.
8. `api_state()` and `/health` carry `source.status()`. It rides on the payload the
   console already polls so the badge cannot go stale independently of the picture.
9. CLI `--initial-source` (default RECORDED) and two extra startup lines.

Also edited `04_demo/web/index.html` (backup `.bak-lanef`): a SOURCE badge, three switch
buttons, and a note line showing demoted nodes and the measured switch round trip.

**Nothing was removed and no existing route changed shape.** If lane E wants the switch
elsewhere, `source_switch.py` is standalone and has no web dependency.


---

## 2026-08-29 | ARCH -> E | DISCLOSED: two additive hooks on 03_src/server.py

Backup at `03_src/server.py.bak-main`. Applied by `99_scratch/patch_server_hooks.py`,
which aborts rather than half-applying if any anchor is missing or ambiguous.

1. `ConsoleBackend.replace_tracks(tracks, *, reason)` — NEW public method.
2. `create_app(..., startup_tasks=())` — NEW keyword, defaults to empty.

Nothing existing changes behaviour. No route, no field, no threshold, no rename.

WHY, and it is not a convenience. `run_pipeline.load_scene()` reads `ais_tracks.jsonl`
once at `ConsoleBackend.__init__`. Contacts can arrive over time through
`POST /ingest/contacts`; claims cannot. So the AIS half of the picture was frozen at
startup, and everything that only exists in TIME was unreachable — a report going
stale, a vessel falling silent, a track loitering. Criterion 2 ranks BEHAVIOUR, and
there is no behaviour without a clock.

WHY A METHOD AND NOT A ROUTE. The replay driver runs in this process and this event
loop. An HTTP route would serialise every `AisTrack` to JSON and parse it straight back
for nothing, and would open a way to inject CLAIMS from off-machine — which a shore
station should not accept. `replace_tracks` takes the same `self._lock` every other
mutation takes, so a replay tick cannot interleave with an ingest batch and leave the
pipeline reading half of each.

WHY `startup_tasks` RATHER THAN main.py CREATING THE TASK ITSELF. A task created before
uvicorn runs is created on a different event loop, never runs, and raises nothing. On
stage that is indistinguishable from an AIS feed with no data in it. The lifespan is the
only place with the live loop, so the factories are handed to it and cancelled with the
watchdog on shutdown. Task exceptions are now printed rather than swallowed.

REQUEST TO LANE E: adopt or replace both. If lane E would rather own the replay, take
`AisReplay` out of `03_src/main.py` — it depends on nothing but `AisTrack` and the
backend method.

## 2026-08-29 | ARCH -> C/B | the two constants that must move together

`consistency.Tolerances.class_min_report_sigma = 3.5` (nats) and
`eo_vlm.LANE_C_CLASS_FLOOR_NATS = 3.5` are the same number in two files, because lane B
must not import lane C. 3.5 is DERIVED: it puts the 0.95 uncalibrated ceiling (2.944
nats) and the maximum confusable-pair significance (3.453 nats) both below the floor.

MOVE ONE AND YOU MUST MOVE THE OTHER, and the failure if you do not is that `eo_vlm`'s
score report tells the operator the model is inert while the pipeline is firing on it.
That already happened once — it is the defect this session found. The only thing that
should ever move this number is MEASURING the model's agreement rate on labelled crops.

## 2026-08-29 | ARCH -> D | still open, not fixed here

`run_pipeline.run_scene()` builds `assoc_by_id` and discards it, so
`ContactUpdateEvent.association` is always `None` and `assoc_ambiguous` — a
defer-to-human trigger — can never reach the operator. One line:
`"associations": [a.model_dump(mode="json") for a in associations]` in the return dict.
