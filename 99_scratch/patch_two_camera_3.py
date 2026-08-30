#!/usr/bin/env python3
"""99_scratch/patch_two_camera_3.py — the tactical picture learns about two cameras."""
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "03_src/web/index.html"
s = p.read_text(encoding="utf-8")


def rep(old, new, label):
    global s
    assert s.count(old) == 1, f"anchor {label}: {s.count(old)}"
    s = s.replace(old, new)
    print(f"  {label}")


# ---------------------------------------------------------------- 1. helpers
rep('''function renderMap() {
  if (!MAP || !LAYER || !S.stage) return;''',
'''/* ===========================================================================
   6a. TWO CAMERAS — the geometry the map needs and nothing more.
   =========================================================================== */

/* A camera's field of view as a closed polygon: apex, arc, apex. Convex for any
   half-angle under 90 degrees, which every real lens here is — and convexity is what
   lets the overlap below be computed by clipping. */
function fovPolygon(pose, step) {
  const half = pose.fov_half_angle_deg ?? (pose.hfov_deg || 60) / 2;
  const rng = pose.max_range_m || 8000, b = pose.boresight_deg_true || 0;
  const arc = [[pose.lat_deg, pose.lon_deg]];
  for (let a = -half; a <= half + 1e-9; a += (step || 2))
    arc.push(destination(pose.lat_deg, pose.lon_deg, b + a, rng));
  arc.push([pose.lat_deg, pose.lon_deg]);
  return arc;
}

/* Sutherland-Hodgman: clip a convex subject polygon by a convex clip polygon.
   WHY THIS IS CORRECT IN RAW LAT/LON. Clipping is a sequence of half-plane
   intersections, and half-planes are preserved by any AFFINE map. Over a few tens of
   kilometres the lat/lon grid is an affine image of the local east-north plane, so the
   polygon this returns is the same set as clipping in metres and projecting back. It is
   NOT correct over continental spans, and nothing here spans one. */
function clipConvex(subject, clip) {
  const inside = (p, a, b) =>
    (b[1] - a[1]) * (p[0] - a[0]) - (b[0] - a[0]) * (p[1] - a[1]) >= 0;
  const cut = (p, q, a, b) => {
    const r = [q[0] - p[0], q[1] - p[1]], e = [b[0] - a[0], b[1] - a[1]];
    const den = e[1] * r[0] - e[0] * r[1];
    if (Math.abs(den) < 1e-15) return q;
    const t = (e[1] * (a[0] - p[0]) - e[0] * (a[1] - p[1])) / den;
    return [p[0] + t * r[0], p[1] + t * r[1]];
  };
  /* The clip polygon must be wound counter-clockwise for `inside` to mean inside.
     Shoelace tells us which way it is; reversing a mis-wound polygon is cheaper than
     demanding every caller know the convention. */
  let area = 0;
  for (let i = 0; i < clip.length; i++) {
    const a = clip[i], b = clip[(i + 1) % clip.length];
    area += a[1] * b[0] - b[1] * a[0];
  }
  const cw = area < 0 ? clip.slice().reverse() : clip;

  let out = subject.slice();
  for (let i = 0; i < cw.length && out.length; i++) {
    const a = cw[i], b = cw[(i + 1) % cw.length];
    const input = out; out = [];
    for (let j = 0; j < input.length; j++) {
      const cur = input[j], prev = input[(j + input.length - 1) % input.length];
      const cin = inside(cur, a, b), pin = inside(prev, a, b);
      if (cin) { if (!pin) out.push(cut(prev, cur, a, b)); out.push(cur); }
      else if (pin) out.push(cut(prev, cur, a, b));
    }
  }
  return out;
}

/* An error ellipse as a polygon, so it draws through the same L.polygon call as
   everything else and works on both map backends. `orient` is the bearing TRUE of the
   MAJOR axis, which is how triangulate.py reports it. */
function ellipsePolygon(lat, lon, majorM, minorM, orientDeg, n) {
  const N = n || 40, th = rad(orientDeg || 0), out = [];
  for (let k = 0; k < N; k++) {
    const t = 2 * Math.PI * k / N;
    const nm = majorM * Math.cos(t) * Math.cos(th) - minorM * Math.sin(t) * Math.sin(th);
    const em = majorM * Math.cos(t) * Math.sin(th) + minorM * Math.sin(t) * Math.cos(th);
    const r = Math.hypot(nm, em);
    out.push(destination(lat, lon, deg(Math.atan2(em, nm)), r));
  }
  return out;
}

const SENSOR_COLOURS = ["#7fd4ff", "#ff9ed8", "#a8ff9e"];

function renderMap() {
  if (!MAP || !LAYER || !S.stage) return;''', "helpers")

