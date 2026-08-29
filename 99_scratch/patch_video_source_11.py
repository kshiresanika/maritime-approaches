#!/usr/bin/env python3
"""
99_scratch/patch_video_source_11.py — PART 8: give the node a pose it can use.

THE BLOCKER, from the first real run:
    [node] hfov_deg is 0 in the pose file.

That refusal is CORRECT and stays. camera_pose_TABLETOP.json and _TEMPLATE.json are
both unfilled by design — every zero in them is a number somebody has to go and
measure, and the node refusing to emit bearings from a zero HFOV is the single most
important guard in the sensor. Weakening it to get a demo running would make every
bearing, every SPOOF confidence and every evidence record quietly meaningless.

But the RENDERED SCENE VIDEO is the one case where nothing needs measuring: it was
drawn from a pose that is written down in scene_manifest.json. The camera that "shot"
it is that pose, exactly, by construction. So the renderer now emits the matching pose
file beside the video, and the node can be pointed at both with nothing assumed.

For real footage from a real lens the answer is unchanged: measure the HFOV.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def patch(rel, pairs):
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    for old, new in pairs:
        n = src.count(old)
        if n != 1:
            raise SystemExit(f"ANCHOR FAILED in {rel}: matched {n}, expected 1\n---\n{old[:240]}\n---")
        src = src.replace(old, new)
    p.write_text(src, encoding="utf-8")
    print(f"  patched {rel} ({len(pairs)} edits)")


patch("04_demo/make_scene_video.py", [

('''    writer.release()
    print(f"wrote {out}  ({n_frames} frames, {n_frames / fps:.1f}s, {W}x{H})")
    print(f"  {len(contacts)} hulls at the projection's own pixel positions")
    print(f"  horizon drawn at y={horizon} — SCENERY, not a measurement")
    print(f"\\nshow it:\\n  python3 03_src/main.py --video {out} --loop")''',

 '''    writer.release()

    # ---- the pose that goes WITH this video -------------------------------------
    # The node refuses to run on a pose whose hfov_deg is 0, and it is right to: every
    # bearing it emits is wrong until that number is measured. There is exactly one
    # case where nothing needs measuring, and this is it — the video was DRAWN from
    # this pose, so the camera that shot it IS this pose, by construction. Written
    # beside the video so the two cannot be separated and mismatched later.
    pose_out = out.parent / "camera_pose_SCENE.json"
    pose_out.write_text(json.dumps({
        "pose_ref": pose.get("pose_ref", "scene"),
        "yaw_deg_true": pose["boresight_deg_true"],
        "hfov_deg": pose["hfov_deg"],
        "yaw_uncertainty_deg": pose.get("yaw_uncertainty_deg", 2.0),
        "lat_deg": pose["lat_deg"],
        "lon_deg": pose["lon_deg"],
        "height_m": pose.get("height_m", 25.0),
        "image_width_px": W,
        "image_height_px": H,
        "hfov_source": "EXACT — this video was rendered from this pose",
        "note": ("Emitted by make_scene_video.py alongside scene.mp4. Every value is "
                 "copied from scene_manifest.json, which is the pose make_synthetic_eo "
                 "projected the AIS truth through. NOTHING HERE IS MEASURED FROM A "
                 "REAL CAMERA, because there is no real camera: the imagery is a "
                 "rendering. Valid for that video and for nothing else. Point the node "
                 "at real footage and you must measure hfov_deg yourself — see "
                 "camera_pose_TEMPLATE.json."),
    }, indent=2) + "\\n", encoding="utf-8")

    print(f"wrote {out}  ({n_frames} frames, {n_frames / fps:.1f}s, {W}x{H})")
    print(f"  {len(contacts)} hulls at the projection's own pixel positions")
    print(f"  horizon drawn at y={horizon} — SCENERY, not a measurement")
    print(f"wrote {pose_out}")
    print(f"  hfov={pose['hfov_deg']:g}  yaw={pose['boresight_deg_true']:.2f}T  "
          f"height={pose.get('height_m', 25.0):g} m — EXACT for this video")
    print(f"\\nshow it:\\n"
          f"  python3 03_src/main.py --video {out} --loop \\\\\\n"
          f"      --pose {pose_out} --scale 1")'''),
])

# --------- the node says out loud where its field of view came from ---------------
patch("04_demo/pi_sensor.py", [
('''    print(f"{SENSOR_VERSION}  pose={pose.get('pose_ref')}  "
          f"hfov={pose['hfov_deg']}  yaw={pose['yaw_deg_true']}  "
          f"sigma={pose.get('yaw_uncertainty_deg')}")''',
 '''    print(f"{SENSOR_VERSION}  pose={pose.get('pose_ref')}  "
          f"hfov={pose['hfov_deg']}  yaw={pose['yaw_deg_true']}  "
          f"sigma={pose.get('yaw_uncertainty_deg')}")
    # WHERE THE FIELD OF VIEW CAME FROM, PRINTED EVERY RUN. hfov_deg feeds every
    # bearing, every bearing sigma and therefore every SPOOF confidence in the case
    # file. A measured 61.4 and a guessed 60 look identical in the JSON and produce
    # verdicts of very different worth, so the pose is asked to declare its provenance
    # and the node repeats the declaration where the operator will see it.
    src_note = str(pose.get("hfov_source") or "NOT STATED")
    if "ASSUM" in src_note.upper() or src_note == "NOT STATED":
        print(f"  !! HFOV PROVENANCE: {src_note}. Bearings from an unmeasured field of "
              f"view are arithmetic on a guess. Usable for a pipeline demo; NOT "
              f"evidence. Measure it before any bearing leaves this machine as a "
              f"claim — camera_pose_TEMPLATE.json says how.")
    else:
        print(f"  hfov provenance: {src_note}")'''),
])
print("PART 8 done.")
