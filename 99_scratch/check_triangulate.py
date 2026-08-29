"""
Verifies triangulate.py's SOLVER against hand-computed geometry.

Runs with no pyproj and no repo: the solver is pure numpy in a flat East-North frame
by design, which is exactly so it can be checked like this. Every expected value below
is derived by hand in the comment above it, not read back out of the code.
"""
import math, sys
import numpy as np
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "03_src"))
from triangulate import (solve_local, ellipse_from_cov, crossing_angle_deg,
                         single_camera_equivalent, MIN_CROSSING_ANGLE_DEG)

FAIL = 0
def check(name, cond, detail=""):
    global FAIL
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    if not cond: FAIL += 1

print("=" * 74)
print("1. PERPENDICULAR CROSSING — the intersection is known by hand")
print("=" * 74)
# Station A at the origin, bearing 090 (due East)  -> ray along +East
# Station B at (5000 E, 5000 N), bearing 180 (due South) -> ray along -North
# The two rays meet at (5000 E, 0 N). No arithmetic needed to see it.
st = np.array([[0.0, 0.0], [5000.0, 5000.0]])
br = np.array([90.0, 180.0])
sg = np.array([1.0, 1.0])
p, cov, along = solve_local(st, br, sg, np.array([5000.0, 5000.0]))
check("fix East  == 5000 m", abs(p[0] - 5000) < 1e-6, f"{p[0]:.6f}")
check("fix North ==    0 m", abs(p[1] - 0) < 1e-6, f"{p[1]:.6f}")
check("both along-ranges positive (in front of both)", bool(np.all(along > 0)),
      f"{along[0]:.0f}, {along[1]:.0f}")
check("crossing angle == 90 deg", abs(crossing_angle_deg(br.tolist()) - 90) < 1e-9)

# Hand-computed sigma: each station is 5000 m from the fix with 1 deg bearing sigma,
# so each constrains its own perpendicular direction to 5000*radians(1) = 87.266 m.
# Perpendicular geometry -> the two constraints are orthogonal -> a CIRCLE of that
# radius. Both semi-axes must equal 87.266 m.
want = 5000 * math.radians(1.0)
major, minor, orient = ellipse_from_cov(cov)
check("semi-major == r*sigma_rad", abs(major - want) < 1e-6, f"{major:.4f} vs {want:.4f}")
check("semi-minor == r*sigma_rad", abs(minor - want) < 1e-6, f"{minor:.4f} vs {want:.4f}")
check("ellipse is circular at 90 deg crossing", abs(major - minor) < 1e-9)

print()
print("=" * 74)
print("2. THE AMPLIFICATION LAW — 1/(sqrt2 sin(g/2)), derived and proven, NOT 1/sin(g)")
print("=" * 74)
# Two stations 10 km apart on an East baseline, both looking at a target far to the
# North. As the target recedes the crossing angle shrinks and the fix must degrade
# exactly as 1/(sqrt2 sin(g/2)) -- the eigenvalue result derived in the module's
# MIN_CROSSING_ANGLE_DEG note. THE FIRST VERSION OF THIS TEST EXPECTED 1/sin(g), the
# figure usually quoted, and it was the TEST that was wrong: at 53.1 deg the solver
# gave 1.581 against the eigenvalue law's 1.582 and 1/sin's 1.25. Proving the law
# here is what caught it.
amp_law = lambda g: 1.0 / (math.sqrt(2) * math.sin(math.radians(g) / 2))
print(f"  {'target N (m)':>12} {'crossing':>9} {'major (m)':>10} {'law':>7} {'ratio':>7}")
prev = None
for north in (5000, 10000, 20000, 40000, 80000):
    A = np.array([-5000.0, 0.0]); B = np.array([5000.0, 0.0])
    T = np.array([0.0, float(north)])
    def brg(s, t):
        d = t - s
        return math.degrees(math.atan2(d[0], d[1])) % 360.0
    st2 = np.vstack([A, B]); br2 = np.array([brg(A, T), brg(B, T)])
    rA = np.linalg.norm(T - A)
    p2, cov2, al2 = solve_local(st2, br2, np.array([1.0, 1.0]), np.array([rA, rA]))
    xa = crossing_angle_deg(br2.tolist())
    mj, mn, _ = ellipse_from_cov(cov2)
    amp = amp_law(xa)
    # cross-range sigma at this range, which is what the fix would equal at 90 deg
    base = rA * math.radians(1.0)
    print(f"  {north:>12} {xa:>8.1f}d {mj:>10.0f} {amp:>7.2f} {mj/base:>7.2f}")
    check(f"    fix error tracks 1/(v2 sin g/2) at {north} m N",
          abs(mj / base - amp) < 0.02 * amp, f"{mj/base:.3f} vs {amp:.3f}")
    check(f"    fix lands on the target at {north} m N",
          np.linalg.norm(p2 - T) < 1.0, f"{np.linalg.norm(p2-T):.4f} m")

