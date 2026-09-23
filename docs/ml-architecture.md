# DeepTrace — ML Pipeline Architecture Plan

**Machine reality (checked):** CPU-only (Intel UHD), i5-13420H, 16 GB RAM, no NVIDIA GPU,
no FFmpeg yet. Every choice below respects these constraints.

---

## 1. Design principles (why this shape)

1. **Use CPU-viable models.** No VideoMAE, no video transformers, no training from scratch.
   One image backbone (Xception or ConvNeXt) + a tiny temporal net. This is what most real
   deepfake methods do anyway — it's not a compromise, it's the proven recipe.
2. **Shared backbone, three heads.** The spatial backbone extracts features ONCE per frame; the
   temporal and frequency branches *reuse* those features. No redundant forward passes, no
   three separate big models.
3. **Batch-friendly frame budget.** 8 sampled frames per video for V1 → predictable CPU time,
   bounded memory.
4. **Interface-first.** `analyze(video_path) -> AnalysisResult` is the contract. Frontend types,
   backend, and this pipeline all speak the same JSON. ML is a swappable black box.
5. **Explainability is not optional.** Every result carries Grad-CAM heatmaps + per-frame scores
   — that's the forensic value.

**Backbone choice — Xception over ConvNeXt-Tiny on CPU:**
- Xception (71M params) runs ~3-4x faster than ConvNeXt-Tiny on CPU at similar accuracy on
  FaceForensics++ (it's the classic FF++ workhorse).
- ConvNeXt-Tiny needs more layers per forward → more CPU-seconds per frame.
- Verdict: **Xception (ImageNet-pretrained via `timm`)**, fine-tuned on FF++ face crops. We can
  swap to ConvNeXt later behind the same `build_backbone()`.

---

## 2. Directory layout

```
ml/
├── config/
│   ├── defaults.yaml            # all hyperparams, paths, thresholds in ONE place
│   └── paths.py                 # helpers to resolve weights/, data/, runs/
├── data/
│   ├── __init__.py
│   ├── download.py              # tiny FF++ sample / dataset prep scripts
│   ├── datasets.py              # PyTorch Dataset: FaceForensics++, Celeb-DF, DFDC
│   ├── face_utils.py            # RetinaFace wrapper + alignment (bbox + 5 landmarks)
│   └── video_utils.py           # FFmpeg/OpenCV: sample N frames, decode, get metadata
├── models/
│   ├── __init__.py
│   ├── backbone.py              # build_xception() / build_convmnext() via timm
│   ├── spatial.py               # SpatialHead: backbone + FC → per-frame fake prob
│   ├── temporal.py              # TemporalHead: per-frame feats → GRU → score
│   ├── frequency.py             # FrequencyHead: FFT/DCT stats → small MLP
│   ├── fusion.py                # FusionHead: concat 3 feats → final prob
│   └── heads.py                 # shared pooling/classification building blocks
├── explain/
│   ├── gradcam.py               # Grad-CAM on the spatial backbone
│   └── overlay.py               # heatmap PNG overlay onto aligned face crop
├── pipeline.py                  # analyze(video_path) -> AnalysisResult  (THE contract)
├── train.py                     # CLI: python -m ml.train --stage spatial|temporal|frequency|fusion
├── evaluate.py                  # CLI: metrics + cross-dataset eval → docs/eval.md
├── requirements.txt
└── weights/                     # gitignored checkpoints
```

---

## 3. The three stages of analysis (runtime flow)

**Stage A — Preprocess (no ML, ~seconds on CPU):**

```
video.mp4
  → ffmpeg: extract 8 evenly-spaced frames (30fps default)
  → OpenCV decode each → rgb
  → RetinaFace (insightface) detect face bbox + 5 landmarks per frame
      · no face found → mark frame as skip
  → align (similarity transform on eyes/nose) → 224×224 face crop
  → save crops as .jpg to scratch (also = forensic "crops" evidence)
```

**Stage B — Spatial backbone (the only big network, runs once):**

```
8 aligned crops (batch of 8)
  → Xception (ImageNet) forward  → per-frame feature vector f_i (2048-d)
  → SpatialHead FC  → per-frame fake prob p_i
  → keep f_i for the other heads (shared features)
```

**Stage C — Temporal + Frequency + Fusion (tiny, reuse f_i):**

```
TemporalHead:  (f_1..f_8)  → GRU(hidden=128) → mean-pool → FC → p_temporal
FrequencyHead: per-crop FFT/DCT → spectral stats (energy, high-freq ratio,
               grid variance) → small MLP over 8-frame aggregate → p_frequency
FusionHead:    [mean(f_i) | temporal_feat | frequency_feat] → MLP → p_final

Verdict:  p_final >= 0.6 → LIKELY_MANIPULATED
          0.4 <= p_final < 0.6 → INCONCLUSIVE
          else → LIKELY_AUTHENTIC
```

**Explainability (Stage D) — only for the "worst" frames:**
- Run Grad-CAM on the spatial backbone for the top-K frames by `p_i` (max 3) → save overlay PNGs
  → these become `evidence[]` entries with `heatmap_url`.

---

## 4. AnalysisResult JSON (exactly what the frontend renders)

```json
{
  "verdict": "LIKELY_MANIPULATED",
  "confidence": 0.947,
  "spatial_score": 0.93,
  "temporal_score": 0.87,
  "frequency_score": 0.76,
  "suspicious_segments": [{ "start": 12.4, "end": 15.8, "score": 0.96 }],
  "frame_scores": [0.12, 0.18, 0.21, 0.94, 0.97, 0.95],
  "evidence": [
    { "id": "ev-001", "timestamp": 12.9, "frame_number": 310,
      "evidence_type": "heatmap", "score": 0.96,
      "heatmap_url": "...", "description": "..." }
  ]
}
```

`frame_scores` are `p_i` mapped back onto the real timeline (frame_number / total_frames ×
duration). The single-frame probabilities from the spatial head, which is why we render them.

---

## 5. Training plan (4 stages, one `train.py`)

> All config lives in `config/defaults.yaml`. You only ever run:
> `python -m ml.train --stage spatial` etc.

**0. Prereqs:** `pip install -r ml/requirements.txt` (torch CPU wheel, timm, insightface,
opencv-python, numpy, scipy, pyyaml, matplotlib, pandas). Install FFmpeg (see §8). Prepare FF++:
FaceForensics++ (raw, c23, c40, DeepFakes subset → need ~2-5 GB for a demo slice).

**1. Spatial head** — the long pole.
- Data: FF++ DeepFakes + Real, aligned face crops. Load `crops` with a custom Dataset
  (`datasets.py`). Stratified split; 80/20 train/val; **cross-dataset test on Celeb-DF**.
- Train: Xception frozen backbone → train FC head first (2-3 epochs, lr 1e-3); then unfreeze
  last block + head (3-5 epochs, lr 1e-4). AdamW, batch 32, CE loss. Save
  `weights/spatial_xception.pt`.
- Eval: ACC / AUC / AP on val; save to `docs/eval.md`.

**2. Temporal head** — shares the backbone.
- Load the SAME crops per video but as a sequence of 8. Backbone weights frozen (features from
  step 1). Train GRU on the per-frame features → classify the video. ~30 min on CPU for a small
  subset. Save `weights/temporal_gru.pt`.

**3. Frequency head** — fast, CPU-friendly, no big net.
- Compute FFT/DCT stats per crop (numpy/scipy) → train small MLP. ~10 min.
  Save `weights/frequency_mlp.pt`.

**4. Fusion head** — must avoid leakage.
- **Critical:** train the fusion MLP only on a *held-out split the branch heads never saw* —
  otherwise the fusion just memorizes which head "wins" on training data and collapses on
  unseen methods. Concatenate `[mean(f_i) | temporal_feat | freq_feat]` → 2-layer MLP → p_final.
  Save `weights/fusion_mlp.pt`.

> **Eval harness (mandatory for the grade):** train on FF++ only; report ACC/AUC/AP on
> (a) FF++ val, (b) Celeb-DF, (c) DFDC sample. The cross-dataset numbers are the ones that
> prove "fusion improves robustness" — the actual SIH story.

---

## 6. What "done" looks like (acceptance)

1. `python -m ml.pipeline sample.mp4` prints the JSON + writes heatmap PNGs, ~1-2 min on your
   CPU for an 8-frame run.
2. Result JSON is schema-identical to `frontend/src/types/index.ts` `AnalysisResult`.
3. `docs/eval.md` shows ACC/AUC/AP on FF++ val AND cross-dataset Celeb-DF/DFDC.
4. Evidence PNGs (Grad-CAM overlays + aligned crops) land in an output folder the backend can
   upload to MinIO.

---

## 7. CPU-only budget & mitigations

| Step | Est. CPU time | Mitigation |
|---|---|---|
| Preprocess (ffmpeg+RetinaFace, 8 frames) | ~10-20s | sample 8 frames, not 30; run inference at most once |
| Xception forward, 8 frames | ~10-25s | batch of 8; this is the floor — no way around one real forward |
| Grad-CAM top-3 | ~15-40s | only on suspicious frames, skip when authentic |
| Temporal/Frequency/Fusion | <1s | tiny nets |
| **Total per video** | **~1-2 min** | acceptable for a demo; matches the frontend's ~2s polling nicely |

Training budget (small FF++ slice, ~5k crops): spatial 15-40 min/epoch on CPU → keep it to a
few epochs for the demo; the eval numbers are what matter, not SOTA.

---

## 8. Prereqs you must set up first

- **FFmpeg** (not installed): `winget install ffmpeg` (or Chocolatey `choco install ffmpeg`), or
  download a static build and add to PATH. Verify `ffmpeg -version`.
- **PyTorch CPU wheel:** `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` (no CUDA needed).
- **Dataset:** download a slice of FaceForensics++ (the authors' download script) or use
  `ml/data/download.py`. If FF++ access is painful, fall back to a Kaggle subset (DFDC sample /
  Celeb-DF) — the pipeline only needs a few hundred real + fake clips for a demo.

---

## 9. Suggested build order for the ML work

1. `ml/requirements.txt` + venv + FFmpeg install
2. `video_utils.py` + `face_utils.py` — verify: sample 8 frames from any mp4, detect+align a face, save crops
3. `data/download.py` + a small dataset slice + `datasets.py`
4. `backbone.py` + `spatial.py` — verify: fake/real prob on a few clips (even untrained = ~random, but the plumbing works)
5. `train.py` → train spatial → checkpoint
6. `temporal.py` → train temporal
7. `frequency.py` → train frequency
8. `fusion.py` → train fusion (held-out split!)
9. `pipeline.py` `analyze()` — the contract
10. `gradcam.py` + `overlay.py` → evidence PNGs
11. `evaluate.py` → `docs/eval.md` (FF++ val + Celeb-DF + DFDC)
12. CPU-end-to-end timing pass on a sample video

---

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| No GPU → slow training | small data slice; frozen backbone + few epochs; eval-driven |
| FF++ download/auth friction | fall back to Kaggle DFDC/Celeb-DF subsets |
| Face detector misses faces | RetinaFace is robust; skip-and-report policy per frame |
| Fusion overfits/leaks | held-out split for fusion training (non-negotiable) |
| 8 frames miss a short manipulation | suspicious_segments granularity is 8-frame limited in V1; more frames is a config change |
