"""
eo_detector.py — the OBSERVED half of criterion 1. Lane B (EO + Geometry).

WHAT THIS FILE ANSWERS
    "What does the camera see, and on what bearing?"

It answers nothing else. It does NOT answer "what kind of ship is that", and it does
NOT answer "is that vessel lying". Those belong to the VLM and to lane C.

THE BOUNDARY THIS FILE DEFENDS
contracts.py puts a hard wall between CLAIMED and OBSERVED. This module sits entirely
on the OBSERVED side: it never reads an MMSI, never opens the AIS slice, and EoContact
has no identity field for it to fill even if it wanted one. That is deliberate. If the
detector could see identity, criterion 3 — catching a vessel that BROADCASTS a false
identity — would have nothing left to compare against.

WHY COCO 'boat', AND WHAT THAT COSTS
CLAUDE.md: start with COCO 'boat', do not train unless it demonstrably fails.
COCO has exactly one maritime class, so the detector:
    CAN say     "there is a vessel at these pixels, confidence 0.83"
    CANNOT say  "that is a tanker rather than a cargo ship"
Therefore this module emits observed_class = None. Always. Not "unknown" — None,
which contracts.py defines as "not available". Emitting a class we did not observe
would hand consistency.py a fabricated claimed-vs-observed comparison, and the SPOOF
verdict that followed would be about our bug, not about a ship.

The detector's own confidence goes in EoContact.detection_confidence, which is a
DIFFERENT field from observed_class_confidence on purpose:
    detection_confidence      = how sure are we this box IS A VESSEL      (YOLO knows)
    observed_class_confidence = how sure are we WHICH CLASS it is         (YOLO cannot)
Collapsing those two floats into one is a silent mis-calibration: no exception, just a
DARK confidence built on a number that does not mean what its field name says.
See the 2026-08-28 request block in 99_scratch/requests.md.

WHAT STAYS None, AND WHY THAT IS THE POINT
    observed_range_m           monocular range needs a horizon line or a size prior
    observed_length_m          metres = range x angular width; no range, no length
    observed_heading_deg_true  needs a stable multi-frame track AND a range
    observed_speed_ms          same
A monocular length with no error bar is precisely the number that produces a confident
SPOOF verdict about the wrong hull. contracts.py says None means "not available".
This module means it, and geometry.py will fill these in when it exists.

THE INFERENCE CHAIN THIS MODULE CONTRIBUTES
    OBSERVED : a box of pixels (x1,y1,x2,y2) in frame F at time T
    ->  BEARING : pinhole projection of the box centre through a stated camera pose
    ->  CONFIDENCE : pose yaw error and box-centroid error combined in quadrature
    ->  WHAT WOULD CHANGE THE ANSWER : a wrong yaw in the pose file rotates EVERY
        bearing by the same amount. So a systematic association failure across ALL
        contacts is a pose bug, not a detector bug — check camera_pose_ref first.
        A failure on only the distant contacts is an optics/pixel-extent problem.

RELOCATION NOTE
CameraPose and the pinhole bearing helpers live here for now. They belong in
geometry.py once that file exists (same lane, no ownership conflict). They are here
rather than there because EoContact.observed_bearing_deg_true is a REQUIRED field —
this module cannot emit a legal contact without them, and writing geometry.py ahead of
its own task would be speculative.

IMPORT CONVENTION
Flat imports with 03_src on sys.path — see LIBRARIES.md. `03_src` begins with a digit
and is not a legal package name. Do not add a sys.path hack in this module; run_demo.py
and tests/conftest.py already do it.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
from ultralytics import YOLO

from contracts import EoContact

# --------------------------------------------------------------------------------
# Constants. Every assumed (as opposed to measured) number is named here rather than
# buried in an expression, so that it is inspectable and so a calibration run can
# replace it without a code search.
# --------------------------------------------------------------------------------

MODULE_VERSION = "eo_detector/0.1.0"

# COCO's single maritime class. Looked up BY NAME at load time rather than hardcoded
# as index 8, because a different weights file may order its classes differently and a
# silently wrong class filter yields "zero vessels detected" with no error.
COCO_BOAT_NAME = "boat"

# ASSUMED, NOT MEASURED. 1-sigma error in locating a bounding box's horizontal centre,
# as a fraction of the box's own width. A wide box on a nearby hull is less precisely
# centred in absolute pixels than a tight box on a distant one, which is why this is a
# fraction and not a constant pixel count. This value is an input to every bearing
# uncertainty, therefore to every position-mismatch significance, therefore to every
# SPOOF confidence. It belongs in EvidenceRecord.limitations until a calibration run
# replaces it.
BBOX_CENTROID_SIGMA_FRACTION = 0.10
BBOX_CENTROID_SIGMA_FLOOR_PX = 1.0

DEFAULT_WEIGHTS = "yolov8n.pt"
DEFAULT_DEVICE = "mps"
DEFAULT_IMGSZ = 640
DEFAULT_CONF = 0.25
DEFAULT_TRACKER = "bytetrack.yaml"

# Frames excluded from the timing statistics. The first MPS inference includes Metal
# shader compilation and weight upload; counting it reports a throughput figure that
# no subsequent frame will ever see again.
WARMUP_FRAMES = 5

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


# --------------------------------------------------------------------------------
# Camera pose. Not a contracts.py type: EoContact carries only camera_pose_ref, a
# string. The pose itself is lane B's internal state, written to the run manifest so
# that the ref resolves to something a judge can inspect. A bearing is only as
# trustworthy as the pose that produced it, and criterion 4 means the pose must be
# recoverable after the fact.
# --------------------------------------------------------------------------------

@dataclass(frozen=True)
class CameraPose:
    """Where the camera was and which way it faced. Frozen: a pose that can change
    after the frames were captured is not evidence."""

    pose_ref: str                  # goes into EoContact.camera_pose_ref verbatim
    yaw_deg_true: float            # boresight azimuth, degrees TRUE, 0-360 clockwise
    hfov_deg: float                # horizontal field of view of the full frame
    yaw_uncertainty_deg: float     # 1-sigma error on yaw_deg_true

    # Position is not used by this module, but without it the pose cannot later
    # produce a range or a cable-proximity check, and camera_pose_ref would point at
    # an incomplete record. Optional so a pure throughput benchmark need not lie
    # about where it stood.
    lat_deg: float | None = None
    lon_deg: float | None = None
    height_m: float | None = None
    note: str | None = None

    @staticmethod
    def from_json_file(path: str | Path) -> "CameraPose":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return CameraPose(**raw)


def uncalibrated_benchmark_pose(hfov_deg: float) -> CameraPose:
    """A pose for measuring throughput when no surveyed pose exists yet.

    yaw_uncertainty_deg is 180.0 ON PURPOSE. That makes every bearing this run
    produces explicitly worthless — a 1-sigma of 180 degrees cannot support any
    position mismatch at any significance — so the run is usable for FPS and
    detection counts and structurally unusable as evidence. The alternative, a
    plausible-looking default of 2 degrees, is how an uncalibrated bearing reaches a
    verdict without anyone noticing.
    """
    return CameraPose(
        pose_ref="UNCALIBRATED-BENCHMARK",
        yaw_deg_true=0.0,
        hfov_deg=hfov_deg,
        yaw_uncertainty_deg=180.0,
        note="Throughput measurement only. Bearings from this pose are not evidence.",
    )


# --------------------------------------------------------------------------------
# Pinhole bearing maths.
#
# LIBRARY-FIRST justification (LIBRARIES.md requires one line): this is single-axis
# rectilinear projection, one atan2 call. pyproj.Geod solves geodesy between two
# geographic points, which is a different problem; no library in the register covers
# pixel-to-angle for a camera. Written here, deliberately, in four lines.
# --------------------------------------------------------------------------------

def focal_length_px(image_width_px: int, hfov_deg: float) -> float:
    """Focal length in pixels implied by a frame width and a horizontal FOV."""
    return (image_width_px / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def relative_bearing_deg(centre_x_px: float, image_width_px: int, hfov_deg: float) -> float:
    """Signed angle from boresight to a pixel column. Positive to the right.

    WHY atan2 AND NOT LINEAR INTERPOLATION ACROSS THE FRAME: a rectilinear lens does
    not map angle linearly onto pixels. On a wide lens (a MacBook camera is wide) the
    linear approximation is several degrees wrong at the frame edges, and it is wrong
    in a direction that fakes a consistent bearing bias — which reads downstream as a
    position spoof rather than as our own arithmetic.
    """
    dx = centre_x_px - (image_width_px / 2.0)
    return math.degrees(math.atan2(dx, focal_length_px(image_width_px, hfov_deg)))


def true_bearing_deg(yaw_deg_true: float, rel_deg: float) -> float:
    """Boresight azimuth plus relative bearing, wrapped into contracts.py's 0-360."""
    return (yaw_deg_true + rel_deg) % 360.0


