# DeepTrace ML Pipeline — Complete Architecture Guide

> Written for **interview + resume prep** and for understanding every file, every dependency,
> and every design decision with first-principles reasoning. Read alongside the code — each
> section cites exact files and responsibilities.

---

## 0. Big Picture — the "Why" in one paragraph

The ML pipeline is a **CPU-first, contract-driven forensic analyzer**. It turns a single video
into the exact `AnalysisResult` JSON the frontend already renders — no UI changes needed. The
core idea is **one shared backbone, three independent evidence heads**: a spatial head reads
per-frame manipulation artifacts, a temporal head reads inconsistencies *across time*, and a
frequency head reads spectral anomalies. A tiny fusion MLP learns how to weight all three into a
final verdict. Everything is tuned for one hard reality: **your machine has no GPU** — so the
design uses a frozen ImageNet backbone + small heads instead of training giant models from
scratch.

> **Interview line:** "We used one frozen CNN backbone and three lightweight evidence heads —
> spatial, temporal, frequency — fused by a small MLP. That gives us a defensible multi-signal
> forensic story at CPU cost, and the pipeline emits the same JSON contract the frontend already
> renders, so the ML is a swappable black box."

---

## 1. What's COMPLETE vs what's REMAINING (honest status)

### ✅ Complete (verified working end-to-end)
- Full `ml/detection/` package: config, data utils, face detection+alignment, models, Grad-CAM, pipeline, train + eval CLIs.
- **FFmpeg 9.0.1 installed** (winget, full build) — frame extraction works.
- **Python 3.11 venv** with all deps installed (torch CPU, timm, insightface, opencv, etc.).
- **Face detection verified** on a real face image (RetinaFace via insightface, auto-downloaded `buffalo_l` models).
- **Pipeline smoke test PASSED** on a real-face video:
  - 8 frames sampled → face detected+aligned on each → Xception forward → full contract JSON emitted
  - Grad-CAM heatmap PNGs written (`scratch/result_astro2/heatmap_fXXX.jpg`)
  - ~20s per 8-frame video on CPU
- CLI entry points work: `python -m detection.pipeline <video>`, `python -m detection.data.prepare`.

### ⏳ Remaining (needs real data + compute)
1. **Real training data** (real + fake face videos) — this is the blocker.
2. **Train the 4 heads** (`python -m detection.train --stage all`) — spatial → temporal → frequency → fusion.
3. **Evaluate** (`python -m detection.evaluate --dataset-root ...`) → `docs/eval.md` with ACC/AUC/AP.
4. **Full-dataset robustness pass** + final timing tuning.

---

## 2. Dependencies — what we installed and WHY

`ml/requirements.txt` is the manifest; actual installed versions below.

| Package | Why | Installed version |
|---|---|---|
| **torch / torchvision** | Deep learning framework; backbone + all heads. CPU wheel (`+cpu`) — no CUDA build | 2.14.0+cpu / 0.29.0+cpu |
| **timm** | Pretrained model zoo — clean `timm.create_model("xception")` | 1.0.29 |
| **opencv-python** | Frame/image decode, resize, warp-affine alignment, heatmap overlay, PNG write | 5.0.0.93 |
| **insightface** | RetinaFace face detection + 5-point landmarks (ONNX), auto-downloads `buffalo_l` | 1.0.1 |
| **onnx / onnxruntime** | Runtime that insightface's detector runs on (CPUExecutionProvider) | 1.22.0 / 1.29.0 |
| **numpy** | Array math for features, stats, FFT/DCT support | 2.4.6 |
| **scipy** | `fftpack.dct` for the frequency head features | 1.17.1 |
| **pyyaml** | Load `config/defaults.yaml` | (installed) |
| **matplotlib / pandas / tqdm** | Eval plots, metrics tables, progress bars | (installed) |
| **sklearn** (optional) | `roc_auc_score` in eval — falls back to rank-AUC if absent | not yet |

**Key Windows setup lessons (memorize):**
- PyTorch CPU wheel install: `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`.
- **FFmpeg isn't on PATH** even after winget — the pipeline resolves it via `FFMPEG_BIN` env var → set it once per shell:
  `set FFMPEG_BIN=C:\Users\OM\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe`

