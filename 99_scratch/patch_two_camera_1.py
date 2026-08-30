#!/usr/bin/env python3
"""
99_scratch/patch_two_camera_1.py — LANE G, WIRED IN.

triangulate.py and multiview.py both existed and were verified in 99_scratch, and
NOTHING IMPORTED EITHER OF THEM. The maths was proven and the console had never seen it.
This patch is the wiring: a pose registry so the server knows where each camera stands, a
cross-fix pass after every recompute, and both published on /api/state.

WHAT IS DELIBERATELY NOT DONE HERE
Camera B's contacts are NOT fed to association.py. Two cameras' contacts through the
pipeline would produce two associations per hull, two verdicts and two rows in the
priority queue for one vessel — an operator asked to spend one scarce asset twice on one
target. Camera B contributes a BETTER POSITION for a hull the pipeline already reasons
about. The verdict, the confidence and the ranking are untouched.
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


patch("03_src/server.py", [

# ---- import ------------------------------------------------------------------
("""import evidence as evidence_mod            # noqa: E402
import run_pipeline                        # noqa: E402""",
 """import evidence as evidence_mod            # noqa: E402
import multiview as multiview_mod           # noqa: E402  (lane G)
import run_pipeline                        # noqa: E402"""),

# ---- backend signature -------------------------------------------------------
("""        scene_dir: Path,
        *,
        audit_path: Path,
        node_stale_after_s: float = NODE_STALE_AFTER_S,
        allow_real_identities: bool = False,
        initial_source: str = DEFAULT_SOURCE,
    ) -> None:""",
 """        scene_dir: Path,
        *,
        audit_path: Path,
        node_stale_after_s: float = NODE_STALE_AFTER_S,
        allow_real_identities: bool = False,
        initial_source: str = DEFAULT_SOURCE,
        sensor_scenes: "Sequence[Path]" = (),
    ) -> None:"""),

# ---- the pose registry -------------------------------------------------------
("""        self._demo = _DemoState()
        self._demo.scene = run_pipeline.load_scene(scene_dir)
        self.scene_dir = scene_dir""",
 '''        self._demo = _DemoState()
        self._demo.scene = run_pipeline.load_scene(scene_dir)
        self.scene_dir = scene_dir

        # ================= THE POSE REGISTRY — LANE G ==============================
        #
        # WHY A REGISTRY AND NOT A FIELD ON THE CONTACT. An EoContact names its camera
        # with `camera_pose_ref`, a STRING. It does not carry the camera's position, and
        # it must not: a contact that carried its own station's coordinates would let
        # one record be edited and not another, and a fix computed from two disagreeing
        # versions of where the same camera stood would be confidently wrong with
        # nothing to catch it. One registry, one answer for where each sensor is.
        #
        # A SECOND CAMERA IS A SECOND SCENE DIRECTORY, not a second pipeline.
        # make_second_view.py writes camera B's pose, its own EoContacts and its own
        # video into 04_demo/out/scene01b. Registering the pose is what lets a bearing
        # from that camera be resolved to a ray; loading its contacts is what gives the
        # RECORDED path something to cross-fix against. On the LIVE path a second node
        # posts its own contacts and the registry is all that is needed.
        primary_pose = ((self._demo.scene or {}).get("manifest") or {}).get("pose") or {}
        self._primary_pose_ref: str | None = primary_pose.get("pose_ref")
        self._poses: dict[str, dict[str, Any]] = {}
        self._sensor_poses: list[dict[str, Any]] = []
        self._extra_contacts: list[EoContact] = []
        if self._primary_pose_ref:
            self._poses[self._primary_pose_ref] = primary_pose
            self._sensor_poses.append({**primary_pose, "role": "primary"})

        for extra in sensor_scenes:
            try:
                sc = run_pipeline.load_scene(Path(extra))
            except Exception as exc:                       # noqa: BLE001
                # LOUD, AND NOT FATAL. A missing second camera must not stop the shore
                # station: the single-camera picture is the guaranteed one and losing
                # the upgrade layer is not losing the demo. Printed rather than
                # swallowed so "the overlap pane is empty" has a cause on the terminal.
                print(f"[server] sensor scene {extra} not loaded: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            pose = (sc.get("manifest") or {}).get("pose") or {}
            ref = pose.get("pose_ref")
            if not ref or ref in self._poses:
                print(f"[server] sensor scene {extra} has no distinct pose_ref "
                      f"({ref!r}) — skipped", file=sys.stderr)
                continue
            self._poses[ref] = pose
            self._sensor_poses.append({**pose, "role": "secondary"})
            self._extra_contacts.extend(sc.get("contacts", []))
            print(f"[server] sensor registered: {ref} at "
                  f"{pose.get('lat_deg'):.5f},{pose.get('lon_deg'):.5f} "
                  f"bore {pose.get('boresight_deg_true'):.1f}T "
                  f"({len(sc.get('contacts', []))} recorded contacts)")

        # Computed at the end of every recompute; empty until then and empty whenever
        # only one camera has contacts, which is the ordinary state outside the overlap.
        self._fixes: list[dict[str, Any]] = []
        self._fix_by_contact: dict[str, dict[str, Any]] = {}'''),
])
print("PART 1 done — registry in place.")
