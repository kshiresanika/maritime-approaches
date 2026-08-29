"""lane_c_seam_check.py — REGRESSION GUARD for the 2026-08-29 P0.

Run it before every demo:   python 99_scratch/lane_c_seam_check.py

WHAT IT GUARDS. verdict.py imported ConsistencyResult and independent_dimension_count
from consistency.py and neither existed, so run_pipeline, server AND app were all
unimportable -- every entry point including the rehearsed fallback. Two lanes that
deliberately do not import each other cannot catch that between them, so this script
is the thing that does: it exercises the seam by RUNNING it, not by importing it.

It also pins four properties that were each found broken by execution and would each
fail silently again:
  * coverage is non-zero for an honest vessel (a bare-list shim gives 0.0 -> UNKNOWN)
  * an UNCALIBRATED vision model cannot move a label OR a confidence
  * a low model confidence cannot erase a geometric finding
  * the classical sensor node's payload validates as an EoContact

Verify the seam that was broken: consistency -> verdict -> prioritizer -> evidence.
Needs no pyproj, so it runs here. association.py and geometry.py are UNCHANGED by this
session and were exercised in the 05:30 clean run."""
import sys, math
_HERE = __import__("pathlib").Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT / "03_src"), str(_ROOT / "04_demo")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from datetime import datetime, timezone
import consistency as C, verdict as V, prioritizer as P, evidence as E
from contracts import AisTrack, EoContact, Association

NOW = datetime(2026, 8, 30, 10, 0, 0, tzinfo=timezone.utc)
CAM = (54.60, 11.30)
ok = lambda b, m: print(("  PASS  " if b else "  FAIL  ") + m) or b

def track(**kw):
    d = dict(track_id="t1", claimed_mmsi="999000001", report_time_utc=NOW,
             claimed_lat_deg=54.63, claimed_lon_deg=11.30,
             claimed_sog_kn=11.0, claimed_cog_deg_true=90.0,
             claimed_heading_deg_true=90.0, claimed_ship_type="fishing",
             claimed_length_m=40.0)
    d.update(kw); return AisTrack(**d)

def contact(**kw):
    d = dict(contact_id="c1", frame_time_utc=NOW, frame_ref="f1",
             bbox_px=[100, 200, 340, 260], observed_bearing_deg_true=0.5,
             bearing_uncertainty_deg=2.5, observed_range_m=3340.0,
             range_uncertainty_m=800.0, detection_confidence=0.82,
             track_length_frames=9, camera_pose_ref="pose1")
    d.update(kw); return EoContact(**d)

def assoc(**kw):
    d = dict(association_id="a1", assoc_time_utc=NOW, track_id="t1", contact_id="c1",
             assoc_score=0.94, assoc_ambiguous=False, candidate_count=1,
             spatial_gap_m=90.0, time_delta_s=4.0)
    d.update(kw); return Association(**d)

results = []
print("\n=== 1. THE P0: does the seam import and run at all? ===")
full = contact(observed_length_m=41.0, observed_length_uncertainty_m=5.0,
               observed_class="fishing", observed_class_confidence=0.90,
               observed_heading_deg_true=91.0, observed_speed_ms=5.7)
r = C.check_pair(assoc(), track(), full, camera=CAM)
results.append(ok(isinstance(r, C.ConsistencyResult), "check_pair returns ConsistencyResult"))
results.append(ok(hasattr(r, "agreements") and hasattr(r, "uncomparable"),
                  "carries agreements + uncomparable"))
print(f"        mismatches={len(r.mismatches)} agreements={len(r.agreements)} "
      f"uncomparable={len(r.uncomparable)}")

print("\n=== 2. COVERAGE: the cap that keeps MATCH honest ===")
cov = V._coverage_fraction(r)
results.append(ok(cov > 0.0, f"honest vessel has NON-ZERO coverage ({cov:.2f}) "
                             f"-- a bare-list shim would give 0.00 and force UNKNOWN"))