def bearing_uncertainty_deg(
    bbox_width_px: float,
    centre_x_px: float,
    image_width_px: int,
    hfov_deg: float,
    yaw_uncertainty_deg: float,
) -> float:
    """1-sigma half-width on observed_bearing_deg_true.

    Two independent error sources, combined in quadrature because they are
    independent: they do not simply add.

      1. POSE. Yaw error rotates every bearing in the run by the same amount. It is
         usually the dominant term and it is systematic, not random.
      2. CENTROID. Where inside the box the hull's centre actually is. Converted from
         pixels to degrees using the LOCAL angular scale d(theta)/dx = f/(f^2+dx^2),
         not the frame-average scale — a pixel near the frame edge subtends less
         angle than a pixel at the centre, and using the average would overstate the
         precision of centre contacts and understate that of edge contacts.

    Without this number a position mismatch is meaningless: contracts.py scores
    significance in sigmas, and a sigma of zero makes every delta infinitely
    significant.
    """
    f = focal_length_px(image_width_px, hfov_deg)
    dx = centre_x_px - (image_width_px / 2.0)
    deg_per_px_here = math.degrees(f / (f * f + dx * dx))

    centroid_sigma_px = max(
        BBOX_CENTROID_SIGMA_FRACTION * bbox_width_px, BBOX_CENTROID_SIGMA_FLOOR_PX
    )
    centroid_sigma_deg = centroid_sigma_px * deg_per_px_here

    return math.sqrt(yaw_uncertainty_deg ** 2 + centroid_sigma_deg ** 2)


