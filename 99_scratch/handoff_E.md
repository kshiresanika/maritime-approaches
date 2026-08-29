# HANDOFF — LANE E (transport + operator console backend)

**Delivered:** `03_src/server.py` (1311 lines), `99_scratch/lane_e_server_check.py`.
**Executed:** the static checks only, in the agent's Linux VM. **10/10 pass.**
**NOT executed:** anything requiring pydantic, fastapi, uvicorn or a browser.

> The agent shell is a Linux VM. The project venv is a macOS venv, so pydantic's
> compiled core will not import there and neither will fastapi. Every claim below
> about *structure* is measured. Every claim about *behaviour* is unmeasured until
> the paste block at the bottom is run on the Mac.

---

## 1. What was built, and what was deliberately not

| Route | Behaviour |
|---|---|
| `GET /` | Serves `--web/index.html`. Defaults to **lane D's existing console**, so this server is a drop-in for `app.py`. 503 naming the path it tried if absent. |
| `GET /api/state` | **Compatibility.** Lane D's payload, produced by lane D's own `State` class *imported, not copied*. This is what makes `app.py` an interchangeable fallback rather than a different demo. |
| `WS /ws` | `ConsoleState` first, then `StreamEvent`s. Gap-free `seq`. |
| `POST /ingest/contacts` | `EoContact` validated on the shore station. |
| `POST /api/contacts` | **Alias.** `pi_sensor.py`'s hardcoded default path — the sensor works unchanged. |
| `GET /stream` | MJPEG relay (`--mjpeg-url`) or local camera (`--camera`). 503 with a reason otherwise. |
| `POST /action` | `OperatorAction` → appended to `04_demo/operator_audit.jsonl`, fsynced, acked. |
| `GET /evidence/{id}` | `evidence.record_to_dict()`, **pseudonymised by default**. |
| `GET /health` | Nodes, source, uptime, event seq, audit line count. |

**Not built, on purpose:** any association, consistency, verdict, prioritisation or
evidence logic. Static check 2 asserts the file does not even *import* those four
modules — it goes through `run_pipeline.run_scene()`, the only tested sequencing of
them in the repo. Check 3 asserts it never compares a `significance`, `assoc_score`,
`delta` or `tolerance` against anything. **The server carries; the pipeline decides.**

---

## 2. Three facts that contradicted the brief, and what was done instead

**(a) `pi_sensor.py` serves no video.** MEASURED by reading it: it is a *push client* —
`urllib` POSTing JSON, no HTTP server, no MJPEG. There is no "Pi MJPEG stream" to
proxy. Rather than invent one, `/stream` relays any configured
`multipart/x-mixed-replace` URL (the wire format of `mjpg-streamer` and `motion`,
either of which runs on a Pi beside `pi_sensor.py` with no new code) or encodes the
local camera with cv2. Configured neither way it returns **503 with a stated reason**,
never a placeholder frame — a still image on a watch screen is indistinguishable from
a live one showing calm water.

**(b) fastapi/uvicorn are not in the venv.** MEASURED: `.venv/lib/python3.12/site-packages`
contains **pydantic and pydantic_core only**. The venv is `--system-site-packages`, so
they may exist in the framework Python; that was not verifiable from the agent shell.
`README.md` records stdlib-zero-network as a *deliberate* choice against FastAPI. That
reasoning still stands and is managed by **separation, not argument**: `04_demo/app.py`
is untouched, stdlib, already run end to end, and serves the same page and the same
`/api/state`. `_require_web_stack()` prints the install command **and** the fallback
command instead of a traceback.

**(c) There is no lane E in `FILE_OWNERSHIP.md`,** and `web/index.html` belongs to
lane D and has no WebSocket client. `server.py` is a new file so no ownership was
breached, but **the `/ws` layer currently has no consumer.** Two requests filed.

---

## 3. The design decisions worth defending on stage

**Broadcast on change, not on a timer — and it is a correctness rule, not a UX one.**
A queue that reshuffles every second looks broken because a watch officer reads motion
as new information. Worse, it *is* broken as evidence: re-emitting an unchanged ranking
makes "the ranking changed" unobservable, and criterion 2's entire claim is that the
order means something. Every emitter is gated on a content fingerprint
(`_queue_key` / `_verdict_key` / `_contact_key` / `_node_key`).

