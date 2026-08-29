# FILE OWNERSHIP — EDTH Hamburg / Topic 02

Six lanes: **A / B / C / D / E / F**, plus **ARCH** (Amol). *(Supersedes the earlier
EO/AIS/FUSION/DECISION/PITCH draft — those lane names are dead.)* **E and F were added
2026-08-29** and are carved OUT of lane D's original `04_demo/` blanket: D kept the
pitch and the demo script, E took the operator console, F took the sensor node. If the
team is small, one person holds D+E — but the files still have one owner each.

One owner per file. Concurrent edits to one file are the top cause of a hackathon
repo losing working code. To change a file you do not own, append to
`99_scratch/requests.md` — never edit it yourself, never "just quickly fix" it.

---

## 1. The four lanes

| Lane | Name | The one question it answers | On the hook for |
|---|---|---|---|
| **A** | AIS | *What is being claimed on the wire, in what window, under what licence?* | Criterion 1 — the CLAIMED half |
| **B** | EO + Geometry | *What does the camera see, on what bearing, at roughly what range?* | Criterion 1 — the OBSERVED half |
| **C** | Fusion + Verdict | *Does the claim match the observation, and if not, which way does it fail?* | **Criterion 3 (the discriminator)** + the verdict |
| **D** | Evidence + Demo | *Which contact gets the boat, how sure are we, and can a judge follow it in four minutes?* | **Criterion 2 (highest weight)** + criterion 4 |
| **E** | Console | *What does the watch officer see, and does it survive a refresh?* | Criteria 2 + 4, **on screen** |
| **F** | Edge node | *What does the sensor at the water's edge actually observe, with no access to AIS?* | Criterion 1 — the OBSERVED half, in hardware |
| **ARCH** | Amol | *Do the modules still fit together?* | The seams, and the rules |

**D carries two criteria, including the one CLAUDE.md says most teams fail.** That is
deliberate — whoever defends *"send the boat here, because X, confidence Y"* on stage
should own the code that computes it. **If the team reaches 4 or 5 people, the second
body goes to D, not to A or B.** If the team is only 3, ARCH takes `04_demo/` and
`05_pitch/` so D's engineer never leaves `prioritizer.py`.

---

## 2. Assignment — every path in the repo

### Lane A — AIS

| Path | Note |
|---|---|
| `03_src/ais_ingest.py` | The module |
| `02_data/subset_ais.py` | Written and run; A maintains it |
| `02_data/AIS_CSV_SCHEMA.md` | |
| `02_data/DATASETS.md` | Licence citations — A is accountable for them |
| `02_data/slices/*.csv` | `fehmarn_belt` is the T1 demo area |
| `02_data/raw/` | 6.4 GB. A is the only lane that touches it. Never committed |
| `99_scratch/handoff_A.md` | |

### Lane B — EO + Geometry

| Path | Note |
|---|---|
| `03_src/eo_detector.py` | |
| `03_src/geometry.py` | Bearing, rough range, cable proximity, **the shared signed-shortest-arc helper** |
| `yolov8n.pt` | Gitignored. AirDrop to teammates |
| `04_demo/frames/`, recorded clips | B produces the media; D owns the demo script |
| `99_scratch/handoff_B.md` | |

### Lane C — Fusion + Verdict

| Path | Note |
|---|---|
| `03_src/association.py` | |
| `03_src/consistency.py` | **Criterion 3 lives here** |
| `03_src/verdict.py` | Deterministic verdict + calibrated confidence. **Never LLM-written** |
| `01_research/ais_spoofing_methods.md` | C owns it because C implements the taxonomy in it |
| `99_scratch/handoff_C.md` | |

### Lane D — Evidence + Demo

| Path | Note |
|---|---|
| `03_src/prioritizer.py` | **Criterion 2.** Highest-weight file in the repo |
| `03_src/evidence.py` | |
| `03_src/report.py` | The LLM writes the RATIONALE here, never the verdict |
| `04_demo/` | Demo script, recording, rehearsed fallback — **except `app.py` and `web/` (lane E) and `pi_sensor.py` and `camera_pose_*.json` (lane F)** |
| `05_pitch/` | Slides, one-pager, TRL declaration |
| `99_scratch/handoff_D.md` | |

### Lane E — Console

The operator screen and the stream that feeds it. **Criteria 2 and 4 are judged on
what is on this screen**, not on what `prioritizer.py` returns in a REPL — a ranking
nobody can see is not a prioritisation step.

| Path | Note |
|---|---|
| `04_demo/app.py` | The shore station: serves the page, pushes `StreamEvent`, answers with `ConsoleState` on connect. **Stdlib only** — a demo that needs a `pip install` on venue wifi is a demo that does not start |
| `04_demo/web/` | `index.html` and anything beside it. **No CDN**, hand-drawn SVG plan view |
| `99_scratch/handoff_E.md` | |

