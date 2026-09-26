"""Bridge to the ml/ detection package.

Keeps every import of the ML code in one place, so the rest of the backend never has to
think about sys.path, ffmpeg settings, or model-file guards.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

_ML_READY = False


def ensure_ml_importable() -> None:
    """Put ml/ on sys.path and point the ML package at our ffmpeg, once."""
    global _ML_READY
    if _ML_READY:
        return
    settings = get_settings()
    root = str(Path(settings.ml_package_root).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    if settings.ffmpeg_bin:
        os.environ.setdefault("FFMPEG_BIN", settings.ffmpeg_bin)
    _ML_READY = True


def sha256_and_size(path: Path) -> tuple[str, int]:
    """Hash and measure a file in one streaming pass."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def probe_video(path: Path) -> dict:
    """Duration / fps / resolution via ffprobe.

    A probe failure must never fail an upload - a file we cannot probe is still worth
    queuing, and we would rather show blanks in the UI than reject the user outright.
    """
    try:
        ensure_ml_importable()
        from detection.data.video_utils import probe_ffprobe

        meta = probe_ffprobe(str(path))
        return {
            "duration": float(meta.duration or 0.0),
            "fps": float(meta.fps or 0.0),
            "resolution": str(meta.resolution or "unknown"),
        }
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        logger.warning("ffprobe failed for %s: %s", path.name, exc)
        return {"duration": 0.0, "fps": 0.0, "resolution": "unknown"}


def analyze_video(
    video_path: Path,
    out_dir: Path,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    """Run the trained 4-head pipeline. Returns AnalysisResult keys plus `_meta`."""
    ensure_ml_importable()
    from detection.pipeline import Analyzer

    return Analyzer().analyze(str(video_path), out_dir=out_dir, on_stage=on_stage)


def weights_status() -> dict:
    """Which checkpoints are present.

    Missing checkpoints are dangerous rather than merely broken: the pipeline silently
    substitutes _PRIORS (~0.10-0.15) and returns confident nonsense with no error.
    """
    settings = get_settings()
    present = [n for n in settings.checkpoint_names if (settings.weights_dir / n).exists()]
    missing = settings.missing_checkpoints()
    return {"weights_dir": str(settings.weights_dir), "present": present, "missing": missing}


def assert_weights_present() -> None:
    missing = get_settings().missing_checkpoints()
    if missing:
        raise RuntimeError(
            "Refusing to start: missing model checkpoints %s in %s. "
            "Without them the pipeline substitutes _PRIORS and returns meaningless "
            "verdicts, so failing here is deliberate."
            % (missing, get_settings().weights_dir)
        )