The fingerprints are **field tuples, not `==` on the frozen models** — and that
exclusion *is* the rule. `Verdict.decided_at_utc` changes on every recompute, so
whole-object comparison would emit a `verdict_update` every pass and "broadcast on
change" would be **silently dead while appearing to be implemented**. Floats are
quantised to 3 places before comparison (the console displays 2), because two runs over
identical inputs differ in the 15th decimal and an un-quantised compare reproduces the
every-second reshuffle exactly.

**The watchdog is a timer that is not a broadcast timer.** A node going silent is a
real change, but nothing arrives to announce it — the whole signal is an *absence*, and
an absence can only be noticed by looking. So the 1 s tick drives **detection** and the
fingerprint still gates **emission**: a server sitting next to a dead Pi sends exactly
one event, the flip. This matters because "the queue went quiet" and "the sensor died"
produce an identical empty map, and one means stand down while the other means the tool
is blind.

**`measured_fps` stays `None` unless the node reports it.** The server can measure POST
rate; `pi_sensor.py` batches on `--interval`, so post rate ≠ frame rate. Substituting
one for the other puts a confident number beside a sensor that never produced it — the
exact failure `contracts.SensorNode.measured_fps` is written to prevent.

**Subscribe before snapshot, never the reverse.** An event emitted between a snapshot
and a subscription would be lost *with no gap in the client's seq to reveal it* — the
console would sit on a quietly wrong picture, which is the one failure the seq counter
exists to make impossible. Subscribing first can only cause a duplicate, and a
duplicate is detectable (`seq <= last_seq`) and discardable. **Prefer the recoverable
error.**

**A full subscriber queue closes the connection rather than dropping an event.**
Dropping breaks the gap-free guarantee; the client would see 411 then 413 and correctly
conclude it lost one, with no recovery but a reconnect. So we force the reconnect,
which delivers a fresh consistent `ConsoleState`. A slow client gets a brief blank
instead of a permanently wrong screen.

**`accepted` on the action ack tracks the disk write and nothing else.** `contracts.py`
is explicit it must never read as "the asset was tasked". An operator's decision on a
contact the server no longer holds is *still a decision a human made*: it is written
with `contact_known: false` rather than refused, so the anomaly is visible to a reviewer
instead of being resolved by this file's opinion. Refusing it would delete it from the
trail.

**`/evidence` is pseudonymised behind two independent gates** — the `--allow-real-identities`
server flag AND `?allow_real=1`. A URL is a thing that leaves the machine the moment
anyone opens it on the projector, and this is the route most likely to name a real
vessel. No single mistake exposes one.

---

## 4. The audit trail is a deliverable, not a log

`04_demo/operator_audit.jsonl`. It is the artefact that makes *"decision support,
never automated enforcement"* **checkable rather than asserted**: every line names a
human and a time, and there is no code path in this server that writes a decision of
its own. A judge can open the file and confirm every entry has an `operator_id`.

- **Append-only.** Opened `a`, never rewritten. A `DISMISS` is *recorded, not deleted* —
  "what the operator chose not to chase" is what an after-action review asks for, and it
  is the entry a system designed to flatter itself would drop.
- **Flushed and fsynced before the ack is sent.** Without it the line lives in the page
  cache and a laptop that loses power leaves the operator believing a decision is on the
  record when it is not.
- **The contract object is nested, not flattened.** Server facts (`recorded_at_utc`,
  `server_seq`, `contact_known`) sit outside the `action` key, so a reader can tell what
  came from `contracts.OperatorAction` and what this layer added — the same separation
  `evidence.record_to_dict()` uses for its disclosure block.
- **It survives a restart.** The line counter is initialised from the existing file, not
  reset — a restart mid-demo must not erase what the operator already decided.

---

## 5. A checker defect worth reading, because it generalises

The static checks failed 3/10 on the first run. **All three were defects in the
checker, and two of them are instructive.**

