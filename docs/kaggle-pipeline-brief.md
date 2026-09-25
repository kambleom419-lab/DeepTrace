# DeepTrace — Kaggle Training Pipeline Brief

> What we built, how it runs on Kaggle's cloud, what actually happened, what's done,
> and what to improve. Numbers are from the real runs, not estimates.
>
> Companion to `docs/ml-architecture.md` (the design) and `docs/ml-guide.md` (the code).
> This file is the **run record + mental model**.

---

## 0. TL;DR

We trained the full 4-head DeepTrace detector on Kaggle, on 4,690 videos
(FaceForensics++ c23 + Celeb-DF v2), with no local GPU.

**Done:** all 9 pipeline stages ran end to end. Four checkpoints packed into
`deeptrace_weights.zip`.

**Results** (ff-c23 test split, n=320): fusion AUC **0.954**, spatial 0.952,
temporal 0.948, **frequency 0.4955 — dead**.

**Open:** ⚠️ **every head trained on the eval datasets** — `TRAIN_DATASETS` is never enforced,
so all "cross-dataset" numbers are in-domain (item 8); the frequency branch needs per-feature
normalization *plus real batching* (item 1); and `fusion` selects its checkpoint on the `test`
split, so **its 0.954 is an upper bound, not an unbiased result** (E6). `eval.md` is now in
hand: it shows celeb-df scoring *higher* than ff-c23, which is the proof of the first point.

---

## Part A — What we built

### A1. The pipeline is a RELAY, not one program

A Kaggle kernel **cannot mount its own output**. So the work is split into 9 kernels,
and each one mounts the *previous* kernel's saved `/kaggle/working` as read-only input.
That's what `kernel_sources` in `kernel-metadata.json` does.

```
index → bench → crops → spatial → temporal → frequency → fusion → eval → export
```

Each stage's saved output is **cumulative** — it contains everything it restored plus
whatever it produced. So chaining one level forward carries the whole history.

### A2. The stages

| # | Stage | What it does | Output | Runtime |
|---|---|---|---|---|
| 1 | `index` | Inventories videos, caps per method, makes the split | `manifest.csv` | n/a |
| 2 | `bench` | Measures face-detection throughput to pick thread/worker settings | (nothing) | n/a |
| 3 | `crops` | Detects + aligns the largest face in 8 sampled frames per video; computes DCT stats **here**, not later | `crops/`, `fstats/`, `crop_index.csv` | **~5.5 h** |
| 4 | `spatial` | Fine-tunes Xception on single crops | `spatial_xception.pt` | **32 min** |
| 5 | `temporal` | Caches backbone features, trains a GRU over the 8-frame sequence | `temporal_gru.pt` | n/a |
| 6 | `frequency` | Trains a small MLP on the DCT stats | `frequency_mlp.pt` | ~10 min |
| 7 | `fusion` | Combines all three branches — **trained only on the `holdout` split** | `fusion_mlp.pt` | ~6 min |
| 8 | `eval` | Scores every branch per dataset + per manipulation method | `eval.md` | ~7 min |
| 9 | `export` | Zips the four checkpoints | `deeptrace_weights.zip` | fast |

