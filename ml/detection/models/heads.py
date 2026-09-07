"""Model heads: Spatial / Temporal / Frequency / Fusion + the composite Detector.

Spatial:   backbone features -> FC -> per-frame p_fake
Temporal:  per-frame features (frozen backbone) -> GRU -> video score
Frequency: per-crop FFT/DCT stats -> MLP -> score
Fusion:    concat(mean(spatial feat) | temporal feat | freq feat) -> MLP -> final p
"""
from __future__ import annotations

import torch
import torch.nn as nn


class SpatialHead(nn.Module):
    """Per-frame classifier over the backbone feature vector."""

    def __init__(self, in_dim: int, dropout: float = 0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(in_dim, 256),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(256, 1),
        )

    def forward(self, feats: torch.Tensor) -> torch.Tensor:
        # feats: (B, D) or (B, T, D) — handle both
        if feats.dim() == 3:
            return self.net(feats).squeeze(-1)  # (B, T)
        return self.net(feats).squeeze(-1)  # (B,)


class TemporalHead(nn.Module):
    """GRU over a sequence of per-frame backbone features -> video score."""

    def __init__(self, in_dim: int, hidden: int = 128, layers: int = 1, dropout: float = 0.3):
        super().__init__()
        self.gru = nn.GRU(in_dim, hidden, num_layers=layers, batch_first=True, dropout=dropout if layers > 1 else 0.0)
        self.head = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(inplace=True),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, feats_seq: torch.Tensor) -> torch.Tensor:
        # feats_seq: (B, T, D)
        out, _ = self.gru(feats_seq)  # (B, T, H)
        pooled = out.mean(dim=1)  # mean-pool over time
        return self.head(pooled).squeeze(-1)  # (B,)


class FrequencyHead(nn.Module):
    """MLP over precomputed frequency statistics."""

    def __init__(self, in_dim: int, hidden: int = 64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden),
            nn.ReLU(inplace=True),
            nn.Dropout(0.2),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(inplace=True),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, stats: torch.Tensor) -> torch.Tensor:
        # stats: (B, F) or (B, T, F) -> aggregate if sequence
        if stats.dim() == 3:
            stats = stats.mean(dim=1)
        return self.net(stats).squeeze(-1)  # (B,)


class FusionHead(nn.Module):
    """Learns how much each evidence branch contributes to the final verdict."""

    def __init__(self, in_dim: int, hidden: int = 64, dropout: float = 0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(in_dim, hidden),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(inplace=True),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, fused: torch.Tensor) -> torch.Tensor:
        return self.net(fused).squeeze(-1)  # (B,)


class Detector(nn.Module):
    """Composite: backbone + spatial head. Exposes forward_feats for other heads.

    Usage in pipeline:
        feats, spatial_logits = detector.forward_with_feats(crops)
    """

    def __init__(
        self,
        backbone_name: str = "xception",
        pretrained: bool = True,
        spatial_dropout: float = 0.3,
    ):
        super().__init__()
        from .backbone import backbone_feature_dim, build_backbone

        self.backbone = build_backbone(backbone_name, pretrained=pretrained)
        self.feat_dim = backbone_feature_dim(backbone_name)
        self.spatial = SpatialHead(self.feat_dim, dropout=spatial_dropout)

    def forward_with_feats(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        feats = self.backbone(x)  # (B, D)
        logits = self.spatial(feats)
        return feats, logits

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _, logits = self.forward_with_feats(x)
        return logits
