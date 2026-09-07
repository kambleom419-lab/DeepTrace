"""Frequency-domain feature extraction (FFT/DCT statistics).

Deepfake pipelines typically leave high-frequency artifacts from resizing,
blending and generation. We compute cheap spectral statistics per aligned crop —
no learned model needed, just numpy/scipy — then feed them to a small MLP.
"""
from __future__ import annotations

import numpy as np


def _dct2(img: np.ndarray) -> np.ndarray:
    """2D type-II DCT via scipy (normalized). Input: single channel float64."""
    from scipy.fftpack import dct

    return dct(dct(img, axis=0, norm="ortho"), axis=1, norm="ortho")


def freq_stats_for_crop(rgb: np.ndarray, n_coeffs: int = 24) -> np.ndarray:
    """Compute frequency statistics for one RGB crop -> flat feature vector.

    Uses the luminance channel (grayscale), split into an 8x8 grid; for each cell
    we compute a small DCT and keep the low-frequency energy + high-frequency
    ratio. Concatenated across cells -> robust to local artifacts.
    """
    gray = rgb.mean(axis=2)
    # normalize to [0,1]
    gray = gray.astype(np.float64) / 255.0

    h, w = gray.shape
    grid = 8
    ch, cw = h // grid, w // grid
    feats: list[float] = []

    for gy in range(grid):
        for gx in range(grid):
            cell = gray[gy * ch:(gy + 1) * ch, gx * cw:(gx + 1) * cw]
            if cell.size == 0:
                feats.extend([0.0] * 4)
                continue
            d = _dct2(cell)
            total = np.sum(d**2) + 1e-12
            # low-freq energy (top-left 3x3), high-freq ratio (rest)
            low = d[:3, :3]
            low_energy = np.sum(low**2) / total
            # radial-ish: high freq outside a small radius
            yy, xx = np.indices(d.shape)
            radius = np.sqrt((yy / ch) ** 2 + (xx / cw) ** 2)
            high_mask = radius > 0.5
            high_energy = np.sum(d[high_mask] ** 2) / total
            mean_mag = np.mean(np.abs(d))
            std_mag = np.std(d)
            feats += [low_energy, high_energy, float(mean_mag), float(std_mag)]

    # optionally trim / pad to a fixed dim
    feats = np.asarray(feats, dtype=np.float32)
    return feats


def freq_stats_for_batch(crops_rgb: list[np.ndarray]) -> np.ndarray:
    """(N, 256) float32 stats for N crops (8x8 grid x 4 stats)."""
    return np.stack([freq_stats_for_crop(c) for c in crops_rgb]).astype(np.float32)