# --------------------------------------------------------------------------------
# Pending-contract guard.
#
# eo_detector is written against EoContact.detection_confidence, requested from ARCH
# on 2026-08-28 (99_scratch/requests.md). Until that field lands, extra="forbid"
# rejects the kwarg with a pydantic ValidationError that names a field nobody has
# heard of. This turns that into one sentence naming the request. It is a fail-loud on
# a KNOWN pending dependency, not speculative code, and it deletes itself the moment
# the field exists.
# --------------------------------------------------------------------------------

def _require_detection_confidence_field() -> None:
    if "detection_confidence" not in EoContact.model_fields:
        raise RuntimeError(
            "EoContact has no 'detection_confidence' field. Lane B's request to ARCH "
            "dated 2026-08-28 in 99_scratch/requests.md has not been applied to "
            "03_src/contracts.py yet. eo_detector cannot emit a contact until it "
            "lands: YOLO's vessel-vs-not-vessel confidence has nowhere legal to go, "
            "and observed_class_confidence is a different quantity."
        )


# --------------------------------------------------------------------------------
# Frame sources: MacBook camera, video file, image folder.
#
# WHY A SOURCE LAYER RATHER THAN model.predict(source=<path>): EoContact requires
# frame_time_utc (timezone-aware) and frame_ref. Ultralytics' internal loader supplies
# neither. Worse, for a RECORDED clip the frame time must come from the clip's own
# start time plus its position within the clip — NOT from the wall clock. T1 replays
# an August-25 AIS slice; stamping today's wall clock onto those frames puts every
# observation three days away from every claim, and association.py then finds nothing
# and reports a sea full of dark vessels. That is the date landmine from
# FILE_OWNERSHIP.md wearing a different hat.
# --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    index: int
    image_bgr: np.ndarray
    frame_ref: str      # goes into EoContact.frame_ref: points back at the evidence
    time_utc: datetime  # timezone-aware UTC, always


def _iter_camera(device_index: int, max_frames: int | None) -> Iterator[Frame]:
    """Live capture. CAP_AVFOUNDATION is named explicitly because it is the backend
    measured working on this Mac (LIBRARIES.md, 2026-08-27); letting OpenCV choose
    can land on a backend that opens the device and then returns empty frames.

    Frame time is the wall clock at grab time, which for a live feed is correct.
    """
    cap = cv2.VideoCapture(device_index, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {device_index} via AVFoundation. On macOS "
            "the first capture from a terminal raises a TCC permission prompt — if "
            "you saw no prompt, grant Camera access to your terminal in System "
            "Settings > Privacy & Security > Camera. This is a permissions failure, "
            "not a code failure."
        )
    try:
        index = 0
        while max_frames is None or index < max_frames:
            ok, image = cap.read()
            if not ok:
                break
            # Timestamp taken immediately after the grab returns, so the value is as
            # close to the exposure as this API allows.
            yield Frame(index, image, f"camera:{device_index}#{index:06d}",
                        datetime.now(timezone.utc))
            index += 1
    finally:
        cap.release()


