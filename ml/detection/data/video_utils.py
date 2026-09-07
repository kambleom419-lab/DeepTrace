"""FFmpeg/OpenCV helpers: metadata, frame sampling, decoding."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# FFmpeg binary: config override > FFMPEG_BIN env > PATH.
_FFMPEG_BIN: str | None = None


def set_ffmpeg_bin(path: str | None) -> None:
    global _FFMPEG_BIN
    _FFMPEG_BIN = path or None


def ffmpeg_bin() -> str:
    import os

    if _FFMPEG_BIN:
        return _FFMPEG_BIN
    env = os.environ.get("FFMPEG_BIN")
    if env:
        return env
    return "ffmpeg"


@dataclass
class VideoMeta:
    path: str
    duration: float  # seconds
    fps: float
    width: int
    height: int
    frame_count: int

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


def probe_ffprobe(video_path: str | Path) -> VideoMeta:
    """Return metadata via ffprobe (fallback to OpenCV if ffprobe missing)."""
    path = str(video_path)
    try:
        import json

        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,avg_frame_rate,duration,nb_frames",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            s = json.loads(out.stdout)["streams"][0]
            w, h = int(s["width"]), int(s["height"])
            num, den = s.get("avg_frame_rate", "0/1").split("/")
            fps = float(num) / float(den) if float(den) else 30.0
            duration = float(s.get("duration", 0) or 0)
            nb = int(s.get("nb_frames", 0) or 0)
            if nb == 0 and duration:
                nb = int(duration * fps)
            return VideoMeta(path, duration, fps, w, h, nb)
    except Exception:
        pass
    # OpenCV fallback
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = n / fps if fps else 0.0
    cap.release()
    return VideoMeta(path, duration, fps, w, h, n)


def extract_frames_at_timestamps(
    video_path: str | Path,
    timestamps: list[float],
    out_dir: str | Path,
    size: int | None = None,
) -> list[dict]:
    """Extract exact frames at given timestamps using FFmpeg.

    Returns list of {timestamp, index, path, frame_number} sorted by timestamp.
    Only the largest face will later be cropped from each returned frame.
    """
    import os

    video_path = str(video_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    for i, ts in enumerate(timestamps):
        out_file = out_dir / f"frame_{i:03d}_t{ts:06.2f}.jpg"
        scale = ["-vf", f"scale={size}:{size}"] if size else []
        cmd = [
            ffmpeg_bin(), "-y", "-v", "error",
            "-ss", f"{ts:.3f}", "-i", video_path,
            "-frames:v", "1", *scale,
            "-q:v", "2", str(out_file),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if out_file.exists() and out_file.stat().st_size > 0:
            results.append({
                "timestamp": round(ts, 3),
                "index": i,
                "path": str(out_file),
                "frame_number": i,
            })
    return results


def sample_timestamps(meta: VideoMeta, n: int) -> list[float]:
    """Evenly sample n timestamps across the video (clamped to [0, duration))."""
    if n <= 0:
        return []
    if meta.duration <= 0:
        return [i * (1.0 / max(n, 1)) for i in range(n)]
    step = meta.duration / n
    return [round(min(i * step, max(meta.duration - 0.01, 0.0)), 3) for i in range(n)]


def read_rgb(path: str | Path) -> np.ndarray:
    """Read an image file as RGB uint8 (H,W,3)."""
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"Cannot read image: {path}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
