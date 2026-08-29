"""
edge/bench_edge.py — measure the edge node. RUNS ON THE PI.

WHY A SEPARATE HARNESS AND NOT A --benchmark FLAG ON THE NODE
The node's job is to keep a stream alive and never block. A benchmark's job is to stall
on purpose, sweep parameters and report percentiles. Bolting the second onto the first
means the measurement code is running during the demo.

WHAT IT MEASURES, AND WHY EACH ONE IS NOT OPTIONAL

  FPS AS A DISTRIBUTION, NOT A MEAN. A mean of 6 FPS hides a 400 ms stall every two
  seconds, and a stall is exactly what an operator notices. p50/p95/worst are reported
  together and the mean is reported last.

  PER-STAGE TIMING. "6 FPS" is not actionable; "capture 8 ms, inference 140 ms, encode
  12 ms" tells you the only lever that matters is imgsz. Without the split you tune the
  wrong thing.

  CPU TEMPERATURE OVER FIVE MINUTES, PLUS THE THROTTLE FLAG. A Pi that thermally
  throttles mid-run produced a number nobody can reproduce. `vcgencmd get_throttled` is
  captured before and after, and if it is non-zero the FPS in the same report is
  labelled UNRELIABLE rather than quietly published.

  DETECTION RATE ON A TEST OBJECT. Frames-with-a-detection over frames-total, plus the
  confidence distribution. This is the number that decides whether the YOLO node is
  viable at all: COCO's 'boat' may not fire on a printed silhouette, and if the rate
  comes back near zero the answer is to switch to 04_demo/pi_sensor.py (classical,
  MOG2), not to lower --conf until something appears.

ON A LOW FPS RESULT — DO NOT APOLOGISE FOR IT, RECORD IT.
A vessel at 12 knots covers 6.2 m/s. At 2 FPS the between-frame displacement is 3.1 m,
which is far inside the position uncertainty this pipeline already carries (measured
cross-range sigma 218 m at 5 km). Frame rate is not the binding constraint on a
maritime picture; it is the binding constraint on a self-driving car, which is a
different product. Put the measured number on the slide with that sentence next to it.

USAGE
    python3 edge/bench_edge.py --mode sweep          # imgsz sweep, ~30 s each
    python3 edge/bench_edge.py --mode soak           # 5 min sustained + temp curve
    python3 edge/bench_edge.py --mode sweep --json out.json
Point the camera at the test object first. Both modes print a markdown block ready to
paste into 04_demo/edge_benchmark.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_HERE), str(_ROOT / "04_demo")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sensor_node import BoatDetector, read_cpu_temp_c, read_throttled  # noqa: E402


def _pct(values: list[float], q: float) -> float:
    """Percentile without numpy's interpolation argument, so this file needs no numpy."""
    if not values:
        return float("nan")
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def _identity() -> dict:
    """Machine identity travels WITH every number. A benchmark without the machine it
    ran on is not evidence, and this report is going in front of judges."""
    def _read(p: str) -> str | None:
        try:
            return Path(p).read_text().replace("\x00", "").strip()
        except Exception:
            return None
    import platform
    return {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "pi_model": _read("/proc/device-tree/model"),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_temp_start_c": read_cpu_temp_c(),
        "throttled_start": read_throttled(),
    }


