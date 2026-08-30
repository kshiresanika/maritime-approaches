#!/usr/bin/env python3
"""
patch_console_polish.py — six console changes, 2026-08-30.

1. GLYPH PROJECTION (a real bug, not a polish item). renderGlyphs called
   MAP.project(lat, lon) — the OFFLINE SHIM's two-argument signature. Real Leaflet's
   project() takes a LatLng and returns ABSOLUTE CRS pixels, not container pixels, so
   with real Leaflet it threw "Cannot read properties of null (reading 'lat')" and
   ABORTED renderMap() partway through, leaving the map at zoom 0. It was invisible
   while Leaflet came from a CDN that was blocked, because the shim ran instead.
2. fitBounds is guarded against non-finite points.
3. BASEMAP: CARTO now watermarks unkeyed tiles ("API Key required"). Replaced with
   Esri World Dark Gray, which needs no key.
4. Legend moves to the top-left; the zoom control moves out of its way.
5. The picture-in-picture doubles in both dimensions when maximised.
6. The rail widens by 50%. A scale bar is added for real Leaflet.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "03_src" / "web" / "index.html"
text = SRC.read_text()

def sub(old, new, label, count=1):
    global text
    n = text.count(old)
    if n != count:
        raise SystemExit(f"[FAIL] {label}: expected {count} occurrence(s), found {n}")
    text = text.replace(old, new)
    print(f"[ok] {label}")

# ============================================================== 1. glyph projection
sub(
    """    /* Container pixels for a lat/lon. The ONE method the glyph layer needs, and the
       only thing it needs from a map backend — real Leaflet answers the same question
       with latLngToContainerPoint(), so the overlay does not care which is running. */
    project(lat, lon) {
      if (!this._proj) return null;
      const [x, y] = this._proj.xy(lat, lon);
      return { x, y };
    }""",
    """    /* Container pixels for a lat/lon. The ONE method the glyph layer needs, and the
       only thing it needs from a map backend — real Leaflet answers the same question
       with latLngToContainerPoint(), so the overlay does not care which is running. */
    project(lat, lon) {
      if (!this._proj) return null;
      const [x, y] = this._proj.xy(lat, lon);
      return { x, y };
    }
    /* THE NAME THE COMMENT ABOVE ALWAYS PROMISED, now actually present.
       The glyph layer used to call project(lat, lon) — this shim's signature. Real
       Leaflet's project() takes a LatLng, not two numbers, and returns ABSOLUTE CRS
       pixels rather than container pixels; called the shim's way it threw
       "Cannot read properties of null (reading 'lat')" and aborted renderMap()
       mid-flight, which is what left the map sitting at zoom 0. The bug could only
       appear once real Leaflet was loading reliably — while it came from a blocked
       CDN the shim ran instead and the two-argument call was correct.
       Both backends now answer to latLngToContainerPoint([lat, lon]). */
    latLngToContainerPoint(ll) {
      const la = Array.isArray(ll) ? ll[0] : ll.lat;
      const lo = Array.isArray(ll) ? ll[1] : (ll.lng ?? ll.lon);
      return this.project(la, lo);
    }""",
    "1a. shim gains latLngToContainerPoint",
)

sub(
    """  if (!host || !MAP || typeof MAP.project !== "function") return;""",
    """  if (!host || !MAP || typeof MAP.latLngToContainerPoint !== "function") return;""",
    "1b. glyph guard checks the method it uses",
)

sub(
    """    const pt = MAP.project(it.lat, it.lon);
    if (!pt) continue;""",
    """    /* Wrapped, because this runs inside renderMap(): before this guard existed a
       single un-projectable point threw and took the REST OF THE MAP down with it —
       the fit, the fixes, everything after the glyph loop. One bad contact must cost
       one glyph, not the tactical picture. */
    let pt = null;
    try { pt = MAP.latLngToContainerPoint([it.lat, it.lon]); } catch { pt = null; }
    if (!pt || !Number.isFinite(pt.x) || !Number.isFinite(pt.y)) continue;""",
    "1c. glyph call site + try/catch",
)

# ============================================================== 2. fitBounds guard
sub(
    """  const box = MAP.getSize ? [MAP.getSize().x, MAP.getSize().y] : [0, 0];""",
    """  /* A single NaN or null in `pts` makes latLngBounds span the globe and fitBounds
     drop the map to zoom 0 — a black world map where a strait should be. Filtered
     rather than trusted: pts is assembled from a dozen optional sources above. */
  const fitPts = pts.filter(p => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1])
                                 && Math.abs(p[0]) <= 90 && Math.abs(p[1]) <= 180);
  const box = MAP.getSize ? [MAP.getSize().x, MAP.getSize().y] : [0, 0];""",
    "2a. filter non-finite fit points",
)

sub(
    """  if ((!fitted || grew) && pts.length > 1) {
    MAP.fitBounds(L.latLngBounds(pts).pad(0.12));
    fitted = true; fittedFor = box;
  }""",
    """  if ((!fitted || grew) && fitPts.length > 1) {
    MAP.fitBounds(L.latLngBounds(fitPts).pad(0.12));
    fitted = true; fittedFor = box;
  }""",
    "2b. fit on the filtered points",
)

# ============================================================== 3. basemap
sub(
    """  const BASEMAPS = {
    dark: {
      url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
      opts: { maxZoom: 20, subdomains: "abcd", detectRetina: true,
              attribution: "&copy; OpenStreetMap contributors &copy; CARTO" },
      chip: "CARTO dark basemap · needs network",
    },
    osm: {
      url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      opts: { maxZoom: 18, attribution: "&copy; OpenStreetMap contributors" },
      chip: "OSM basemap · needs network",
    },
  };""",
    """  /* CARTO WAS DROPPED, AND THE REASON IS WORTH RECORDING. dark_all still SERVES a
     200 and a valid 256 px PNG without a key — so every automated check passed — but
     the image it serves now has "API Key required" tiled across it. A basemap that
     fails by returning a watermark instead of an error is the worst failure mode
     there is: nothing downstream can detect it, and it is visible to everyone in the
     room. Verified by rendering candidate tiles side by side and LOOKING at them.

     Esri World Dark Gray needs no key, renders land grey on near-black water, and
     keeps its coastline crisp at the strait scale this demo lives at. The Reference
     layer is a separate transparent overlay carrying place names — Esri splits base
     and labels, which is useful here: labels can be dropped without losing the land.

     LICENCE, stated rather than assumed: these are Esri's public ArcGIS Online
     basemaps, free to use with the attribution string below, and the underlying data
     is OpenStreetMap under ODbL plus Esri/HERE/Garmin sources. They are FETCHED LIVE
     and nothing is cached or redistributed by this build. Esri's terms are written
     for ArcGIS customers and are not a clean open licence; for a hackathon demo with
     attribution that is defensible, and it is flagged in 02_data/DATASETS.md rather
     than left as a silent assumption. ?tiles=osm is the unambiguous fallback: plain
     OpenStreetMap under ODbL, no ambiguity, at the cost of being a light map. */
  const ESRI = "https://services.arcgisonline.com/ArcGIS/rest/services/";
  const ESRI_ATTR = "Tiles &copy; Esri — sources: Esri, HERE, Garmin, "
                  + "&copy; OpenStreetMap contributors";
  const BASEMAPS = {
    dark: {
      url: ESRI + "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      opts: { maxZoom: 16, attribution: ESRI_ATTR },
      /* Place names, as a transparent layer over the base. */
      overlay: ESRI + "Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
      chip: "Esri dark basemap · needs network",
    },
    osm: {
      url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      opts: { maxZoom: 18, attribution: "&copy; OpenStreetMap contributors" },
      chip: "OSM basemap · needs network",
    },
  };""",
    "3a. Esri dark gray replaces CARTO",
)

sub(
    """    const tiles = L.tileLayer(choice.url, choice.opts).addTo(MAP);
    $("cBasemap").textContent = choice.chip;""",
    """    const tiles = L.tileLayer(choice.url, choice.opts).addTo(MAP);
    if (choice.overlay)
      L.tileLayer(choice.overlay, { maxZoom: choice.opts.maxZoom, pane: "overlayPane" })
        .addTo(MAP);
    $("cBasemap").textContent = choice.chip;""",
    "3b. label overlay",
)

# ---- the scale bar, and the zoom control out of the legend's new corner
sub(
    """  MAP = L.map("map", { zoomControl: true, attributionControl: true })
        .setView([54.60, 11.30], 11);""",
    """  MAP = L.map("map", { zoomControl: false, attributionControl: true })
        .setView([54.60, 11.30], 11);

  /* CONTROLS GO BOTTOM-RIGHT — the only corner not already spoken for. Top-left is
     the legend (moved there so it reads first), top-right the status chips,
     bottom-left the picture-in-picture. */
  if (L.control && L.control.zoom) L.control.zoom({ position: "bottomright" }).addTo(MAP);
  /* A SCALE BAR IS NOT DECORATION ON THIS PANE. The hull marks are drawn to the
     CAMERA-OBSERVED length, so their size on screen is a measurement — and a
     measurement with no ruler beside it cannot be checked. Metric only: a maritime
     watch officer works in metres and nautical miles, and an imperial bar next to a
     bearing in degrees true is noise. The offline shim draws its own bar and north
     mark, so this is added only for real Leaflet. */
  if (L.control && L.control.scale)
    L.control.scale({ position: "bottomright", metric: true, imperial: false,
                      maxWidth: 190 }).addTo(MAP);""",
    "3c. controls bottom-right + scale bar",
)

# ============================================================== 4. legend top-left
sub(
    """#legend{
  position:absolute;left:8px;bottom:8px;z-index:600;background:rgba(8,11,16,.94);""",
    """/* TOP-LEFT, not bottom-left. It is the first thing a newcomer needs and the corner
   the eye lands on; the bottom-left corner belongs to the picture-in-picture. The
   zoom control was moved to bottom-right to clear this space — see initMap(). */