Checks 8 and 10 matched raw source. Check 8 forbids opening the audit file `"w"`; the
docstring *explaining that rule* contains those characters. Check 10 forbids a merged
`"vessel"` payload; the comment *explaining why* contains that word. **A file that
documented a rule was reported as breaking it.** The general form: a substring check
over source cannot tell code from prose about code, so the better a file is commented
the more false positives it produces — a check that punishes explanation. Those were
harmless because they were loud. **The dangerous mirror image is a check that PASSES
because the forbidden token happens to sit in a comment.**

The first fix used a tokenize heuristic — *"a STRING preceded by NEWLINE/NL/INDENT is a
docstring"*. That silently ate every continuation line of an implicitly concatenated
string inside parentheses, because tokenize emits `NL` for newlines within brackets.
Checks 7 and 8 then failed for the **opposite** reason: the code they looked for had
been deleted by the stripper. Docstrings are now located by **AST position**, which is
exact. Third defect: `code_only()` re-joins tokens without original spacing, so every
substring check against it must be whitespace-insensitive.

---

## 6. PASTE BLOCK — run this on the Mac. Nothing below has been executed.

```bash
cd ~/Desktop/MaritimeApproaches
source .venv/bin/activate

# ---- 0. Is the web stack actually there? This is the unmeasured fact. ----------
python -c "import fastapi, uvicorn; print('fastapi', fastapi.__version__, '| uvicorn', uvicorn.__version__)" \
  || pip install 'fastapi>=0.115' 'uvicorn[standard]>=0.30'

# ---- 1. The scene must be current. STATUS.md 2026-08-29 ARCH+B records
#         04_demo/out/scene01/eo_contacts.jsonl as STALE (pre-detection_confidence).
#         If this raises a ValidationError, regenerate the scene FIRST.
python 04_demo/run_pipeline.py --scene 04_demo/out/scene01

# ---- 2. Start the shore station. --------------------------------------------
python 03_src/server.py --scene 04_demo/out/scene01
#   expect the banner, then silence. Open http://127.0.0.1:8000
#   THE FIRST THING TO CONFIRM: lane D's console renders exactly as it does under
#   app.py. If it does not, /api/state has drifted and that is the bug to chase.
```

In a second terminal:

```bash
cd ~/Desktop/MaritimeApproaches && source .venv/bin/activate
B=http://127.0.0.1:8000

# ---- 3. Health. Read `nodes` and `audit_log` specifically. -------------------
curl -sS $B/health | python -m json.tool

# ---- 4. Evidence. Confirm the identity block is 999-prefixed and that the
#         disclosure block says PSEUDONYMISED, NOT anonymised. ----------------
RID=$(curl -sS $B/api/state | python -c "import json,sys; print(json.load(sys.stdin)['records'][0]['record_id'])")
echo "record: $RID"
curl -sS "$B/evidence/$RID" | python -m json.tool | head -40
curl -sS -o /dev/null -w "allow_real without the flag -> %{http_code} (expect 403)\n" \
     "$B/evidence/$RID?allow_real=1"

# ---- 5. The audit trail. THIS IS THE CRITERION 4 DELIVERABLE. ---------------
CID=$(curl -sS $B/api/state | python -c "import json,sys; r=json.load(sys.stdin)['records'][0]; print((r.get('eo_contact') or {}).get('contact_id','none'))")
curl -sS -X POST $B/action -H 'Content-Type: application/json' -d "{
  \"action\":\"DISPATCH\", \"contact_id\":\"$CID\", \"operator_id\":\"amol\",
  \"action_time_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
  \"note\":\"smoke test\"}" | python -m json.tool
tail -1 04_demo/operator_audit.jsonl | python -m json.tool
#   EXPECT: accepted true, and a line whose "action" block matches contracts.OperatorAction
#   exactly, with recorded_at_utc / server_seq / contact_known OUTSIDE it.

# ---- 6. Bad action is refused loudly, not silently ignored ------------------
curl -sS -o /dev/null -w "missing operator_id -> %{http_code} (expect 422 or 400)\n" \
     -X POST $B/action -H 'Content-Type: application/json' \
     -d '{"action":"DISMISS","contact_id":"x"}'

# ---- 7. No video configured -> honest 503, never a blank frame -------------
curl -sS -o /dev/null -w "/stream unconfigured -> %{http_code} (expect 503)\n" $B/stream
```