def _iter_video(path: Path, clip_start_utc: datetime, max_frames: int | None) -> Iterator[Frame]:
    """Recorded clip. Frame time = clip_start_utc + position within the clip."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    try:
        index = 0
        while max_frames is None or index < max_frames:
            # Read the position BEFORE the grab: CAP_PROP_POS_MSEC reports the
            # timestamp of the frame that read() is about to return. Reading it after
            # gives you the NEXT frame's timestamp, a one-frame skew that is invisible
            # at 30 fps and matters the moment anyone resamples.
            pos_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            ok, image = cap.read()
            if not ok:
                break
            # Some codec/container combinations report 0.0 for every frame. Fall back
            # to index/fps, and say so rather than silently stamping every frame with
            # the clip start.
            if pos_msec <= 0.0 and index > 0:
                if fps <= 0.0:
                    raise RuntimeError(
                        f"{path} reports neither frame timestamps nor a frame rate. "
                        "Re-encode the clip (ffmpeg -i in.mov -c:v libx264 -r 30 "
                        "out.mp4) rather than guessing frame times."
                    )
                pos_msec = (index / fps) * 1000.0
            yield Frame(index, image, f"{path.name}#{index:06d}",
                        clip_start_utc + timedelta(milliseconds=pos_msec))
            index += 1
    finally:
        cap.release()


def _iter_image_dir(path: Path, max_frames: int | None) -> Iterator[Frame]:
    """Still frames. Frame time is each file's modification time.

    That is a real recorded time rather than a guess, but it survives a copy poorly,
    so the run manifest records the time source as file_mtime and the caller should
    carry it into EvidenceRecord.limitations.
    """
    files = sorted(p for p in path.iterdir()
                   if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file())
    if not files:
        raise RuntimeError(f"No images with extensions {IMAGE_EXTENSIONS} in {path}")
    for index, file_path in enumerate(files):
        if max_frames is not None and index >= max_frames:
            break
        image = cv2.imread(str(file_path))
        if image is None:
            raise RuntimeError(f"cv2 could not decode {file_path}")
        yield Frame(index, image, str(file_path),
                    datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc))


def open_source(
    source_spec: str,
    clip_start_utc: datetime | None,
    max_frames: int | None,
) -> tuple[Iterator[Frame], str, bool]:
    """Dispatch on the source spec.

    Returns (frame iterator, frame_time_source label, tracking_is_meaningful).

    tracking_is_meaningful is False for an image folder: ByteTrack associates across
    CONSECUTIVE frames, and an arbitrary folder of stills has no temporal continuity.
    Running a tracker over it invents track identities that link unrelated pictures,
    and track_length_frames then lies to lane C about how much motion evidence exists.
    """
    if source_spec == "camera" or source_spec.startswith("camera:"):
        device_index = int(source_spec.split(":", 1)[1]) if ":" in source_spec else 0
        return _iter_camera(device_index, max_frames), "wall_clock_utc", True

    path = Path(source_spec).expanduser()
    if path.is_dir():
        return _iter_image_dir(path, max_frames), "file_mtime", False
    if path.is_file():
        if clip_start_utc is None:
            raise RuntimeError(
                "A recorded clip needs --clip-start-utc (ISO-8601 WITH an offset, "
                "e.g. 2026-08-28T18:20:00+02:00). Refusing to guess: stamping wall "
                "clock onto recorded frames puts every observation days away from "
                "the AIS claims it must be matched against, and the pipeline then "
                "reports a sea full of dark vessels."
            )
        return _iter_video(path, clip_start_utc, max_frames), "clip_start+pos_msec", True

    raise RuntimeError(f"Source is neither 'camera', a file, nor a directory: {source_spec}")


# --------------------------------------------------------------------------------
# The detector.
# --------------------------------------------------------------------------------

class EoDetector:
    """Wraps ultralytics and emits contract-shaped EoContacts. Holds exactly one piece
    of mutable state — the per-track frame counter — because track_length_frames is a
    count over history and cannot be derived from a single frame."""

    def __init__(
        self,
        pose: CameraPose,
        run_id: str,
        weights: str = DEFAULT_WEIGHTS,
        device: str = DEFAULT_DEVICE,
        imgsz: int = DEFAULT_IMGSZ,
        conf: float = DEFAULT_CONF,
        tracker: str = DEFAULT_TRACKER,
    ) -> None:
        _require_detection_confidence_field()

        self.pose = pose
        self.run_id = run_id
        self.device = device
        self.imgsz = imgsz
        self.conf = conf
        self.tracker = tracker

        self.model = YOLO(weights)
        self.boat_class_index = self._resolve_boat_class_index()

        # track id -> how many frames we have now seen it in.
        self._track_frame_counts: dict[int, int] = {}

    def _resolve_boat_class_index(self) -> int:
        """Find COCO 'boat' by NAME, not by its index of 8.

        A weights file with a different class order would make a hardcoded 8 filter on
        something else entirely, and the symptom is "the detector finds no vessels" —
        a result indistinguishable from a genuinely empty sea. Failing loudly here
        costs one dict scan and removes a whole category of 03:00 debugging.
        """
        names: dict[int, str] = self.model.names
        for index, name in names.items():
            if str(name).strip().lower() == COCO_BOAT_NAME:
                return int(index)
        raise RuntimeError(
            f"No class named '{COCO_BOAT_NAME}' in these weights. Classes present: "
            f"{sorted(str(n) for n in names.values())}. These are not COCO weights; "
            "either point --weights at yolov8n.pt or update the class mapping."
        )

    def process_frame(self, frame: Frame, use_tracking: bool) -> tuple[list[EoContact], float]:
        """One frame in, contacts plus the inference time in milliseconds out.

        Inference time is measured around the model call ONLY. Decode, colour
        conversion and disk I/O are timed separately by the caller, because a figure
        that mixes them answers "how fast is my SSD" rather than "how fast is the
        detector", and the two diverge wildly between a 4K clip and a 1080p one.
        """
        started = time.perf_counter()
        if use_tracking:
            # persist=True carries tracker state across successive calls — the
            # documented pattern for feeding frames one at a time. Without it every
            # frame starts a fresh tracker and every contact is one frame old.
            results = self.model.track(
                frame.image_bgr, persist=True, tracker=self.tracker,
                device=self.device, imgsz=self.imgsz, conf=self.conf,
                classes=[self.boat_class_index], verbose=False,
            )
        else:
            results = self.model.predict(
                frame.image_bgr, device=self.device, imgsz=self.imgsz, conf=self.conf,
                classes=[self.boat_class_index], verbose=False,
            )
        inference_ms = (time.perf_counter() - started) * 1000.0

        result = results[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return [], inference_ms

        image_height_px, image_width_px = frame.image_bgr.shape[:2]
        # boxes.id is None when the tracker produced no ids for this frame (first
        # frame, or a detection it has not yet confirmed). is_track is ultralytics'
        # own predicate for that, so we ask it rather than inferring.
        has_ids = bool(getattr(boxes, "is_track", False)) and boxes.id is not None

        contacts: list[EoContact] = []
        for i in range(len(boxes)):
            x1, y1, x2, y2 = (float(v) for v in boxes.xyxy[i].tolist())

            # Clamp to the frame. Detectors routinely return boxes a few pixels
            # outside the image on partially-visible objects. An x2 beyond the frame
            # width drags the computed centroid outward, which biases the bearing
            # toward the edge of the field of view — a systematic error that reads
            # downstream as a position mismatch rather than as our arithmetic.
            x1 = max(0.0, min(x1, image_width_px - 1.0))
            x2 = max(0.0, min(x2, image_width_px - 1.0))
            y1 = max(0.0, min(y1, image_height_px - 1.0))
            y2 = max(0.0, min(y2, image_height_px - 1.0))

            centre_x_px = (x1 + x2) / 2.0
            bbox_width_px = max(x2 - x1, 1.0)

            rel_deg = relative_bearing_deg(centre_x_px, image_width_px, self.pose.hfov_deg)
            contacts.append(EoContact(
                contact_id=self._contact_id(frame, i, boxes, has_ids),
                frame_time_utc=frame.time_utc,
                frame_ref=frame.frame_ref,
                bbox_px=(int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))),

                observed_bearing_deg_true=true_bearing_deg(self.pose.yaw_deg_true, rel_deg),
                bearing_uncertainty_deg=bearing_uncertainty_deg(
                    bbox_width_px, centre_x_px, image_width_px,
                    self.pose.hfov_deg, self.pose.yaw_uncertainty_deg,
                ),
                observed_bearing_rel_deg=rel_deg,

                detection_confidence=float(boxes.conf[i]),

                # Deliberate Nones, spelled out rather than defaulted so that the
                # reader sees they are a decision and not an oversight:
                observed_class=None,             # COCO has one maritime class; the VLM
                observed_class_confidence=None,  # answers class, this detector cannot
                observed_range_m=None,           # monocular range not implemented yet
                range_uncertainty_m=None,        # so no range, and no fake error bar
                observed_length_m=None,          # metres = range x angular width
                observed_length_uncertainty_m=None,
                observed_heading_deg_true=None,  # needs a track AND a range
                observed_speed_ms=None,          # same

                track_length_frames=self._track_length(boxes, i, has_ids),
                camera_pose_ref=self.pose.pose_ref,
            ))
        return contacts, inference_ms

    def _contact_id(self, frame: Frame, i: int, boxes, has_ids: bool) -> str:
        """Run-stable and unique. Carries the track id when there is one, so a human
        reading two contact_ids can see at a glance whether they are the same hull."""
        if has_ids:
            return f"{self.run_id}-t{int(boxes.id[i]):04d}-f{frame.index:06d}"
        return f"{self.run_id}-f{frame.index:06d}-d{i:02d}"

    def _track_length(self, boxes, i: int, has_ids: bool) -> int:
        """How many frames this hull has been observed for.

        1 when there is no track id. contracts.py: a single-frame contact cannot
        support any motion-derived mismatch, so lane C must be able to see that no
        motion evidence exists rather than assume it does.
        """
        if not has_ids:
            return 1
        track_id = int(boxes.id[i])
        self._track_frame_counts[track_id] = self._track_frame_counts.get(track_id, 0) + 1
        return self._track_frame_counts[track_id]


# --------------------------------------------------------------------------------
# Run orchestration and measured output.
# --------------------------------------------------------------------------------

@dataclass
class RunResult:
    contacts: list[EoContact]
    frames_processed: int
    frames_with_detection: int
    detections_total: int
    inference_ms: list[float]     # post-warmup only
    end_to_end_ms: list[float]    # post-warmup only, includes decode
    frame_time_source: str
    source_spec: str
    image_size_px: tuple[int, int] | None
    tracking_used: bool

    def _median(self, values: list[float]) -> float | None:
        return statistics.median(values) if values else None

    @property
    def inference_fps(self) -> float | None:
        """Detector throughput: what the model could sustain if frames were free."""
        m = self._median(self.inference_ms)
        return (1000.0 / m) if m else None

    @property
    def end_to_end_fps(self) -> float | None:
        """What the pipeline actually achieves, decode included. THIS is the number
        that decides whether the live Elbe demo keeps up; inference_fps is the number
        that tells you whether the model or the I/O is the thing to fix."""
        m = self._median(self.end_to_end_ms)
        return (1000.0 / m) if m else None

    @property
    def detection_rate(self) -> float | None:
        """Fraction of frames containing at least one vessel. On a clip of open water
        with known traffic this is the honest recall proxy: a low rate with vessels
        visibly present is the distant-vessel/pixel-extent failure, not a speed
        problem."""
        if self.frames_processed == 0:
            return None
        return self.frames_with_detection / self.frames_processed


def run(
    source_spec: str,
    pose: CameraPose,
    run_id: str | None = None,
    weights: str = DEFAULT_WEIGHTS,
    device: str = DEFAULT_DEVICE,
    imgsz: int = DEFAULT_IMGSZ,
    conf: float = DEFAULT_CONF,
    tracker: str = DEFAULT_TRACKER,
    clip_start_utc: datetime | None = None,
    max_frames: int | None = None,
    out_dir: str | Path | None = None,
    force_no_tracking: bool = False,
) -> RunResult:
    """Detect over a whole source and, if out_dir is given, write the evidence trail."""
    run_id = run_id or f"eo{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    frames, frame_time_source, tracking_ok = open_source(source_spec, clip_start_utc, max_frames)
    use_tracking = tracking_ok and not force_no_tracking

    detector = EoDetector(pose, run_id, weights, device, imgsz, conf, tracker)

    contacts: list[EoContact] = []
    inference_ms: list[float] = []
    end_to_end_ms: list[float] = []
    frames_processed = 0
    frames_with_detection = 0
    image_size_px: tuple[int, int] | None = None

    for frame in frames:
        loop_started = time.perf_counter()
        frame_contacts, frame_inference_ms = detector.process_frame(frame, use_tracking)
        loop_ms = (time.perf_counter() - loop_started) * 1000.0

        if image_size_px is None:
            height, width = frame.image_bgr.shape[:2]
            image_size_px = (width, height)

        # Warmup frames are processed but not timed: the first MPS inference includes
        # Metal shader compilation, which no later frame pays for. Their DETECTIONS
        # still count — the model's output is correct from frame zero, only its timing
        # is unrepresentative.
        if frames_processed >= WARMUP_FRAMES:
            inference_ms.append(frame_inference_ms)
            end_to_end_ms.append(loop_ms)

        contacts.extend(frame_contacts)
        frames_processed += 1
        if frame_contacts:
            frames_with_detection += 1

    result = RunResult(
        contacts=contacts,
        frames_processed=frames_processed,
        frames_with_detection=frames_with_detection,
        detections_total=len(contacts),
        inference_ms=inference_ms,
        end_to_end_ms=end_to_end_ms,
        frame_time_source=frame_time_source,
        source_spec=source_spec,
        image_size_px=image_size_px,
        tracking_used=use_tracking,
    )

    if out_dir is not None:
        _write_outputs(Path(out_dir), run_id, result, pose, detector, weights)
    return result


def _write_outputs(
    out_dir: Path, run_id: str, result: RunResult, pose: CameraPose,
    detector: EoDetector, weights: str,
) -> None:
    """Contacts as JSONL plus a run manifest.

    WHY A MANIFEST: EoContact.camera_pose_ref is a string. Criterion 4 says evidence
    must be reconstructable after the fact, so the ref has to resolve to the actual
    pose, model, thresholds and time source that produced these bearings. A bearing
    whose pose cannot be recovered is an assertion, not evidence.

    JSONL rather than one JSON array so that a long run is appendable and a truncated
    file still parses line by line — a killed live capture should not lose everything
    it had already seen.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    contacts_path = out_dir / f"{run_id}_contacts.jsonl"
    with open(contacts_path, "w", encoding="utf-8") as fh:
        for contact in result.contacts:
            fh.write(contact.model_dump_json() + "\n")

    manifest = {
        "run_id": run_id,
        "module_version": MODULE_VERSION,
        "written_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_spec": result.source_spec,
        "frame_time_source": result.frame_time_source,
        "camera_pose": asdict(pose),
        "model": {
            "weights": weights,
            "device": detector.device,
            "imgsz": detector.imgsz,
            "conf_threshold": detector.conf,
            "class_filter_index": detector.boat_class_index,
            "class_filter_name": COCO_BOAT_NAME,
            "tracker": detector.tracker if result.tracking_used else None,
            "tracking_used": result.tracking_used,
        },
        "counts": {
            "frames_processed": result.frames_processed,
            "frames_with_detection": result.frames_with_detection,
            "detections_total": result.detections_total,
            "detection_rate": result.detection_rate,
        },
        "timing": {
            "warmup_frames_excluded": WARMUP_FRAMES,
            "timed_frames": len(result.inference_ms),
            "median_inference_ms": result._median(result.inference_ms),
            "median_end_to_end_ms": result._median(result.end_to_end_ms),
            "inference_fps": result.inference_fps,
            "end_to_end_fps": result.end_to_end_fps,
        },
        "limitations": [
            "observed_class is None: COCO has one maritime class and cannot "
            "discriminate vessel type. Class comes from the VLM, not from here.",
            "observed_range_m, observed_length_m, observed_heading_deg_true and "
            "observed_speed_ms are None: monocular range is not implemented.",
            f"Bearing centroid error assumes sigma = {BBOX_CENTROID_SIGMA_FRACTION} "
            "x bbox width. ASSUMED, not calibrated.",
            f"Frame times derive from {result.frame_time_source}.",
        ] + ([
            "Camera pose is UNCALIBRATED (yaw_uncertainty_deg=180). Bearings from "
            "this run are throughput measurement only and are not evidence."
        ] if pose.pose_ref == "UNCALIBRATED-BENCHMARK" else []),
    }
    with open(out_dir / f"{run_id}_manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)


# --------------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------------

def _parse_aware_utc(text: str) -> datetime:
    """ISO-8601 with an explicit offset. Naive input is refused, not assumed to be
    UTC — contracts.py rejects naive datetimes at the boundary precisely because a
    silently-assumed timezone is how a two-hour error reaches a verdict."""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"'{text}' has no UTC offset. Write it as 2026-08-28T18:20:00+02:00 "
            "(Hamburg is UTC+2 in August) or 2026-08-28T16:20:00Z."
        )
    return parsed.astimezone(timezone.utc)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eo_detector.py",
        description="Lane B: EO frames in, contract-shaped EoContacts out.",
    )
    parser.add_argument("--source", required=True,
                        help="'camera', 'camera:N', a video file, or a directory of images.")
    parser.add_argument("--pose-file", default=None,
                        help="JSON file of CameraPose fields. Required for anything "
                             "whose bearings will be used as evidence.")
    parser.add_argument("--hfov-deg", type=float, default=None,
                        help="Horizontal field of view, used to build an UNCALIBRATED "
                             "benchmark pose when --pose-file is absent. Bearings from "
                             "such a run are explicitly not evidence.")
    parser.add_argument("--clip-start-utc", type=_parse_aware_utc, default=None,
                        help="UTC start time of a recorded clip, ISO-8601 with offset.")
    parser.add_argument("--weights", default=DEFAULT_WEIGHTS)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--imgsz", type=int, default=DEFAULT_IMGSZ)
    parser.add_argument("--conf", type=float, default=DEFAULT_CONF)
    parser.add_argument("--tracker", default=DEFAULT_TRACKER)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--no-track", action="store_true",
                        help="Detect per frame without ByteTrack. track_length_frames "
                             "then reports 1 for every contact.")
    parser.add_argument("--out-dir", default=None,
                        help="Write <run>_contacts.jsonl and <run>_manifest.json here.")
    parser.add_argument("--benchmark", action="store_true",
                        help="Print measured FPS and detection counts as PASS/FAIL lines.")
    return parser


