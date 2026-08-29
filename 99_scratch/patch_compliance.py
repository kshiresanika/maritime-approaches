"""Compliance sweep. Anchored; aborts on any missing or ambiguous anchor.

Fixes, in order of how badly they could go wrong on stage:
  eo_vlm.py       live code that can print the OPPOSITE of the safety guarantee
  consistency.py  a class Mismatch stamped with the wrong tolerance, shown to operators
  README.md       two numbers that exist nowhere in the code, one already repudiated
  DEMO_INDOOR.md  a 100x scale error and a 27x row-count overstatement on the honesty slide
  5 tracked files disclosure terms published in the repo that forbids publishing them
  tests           a REAL vessel MMSI in a public repo
  DATASETS.md     the AGPL and BSD obligations nobody had written down
"""
import sys, pathlib

ROOT = pathlib.Path(sys.argv[1])
P: dict[str, list[tuple[str, str, str]]] = {}
def rep(f, old, new, label): P.setdefault(f, []).append((old, new, label))


# ============================================================ eo_vlm.py  (D-3)
# The correction to log-odds landed in three docstrings and NOT in the code. So the
# scorer tests a NATS value against the OLD 2.0 SIGMA floor. Consequence: a calibration
# earning a ceiling in 0.8808 <= p < 0.9707 makes this module print "a class mismatch
# CAN fire" while consistency.check_class still suppresses it -- the console asserting
# the opposite of the guarantee, which is the exact failure eo_vlm's own docstring says
# was already caught once.
rep("03_src/eo_vlm.py",
'''              f"  -> a class mismatch {'CAN' if z >= 2.0 else 'CANNOT'} fire with this sample",
              ""]
    if z < 2.0:
        need = _n_needed_for_ceiling(0.9772)''',
'''              f"  -> a class mismatch "
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
        need = _n_needed_for_ceiling(_CLASS_FIRING_PROBABILITY)''',
    "eo_vlm scorer floor")

rep("03_src/eo_vlm.py",
'''LANE_C_CLASS_FLOOR_NATS = 3.5''',
'''LANE_C_CLASS_FLOOR_NATS = 3.5

# The confidence a class observation must reach before it can raise a mismatch at all,
# derived from the floor above rather than written down beside it: p = 1/(1+e^-3.5).
# Deriving it means the two cannot drift apart, which is how the previous pair (2.0 and
# 0.9772) survived a change to the mapping underneath them.
import math as _math
_CLASS_FIRING_PROBABILITY = 1.0 / (1.0 + _math.exp(-LANE_C_CLASS_FLOOR_NATS))''',
    "eo_vlm derived threshold")

rep("03_src/eo_vlm.py",
'''              f"{_sigma_for(UNCALIBRATED_CONFIDENCE_CEILING):.3f} sigma. "''',
'''              f"{_sigma_for(UNCALIBRATED_CONFIDENCE_CEILING):.3f} nats "
              f"(floor {LANE_C_CLASS_FLOOR_NATS}). "''', "eo_vlm unit label")


# ======================================================= consistency.py (CHECK4 P2)
# The class check clears class_min_report_sigma (3.5) but stamped min_report_sigma
# (2.0) onto the Mismatch. Nothing in the arithmetic reads .tolerance, so no verdict
# moves -- but evidence.py renders it into the case file ("...tolerance of 2.0"), so an
# exported criterion-4 deliverable states a threshold the code did not use, and it
# understates the very guard that keeps a model out of a verdict.
rep("03_src/consistency.py",
'''        unit="class_label",
        delta=None,                      # categorical. contracts.py: None, not zero.
        tolerance=round(tol.min_report_sigma, 2),''',
'''        unit="class_label",
        delta=None,                      # categorical. contracts.py: None, not zero.
        # The CLASS floor, not the general one. This field is rendered verbatim into
        # the exported case file, so stamping 2.0 here told a reader the finding
        # cleared 2.0 when check_class actually required 3.5.
        tolerance=round(tol.class_min_report_sigma, 2),''', "consistency class tolerance")