### 8. THE TEST THAT MATTERS MOST — "broadcast on change, not on a timer"

This is the one behavioural claim in section 3 that a judge could probe. It is
falsifiable, so falsify it:

```bash
cd ~/Desktop/MaritimeApproaches && source .venv/bin/activate
python - <<'PY'
import asyncio, json, urllib.request, websockets   # websockets ships with uvicorn[standard]

B, WS = "http://127.0.0.1:8000", "ws://127.0.0.1:8000/ws"

def post(path, body):
    r = urllib.request.Request(B + path, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))

async def main():
    async with websockets.connect(WS) as ws:
        snap = json.loads(await ws.recv())
        assert "last_seq" in snap and "event" not in snap, "first message must be ConsoleState"
        print(f"snapshot  : last_seq={snap['last_seq']} records={len(snap['records'])} "
              f"nodes={len(snap['nodes'])} unresolved={len(snap['unresolved_contacts'])}")

        contacts = [json.loads(l) for l in
                    open("04_demo/out/scene01/eo_contacts.jsonl") if l.strip()]

        async def drain(label, seconds=2.0):
            got = []
            try:
                while True:
                    got.append(json.loads(await asyncio.wait_for(ws.recv(), seconds)))
            except asyncio.TimeoutError:
                pass
            kinds = {}
            for e in got:
                kinds[e["event"]] = kinds.get(e["event"], 0) + 1
            print(f"{label:<28} {len(got):>3} events  {kinds}")
            return got

        print(post("/ingest/contacts", {"contacts": contacts, "node_id": "pi-01"}))
        first = await drain("POST #1 (new contacts):")

        print(post("/ingest/contacts", {"contacts": contacts, "node_id": "pi-01"}))
        second = await drain("POST #2 (IDENTICAL body):")

        # THE ASSERTION. Identical input must produce no contact/verdict/queue traffic.
        noise = [e for e in second
                 if e["event"] in ("contact_update", "verdict_update", "queue_update")]
        print()
        print("RESULT:", "PASS — the stream is silent on unchanged input" if not noise
              else f"FAIL — {len(noise)} redundant events: {[e['event'] for e in noise]}")

        seqs = [e["seq"] for e in first + second]
        print("seq gap-free:", seqs == list(range(seqs[0], seqs[0] + len(seqs))) if seqs
              else "n/a (no events)")

asyncio.run(main())
PY
```

**Expected:** POST #1 emits `contact_update` × N, `verdict_update` × N and one
`queue_update`. **POST #2 emits none of those.** If POST #2 produces traffic, a
fingerprint is including a churning field — check `_verdict_key` first, since
`decided_at_utc` is the known offender and is deliberately excluded.

Then leave it idle 40 s with no POSTs and expect **exactly one** `node_status` event as
`pi-01` crosses `--node-stale-after` (30 s) — that is the dead-sensor flip, and it is
the difference between "empty sea" and "we are blind".

### 9. Refresh recovery — the criterion 4 demo moment

With the server running and a console open: hard-refresh the browser mid-demo. The page
must repaint fully from one `ConsoleState`. Under `app.py` this works by accident (it
re-polls); under `server.py` it is the protocol. Worth rehearsing on stage.

---

## 7. OPEN / BLOCKED

1. **`/ws` has no client.** Lane D's `index.html` polls. Request filed to D. Until it
   lands, the stream layer is correct and unexercised except by the script in §8 —
   which is exactly why that script exists.
2. **`/evidence` returns no `pairing_basis` block.** `run_scene()` does not surface the
   `Association` objects it builds, so `record_to_dict(association=None)`. Fabricating
   one would be this server inventing a pairing quality it did not compute — and
   `assoc_ambiguous` is a defer-to-human trigger, the one number a console must never
   guess. Request filed to D.
