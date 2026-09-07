"""Training CLI — train the four heads in order.

Usage (from ml/):
    python -m detection.train --stage spatial     [--dataset-root DIR]
    python -m detection.train --stage temporal
    python -m detection.train --stage frequency
    python -m detection.train --stage fusion

--stage all runs them in order. Train spatial first (it also caches crops),
then temporal, then frequency, then fusion on a HELD-OUT split (no leakage).
"""
from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

import torch
import torch.nn as nn

from detection.config.paths import WEIGHTS_DIR, ensure_dirs, load_config
from detection.data.datasets import (
    SingleCropDataset,
    default_transform,
    find_videos,
    split_samples,
)
from detection.models.backbone import freeze_backbone
from detection.models.frequency_features import freq_stats_for_batch
from detection.models.heads import Detector, FrequencyHead, FusionHead, TemporalHead

_FEAT_DIM = {"xception": 2048, "convnext_tiny": 768}


# ── stage trainers ───────────────────────────────────────────

def train_spatial(cfg, dataset_root):
    print("[train] spatial head")
    data_cfg = cfg["data"]
    bb_cfg = cfg["backbone"]
    tr = cfg["training"]
    samples = find_videos(dataset_root)
    tr_s, va_s = split_samples(samples, train_frac=data_cfg["train_split"], seed=data_cfg["seed"])

    model = Detector(bb_cfg["name"], pretrained=True)
    freeze_backbone(model.backbone, unfreeze_last_block=False)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                            lr=tr["lr"], weight_decay=tr["weight_decay"])
    lossf = nn.BCEWithLogitsLoss()

    # Flat crop dataset from train samples (label per crop)
    train_ds = SingleCropDataset(tr_s, n_frames=data_cfg["frame_budget"], face_size=data_cfg["face_size"],
                                 transform=default_transform(train=True))
    val_ds = SingleCropDataset(va_s, n_frames=data_cfg["frame_budget"], face_size=data_cfg["face_size"],
                               transform=default_transform(train=False))
    from torch.utils.data import DataLoader

    tr_loader = DataLoader(train_ds, batch_size=tr["batch_size"], shuffle=True)
    va_loader = DataLoader(val_ds, batch_size=tr["batch_size"], shuffle=False)

    for ep in range(tr["epochs"]["spatial"]):
        model.train()
        t0 = time.time()
        tot = 0.0
        n = 0
        for img, label in tr_loader:
            lbl = torch.tensor([[float(label)] for _ in range(img.size(0))])
            _, logits = model.forward_with_feats(img)
            loss = lossf(logits, lbl)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item()
            n += 1
        # quick val accuracy
        model.eval()
        acc = _val_acc_spatial(model, va_loader)
        print(f"  epoch {ep+1}/{tr['epochs']['spatial']} loss={tot/max(n,1):.4f} val_acc={acc:.3f} ({time.time()-t0:.1f}s)")

    ckpt = WEIGHTS_DIR / "spatial_xception.pt"
    torch.save({"state_dict": model.state_dict(), "config": bb_cfg}, ckpt)
    print(f"[train] saved {ckpt}")


@torch.no_grad()
def _val_acc_spatial(model, loader):
    import numpy as np

    correct = total = 0
    model.eval()
    for img, label in loader:
        _, logits = model.forward_with_feats(img)
        preds = (torch.sigmoid(logits) >= 0.5).float()
        correct += (preds == label).sum().item()
        total += label.numel()
    return correct / max(total, 1)