def _resolve_pose(args: argparse.Namespace) -> CameraPose:
    if args.pose_file:
        return CameraPose.from_json_file(args.pose_file)
    if args.hfov_deg is None:
        raise SystemExit(
            "ERROR: give --pose-file (for evidence) or --hfov-deg (for a throughput "
            "benchmark). There is no default field of view: guessing one silently "
            "scales every bearing in the run."
        )
    return uncalibrated_benchmark_pose(args.hfov_deg)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    pose = _resolve_pose(args)

    result = run(
        source_spec=args.source, pose=pose, weights=args.weights, device=args.device,
        imgsz=args.imgsz, conf=args.conf, tracker=args.tracker,
        clip_start_utc=args.clip_start_utc, max_frames=args.max_frames,
        out_dir=args.out_dir, force_no_tracking=args.no_track,
    )

    if args.benchmark:
        _print_benchmark(result, args, pose)
    else:
        print(f"{result.detections_total} contacts over {result.frames_processed} frames")
    return 0


def _print_benchmark(result: RunResult, args: argparse.Namespace, pose: CameraPose) -> None:
    """Self-interpreting output: every line says what it means, so the numbers can be
    pasted back into STATUS.md without a second round trip."""
    def line(ok: bool, label: str, value: str) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label:<34} {value}")

    print("\n=== eo_detector benchmark — MEASURED ON THIS MACHINE, THIS CLIP ===")
    print(f"source              : {result.source_spec}")
    print(f"frame time source   : {result.frame_time_source}")
    print(f"camera pose         : {pose.pose_ref} (yaw sigma {pose.yaw_uncertainty_deg} deg)")
    print(f"model               : {args.weights} @ {args.imgsz}px, device={args.device}, conf={args.conf}")
    print(f"tracking            : {args.tracker if result.tracking_used else 'OFF'}")
    if result.image_size_px:
        print(f"frame size          : {result.image_size_px[0]}x{result.image_size_px[1]}")
    print(f"warmup excluded     : {WARMUP_FRAMES} frames; {len(result.inference_ms)} frames timed")
    print("-" * 66)

    line(result.frames_processed > 0, "frames processed", str(result.frames_processed))

    if result.inference_fps:
        line(True, "inference only",
             f"{result.inference_fps:.1f} FPS ({statistics.median(result.inference_ms):.1f} ms/frame)")
    else:
        line(False, "inference only", "no timed frames — clip shorter than warmup")

    if result.end_to_end_fps:
        # 25 FPS is the honest live-video floor: below it the Elbe feed drops frames
        # and the tracker starts losing hulls between updates.
        line(result.end_to_end_fps >= 25.0, "end-to-end incl. decode",
             f"{result.end_to_end_fps:.1f} FPS ({statistics.median(result.end_to_end_ms):.1f} ms/frame)")
    else:
        line(False, "end-to-end incl. decode", "no timed frames")

    line(result.detections_total > 0, "vessel detections total", str(result.detections_total))
    line(result.frames_with_detection > 0, "frames with >=1 detection",
         f"{result.frames_with_detection} of {result.frames_processed}"
         + (f" ({result.detection_rate:.1%})" if result.detection_rate is not None else ""))

    if result.contacts:
        confs = [c.detection_confidence for c in result.contacts]
        widths = [c.bbox_px[2] - c.bbox_px[0] for c in result.contacts]
        print(f"      detection confidence  min {min(confs):.2f} / median "
              f"{statistics.median(confs):.2f} / max {max(confs):.2f}")
        # Pixel extent is the discriminator for the distant-vessel failure. A median
        # box width under ~30 px means COCO 'boat' is operating below the scale it was
        # trained at, and the fix is optics or imgsz, not a different model.
        print(f"      bbox width px         min {min(widths)} / median "
              f"{int(statistics.median(widths))} / max {max(widths)}")
        if statistics.median(widths) < 30:
            print("      NOTE: median box under 30 px. Distant vessels are near the "
                  "limit of COCO 'boat'. Raise --imgsz or use longer optics before "
                  "concluding the model failed.")
    else:
        print("      NO DETECTIONS. Before retraining, check in this order: "
              "(1) were vessels actually in frame, (2) is the median vessel more than "
              "~20 px wide, (3) does --conf 0.10 find them.")
    print("=" * 66 + "\n")


if __name__ == "__main__":
    sys.exit(main())
