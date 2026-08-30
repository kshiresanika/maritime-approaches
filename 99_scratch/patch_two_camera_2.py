#!/usr/bin/env python3
"""99_scratch/patch_two_camera_2.py — LANE G part 2: compute the fixes and publish
them, plus the CLI that declares a second camera."""
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

# ---- compute the fixes at the end of recompute --------------------------------
("""        unresolved = [c for c in source_contacts
                      if c.contact_id not in contacts_in_records]

        async with self._lock:""",
 '''        unresolved = [c for c in source_contacts
                      if c.contact_id not in contacts_in_records]

        # ============ CROSS-CAMERA FIXES — LANE G ==================================
        #
        # Run AFTER the pipeline and OUTSIDE the lock, alongside it rather than inside
        # it: this changes no verdict, no confidence and no rank. It adds a better
        # POSITION for hulls two cameras can both see, and nothing else. If it raises,
        # the single-camera picture is untouched — which is why it is wrapped.
        #
        # THE CONTACT SET IS THE UNION, AND THE DUPLICATE GUARD IS LOAD-BEARING. On the
        # LIVE path a second node's contacts are already in `source_contacts`, because
        # _fuse_live_contacts() merges every live node. On the RECORDED path they are
        # not, and come from the second scene directory. Adding both without the id
        # guard would offer camera B's contacts to the solver twice, and the assignment
        # would happily pair a contact with its own duplicate — a "fix" at zero baseline
        # that the crossing-angle gate would reject, but only by luck.
        fixes: list[dict[str, Any]] = []
        by_contact: dict[str, dict[str, Any]] = {}
        if len(self._poses) >= 2:
            try:
                seen_ids = {c.contact_id for c in source_contacts}
                pool = list(source_contacts) + [
                    c for c in self._extra_contacts if c.contact_id not in seen_ids]
                cfs = await asyncio.to_thread(
                    multiview_mod.cross_fix, pool, self._poses,
                    primary=self._primary_pose_ref)
                fixes = [cf.to_dict() for cf in cfs]
                by_contact = multiview_mod.fixes_by_contact(cfs)
            except Exception as exc:                       # noqa: BLE001
                print(f"[server] cross-fix pass failed: {type(exc).__name__}: {exc}",
                      file=sys.stderr)

        async with self._lock:
            self._fixes = fixes
            self._fix_by_contact = by_contact'''),

# ---- publish on /api/state ----------------------------------------------------
("""            body["source"] = self.source.status()
        body["last_update"] = self._demo.last_update""",
 '''            body["source"] = self.source.status()
            # LANE G. `sensors` is every camera the station knows about, primary first,
            # so the map can draw one field-of-view wedge per sensor and the overlap
            # between them. `fixes` is the cross-camera positions. Both are published
            # even when there is only one sensor and no fix — an absent key and an empty
            # list read identically to a console, and only one of them is a statement.
            body["sensors"] = list(self._sensor_poses)
            body["fixes"] = list(self._fixes)
            body["fix_by_contact"] = dict(self._fix_by_contact)
        body["last_update"] = self._demo.last_update'''),

# ---- create_app parameter ------------------------------------------------------
("""    video_source: "str | int | None" = None,
    frame_stale_after_s: float = 5.0,""",
 """    video_source: "str | int | None" = None,
    frame_stale_after_s: float = 5.0,
    sensor_scenes: "Sequence[Path]" = (),"""),
])

# ---- pass it through to the backend --------------------------------------------
p = ROOT / "03_src/server.py"
s = p.read_text(encoding="utf-8")
i = s.index("    backend = ConsoleBackend(scene_dir, audit_path=audit_path,")
j = s.index(")", s.index("initial_source=", i))
seg = s[i:j]
assert "sensor_scenes" not in seg, "already wired"
s = s[:j] + ",\n                             sensor_scenes=sensor_scenes" + s[j:]
p.write_text(s, encoding="utf-8")
print("  backend receives sensor_scenes")
