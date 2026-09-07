"""Dataset download / prep helpers.

V1 reality: FaceForensics++ requires the authors' download script + Google Drive
access, which is often painful. This module documents the expected layout and
offers a tiny synthetic-clip generator so the pipeline can be smoke-tested with
ZERO external data (python -m detection.data.prepare_synthetic).

Expected dataset layout for training/eval (see datasets.find_videos):
    <root>/real/  <video_id>.mp4 ...
    <root>/fake/  <video_id>.mp4 ...

Suggested public sources (documented, not bundled):
  - FaceForensics++      (per-request download)
  - Celeb-DF v2          (Google Drive)
  - DFDC / WildDeepfake  (Kaggle subsets)
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def generate_synthetic_clips(out_root: str | Path, n_fake: int = 3, n_real: int = 3,
                             seconds: float = 6.0, fps: int = 12, size: int = 256,
                             ffmpeg: str | None = None) -> None:
    """Generate labeled synthetic test clips (a moving face-ish pattern).

    Not real faces — just enough motion for a smoke test of the full pipeline.
    A real dataset replaces these for actual training.
    """
    import os

    ffmpeg = ffmpeg or os.environ.get("FFMPEG_BIN") or "ffmpeg"
    out_root = Path(out_root)
    for label, count in (("real", n_real), ("fake", n_fake)):
        folder = out_root / label
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            path = folder / f"clip_{i:02d}.mp4"
            # moving gradient + noise; fake clips get higher-frequency flicker (a crude
            # stand-in for generation artifacts) so heads have *something* to separate
            hf = "1" if label == "fake" else "0"
            cmd = [
                ffmpeg, "-y", "-v", "error",
                "-f", "lavfi",
                "-i", f"testsrc2=size={size}x{size}:rate={fps}:duration={seconds}",
                "-vf", f"format=yuv420p,noise=alls={8 + 30 * int(hf)}:allf=t",
                "-t", str(seconds), "-r", str(fps), str(path),
            ]
            subprocess.run(cmd, check=True)
            print(f"  wrote {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synthetic")
    ap.add_argument("--n-fake", type=int, default=3)
    ap.add_argument("--n-real", type=int, default=3)
    args = ap.parse_args()
    generate_synthetic_clips(args.out, n_fake=args.n_fake, n_real=args.n_real)


if __name__ == "__main__":
    main()
