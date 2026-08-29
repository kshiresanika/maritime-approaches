# FAILOVER.md — surviving the Pi dying mid-pitch

**Lane F, 2026-08-29.** Source switch: `EDGE_PI | MAC_CAMERA | RECORDED`.

---

## 0. BLOCKER ABOVE THIS ONE — CLEARED 2026-08-29

**RESOLVED.** `03_src/consistency.py` now exports `ConsistencyResult` (line 201) and
`independent_dimension_count` (line 214); `03_src/verdict.py:122` imports both. The
four `ImportError`s recorded here are gone.

**What that does and does not license.** The import chain is whole, so the failover
below *can* now be exercised. It has still not been exercised end to end — §5 lists the
two quantities that remain unmeasured and both need the Mac. Do not read a cleared
blocker as a passed test.

---

## 1. The design in one line

> The switch selects an **authority**. It does not move data.

`03_src/source_switch.py` holds no reference to the contact store, the record store, the
evidence queue or the anonymiser. It **cannot** clear them — that is a property of the
object graph, asserted by inspecting `__dict__`, not a promise kept by careful coding.
A switch written as "reset state, then re-seed from the new source" would pass every
functional test on a good day and lose the operator's queue on the one day it fires.

## 2. The rule that makes 5 seconds achievable

**Run both nodes from the start.** Every ingest buffers its contacts by sensor kind
*even when that node is not the authority*, so switching promotes contacts that are
already in memory. Starting the Mac camera at the moment the Pi dies turns a
sub-millisecond switch into however long YOLO takes to load and see something.

A node that is not the authority is still **accepted** — its liveness is recorded so the
console can show the Pi coming back — but its contacts are **not promoted**. Two
consequences: a rebooting Pi cannot silently seize the picture back, and the console can
say *"edge-pi-01 is posting but MAC_CAMERA is live"*, which is a different sentence from
*"edge-pi-01 is offline"* and stops you debugging a sensor that is fine.

## 3. The switch is NOT gated on the node being declared dead

Deliberate. An operator who can see the video has frozen must not wait out a staleness
threshold before they are allowed to act. **Detection is for the strip; the switch is for
the operator.** They run in parallel:

| | mechanism | measured |
|---|---|---|
| Operator switches | `POST /source` | **0.0032 ms** p50 (see §5) |
| Console shows OFFLINE | watchdog, `NODE_STALE_AFTER_S` | **≤ 9 s** worst case |

`NODE_STALE_AFTER_S` was **30.0** and is now **8.0**. Derivation: the node posts contacts
every `--interval` (1.0 s) and a heartbeat every `--heartbeat` (2.0 s) when the sea is
empty, so 8 s is four consecutive missed heartbeats — past ordinary jitter, and short
enough to matter inside a five-minute slot. At 30 s an unplugged Pi stayed green for half
the pitch. Do not go below ~3× the heartbeat: a strip that flickers teaches the operator
to ignore it.

**The heartbeat is why this works at all.** Before it, the node posted only when it had
contacts, so a healthy sensor watching an empty sea posted nothing and the watchdog
declared it dead — the exact "quiet queue or dead sensor?" ambiguity `SensorNode` exists
to resolve, reintroduced from the sensor end. Heartbeats carry `heartbeat: true` so the
server records liveness without blanking the picture or spinning the pipeline.

## 4. Run sheet

```bash
# --- Mac, terminal 1: the shore station. --host 0.0.0.0 or the Pi cannot reach it.
python3 03_src/server.py --host 0.0.0.0 --initial-source RECORDED \
        --mjpeg-url http://<pi-ip>:8080/stream

# --- Mac, terminal 2: the MAC_CAMERA source. START IT NOW, NOT AT THE FAILURE.
python3 edge/sensor_node.py --backend cv2 --source 0 --kind mac_camera \
        --node-id mac-cam-01 --pose 04_demo/camera_pose_TABLETOP.json \
        --post http://127.0.0.1:8000/ingest/contacts --port 8081

# --- Pi: the EDGE_PI source
python3 edge/sensor_node.py --kind edge_pi --node-id edge-pi-01 \
        --pose 04_demo/camera_pose_TABLETOP.json \
        --post http://<mac-ip>:8000/ingest/contacts --port 8080

# --- Mac, terminal 3: watch both nodes from the outside
python3 03_src/edge_client.py --node http://<pi-ip>:8080 --node http://127.0.0.1:8081 \
        --server http://127.0.0.1:8000 --watch
```

Then in the console: `SOURCE EDGE_PI` → click **MAC_CAMERA**. The badge turns red the
moment the Pi's node flips offline; the note line reports the measured round trip.

Switching from the shell instead:

```bash
curl -s -X POST localhost:8000/source -H 'Content-Type: application/json' \
     -d '{"source":"MAC_CAMERA","operator_id":"amol","reason":"pi unplugged"}' | python3 -m json.tool
```

The response returns `switch_ms`, `recompute_ms`, `total_ms`, `buffered_contacts` and
**`records_before` / `records_after`** — so "the queue survived" is something you verify
from the response rather than take on trust.

## 5. MEASURED

`99_scratch/lane_f_failover_check.py`, cloud container (python 3.11.15, pydantic 2.13.3).
**24 checks in Part A, all passing.**

| Quantity | Measured | n |
|---|---|---|
| `SourceSwitch.switch()` p50 | **0.0032 ms** | 3000 |
| p95 | **0.0072 ms** | 3000 |
| worst observed | **0.1237 ms** | 3000 |

Verified structurally, not asserted:
- `SourceSwitch.__dict__` contains only `active`, `operator_id`, `changed_at_utc`,
  `history`, `_demoted`, `_lock` — **no data reference exists to clear**;
- operator labels map onto `contracts.SensorKind`; there is no parallel enum;
- an unselected node is not promoted, gets an explanation, and an unknown kind neither
  becomes the authority nor raises;
- a no-op switch is *recorded and flagged*, not dropped — "the operator pressed it and
  nothing happened" differs from "they never pressed it", and only one means confusion;
- `SwitchEvent` is frozen: a source change is evidence and cannot be edited afterwards.

**NOT YET MEASURED, and both need the Mac:**
1. `recompute_ms` — the pipeline half, which is the real cost. Part B of the harness runs
   it, and is **skipped** in the container: pyproj is absent, and a recompute timed
   against a substitute geodesy library is not a measurement of this system.
2. The physical unplug. That adds the node's own detection latency — a last in-flight
   POST, TCP timeouts — on top of everything above. Run §4, pull the cable, and record it.

> Reporting `switch_ms` and `recompute_ms` as one number would hide which half is the
> cost. The switch is free; the recompute is the price, and it is the half that does not
> get better by writing faster switch code.

## 6. What still has to be true on the day

- [ ] Lane C's `ConsistencyResult` P0 fixed — nothing runs until then (§0).
- [ ] `camera_pose_TABLETOP.json` has a non-zero `hfov_deg`. Both nodes refuse to start
      without it, by design.
- [ ] `server.py` started with `--host 0.0.0.0`, and the macOS incoming-connections
      dialog **accepted**.
- [ ] Both nodes running and both visible in `edge_client --watch` before the pitch.
- [ ] Practise the switch once. The button is one click; finding it while talking is not.