---

## 3. File Tree, Explained

```
ml/
├── .gitignore              # ignores .venv, weights, data, scratch, *.mp4/jpg/png
├── requirements.txt        # pip manifest
├── config/
│   └── defaults.yaml       # ALL hyperparams/thresholds/paths in one place
├── detection/              # the importable package (run from ml/ so 'detection' resolves)
│   ├── __init__.py         # package docstring + usage
│   ├── config/
│   │   ├── __init__.py     # re-exports from paths
│   │   └── paths.py        # ROOT/WEIGHTS_DIR/DATA_DIR/SCRATCH_DIR + ensure_dirs + load_config
│   ├── data/
│   │   ├── __init__.py
│   │   ├── video_utils.py  # FFmpeg/OpenCV: metadata, frame extraction, timestamp sampling
│   │   ├── face_utils.py   # RetinaFace detect + landmark alignment -> 224x224 crops
│   │   ├── datasets.py     # find_videos, train/val splits, PyTorch Datasets
│   │   └── prepare.py      # synthetic clip generator (smoke-test data)
│   ├── models/
│   │   ├── __init__.py
│   │   ├── backbone.py     # timm Xception/ConvNeXt factory + freeze helpers
│   │   ├── heads.py        # SpatialHead / TemporalHead / FrequencyHead / FusionHead / Detector
│   │   └── frequency_features.py  # FFT/DCT stats per crop (numpy/scipy, no learned model)
│   ├── explain/
│   │   ├── __init__.py
│   │   └── gradcam.py      # Grad-CAM + heatmap overlay PNG
│   ├── pipeline.py         # Analyzer.analyze(video) -> AnalysisResult (THE contract)
│   ├── train.py            # CLI to train the 4 heads in order
│   ├── evaluate.py         # CLI: ACC/AUC/AP per dataset + cross-dataset
│   ├── weights/            # (empty, gitignored) — checkpoints land here
│   ├── runs/               # (empty) — training logs/plots
│   └── scratch/            # (ignored) — extraction frames, results, heatmaps
├── data/                   # (gitignored) — datasets live here (real/, fake/ subfolders)
└── .venv/                  # the virtualenv — never committed
```

> **Why this exact structure?** Mirror of the frontend philosophy:
> - `config/` + `paths.py` centralize every tunable and every directory — nothing is hardcoded in logic files.
> - `data/` is separated from `models/` so the pipeline can run on ANY dataset layout.
> - `pipeline.py` is the only thing the backend/CLI touches — same "single public contract" idea as `lib/api.ts` in the frontend.

---

## 4. First Principles: Why one backbone + three heads (the core design)

**The naive approach** (and why we rejected it): train three *separate* big CNNs (one for spatial,
one for temporal on frame stacks, one on spectrograms). That triples training/inference cost, needs
a GPU, and produces three unrelated feature spaces that are hard to fuse meaningfully.

**Our approach:** one ImageNet-pretrained **Xception** is the single feature extractor.
For each sampled frame it emits a 2048-dim feature vector. Then:
- **Spatial head** = small FC on each frame's vector → per-frame fake probability.
- **Temporal head** = a GRU over the *sequence* of frame vectors → catches "frame 3 is fine but the
  eye shape drifts across frames."
- **Frequency head** = DCT statistics of each crop → small MLP (catches resampling/blending artifacts
  that are invisible in RGB but obvious in the spectrum).
- **Fusion head** = concatenates the three feature representations → learns branch weighting.

This is **not** a compromise — it's the proven recipe (most FF++ methods do exactly this). And it
means the Xception forward pass happens ONCE per video, not three times.

**Why Xception over ConvNeXt-Tiny on CPU?** Xception (71M params) is ~3-4x faster per forward on
CPU than ConvNeXt at comparable FF++ accuracy, and it's the classic deepfake-detection backbone.
ConvNeXt is a config switch away (`backbone.name: convnext_tiny`).

**Why GRU over a transformer for temporal?** A video transformer needs big data + big compute to
train. A GRU over 8 pre-extracted feature vectors is ~100k params — trains in minutes on CPU and
still models order/sequence. Same "temporal inconsistency" story, 100x cheaper.