#legend{
  position:absolute;left:8px;top:8px;z-index:600;background:rgba(8,11,16,.94);""",
    "4a. legend to the top-left",
)

sub(
    """     long explanatory paragraph inside it. Anchored at bottom:8px it grew UPWARDS past
     the top of the pane and covered the tactical picture it was supposed to explain —
     a legend that hides the map is worse than no legend. Height is now bounded to
     less than half the pane and the list scrolls inside itself. */""",
    """     long explanatory paragraph inside it. Anchored at bottom:8px it grew UPWARDS past
     the top of the pane and covered the tactical picture it was supposed to explain —
     a legend that hides the map is worse than no legend. Height is now bounded to
     less than half the pane and the list scrolls inside itself. The cap still applies
     now that it is anchored at the top: it simply grows downwards instead. */""",
    "4b. legend cap note",
)

# ============================================================== 5. PiP doubles
sub(
    """#app.operator .pane:nth-of-type(1){
  position:absolute;left:14px;bottom:14px;z-index:700;
  width:clamp(300px,26vw,460px);""",
    """/* MAXIMISED SIZE DOUBLED in both dimensions on request — the feed is what an
   operator looks at while deciding, and at the old size a hull was a few dozen
   pixels. One custom property drives the width AND the 16:9 body height, so the two
   cannot drift apart. min(90vw, ...) keeps it on screen on a laptop, where twice the
   old floor would otherwise be wider than the window. */