3. **fastapi/uvicorn presence UNVERIFIED.** §6 step 0 is the measurement.
4. **`FILE_OWNERSHIP.md` has no lane E.** Request filed to ARCH.
5. **Nothing in `server.py` has been executed.** Expect import errors and contract
   validation failures on the first run, not a clean pass.
6. **`--host 127.0.0.1` by default** means the Pi cannot reach it. The live path needs
   `--host 0.0.0.0`, and the banner says so.
7. **Add `?node_id=pi-01`** to `pi_sensor.py --post` for real provenance. It is a query
   parameter on the existing URL — **no change to `pi_sensor.py` is needed.**

---

# ADDENDUM — 2026-08-29 · THE OPERATOR CONSOLE

**Delivered:** `03_src/web/index.html` (one file, ~69 KB, vanilla JS, no build step),
`99_scratch/lane_e_contrast.py`, `99_scratch/lane_e_console_render.png`.
**Static checks now 16/16.** And unlike `server.py`, **this one was actually run.**

## 8. WHAT WAS EXECUTED, AND WHAT WAS NOT

The agent VM has node and the cloud container has Chromium, so the page was rendered
rather than merely written:

| Verified | How |
|---|---|
| JS parses — all 5 blocks, **and concatenated** | `node --check`. The concatenated pass matters: separate `<script>` tags share one global scope, so a duplicate top-level `const` across blocks is a real failure the per-block check misses. |
| Renders with **0 page errors** | Headless Chromium, 1920x1080, against a stub serving `scene01/ranked.json`. |
| All 8 records render, ranks 1-8, all four verdict labels | DOM interrogation, not a screenshot. |
| **The identity guard fires** | A second stub served a real-looking MMSI (`219000606`). Rank 1 rendered `REDACTED` and the alarm banner came up. **This is the safety feature working, measured.** |
| Nothing below the fold at 1080p | `document.scrollHeight <= innerHeight`. Panes measured 427/427/589/589 px. |
| The map asks for the right geometry | A Leaflet **test double** recorded the calls: 1 FOV wedge, 1 cable polyline, 3 infrastructure markers, 1 asset, 27 uncertainty quads + claim markers with correct tooltips. |
| Poll fallback works | The stub has no `/ws`; the page fell back and said so in the header. |
| Every colour ≥ 7:1 (3:1 non-text) | `lane_e_contrast.py`, **18/18**. |

| **NOT verified** | Why |
|---|---|
| **Leaflet's actual rendering** | Neither the CDN nor the npm registry was reachable from the container. The map pane correctly showed its honest fallback message instead. **Check this first on the Mac.** |
| The WebSocket path end to end | The stub is HTTP-only. §6's script is the test. |
| `/stream`, `/action`, `/evidence` against the real server | `server.py` has still never run. |

## 9. THE DEFECT THIS PANE FOUND

Rendering the priority breakdown and **checking the sum** turned up a real bug in
`prioritizer.py`. In `ranked.json`, 6 of 8 records fail
`breakdown_sums_to_score(tol=5e-4)`, and the signature is exact:

```
score / sum(component_scores[k] * component_weights[k])
  = 0.6500   for every record with defer_to_human true   (6 records)
  = 1.0000   for both records with defer_to_human false
```

`DEFER_EVIDENCE_DAMPING = 0.65`. The constant's own comment says *"Applied before the
weighted sum so the published breakdown still adds up."* It is not — it is applied to
the total. `breakdown_sums_to_score()` exists to catch exactly this and was never run
against a deferred verdict, which is most of any real scene.

**The ranking is unaffected** — every deferred verdict is damped identically, so the
order holds. What is wrong is the published **explanation**, which for criterion 2 is
the deliverable. A judge adding up the column on the slide gets 0.657 where the tool
says 0.427.

**`ranked.json` is stale** (old weights 0.32/0.22, not the current 0.30/0.26), so
re-run before fixing — the defect may already be gone. Filed to D in `requests.md`.
The console prints a **"BREAKDOWN DOES NOT SUM"** warning rather than a total it has
not verified; fix the bug and the warning disappears on its own.

## 10. DESIGN DECISIONS WORTH DEFENDING