print()
print("=" * 74)
print("3. TWO CAMERAS vs ONE — the actual pitch number, on scene01's measured sigmas")
print("=" * 74)
# Lane C's MEASURED single-camera figures, quoted in the console and in memory:
#   cross-range sigma  218 m  at 5 km   -> implies bearing sigma 2.50 deg
#   along-range sigma 1250 m  at 5 km   (monocular waterline ranging)
R = 5000.0
sig_bearing_deg = math.degrees(218.0 / R)
one_major, one_minor = single_camera_equivalent(R, sig_bearing_deg, 1250.0)
print(f"  ONE camera : {one_major:6.0f} x {one_minor:5.0f} m  "
      f"(bearing sigma {sig_bearing_deg:.2f} deg implied by 218 m at 5 km)")
check("single-camera major axis is the 1250 m range error",
      abs(one_major - 1250) < 1, f"{one_major:.0f}")
check("single-camera minor axis is the 218 m cross-range error",
      abs(one_minor - 218) < 1, f"{one_minor:.0f}")

for xdeg in (90, 60, 45, 30, 20):
    # Place two stations so their bearings to one target cross at exactly xdeg.
    T = np.array([0.0, 0.0])
    a1 = math.radians(90.0); a2 = a1 + math.radians(xdeg)
    A = T - R * np.array([math.sin(a1), math.cos(a1)])
    B = T - R * np.array([math.sin(a2), math.cos(a2)])
    st3 = np.vstack([A, B])
    br3 = np.array([math.degrees(a1) % 360.0, math.degrees(a2) % 360.0])
    p3, cov3, al3 = solve_local(st3, br3, np.array([sig_bearing_deg]*2),
                                np.array([R, R]))
    mj, mn, _ = ellipse_from_cov(cov3)
    gain = one_major / mj
    flag = "" if xdeg >= MIN_CROSSING_ANGLE_DEG else "  (below the refusal threshold)"
    print(f"  TWO @ {xdeg:>2}d : {mj:6.0f} x {mn:5.0f} m   "
          f"major axis {gain:5.1f}x smaller than one camera{flag}")
    check(f"    two-camera fix beats one camera at {xdeg} deg crossing", gain > 1.0,
          f"{gain:.2f}x")
    check(f"    fix lands on the target at {xdeg} deg", np.linalg.norm(p3 - T) < 1.0)

print()
print("=" * 74)
print("4. THE FAILURE THAT LOOKS PERFECT — a crossing BEHIND a camera")
print("=" * 74)
# A HAND-BUILT rearward crossing, so the sign is known before the code runs.
#   A at (0,0) looking due EAST  (090) -> its ray is the half-line y=0, x>0.
#   B at (5000,5000) looking due NORTH (000) -> its ray is x=5000, y>5000.
# As LINES they meet at (5000, 0). As RAYS they never meet: (5000,0) is 5000 m
# ASTERN of B. The solver must still return (5000,0) -- it is the least-squares
# answer to the linear constraints -- and B's along-range must come back NEGATIVE.
# That negative number is the ONLY evidence that this perfect-looking fix is
# physically impossible, and gate 6 in triangulate() reads exactly this array.
st4 = np.array([[0.0, 0.0], [5000.0, 5000.0]])
br4 = np.array([90.0, 0.0])
p4, cov4, al4 = solve_local(st4, br4, np.array([1.0, 1.0]), np.array([5000.0, 5000.0]))
print(f"  fix at E={p4[0]:.0f} N={p4[1]:.0f}; along-ranges A={al4[0]:.0f} B={al4[1]:.0f}")
check("the line-intersection is still found at (5000, 0)",
      abs(p4[0] - 5000) < 1e-6 and abs(p4[1]) < 1e-6, f"E={p4[0]:.3f} N={p4[1]:.3f}")