---

## 5. The Full Analysis Flow (pipeline.py `Analyzer.analyze`)

```
video.mp4
  │
  ├─ A. PREPROCESS (no ML) ─────────────────────────────────────────
  │    probe_ffprobe(video) → duration/fps/resolution
  │    sample_timestamps → 8 evenly-spaced times
  │    extract_frames_at_timestamps → 8 .jpg via FFmpeg
  │    for each frame: RetinaFace detect → align largest face → 224×224 crop
  │        (no face → frame skipped; all skipped → RuntimeError)
  │
  ├─ B. SPATIAL (the ONE big network pass) ─────────────────────────
  │    batch 8 crops → Xception → 8×(2048-d features, per-frame fake prob)
  │        feats also saved for temporal + fusion (no re-forward!)
  │
  ├─ C. TEMPORAL ───────────────────────────────────────────────────
  │    GRU over the 8 feature vectors → temporal score
  │
  ├─ D. FREQUENCY ──────────────────────────────────────────────────
  │    per-crop DCT stats (8×8 grid × 4 stats = 256-d) → MLP → freq score
  │
  ├─ E. FUSION ─────────────────────────────────────────────────────
  │    concat(spatial-mean-feat | temporal-hidden | freq-feat)
  │        → MLP → final confidence
  │
  ├─ F. VERDICT (thresholds) ───────────────────────────────────────
  │    p ≥ 0.6 → LIKELY_MANIPULATED
  │    0.4 < p < 0.6 → INCONCLUSIVE
  │    p ≤ 0.4 → LIKELY_AUTHENTIC
  │
  ├─ G. EXPLAINABILITY (top-K suspicious frames only) ──────────────
  │    Grad-CAM on Xception conv4.pointwise → overlay heatmap PNG → evidence[]
  │
  └─ H. OUTPUT JSON (matches frontend AnalysisResult + _meta) ──────
       verdict, confidence, spatial/temporal/frequency_score,
       suspicious_segments[], frame_scores[], evidence[], _meta
```

**The `_meta` bonus key:** filename, duration, fps, resolution, frames_sampled, timing_s — the
backend will need this for the report screen (video info table).

---

## 6. Model Head Details (what each file owns)

### `models/backbone.py`
- `build_backbone(name, pretrained)` → timm model with `num_classes=0` (features only).
- `freeze_backbone(model, unfreeze_last_block)` → CPU budget: freeze everything; optionally unfreeze `block14` for fine-tune.

### `models/heads.py` (all four heads + composite)
- **`Detector`** — the composite the pipeline loads: `backbone + SpatialHead`. Exposes
  `forward_with_feats(x) -> (feats, logits)` so the pipeline gets features AND spatial score in one call.
- **`SpatialHead`** — `Dropout → Linear(2048→256) → ReLU → Dropout → Linear(256→1)`.
- **`TemporalHead`** — `GRU(2048→128) → mean-pool → FC → score`. Takes the (T, D) feature sequence.
- **`FrequencyHead`** — `Linear(256→64) → ... → 1`. Input = DCT stats.
- **`FusionHead`** — concat input `(2048 + 128 + 256)` → MLP → final.

### `models/frequency_features.py`
- `freq_stats_for_crop(rgb)` → grayscale → split 8×8 grid → per-cell 2D DCT → keeps 4 stats
  (low-freq energy ratio, high-freq ratio, mean magnitude, std) → 256-d vector.
- No learned weights — pure numpy/scipy. This is the "forensic signal" branch.

### `explain/gradcam.py`
- `GradCAM(model, target_layer)` — hooks the last conv (`backbone.conv4.pointwise` for Xception),
  forward/backward, channel-average weighted activation → (H,W) CAM.
- `overlay_heatmap(image, cam)` → Jet colormap blended at α=0.55 → saved as evidence PNG.

---

## 7. Training Plan (train.py) — the leakage-safe order

