"""Face detection + alignment.

V1 uses insightface's RetinaFace (ONNX) which returns bbox + 5 landmarks in one
call — no separate landmark model, no training. Falls back to MTCNN-free policy:
if no face is found we return None and the caller skips the frame.

Reference alignment: we use the 5-point (eyes, nose, mouth corners) similarity
transform into a canonical 224x224 crop.
"""
from __future__ import annotations

import cv2
import numpy as np

_FACE_MODEL = None
_FACE_MODEL_NAME = "buffalo_l"

# canonical 5-point template (right eye, left eye, nose, right mouth, left mouth)
# in a 224x224 space — matches insightface's alignment convention.
TEMPLATE = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)


def get_face_model(name: str = _FACE_MODEL_NAME):
    """Lazily load insightface RetinaFace (buffalo_l). Downloads models on first use."""
    global _FACE_MODEL
    if _FACE_MODEL is None:
        import insightface
        from insightface.app import FaceAnalysis

        app = FaceAnalysis(name=name, providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
        # keep just the detector + landmarker; buffalo_l includes more models (rec, genderage)
        _FACE_MODEL = app
    return _FACE_MODEL


def detect_faces(rgb: np.ndarray, model=None):
    """Return list of insightface Face objects (each has .bbox, .kps, .score)."""
    model = model or get_face_model()
    return model.get(rgb)


def align_face(rgb: np.ndarray, kps: np.ndarray, size: int = 224) -> np.ndarray:
    """Similarity-transform an aligned face crop from 5 landmarks.

    kps: (5,2) float32 in the order insightface returns (right eye, left eye,
         nose, right mouth, left mouth).
    """
    # Use cv2 estimateAffinePartial2D (similarity) on the 5-point pairs
    M, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), TEMPLATE, method=cv2.RANSAC)
    if M is None:
        # degenerate landmarks -> fall back to plain center crop
        return cv2.resize(rgb, (size, size))
    aligned = cv2.warpAffine(rgb, M, (size, size), flags=cv2.INTER_LINEAR)
    return aligned


def crop_face(rgb: np.ndarray, face, margin: float = 0.2, size: int = 224) -> np.ndarray | None:
    """Crop bbox with margin and resize to (size,size). Returns None on empty."""
    x1, y1, x2, y2 = [float(v) for v in face.bbox]
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return None
    mx, my = w * margin, h * margin
    x1, y1 = max(0, x1 - mx), max(0, y1 - my)
    x2, y2 = min(rgb.shape[1], x2 + mx), min(rgb.shape[0], y2 + my)
    crop = rgb[int(y1):int(y2), int(x1):int(x2)]
    if crop.size == 0:
        return None
    return cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)


def largest_face(faces) -> object | None:
    """Pick the largest (by bbox area) detected face."""
    if not faces:
        return None
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def align_largest_face(
    rgb: np.ndarray, size: int = 224, min_score: float = 0.5, model=None,
) -> tuple[np.ndarray | None, object | None]:
    """Detect + align the largest face. Returns (crop_or_None, face_or_None).

    ``face.score`` is None for some model packs (buffalo_l) — treat a missing
    score as "accept" rather than crashing.
    """
    faces = detect_faces(rgb, model)
    face = largest_face(faces)
    if face is None:
        return None, None
    score = getattr(face, "score", None)
    if score is not None and float(score) < min_score:
        return None, None
    try:
        crop = align_face(rgb, face.kps.astype(np.float32), size=size)
        return crop, face
    except Exception:
        return crop_face(rgb, face, size=size), face