**The observation is drawn as an uncertainty QUAD, not a dot** — the most honest thing
on the map. Lane C measured cross-range sigma at 218 m and along-range at 1250 m at
5 km, so a monocular fix is a long sliver pointing at the camera. A dot asserts
precision this sensor does not have, and it hides *why* an along-line-of-sight position
spoof is undetectable: the quad is stretched in exactly the direction the deception
hides in. **The limit is visible in the picture, not just in the limitations list.**

**The FOV wedge is not decoration.** A claim with no observation *inside* the wedge is a
finding; the same claim *outside* it is a vessel the camera cannot see. Drawing the
boundary stops an operator reading the second as the first.

**No basemap tiles by default** (`?tiles=1` opts in). Tiles need live internet on every
pan and zoom, and a half-loaded tile grid on a projector reads as a broken tool. The
graticule is deterministic, rehearsable and identical every run.

**Leaflet: local copy first, two CDNs after.** One curl makes the demo immune to venue
wifi and nothing changes if it never happens:
```bash
mkdir -p 03_src/web && cd 03_src/web
curl -sSLO https://unpkg.com/leaflet@1.9.4/dist/leaflet.js
curl -sSLO https://unpkg.com/leaflet@1.9.4/dist/leaflet.css
curl -sSLO https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png   # optional
```
`server.py` mounts that directory at `/assets`. **Do this on hotel wifi, not at the venue.**

**Sticky head and sticky action bar.** The evidence pane holds more than one screen —
correctly, it is a case file. What must never scroll away is *which* contact this is and
the operator's decision buttons. This was found by **looking at a screenshot**: the DOM
dump reported the buttons present and correct, and they were — 200 px below the fold.

**A categorical difference that raised no mismatch says so.** Claimed `fishing` beside
observed `tug` with no annotation reads as the tool having missed it. It did not; the
consistency layer declined to raise it. Restricted to categorical dimensions — for a
numeric field a small difference is normal and flagging every one would be noise.

**`BELIEF IS STALE`.** `contracts.SensorNode` carries `online` and `last_seen`
separately so the belief can be checked against its evidence. When a node reports online
but was last heard from beyond the threshold, the badge turns amber. **The threshold
comes from `/health`, never from a number invented in the page** — two components
disagreeing about when a sensor is dead is worse than either answer.

## 11. WHAT CHANGED IN server.py

1. **Pseudonymisation of everything outbound.** `run_scene()` returns REAL
   `claimed_mmsi` / `claimed_name` / `claimed_imo` / `claimed_callsign`, and both
   `/api/state` and the WebSocket served them verbatim. On the synthetic scene that is
   harmless *because the identities are already 999-prefixed* — which is what made it
   dangerous: **invisible until someone points the server at the real Fehmarn golden
   window, and then a real named vessel appears on a projector under a SPOOF badge.**
   There are now two record stores: `_records` (real, only ever reachable through
   `/evidence` and its two gates) and `_public_records` (pseudonymised, everything the
   console can see). One shared `Anonymiser` per run — a fresh instance per record
   allocates index 0 every time and turns **every** vessel into `999000001`.
2. `/assets` static mount for the local Leaflet copy.
3. `--web` now defaults to `03_src/web`. Point it at `04_demo/web` for lane D's page.
4. `/health` publishes `node_stale_after_s`.

## 12. DEMO NOTE

Keyboard works without a mouse: `↑`/`↓` move the selection, `1`-`9` jump to a rank.
Useful on a lectern. The operator id persists in `localStorage`; set it before the
pitch so the audit trail carries a real name.

---

# ADDENDUM 2 — 2026-08-29 · DEMO HARDENING + REHEARSAL

Three things break live demos. All three are now handled, and the handling was
**measured against a real WebSocket, not reasoned about.**

## 13. THE REHEARSAL — kill the server mid-demo, restart it, time it

Run in headless Chromium against a stdlib WebSocket stub that speaks `server.py`'s
`/ws` protocol. The stub's fidelity point: **`seq` resets to 0 on every process start,
exactly as `server.py`'s counter does** — that is what makes a restart different from a
dropped socket, and it is the case that breaks a naive client.