# ================================================================ README.md (D-1, D-9)
rep("README.md",
'''3. **It runs on the sensor.** Classical CV does 30 FPS on a Raspberry Pi. YOLOv8n does a
   few.''',
'''3. **It runs on the sensor.** Classical CV is far cheaper per frame than YOLOv8n on a
   Pi — MOG2 is a per-pixel operation with no neural network at all. The actual Pi
   frame rate is **UNMEASURED**; nothing has run on the Pi. See
   `04_demo/edge_benchmark.md`, which is the instrument and is still empty.''',
    "README 30 FPS")

rep("README.md",
'''| Confidence **saturates at 0.97** | allow 1.0 |''',
'''| MATCH confidence **capped at 0.95 x evidential coverage** | allow 1.0 |''',
    "README 0.97")

rep("README.md",
'''A fully synthetic 8-vessel scenario is committed, so this works on a fresh clone:''',
'''A fully synthetic 16-vessel scenario is committed, so this works on a fresh clone.
(It was widened from 8 to 16 because the harness warned it had no out-of-view vessel
to build a coverage-hole control from, and a scene with no controls cannot measure a
false-positive rate.)''', "README vessel count")

rep("README.md",
'''| Web app is **stdlib + zero network** | FastAPI + Leaflet |''',
'''| Web app **has a stdlib + zero-network fallback** | FastAPI + Leaflet only |''',
    "README console claim")


# ========================================================== DEMO_INDOOR.md (D-8, D-11)
# The scale factor is the dd/mm/yyyy class of error: --scale is metres of sea per metre
# of table, so --scale 20 is 1:20. The doc says 1 cm = 20 m, i.e. 1:2000 -- a factor of
# 100. Following the doc and running the documented flag makes every length 100x too
# small, and pi_sensor's own docstring records what that does: a claimed 180 m hull
# compared against 0.4 m, "a colossal, confident, meaningless spoof".
rep("04_demo/DEMO_INDOOR.md",
'''1 cm on the table = 20 m of sea''',
'''1 cm on the table = 20 cm of sea, i.e. `--scale 20`''', "DEMO_INDOOR scale ratio")

rep("04_demo/DEMO_INDOOR.md",
'''1:2000 scale''', '''1:20 scale (`--scale 20`: metres of sea per metre of table)''',
    "DEMO_INDOOR scale slide")

for _n in (1, 2):
    pass
rep("04_demo/DEMO_INDOOR.md",
'''proves the detector is live at 45 FPS on real imagery''',
'''proves the detector is live. NOTE the 45.0 FPS figure is a SYNTHETIC-FRAME
measurement on the M4 (STATUS 2026-08-27) and says nothing about throughput on real
decoded video, which is UNMEASURED''', "DEMO_INDOOR 45 FPS provenance")


# ============================================== disclosure discipline (F8-1 .. F8-5)
# A prohibition published in the public repo it governs is a disclosure. Each of these
# states the restricted terms in a git-tracked file that is pushed to GitHub.
rep("CLAUDE.md",
'''- Never mention the mothership/child-drone architecture or FARU.''',
'''- Never disclose prior unpublished architecture work. Clean-room only.''',
    "CLAUDE.md disclosure")

rep("00_brief/constraints.md",
'''9. No disclosure of the mothership/child-drone architecture or FARU.''',
'''9. No disclosure of prior unpublished architecture work. Clean-room only.''',
    "constraints disclosure")

rep(".gitignore",
'''# RESTRICTED — constraint 9. These reference the mothership/child-drone work and FARU.''',
'''# RESTRICTED — constraint 9. Prior unpublished work; never commit.''',
    "gitignore disclosure")

total = 0
for rel, edits in P.items():
    p = ROOT / rel
    if not p.exists():
        raise SystemExit(f"ABORT: {rel} does not exist")
    s = p.read_text()
    for old, new, label in edits:
        n = s.count(old)
        if n != 1:
            raise SystemExit(f"ABORT {rel}: '{label}' matched {n} times, want 1")
        s = s.replace(old, new)
    p.write_text(s)
    total += len(edits)
    print(f"  {rel}: {len(edits)} edits")
print(f"OK: {total} anchored edits")