def run_once(*, imgsz: int, frames: int, args, picam2) -> dict:
    """
    One measurement run at one imgsz. Returns per-stage timings and detection counts.

    The detector is constructed INSIDE the run so each imgsz gets a clean tracker; a
    tracker carrying state across a resolution change would attribute frames from the
    previous run to ids in this one and inflate track_length_frames.
    """
    import cv2
    det = BoatDetector(args.weights, conf=args.conf, imgsz=imgsz,
                       track=not args.no_track, threads=args.threads)

    # WARMUP IS NOT OPTIONAL. The first torch inference pays lazy kernel init and can
    # be 10x the steady-state cost. Including it makes short runs look worse than the
    # thing they are measuring.
    for _ in range(args.warmup):
        det(picam2.capture_array())

    t_cap: list[float] = []
    t_inf: list[float] = []
    t_enc: list[float] = []
    t_tot: list[float] = []
    frames_with_det = 0
    total_dets = 0
    confs: list[float] = []

    for _ in range(frames):
        f0 = time.perf_counter()
        frame = picam2.capture_array()
        f1 = time.perf_counter()
        dets = det(frame)
        f2 = time.perf_counter()
        ok, _buf = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                                [int(cv2.IMWRITE_JPEG_QUALITY), args.jpeg_quality])
        f3 = time.perf_counter()

        t_cap.append((f1 - f0) * 1000.0)
        t_inf.append((f2 - f1) * 1000.0)
        t_enc.append((f3 - f2) * 1000.0)
        t_tot.append((f3 - f0) * 1000.0)
        if dets:
            frames_with_det += 1
            total_dets += len(dets)
            confs.extend(d["conf"] for d in dets)

    fps = [1000.0 / t for t in t_tot if t > 0]
    return {
        "imgsz": imgsz,
        "frames": frames,
        "fps_mean": round(statistics.fmean(fps), 2) if fps else None,
        "fps_p50": round(_pct(fps, 0.50), 2),
        "fps_p95": round(_pct(fps, 0.95), 2),
        "fps_worst": round(min(fps), 2) if fps else None,
        "ms_capture_p50": round(_pct(t_cap, 0.50), 1),
        "ms_inference_p50": round(_pct(t_inf, 0.50), 1),
        "ms_encode_p50": round(_pct(t_enc, 0.50), 1),
        "ms_total_p95": round(_pct(t_tot, 0.95), 1),
        "frames_with_detection": frames_with_det,
        "detection_rate": round(frames_with_det / frames, 3) if frames else None,
        "total_detections": total_dets,
        "conf_p50": round(_pct(confs, 0.50), 3) if confs else None,
        "conf_min": round(min(confs), 3) if confs else None,
        "cpu_temp_c": read_cpu_temp_c(),
    }