### Measured

| | |
|---|---|
| **Refresh mid-demo → full picture back** | **239 ms**, selection preserved, evidence panel reopened on the same contact |
| Kill → console notices | **5 ms** (socket close is synchronous) |
| Kill → honest `SERVER UNREACHABLE` state | **2.0 s** (one poll interval — it waits to be sure) |
| While down | **8 of 8 rows still on screen**, desaturated, every pane heading marked `· STALE`, age counting up, retry counting down |
| Backoff intervals actually observed | 0.54, 1.06, 2.48, 3.68, 6.37 s — the intended 0.5/1/2/4/8 with ±25% jitter |
| **Restart → console live again (worst case, backoff at its cap)** | **0.21 / 2.01 / 2.02 s → median 2.01 s, max 2.02 s** |
| Stream genuinely flowing after restart | seq 0 → 3, verified — not merely "connected" |
| Page errors, across every run | **none** |

Screenshots: `99_scratch/lane_e_console_down.png` (the degraded state),
`99_scratch/lane_e_console_injected.png` (an injected contact, labelled).

**The server half is NOT in these numbers.** `server.py` cannot run in the agent's
container — no fastapi, no pyproj, no loguru, and no route to PyPI to install them.
Run `99_scratch/lane_e_rehearse_server.py` on the Mac for it; it reports `boot_ms`
(import + scene load) and `pipeline_ms` (`run_scene()` over the whole scene) separately,
because they have different causes and only one of them is worth attacking.

**Total an operator sees = server restart + ≤2.02 s client.**

### Two defects the rehearsal found — neither would have been found by reading

**1. A dead server reported a healthy fallback.** Every reconnect attempt ends in
`onclose`, which called `setTransport("poll")` unconditionally. So with the server
killed, the header read *"polling every 2 s — the picture below is live but slower"*
while the polls were failing too. **The page was describing a fallback that was not
running.** That is precisely the looks-fine-but-isn't failure this work exists to
prevent, and it survived a code review by me because the code is correct in every state
except the one nobody reaches by reading. Poll health (`S.pollOk`) is now tracked
separately from socket health, and `"poll"` is only ever displayed after a fetch has
actually succeeded. Down now says: *"neither the socket nor the REST fallback is
answering. THE PICTURE BELOW IS HISTORY."*

**2. Recovery took 7.09 s when the restart landed badly.** First run measured 1.48 s,
second 7.09 s — the difference is where in the backoff the restart happened. On stage
the number that matters is the worst case. **The fix is that the 2 s poll was already a
liveness probe and was not being used as one:** a successful `/api/state` fetch is proof
the server is back, so the pending retry is cancelled and the socket reopened at once.
Recovery is now bounded by the poll interval — **2.02 s worst case regardless of how far
the backoff had escalated.** Backing off stays correct where there is no other evidence;
this is the case where there is evidence.

## 14. RECONNECT (item 1)

Backoff **0.5, 1, 2, 4, 8 s then 8 s**, each with ±25% jitter.
- **Capped at 8 s, not the usual 30-60.** A datacentre schedule would leave the console
  dark for half a four-minute slot after a blip.
- **Jittered** so two consoles on one laptop do not reconnect in lockstep forever.
- **Reset on a successful OPEN, never on an attempt** — a server that accepts TCP and
  immediately closes would otherwise reset the schedule every time and turn the backoff
  into a busy loop.
- **Superseded by the poll probe** whenever REST answers first (§13).

**The restart bug this fixes, which is the nastiest of the three:** `seq` is monotonic
*per connection* and starts again at 0 when the process restarts. A client that kept its
old `lastSeq` across a restart would discard every event from the new server as "already
folded in" — **it would reconnect, look connected, and never update again.** The snapshot's
`last_seq` is therefore ASSIGNED, never `max()`'d. A `last_seq` lower than the one held
is the restart signature and raises an 8 s notice, *"SERVER RESTARTED — picture rebuilt
from its snapshot"*, which then clears itself.

