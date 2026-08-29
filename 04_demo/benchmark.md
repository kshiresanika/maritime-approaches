# benchmark.md — the integrated rig

**Created 2026-08-29 (lane ARCH). Status: INSTRUMENT BUILT AND UNIT-TESTED. ALL RIG NUMBERS UNMEASURED.**

Subject: the whole system end to end — `03_src/main.py` driving AIS replay, edge
ingest, fusion and the console, with a sensor node posting real contacts.

Instrument: `04_demo/benchmark.py`. It writes the filled version of this file.

---

## 0. Why every table below is empty

The agent that built this could not run it. Three separate walls, and all three
would have to be described as "measured" for anything here to be filled in
honestly:

- the Mac's `.venv` is unreachable — `device_bash` is a network-less Linux VM with
  no `pydantic` and no `python3.12`, and PyPI answers 403 through the proxy;
- the cloud container has no `pyproj`, `loguru` or `fastapi` and no package index,
  so `association.py` and `geometry.py` cannot import and the pipeline cannot run;
- the Pi has been on neither path for the whole project.

Filling these tables from a plausible-looking estimate would break the project's
MEASURED NUMBERS ONLY rule in the one place where it matters most, because these
are the numbers that go on a slide. `04_demo/edge_benchmark.md` already sets the
house style for this situation and this file follows it.

```bash
# on the Mac, in the venv
python 03_src/main.py --camera 0                    # terminal 1: the rig
python 04_demo/benchmark.py all \
    --expect-browser --emit 04_demo/benchmark.md    # terminal 2: the instrument
```

`benchmark.py` **overwrites this file** with the measured version. Everything below
is what it will fill.

---

## 1. THE HEADLINE — capture to VISIBLE re-rank

| interval | p50 | p95 | worst | n |
|---|---|---|---|---|
| capture → pixels on glass (SINGLE CLOCK) | **UNMEASURED** | | | |
| POST → `queue_update` on the wire | **UNMEASURED** | | | |
| WS received → painted (2× rAF) | **UNMEASURED** | | | |

### The clock problem, because it is the whole difficulty of this number

The obvious implementation is

```
browser_paint_time  −  EoContact.frame_time_utc
```

and it is **wrong**, silently, in an unknown direction and by an unknown amount.
Those two stamps come from two machines. `frame_time_utc` is written on the Pi; the
paint happens on the Mac. The difference between their clocks is whatever NTP has
managed — and at a venue, on contested wifi, with a Pi that may have booted without
a network, the skew can be **seconds**. Seconds is larger than the quantity being
measured. That subtraction returns a confident, precise, meaningless number, and it
can come out negative.

So the instrument never subtracts two clocks. It takes two measurements instead.

**(A) The headline. One clock, nothing estimated.**
Run the sensor on the Mac — `main.py --camera 0`, or the benchmark's own probe
contact. Capture stamp, server and browser are then all the same clock, and
`t_painted − frame_time_utc` is a true end-to-end interval with no skew term in it.
**This is the number to say out loud**, and the sentence that goes with it is
*"measured on a single clock, so there is no clock-skew term in it."*

**(B) The Pi, decomposed into legs that are each on one clock.**

| leg | interval | clock |
|---|---|---|
| 1 | capture → POST sent | Pi monotonic |
| 2 | Pi → Mac network | **half the POST round trip — the only ESTIMATE here** |
| 3 | POST sent → `queue_update` emitted | the benchmark's clock (Mac) |
| 4 | WS received → pixels on glass | browser `performance.now()` |

Leg 2 is the only interval spanning two machines, and half-RTT assumes a symmetric
path. On a switched LAN that is close. It is labelled an estimate everywhere it
appears, and it is the reason (A) rather than (B) is the number for the pitch.

### Why the paint leg uses two `requestAnimationFrame`s

One rAF callback runs *before* the frame carrying the DOM writes is composited — it
means "the browser agreed to paint", not "the pixels are on the glass". The second
fires on the frame after, once the first has been presented. The claim is a
**visible** re-rank, so the measurement has to be visible. It costs about one frame
(~16 ms at 60 Hz) and errs slow, which is the right direction to err.

### Why the collector is not a route on `server.py`

The shore station is the system under measurement. Putting the instrument endpoint
inside it makes the measured process and the measuring process the same process, and
the first question anyone asks about a latency number is whether the measurement
perturbed it. `benchmark.py` runs its own one-route listener on `:8899`, so
`server.py` is byte-identical whether or not anyone is benchmarking, and there is
nothing to remember to remove before going on stage.

---

## 2. Detection rate per scenario

| scenario | caught | trials | rate | 95% CI | actionable FP | controls |
|---|---|---|---|---|---|---|
| identity_length | **UNMEASURED** | | | | | |
| identity_class | **UNMEASURED** | | | | | |
| position | **UNMEASURED** | | | | | |
| ais_off | **UNMEASURED** | | | | | |