check("station A, which really can see it, gets a POSITIVE along-range",
      al4[0] > 0, f"{al4[0]:.0f} m")
check("station B, which is pointing away, gets a NEGATIVE along-range",
      al4[1] < 0, f"{al4[1]:.0f} m")
check("...so gate 6 would refuse this fix", bool(np.any(al4 <= 0)))

# Now an unambiguous one: both stations on the same side, target behind both.
st5 = np.array([[0.0, 0.0], [1000.0, 0.0]])
br5 = np.array([0.0, 350.0])       # both looking NORTH; true crossing is far north
p5, cov5, al5 = solve_local(st5, br5, np.array([1.0, 1.0]), np.array([5000.0, 5000.0]))
check("north-looking pair puts the fix NORTH (both along-ranges > 0)",
      bool(np.all(al5 > 0)), f"{al5[0]:.0f}, {al5[1]:.0f}")

print()
print("=" * 74)
print("5. DEGENERATE GEOMETRY")
print("=" * 74)
st6 = np.array([[0.0, 0.0], [200.0, 0.0]])
br6 = np.array([10.0, 10.4])       # essentially parallel
xa6 = crossing_angle_deg(br6.tolist())
check("near-parallel bearings are detected as a shallow crossing",
      xa6 < MIN_CROSSING_ANGLE_DEG, f"{xa6:.2f} deg < {MIN_CROSSING_ANGLE_DEG}")
st7 = np.array([[0.0, 0.0], [200.0, 0.0]])
br7 = np.array([45.0, 45.0])       # exactly parallel -> singular
p7, cov7, al7 = solve_local(st7, br7, np.array([1.0, 1.0]), None)
check("exactly parallel bearings return NaN rather than an infinity",
      bool(np.all(np.isnan(p7))), "singular matrix caught")

print()
print("=" * 74)
print("6. WEIGHTING — the noisier bearing must pull the fix less")
print("=" * 74)
# Same geometry as test 1 but station B's bearing is 10x more uncertain. The fix must
# move TOWARDS satisfying A (the sharper bearing) and the ellipse must elongate along
# the direction B was the only one constraining.
p8, cov8, _ = solve_local(st, br, np.array([1.0, 10.0]), np.array([5000.0, 5000.0]))
mj8, mn8, _ = ellipse_from_cov(cov8)
check("a 10x noisier second bearing inflates the fix ellipse ~10x on one axis",
      9.0 < mj8 / mn8 < 11.0, f"aspect {mj8/mn8:.2f}")
check("the sharp bearing's own axis is unaffected", abs(mn8 - want) < 1e-6,
      f"{mn8:.3f} vs {want:.3f}")

print()
print("=" * 74)
print("7. WEIGHT-ITERATION CONVERGENCE — the claim that 2 passes is enough")
print("=" * 74)
import triangulate as T
orig = T._WEIGHT_PASSES
res = {}
for passes in (1, 2, 3, 6):
    T._WEIGHT_PASSES = passes
    pp, _, _ = solve_local(st2 if False else np.array([[-5000.,0.],[5000.,0.]]),
                           np.array([20.0, 340.0]), np.array([1.5, 1.5]),
                           np.array([1000.0, 1000.0]))   # deliberately bad seed ranges
    res[passes] = pp
T._WEIGHT_PASSES = orig
d23 = float(np.linalg.norm(res[2] - res[3]))
d36 = float(np.linalg.norm(res[3] - res[6]))
print(f"  pass2->3 moves {d23:.4f} m ; pass3->6 moves {d36:.4f} m  (seed range was 5x wrong)")
check("a 3rd pass moves the fix less than 0.1 m", d23 < 0.1, f"{d23:.5f} m")
check("further passes change nothing", d36 < 1e-6, f"{d36:.2e} m")

print()
print("=" * 74)
print("ALL CHECKS PASSED" if not FAIL else f"FAILED: {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
