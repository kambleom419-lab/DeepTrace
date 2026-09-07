"""Path + config helpers for the ML package.

Usage:
    from detection.config.paths import ROOT, WEIGHTS_DIR, ensure_dirs
"""
from __future__ import annotations

from pathlib import Path

# ml/  (parent of the detection/ package)
ROOT = Path(__file__).resolve().parent.parent

WEIGHTS_DIR = ROOT / "weights"
DATA_DIR = ROOT / "data"
RUNS_DIR = ROOT / "runs"
SCRATCH_DIR = ROOT / "scratch"

CONFIG_FILE = ROOT / "config" / "defaults.yaml"


def ensure_dirs() -> None:
    """Create all output directories (safe to call at import time)."""
    for d in (WEIGHTS_DIR, DATA_DIR, RUNS_DIR, SCRATCH_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load config/defaults.yaml, overlay optional env overrides. Returns dict."""
    import os
    import yaml

    ensure_dirs()
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}

    # env overrides (allow minimal tuning without touching yaml)
    for key in ("DATASET_ROOT", "FFMPEG_BIN"):
        val = os.environ.get(key)
        if val:
            cfg[key.lower()] = val
    return cfg