**Identity is split in two on purpose.** A vessel can lie about what it is in two
independent ways, and the pipeline treats them differently: length is physical
extent and cannot be argued with, class rides on a classifier and is deliberately
capped so it can never convict alone. Averaging them into one "identity" figure
would hide that the strong half is carrying the weak one.

**A fresh seed per trial.** With one fixed scene the number says whether the
pipeline handles *one* arrangement of vessels — a figure that cannot generalise and
will be quoted as though it can. Re-drawing geometry, noise, victims and spoof
factors each trial is what turns "it caught the spoof" into "it catches this class
of spoof at this rate, with this interval".

**Wilson intervals, not normal ones.** At 12/12 the normal approximation returns
[1.00, 1.00] — certainty, from twelve trials. Wilson returns [0.76, 1.00], which is
the truth. Small samples are exactly where the ends matter and exactly what a
hackathon produces.

**Two false-positive numbers, both reported.** *Actionable* FP = labelled DARK or
SPOOF **and not** deferred. A deferred verdict is the tool saying "I cannot tell, a
human should look", which is the behaviour criterion 4 asks for — counting it as a
false positive understates the tool, and counting only actionable ones without
saying so flatters it. `score_scene` already separates them; this document quotes
both.

**Controls ride along in every trial.** They are not a scenario — they are the
reason the detection numbers mean anything. A scene with no controls cannot measure
a false-positive rate, and a detection rate without one is a marketing number.

---

## 3. Source switch (lane F, F3)

| interval | p50 | p95 | worst | n |
|---|---|---|---|---|
| `POST /source` returns | **UNMEASURED** | | | |
| …and the console is told | **UNMEASURED** | | | |

Lane F measured `SourceSwitch.switch()` itself at p50 **0.0032 ms** (n=3000, cloud
container) — the object changing its mind is free, and it is not the cost. What an
operator experiences is the second row: the POST returning *and* a stream event
carrying the new authority reaching a subscriber, which includes the pipeline
recompute lane F recorded as **SKIPPED** because `pyproj` was absent. That recompute
is the half that was never measured, and it is the half that takes time.

The physical unplug adds the node's own detection latency on top. Worst case for the
console to show OFFLINE is the 8 s staleness threshold plus the 1 s watchdog tick =
**9 s**, and that is a threshold, not a measurement.

---

## 4. Sensor nodes

| node | kind | online | measured FPS |
|---|---|---|---|
| **UNMEASURED** | | | |

Read straight off `/health`, never computed here and never defaulted.
`contracts.py` leaves `SensorNode.measured_fps` nullable precisely so that "nobody
counted" is representable; a plausible `30` printed next to a node delivering 4 is
the exact failure MEASURED NUMBERS ONLY exists to prevent. A null renders as
UNMEASURED, which is the truth.

Per-`imgsz` sweeps, thermals and the throttle flag live in
`04_demo/edge_benchmark.md` and are also still unmeasured.

---

## 5. What is deliberately NOT in these numbers

- **Any claim about real-world spoofing rates.** The detection rates are measured
  against faults *this project injected*, chosen because they are visible. They are
  an upper bound on a real rate, never a prediction of one.
- **Anything about the sea.** Every EO contact in replay mode is forward-projected
  from AIS through a declared pose and perturbed by a declared error model. No
  camera observed anything. `READ_ME_FIRST.txt` in every scene directory says so and
  is a tripwire — if it is missing, someone copied the scene and dropped the caveat.
- **The along-line-of-sight position spoof.** It is undetectable by one monocular
  sensor at any offset, ever. It is not a low score in the `position` row; it is
  outside what the row can measure. See `consistency.check_position`.

---

## 6. What HAS been verified, and where

Unit-tested in the cloud container (python 3.11.15, pydantic 2.13.3) — the
instrument, not the rig:

- Wilson interval at the degenerate ends (12/12, 0/12, n=0);
- nearest-rank percentiles — p95 is always a value the system actually produced,
  never an interpolation between two it did not;
- the probe contact validates as a real `EoContact` and carries **no** identity
  field, so the claimed/observed wall holds at the ingest boundary;
- **the renderer cannot fabricate.** An empty run produces UNMEASURED and zero
  digit-bearing latency or rate cells; a partial run reports the leg that was
  measured and leaves the end-to-end row UNMEASURED beside it, with an explicit
  do-not-quote warning; a null `measured_fps` renders UNMEASURED rather than a
  plausible number. 18/18 checks.
- the paint hook's JavaScript passes `node --check`, and is inert without `?bench=1`.

**Not verified:** anything requiring the pipeline, the server, a browser or the Pi.
Expect first-run failures — this instrument has never met the system it measures.