bare = C.ConsistencyResult("a1", [], [], [])
results.append(ok(V._coverage_fraction(bare) == 0.0,
                  "a pair where nothing ran still reads 0.00 (no free confidence)"))

print("\n=== 3. HONEST VESSEL -> MATCH ===")
v, _ = V.decide(assoc(), r, decided_at_utc=NOW)
results.append(ok(v.label == "MATCH", f"label={v.label} confidence={v.confidence:.3f}"))

print("\n=== 4. CRITERION 3: the identity spoof still fires ===")
# 40 m fishing claimed, 248 m tanker observed -- the exemplar pair
r2 = C.check_pair(assoc(), track(),
                  contact(observed_length_m=248.0, observed_length_uncertainty_m=30.0,
                          observed_class="tanker", observed_class_confidence=0.99),
                  camera=CAM)
dims = {m.dimension: m for m in r2.mismatches}
print(f"        mismatches: " + ", ".join(
    f"{k} {m.significance} {m.severity}" for k, m in dims.items()))
v2, _ = V.decide(assoc(), r2, decided_at_utc=NOW)
results.append(ok(v2.label == "SPOOF", f"label={v2.label} confidence={v2.confidence:.3f} "
                                       f"subtype={v2.spoof_subtype}"))
results.append(ok(V.independent_dimension_count(r2, "major") >= 2,
                  f"independent major dimensions = {V.independent_dimension_count(r2,'major')}"))

print("\n=== 5. THE INVARIANT: can a model output reach a verdict? ===")
# length alone at ~3 sigma: below the spoof threshold on its own
c_len = contact(observed_length_m=52.0, observed_length_uncertainty_m=4.0)
r_len = C.check_pair(assoc(), track(), c_len, camera=CAM)
v_len, _ = V.decide(assoc(), r_len, decided_at_utc=NOW)
# same, plus a vision model at its UNCALIBRATED CEILING claiming a different class
c_both = contact(observed_length_m=52.0, observed_length_uncertainty_m=4.0,
                 observed_class="tug", observed_class_confidence=0.95)
r_both = C.check_pair(assoc(), track(), c_both, camera=CAM)
v_both, _ = V.decide(assoc(), r_both, decided_at_utc=NOW)
print(f"        geometry alone            : {v_len.label} {v_len.confidence:.3f}")
print(f"        + VLM class @ 0.95 ceiling: {v_both.label} {v_both.confidence:.3f}")
results.append(ok(not any(m.dimension == "class" for m in r_both.mismatches),
                  "an UNCALIBRATED model raises NO class mismatch (inert, as designed)"))
results.append(ok(v_both.label == v_len.label,
                  "the model did not change the LABEL"))
results.append(ok(abs(v_both.confidence - v_len.confidence) < 0.02,
                  f"the model did not move the CONFIDENCE "
                  f"(delta {abs(v_both.confidence-v_len.confidence):.4f})"))
# the erasure path
oc_low = C._observation_confidence(contact(observed_class="unknown",
                                           observed_class_confidence=0.05))
oc_none = C._observation_confidence(contact())
results.append(ok(oc_low >= V.LOW_OBSERVATION_CONFIDENCE,
                  f"a 0.05 model confidence can no longer erase a geometric finding "
                  f"({oc_none:.3f} -> {oc_low:.3f}, floor {V.LOW_OBSERVATION_CONFIDENCE})"))
# confusable pairs
maxc = C._probability_to_sigma(0.999) * C.DEFAULT_TOLERANCES.confusable_class_discount
results.append(ok(maxc < C.DEFAULT_TOLERANCES.class_min_report_sigma,
                  f"a confusable pair can NEVER fire (max {maxc:.3f} < "
                  f"{C.DEFAULT_TOLERANCES.class_min_report_sigma})"))

print("\n=== 6. CRITERION 4: uncomparable reaches the case file ===")
r3 = C.check_pair(assoc(), track(claimed_length_m=None, claimed_ship_type=None),
                  contact(), camera=CAM)