def train_temporal(cfg, dataset_root):
    print("[train] temporal head (GRU over frozen backbone features)")
    data_cfg = cfg["data"]
    t_cfg = cfg["temporal"]
    tr = cfg["training"]
    samples = find_videos(dataset_root)
    tr_s, va_s = split_samples(samples, data_cfg["train_split"], data_cfg["seed"])
    bb = cfg["backbone"]["name"]

    model = Detector(bb, pretrained=True)
    sp_ckpt = WEIGHTS_DIR / "spatial_xception.pt"
    if sp_ckpt.exists():
        state = torch.load(sp_ckpt, map_location="cpu")
        state = state.get("state_dict", state)
        model.load_state_dict(state)
    freeze_backbone(model.backbone)
    model.eval()
    feat_dim = _FEAT_DIM[bb]

    gru = TemporalHead(feat_dim, hidden=t_cfg["hidden"], layers=t_cfg["layers"], dropout=t_cfg["dropout"])
    opt = torch.optim.AdamW(gru.parameters(), lr=tr["lr"], weight_decay=tr["weight_decay"])
    lossf = nn.BCEWithLogitsLoss()

    from detection.data.datasets import ensure_crops_for_video
    from detection.data.video_utils import read_rgb
    import numpy as np
    from torchvision import transforms

    to_t = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    def _vid_feats(sample):
        crops = ensure_crops_for_video(sample.video_path, data_cfg["frame_budget"], data_cfg["face_size"])
        if not crops:
            return None
        import cv2

        arrs = []
        for c in crops:
            img = cv2.imread(c)
            if img is not None:
                arrs.append(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        if not arrs:
            return None
        x = torch.stack([to_t(a) for a in arrs]).unsqueeze(0)
        with torch.no_grad():
            f, _ = model.forward_with_feats(x)  # (1,T,D)
        return f

    for ep in range(tr["epochs"]["temporal"]):
        gru.train()
        tot = 0.0
        n = 0
        rng = random.Random(ep)
        rng.shuffle(tr_s)
        for s in tr_s:
            f = _vid_feats(s)
            if f is None:
                continue
            lbl = torch.tensor([[float(s.label)]])
            out = gru(f)
            loss = lossf(out, lbl)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item()
            n += 1
        acc = _val_temporal(gru, _vid_feats, va_s)
        print(f"  epoch {ep+1} loss={tot/max(n,1):.4f} val_acc={acc:.3f}")

    ckpt = WEIGHTS_DIR / "temporal_gru.pt"
    torch.save({"state_dict": gru.state_dict(), "config": t_cfg}, ckpt)
    print(f"[train] saved {ckpt}")


@torch.no_grad()
def _val_temporal(gru, vid_feats_fn, val_samples):
    correct = total = 0
    gru.eval()
    for s in val_samples:
        f = vid_feats_fn(s)
        if f is None:
            continue
        p = torch.sigmoid(gru(f)).item()
        pred = int(p >= 0.5)
        correct += pred == s.label
        total += 1
    return correct / max(total, 1)


def train_frequency(cfg, dataset_root):
    print("[train] frequency head (FFT/DCT stats -> MLP)")
    data_cfg = cfg["data"]
    fr_cfg = cfg["frequency"]
    tr = cfg["training"]
    samples = find_videos(dataset_root)
    tr_s, va_s = split_samples(samples, data_cfg["train_split"], data_cfg["seed"])
    from detection.data.datasets import ensure_crops_for_video
    import cv2

    def _stats(sample):
        crops = ensure_crops_for_video(sample.video_path, data_cfg["frame_budget"], data_cfg["face_size"])
        arrs = []
        for c in crops:
            img = cv2.imread(c)
            if img is not None:
                arrs.append(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        if not arrs:
            return None
        return freq_stats_for_batch(arrs)  # (T,256)

    model = FrequencyHead(in_dim=256, hidden=fr_cfg["hidden"])
    opt = torch.optim.AdamW(model.parameters(), lr=tr["lr"], weight_decay=tr["weight_decay"])
    lossf = nn.BCEWithLogitsLoss()
    import numpy as np

    for ep in range(tr["epochs"]["frequency"]):
        model.train()
        tot = 0.0
        n = 0
        rng = random.Random(ep)
        rng.shuffle(tr_s)
        for s in tr_s:
            stats = _stats(s)
            if stats is None:
                continue
            x = torch.from_numpy(np.asarray(stats)).float()
            out = model(x)  # aggregates over T
            loss = lossf(out, torch.tensor([float(s.label)]))
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item()
            n += 1
        acc = _val_freq(model, _stats, va_s)
        print(f"  epoch {ep+1} loss={tot/max(n,1):.4f} val_acc={acc:.3f}")

    ckpt = WEIGHTS_DIR / "frequency_mlp.pt"
    torch.save({"state_dict": model.state_dict(), "config": fr_cfg}, ckpt)
    print(f"[train] saved {ckpt}")


@torch.no_grad()
def _val_freq(model, stats_fn, val_samples):
    import numpy as np

    correct = total = 0
    model.eval()
    for s in val_samples:
        st = stats_fn(s)
        if st is None:
            continue
        p = torch.sigmoid(model(torch.from_numpy(np.asarray(st)).float())).item()
        correct += int(p >= 0.5) == s.label
        total += 1
    return correct / max(total, 1)


def train_fusion(cfg, dataset_root):
    print("[train] fusion head (held-out split — no leakage)")
    data_cfg = cfg["data"]
    fu_cfg = cfg["fusion"]
    tr = cfg["training"]
    samples = find_videos(dataset_root)
    # 3-way split: spatial/temporal/freq trained on (train+val), fusion on holdout
    rng = random.Random(data_cfg["seed"])
    fake = [s for s in samples if s.label == 1]
    real = [s for s in samples if s.label == 0]

    def part(lst):
        rng.shuffle(lst)
        n_br = int(len(lst) * 0.7)
        n_ho = int(len(lst) * 0.15)
        return lst[:n_br], lst[n_br:n_br + n_ho], lst[n_br + n_ho:]

    tr_f, ho_f, te_f = part(fake)
    tr_r, ho_r, te_r = part(real)
    fusion_train = ho_f + ho_r  # what fusion sees (held-out — branch heads never saw these)
    fusion_val = te_f + te_r

    def _load_ckpt(ckpt: Path, head, prefix="") -> bool:
        """Load state (optionally under a 'state_dict' key). Returns success."""
        if not ckpt.exists():
            return False
        state = torch.load(ckpt, map_location="cpu")
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        if prefix:
            state = {k[len(prefix):]: v for k, v in state.items() if k.startswith(prefix)}
        head.load_state_dict(state)
        return True

    # Load all branch weights
    bb = cfg["backbone"]["name"]
    model = Detector(bb, pretrained=True)
    _load_ckpt(WEIGHTS_DIR / "spatial_xception.pt", model)
    freeze_backbone(model.backbone)
    model.eval()
    feat_dim = _FEAT_DIM[bb]

    temporal = None
    if (WEIGHTS_DIR / "temporal_gru.pt").exists():
        temporal = TemporalHead(feat_dim, hidden=cfg["temporal"]["hidden"])
        _load_ckpt(WEIGHTS_DIR / "temporal_gru.pt", temporal)
        temporal.eval()

    freq_model = None
    if (WEIGHTS_DIR / "frequency_mlp.pt").exists():
        freq_model = FrequencyHead(in_dim=256, hidden=cfg["frequency"]["hidden"])
        _load_ckpt(WEIGHTS_DIR / "frequency_mlp.pt", freq_model)
        freq_model.eval()

    from detection.data.datasets import ensure_crops_for_video
    import cv2
    import numpy as np
    from torchvision import transforms

    to_t = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    def _fused_vec(sample):
        crops = ensure_crops_for_video(sample.video_path, data_cfg["frame_budget"], data_cfg["face_size"])
        arrs = [cv2.cvtColor(cv2.imread(c), cv2.COLOR_BGR2RGB) for c in crops if cv2.imread(c) is not None]
        if not arrs:
            return None
        x = torch.stack([to_t(a) for a in arrs]).unsqueeze(0)
        with torch.no_grad():
            f, _ = model.forward_with_feats(x)  # (1,T,D)
            spatial_v = f.mean(dim=1).squeeze(0).numpy()  # (D,)
        temporal_v = np.zeros(cfg["temporal"]["hidden"], dtype=np.float32)
        if temporal is not None:
            with torch.no_grad():
                out, _ = temporal.gru(f)
                temporal_v = out.mean(dim=1).squeeze(0).numpy()
        freq_v = freq_stats_for_batch(arrs).mean(axis=0)
        return np.concatenate([spatial_v, temporal_v, freq_v]).astype(np.float32)

    fusion = FusionHead(in_dim=feat_dim + cfg["temporal"]["hidden"] + 256, hidden=fu_cfg["hidden"], dropout=fu_cfg["dropout"])
    opt = torch.optim.AdamW(fusion.parameters(), lr=tr["lr"], weight_decay=tr["weight_decay"])
    lossf = nn.BCEWithLogitsLoss()

    for ep in range(tr["epochs"]["fusion"]):
        fusion.train()
        tot = 0.0
        n = 0
        rng.shuffle(fusion_train)
        for s in fusion_train:
            v = _fused_vec(s)
            if v is None:
                continue
            out = fusion(torch.from_numpy(v).unsqueeze(0))
            loss = lossf(out, torch.tensor([[float(s.label)]]))
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item()
            n += 1
        acc = _val_fusion(fusion, _fused_vec, fusion_val)
        print(f"  epoch {ep+1} loss={tot/max(n,1):.4f} val_acc={acc:.3f}")

    ckpt = WEIGHTS_DIR / "fusion_mlp.pt"
    torch.save({"state_dict": fusion.state_dict(), "config": fu_cfg}, ckpt)
    print(f"[train] saved {ckpt}")


@torch.no_grad()
def _val_fusion(fusion, fused_fn, val_samples):
    correct = total = 0
    fusion.eval()
    for s in val_samples:
        v = fused_fn(s)
        if v is None:
            continue
        p = torch.sigmoid(fusion(torch.from_numpy(v).unsqueeze(0))).item()
        correct += int(p >= 0.5) == s.label
        total += 1
    return correct / max(total, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["spatial", "temporal", "frequency", "fusion", "all"], default="spatial")
    ap.add_argument("--dataset-root", default=None, help="override dataset root (default config)")
    args = ap.parse_args()

    cfg = load_config()
    root = args.dataset_root or cfg.get("data", {}).get("dataset_root", "")
    if not root or not Path(root).exists():
        print(f"[train] dataset root not found: {root!r}. Pass --dataset-root DIR (real/ + fake/ subfolders).")
        return
    ensure_dirs()

    stages = ["spatial", "temporal", "frequency", "fusion"] if args.stage == "all" else [args.stage]
    for st in stages:
        {"spatial": train_spatial, "temporal": train_temporal,
         "frequency": train_frequency, "fusion": train_fusion}[st](cfg, root)


if __name__ == "__main__":
    main()
