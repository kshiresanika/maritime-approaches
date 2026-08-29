"""
lane_e_contrast.py — WCAG contrast for the console palette. MEASURED, not asserted.

The brief says contrast >= 7:1 (WCAG AAA) and legible from three metres on a projector.
"Dark theme with nice colours" is how that requirement gets quietly missed: saturated
red on near-black is the classic failure — it LOOKS high contrast and computes to about
5:1, below AA-large and nowhere near AAA.

Every colour in 03_src/web/index.html is listed here with its measured ratio against the
two backgrounds it can appear on. If a colour is changed there it must be changed here
and this must be re-run.

    python3 99_scratch/lane_e_contrast.py
"""

from __future__ import annotations


def _lin(c: float) -> float:
    """sRGB -> linear. The 0.03928 branch is the gamma toe; using a plain 2.2 power
    here overstates contrast for dark colours, which is exactly where this palette
    lives."""
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hexcolour: str) -> float:
    h = hexcolour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


BG = "#080b10"        # page ground
PANEL = "#10151c"     # pane ground — the harder of the two, so it is the gate
RAISED = "#18202a"    # selected row / evidence card

FOREGROUNDS = {
    "text primary":        "#f4f8fc",
    "text secondary":      "#c8d6e4",
    "text muted (labels)": "#9fb3c8",
    # MEASURED FIX. #2b3644 was chosen by eye and came in at 1.50:1 — a border that
    # separates the four panes carries meaning, so 1.4.11 applies and it must reach
    # 3:1. #586b82 is the first candidate that does. It reads as a heavier rule than a
    # dark theme usually wants, which is correct here: the judge is three metres back.
    "rule / border":       "#586b82",   # non-text: 3:1 is the bar, not 7:1
    "MATCH grey":          "#b6c6d6",
    "DARK amber":          "#ffb64d",
    # MEASURED FIX, AND THE ONE THIS SCRIPT WAS WRITTEN TO CATCH. #ff7a7a passes on
    # the panel at 7.26 and FAILS on a selected row at 6.50 — and a SPOOF row is
    # exactly the row that gets selected, so the failure would land on the highest-
    # stakes text on the screen. Held to the worst of the three grounds, not the best.
    "SPOOF red":           "#ff9090",
    "UNKNOWN violet":      "#c9a6ff",
    "defer badge":         "#ffd166",
    "ok / online":         "#6ee7a8",
    "offline":             "#ff9aa2",
    "accent (rank 1)":     "#7fd4ff",
    "claimed side":        "#9fc2ff",
    "observed side":       "#ffd08a",
}

# Non-text elements only need 3:1 (WCAG 1.4.11). Everything else is held to 7:1.
NON_TEXT = {"rule / border"}

print("=" * 78)
print("WCAG CONTRAST — console palette. Gate: 7:1 for text, 3:1 for non-text.")
print("=" * 78)
print(f"{'colour':<22} {'hex':<9} {'vs panel':>9} {'vs bg':>8} {'vs raised':>10}  verdict")
print("-" * 78)

fails = []
for name, hexv in FOREGROUNDS.items():
    r_panel = ratio(hexv, PANEL)
    r_bg = ratio(hexv, BG)
    r_raised = ratio(hexv, RAISED)
    gate = 3.0 if name in NON_TEXT else 7.0
    worst = min(r_panel, r_bg, r_raised)
    ok = worst >= gate
    if not ok:
        fails.append((name, hexv, round(worst, 2), gate))
    print(f"{name:<22} {hexv:<9} {r_panel:>9.2f} {r_bg:>8.2f} {r_raised:>10.2f}  "
          f"{'PASS' if ok else 'FAIL'} (gate {gate:.0f}:1, worst {worst:.2f})")

# ---------------------------------------------------------------------------
# SECOND TABLE: the SOLID verdict badges invert — dark text on the verdict colour.
# That is a different contrast pair from every row above and was missed on the first
# pass: the palette was verified as FOREGROUNDS and the badges use the same values as
# BACKGROUNDS. A colour can pass one and fail the other.
# ---------------------------------------------------------------------------
BADGE_INK = "#05070a"
print()
print("SOLID BADGES — dark ink on the verdict colour (a different pair entirely):")
for name in ("MATCH grey", "DARK amber", "SPOOF red", "UNKNOWN violet"):
    hexv = FOREGROUNDS[name]
    r = ratio(BADGE_INK, hexv)
    ok = r >= 7.0
    if not ok:
        fails.append((name + " (as badge ground)", hexv, round(r, 2), 7.0))
    print(f"  {BADGE_INK} on {hexv} ({name:<16}) {r:>6.2f}  {'PASS' if ok else 'FAIL'}")

print("-" * 78)
print(f"backgrounds: page {BG}  panel {PANEL}  raised {RAISED}")
print(f"panel vs page: {ratio(PANEL, BG):.2f}:1 — pane separation is carried by the "
      f"border, not by this.")
if fails:
    print()
    print("FAILURES:")
    for name, hexv, worst, gate in fails:
        print(f"  {name} {hexv}: {worst}:1 < {gate}:1")
raise SystemExit(1 if fails else 0)
