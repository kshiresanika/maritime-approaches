# BUILD PROMPT — real basemap under the tactical picture

*Paste this whole file as the task. It assumes no memory of the conversation that produced it.*

---

## 1. What you are working on

`~/Desktop/MaritimeApproaches` — a maritime situational-awareness console built for the
EDTH Hamburg hackathon (Topic 02). It fuses an electro-optical feed with recorded Danish
AIS to flag dark vessels, AIS spoofing and anomalies near critical infrastructure. The
web console is **one file**: `03_src/web/index.html`, served by `03_src/server.py`.

It already has two layouts, switched by a class on `#app`:

- **console** — four panes: live sensor · tactical picture · priority queue · evidence.
- **operator** — full-bleed map, the sensor pane as a minimisable picture-in-picture,
  queue + evidence in a rail that slides away. Keys: `v` layout, `m` PiP, `r` rail,
  `l` legend.

**The dual view is signed off. Keep it exactly as it is.** Every change below happens
*underneath* the existing map layer.

Read first: `04_demo/RUNBOOK.md`, `04_demo/TWO_CAMERAS.md`, `CLAUDE.md`, and the top of
`03_src/web/index.html` §5b (the offline map shim) and §6 (`renderMap`).

---

## 2. Decisions already made — do not reopen these

| Decision | Answer |
|---|---|
| **Location** | **Keep Fehmarn Belt.** The AIS was genuinely recorded there. Do not move the scene to the Elbe. |
| **Basemap style + delivery** | **Dark, OSM-derived, pre-baked offline.** No live tile fetches at run time. |
| **Map engine** | **Raster basemap painted UNDER the existing SVG.** Do **not** introduce MapLibre/Leaflet-GL. The fixed north-up plan view stays. |
| **Realism fixes** | **Snap both cameras to real shoreline** and **put the infrastructure on a real, cited cable route.** |

### The one tension, and its resolution

"Dark **vector** cartography" and "**raster** under the SVG" only reconcile one way:
**render the vector data down to raster tiles ONCE, offline, and ship the PNGs.** You get
vector-quality dark cartography with no vector renderer at run time. Bake on a machine
with normal internet; the console then reads local files only.

---

## 3. What must not break

1. **No network at run time.** The console already refuses to depend on the internet — the
   map has a dependency-free SVG backend precisely because the CDN was unreachable at the
   venue. A basemap that fetches tiles live re-introduces the failure that was engineered
   out. Tiles are local files or they do not ship.
2. **`renderMap()` keeps drawing in lat/lon** through `L.polygon` / `L.polyline` /
   `L.circleMarker`. The FOV wedges, the two-camera overlap, cross-fix ellipses, hull
   glyphs and the bearing rays must render **unchanged**.
3. **The glyph layer** (`#mapGlyphs`, HTML over the map, projected via `MAP.project()`)
   must stay pixel-aligned with the SVG at every size. It is fixed-screen-size on purpose.
4. **Both layouts** must survive at 1280×800, 1440×900 and 1920×1080 with **zero
   horizontal or vertical overflow** and zero page errors.
5. **The honesty rules are absolute** (`CLAUDE.md`): no real vessel or operator named;
   every verdict carries a confidence and a defer-to-human flag; the LLM writes rationale,
   never verdicts; anything synthetic is labelled as synthetic on screen.

---

## 4. The work, in order

### Task 1 — bake the tiles

Scene bounding box (both camera wedges at 8 km max range, before padding):

```
lat  54.5407 .. 54.7193      lon  11.1890 .. 11.5284
~20 km N–S  ×  ~22 km E–W    Fehmarn Belt, between Lolland (DK) and Fehmarn (DE)
```

Bake **z11–z14** with ~10% padding. That is roughly **120 tiles at 256 px** — a few MB.
Store under `03_src/web/basemap/{z}/{x}/{y}.png` and serve them from the existing
`/assets` static mount (or add a sibling mount).