Precisely what that detects — narrower than "detects restarts", and the rehearsal is why
the claim is narrow: it fires when the client holds a **higher** seq than the server now
has, which is exactly the case that would stall the stream. A server that restarts having
emitted nothing is not announced, and should not be — nothing was lost.

## 15. REFRESH SAFETY (item 2)

The **data** half was already solved by `contracts.ConsoleState` — the whole picture in
one message. What the server cannot restore is **which contact the operator was reading**,
and losing that on stage means hunting for the row while a judge waits. The selected
`record_id` goes in `sessionStorage`: survives a refresh, does not survive closing the
tab, so tomorrow's demo does not open on yesterday's selection. Reads and writes are in
`try/catch` — a browser with site data blocked throws on *access*, and a console that
will not start because it cannot remember a selection has traded a small problem for a
total one.

If the remembered contact is gone (different scene, or an injection replaced it) the page
**says so** rather than silently selecting something else. A quiet substitution is how an
operator stops trusting a tool.

Measured: **239 ms**, selection and open evidence panel both preserved.

## 16. SCRIPTED INJECTION (item 3)

```
POST /demo/inject {"scenario":"spoof"}                      the key moment
POST /demo/inject {"scenario":"spoof","contact_id":"c-0004"}  pinned, rehearsable
POST /demo/inject {"scenario":"dark"}
POST /demo/inject {"scenario":"reset"}
POST /demo/inject {"scenario":"prepared","contacts":[...]}    a prepared file
GET  /demo/inject                                             what is injected now
```

**The label is inside the contract object, not beside it.** An injected contact gets
`contact_id: "inj-…"` and `frame_ref: "injected://spoof/…"`. `frame_ref` is a real
`EoContact` field and `evidence.build_record()` copies it into
`EvidenceRecord.frame_refs` — so **the word INJECTED survives into the exported case file
at `/evidence/{id}`.** A judge downloading the evidence sees it without being told, and
there is no code path in this server that can produce an injected contact whose
provenance is absent. A UI-only badge would be a promise; this is a property.

Shown in four places, verified in the browser: the header chip, a dashed badge on the
queue row, a badge in the evidence header, and **the first line of the limitations** —
above the real ones, because a scripted contact that reads like a real detection is the
most misleading thing this console could put in front of a judge. The detection box is
drawn **dashed**, so the distinction survives a black-and-white projector.

**What the injection does not touch: bearing, range, their uncertainties, or the camera
pose.** Association is geometry-only, so leaving the geometry pristine is what guarantees
the injected contact pairs with the *same* AIS claim the original did. Only the attributes
under comparison change — which is what a spoof *is*: a claim and an observation that
disagree about what the hull is while agreeing about where it is.

Other decisions: the scenario **pins the source to RECORDED** (a scripted moment a live
node can overwrite two seconds later is not scripted); every injection **names an
operator**, as `/action` does; and it is **deliberately NOT written to
`operator_audit.jsonl`** — that file records decisions about vessels, and mixing demo
stagecraft into it would corrupt the one artefact whose value is that everything in it is
a human judgement about a contact. Default target is the matched contact with the **worst**
current rank, so the re-rank is visible; the chosen id is returned so a rehearsed demo can
pin it.

## 17. DEMO CARD

```bash
# before the venue, on good wifi
cd 03_src/web && curl -sSLO https://unpkg.com/leaflet@1.9.4/dist/leaflet.js \
                && curl -sSLO https://unpkg.com/leaflet@1.9.4/dist/leaflet.css

python 03_src/server.py --scene 04_demo/out/scene01 --host 0.0.0.0
python 99_scratch/lane_e_rehearse_server.py --runs 3      # get YOUR restart number

# on stage, one keystroke each
curl -sX POST localhost:8000/demo/inject -H 'Content-Type: application/json' \
     -d '{"scenario":"spoof","operator_id":"amol"}'
curl -sX POST localhost:8000/demo/inject -H 'Content-Type: application/json' \
     -d '{"scenario":"reset","operator_id":"amol"}'
```
Keyboard: `↑`/`↓` move the selection, `1`-`9` jump to a rank. No mouse needed on a lectern.
