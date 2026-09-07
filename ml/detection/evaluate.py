"""Evaluation CLI — metrics + cross-dataset robustness.

Usage (from ml/):
    python -m detection.evaluate --dataset-root <root>

Scores videos through the FULL fusion pipeline (spatial + temporal + frequency +
fusion) and reports ACC / AUC / AP per dataset folder. The cross-dataset numbers
(FF++ train -> Celeb-DF / DFDC test) are the ones that prove the fusion claim.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from detection.config.paths import ensure_dirs, load_config
from detection.data.datasets import find_videos
from detection.pipeline import Analyzer


def evaluate_dataset(cfg: dict, dataset_root: str | Path, label_filter: str | None = None) -> dict:
    """Run pipeline over a dataset root (subfolders real/, fake/). Return metrics."""
    import time

    samples = find_videos(dataset_root)
    if label_filter is not None:
        want = 1 if label_filter == "fake" else 0
        samples = [s for s in samples if s.label == want]

    an = Analyzer(cfg)
    y_true, y_score = [], []
    t0 = time.time()
    for i, s in enumerate(samples, 1):
        try:
            res = an.analyze(s.video_path)
        except Exception as e:  # noqa: BLE001
            print(f"  [eval] failed {Path(s.video_path).name}: {e}")
            continue
        y_true.append(s.label)
        y_score.append(res["confidence"])
        if i % 5 == 0 or i == len(samples):
            print(f"  [{i}/{len(samples)}] {time.time()-t0:.0f}s elapsed")

    if not y_true:
        return {"n": 0}

    y_true = np.asarray(y_true)
    y_score = np.asarray(y_score)
    acc = float(((y_score >= 0.5) == y_true).mean())
    try:
        from sklearn.metrics import roc_auc_score, average_precision_score
        auc = float(roc_auc_score(y_true, y_score))
        ap = float(average_precision_score(y_true, y_score))
    except Exception:  # sklearn may be absent; fall back to rank-based AUC
        auc = _rank_auc(y_true, y_score)
        ap = float("nan")

    return {"n": int(len(y_true)), "acc": round(acc, 4), "auc": round(auc, 4), "ap": round(ap, 4)}


def _rank_auc(y_true, y_score) -> float:
    """Mann-Whitney U AUC without sklearn."""
    order = np.argsort(y_score)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(y_score) + 1)
    n_pos = y_true.sum()
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[y_true == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def main():
    ap = argparse.ArgumentParser(description="Evaluate the DeepTrace pipeline on a dataset")
    ap.add_argument("--dataset-root", required=True, help="root with real/ + fake/ subfolders")
    args = ap.parse_args()

    cfg = load_config()
    ensure_dirs()
    print(f"Evaluating {args.dataset_root}")
    m = evaluate_dataset(cfg, args.dataset_root)
    print(f"  {Path(args.dataset_root).name}: n={m.get('n', 0)} acc={m.get('acc')} auc={m.get('auc')} ap={m.get('ap')}")


if __name__ == "__main__":
    main()