- Source a **dark OSM-derived** style (Protomaps PMTiles rendered to raster, or any
  equivalent dark basemap you can legally cache).
- **Water must read as water and land as land at a glance** — that is the entire point.
  Coastline, land fill, a little place labelling. No roads clutter at z11–12.
- Write `03_src/web/basemap/MANIFEST.json`: source, style, licence, bbox, zoom range,
  bake date, tile count, total bytes.

> **Known blocker:** CDNs and tile servers are blocked from the sandboxes used to build
> this project. The bake step must run on the Mac, which has normal internet. Do not
> spend cycles trying to fetch tiles from a sandbox — it will 403.

**Licence is not optional.** OSM-derived data is **ODbL**: attribution is required and the
terms must be honoured for a cached copy. Record the licence in
`02_data/DATASETS.md` **before** the tiles go on a screen, and put the attribution string
permanently in the map pane (the console already has a `#mapFoot` for this).

### Task 2 — paint them under the SVG

In `03_src/web/index.html`, inside the offline map shim (§5b):

- Add a tile layer that draws **beneath** the existing `<svg>` in `#map` — an absolutely
  positioned div of `<img>` tiles, or a `<canvas>`. The SVG stays on top, unchanged.
- Position tiles from the shim's existing projection (`Proj.xy` / `Map.project`), so the
  basemap and the vectors share **one** coordinate system by construction. Do not add a
  second projection — two projections is how the map starts lying.
- Pick the zoom level whose scale is closest to the current view; redraw on
  `_redraw()`, which already fires on resize and layout change.
- **Degrade silently and completely.** A missing tile is a dark square, not an error and
  not a broken-image icon. The console must look exactly as it does today if
  `basemap/` is absent — that is the fallback, and it must stay working.
- Update the `#cBasemap` chip: it currently says *offline plan view · no basemap*. It
  should now name the source and licence, and still say *no basemap* when tiles are
  missing.

### Task 3 — make it read like Qatium

With real land under it, retune so the data is the brightest thing on screen:

- Basemap sits back: land near-black, water a shade lighter, labels muted. If the baked
  style is too bright, dim it with a CSS filter over the tile layer rather than re-baking.
- Keep the existing verdict palette and glyph shapes (`✓ ! ? ·`) exactly.
- The FOV wedges and the overlap will now sit over real coastline — check their fill
  opacities still let the coast read through. Adjust opacity, not colour.
- Keep the scale bar and north arrow.

### Task 4 — snap the cameras to real shoreline

Current poses — **verify both against the basemap; at least one is almost certainly
standing in open water:**

```
camera A  synthetic-auto-182.71T   54.64743N  11.31318E   bore 182.7°T  hfov 60°  8 km  h 25 m
camera B  synthetic-B-240.92T      54.61258N  11.40432E   bore 240.9°T  hfov 60°  8 km  h 25 m
```

Move both to real coastal points on the Fehmarn Belt, then **re-derive everything that
depends on them**. Candidate anchors on Lolland's south coast — *verify before using, I
have not confirmed these against a coastline dataset:* Rødbyhavn (~54.65N, 11.35E) and
the Hyllekrog spit (~54.61N, 11.47E). They give a ~10 km baseline with both cameras
looking south into the shipping lane, which is what the crossing geometry needs.

Constraints on the new placement:

- **Crossing angle in the overlap should stay in the 45–75° band.** Below ~30° the
  cross-fix ellipse degenerates into a sliver and the second camera stops earning its
  place. `99_scratch/lane_g_two_camera_check.py` measures this — run it.
- Camera height 25 m must remain plausible for the chosen point (a mast or a low bluff).
- Regenerate in this order and re-run the checks after each:
  `make_scene_video.py` (both cuts) → `make_second_view.py` → the full battery.

### Task 5 — a real cable route

Replace the illustrative infrastructure line with a **published** Baltic cable or
interconnector corridor through the Fehmarn Belt, cited in `02_data/DATASETS.md` with its
licence.

