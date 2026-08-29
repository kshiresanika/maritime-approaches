# Demo plan — INDOOR, NO WATER

**Hard constraint, confirmed 2026-08-28 by Amol: there is no access to the Elbe, to the
harbour, or to any water. The venue is indoors. There is no line of sight to a vessel.**

This supersedes the old T1/T2 ladder in `STATUS.md`. **T2 (live camera on the Elbe + live
AIS feed) is cancelled — not deferred.** Do not spend an hour on it on Saturday night.

## What this actually costs, and what it does not

Read this before panicking, because the loss is smaller than it feels.

**Lost:** live vessels in frame. Nothing else.

**Not lost:** the AIS half is *recorded* data and was never going to be live anyway — it
is real Danish Maritime Authority traffic from the Fehmarn Belt, 1.2 M rows cut from a
32.6 M-row national day file, and it does
not care where the laptop is. Association, consistency, verdict, prioritization and
evidence are all pure functions of `EoContact` + `AisTrack`. **Not one line of
`03_src/` needs to change.** Only the thing that *produces* `EoContact` changes.

**Gained, unexpectedly:** two blockers die with T2. The live-AIS-feed licence was
unread and was gating T2 — now irrelevant. And a live demo on a moving river is the
single highest-variance thing you could put in front of judges; an indoor demo is
reproducible on demand, which is worth more than authenticity you cannot control.

**The honesty cost is real and must be paid on a slide.** See §5.

---

## The new ladder — build in this order, never out of it

### D0 — Replay harness (GUARANTEED. Build first. Nothing else starts until this runs.)

No camera. No window. Runs on a plane.

Real AIS from the golden window is **forward-projected** through a declared synthetic
camera pose to produce the `EoContact` stream that a camera at that pose *would* have
produced. `association.predict_measurement()` already computes exactly this projection —
the harness inverts the usual direction of use, which is why it is cheap to build.

Then faults are **injected deliberately**, because a demo needs something to find:

| Inject | How | Expected verdict |
|---|---|---|
| Dark vessel | Emit the `EoContact`, drop the matching `AisTrack` | DARK |
| Identity spoof | Keep both, rewrite `claimed_length_m` 30 m -> 180 m | SPOOF, dimension mismatch |
| Class spoof | Rewrite `claimed_ship_type` to "cargo" over a tug silhouette | SPOOF, class mismatch |
| Position spoof | Shift `claimed_lat/lon` 800 m, leave the contact where it was | SPOOF, position mismatch |
| Coverage hole | Drop an `AisTrack` for a vessel **outside** the field of view | **MATCH-not-applicable / defer.** This is the false-positive control and it must NOT fire |

That last row is the one to demo to a judge. Anyone can make an alert fire. Showing the
case where your system **declines to** is criterion 4.

Implemented in **`04_demo/make_synthetic_eo.py`**. Ground truth is known by
construction, so this is also the only way to state a measured false-positive rate on
Sunday morning.

Three properties of that harness worth knowing before you touch it:

1. **Measurement error is mandatory, not optional.** A perfect projection of the claims
   would associate at zero sigma, pass every tolerance, and prove only that `a == a` —
   while hiding real defects in `association.py` until a judge finds them. The noise
   model applies bearing error from the pose, *fractional* range error (25% 1-sigma,
   because monocular range divides by an apparent size a few pixels wide), length error,
   class confusion **restricted to visually similar types** (a VLM confuses tanker with
   cargo, never tanker with fishing boat — random confusion would manufacture easy
   spoofs a real system never sees), random missed detections, and clutter contacts
   belonging to no vessel at all. `--noise none` exists for debugging plumbing and must
   never be used for a demo.
2. **The answer key is a separate file.** No field on `EoContact` or `AisTrack` records
   that a vessel was tampered with. If the key travelled inside the data a downstream
   lane could read it by accident, score 100%, and nobody would find out until the demo.
   Nothing in `03_src` may import `ground_truth.jsonl`.
3. **The spoofer is competent by default.** AIS declares dimensions twice — Length/Width,
   and the reference offsets Size A/B/C/D where A+B == length. A lazy spoofer changes only
   Length and can be caught from the AIS message alone, no camera required. The harness
   updates both by default, so the injected spoof is only catchable by comparing against
   the camera. `--lazy-spoofer` generates the easy one, and `consistency.py` should catch
   *both*.

```bash
python 04_demo/make_synthetic_eo.py \
    --ais 02_data/golden/<window>_anon.csv \
    --out 04_demo/out/scene01 --seed 20260830 --dry-run
```

The harness **refuses to run on non-999 MMSIs** unless explicitly forced — if it stops,
you have pointed it at the real-identity file sitting next to the pseudonymised one.

### D1 — Live camera, tabletop scale range (THE LIVE PROOF. Build second.)

Toy boats, or printed silhouettes cut out and stood on blocks, on a table. The MacBook
camera at one end.

