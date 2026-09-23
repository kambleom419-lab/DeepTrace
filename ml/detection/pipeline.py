"""End-to-end analysis pipeline.

Public contract:
    python -m detection.pipeline <video_path> [--out DIR]
    or in code:  run_analysis(video_path, cfg) -> dict

The returned dict matches ``frontend/src/types/index.ts::AnalysisResult`` (plus a
``_meta`` key carrying video metadata + timing for the backend/report).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torchvision.transforms as T

from detection.config.paths import SCRATCH_DIR, WEIGHTS_DIR, ensure_dirs, load_config
from detection.data.face_utils import align_largest_face
from detection.data.video_utils import (
    extract_frames_at_timestamps,
    probe_ffprobe,
    read_rgb,
    sample_timestamps,
    set_ffmpeg_bin,
)
from detection.explain.gradcam import GradCAM, default_target_layer, overlay_heatmap
from detection.models.frequency_features import freq_stats_for_batch
from detection.models.heads import Detector, FrequencyHead, FusionHead, TemporalHead

# Default priors used ONLY when a head checkpoint is missing (untrained smoke mode),
# so analyze() runs end-to-end before any training. Real checkpoints override these.
_PRIORS = {"spatial": 0.12, "temporal": 0.10, "frequency": 0.15, "fusion": 0.11}

_FEAT_DIM = {"xception": 2048, "convnext_tiny": 768}


def _load_head(ckpt: Path, head_cls, **kw) -> torch.nn.Module | None:
    if not ckpt.exists():
        return None
    try:
        head = head_cls(**kw)
        state = torch.load(ckpt, map_location="cpu")
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        head.load_state_dict(state)
        head.eval()
        return head
    except Exception as e:  # noqa: BLE001
        print(f"[pipeline] warn: could not load {ckpt.name}: {e}", file=sys.stderr)
        return None


class Analyzer:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        vid = self.cfg.get("video", {})
        if vid.get("ffmpeg_bin"):
            set_ffmpeg_bin(vid["ffmpeg_bin"])
        bb = self.cfg.get("backbone", {})
        self.backbone_name = bb.get("name", "xception")
        self.frame_budget = int(self.cfg.get("data", {}).get("frame_budget", 8))
        self.face_size = int(self.cfg.get("data", {}).get("face_size", 224))
        th = self.cfg.get("thresholds", {})
        self.th_fake = float(th.get("fake", 0.6))
        self.th_real = float(th.get("real", 0.4))
        self.th_susp = float(th.get("suspicious", 0.5))
        self.top_k = int(th.get("heatmap_top_k", 3))
        self.t_hidden = int(self.cfg.get("temporal", {}).get("hidden", 128))

        # weight paths
        self.weights = WEIGHTS_DIR
        self._spatial_ckpt = WEIGHTS_DIR / "spatial_xception.pt"
        self._temporal_ckpt = WEIGHTS_DIR / "temporal_gru.pt"
        self._freq_ckpt = WEIGHTS_DIR / "frequency_mlp.pt"
        self._fusion_ckpt = WEIGHTS_DIR / "fusion_mlp.pt"

        self.detector: Detector | None = None
        self.temporal: TemporalHead | None = None
        self.frequency: FrequencyHead | None = None
        self.fusion: FusionHead | None = None
        self._models_ready = False
        self.device = torch.device("cpu")

    # ── model loading ───────────────────────────────────────
    def _load_models(self):
        if self._models_ready:
            return
        ensure_dirs()
        feat_dim = _FEAT_DIM.get(self.backbone_name, 2048)

        # Spatial detector (backbone + spatial head)
        self.detector = Detector(self.backbone_name, pretrained=not self._spatial_ckpt.exists())
        if self._spatial_ckpt.exists():
            state = torch.load(self._spatial_ckpt, map_location="cpu")
            if isinstance(state, dict) and "state_dict" in state:
                state = state["state_dict"]
            self.detector.load_state_dict(state)
        self.detector.eval()

        self.temporal = _load_head(self._temporal_ckpt, TemporalHead,
                                   in_dim=feat_dim,
                                   hidden=self.t_hidden)
        self.frequency = _load_head(self._freq_ckpt, FrequencyHead,
                                    in_dim=256,
                                    hidden=int(self.cfg.get("frequency", {}).get("hidden", 64)))
        # fusion input = spatial mean feat (feat_dim) + temporal hidden (t_hidden) + freq (256)
        self.fusion = _load_head(self._fusion_ckpt, FusionHead,
                                 in_dim=feat_dim + self.t_hidden + 256,
                                 hidden=int(self.cfg.get("fusion", {}).get("hidden", 64)))
        self._models_ready = True

    # ── the pipeline ────────────────────────────────────────
    def analyze(self, video_path: str | Path, out_dir: str | Path | None = None) -> dict:
        t0 = time.time()
        self._load_models()
        out_dir = Path(out_dir or (SCRATCH_DIR / "results"))
        (out_dir / "frames").mkdir(parents=True, exist_ok=True)

        # 1) preprocess: sample frames, extract, detect+align largest face
        meta = probe_ffprobe(video_path)
        tss = sample_timestamps(meta, self.frame_budget)
        frames = extract_frames_at_timestamps(video_path, tss, out_dir / "frames")

        crops: list[np.ndarray] = []
        frame_map: list[dict] = []
        for fr in frames:
            rgb = read_rgb(fr["path"])
            crop, face = align_largest_face(rgb, size=self.face_size)
            if crop is not None:
                crops.append(crop)
                frame_map.append({"timestamp": fr["timestamp"], "frame_number": fr["frame_number"]})
        if not crops:
            raise RuntimeError(f"No face detected in any sampled frame of {video_path}")

        # 2) spatial (single backbone pass over all crops)
        to_t = T.Compose([
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        x = torch.stack([to_t(c) for c in crops])  # (T,3,224,224)
        with torch.no_grad():
            feats, sp_logits = self.detector.forward_with_feats(x)  # (T,D),(T,)
        sp_probs = torch.sigmoid(sp_logits).numpy()

        # 3) temporal (features reused — no extra backbone pass)
        temporal_prob = _PRIORS["temporal"]
        temporal_hidden = np.zeros(self.t_hidden, dtype=np.float32)
        if self.temporal is not None:
            with torch.no_grad():
                temporal_prob = float(torch.sigmoid(self.temporal(feats.unsqueeze(0))).item())
                # mean-pooled GRU hidden state — the exact temporal feature the fusion
                # head was trained on in train.py::train_fusion. Must not be zeros:
                # that would make inference disagree with training.
                gru_out, _ = self.temporal.gru(feats.unsqueeze(0))
                temporal_hidden = gru_out.mean(dim=1).squeeze(0).numpy()

        # 4) frequency
        fstats = freq_stats_for_batch(crops)  # (T,256)
        freq_prob = _PRIORS["frequency"]
        if self.frequency is not None:
            with torch.no_grad():
                # unsqueeze -> (1,T,256) so FrequencyHead averages over the T crops
                # (its (B,T,F) branch). A bare (T,256) is read as B=T and yields T
                # scores instead of one video score.
                freq_prob = float(torch.sigmoid(
                    self.frequency(torch.from_numpy(fstats).unsqueeze(0))).item())

        # 5) fusion
        spatial_feat = feats.mean(dim=0).numpy()
        fused_vec = np.concatenate([spatial_feat, temporal_hidden, fstats.mean(axis=0)])
        fusion_prob = _PRIORS["fusion"]
        if self.fusion is not None:
            with torch.no_grad():
                fusion_prob = float(torch.sigmoid(self.fusion(torch.from_numpy(fused_vec).float())).item())

        spatial_prob = float(sp_mean(sp_probs))
        if self.fusion is not None:
            final = fusion_prob
        else:
            # no fusion ckpt yet -> weighted blend of the branch signals
            final = float(np.clip(
                _PRIORS["fusion"] * 0.35 + spatial_prob * 0.30 + temporal_prob * 0.20 + freq_prob * 0.15, 0, 1))

        # 6) explainability (top suspicious frames only)
        evidence = []
        if self.detector is not None and len(sp_probs) > 0:
            order = np.argsort(sp_probs)[::-1][: self.top_k]
            for i in order:
                if float(sp_probs[i]) >= self.th_susp:
                    try:
                        gcam = GradCAM(self.detector, default_target_layer(self.backbone_name))
                        cam = gcam.generate(x[i:i + 1])
                        overlay = overlay_heatmap(crops[i], cam)
                        import cv2

                        ev_path = out_dir / f"heatmap_f{frame_map[i]['frame_number']:03d}.jpg"
                        cv2.imwrite(str(ev_path), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
                        evidence.append({
                            "id": f"ev-{len(evidence)+1:03d}",
                            "timestamp": round(float(frame_map[i]["timestamp"]), 2),
                            "frame_number": int(frame_map[i]["frame_number"]),
                            "evidence_type": "heatmap",
                            "score": round(float(sp_probs[i]), 4),
                            "heatmap_url": ev_path.name,
                            "description": (
                                "Grad-CAM: strong activation — possible blending/generation artifact"
                                if float(sp_probs[i]) > 0.7
                                else "Grad-CAM: moderate activation region"
                            ),
                        })
                    except Exception as e:  # noqa: BLE001
                        print(f"[pipeline] gradcam skip: {e}", file=sys.stderr)

        # 7) suspicious segments from per-frame timestamps
        segments: list[dict] = []
        active: list | None = None
        for i, p in enumerate(sp_probs):
            ts = float(frame_map[i]["timestamp"])
            if float(p) >= self.th_susp:
                if active is None:
                    active = [ts, ts, float(p)]
                else:
                    active[1] = ts
                    active[2] = max(active[2], float(p))
            elif active is not None:
                segments.append({"start": active[0], "end": active[1], "score": round(active[2], 4)})
                active = None
        if active is not None:
            segments.append({"start": active[0], "end": active[1], "score": round(active[2], 4)})

        # 8) verdict
        if final >= self.th_fake:
            verdict = "LIKELY_MANIPULATED"
        elif final <= self.th_real:
            verdict = "LIKELY_AUTHENTIC"
        else:
            verdict = "INCONCLUSIVE"

        return {
            "verdict": verdict,
            "confidence": round(final, 4),
            "spatial_score": round(spatial_prob, 4),
            "temporal_score": round(temporal_prob, 4),
            "frequency_score": round(freq_prob, 4),
            "suspicious_segments": segments,
            "frame_scores": [round(float(p), 4) for p in sp_probs],
            "evidence": evidence,
            "_meta": {
                "filename": Path(video_path).name,
                "duration": round(meta.duration, 2),
                "fps": round(meta.fps, 2),
                "resolution": meta.resolution,
                "frames_sampled": len(crops),
                "timing_s": round(time.time() - t0, 2),
            },
        }


def sp_mean(probs: np.ndarray) -> float:
    return float(np.mean(probs))


def main() -> None:
    ap = argparse.ArgumentParser(description="DeepTrace analyze a video for manipulation")
    ap.add_argument("video", help="path to video file")
    ap.add_argument("--out", default=None, help="output dir (default ml/scratch/results)")
    ap.add_argument("--json", default=None, help="also write result JSON to this path")
    args = ap.parse_args()

    ensure_dirs()
    cfg = load_config()
    result = Analyzer(cfg).analyze(args.video, out_dir=args.out)
    print(json.dumps(result, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