- First place to look: **EMODnet Human Activities** (telecommunication cables /
  submarine cables) — EU source, clear terms. *Unverified by me; confirm coverage and
  licence yourself.*
- **It must stay labelled approximate.** The tooltip already says *"protected route
  (illustrative, not surveyed)"* — change it to name the source and keep the hedge. Do not
  present a published corridor as a surveyed position.
- The prioritiser weights proximity to infrastructure heavily, so moving this line
  **changes the ranking**. Re-run `lane_g_console_check.py` and check the queue order is
  still explicable.

---

## 5. Gotchas that have already cost this project time

- **A `display:none` child is REMOVED from a CSS grid, not collapsed.** This has broken
  the layout three separate times (`#app`'s implicit row, the map pane's heading, the
  banner rows). Every grid child in `#app` and `#grid` is now placed explicitly — keep it
  that way.
- **`max-height:100%` against a grid row sized by its own item is circular** and gets
  dropped. That silently mis-scaled every detection box for the life of the project. If
  you add a layer to `#map`, pin it with `position:absolute; inset:0`.
- **`uvicorn` answers 404 to a WebSocket upgrade with no ws transport installed** and does
  not warn. `pip install websockets`.
- **Do not fetch anything at run time.** See §3.1.

---

## 6. Verification bar — measured, not eyeballed

Reproduce this before claiming done:

```bash
for t in check_triangulate lane_g_two_camera_check lane_g_console_check lane_video_check lane_video_e2e lane_f_failover_check; do
  printf "  %-30s" "$t"
  if python3 99_scratch/$t.py >/tmp/$t.log 2>&1 && grep -q "ALL CHECKS PASSED" /tmp/$t.log; then
    echo PASS
  else
    echo "FAIL — tail -40 /tmp/$t.log"
  fi
done
python3 -m pytest -q tests/
```

Expect six PASS, then `163 passed`.

If your Python cannot import `movingpandas` you will see `145 passed, 18 skipped`
instead — the whole of `tests/test_ais_trajectory.py` skips itself, which is why
this file's expected count was wrong until 2026-08-30. A skipped file is not a
passing file. Install the geo stack before trusting a green run:
`pip install movingpandas geopandas shapely`.

**This repo is developed on macOS. Do not use `timeout` in any command you hand back —
it is GNU coreutils and is not present on a stock Mac.** An earlier version of this loop
used it; the command was not found, the `&&` short-circuited, and all six checks reported
FAIL without running. Give each check its own log file, not one shared `/tmp/o.txt`, or a
failure points at the wrong output. Assume commands are pasted into an interactive zsh,
where a leading `#` is **not** a comment.

Then render the page headless (Playwright + Chromium) in **both** layouts at 1280×800,
1440×900 and 1920×1080 and assert:

| Assertion | Expected |
|---|---|
| `scrollWidth == clientWidth` and `scrollHeight == clientHeight` | both layouts, all three sizes |
| page errors | zero |
| basemap tiles present in `#map` | > 0 |
| SVG shape count | unchanged from before your change |
| `#mapGlyphs .gm` count | unchanged, and each within 2 px of its pre-change position |
| with `basemap/` renamed away | page renders exactly as today |

Add a new check `99_scratch/lane_h_basemap_check.py` covering: tile manifest present and
parseable, licence recorded in `02_data/DATASETS.md`, both camera positions on land per
the coastline dataset, crossing angle in the overlap within 45–75°.

Append one dated line to `STATUS.md` in the existing house style: what changed, what was
**measured**, and what remains **UNVERIFIED**.

---

## 7. Done means

- Real dark coastline under the tactical picture, **entirely offline**, licence recorded
  and attributed on screen.
- Both cameras on real shoreline; the cable route cited; crossing angle still healthy.
- Both layouts unchanged in behaviour, no overflow, no errors, glyphs still aligned.
- The whole battery green, and a new check guarding the basemap.
- The console still renders correctly with the basemap directory deleted.
