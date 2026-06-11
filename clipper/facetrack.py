"""Find where the speaker sits in frame so the 9:16 crop can center on them.

Samples a handful of frames across the clip window and runs OpenCV's Haar
frontal-face detector. Returns the median horizontal face center as a 0..1
fraction of frame width — a single static value, deliberately not per-frame
tracking: a fixed window never jitters, and podcast speakers rarely move.

opencv-python is an optional dependency; without it (or when no face is found
in enough frames) the caller falls back to a plain center crop.
"""
from __future__ import annotations

import logging
import statistics
from pathlib import Path

log = logging.getLogger(__name__)

_MIN_DETECTIONS = 2  # fewer than this across all samples -> not confident


def face_center_fraction(
    video_path: Path, start: float, end: float, samples: int = 7
) -> float | None:
    """Median horizontal center (0..1) of the largest face, or None."""
    try:
        import cv2
    except ImportError:
        log.warning("opencv-python not installed — smart crop falls back to center")
        return None

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        log.warning("OpenCV could not open %s — falling back to center", video_path)
        return None

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    span = max(0.1, end - start)
    centers: list[float] = []
    try:
        for i in range(samples):
            t = start + span * (i + 0.5) / samples
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            scale = 480.0 / w if w > 480 else 1.0
            small = cv2.resize(frame, (int(w * scale), int(h * scale)))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
            if len(faces) == 0:
                continue
            x, _, fw, _ = max(faces, key=lambda f: f[2] * f[3])  # largest face
            centers.append((x + fw / 2.0) / small.shape[1])
    finally:
        cap.release()

    if len(centers) < _MIN_DETECTIONS:
        log.info("Smart crop: %d face detection(s) — not enough, using center", len(centers))
        return None
    frac = statistics.median(centers)
    log.info("Smart crop: speaker at %.0f%% of frame width (%d detections)",
             frac * 100, len(centers))
    return frac