Three rules that are E's alone to keep:

1. **Never merge a claim and an observation into one screen object.** `ContactUpdateEvent`
   carries `ais_track` and `eo_contact` as separate optional fields. A merged "vessel"
   is the easiest thing in the world to write for a UI and it deletes criterion 3 from
   the product without raising anything.
2. **The verdict and the confidence are rendered, never computed here.** E displays
   `Verdict.label`, `Verdict.confidence` and `defer_to_human`. A threshold in the
   JavaScript is a second verdict engine that nobody versioned.
3. **`ConsoleState.last_seq` is the refresh contract.** Apply the snapshot, drop every
   buffered event with `seq <= last_seq`, resume at `last_seq + 1`. Skip it and the
   console double-applies or silently misses events, which reads as a UI bug and costs
   an hour.

### Lane F — Edge node

The camera at the water's edge. **This process has no AIS connection and nowhere to put
an MMSI** — that is the demonstration, not an implementation detail. When a judge asks
how you know the detector is not peeking at the claims, you point at the node.

| Path | Note |
|---|---|
| `04_demo/pi_sensor.py` | The node. Classical CV, `observed_class` stays `None`, POSTs `EoContact`-shaped JSON |
| `04_demo/camera_pose_*.json` | Measured pose for F's own rig. **The pose CONVENTION is lane B's** (`geometry.py`); F fills in measured values, it does not redefine the format |
| `99_scratch/handoff_F.md` | |

**F does not own `03_src/eo_detector.py`.** That is lane B's, and F consumes the same
`EoContact` contract rather than forking a second detector — two detectors emitting the
same type but disagreeing about what a contact is would be undiscoverable until the
verdicts disagreed.

Two rules that are F's alone to keep:

1. **`measured_fps` is measured or it is `None`.** A nominal 30 next to a node
   delivering 4 is how a demo gets questioned on stage with no answer.
2. **`contracts.py` must stay importable on the node.** F is the reason ARCH will
   refuse a FastAPI import in `contracts.py`: one transport dependency there and the
   sensor stops booting with an `ImportError` pointing at a file nobody suspects,
   while the shore station keeps working.

### ARCH — Amol only

| Path | Why ARCH |
|---|---|
| `03_src/contracts.py` | **A type change here breaks four modules at once** |
| `03_src/LIBRARIES.md` | Library register, env, secrets, import convention |
| `00_brief/CONTRACTS.md` | The prose companion to contracts.py |
| `tests/` (`conftest.py`, `fixtures.py`, `test_contracts.py`) | A fixture is a contract expressed as data |
| `03_src/run_demo.py` *(to be created)* | The only file that imports all four lanes |
| `CLAUDE.md`, `FILE_OWNERSHIP.md`, `STATUS.md`, `.gitignore` | |
| `06_team/`, `99_scratch/verify_env.sh` | |
| `99_scratch/verify_env_output.txt` | Measured evidence. **Immutable** — re-run, never edit |

### Shared, read-only

`00_brief/challenge_statement.md`, `00_brief/judging_criteria.md`,
`00_brief/constraints.md` — read them, never edit them. ARCH-owned if they must change.

### Shared, append-only (everyone writes, nobody rewrites)

`99_scratch/requests.md` — append at the bottom; only the **owning** lane sets `STATUS:`.
`STATUS.md` — one line per session at the bottom; never delete a line, correct with a new one.

---

## 3. Dependency order

```
ARCH: contracts.py + LIBRARIES.md + tests/fixtures.py   <-- DONE. Gate is open.
        |
        +--> A (AIS)      START NOW. Data on disk, dialect settled.
        +--> B (EO)       START NOW. Weights present, 45 FPS measured.
        +--> C (Fusion)   START NOW on fixtures. Real data later.
        +--> D (Decision) START NOW on fixtures. Never blocked.
        +--> E (Console)  START NOW. ConsoleState + StreamEvent are in contracts.py;
        |                 render fixtures first, connect the live stream after.
        +--> F (Edge)     START NOW. Emits EoContact only — needs nothing from A, C or D.
```

**E and F are not blocked on each other either.** F emits `EoContact`; E renders
`ConsoleState`. Each can be built against `tests/fixtures.py` and meet at `app.py`.

**No lane is blocked.** C and D develop against `tests/fixtures.py`, which emits
contract-shaped synthetic tracks and contacts. **If lane C is ever idle waiting for
data, that is an ARCH bug in the fixtures, not an A or B bug.**

Convergence order once fixtures are replaced: A + B → C → D. The first integration is
A's golden window meeting B's frame detections in `association.py`. **Schedule that
meeting explicitly. Do not let it happen by accident at 04:00.**

### First deliverable per lane — the smallest thing that unblocks someone else