| Stage | What learns | Input | Output ckpt | CPU time |
|---|---|---|---|---|
| **spatial** | Xception head (backbone frozen) | single crops → label | `weights/spatial_xception.pt` | 15-40 min/epoch |
| **temporal** | GRU (backbone frozen, features cached) | 8-frame feature seq → label | `weights/temporal_gru.pt` | minutes |
| **frequency** | MLP over DCT stats | DCT stats → label | `weights/frequency_mlp.pt` | ~10 min |
| **fusion** | MLP over concat features | 3-branch feats → label | `weights/fusion_mlp.pt` | minutes |

**The anti-leakage rule (critical):** branch heads train on split A. Fusion trains ONLY on a
**held-out split B** that the branch heads never saw. Otherwise fusion memorizes "which head wins
on training data" and collapses on unseen manipulations. This is baked into `train_fusion()`'s
3-way split.

**Dataset contract:** `dataset_root/real/*.mp4` + `dataset_root/fake/*.mp4`. `find_videos()` scans
them into labeled samples; `split_samples()` does a stratified 80/20. Each video's face crops are
cached under `scratch/crops/<name>/` after first extraction so repeated epochs don't re-run RetinaFace.

---

## 8. How the ML connects to frontend / backend / DB (the handoff)

| Concern | Frontend mock today | ML pipeline now | Backend/DB tomorrow |
|---|---|---|---|
| Analysis result | `mocks/fixtures.ts` `mockResult` | `pipeline.py` returns the SAME shape | worker stores into `analysis_results` |
| Heatmaps | placeholder | `heatmap_fXXX.jpg` PNGs on disk | uploaded to MinIO → `heatmap_url` |
| Video metadata | `VideoMeta` fixture | `_meta` key (duration/fps/resolution) | `videos` table row |
| Job progress | MSW advances stageIndex | train/eval CLIs | Celery worker calls `analyze()` |
| Scores | mock 0.93/0.87/0.76 | real spatial/temporal/frequency scores | `analysis_results` columns |

**The single contract to keep identical:**
```json
{ verdict, confidence, spatial_score, temporal_score, frequency_score,
  suspicious_segments[], frame_scores[], evidence[] }
```
`evidence[].heatmap_url` will become a MinIO key; `frame_scores` maps to the timeline chart.

---

## 9. Verified Run Example (what "it works" looks like)

Command:
```
python -m detection.pipeline data/synthetic/real/face_astronaut.mp4 --out scratch/result_astro2
```
Result (untrained weights — hence ~0.5 scores pulled to a real prior):
```json
{
  "verdict": "LIKELY_AUTHENTIC", "confidence": 0.2405,
  "spatial_score": 0.5317, "temporal_score": 0.1, "frequency_score": 0.15,
  "suspicious_segments": [{ "start": 0.0, "end": 7.0, "score": 0.5341 }],
  "frame_scores": [0.5326, 0.532, ...],
  "evidence": [ { "id": "ev-001", "timestamp": 5.0, "frame_number": 5,
    "evidence_type": "heatmap", "score": 0.5341, "heatmap_url": "heatmap_f005.jpg", ... } ],
  "_meta": { "filename": "...", "duration": 8.0, "fps": 15.0,
    "resolution": "768x576", "frames_sampled": 8, "timing_s": 20.49 }
}
```
Once trained, temporal/frequency scores become real (not priors) and confidence reflects true
manipulation probability.

---

## 10. What I need from YOU (specific requirements)

1. **A real dataset** with `real/` + `fake/` subfolders of **video files** (`.mp4`/`.mov`), each ~2-10s, faces clearly visible, ~50-300 clips per class minimum. (Kaggle "DFDC"-style sets or FaceForensics++ DF subset.) Tell me the folder path when ready.
2. **Storage:** ~1-5 GB free for the dataset + crops cache.
3. **Time to run training** on your CPU (spatial head is the long pole — can be hours total; we can also do a "mini-train" on 50+50 clips for a quick demo first).
4. **Optional but helpful:** the Kaggle page link if you pick a set there, so I can verify it's video-based (many "deepfake" Kaggle sets are images only).

---

## 11. Interview One-Liners (memorize)

- **Why one backbone + 3 heads?** "One frozen Xception extracts features once; three tiny heads
  read different evidence — per-frame artifacts, temporal drift, spectral anomalies. Cheaper and
  more defensible than three giant models."
