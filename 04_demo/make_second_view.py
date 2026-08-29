#!/usr/bin/env python3
"""
04_demo/make_second_view.py — THE SAME WATER, FROM A SECOND CAMERA.

    python3 04_demo/make_second_view.py                      # sensible defaults
    python3 04_demo/make_second_view.py --crossing-deg 55     # pick the geometry

Writes a complete second scene directory (default `04_demo/out/scene01b/`) holding a
second camera's view of the SAME vessels the first camera is watching: its own pose, its
own EoContacts, its own rendered video and its own pose sidecar. Point a second
`pi_sensor.py` node at it and the shore station has two bearings on every hull in the
overlap.

=================================================================================
WHY A SECOND CAMERA, IN ONE PARAGRAPH
=================================================================================
A single camera measures ANGLE well and RANGE badly — lane C measured 218 m cross-range
against 1250 m along-range at 5 km. That is why the console draws an uncertainty QUAD
and not a dot, and it is why an along-line-of-sight position spoof is the hard case: the
deception hides in the long axis, exactly where the sensor is blind. Two cameras on
different bearings do not ESTIMATE range, they CONSTRUCT it — two rays cross — and the
fix is bounded by cross-range error in both directions. MEASURED in
99_scratch/check_triangulate.py: the 1250 x 218 m sliver becomes a 218 x 218 m circle at
a 90-degree crossing, a 5.7x improvement on the long axis.

=================================================================================
NOTHING HERE IS INVENTED, AND THAT IS THE POINT
=================================================================================
The vessels are the SAME AisTracks the first scene used — read from its
`ais_tracks.jsonl`, not regenerated — so the two cameras are genuinely looking at one
set of hulls. Every bearing and range is `association.predict_measurement()` from the
new pose to those same lat/lons: the identical function the pipeline uses to test a
claim against an observation, so the second view cannot be geometrically inconsistent
with the first by construction.

What IS synthetic: the imagery, and the measurement noise. Both are declared. The output
carries `"synthetic": true` and every contact's `frame_ref` is stamped `synthetic://`,
which both consoles already recognise as NOT CAMERA-OBSERVED.

=================================================================================
WHERE THE SECOND CAMERA GOES, AND WHY IT IS COMPUTED RATHER THAN TYPED
=================================================================================
Crossing angle is the whole value of the second camera, and it is a property of WHERE
the cameras are, not of how good they are. Put camera B beside camera A and you have
spent a mast to learn nothing: the bearings are near-parallel and
`triangulate.MIN_CROSSING_ANGLE_DEG` will refuse the fix.

So B is placed by solving for the geometry rather than by guessing coordinates: take the
centroid of the visible traffic, and put B on an arc around that centroid at
`--crossing-deg` from A, at the same standoff. The two bearing rays then cross at
approximately that angle over the middle of the scene. B is aimed at the same centroid,
so its boresight is derived too. Type nothing, verify everything: the script prints the
crossing angle it actually achieved for a sample of vessels.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "03_src"))
sys.path.insert(0, str(ROOT / "04_demo"))

from contracts import AisTrack                                   # noqa: E402
from association import predict_measurement, is_in_field_of_view  # noqa: E402
from geometry import project_to_position                          # noqa: E402
import make_synthetic_eo as mse                                   # noqa: E402
import make_scene_video as msv                                    # noqa: E402


def load_tracks(scene: Path) -> list[AisTrack]:
    """The FIRST scene's tracks, unchanged. Not a fresh draw — the whole point is that
    both cameras are looking at one set of vessels."""
    return [AisTrack.model_validate_json(l)
            for l in (scene / "ais_tracks.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]


def place_second_camera(pose_a: dict, tracks: list[AisTrack], *,
                        crossing_deg: float) -> tuple[float, float, float, float]:
    """
    Solve for B's position and boresight. Returns (lat, lon, boresight, standoff_m).

    The centroid of the traffic is the point the two cameras should cross OVER, so it
    is the pivot. A sits at some bearing and range from it; B is placed at the same
    range, rotated by the requested crossing angle about that pivot. The angle between
    the two lines of sight AT the pivot is then the crossing angle, by construction.

    Rotating rather than translating matters: translating B along a baseline changes
    the crossing angle with range, so the geometry would be good for near vessels and
    useless for far ones. Rotating about the traffic keeps it roughly constant across
    the scene, which is what an operator siting two masts would actually try to do.
    """
    clat = float(np.mean([t.claimed_lat_deg for t in tracks]))
    clon = float(np.mean([t.claimed_lon_deg for t in tracks]))
    # A, as seen from the pivot.
    brg_pivot_to_a, standoff = predict_measurement(
        camera_lat_deg=clat, camera_lon_deg=clon,
        target_lat_deg=np.array([pose_a["lat_deg"]]),
        target_lon_deg=np.array([pose_a["lon_deg"]]))
    brg_pivot_to_a = float(brg_pivot_to_a[0]); standoff = float(standoff[0])
    brg_pivot_to_b = (brg_pivot_to_a + crossing_deg) % 360.0
    lat_b, lon_b = project_to_position(clat, clon, brg_pivot_to_b, standoff)
    # Aim B back at the pivot.
    bore_b, _ = predict_measurement(
        camera_lat_deg=lat_b, camera_lon_deg=lon_b,
        target_lat_deg=np.array([clat]), target_lon_deg=np.array([clon]))
    return lat_b, lon_b, float(bore_b[0]) % 360.0, standoff


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render a SECOND camera's view of the first scene's vessels.")
    ap.add_argument("--scene", type=Path, default=ROOT / "04_demo/out/scene01",
                    help="the FIRST camera's scene directory (read only)")
    ap.add_argument("--out", type=Path, default=ROOT / "04_demo/out/scene01b",
                    help="where the second view is written")
    ap.add_argument("--crossing-deg", type=float, default=55.0,
                    help="target crossing angle at the traffic centroid. Below "
                         "triangulate.MIN_CROSSING_ANGLE_DEG (15) a fix is refused; "
                         "90 is ideal but often unsiteable on a real coast.")
    ap.add_argument("--node-id", default="sensor-02")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--fps", type=float, default=12.0)
    ap.add_argument("--time-lapse", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=20260831,
                    help="DIFFERENT from the first scene's seed on purpose: two "
                         "cameras with correlated measurement noise would make "
                         "triangulation look better than it is.")
    a = ap.parse_args(argv)

    manifest_a = json.loads((a.scene / "scene_manifest.json").read_text())
    pose_a = manifest_a["pose"]
    tracks = load_tracks(a.scene)
    print(f"read {len(tracks)} tracks from {a.scene}")

    lat_b, lon_b, bore_b, standoff = place_second_camera(
        pose_a, tracks, crossing_deg=a.crossing_deg)
    pose_b = mse.ScenePose(
        pose_ref=f"synthetic-B-{bore_b:.2f}T",
        lat_deg=lat_b, lon_deg=lon_b, boresight_deg_true=bore_b,
        hfov_deg=pose_a["hfov_deg"],
        yaw_uncertainty_deg=pose_a.get("yaw_uncertainty_deg", 2.0),
        max_range_m=pose_a["max_range_m"],
        image_width_px=int(pose_a["image_width_px"]),
        image_height_px=int(pose_a["image_height_px"]),
        height_m=pose_a.get("height_m", 25.0))
    print(f"camera A  {pose_a['lat_deg']:.5f}, {pose_a['lon_deg']:.5f}  "
          f"boresight {pose_a['boresight_deg_true']:.1f}T")
    print(f"camera B  {lat_b:.5f}, {lon_b:.5f}  boresight {bore_b:.1f}T   "
          f"standoff {standoff/1000:.2f} km")

    # ---- project the SAME vessels through B ------------------------------------
    projected = mse.project(tracks, pose_b)
    rng = np.random.default_rng(a.seed)
    noise = mse.NoiseModel()
    scene_time = None
    for key in ("scene_time_utc",):
        stamp = (manifest_a.get("ais") or {}).get(key)
        if stamp:
            from datetime import datetime
            scene_time = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    if scene_time is None:
        scene_time = tracks[0].report_time_utc

    contacts = []
    seen = 0
    for i, t in enumerate(tracks):
        if not projected["visible"][i]:
            continue
        seen += 1
        contacts.append(mse.make_contact(
            track=t, bearing_true=float(projected["bearing"][i]),
            range_m=float(projected["range"][i]), pose=pose_b,
            scene_time=scene_time, rng=rng, noise=noise,
            contact_id=f"{a.node_id}-{seen:03d}", track_length_frames=8))
    print(f"{seen} of {len(tracks)} vessels are inside camera B's field of view")
    if not contacts:
        print("NO VESSEL IS VISIBLE FROM B. Try a smaller --crossing-deg: a large "
              "rotation can swing the traffic out of the field of view.", file=sys.stderr)
        return 2

    # ---- how good is the geometry, MEASURED not promised ------------------------
    print("\ncrossing angle achieved, per vessel (A's bearing vs B's bearing):")
    ba, _ = predict_measurement(
        camera_lat_deg=pose_a["lat_deg"], camera_lon_deg=pose_a["lon_deg"],
        target_lat_deg=np.array([t.claimed_lat_deg for t in tracks]),
        target_lon_deg=np.array([t.claimed_lon_deg for t in tracks]))
    xs = []
    for i, t in enumerate(tracks):
        if not projected["visible"][i]:
            continue
        d = abs((float(ba[i]) - float(projected["bearing"][i])) % 180.0)
        xs.append(min(d, 180.0 - d))
    import triangulate as tri
    print(f"  min {min(xs):.1f}deg   median {float(np.median(xs)):.1f}deg   max {max(xs):.1f}deg")
    usable = sum(1 for x in xs if x >= tri.MIN_CROSSING_ANGLE_DEG)
    print(f"  {usable} of {len(xs)} exceed MIN_CROSSING_ANGLE_DEG "
          f"({tri.MIN_CROSSING_ANGLE_DEG:g}deg) and will produce a fix")
    print(f"  amplification at the median: "
          f"{tri._amplification(float(np.median(xs))):.2f}x over ideal 90deg geometry")

    # ---- write the scene, in the shape make_scene_video already reads ------------
    a.out.mkdir(parents=True, exist_ok=True)
    manifest_b = {
        "harness_version": "make_second_view/0.1.0",
        "derived_from": str(a.scene),
        "synthetic": True,
        "seed": a.seed,
        "ais": manifest_a.get("ais", {}),
        "pose": pose_b.to_dict(),
        "counts": {"ais_tracks": len(tracks), "eo_contacts": len(contacts)},
        "caveats": [
            "SECOND CAMERA, SYNTHETIC IMAGERY. The vessels are the first scene's real "
            "AIS tracks projected through a second declared pose; no camera observed "
            "this view. It exists to demonstrate two-bearing triangulation.",
            f"Camera B is placed {a.crossing_deg:.0f} degrees around the traffic "
            f"centroid from camera A, at the same standoff, and aimed back at it.",
        ],
    }
    (a.out / "scene_manifest.json").write_text(json.dumps(manifest_b, indent=2) + "\n")
    with (a.out / "ais_tracks.jsonl").open("w", encoding="utf-8") as fh:
        for t in tracks:
            fh.write(t.model_dump_json() + "\n")
    with (a.out / "eo_contacts.jsonl").open("w", encoding="utf-8") as fh:
        for c in contacts:
            fh.write(c.model_dump_json() + "\n")
    print(f"\nwrote {a.out}/scene_manifest.json, ais_tracks.jsonl, eo_contacts.jsonl")

    # ---- render B's imagery, using the SAME renderer as camera A -----------------
    cdicts = [json.loads(c.model_dump_json()) for c in contacts]
    out_mp4 = a.out / "scene_detector.mp4"
    msv.render(manifest_b, cdicts, out_mp4, seconds=a.seconds, fps=a.fps,
               time_lapse=a.time_lapse, mode="detector")

    print(f"\nrun the SECOND node against the running shore station:\n"
          f"  python3 04_demo/pi_sensor.py --source {out_mp4} --loop \\\n"
          f"      --pose {a.out}/camera_pose_SCENE.json --scale 1 \\\n"
          f"      --node-id {a.node_id} --min-area 30 \\\n"
          f"      --post http://127.0.0.1:8000/api/contacts")
    print("\nNOTE: do NOT give the second node --publish-frames. Only one node should "
          "publish imagery, or the video pane alternates between two viewpoints and "
          "the boxes belong to whichever arrived last.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
