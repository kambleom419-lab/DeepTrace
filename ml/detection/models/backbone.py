"""Backbone factory — the ONLY big network in the pipeline.

V1 default: Xception via timm (CPU-fast, FF++ classic).
Alternative: convnext_tiny (swap via config, same interface).
"""
from __future__ import annotations

import torch.nn as nn


def build_backbone(name: str = "xception", pretrained: bool = True) -> nn.Module:
    """Return a feature-extractor backbone that outputs a pooled feature vector."""
    import timm

    if name == "xception":
        model = timm.create_model("xception", pretrained=pretrained, num_classes=0)
    elif name in ("convnext_tiny", "convnext-tiny"):
        model = timm.create_model("convnext_tiny", pretrained=pretrained, num_classes=0)
    else:
        raise ValueError(f"Unknown backbone: {name} (use 'xception' or 'convnext_tiny')")
    return model


def backbone_feature_dim(name: str) -> int:
    return {"xception": 2048, "convnext_tiny": 768, "convnext-tiny": 768}[name]


def freeze_backbone(model: nn.Module, unfreeze_last_block: bool = False) -> None:
    """Freeze all params except optionally the final block (CPU fine-tune budget)."""
    for p in model.parameters():
        p.requires_grad = False
    if unfreeze_last_block:
        # timm models expose .blocks / .layers for convnext / xception lacks easy split;
        # Xception: unfreeze the last 'block14' via named modules heuristics
        for name, p in model.named_parameters():
            if "block14" in name or "head" in name or "fc" in name:
                p.requires_grad = True