| Lane | First deliverable | Unblocks |
|---|---|---|
| **A** | One **golden window**: ~10 min of `fehmarn_belt`, ≤10 MB, parsed with an explicit `dd/mm/yyyy` format, emitted as `list[AisTrack]` | C gets real claims; D gets real (anonymised) identities |
| **B** | One frame → `list[EoContact]`, with the camera pose recorded beside it | C can associate against something real; D has an image |
| **C** | `associate()` over fixtures returning all three outcomes — MATCH / unmatched observation (dark) / unmatched claim | D can render a card before verdict logic exists |
| **D** | `report.py` rendering **one fixture EvidenceRecord** into the finished card (identity, track, timestamp, rationale, confidence, defer line) | C immediately — it freezes the output shape so C stops guessing |

D's first deliverable runs backwards through the pipeline on purpose. The output
format is the cheapest thing to fix at 20:00 and the most expensive at 04:00.

---

## 4. Cross-lane change requests

1. Append a block to `99_scratch/requests.md`: `### <UTC ts> | <lane> -> <owning
   lane> | <file>` then `WANT: / WHY: / IMPACT: / STATUS: OPEN`. Lane letters are
   `A B C D E F ARCH`.
2. `WHY:` states cause and effect (what breaks without it), never preference.
   `IMPACT:` names every module that changes. Only the **owning** lane edits `STATUS:`.
3. A `contracts.py` request is an **interrupt, not a queue item** — say it out loud to
   ARCH as well as filing it, because four modules stall behind it.

---

## 5. Traps for a newcomer at 03:00

1. **`02_data/slices/` is 427 MB and the Fehmarn slice alone is 248 MB — over
   GitHub's hard 100 MB per-file limit.** `.gitignore` now excludes the slices.
   Commit only A's ≤10 MB golden window. Do not "fix" a rejected push by rewriting
   history.
2. **`02_data/raw/` is 6.4 GB.** Do not open it in an editor. Do not `pd.read_csv` it
   without chunking. Check disk before anyone downloads a second day.
3. **`yolov8n.pt` is gitignored.** A fresh clone has no weights and ultralytics will
   try to fetch them over venue wifi. AirDrop the 6.3 MB file on arrival.
4. **The Featherless key is outside the tree** — `EDTH_FEATHERLESS_KEY` in
   `~/.config/edth-hamburg/edth-hamburg-2026.env`. A 401 from `report.py` almost always
   means you did not `source` it. Never use a bare `FEATHERLESS_API_KEY`.
5. **`.venv/` is not portable.** No `requirements.txt` yet. Amol's Mac is the only
   verified machine — the demo runs there.
6. **`03_src` is not importable as a package** (leading digit). Convention is
   `sys.path` + flat imports; see `LIBRARIES.md`. Do not invent a different one.
7. **Two files leak restricted material.** `EDTH_Hamburg_Topic02_COMPLETE.md`
   and `00_SHARED_macOS_Demo_Architecture.md` reference the mothership/child-drone
   architecture and FARU. Constraint 9 forbids disclosure and hackathons often
   require open-sourcing. **Both are gitignored — keep it that way.** Related and
   still open: constraint 8, the event IP terms, which gate the first commit at all.
8. **The date landmine is real and silent.** Timestamps are `dd/mm/yyyy`; pandas
   defaults month-first and swaps every date with day ≤ 12 *without raising*. Only
   `ais_ingest.py` reads the CSV, and it must pass an explicit format. The header's
   first column is literally `# Timestamp`, with a hash.
9. **Bornholm is a false-positive factory.** Tracks there are 2.5× sparser — the edge
   of Danish basestation coverage. A coverage gap is not a switched-off transponder.
   Demo area is **fehmarn_belt**. The distinction is a criterion 4 slide, not a
   footnote.
10. **Any network result on venue wifi is provisional** — a captive portal already
    produced one false "endpoint is down". Use `curl -sS`, not `-s`.

---

## 6. Assignment — fill in at 19:00

| Lane | Person | Contact | Confirmed |
|---|---|---|---|
| A — AIS | | | ☐ |
| B — EO + Geometry | | | ☐ |
| C — Fusion + Verdict | | | ☐ |
| D — Evidence + Demo *(second body here first)* | | | ☐ |
| E — Console *(D+E on one person if the team is small)* | | | ☐ |
| F — Edge node *(only if the Pi is on site — otherwise fold into B)* | | | ☐ |
| ARCH | Amol Vivek Kulkarni | — | ☑ |

**Rules that survive a 3 a.m. merge:** only write files your lane owns · never pass a
bare dict across a boundary · the deterministic pipeline produces the VERDICT and
CONFIDENCE, the LLM produces the RATIONALE only · every verdict carries a confidence
and a defer-to-human flag · measured numbers only · anonymise every real vessel in
anything a judge or a camera can see · end each session with
`99_scratch/handoff_<lane>.md` and one line in `STATUS.md`.