#app.operator .pane:nth-of-type(1){
  --pipw:min(90vw,clamp(600px,52vw,920px));
  position:absolute;left:14px;bottom:14px;z-index:700;
  width:var(--pipw);""",
    "5a. PiP width doubled",
)

sub(
    """#app.operator .pane:nth-of-type(1) .body{height:calc(clamp(300px,26vw,460px)*0.5625)}""",
    """#app.operator .pane:nth-of-type(1) .body{height:calc(var(--pipw)*0.5625)}""",
    "5b. PiP body follows the width",
)

# ============================================================== 6. rail +50%
sub(
    """  position:absolute;right:0;z-index:600;width:min(38vw,470px);""",
    """  /* WIDENED 50% on request: min(38vw,470px) -> min(57vw,705px). This also buys back
     the room the queue rows were fighting over — see the .ident badge note above,
     which stays because the rail can still be narrow on a laptop. */
  position:absolute;right:0;z-index:600;width:min(57vw,705px);""",
    "6a. rail width +50%",
)

sub(
    """  position:absolute;top:50%;right:min(38vw,470px);transform:translateY(-50%);""",
    """  position:absolute;top:50%;right:min(57vw,705px);transform:translateY(-50%);""",
    "6b. rail toggle follows the rail",
)

# ---- dark styling for Leaflet's own controls, which ship light
sub(
    """.leaflet-control-attribution{background:rgba(8,11,16,.9)!important;color:var(--muted)!important;font-size:11px!important}""",
    """.leaflet-control-attribution{background:rgba(8,11,16,.9)!important;color:var(--muted)!important;font-size:11px!important}
/* Leaflet's controls ship white-on-white. Restyled rather than hidden: the scale bar
   is load-bearing (the hull marks are drawn to observed length) and a white slab in
   the corner of a dark console reads as a rendering fault. */
.leaflet-control-zoom a{
  background:rgba(8,11,16,.94)!important;color:var(--fg)!important;
  border-color:var(--rule)!important;
}
.leaflet-control-zoom a:hover{background:var(--raised)!important}
.leaflet-control-scale-line{
  background:rgba(8,11,16,.94)!important;border-color:#7f93a6!important;
  color:var(--fg)!important;font:700 12px ui-monospace,Menlo,monospace!important;
  padding:2px 6px!important;
}""",
    "6c. dark Leaflet controls",
)

SRC.write_text(text)
print(f"\nwrote {SRC}")