- **Why Xception on CPU?** "3-4x faster per forward than ConvNeXt at comparable FF++ accuracy;
  ConvNeXt is a one-line config swap."
- **Why GRU not a transformer?** "A transformer needs GPU-scale data; a GRU over 8 feature vectors
  is ~100k params, trains in minutes on CPU, and still captures order."
- **Why frequency branch?** "Resizing/blending artifacts are subtle in RGB but obvious in the DCT
  spectrum — a cheap forensic signal with no learned vision backbone."
- **Why train fusion on a held-out split?** "Otherwise it memorizes which head wins on training
  data and collapses cross-dataset. Held-out fusion is what makes the robustness claim honest."
- **Why Grad-CAM?** "Forensics needs to show WHERE the model saw evidence, not just a score —
  that's the difference between an ML demo and an investigation tool."

---

## 12. Common Gotchas We Hit (war stories)

1. **`face.score` is None on buffalo_l** → naive `score < min_score` crashed. Fix: treat missing
   score as accept. Real robustness lesson: library model packs differ in what fields they fill.
2. **timm Xception layer names ≠ keras Xception** → `block14` doesn't exist; last conv is
   `conv4.pointwise`. Fix: inspect `named_modules()` for the real last Conv2d.
3. **RetinaFace misses tiny faces** → the 112×112 insightface test image returns 0 faces, but a
   512×512 face detects fine. Lesson: real datasets need reasonably large faces.
4. **FFmpeg not on PATH after winget** → set `FFMPEG_BIN` env var to the full exe path.
5. **`testsrc2` ffmpeg filter rejects extra options** (`seed`, `decimal`) → keep lavfi sources minimal.
6. **Frame extraction time can exceed 60s timeout** on long/4K videos → watch for `timeout` in
   `extract_frames_at_timestamps`.

---

## 13. YouTube Learning Path (recommended videos)

Watch in this order — each maps directly to files in our repo.

**1. PyTorch for Deep Learning (Daniel Bourke / freeCodeCamp, 26h)** — THE foundational course.
Covers tensors, `nn.Module`, the training loop, custom `Dataset`/`DataLoader`, saving checkpoints.
After this, our `train.py`, `heads.py`, `datasets.py` will read like familiar vocabulary.
https://www.youtube.com/watch?v=V_xro1bcAuA (materials: learnpytorch.io)

**2. PyTorch Transfer Learning (Aladdin Persson, ~20 min)** — exactly our spatial-stage recipe:
freeze a pretrained backbone, swap the head, train only the head. Shows both "feature extractor"
(frozen) and "fine-tune last block" — both patterns exist in our `backbone.py`.
https://www.youtube.com/watch?v=qaDe0qQZ5AQ

**3. PyTorch Tutorial — Transfer Learning (Patrick Loeber)** — alternative, gentle walkthrough
using ResNet on ants/bees; clarifies why we freeze and what `requires_grad=False` does.
https://www.youtube.com/watch?v=K0lWSB2QoIQ

**4. GRU / RNN intuition (StatQuest)** — a 10-min conceptual video is enough to understand why our
`TemporalHead` uses `nn.GRU` over frame features. Search "StatQuest RNN" / "StatQuest GRU".

**5. Insightface / face alignment (optional)** — most is hidden by the library, but a 5-min read of
"5 facial landmarks → similarity transform" makes `face_utils.py` click. Search "face alignment landmarks affine transform".

**Suggested order to pair with our code:**
1. Bourke's course sections 00-06 (tensors → training loop) → re-read `heads.py` + `train.py`
2. Aladdin's transfer learning → re-read `backbone.py` + the spatial trainer
3. StatQuest GRU → re-read `TemporalHead`
4. Then you're ready to run `python -m detection.train --stage all` on real data and understand every line

Sources:
- [PyTorch for Deep Learning — Daniel Bourke / freeCodeCamp](https://www.youtube.com/watch?hv=V_xro1bcAuA)
- [PyTorch Transfer Learning and Fine Tuning — Aladdin Persson](https://www.youtube.com/watch?v=qaDe0qQZ5AQ)
- [PyTorch Tutorial: Transfer Learning — Patrick Loeber](https://www.youtube.com/watch?v=K0lWSB2QoIQ)