`n/a` = not captured in the logs. Note every stage also pays a **restore cost** first (copying
the previous stage's ~4 GB forward), which is why the elapsed times above look large relative
to the work done.

### A3. Two design decisions worth remembering

**Why `crops` computes the frequency stats.** `pipeline.py` computes them from the
*in-memory* aligned crop at inference. If they were recomputed from JPEG files on disk,
the frequency head would learn JPEG recompression instead of manipulation artifacts.
Train-time and inference-time features must match.

**Why `fusion` trains on the `holdout` split only.** The branch heads train on `train`.
If fusion also trained there, it would memorise *which branch wins on training data*
and collapse on unseen manipulations. Training on a split the branches never saw is what
makes the robustness claim honest. This is the anti-leakage rule and it is non-negotiable.

---

## Part B — How the Kaggle side works

### B1. The two runtimes

| | GPU runtime | CPU runtime |
|---|---|---|
| Hardware | T4 x2 (we used one) | 4 vCPU |
| Quota | **metered / capped** | **unmetered** |
| Use for | training (`spatial`…`fusion`) | long CPU-bound jobs (`crops`) |

That's why `crops` was pushed with `--no-gpu`: face detection is CPU-bound, so the GPU
buys nothing, and the CPU runtime is free.

### B2. The commit window (the trap that cost us a run)

```
notebook prints "NEXT STAGE: 'frequency'"   <- the CODE is done
      ...Kaggle writes the /kaggle/working snapshot...   <- status still RUNNING
status becomes COMPLETE                     <- now the next stage can mount it
```

**The banner is printed by the notebook, before the kernel process exits.** Kaggle then
snapshots the output, which takes longer as the output grows (~4 GB of crops per stage).
Push the next stage inside that window and it mounts an *empty* `/kaggle/input` and dies
with a misleading "the earlier stage's output was not mounted" error.

**`push.py` now guards against this** — it checks the previous stage's status and refuses
to push unless it is `COMPLETE`. It fails closed on `RUNNING`/`ERROR`/`CANCEL`, and open
(continue with a warning) if the status can't be determined.

### B3. Why artifacts are copied, not symlinked

`COPY_PRIOR_ARTIFACTS = True` in the config. A symlink in `/kaggle/working` pointing at
`/kaggle/input/<slug>/...` works *this* run but **dangles in the next kernel**, silently
breaking the chain. Copying costs a few minutes of I/O and is correct.

### B4. The CLI gotcha

`push.py` finds Kaggle via `shutil.which("kaggle")` — the **standalone CLI**
(`C:\Users\OM\AppData\Roaming\Python\Python311\Scripts\kaggle.exe`, v2.2.4).
The project venv does **not** have the `kaggle` Python module, so:

```powershell
kaggle kernels status kambleom/deeptrace-crops      # correct
..\.venv\Scripts\python.exe -m kaggle ...           # FAILS: No module named 'kaggle'
```

### B5. Rate limits

The output endpoint returns **429 Too Many Requests** if called repeatedly. Commands can
*appear* to fail while the download actually succeeded. Space out download calls, and use
`kaggle kernels status` (cheap) to check progress instead of re-downloading.

---

## Part C — What actually happened

### C1. Data (`index`)

- **4,690 videos** — FaceForensics++ (c23) + Celeb-DF v2
- Splits: `train` 3,242 · `val` 444 · `test` 491 · `holdout` 513
- Labels: 2,800 fake / 1,890 real (FF++ is fake-heavy; capped per method at index time)

### C2. Cropping (`crops`) — the long pole

```powershell
..\.venv\Scripts\python.exe push.py crops --no-gpu
```

| Metric | Result |
|---|---|
| Videos kept | **4,690 / 4,690 (100%)** — none dropped |
| Frames with a face | **37,508 / 37,520 (99.97%)** |
| Full 8 crops | 4,684 videos |
| 7 crops / 1 crop | 5 / 1 videos |
| Runtime | ~5.5 h on the unmetered CPU runtime |

Measured rate: 400 videos in 17.4 min = **2.61 s/video = 0.326 s/frame**.

That is **1.4x faster than `bench` predicted** (0.453 s/frame) — because `bench` measured
the *default*-threads configuration and never measured `intra_op=1`, which is what
`crops` actually uses.

### C3. Training

| Stage | Config | Best val_acc | Eval split | Majority baseline | Checkpoint |
|---|---|---|---|---|---|
| `spatial` | Xception, 6 epochs (1 frozen + 5 finetune) | **0.8561** | `val` (444) | 0.559 | 85.64 MB |
| `temporal` | GRU(2048→128), 15 epochs | — | `val` (444) | 0.559 | 3.38 MB |
| `frequency` | MLP(256→64→32→1), 40 epochs | **0.5586** | `val` (444) | 0.559 | 0.08 MB |
| `fusion` | MLP(2432→64→32→1), 25 epochs | **0.9267** | ⚠️ **`test` (491)** | 0.548 | 0.63 MB |

⚠️ **`fusion` reports on `test`, not `val`** — see E6. Its number is therefore not
comparable to the other three, and is optimistically biased.

Spatial's trajectory shows the fine-tune working exactly as designed:

```
epoch 1/6  frozen    loss=0.6517  val_acc=0.5726   <- linear probe only
epoch 2/6  finetune  loss=0.3952  val_acc=0.8049   <- +23 points
epoch 6/6  finetune  loss=0.0827  val_acc=0.8561
```

**Frequency learned nothing.** `val_acc=0.5586` is *exactly* the majority baseline, and
`loss=0.6704` is essentially random (ln 2 = 0.6931). The signature of a model that
outputs a constant.

---

## Part D — Results (`eval`, ff-c23 test split, n = 320)

| branch | ACC | AUC | AP |
|---|---|---|---|
| spatial | 0.9125 | 0.9516 | 0.9754 |
| temporal | 0.9156 | 0.9476 | 0.9622 |
| **frequency** | **0.6781** | **0.4955** | **0.6762** |
| **fusion** | **0.9250** | **0.9541** | **0.9742** |

### How to read this honestly

- **Frequency AUC = 0.4955 = pure chance.** Worse than random. And its ACC (0.6781) is the
  *exact* majority-class rate of this split (217 fake / 320 = 0.678125) — to four decimal
  places. It predicts "fake" for everything and never varies. This is confirmed dead, not
  merely weak.
- **Spatial and temporal are genuinely strong** — AUC ~0.95 on this split.
- **Fusion beats spatial by only 0.0025** (0.9541 vs 0.9516). On 320 videos that's noise.
  With frequency dead and temporal likely correlated with spatial, the ensemble is
  currently earning very little over one branch.
- **The fusion row is also biased.** Its checkpoint was selected on this same `test` split
  (E6), so 0.9541 is the best-looking epoch rather than an unbiased estimate. Treat it as
  an upper bound until E6 is fixed.
- **This is the ff-c23 split — the training distribution.** 0.95 here is expected and
  proves little. The real test is cross-dataset.

### ⚠️ Still missing

The **`### celeb-df-v2 (n=171)`** block of `eval.md` was never captured. That is the
cross-dataset number, and it is the one the project's whole robustness argument rests on.
**Get it before drawing any conclusion:**

```powershell
kaggle kernels output kambleom/deeptrace-eval -p .\eval_out --file-pattern "eval.md"
```

---

## Part E — What went wrong, and why

### E1. `ModuleNotFoundError: No module named 'insightface'`

Rewriting cell 3 dropped `insightface` from the install line, and it is not in the Kaggle
image. **Fix:** re-added, plus a guard so a missing dependency reports plainly instead of
dumping a traceback.

### E2. "The GPU works" — it did not (the big one)

Cell 3 originally trusted `ort.get_available_providers()`. **That function reports what is
*compiled in*, not what *loaded*.** The CUDA provider `.so` failed to `dlopen` and ONNX
Runtime only wrote to stderr and fell back to CPU — no exception, nothing catchable. We
shipped a "GPU works" verdict that was false.

**Fix:** never trust the declared list. Build a one-op model, create a real session, and
read `session.get_providers()`. That is the only honest answer.

### E3. GPU face detection is impossible on this image

Three layered reasons, fully diagnosed:

1. The Kaggle image ships a **custom `onnxruntime`** (`AzureExecutionProvider` exists in no
   PyPI wheel) that **shadows** anything pip installs, since both own the module name.
2. PyPI `onnxruntime-gpu` **≥ 1.27 is built for CUDA 13**; the image has **CUDA 12.8**
   (torch is `+cu128`). The cutoff is exactly 1.27.
3. Installing a CUDA-12 build with `--target` puts its `nvidia-*` libraries somewhere the
   dynamic loader never looks, so it still fails with
   `libcublasLt.so.13: cannot open shared object file`.

**Resolution:** stop chasing it. Face detection runs on **CPU**, which is fine — it was the
only stage affected, and it was already tuned (`ORT_INTRA_THREADS=1`, `N_WORKERS=4`).
`ORT_TRY_GPU = False` in cell 2 now skips the whole wheel-download dance and goes straight
to the tuned CPU path. Set it `True` to retry.

### E4. `temporal` died with "the earlier stage's output was not mounted"

It was pushed while `spatial` was still inside its commit window (Part B2). Nothing was
wrong with the config. **Fix:** the pre-flight guard in `push.py`.

### E5. The frequency branch is degenerate

Diagnosed by measuring the features directly on real crops:

```
stat            min      max     mean     std
low_energy   0.3257   1.0000   0.9265   0.1000
high_energy  0.0000   0.1725   0.0084   0.0165   <- the actual signal
mean_mag     0.0005   0.1305   0.0654   0.0249
std_mag      0.0009   0.8508   0.5352   0.2076
```

`FrequencyHead` is a bare `nn.Linear(256, 64)` with default init. The features that carry
the manipulation signal (`high_energy`) have a mean of **0.008**, while the near-useless
DC-dominated ones sit at **0.93** — a ~100x gap, and the matrix spans a ratio of
**173,000x**. So `high_energy` contributes ~0.0005 to each pre-activation and gets no
usable gradient at `lr=1e-3`. The MLP collapses to a constant.

Two supporting facts: **27 of the 64 high-frequency cells are dead constants**
(std < 1e-4) — 8×8 DCT cells on a smooth aligned face have almost no energy up there; and
`total = sum(d**2)` includes the DC term, which then dominates, so both energy "ratios"
are really ratios *against brightness*.

### E6. `fusion` selects its checkpoint on the test split

This one was found by reading the code, not from an error message — so it has been
silently affecting every reported number.

`do_fusion` trains on `holdout` (correct — that's the anti-leakage rule) but **validates
on `test`**, and saves whichever epoch scored best on it:

```python
tr_rows = idx[idx["split"] == "holdout"].to_dict("records")
te_rows = idx[idx["split"] == "test"].to_dict("records")   # printed as "fusion val (test)"
...
if acc >= best:
    torch.save(...)          # <- checkpoint selection driven by TEST accuracy
```

The notebook prints `fusion val (test):`, so this was deliberate rather than a slip — but
it still means the `test` split does double duty as *validation* and *final report*.

**Consequences:**

- The `fusion` row in `eval.md` (AUC 0.9541) is **optimistically biased**. Part of what
  looks like fusion skill is just "we picked the epoch that looked best on this set".
- It is a live candidate explanation for why fusion beats spatial by only 0.0025 — there is
  no honest model selection happening.
- `holdout` (513 videos) is used purely for training with **no validation at all**, so
  there is no clean way to early-stop.

**The fix** — see Part F item 3.

---

## Part F — What to improve (prioritised)

Ordered by **what to run first**, not by importance: cheap and unblocking changes come
before the expensive one. Item numbers are stable — `E6` points at item 3.

| Run | Item | Change | Cost | GPU | Why now |
|---|---|---|---|---|---|
| 1 | **8** | stop training on the eval datasets | full retrain: `spatial → temporal → frequency → fusion → eval` | yes | **makes the cross-dataset claim real at all** |
| 2 | **3** | honest fusion validation split | `fusion → eval`, ~10 min | yes | every quoted fusion number depends on it |
| 3 | **1** | BatchNorm **+ real batching** | `frequency → fusion → eval`, ~10 min | yes | free fix for the dead branch |
| 4 | **7** | seed torch/numpy | bundled with 1 and 3 | — | makes "1 vs 3" comparisons valid |
| 5 | **4** | get the cross-dataset numbers | **done** — they exposed item 8 | no | it decided the plan |
| 6 | **5** | CI on the fusion-vs-spatial gap | `eval` only | yes | turns 0.0025 into a real claim |
| 7 | **2** | re-feature the DCT stats | in-`frequency` recompute, minutes | no | only if item 1 fails |
| 8 | **6** | integrate into `ml/weights/` | local | no | ships it |

Each item below flags its own traps inline (⚠️). Two of them cost a duplicate 5.5-hour run
if missed — read them before touching `build_notebook.py`.

---

### 1. Fix the frequency branch — cheap first (`BatchNorm` … but read the trap)

**Problem** (E5): the informative features (`high_energy`, mean 0.008) sit ~100x below the
DC-dominated ones (`low_energy`, 0.93). `nn.Linear(256, 64)` starts with default init, so
`high_energy` contributes ~0.0005 per pre-activation and gets no usable gradient. The MLP
collapses to a constant — the exact signature we saw.

**Change:** add a per-feature normalization at the front of both heads, in the notebook
`build_notebook.py` **and** `ml/detection/models/heads.py`:

```python
class FrequencyHead(nn.Module):
    def __init__(self, in_dim, hidden=64):
        super().__init__()
        self.net = nn.Sequential(
            nn.BatchNorm1d(in_dim),          # high_energy and low_energy both -> unit scale
            nn.Linear(in_dim, hidden),
            ...
        )
```

and `FusionHead` gets `nn.BatchNorm1d(in_dim)` as its first layer (before the `Dropout`).

It stays **lossless** — invertible per feature, ample float32 precision at 0.008 — and the
running statistics live **inside the checkpoint**, so there is no side-car normalization
file to keep in sync with `heads.py`.

⚠️ **The trap: `BatchNorm1d` needs a batch > 1, and today the loops feed exactly one video.**
`FREQUENCY_CFG["batch"] = 64` is *dead config*. `do_frequency` forwards
`model(s.unsqueeze(0))` inside a per-row loop; `do_fusion` does the same. Over a batch of 1
the per-sample variance is 0, and PyTorch does not silently degrade — it raises
`ValueError: Expected more than 1 value per channel when training`. **Adding BN without
batching does not merely fail to help: it crashes the stage.** (Verified locally against
torch 2.x.)

So the real change is to batch. Precompute each video's vector once (it is tiny —
4,690 × 256 floats ≈ 4.8 MB), then iterate real mini-batches:

```python
def load_matrix(rows):
    xs, ys = [], []
    for r in rows:
        s = stats(r["video_id"])
        if s is None:
            continue
        xs.append(s.mean(dim=0))               # (256,) — the head averages over T anyway
        ys.append(float(r["label"]))
    return torch.stack(xs).to(dev), torch.tensor(ys, device=dev)

Xtr, ytr = load_matrix(idx[idx["split"] == "train"].to_dict("records"))
Xva, yva = load_matrix(idx[idx["split"] == "val"].to_dict("records"))

for ep in range(FREQUENCY_CFG["epochs"]):
    model.train()
    perm = torch.randperm(len(Xtr), device=dev)
    for i in range(0, len(Xtr), FREQUENCY_CFG["batch"]):
        j = perm[i:i + FREQUENCY_CFG["batch"]]
        loss = lossf(model(Xtr[j]), ytr[j])    # (B,) vs (B,) — a real batch of 64
        opt.zero_grad(set_to_none=True); loss.backward(); opt.step()
    model.eval()
    with torch.no_grad():
        logits = torch.cat([model(Xva[i:i + 256]) for i in range(0, len(Xva), 256)])
    auc = roc_auc_score(yva.cpu(), logits.cpu())     # select on AUC, not ACC
```

Side benefit: this is also **~40x faster** than the current loop (no per-epoch file loads,
no per-sample forward). Do the same batching in `do_fusion` (batch the precomputed fused
vectors) and in `ml/detection/train.py`.

**While you're in there — fix checkpoint selection.** `do_frequency` and `do_fusion` both
save on `if acc >= best`: ACC is the wrong selector on an imbalanced split, and `>=`
re-saves on every tie. Select on **validation AUC** with `>` and add a `patience` early stop
that restores the best weights.

**Verify:** `val AUC > 0.60` (was chance); predicted probabilities are no longer constant
(`p.std() > 1e-3`); the `frequency` row in `eval.md` moves off 0.4955.

⚠️ **Risk:** the `state_dict` keys change (`net.0.*` → `net.1.*`), so existing
`frequency_mlp.pt` / `fusion_mlp.pt` will **not** load. Notebook, `heads.py`, and the
checkpoint archive move together — re-export `deeptrace_weights.zip` after this.

---

### 2. If that fails — fix the features (and skip the 5.5-hour re-run)

**Problem:** `total = sum(d**2)` includes the DC term, so both "energy ratios" are really
ratios *against brightness*. And 27 of the 64 high-frequency cells are dead constants
(std < 1e-4).

**Change:** kill DC once, at the source, in `freq_stats_for_crop`:

```python
d = _dct2(cell)
d[0, 0] = 0.0                     # so low_energy / high_energy are true energy ratios
total = np.sum(d**2) + 1e-12
```

⚠️ **Don't budget 3.5 h — `crops` measured ~5.5 h** (Part C2). But you do **not** need to
re-run it. `fstats/` is derived data, and crops are written as **lossless PNG**
(`CROP_FORMAT = "png"`) from the very arrays the DCT sees, so the stats can be recomputed
from `crops/*.png` and come out matching what `crops` would have produced.

Do the recompute at the **top of `do_frequency`**, behind a flag — `frequency` already has
`crops/` restored, so nothing else in the pipeline changes:

```python
REGEN_FSTATS = False          # cell 2; flip True after changing freq_stats_for_crop

def do_frequency():
    require(CROP_INDEX_CSV, "produced by the 'crops' stage")
    idx = load_crop_index()
    dev = device()
    if REGEN_FSTATS:
        for _, r in idx.iterrows():
            paths = crop_files(r["video_id"])
            if paths:
                np.save(FSTATS_DIR / (r["video_id"] + ".npy"),
                        freq_stats_for_batch([read_crop(p) for p in paths]))
    ...
```

Cost: 37,508 DCTs over 224×224 PNGs — **minutes** on the unmetered CPU runtime. Then
`frequency → fusion → eval` as usual.

⚠️ **Don't ship this as a standalone `fstats` kernel unless you put it *immediately before*
`frequency` in `push.py`'s `ORDER`.** `kernel_sources` is positional (`ORDER[idx - 1]`), so a
stage inserted anywhere else forces `spatial` and `temporal` to re-run behind it just to
carry the chain forward.

⚠️ **This equivalence only holds while `CROP_FORMAT` is lossless.** Switch it to `jpg` and
the reloaded pixels no longer match the in-memory inference path — exactly the
JPEG-recompression trap from A3.

⚠️ **One source of truth for the feature code.** `freq_stats_for_crop` exists **twice**:
in `ml/detection/models/frequency_features.py` and as an inlined copy in
`build_notebook.py` (the notebook can't import the repo). They have already drifted apart
once by construction. Have `build_notebook.py` **read the function source out of
`frequency_features.py` and inline it**, so train-time and inference-time features cannot
disagree.

---

### 3. Give `fusion` a proper validation split (run this first)

**Problem** (E6): fusion trains on `holdout` but selects its checkpoint on `test`, so `test`
does double duty as validation and final report — every fusion number is an optimistic
bound. `ml/detection/train.py::train_fusion` has the same shape (`fusion_val = te_f + te_r`)
and, worse, saves the **last** epoch with no selection at all, so local and Kaggle training
don't even match.

⚠️ **First, the relay trap: you cannot fix this by editing `index`.** `push.py` chains each
kernel to the *immediately preceding* stage only, so a new `manifest.csv` from re-pushing
`index` never reaches `fusion` unless `bench → crops → spatial → temporal → frequency` all
re-run behind it (another ~5.5 h). Push `index` and then `fusion`, and fusion mounts the
**old** frequency output — old manifest, old splits — and the fix silently does nothing.

**Change — carve the split inside `do_fusion`, from the existing `holdout` rows.** No new
manifest is needed, so the only stages to re-run are `fusion → eval`:

```python
    ho = idx[idx["split"] == "holdout"].copy()
    man = load_manifest()
    man["video_id"] = [video_id_for(r) for _, r in man.iterrows()]
    ho = ho.merge(man[["video_id", "source_id"]], on="video_id", how="left")

    # group by identity: a real clip and its manipulated twin must not straddle the split
    keys = list(zip(ho["dataset"], ho["source_id"]))
    groups = sorted(set(keys))
    rng = random.Random(SEED)
    rng.shuffle(groups)
    val_g = set(groups[int(len(groups) * 0.7):])

    tr_rows = ho[[k not in val_g for k in keys]].to_dict("records")
    va_rows = ho[[k in val_g for k in keys]].to_dict("records")
```

and select the checkpoint on `va_rows`. `test` stays untouched until `eval`. (If
`manifest.csv` isn't mounted, fall back to grouping on the stem parsed out of `video_id`.)

**Then fix `grouped_split` too**, so a *fresh* chain is honest from the start — add
`holdout_val` and split the holdout groups 70/30:

```python
        ho_g = sorted(groups[n_tr + n_va:n_tr + n_va + n_ho])
        rng.shuffle(ho_g)
        cut = int(len(ho_g) * 0.7)
        ho_train_g, ho_val_g = set(ho_g[:cut]), set(ho_g[cut:])
```

⚠️ **Also stop trusting the split baked into `crop_index.csv`.** `do_crops` writes `split`
into every row and every downstream stage reads it from there — which is exactly why split
changes look cheap but aren't. Join to the manifest at read time:

```python
def load_crop_index():
    idx = pd.read_csv(CROP_INDEX_CSV)
    man = pd.read_csv(MANIFEST_CSV)
    man["video_id"] = [video_id_for(r) for _, r in man.iterrows()]
    return idx.drop(columns=["split", "label", "dataset", "method"]).merge(
        man[["video_id", "split", "label", "dataset", "method"]],
        on="video_id", how="left")
```

Once a fresh chain has run, this is what makes later split changes cost minutes instead of
hours.

Also size it properly: 513 holdout videos split 70/30 leaves ~154 for fusion validation,
which is thin. Growing the holdout to `0.15` (taken from `train`) helps — but that *does*
need the fresh chain.

**Verify:** the fusion log no longer says "test"; training and selection sets are disjoint;
the fusion number moves **down** — it should, it stops being optimistic — and *that* is the
one you report. **Do not quote 0.9541 anywhere.**

---

### 4. Get the cross-dataset numbers — DONE, and it exposed item 8

`eval.md` is retrieved (via the output page — the API path 429s persistently). The Celeb-DF
block **does** exist; the earlier "never captured" was a download problem.

But the numbers are the real news:

| dataset | spatial | temporal | frequency | fusion |
|---|---|---|---|---|
| ff-c23 (n=320) | 0.9516 | 0.9476 | 0.4955 | 0.9541 |
| celeb-df-v2 (n=171) | 0.9649 | 0.9786 | 0.5042 | **0.9879** |

The "cross-dataset" set scores **higher than the training distribution**. A held-out dataset
should not beat the one you trained on — that is the signature of item 8, not of robustness.

**Still make the omission loud, not silent.** A missing eval dataset used to leave no trace:

```python
for ds in EVAL_DATASETS:
    if int((idx["dataset"] == ds).sum()) == 0:
        print("WARNING: EVAL_DATASET %r has 0 videos in crop_index.csv - "
              "the cross-dataset numbers will be missing" % ds)
```

Then re-pull (space the calls out — B5, 429):

```powershell
kaggle kernels output kambleom/deeptrace-eval -p .\eval_out --file-pattern "eval.md"
kaggle kernels status kambleom/deeptrace-eval
```

Check `.\eval_out\eval.md` for the `### celeb-df-v2` heading. If it's absent, the omission is
upstream in `crops`, not in the download.

---

### 5. Make fusion earn its place (stop eyeballing 0.0025)

**Problem:** 0.9541 − 0.9516 = 0.0025 on 320 videos is noise. Two rows aren't even the same
statistic: the `spatial` row averages **per-frame sigmoids** (`probs_for_video`), while
fusion consumes **mean-pooled features**. So the comparison is not apples-to-apples.

**Change:**

- Add a **paired bootstrap** over videos in `do_eval`, reporting `fusion − branch` AUC with a
  95% CI. Only claim a gain if the CI excludes 0.
- Report the spatial baseline using the **same** video-level reduction fusion sees
  (mean-pooled features → one logit), or report both reductions explicitly.

A line like `fusion − spatial = +0.0025 [−0.02, +0.03]` is the honest result; if frequency
becomes a real signal after item 1, the gap should widen on its own.

---

### 6. Then integrate

Weights go in **`ml/weights/`** — confirmed from `detection/config/paths.py`, where
`ROOT = parents[2]` → `ml/`, so `WEIGHTS_DIR = ml/weights`. (`docs/ml-guide.md`'s file tree
says `detection/weights/`; trust `paths.py`.)

```powershell
Expand-Archive .\eval_out\deeptrace_weights.zip -DestinationPath .\ml\weights -Force
cd ml
..\.venv\Scripts\python.exe -m detection.pipeline ..\samples\clip.mp4
```

Then the backend/frontend work from the plan (§24 phases 7–9).

---

### 7. Make the runs reproducible (the brief claims it; the code only half-delivers)

`cell_config` seeds **`random` only**. `torch`, `numpy`, dataloader shuffling and cuDNN are
all unseeded, so two pushes of the same `STAGE` don't reproduce — which undermines the
"1 vs 3" comparison above. Add, right after `random.seed(SEED)`:

```python
import numpy as np, torch
torch.manual_seed(SEED); torch.cuda.manual_seed_all(SEED)
np.random.seed(SEED)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
```

and use a seeded `torch.Generator` (not `random.Random`) for any shuffle that feeds the
model. Cost: near zero. Also change `if acc >= best` to `>` (ties currently re-save).

---

### 8. Stop training on the eval datasets (the real leak)

**Problem:** `TRAIN_DATASETS = ["ff-c23"]` is **only ever printed** — no training stage
filters on it. Every head trains on `split == "<its split>"` across *all* datasets:

```
celeb-df-v2 train   826  +  ff-c23 train   2416  =  3,242   (the brief's own "train 3,242")
celeb-df-v2 holdout 161  +  ff-c23 holdout  352  =    513   (fusion's train set)
```

So `spatial`, `temporal` and `frequency` each saw **826 celeb-df-v2 videos**, and `fusion`
saw 161 more. The `### celeb-df-v2` block is therefore *within-dataset* generalization, not
cross-dataset — which is exactly why it beats ff-c23 (item 4). `val` was contaminated too
(444 rows, 132 of them celeb-df), so every early stop was tuned on eval data. The
`do_fusion` log line "fusion trains on the holdout split of: TRAIN_DATASETS" named a
constant it never consulted.

**Change:** route every training/validation set through `train_rows()`:

```python
tr = train_rows(idx, "train")       # train split AND train datasets only
va = train_rows(idx, "val")         # ...so selection is in-domain as well
```

`do_fusion` uses `train_rows(idx, "holdout")`.

**The expensive consequence:** this invalidates `spatial` and `temporal`, not just frequency
— their weights contain celeb-df. The whole chain must be retrained (`spatial → temporal →
frequency → fusion → eval`). There is no shortcut; the contamination is baked into the
weights.

**Related gotcha (now handled):** `temporal` caches backbone features in `feat_cache/`, and
the relay faithfully copies that folder forward. Retraining `spatial` would therefore leave
`temporal` training on features from the *old* backbone — the same class of silent staleness.
The cache is now versioned by the spatial checkpoint's digest (`_spatial_tag()`), so a new
`spatial` checkpoint automatically starts a fresh cache.

**Verify:** after the retrain the celeb-df numbers should **fall below** the ff-c23 ones. That
drop is the result — not a regression. If they stay higher, something is still leaking.

---

### Runbook (after the code changes)

```powershell
# 1. regenerate the notebook from the builder - never hand-edit the .ipynb
..\.venv\Scripts\python.exe ml\kaggle\build_notebook.py ml\kaggle\deeptrace_kaggle_train.ipynb

# 2. item 8 + 3 + 1 + 7: full retrain without the eval datasets, honest fusion val,
#    BatchNorm frequency/fusion. Push in order; each must reach COMPLETE before the next
#    (push.py refuses otherwise - see B2).
..\.venv\Scripts\python.exe push.py spatial
..\.venv\Scripts\python.exe push.py temporal
..\.venv\Scripts\python.exe push.py frequency
..\.venv\Scripts\python.exe push.py fusion
..\.venv\Scripts\python.exe push.py eval

# 3. only if item 1 left the frequency AUC near 0.5: set REGEN_FSTATS = True, then
#    re-push frequency -> fusion -> eval (spatial/temporal ride along, untouched)
```

### Definition of done

- [ ] no head has seen an eval dataset's pixels (`train_rows()` used everywhere)
- [ ] no head selected its checkpoint on an eval dataset
- [ ] fusion validates on a split the branch heads never trained **or** tuned on
- [ ] `eval.md` contains a `### celeb-df-v2` block populated from the new weights
- [ ] frequency AUC meaningfully above 0.5 and its probabilities actually vary
- [ ] every quoted number is held-out, or is explicitly labelled an upper bound
- [ ] fusion's gain over the best single branch has a CI; if it excludes 0, claim the ensemble

### Do not

- Retry the GPU face-detection path — E3 is exhausted (`ORT_TRY_GPU = False`).
- Lower `FRAME_BUDGET` to buy speed — `do_bench` explicitly warns against it.
- Quote **0.9541**, or any fusion number produced before item 3.

### Caveat to carry into the report

FF++ **c23** is heavily compressed, and JPEG compression *deliberately discards* the
high-frequency band that this branch reads. So the frequency branch may be genuinely
weaker on c23 than published results on other datasets. That is a legitimate finding to
state, not a bug to hide.

---

## Part G — Learning resources

### G1. The frequency branch (read in this order)

1. **[3Blue1Brown — But what is the Fourier Transform?](https://www.youtube.com/watch?v=spUNpyF58BY)**
   Visual intuition that a signal is a sum of frequencies. Start here.
2. **[Computerphile — JPEG DCT, Discrete Cosine Transform](https://m.youtube.com/watch?v=Q2aEzeMDHMA)**
   The exact transform this code uses (`scipy.fftpack.dct`). Key idea: JPEG keeps
   low-frequency coefficients and **throws away high-frequency ones** — so compression and
   manipulation both live up there.
3. **[Frank et al. 2020 — Leveraging Frequency Analysis for Deep Fake Image Recognition (ICML)](https://arxiv.org/abs/2003.08685)**
   The foundational paper. GAN upsampling leaves a periodic grid in the spectrum, invisible
   in pixel space. That's what `high_energy` is trying to capture.
4. **[F3-Net — Thinking in Frequency (ECCV 2020)](https://arxiv.org/abs/2007.09355)**
   **The direct ancestor of this code.** Its second clue is *"local frequency statistics"* —
   DCT statistics over **local blocks** of the face. Our `freq_stats_for_crop` (an 8×8 grid
   of cells, 4 stats each) is a hand-rolled version of exactly that.
5. **[FreqNet — Frequency-Aware Deepfake Detection](https://arxiv.org/html/2403.07240v1)**
   Where the field went next: frequency learning aimed at **generalization** to unseen
   manipulations — your project's core argument.

### G2. ML pipeline fundamentals

Already curated in `docs/ml-guide.md` §13 — follow that list in order:

- **PyTorch for Deep Learning** (Daniel Bourke / freeCodeCamp) — the foundational course:
  tensors, `nn.Module`, the training loop, `Dataset`/`DataLoader`, checkpoints. After this,
  `heads.py` and `do_spatial` read like vocabulary.
  https://www.youtube.com/watch?v=V_xro1bcAuA · materials at learnpytorch.io
- **PyTorch Transfer Learning** (Aladdin Persson) — exactly the spatial recipe: freeze a
  pretrained backbone, swap the head, train the head, then unfreeze.
  https://www.youtube.com/watch?v=qaDe0qQZ5AQ
- **PyTorch Transfer Learning** (Patrick Loeber) — gentler alternative; clarifies what
  `requires_grad=False` actually does. https://www.youtube.com/watch?v=K0lWSB2QoIQ
- **StatQuest — RNN / GRU** — 10 minutes of intuition is enough to understand why
  `TemporalHead` uses a GRU over cached frame features.
- **Face alignment: 5 landmarks → similarity transform** — makes `face_utils.py` click.

### G3. Kaggle mechanics

- **[Kaggle API — Getting Started](https://www.kaggle.com/docs/api)** — auth, API tokens,
  CLI usage.
- **[Official Kaggle CLI](https://github.com/Kaggle/kaggle-cli)** — the tool `push.py` drives.
- **[kaggle-cli docs — kernels](https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md)**
  — `kernel-metadata.json` fields, including `kernel_sources` and `dataset_sources`.
- **[Kaggle discussion — downloading kernel output](https://www.kaggle.com/discussions/getting-started/168312)**
  — how `/kaggle/working` output is retrieved.

### G4. Concepts this pipeline exercises

| Concept | Where it lives |
|---|---|
| Transfer learning / discriminative LRs | `do_spatial` — head 1e-3, backbone 1e-4 |
| Anti-leakage holdout split | `do_fusion` |
| Feature caching (avoid recompute) | `do_temporal` — `feat_cache/` |
| Class imbalance handling | `WeightedRandomSampler` in `do_spatial` |
| GIL release for real concurrency | `crops` — threads, not processes |
| Feature scaling / normalization | **the frequency bug** |
| Metrics: ACC vs AUC vs AP | `eval.md` — always prefer AUC on imbalanced sets |
| Reproducibility (seeded, sorted) | `do_index` |
| Artifact lineage / data versioning | the relay chain + `manifest.csv` |

---

## Appendix — Commands

```powershell
# push a stage (writes STAGE into the notebook + kernel-metadata.json, then uploads)
..\.venv\Scripts\python.exe push.py <stage>

# write files only, no upload
..\.venv\Scripts\python.exe push.py <stage> --no-push

# long CPU-bound job on the unmetered runtime
..\.venv\Scripts\python.exe push.py crops --no-gpu

# status (cheap, safe to repeat)
kaggle kernels status kambleom/deeptrace-<stage>

# fetch one artifact (SPACE THESE OUT - 429 rate limit)
kaggle kernels output kambleom/deeptrace-<stage> -p .\out --file-pattern "<name>"
```

The notebook is **generated** — edit `ml/kaggle/build_notebook.py` and regenerate, never the
`.ipynb`:

```powershell
..\.venv\Scripts\python.exe ml\kaggle\build_notebook.py ml\kaggle\deeptrace_kaggle_train.ipynb
```

---

## Sources

- [But what is the Fourier Transform? A visual introduction — 3Blue1Brown](https://www.youtube.com/watch?v=spUNpyF58BY)
- [JPEG DCT, Discrete Cosine Transform (JPEG Pt2) — Computerphile](https://m.youtube.com/watch?v=Q2aEzeMDHMA)
- [Leveraging Frequency Analysis for Deep Fake Image Recognition — Frank et al., ICML 2020](https://arxiv.org/abs/2003.08685)
- [Thinking in Frequency: Face Forgery Detection by Mining Frequency-aware Clues (F3-Net) — ECCV 2020](https://arxiv.org/abs/2007.09355)
- [Frequency-Aware Deepfake Detection: Improving Generalizability through Frequency Space Learning (FreqNet)](https://arxiv.org/html/2403.07240v1)
- [Kaggle API — Getting Started](https://www.kaggle.com/docs/api)
- [Official Kaggle CLI — Kaggle/kaggle-cli](https://github.com/Kaggle/kaggle-cli)
- [kaggle-cli docs — kernels.md](https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md)
- [Multiple ways to download output file generated in Kaggle Kernel](https://www.kaggle.com/discussions/getting-started/168312)
- [PyTorch for Deep Learning — Daniel Bourke / freeCodeCamp](https://www.youtube.com/watch?v=V_xro1bcAuA)
- [PyTorch Transfer Learning and Fine Tuning — Aladdin Persson](https://www.youtube.com/watch?v=qaDe0qQZ5AQ)
- [PyTorch Tutorial: Transfer Learning — Patrick Loeber](https://www.youtube.com/watch?v=K0lWSB2QoIQ)