unc = {u.dimension: u for u in r3.uncomparable}
print("        " + " | ".join(f"{k}: {u.reason[:52]}" for k, u in list(unc.items())[:3]))
results.append(ok(any(u.suspicious for u in r3.uncomparable),
                  "a vessel broadcasting NO identity field at all is flagged suspicious"))
v3, _ = V.decide(assoc(), r3, decided_at_utc=NOW)
lims = V.limitation_strings(r3, v3)
results.append(ok(len(lims) > 1 and any("could not be compared" in s for s in lims),
                  f"limitation_strings carries {len(lims)} honest limits into evidence"))
print("        e.g. " + [s for s in lims if "could not be compared" in s][0][:96])

print("\n=== 7. DOWNSTREAM: prioritizer + evidence still build ===")
pr, notes = P.rank_all([v2], {"a1": track()}, {"a1": contact(observed_length_m=248.0)})
results.append(ok(len(pr) == 1, f"rank_all -> rank {pr[0].rank} score {pr[0].score:.3f}"))
results.append(ok(P.breakdown_sums_to_score(pr[0]),
                  "the published breakdown SUMS to the score (criterion 2's deliverable)"))
rec = E.build_record(verdict=v2, association=assoc(), mismatches=r2.mismatches,
                     track=track(), contact=contact(observed_length_m=248.0),
                     priority=pr[0], behaviour_notes=notes.get(v2.verdict_id),
                     consistency_limitations=V.limitation_strings(r2, v2), now=NOW)
results.append(ok(rec.limitations and len(rec.limitations) > 1,
                  f"EvidenceRecord carries {len(rec.limitations)} limitations "
                  f"(was 0 -- limitation_strings was dead code)"))
results.append(ok(rec.rationale_model is not None,
                  f"rationale is attributed: {rec.rationale_model!r}"))

print("\n=== 8. pi_sensor: the HTTP 400 P0 ===")
import pi_sensor
det = dict(track_id=3, cx=320.0, cy=250.0, x=100, y=200, w=240, h=60,
           waterline_y=260.0, frames=9, area=14400)
pose = dict(hfov_deg=66.0, yaw_deg_true=0.0, height_m=20.0, yaw_uncertainty_deg=2.5,
            pose_ref="pose1")
d1 = pi_sensor.to_contact(det, horizon_y=180.0, pose=pose, frame_w=640, frame_h=480,
                          scale=1.0, frame_time=NOW, frame_ref="f1")
try:
    EoContact(**d1); passed = True; err = ""
except Exception as e: passed = False; err = str(e).splitlines()[1]
results.append(ok(passed, f"classical node payload validates as EoContact {err}"))
results.append(ok(0.0 < d1["detection_confidence"] < 1.0,
                  f"detection_confidence = {d1['detection_confidence']} (not a hardcoded 1.0)"))
d20 = pi_sensor.to_contact(det, horizon_y=180.0, pose=pose, frame_w=640, frame_h=480,
                           scale=20.0, frame_time=NOW, frame_ref="f1")
results.append(ok(abs(d20["observed_range_m"] - d1["observed_range_m"] * 20) < 1.0,
                  f"--scale is APPLIED: range {d1['observed_range_m']} -> "
                  f"{d20['observed_range_m']} m at scale 20"))
results.append(ok(abs(d20["observed_bearing_deg_true"] - d1["observed_bearing_deg_true"]) < 1e-9,
                  "bearing is NOT scaled (an angle is dimensionless)"))
r_s1 = C.check_pair(assoc(), track(), EoContact(**d1), camera=CAM)
r_s20 = C.check_pair(assoc(), track(), EoContact(**d20), camera=CAM)
print(f"        scale 1  -> observed_length {d1['observed_length_m']} m")
print(f"        scale 20 -> observed_length {d20['observed_length_m']} m")

print(f"\n{'='*70}\n{sum(results)}/{len(results)} checks passed\n{'='*70}")
sys.exit(0 if all(results) else 1)
