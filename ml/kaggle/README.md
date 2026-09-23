# DeepTrace — Kaggle training

`deeptrace_kaggle_train.ipynb` trains the four DeepTrace heads on a Kaggle GPU and
exports checkpoints that the **existing local `ml/detection/pipeline.py` loads
unchanged**. Nothing in the architecture changes — the notebook's ports of the model
classes were verified to produce byte-identical `state_dict` keys and shapes.

## Why train on Kaggle

The local machine is CPU-only (Intel UHD, no NVIDIA GPU). Training the spatial head
locally is hours per epoch; on a Kaggle T4 it is minutes. The notebook fine-tunes the
whole Xception backbone, which the CPU config explicitly leaves frozen
(`backbone.train_backbone: false`). The saved checkpoint still contains
`backbone.*` + `spatial.*`, so the pipeline loads it without any code change.

## Datasets to attach

Add these via **Add Input → Datasets** before running.

| Rank | Kaggle dataset | Role | Notes |
|---|---|---|---|
| 1 | `xdxd003/ff-c23` | **train** | FaceForensics++ c23. ~7000 **mp4** files: `original/` (1000 real) + `Deepfakes/`, `Face2Face`, `FaceSwap`, `NeuralTextures`, `DeepFakeDetection`, `FaceShifter`. The workhorse. |
| 2 | `reubensuju/celeb-df-v2` | **cross-dataset test** | 590 real + 5639 fake (`Celeb-real`, `Celeb-synthesis`, `YouTube-real`). The standard "does it generalise?" benchmark. |
| 3 | competition `deepfake-detection-challenge` | **cross-dataset test** | Requires clicking **Accept Rules** on the competition page. Labels come from its `metadata.json` (the notebook reads it). |

Not on Kaggle (reference only):

- **DF40** (`YZY-stack/DF40`) — 40 modern manipulation techniques, request-based. The
  strongest "unseen method" test if you can obtain it.
- **DeeperForensics-1.0**, **WildDeepfake** — GitHub registration/request only.
- **DeepfakeBench** (`SCLBD/DeepfakeBench`) — benchmark framework + dataset zoo.

### Watch out

Many Kaggle "deepfake" datasets are **image frame dumps**, not videos
(e.g. `pranabr0y/celebdf-v2image-dataset`). They cannot feed the temporal branch and
break the `real/*.mp4` / `fake/*.mp4` contract. The notebook only indexes video files —
if a mounted dataset contributes ~0 videos, it is an image dump.

Turn **Internet ON** (notebook Settings) — needed for `pip install insightface`, the
`buffalo_l` face-model download, and timm pretrained weights.

## How to run — VS Code + kaggle CLI

`push.py` sets `STAGE` in the notebook, writes `kernel-metadata.json`, and pushes — so
you never hand-edit the metadata between stages:

```
cd ml/kaggle
python push.py index
```

Run the stages one at a time to inspect each result:

```
python push.py index       # -> manifest.csv                (fast)
python push.py crops       # -> crops/ + fstats/            (slow, hours)
python push.py spatial     # -> weights/spatial_xception.pt (GPU, long pole)
python push.py temporal    # -> weights/temporal_gru.pt
python push.py frequency   # -> weights/frequency_mlp.pt
python push.py fusion      # -> weights/fusion_mlp.pt
python push.py eval        # -> eval.md
python push.py export      # -> deeptrace_weights.zip
```

Each push gets its own kernel id and automatically sets `kernel_sources` to the
preceding stage, e.g. `deeptrace-crops` mounts `deeptrace-index`. A kernel cannot mount
its own output, which is why every stage is a separate kernel.

Watch it and pull the results:

```
kaggle kernels status kambleom/deeptrace-index

mkdir out
kaggle kernels output kambleom/deeptrace-index -p ./out --file-pattern "(manifest\.csv|eval\.md|deeptrace_weights\.zip)"
```

`-p` means the **source** folder for `push`, but the **destination** folder for
`output`. Omitting `--file-pattern` downloads everything in `/kaggle/working`,
including `crops/`, `fstats/` and `feat_cache/` — several GB.

Once things are proven, group the fast stages into one push to save GPU hours:

```
python push.py temporal    # (edit STAGE to "temporal,frequency,fusion" by hand)
```

or just run `python push.py <stage>` then change `STAGE` to a comma-separated list in
cell 2 and push again.

### Continuing between pushes

`kernel_sources` mounts the earlier kernel's saved `/kaggle/working` **read-only at
`/kaggle/input/<slug>/`**. The notebook's `main()` calls `restore_prior_artifacts()`
first, which copies those artifacts into `/kaggle/working` so the next stage finds them.

It **copies**, not symlinks (`COPY_PRIOR_ARTIFACTS = True`). A symlink created in
`/kaggle/working` points at `/kaggle/input/<slug>/...`, and that path does not exist in
the *next* kernel — so a symlinked chain breaks as soon as it is more than one hop deep.

### Adding DFDC

FF++ and Celeb-DF are wired in `dataset_sources`. DFDC is a **competition**, so:

1. Accept the rules at <https://www.kaggle.com/c/deepfake-detection-challenge/rules>.
2. `python push.py index --competition-sources deepfake-detection-challenge`

Its data ships as `train_sample_videos.zip`, not a folder of videos. The notebook
unzips any archive under `/kaggle/input` into `/kaggle/working/extracted/` during the
`index` stage and reads labels from its `metadata.json`, so it indexes with no manual
step. An inaccessible competition source fails the push — so only add it after the
rules are accepted.


Stage outputs in `/kaggle/working`:

| Path | Produced by | Used by |
|---|---|---|
| `manifest.csv` | index | crops |
| `crops/<video_id>/*.png` | crops | spatial |
| `fstats/<video_id>.npy` | crops | frequency, fusion, eval |
| `feat_cache/<video_id>.pt` | temporal | temporal, fusion, eval |
| `weights/*.pt` | spatial/temporal/frequency/fusion | eval, export |
| `eval.md` | eval | you |

Two design choices worth knowing:

- **Lossless PNG crops.** Cuts are saved as PNG (not JPEG) so training pixels match the
  in-memory aligned crop the pipeline uses at inference.
- **Frequency features are dumped at crop time** (`fstats/*.npy`) rather than recomputed
  from disk. Computing them from re-encoded JPEGs would make the frequency head learn
  JPEG artifacts instead of manipulation artifacts.

Budget: `MAX_VIDEOS_PER_METHOD=400` + `MAX_REAL_VIDEOS=1000` ≈ 3400 videos ≈ 27k crops
≈ 2.7 GB. Kaggle gives ~20 GB on `/kaggle/working` and ~9-12 h per GPU session.

## Getting the weights back into the repo

Copy the four files from the notebook Output pane (`deeptrace_weights.zip`) into:

```
ml/weights/spatial_xception.pt
ml/weights/temporal_gru.pt
ml/weights/frequency_mlp.pt
ml/weights/fusion_mlp.pt
```

Note the location: **`ml/weights/`**, not `ml/detection/weights/`. This was a real trap —
`detection/config/paths.py` used to resolve its root to `ml/detection`, which silently
made every checkpoint invisible (the pipeline falls back to untrained priors with no
error). Fixed; the paths helper now resolves to `ml/`.

Then verify:

```bat
cd ml
set FFMPEG_BIN=C:\Users\OM\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe
.venv\Scripts\python -m detection.pipeline path\to\test.mp4 --out scratch\result_test --json scratch\result.json
```

If `temporal_score`, `frequency_score` and `confidence` still equal the untrained priors
(`0.10`, `0.15`, `0.11`), a checkpoint did not load — the pipeline prints
`[pipeline] warn: could not load ...` to stderr when that happens.

### Sanity checks before trusting the numbers

- `spatial_score` should clearly separate a known-real from a known-fake clip.
- Run the pipeline on one FF++ clip **and** one Celeb-DF clip from the test split. If
  FF++ looks strong and Celeb-DF sits near 0.5, that is the expected cross-dataset gap —
  reportable, not a bug.
- `eval.md` reports ACC/AUC/AP per dataset and per manipulation method, so you can state
  the cross-dataset numbers rather than only in-domain accuracy.
