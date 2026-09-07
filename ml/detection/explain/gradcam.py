"""Grad-CAM explainability for the spatial head.

Produces class-activation heatmaps over the aligned face crop so a human can see
WHICH region drove the "manipulated" decision (jawline blend, eye artifact, ...).
"""
from __future__ import annotations

import cv2
import numpy as np
import torch


class GradCAM:
    """Channel-average gradient-weighted activation for a target conv layer."""

    def __init__(self, model: torch.nn.Module, target_layer: torch.nn.Module | str):
        self.model = model
        self.gradients: torch.Tensor | None = None
        self.activations: torch.Tensor | None = None

        if isinstance(target_layer, str):
            target_layer = _resolve_layer(model, target_layer)
        self.target = target_layer
        self._register_hooks()

    def _register_hooks(self):
        self.target.register_forward_hook(self._save_activation)
        self.target.register_full_backward_hook(self._save_gradient)

    def _save_activation(self, module, inp, out):
        self.activations = out.detach()

    def _save_gradient(self, module, grad_in, grad_out):
        self.gradients = grad_out[0].detach()

    def generate(self, x: torch.Tensor, class_idx: int = 0) -> np.ndarray:
        """Run forward+backward, return (H,W) heatmap in [0,1]."""
        self.model.zero_grad()
        out = self.model(x)  # (B,)
        score = out[:, class_idx] if out.dim() > 1 and out.size(1) > 1 else out
        score.sum().backward()

        act = self.activations[0]  # (C, H', W')
        grad = self.gradients[0]
        weights = grad.mean(dim=(1, 2), keepdim=True)  # (C,1,1)
        cam = (weights * act).sum(dim=0).clamp(min=0)
        # resize to input spatial size
        h, w = x.shape[-2:]
        cam = cam.detach().cpu().numpy()
        cam = cv2.resize(cam, (w, h))
        cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)
        return cam


def _resolve_layer(model: torch.nn.Module, name: str) -> torch.nn.Module:
    node = model
    for part in name.split("."):
        node = getattr(node, part)
    return node


def default_target_layer(backbone_name: str = "xception") -> str:
    """A sensible last-conv layer per backbone for Grad-CAM."""
    if backbone_name == "xception":
        # timm xception ends: block12 -> conv3 -> conv4; conv4.pointwise is the last conv
        return "backbone.conv4.pointwise"
    return "backbone.stages.3"  # convnext_tiny last stage


def overlay_heatmap(
    image: np.ndarray,
    cam: np.ndarray,
    alpha: float = 0.55,
    colormap: int = cv2.COLORMAP_JET,
) -> np.ndarray:
    """Blend a (H,W) CAM onto an RGB image, return RGB overlay."""
    heat = cv2.applyColorMap(np.uint8(255 * cam), colormap)
    heat = cv2.cvtColor(heat, cv2.COLOR_BGR2RGB)
    return np.uint8(alpha * heat + (1 - alpha) * image)
