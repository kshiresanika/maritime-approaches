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
STATUS: OPEN

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