This is not a toy demo — it exercises the **real optics and the real geometry**:

1. Declare a synthetic anchor position (a plausible Fehmarn Belt lat/lon) as the camera's
   location, and a **scale factor** (suggested: 1 cm on the table = 20 cm of sea, i.e. `--scale 20`).
2. Survey the pose the way you would survey a real coastal camera: measure the camera
   height above the table with a ruler, the boresight direction with the phone compass,
   and record it in `camera_pose_TEMPLATE.json`. **Set `yaw_uncertainty_deg` honestly** —
   a phone compass indoors near metal is worth about 10-15 deg, not 2.
3. Place each silhouette, measure its table position, convert through the scale factor to
   a synthetic lat/lon, and write it as an `AisTrack`.
4. Run `eo_detector.py` live on the camera. YOLO produces real detections at real pixel
   columns, `relative_bearing_deg` converts them through the real `atan2` pinhole model,
   and association pairs them against the claims.
5. **Spoof one of them physically**: swap a small silhouette for a large one while the
   claim still says 180 m. The mismatch is generated by the actual camera, not by a script.

Known weakness, state it: YOLO's COCO `boat` class may not fire reliably on a paper
silhouette. **Test this early** — it is the one thing that can kill D1. Mitigations in
order: use actual toy boats; print photographs of vessels rather than outlines; or fall
back to D2.

### D2 — Screen-as-scene (FALLBACK ONLY)

Point the camera at a second monitor playing a harbour clip. YOLO fires reliably on real
vessel pixels, which proves the detector is live. NOTE the 45.0 FPS figure is a SYNTHETIC-FRAME
measurement on the M4 (STATUS 2026-08-27) and says nothing about throughput on real
decoded video, which is UNMEASURED.

**Bearings from this configuration are meaningless** and must not be claimed. Run it with
`uncalibrated_benchmark_pose()`, whose `yaw_uncertainty_deg` is **180 on purpose** — that
makes every bearing explicitly worthless, so no position mismatch can reach any
significance at all. The pipeline will correctly refuse to produce a position spoof
verdict, and that refusal is the honest behaviour. Use D2 to show *detection*, never to
show *fusion*.

---

## The demo script (5 minutes)

1. **The problem.** One patrol boat, 3000 transits. 15 s.
2. **D0 replay, full screen.** Real Baltic traffic. The ranked list appears — *not* a flat
   alert list. Top contact, with its rationale. 90 s.
3. **Open the evidence record for that contact.** Identity, track, timestamp, the specific
   claimed-vs-observed numbers with both field names, the confidence, and the limits
   section. 60 s.
4. **The false-positive control.** Show the coverage-hole vessel that the system declined
   to flag, and say why. 45 s.
5. **Turn to the table.** Live camera, real detection, physically swap the silhouette,
   watch the SPOOF verdict appear from real optics. 60 s.
6. **Limits slide.** 30 s. See §5.

## §5 — The honesty slide. Non-negotiable.

Put this on a slide, in these words or close to them. Judges reward it and the ethical
floor requires it.

> **What is real:** the AIS is real. 1.2 M rows of Danish Maritime Authority traffic
> (cut from a 32.6 M-row national day file) from
> the Fehmarn Belt, 2026-08-25 — a real government dataset with a real licence, carrying
> real vessel behaviour, real coverage holes and real reporting gaps. Every measured
> number in this pitch came out of it.
>
> **What is synthetic:** the camera feed. We have no water access. The electro-optical
> observations are forward-projected from the AIS through a declared camera pose, and the
> anomalies are injected deliberately so the false-positive rate can be measured against
> known ground truth. The live tabletop segment uses the real detector and the real
> geometry at 1:20 scale (`--scale 20`: metres of sea per metre of table).
>
> **What we therefore cannot claim:** a detection rate against real sea clutter, real
> weather, real glare, or real range. Those need a camera pointed at water, and that is
> the first thing we would do with another week.

Do not bury this. Say it out loud in the room. A team that states its limits precisely is
more credible than a team that claims a sea trial nobody can verify.

## Setup checklist (do Saturday morning, not Sunday)

- [x] `make_synthetic_eo.py` written
- [ ] `make_golden_window.py` run — `02_data/golden/` is still EMPTY, D0 cannot run without it
- [ ] D0 running end to end on the Mac
- [ ] Golden window loaded, 139 vessels, 1 h, pseudonymised
- [ ] Five injected faults produce five expected verdicts, plus the control that stays silent
- [ ] Toy boats / printed silhouettes acquired
- [ ] **YOLO tested against the actual silhouettes** — this is the D1 kill switch
- [ ] Table measured, `camera_pose_TABLETOP.json` filled, `yaw_uncertainty_deg` set honestly
- [ ] Second monitor + harbour clip on disk as the D2 fallback
- [ ] Whole demo rehearsed once with the laptop on battery and wifi OFF