def run_soak(*, args, picam2) -> dict:
    """
    Sustained run with a temperature curve. This is the number that decides whether the
    node survives a five-minute demo slot, which the sweep cannot answer: a Pi is fast
    for thirty seconds and then throttles.
    """
    import cv2
    det = BoatDetector(args.weights, conf=args.conf, imgsz=args.imgsz,
                       track=not args.no_track, threads=args.threads)
    for _ in range(args.warmup):
        det(picam2.capture_array())

    t_end = time.time() + args.soak_seconds
    samples: list[dict] = []
    next_sample = time.time()
    fps_window: list[float] = []
    frames = frames_with_det = 0
    t0 = time.time()

    while time.time() < t_end:
        a = time.perf_counter()
        frame = picam2.capture_array()
        dets = det(frame)
        cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                     [int(cv2.IMWRITE_JPEG_QUALITY), args.jpeg_quality])
        dt = time.perf_counter() - a
        frames += 1
        if dets:
            frames_with_det += 1
        if dt > 0:
            fps_window.append(1.0 / dt)

        if time.time() >= next_sample:
            samples.append({
                "t_s": round(time.time() - t0, 1),
                "cpu_temp_c": read_cpu_temp_c(),
                "fps_window_mean": (round(statistics.fmean(fps_window), 2)
                                    if fps_window else None),
                "throttled": read_throttled(),
            })
            # The window is CLEARED each sample so the curve shows fps AT that moment
            # rather than a running average, which would smooth away exactly the
            # throttling collapse this run exists to detect.
            fps_window = []
            next_sample = time.time() + args.sample_every
            print(f"  t={samples[-1]['t_s']:6.1f}s  "
                  f"temp={samples[-1]['cpu_temp_c']}C  "
                  f"fps={samples[-1]['fps_window_mean']}  "
                  f"{samples[-1]['throttled']}", flush=True)

    temps = [s["cpu_temp_c"] for s in samples if s["cpu_temp_c"] is not None]
    return {
        "soak_seconds": args.soak_seconds,
        "imgsz": args.imgsz,
        "frames": frames,
        "frames_with_detection": frames_with_det,
        "detection_rate": round(frames_with_det / frames, 3) if frames else None,
        "fps_overall": round(frames / max(time.time() - t0, 1e-6), 2),
        "cpu_temp_start_c": temps[0] if temps else None,
        "cpu_temp_end_c": temps[-1] if temps else None,
        "cpu_temp_max_c": max(temps) if temps else None,
        "throttled_end": read_throttled(),
        "samples": samples,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Measure the Pi edge node.")
    p.add_argument("--mode", choices=["sweep", "soak"], default="sweep")
    p.add_argument("--weights", default=str(_ROOT / "yolov8n.pt"))
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--imgsz", type=int, default=320, help="soak mode only")
    p.add_argument("--sweep", default="256,320,416,640",
                   help="comma-separated imgsz values for sweep mode")
    p.add_argument("--frames", type=int, default=60, help="timed frames per sweep point")
    p.add_argument("--warmup", type=int, default=5)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--threads", type=int, default=None)
    p.add_argument("--no-track", action="store_true")
    p.add_argument("--jpeg-quality", type=int, default=70)
    p.add_argument("--soak-seconds", type=float, default=300.0, help="default 5 minutes")
    p.add_argument("--sample-every", type=float, default=15.0)
    p.add_argument("--json", default=str(_ROOT / "04_demo" / "edge_benchmark.json"))
    args = p.parse_args(argv)

    if args.threads is None:
        import os
        args.threads = max(1, (os.cpu_count() or 2) - 1)

    from picamera2 import Picamera2
    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(
        main={"size": (args.width, args.height), "format": "RGB888"}))
    picam2.start()
    time.sleep(1.0)

    report: dict = {"identity": _identity(), "mode": args.mode,
                    "capture_size": [args.width, args.height],
                    "conf": args.conf, "threads": args.threads,
                    "tracking": not args.no_track}
    try:
        if args.mode == "sweep":
            report["runs"] = []
            for s in [int(x) for x in args.sweep.split(",") if x.strip()]:
                print(f"\n--- imgsz {s} ---", flush=True)
                r = run_once(imgsz=s, frames=args.frames, args=args, picam2=picam2)
                report["runs"].append(r)
                print(f"  fps p50={r['fps_p50']} p95={r['fps_p95']} worst={r['fps_worst']}  "
                      f"inf={r['ms_inference_p50']}ms  det_rate={r['detection_rate']}",
                      flush=True)
        else:
            print(f"--- soak {args.soak_seconds:.0f}s at imgsz {args.imgsz} ---", flush=True)
            report["soak"] = run_soak(args=args, picam2=picam2)
    finally:
        picam2.stop(); picam2.close()

    report["throttled_end"] = read_throttled()
    report["cpu_temp_end_c"] = read_cpu_temp_c()

    Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nJSON written to {args.json}")

    # -------- markdown block, ready to paste into 04_demo/edge_benchmark.md --------
    thr = str(report.get("throttled_end") or "")
    unreliable = ("0x0" not in thr) and thr != ""
    print("\n" + "=" * 74)
    print("PASTE THE BLOCK BELOW INTO 04_demo/edge_benchmark.md")
    print("=" * 74 + "\n")
    ident = report["identity"]
    print(f"Measured {ident['measured_at_utc']} on {ident['pi_model']} "
          f"({ident['machine']}, python {ident['python']}), capture "
          f"{args.width}x{args.height}, conf {args.conf}, threads {args.threads}, "
          f"tracking {'on' if not args.no_track else 'OFF'}.\n")
    if unreliable:
        print(f"> **THROTTLED DURING THIS RUN — `{thr}`. These numbers are NOT "
              f"reproducible and must not be quoted without this line.**\n")
    if args.mode == "sweep":
        print("| imgsz | FPS p50 | FPS p95 | FPS worst | capture ms | inference ms | "
              "encode ms | det rate | conf p50 |")
        print("|---|---|---|---|---|---|---|---|---|")
        for r in report["runs"]:
            print(f"| {r['imgsz']} | {r['fps_p50']} | {r['fps_p95']} | {r['fps_worst']} "
                  f"| {r['ms_capture_p50']} | {r['ms_inference_p50']} | "
                  f"{r['ms_encode_p50']} | {r['detection_rate']} | {r['conf_p50']} |")
    else:
        s = report["soak"]
        print(f"- Sustained {s['soak_seconds']:.0f} s at imgsz {s['imgsz']}: "
              f"**{s['fps_overall']} FPS overall**, {s['frames']} frames.")
        print(f"- CPU temp {s['cpu_temp_start_c']} C -> {s['cpu_temp_end_c']} C "
              f"(max {s['cpu_temp_max_c']} C). Throttle flag at end: `{s['throttled_end']}`.")
        print(f"- Detection rate {s['detection_rate']} "
              f"({s['frames_with_detection']}/{s['frames']} frames).\n")
        print("| t (s) | CPU temp C | FPS in window |")
        print("|---|---|---|")
        for smp in s["samples"]:
            print(f"| {smp['t_s']} | {smp['cpu_temp_c']} | {smp['fps_window_mean']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
