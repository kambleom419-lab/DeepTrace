"""Dataset preparation + PyTorch Datasets.

Two consumption modes:
  A) Raw-video mode (used by train.py --stage all when crops don't exist yet):
     extract aligned face crops per video into scratch/ once, then train heads.
  B) Pre-crop mode: Datasets read existing {video_id}__{frame}.jpg files.

The V1 data contract (matching FF++ / Celeb-DF / DFDC layout after prep):
    root/
      real/     <video_id>.mp4
      fake/     <video_id>.mp4
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from detection.config.paths import DATA_DIR, SCRATCH_DIR


@dataclass
class VideoSample:
    video_path: str
    label: int          # 1 = fake, 0 = real
    dataset: str        # e.g. "ffpp", "celebdf", "dfdc"


def find_videos(dataset_root: str | Path) -> list[VideoSample]:
    """Scan root/{real,fake}/*.mp4 into labeled samples (also allows nested)."""
    root = Path(dataset_root)
    out: list[VideoSample] = []
    for label_name, label in (("real", 0), ("fake", 1)):
        folder = root / label_name
        if not folder.exists():
            continue
        for vid in sorted(folder.rglob("*")):
            if vid.suffix.lower() in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
                out.append(VideoSample(str(vid), label, root.name))
    return out


def split_samples(samples: list[VideoSample], train_frac: float = 0.8, seed: int = 42):
    """Stratified train/val split by label, deterministic."""
    rng = random.Random(seed)
    fake = [s for s in samples if s.label == 1]
    real = [s for s in samples if s.label == 0]

    def split(lst):
        rng.shuffle(lst)
        n_tr = int(len(lst) * train_frac)
        return lst[:n_tr], lst[n_tr:]

    tr_f, va_f = split(fake)
    tr_r, va_r = split(real)
    return tr_f + tr_r, va_f + va_r


def _crop_cache_path(video_path: str | Path) -> Path:
    """Where pre-extracted aligned crops live for a video: scratch/crops/<name>/f_XX.jpg"""
    p = Path(video_path)
    stem = f"{p.parent.name}__{p.stem}"
    return SCRATCH_DIR / "crops" / stem


def ensure_crops_for_video(video_path: str | Path, n_frames: int = 8, face_size: int = 224):
    """Extract aligned face crops for a video if not cached. Returns list of crop paths."""
    cache = _crop_cache_path(video_path)
    if cache.exists() and len(list(cache.glob("*.jpg"))) >= 1:
        return sorted(str(p) for p in cache.glob("*.jpg"))

    from detection.data.face_utils import align_largest_face
    from detection.data.video_utils import extract_frames_at_timestamps, probe_ffprobe, sample_timestamps, read_rgb

    meta = probe_ffprobe(video_path)
    tss = sample_timestamps(meta, n_frames)
    cache.mkdir(parents=True, exist_ok=True)
    frames = extract_frames_at_timestamps(video_path, tss, cache, size=None)
    paths: list[str] = []
    for i, fr in enumerate(frames):
        rgb = read_rgb(fr["path"])
        crop, face = align_largest_face(rgb, size=face_size)
        if crop is not None:
            import cv2

            out = cache / f"f_{i:02d}.jpg"
            cv2.imwrite(str(out), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            paths.append(str(out))
    return paths


# ── PyTorch Datasets ─────────────────────────────────────────

class CropSequenceDataset:
    """Yields (tensor_of_crops, label) per VIDEO: shape (T,3,H,W) after to_tensor.

    Build on the fly: for each sample ensure crops exist (cheap when cached).
    """

    def __init__(self, samples: list[VideoSample], n_frames: int = 8, face_size: int = 224,
                 transform=None, cache_crops: bool = True):
        self.samples = samples
        self.n_frames = n_frames
        self.face_size = face_size
        self.transform = transform
        self.cache_crops = cache_crops
        # filter to samples with at least 1 usable crop
        self.valid: list[tuple[list[str], int]] = []
        for s in samples:
            try:
                crops = ensure_crops_for_video(s.video_path, n_frames, face_size)
            except Exception:
                crops = []
            if crops:
                self.valid.append((crops, s.label))

    def __len__(self):
        return len(self.valid)

    def __getitem__(self, idx):
        crops, label = self.valid[idx]
        # if fewer than n_frames extracted, tile to keep sequences dense
        import cv2

        chosen = crops
        if len(chosen) < self.n_frames:
            chosen = (chosen * (self.n_frames // len(chosen) + 1))[: self.n_frames]
        arrs = [cv2.imread(c) for c in chosen]
        arrs = [cv2.cvtColor(a, cv2.COLOR_BGR2RGB) for a in arrs if a is not None]
        if not arrs:
            raise RuntimeError("empty crops")
        if self.transform:
            arrs = [self.transform(a) for a in arrs]
        import torch

        return torch.stack(arrs), int(label)


class SingleCropDataset:
    """Flat (crop -> label) dataset for training the spatial head per-frame."""

    def __init__(self, samples: list[VideoSample], n_frames: int = 8, face_size: int = 224,
                 transform=None, max_crops_per_video: int = 8):
        self.items: list[tuple[str, int]] = []
        for s in samples:
            try:
                crops = ensure_crops_for_video(s.video_path, n_frames, face_size)
            except Exception:
                crops = []
            for c in crops[:max_crops_per_video]:
                self.items.append((c, s.label))
        self.transform = transform

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        import cv2
        import torch

        path, label = self.items[idx]
        img = cv2.imread(path)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if self.transform:
            img = self.transform(img)
        return img, int(label)


def default_transform(face_size: int = 224, train: bool = False):
    """Light normalization/augmentation. No heavy aug on CPU."""
    from torchvision import transforms

    ops = []
    if train:
        ops.append(transforms.RandomHorizontalFlip(p=0.5))
        ops.append(transforms.ColorJitter(brightness=0.1, contrast=0.1))
    ops += [
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ]
    return transforms.Compose(ops)


def numpy_transform_to_tensor(crop_rgb: np.ndarray):
    """Transform used inside pipeline (numpy in -> normalized tensor out)."""
    from torchvision import transforms

    return transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])(crop_rgb)