# ------------------------------------------------- 2. the FOV block, per sensor
rep('''  if (pose.lat_deg !== undefined) {
    const half = pose.fov_half_angle_deg ?? (pose.hfov_deg || 60) / 2;
    const rng = pose.max_range_m || 8000, b = pose.boresight_deg_true || 0;
    const arc = [[pose.lat_deg, pose.lon_deg]];
    for (let a = -half; a <= half; a += 2) arc.push(destination(pose.lat_deg, pose.lon_deg, b + a, rng));
    arc.push([pose.lat_deg, pose.lon_deg]);
    L.polygon(arc, { color: "#3d5468", weight: 2, fillColor: "#0e1a24", fillOpacity: .45, interactive: false })
      .addTo(LAYER).bindTooltip(`sensor field of view · ${fmt(rng / 1000, 1)} km max range`);
    L.circleMarker([pose.lat_deg, pose.lon_deg], { radius: 8, color: "#7fd4ff", weight: 3, fillOpacity: 1 })
      .addTo(LAYER).bindTooltip("camera · " + (pose.pose_ref || "pose"));
    pts.push([pose.lat_deg, pose.lon_deg]);
  }''',
'''  /* EVERY sensor the station knows about, not just the first. `sensors` is published
     by server.py's pose registry, primary first; the single-pose fallback keeps this
     working against a server that predates lane G. */
  const sensors = (S.stage.sensors && S.stage.sensors.length)
    ? S.stage.sensors : (pose.lat_deg !== undefined ? [pose] : []);
  const wedges = [];
  sensors.forEach((sp, i) => {
    if (sp.lat_deg === undefined) return;
    const col = SENSOR_COLOURS[i % SENSOR_COLOURS.length];
    const poly = fovPolygon(sp, 2);
    wedges.push(poly);
    L.polygon(poly, { color: i ? col : "#3d5468", weight: 2, fillColor: "#0e1a24",
                      fillOpacity: i ? .28 : .45, dashArray: i ? "7 6" : null,
                      interactive: false })
      .addTo(LAYER)
      .bindTooltip(`${sp.role === "secondary" ? "camera B" : "camera A"} field of view · `
        + `${fmt((sp.max_range_m || 8000) / 1000, 1)} km · bore ${fmt(sp.boresight_deg_true, 1)}°T`);
    L.circleMarker([sp.lat_deg, sp.lon_deg],
      { radius: 8, color: col, weight: 3, fillColor: "#0b0e13", fillOpacity: 1 })
      .addTo(LAYER)
      .bindTooltip((sp.role === "secondary" ? "camera B · " : "camera A · ") + (sp.pose_ref || "pose"));
    pts.push([sp.lat_deg, sp.lon_deg]);
  });

  /* THE OVERLAP IS THE POINT OF THE SECOND CAMERA, SO IT IS DRAWN.
     Inside it a hull can be cross-fixed: two bearings CONSTRUCT a position and the
     long monocular sliver collapses to a small ellipse. Outside it, camera B adds
     nothing and the operator should be able to see that at a glance rather than
     wonder why one contact has a tight fix and its neighbour does not. */
  if (wedges.length >= 2) {
    const ov = clipConvex(wedges[0], wedges[1]);
    if (ov.length >= 3) {
      L.polygon(ov, { color: "#5ad1c8", weight: 2, dashArray: "4 5",
                      fillColor: "#123c40", fillOpacity: .55, interactive: false })
        .addTo(LAYER)
        .bindTooltip("OVERLAP — both cameras see this water. A hull here can be "
          + "cross-fixed: two bearings construct a position instead of estimating one.");
    }
    /* The baseline. Its LENGTH and its angle to a target are what set the crossing
       angle, and the crossing angle is what decides whether a fix is a circle or a
       sliver. Drawn because an operator asking "why is that ellipse so long" is asking
       a question about this line. */
    L.polyline([[sensors[0].lat_deg, sensors[0].lon_deg],
                [sensors[1].lat_deg, sensors[1].lon_deg]],
      { color: "#5ad1c8", weight: 1, dashArray: "2 7", opacity: .8, interactive: false })
      .addTo(LAYER).bindTooltip("baseline between the two cameras");
  }''', "per-sensor wedges + overlap + baseline")

# ------------------------------------------------------- 3. the fix ellipses
rep('''  if (!fitted && pts.length > 1) { MAP.fitBounds(L.latLngBounds(pts).pad(0.12)); fitted = true; }
}''',
'''  /* ---- CROSS-CAMERA FIXES -------------------------------------------------
     Drawn LAST so they sit on top of the monocular quads they replace, and drawn as a
     1-sigma ellipse rather than a dot for the same reason the quad is not a dot: it is
     the honest shape of the uncertainty. Seeing the small ellipse inside the long quad
     is the entire argument for the second camera, made without a word of explanation.

     A fix is attached to a CONTACT id, not to a record id: the pipeline reasons about
     camera A's contacts, and server.py keys the lookup by BOTH cameras' ids so the
     same fix is found from either side. */
  const fixes = S.stage.fixes || [];
  for (const f of fixes) {
    if (!f.usable_for_position || f.lat_deg == null) continue;
    const selected = [...S.records.values()].some(
      r => r.record_id === S.selected && r.eo_contact
           && (f.contact_ids || []).includes(r.eo_contact.contact_id));
    const ell = ellipsePolygon(f.lat_deg, f.lon_deg,
                               f.sigma_major_m || 1, f.sigma_minor_m || 1,
                               f.ellipse_orientation_deg || 0);
    L.polygon(ell, { color: "#5ad1c8", weight: selected ? 4 : 3,
                     fillColor: "#5ad1c8", fillOpacity: selected ? .35 : .18 })
      .addTo(LAYER)
      .bindTooltip(`CROSS-FIX ${(f.contact_ids || []).join(" + ")} · `
        + `${f.station_count} cameras · crossing ${fmt(f.crossing_angle_deg, 0)}° · `
        + `1σ ${fmt(f.sigma_major_m, 0)} × ${fmt(f.sigma_minor_m, 0)} m`);
    L.circleMarker([f.lat_deg, f.lon_deg],
      { radius: 4, color: "#5ad1c8", weight: 2, fillColor: "#5ad1c8", fillOpacity: 1 })
      .addTo(LAYER);
    /* The two bearing rays that made it. Without them the ellipse is an assertion;
       with them it is visibly the intersection of two measurements. */
    sensors.forEach(sp => {
      if (sp.lat_deg === undefined) return;
      L.polyline([[sp.lat_deg, sp.lon_deg], [f.lat_deg, f.lon_deg]],
        { color: "#5ad1c8", weight: 1, opacity: .45, dashArray: "3 6",
          interactive: false }).addTo(LAYER);
    });
    pts.push([f.lat_deg, f.lon_deg]);
  }

  if (!fitted && pts.length > 1) { MAP.fitBounds(L.latLngBounds(pts).pad(0.12)); fitted = true; }
}''', "fix ellipses")

# ------------------------------------------------------------------ 4. legend
rep('''        <div class="lg-h">verdict</div>''',
'''        <div class="lg-h">two cameras</div>
        <span><i class="g-line" style="border-color:#ff9ed8;border-style:dashed"></i>camera B field of view</span>
        <span><i class="g-quad" style="border-color:#5ad1c8;background:#123c40"></i>overlap &mdash; both cameras see it</span>
        <span><i class="g-quad" style="border-color:#5ad1c8;background:rgba(90,209,200,.25)"></i>cross-fix 1&sigma; ellipse</span>
        <div class="lg-h">verdict</div>''', "legend")

p.write_text(s, encoding="utf-8")
print("map patched")
