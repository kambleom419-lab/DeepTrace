"""Build ml/kaggle/deeptrace_kaggle_train.ipynb from the cell sources below.

This file is the single source of truth for the notebook. Edit a cell here, then
regenerate - do not hand-edit the .ipynb, or the next regeneration will discard it.

    python build_notebook.py deeptrace_kaggle_train.ipynb

The cell strings are raw triple-quoted strings so backslashes survive; a cell body
must therefore not itself contain a triple quote. Use # comments instead of
docstrings inside cells.

push.py edits STAGE in the generated notebook in place; regenerating resets it to
"index", which is the intended default.
"""
import json, sys
from pathlib import Path

OUT = Path(sys.argv[1])

md_intro = r"""# DeepTrace - Kaggle training notebook

Trains the four DeepTrace heads on GPU and exports checkpoints that the **local**
`ml/detection/pipeline.py` loads **unchanged**.

## What this notebook produces

| File | Trained by | Consumed by |
|---|---|---|
| `weights/spatial_xception.pt` | Xception + SpatialHead (`Detector`) | `Analyzer._load_models` |
| `weights/temporal_gru.pt` | `TemporalHead` (GRU over frame feats) | `_load_head` |
| `weights/frequency_mlp.pt` | `FrequencyHead` (DCT stats MLP) | `_load_head` |
| `weights/fusion_mlp.pt` | `FusionHead` (concat -> MLP) | `_load_head` |

Each is saved as `{"state_dict": ..., "config": ...}` - exactly the format
`ml/detection/train.py` writes and `pipeline.py` reads.

## Architecture is FROZEN

The local pipeline builds models with fixed shapes. The ports in cell 4/5 are
**verbatim** copies of `ml/detection/models/*.py`:

* backbone `timm.create_model("xception", num_classes=0)` -> 2048-d features
* `SpatialHead(2048, dropout=0.3)` -> 256 -> 1
* `TemporalHead(2048, hidden=128, layers=1, dropout=0.3)`
* `FrequencyHead(256, hidden=64)`
* `FusionHead(2048+128+256=2432, hidden=64, dropout=0.3)`

If you change any dimension here the local pipeline will fail to load the weights.
Keep them in sync with `ml/detection/models/heads.py`.

## Staging (important)

A single session cannot do everything (Kaggle caps a GPU session at ~9-12h).
Run one stage group per push, and for the next group point `kernel_sources` at the
previous kernel in `kernel-metadata.json` - that mounts its saved `/kaggle/working`
read-only at `/kaggle/input/<slug>/`, and `main()` restores it automatically.

```
cd ml/kaggle && kaggle kernels push -p .
```

Recommended order:

```
session 1: index -> crops        (CPU is fine; this is the slow, I/O-heavy part)
session 2: spatial               (GPU, the long pole)
session 3: temporal -> frequency -> fusion
session 4: eval -> export
```

Set `STAGE` in cell 2. `STAGE="all"` only makes sense on a small subsample.
"""

md_datasets = r"""## Datasets to attach

Add these under **Add Input -> Datasets** before running:

| Rank | Kaggle dataset | Role | Notes |
|---|---|---|---|
| 1 | `xdxd003/ff-c23` | **train** | FaceForensics++ c23, ~7000 **mp4** files: `original/` (1000 real) + `Deepfakes/`, `Face2Face/`, `FaceSwap/`, `NeuralTextures/`, `DeepFakeDetection/`, `FaceShifter/`. The workhorse. |
| 2 | `reubensuju/celeb-df-v2` | **cross-dataset test** | 590 real + 5639 fake. The standard "does it generalize?" benchmark. |
| 3 | `deepfake-detection-challenge` (competition data) | **cross-dataset test** | Needs "Accept Rules" on the competition page. Use `train_sample_videos.zip` (~400 labeled videos + `metadata.json`). |

Optional / not attachable:

* **DF40** (`YZY-stack/DF40`) - 40 modern manipulation techniques, request-based, not on
  Kaggle. The strongest "unseen method" test if you can get it.
* **DeeperForensics-1.0**, **WildDeepfake** - GitHub request/registration only.
* **DeepfakeBench** - framework + dataset zoo (SCLBD), useful reference not a dataset.

### Watch out

Many Kaggle "deepfake" datasets are **image frame dumps**, not videos
(e.g. `pranabr0y/celebdf-v2image-dataset`). They cannot feed the temporal branch and
break the `real/*.mp4` / `fake/*.mp4` contract. This notebook auto-detects video files
only - if a dataset mounts but contributes ~0 videos, it is an image dump.

Also confirm the notebook has **Internet ON** (Settings -> Internet) - needed for
`pip install insightface`, the `buffalo_l` face model download, and timm pretrained weights.
"""

cell_config = r"""# =============================================================
# CELL 2 - CONFIG
# =============================================================
import os, sys, json, math, random, shutil, zipfile, warnings
from pathlib import Path

warnings.filterwarnings("ignore")

STAGE = "index"
# A single stage, a comma-separated group, or "all".
#   "index"      scan the attached datasets -> manifest.csv
#   "bench"      measure ONNX Runtime face-detection throughput at 1/2/4 threads
#   "crops"      detect+align faces -> crops/ + fstats/          (slow, hours)
#   "spatial"    train Xception + spatial head                   (GPU, long pole)
#   "temporal"   cache backbone features, train the GRU
#   "frequency"  train the DCT-stat MLP
#   "fusion"     train the fusion MLP on the held-out split
#   "eval"       cross-dataset metrics -> eval.md
#   "export"     zip the four checkpoints
# Groups that fit one Kaggle session:
#   "temporal,frequency,fusion"     "eval,export"
# "all" runs everything in one push - only sensible on a small subsample.

WORK  = Path("/kaggle/working")
INPUT = Path("/kaggle/input")

FACE_SIZE    = 224
FRAME_BUDGET = 8
SEED         = 42

# Set True to ATTEMPT the GPU face-detection path (cell 3 installs a CUDA-12 build
# into a private dir and probes whether CUDA really activates). On Kaggle this does
# not work: the image shadows pip-installed onnxruntime, and the CUDA provider cannot
# load - the PyPI wheels are built for CUDA 13 while the image ships CUDA 12.8.
# False skips several minutes of downloads on every push and goes straight to the
# tuned CPU path. See ml/kaggle/README.md.
ORT_TRY_GPU = False

# Face detection runs on ONNX Runtime, so the choices differ by device.
#
# These values are the CPU-only path: ORT's CPU EP defaults to one intra-op thread
# per core, and measured on a real box that is SLOWER per frame than a single thread
# (sync overhead dominates for this model). So: one thread per session, one session
# per core.
#
# Cell 3 overrides both if it ever finds a working CUDA provider, because the GPU
# path wants ORT's default threading and little concurrency. STAGE="bench" measures
# the real throughput on the machine you got - trust it over these values.
ORT_INTRA_THREADS = 1
N_WORKERS         = max(1, (os.cpu_count() or 4))

# lossless crops: training pixels then match inference pixels (inference uses the
# in-memory aligned crop, never a JPEG on disk).
CROP_FORMAT = "png"

# Subsample caps. FF++ c23 is 6:1 fake:real, so real is capped separately.
# 400 x 6 methods + 1000 real = ~3400 videos -> ~27k crops -> ~2.7 GB. Raise if you
# have disk/time; the full 7000 videos is ~5.6 GB of PNG crops.
MAX_VIDEOS_PER_METHOD = 400
MAX_REAL_VIDEOS       = 1000

TRAIN_DATASETS = ["ff-c23"]
EVAL_DATASETS  = ["celeb-df-v2", "dfdc"]

# grouped (identity-aware) split fractions per dataset
SPLIT = dict(train=0.70, val=0.10, holdout=0.10, test=0.10)

BACKBONE = "xception"

SPATIAL_CFG   = dict(epochs=6, frozen_epochs=1, batch=32, lr_head=1e-3, lr_backbone=1e-4, dropout=0.3)
TEMPORAL_CFG  = dict(epochs=15, hidden=128, layers=1, dropout=0.3, batch=32, lr=1e-3)
FREQUENCY_CFG = dict(epochs=40, hidden=64, batch=64, lr=1e-3)
FUSION_CFG    = dict(epochs=25, hidden=64, dropout=0.3, batch=64, lr=1e-3)

WEIGHTS_DIR  = WORK / "weights"
CROPS_DIR    = WORK / "crops"
FSTATS_DIR   = WORK / "fstats"
FEATCACHE_DIR = WORK / "feat_cache"
MANIFEST_CSV  = WORK / "manifest.csv"
CROP_INDEX_CSV = WORK / "crop_index.csv"
EXTRACT_DIR   = WORK / "extracted"   # DFDC ships as a .zip; we unzip it here

# How to bring a previous stage's artifacts out of read-only /kaggle/input into
# /kaggle/working. True = copy. Keep it True for chained pushes: a symlink in
# /kaggle/working points at /kaggle/input/<slug>/..., and that target does not exist
# in the NEXT kernel, so the chain breaks. Copying costs disk/time but is correct.
COPY_PRIOR_ARTIFACTS = True

for _d in (WEIGHTS_DIR, CROPS_DIR, FSTATS_DIR, FEATCACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

random.seed(SEED)
print("torch cuda available:", __import__("torch").cuda.is_available())
print("work dir:", WORK)
"""

cell_install = r"""# =============================================================
# CELL 3 - DEPENDENCIES
# =============================================================
# Kaggle images already ship torch, torchvision, timm, opencv, scipy, pandas, sklearn.
# We only add insightface (RetinaFace + 5 landmarks) and its ONNX runtime.
import subprocess, sys

def sh(cmd):
    print("$", cmd)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if out:
        print("\n".join(out.splitlines()[-8:]))
    return r

# Face detection runs on ONNX Runtime, NOT on PyTorch - so torch.cuda.is_available()
# being True says nothing about it. The Kaggle image ships a custom onnxruntime
# (AzureExecutionProvider exists in no PyPI wheel) and it shadows anything installed
# into site-packages, since both distributions own the same module name. Workaround:
# install a build into a PRIVATE directory and put that directory FIRST on sys.path.
#
# WHICH build matters, because CUDA majors are not interchangeable. Per onnxruntime's
# own docs: "Starting with version 1.27, GPU packages published to PyPI
# (onnxruntime-gpu) are built with CUDA 13.0 by default. Older GPU package versions
# are built with CUDA 12.8 by default."
#   * 1.27+ wants libcublasLt.so.13 / libcudart.so.13, which this image (CUDA 12.8,
#     torch +cu128) does not have. It installs fine and then fails to dlopen its
#     provider when a session is created - ONNX Runtime only writes to stderr and
#     quietly falls back to CPU. You cannot catch that as an error.
#   * <1.27 is a CUDA 12.8 build: it matches this image AND the torch already loaded
#     in this process, which matters because mixing CUDA 12 (torch) and CUDA 13 (ORT)
#     in one process is its own source of trouble.
# So candidates are tried in order, and each is PROBED for real before being kept.
# get_available_providers() is never trusted: it reports what is compiled in.
# insightface is also NOT in the Kaggle image.
sh(f"{sys.executable} -m pip install -q insightface")

ORT_DIR = "/tmp/ort_gpu"

# (pip requirement, extra flags, why we try it)
ORT_CANDIDATES = [
    ("onnxruntime-gpu<1.27", "",
     "CUDA 12 build + its own CUDA 12 libraries - matches this image"),
    ("onnxruntime-gpu<1.27", "--no-deps",
     "same build, rely on the image's CUDA 12.8 - if the download is too large"),
    ("onnxruntime-gpu", "",
     "CUDA 13 build (1.27+), last resort - needs its own CUDA 13 libraries"),
]

# Must run in a FRESH interpreter: onnxruntime cannot be swapped in-process once a
# provider has been loaded. Prints ACTIVE=<json list of providers a session really got>.
PROBE_SRC = '''
import ctypes, glob, json, os, sys, sysconfig
sys.path.insert(0, "__ORT_DIR__")

# ONNX Runtime's CUDA provider dlopens libcublasLt / libcudart / libcudnn BY NAME.
# Those ship in the pip nvidia-* packages, but --target put them under
# <ORT_DIR>/nvidia/*/lib, which is not on the loader's search path - so the by-name
# dlopen fails with "cannot open shared object file" and ORT silently falls back to
# CPU. Preloading each one by ABSOLUTE path with RTLD_GLOBAL registers its SONAME
# process-wide, so the later by-name dlopen is satisfied by the loaded copy.
# Two passes, so a library's own dependencies can resolve on the second attempt.
roots = ["__ORT_DIR__", sysconfig.get_paths()["purelib"],
         os.path.join(sysconfig.get_paths()["purelib"], "torch", "lib")]
libs = set()
for root in roots:
    libs |= set(glob.glob(os.path.join(root, "nvidia", "**", "*.so*"), recursive=True))
if "__ORT_DIR__" != roots[1]:
    libs |= set(glob.glob(os.path.join("__ORT_DIR__", "**", "*.so*"), recursive=True))
libs = sorted(libs)
print("NV_LIBS_FOUND=" + str(len(libs)))
loaded = set()
for _ in range(2):
    for so in libs:
        if so in loaded:
            continue
        try:
            ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
            loaded.add(so)
        except OSError:
            pass
print("PRELOAD_FAILED=" + json.dumps([os.path.basename(s) for s in libs
                                      if s not in loaded][:12]))

try:
    import onnxruntime as ort
except Exception as e:
    print("PROBE_FAILED=" + repr(e)); raise SystemExit(0)
try:
    ort.preload_dlls()                # ORT's own version of the same idea
except Exception:
    pass
try:
    from onnx import TensorProto, helper
    g = helper.make_graph(
        [helper.make_node("Add", ["a", "b"], ["c"])], "probe",
        [helper.make_tensor_value_info("a", TensorProto.FLOAT, [1]),
         helper.make_tensor_value_info("b", TensorProto.FLOAT, [1])],
        [helper.make_tensor_value_info("c", TensorProto.FLOAT, [1])])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    so = ort.SessionOptions(); so.log_severity_level = 3
    s = ort.InferenceSession(m.SerializeToString(), so,
                             providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    print("VERSION=" + ort.__version__)
    print("ACTIVE=" + json.dumps(s.get_providers()))
except Exception as e:
    print("PROBE_FAILED=" + repr(e))
'''


def probe_ort():
    r = subprocess.run([sys.executable, "-c", PROBE_SRC.replace("__ORT_DIR__", ORT_DIR)],
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    active = None
    for line in out.splitlines():
        if line.startswith("ACTIVE="):
            active = json.loads(line[len("ACTIVE="):])
    return active, out


def probe_field(out, key):
    for line in out.splitlines():
        if line.startswith(key + "="):
            return line[len(key) + 1:]
    return ""


active = None
if not ORT_TRY_GPU:
    print("ORT_TRY_GPU = False (cell 2) - using the image's onnxruntime on CPU.")
    print("The image shadows pip installs of onnxruntime, and its CUDA provider will")
    print("not load here (the PyPI wheels want CUDA 13, this image has CUDA 12.8).")
    print("Set ORT_TRY_GPU = True in cell 2 to retry the GPU path. crops is the only")
    print("stage affected - spatial/temporal/frequency/fusion are pure PyTorch.")
else:
    for spec, flags, why in ORT_CANDIDATES:
        print("-" * 70)
        print("trying %s - %s" % (spec, why))
        shutil.rmtree(ORT_DIR, ignore_errors=True)
        if sh(f"{sys.executable} -m pip install -q --target {ORT_DIR} {flags} {spec}").returncode:
            print("  install failed, moving to the next candidate")
            continue
        prov, out = probe_ort()
        if prov is None:
            print("  probe could not import onnxruntime:")
            print("  " + "\n  ".join(out.strip().splitlines()[-4:]))
            continue
        ver = probe_field(out, "VERSION") or "?"
        print("  onnxruntime %s -> ACTIVE providers %s" % (ver, prov))
        nv = probe_field(out, "NV_LIBS_FOUND")
        if nv:
            print("  CUDA libraries found alongside it: %s" % nv)
            print("  preload could not load          : %s"
                  % (probe_field(out, "PRELOAD_FAILED") or "[]"))
        miss = next((l for l in out.splitlines()
                     if "cannot open shared object" in l or "Failed to load" in l), "")
        if "CUDAExecutionProvider" in prov:
            active = prov
            break
        print("  CUDA did not load%s" % (": " + miss.strip()[:160] if miss else ""))
        print("  moving to the next candidate")
        if active is None:
            active = prov                 # keep the first build that at least works

    if active is None:
        print("no installed candidate worked - using the image's onnxruntime (CPU)")

if os.path.isdir(ORT_DIR):
    sys.path.insert(0, ORT_DIR)
sys.modules.pop("onnxruntime", None)          # drop any already-imported copy
try:
    import insightface, onnxruntime, timm, torch
except ModuleNotFoundError as e:
    raise SystemExit("dependency install failed (%s) - read the pip output above; "
                     "check the notebook has Internet enabled" % e)

if active is None:
    active = onnxruntime.get_available_providers()

ORT_USES_GPU = "CUDAExecutionProvider" in active

if ORT_USES_GPU:
    # One T4 serialises kernel execution anyway, so extra workers buy little, and
    # ORT_INTRA_THREADS=1 would only slow the CPU-side ops down. Cell 2 holds the
    # CPU-only values, so override them now that the device is known.
    ORT_INTRA_THREADS = 0
    N_WORKERS = min(2, os.cpu_count() or 1)

print("=" * 70)
print("insightface", insightface.__version__, "| onnxruntime", onnxruntime.__version__)
print("ACTIVE providers:", active)
print("torch", torch.__version__, "| cuda available:", torch.cuda.is_available())
if ORT_USES_GPU:
    print("face detection device: GPU")
    print("  ORT_INTRA_THREADS = %d  (leave onnxruntime's default)" % ORT_INTRA_THREADS)
    print("  N_WORKERS         = %d  (a single T4 barely benefits from concurrency)"
          % N_WORKERS)
else:
    print("face detection device: CPU")
    print("No CUDA provider activated, so the tuned CPU path is in use")
    print("(ORT_INTRA_THREADS=%s, N_WORKERS=%s), which measured ~2.4x faster than the"
          % (ORT_INTRA_THREADS, N_WORKERS))
    print("old sequential all-cores default. crops is the ONLY stage affected -")
    print("spatial/temporal/frequency/fusion are pure PyTorch and use the GPU normally.")
print("=" * 70)
"""

cell_lib_a = r"""# =============================================================
# CELL 4 - LIBRARY A: face detect/align + frequency stats
# VERBATIM port of ml/detection/data/face_utils.py and
# ml/detection/models/frequency_features.py. Do not diverge:
# training preprocessing must equal inference preprocessing.
# =============================================================
import cv2
import numpy as np

_FACE_MODEL = None
_FACE_MODEL_NAME = "buffalo_l"
_ORT_OPTS = None
_ORT_CORES = max(1, (os.cpu_count() or 4))

# canonical 5-point template (right eye, left eye, nose, right mouth, left mouth)
TEMPLATE = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)


def set_ort_threads(intra, inter=1):
    # onnxruntime's default intra_op_num_threads is one per core, so a single
    # detection already spans the box. Fewer threads per session + several
    # concurrent sessions usually beats that, but only a measurement says so.
    global _ORT_OPTS
    import onnxruntime
    so = onnxruntime.SessionOptions()
    so.intra_op_num_threads = int(intra)
    so.inter_op_num_threads = int(inter)
    so.log_severity_level = 3
    _ORT_OPTS = so
    return so


def _patch_session_options():
    # insightface's model_zoo.get_model() forwards only providers/provider_options
    # and silently DROPS sess_options, so there is no public way to set threads.
    # get_model() builds PickableInferenceSession as a module global, so patching
    # that name is enough. Same lever insightface's own GUI uses.
    from insightface.model_zoo import model_zoo as _mz
    if getattr(_mz, "_deeptrace_patched", False):
        return
    base = _mz.PickableInferenceSession

    class _Sess(base):
        def __init__(self, model_path, **kw):
            if _ORT_OPTS is not None:
                kw.setdefault("sess_options", _ORT_OPTS)
            super().__init__(model_path, **kw)

    _mz.PickableInferenceSession = _Sess
    _mz._deeptrace_patched = True


def reset_face_model():
    global _FACE_MODEL
    _FACE_MODEL = None


def get_face_model(name=_FACE_MODEL_NAME):
    global _FACE_MODEL
    if _FACE_MODEL is None:
        from insightface.app import FaceAnalysis
        if ORT_INTRA_THREADS:
            set_ort_threads(ORT_INTRA_THREADS)
            _patch_session_options()
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        app = FaceAnalysis(name=name, providers=providers)
        app.prepare(ctx_id=0, det_size=(640, 640))
        _FACE_MODEL = app
    return _FACE_MODEL


def detect_faces(rgb, model=None):
    model = model or get_face_model()
    return model.get(rgb)


def align_face(rgb, kps, size=224):
    M, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), TEMPLATE, method=cv2.RANSAC)
    if M is None:
        return cv2.resize(rgb, (size, size))
    return cv2.warpAffine(rgb, M, (size, size), flags=cv2.INTER_LINEAR)


def crop_face(rgb, face, margin=0.2, size=224):
    x1, y1, x2, y2 = [float(v) for v in face.bbox]
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return None
    mx, my = w * margin, h * margin
    x1, y1 = max(0, x1 - mx), max(0, y1 - my)
    x2, y2 = min(rgb.shape[1], x2 + mx), min(rgb.shape[0], y2 + my)
    crop = rgb[int(y1):int(y2), int(x1):int(x2)]
    if crop.size == 0:
        return None
    return cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)


def largest_face(faces):
    if not faces:
        return None
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def align_largest_face(rgb, size=224, min_score=0.5, model=None):
    faces = detect_faces(rgb, model)
    face = largest_face(faces)
    if face is None:
        return None, None
    score = getattr(face, "score", None)
    if score is not None and float(score) < min_score:
        return None, None
    try:
        return align_face(rgb, face.kps.astype(np.float32), size=size), face
    except Exception:
        return crop_face(rgb, face, size=size), face


# ---------------- frequency stats (8x8 grid x 4 stats = 256-d) ----------------

def _dct2(img):
    from scipy.fftpack import dct
    return dct(dct(img, axis=0, norm="ortho"), axis=1, norm="ortho")


def freq_stats_for_crop(rgb, n_coeffs=24):
    gray = rgb.mean(axis=2).astype(np.float64) / 255.0
    h, w = gray.shape
    grid = 8
    ch, cw = h // grid, w // grid
    feats = []
    for gy in range(grid):
        for gx in range(grid):
            cell = gray[gy * ch:(gy + 1) * ch, gx * cw:(gx + 1) * cw]
            if cell.size == 0:
                feats.extend([0.0] * 4)
                continue
            d = _dct2(cell)
            total = np.sum(d ** 2) + 1e-12
            low = d[:3, :3]
            low_energy = np.sum(low ** 2) / total
            yy, xx = np.indices(d.shape)
            radius = np.sqrt((yy / ch) ** 2 + (xx / cw) ** 2)
            high_mask = radius > 0.5
            high_energy = np.sum(d[high_mask] ** 2) / total
            mean_mag = np.mean(np.abs(d))
            std_mag = np.std(d)
            feats += [low_energy, high_energy, float(mean_mag), float(std_mag)]
    return np.asarray(feats, dtype=np.float32)


def freq_stats_for_batch(crops_rgb):
    return np.stack([freq_stats_for_crop(c) for c in crops_rgb]).astype(np.float32)


def sample_frames_cv2(path, n=8):
    # evenly spaced RGB frames via OpenCV (fast; avoids 8 ffmpeg subprocesses/video)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        cap.release()
        return []
    step = total / float(n)
    idxs = [int(min(k * step, total - 1)) for k in range(n)]
    frames = []
    for idx in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, bgr = cap.read()
        if ok and bgr is not None:
            frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    cap.release()
    return frames
"""

cell_lib_b = r"""# =============================================================
# CELL 5 - LIBRARY B: models + transforms
# VERBATIM port of ml/detection/models/heads.py, backbone.py and the
# transforms in ml/detection/data/datasets.py.
# =============================================================
import torch
import torch.nn as nn
from torchvision import transforms as T

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD  = [0.229, 0.224, 0.225]


def to_tensor(img_rgb):
    return T.Compose([T.ToTensor(), T.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD)])(img_rgb)


# augmentation runs on the tensor so it is valid for numpy HWC input
TRAIN_TF = T.Compose([
    T.ToTensor(),
    T.RandomHorizontalFlip(p=0.5),
    T.ColorJitter(brightness=0.1, contrast=0.1),
    T.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
])


class SpatialHead(nn.Module):
    def __init__(self, in_dim, dropout=0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(in_dim, 256),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(256, 1),
        )

    def forward(self, feats):
        if feats.dim() == 3:
            return self.net(feats).squeeze(-1)
        return self.net(feats).squeeze(-1)


class TemporalHead(nn.Module):
    def __init__(self, in_dim, hidden=128, layers=1, dropout=0.3):
        super().__init__()
        self.gru = nn.GRU(in_dim, hidden, num_layers=layers, batch_first=True,
                          dropout=dropout if layers > 1 else 0.0)
        self.head = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(inplace=True),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, feats_seq):
        out, _ = self.gru(feats_seq)
        pooled = out.mean(dim=1)
        return self.head(pooled).squeeze(-1)


class FrequencyHead(nn.Module):
    def __init__(self, in_dim, hidden=64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden),
            nn.ReLU(inplace=True),
            nn.Dropout(0.2),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(inplace=True),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, stats):
        if stats.dim() == 3:
            stats = stats.mean(dim=1)
        return self.net(stats).squeeze(-1)


class FusionHead(nn.Module):
    def __init__(self, in_dim, hidden=64, dropout=0.3):
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

    def forward(self, fused):
        return self.net(fused).squeeze(-1)


def build_backbone(name="xception", pretrained=True):
    import timm
    if name == "xception":
        return timm.create_model("xception", pretrained=pretrained, num_classes=0)
    if name in ("convnext_tiny", "convnext-tiny"):
        return timm.create_model("convnext_tiny", pretrained=pretrained, num_classes=0)
    raise ValueError("Unknown backbone: " + name)


def backbone_feature_dim(name):
    return {"xception": 2048, "convnext_tiny": 768, "convnext-tiny": 768}[name]


class Detector(nn.Module):
    # backbone + spatial head. CUDA is fine here - the local pipeline trains on CPU
    # but the architecture (and therefore the state_dict) is identical.

    def __init__(self, backbone_name="xception", pretrained=True, spatial_dropout=0.3):
        super().__init__()
        self.backbone = build_backbone(backbone_name, pretrained=pretrained)
        self.feat_dim = backbone_feature_dim(backbone_name)
        self.spatial = SpatialHead(self.feat_dim, dropout=spatial_dropout)
        # device hint only; the state_dict never contains it
        self._device_hint = None

    def forward_with_feats(self, x):
        feats = self.backbone(x)
        return feats, self.spatial(feats)

    def forward(self, x):
        _, logits = self.forward_with_feats(x)
        return logits


def device():
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")
"""

cell_manifest = r"""# =============================================================
# CELL 6 - STAGE: index  ->  manifest.csv
# Scans /kaggle/input, classifies every video as real/fake, and does a
# GROUPED (identity-aware) split so the same source video never lands in
# two splits - the classic FF++ / Celeb-DF leakage trap.
# =============================================================
import pandas as pd

REAL_DIRS = {"original", "original_sequences", "real", "real_videos", "origin", "youtube",
             "celeb-real", "youtube-real", "celeb_real", "youtube_real", "celebdf-real"}
FAKE_DIRS = {"deepfakes", "face2face", "faceswap", "neuraltextures", "deepfakedetection",
             "faceshifter", "fake", "fake_videos", "manipulated", "deepfake",
             "celeb-synthesis", "celeb_synthesis", "synthesis", "celebdf-synthesis"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def source_id(stem):
    # FF++: 000 / 000_003 -> 000 ; Celeb-DF: id0_0000 -> id0 ; DFDC: <hash>
    s = stem.strip()
    if "_" in s:
        head = s.split("_")[0]
        if head:
            return head
    return s


def dataset_of(p, root):
    # Kaggle mounts datasets at either
    #   /kaggle/input/<slug>/...                        (older)
    #   /kaggle/input/datasets/<owner>/<slug>/...       (current)
    # so parts[0] alone is the literal "datasets". Take the slug, not the owner.
    parts = p.relative_to(root).parts
    if parts and parts[0] == "datasets" and len(parts) >= 3:
        return parts[2]
    return parts[0] if parts else "unknown"


def scan_dirs(root=INPUT, max_depth=5):
    rows = []
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            children = list(d.iterdir())
        except Exception:
            continue
        for e in children:
            if not e.is_dir():
                continue
            nm = e.name.lower()
            if nm in REAL_DIRS or nm in FAKE_DIRS:
                label = 0 if nm in REAL_DIRS else 1
                ds = dataset_of(e, root)
                for v in e.rglob("*"):
                    if v.suffix.lower() in VIDEO_EXT:
                        rows.append(dict(path=str(v), label=label, dataset=ds,
                                         method=e.name, source_id=source_id(v.stem)))
            else:
                stack.append((e, depth + 1))
    return rows


def scan_metadata(root=INPUT):
    # DFDC-style: a metadata.json mapping filename -> REAL/FAKE
    rows = []
    for mj in root.rglob("metadata.json"):
        try:
            meta = json.loads(mj.read_text())
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        for fname, info in meta.items():
            if not isinstance(info, dict):
                continue
            lab = str(info.get("label", "")).upper()
            if lab not in ("REAL", "FAKE"):
                continue
            cands = list(mj.parent.rglob(fname))
            if not cands:
                continue
            p = cands[0]
            ds = dataset_of(p, root)
            rows.append(dict(path=str(p), label=0 if lab == "REAL" else 1, dataset=ds,
                             method="dfdc", source_id=source_id(p.stem)))
    return rows


def grouped_split(df):
    # per-dataset grouped split: rows sharing a source_id stay together, so a real
    # video and its manipulated twin are always in the same split.
    rng = random.Random(SEED)
    df = df.copy()
    df["group"] = df["dataset"] + ":" + df["source_id"]
    out = []
    for ds, sub in df.groupby("dataset"):
        groups = sorted(sub["group"].unique())
        rng.shuffle(groups)
        n = len(groups)
        n_tr = int(n * SPLIT["train"])
        n_va = int(n * SPLIT["val"])
        n_ho = int(n * SPLIT["holdout"])
        train_g = set(groups[:n_tr])
        val_g = set(groups[n_tr:n_tr + n_va])
        ho_g = set(groups[n_tr + n_va:n_tr + n_va + n_ho])
        for g in groups:
            if g in train_g:
                sub.loc[sub["group"] == g, "split"] = "train"
            elif g in val_g:
                sub.loc[sub["group"] == g, "split"] = "val"
            elif g in ho_g:
                sub.loc[sub["group"] == g, "split"] = "holdout"
            else:
                sub.loc[sub["group"] == g, "split"] = "test"
        out.append(sub)
    return pd.concat(out, ignore_index=True)


def extract_zips():
    # DFDC's competition data arrives as train_sample_videos.zip, not a folder of
    # videos, so it would otherwise contribute 0 videos to the index.
    import zipfile
    zips = [z for z in INPUT.rglob("*.zip") if not z.name.startswith(".")]
    if not zips:
        return
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    for z in zips:
        # name the extraction after the dataset folder so the dataset column reads
        # "deepfake-detection-challenge" instead of "train_sample_videos"
        slug = z.parent.name if z.parent.name not in ("", "input") else z.stem
        dest = EXTRACT_DIR / slug
        if dest.exists() and any(dest.rglob("*")):
            continue
        print("  unzipping %s -> %s" % (z.name, dest.name))
        try:
            with zipfile.ZipFile(z) as zf:
                zf.extractall(dest)
        except Exception as e:
            print("  could not unzip %s: %s: %s" % (z.name, type(e).__name__, e))


def index_roots():
    roots = [INPUT]
    if EXTRACT_DIR.exists():
        roots.append(EXTRACT_DIR)
    return roots


ARTIFACT_MARKERS = ("manifest.csv", "crop_index.csv", "crops", "fstats",
                    "feat_cache", "weights")


def find_prior_roots(max_depth=5):
    # A previous stage's /kaggle/working, wherever Kaggle happened to mount it.
    # Datasets mount at /kaggle/input/datasets/<owner>/<slug>/ and kernel outputs
    # at /kaggle/input/<slug>/ or /kaggle/input/kernels/<owner>/<slug>/, so a
    # single-level glob is not enough - search for the artifacts themselves.
    roots = []
    if not INPUT.exists():
        return roots
    stack = [(INPUT, 0)]
    while stack:
        d, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            if d != INPUT and any((d / m).exists() for m in ARTIFACT_MARKERS):
                roots.append(d)
                continue          # an artifact root is a leaf for our purposes
            children = list(d.iterdir())
        except Exception:
            continue
        for c in children:
            if c.is_dir():
                stack.append((c, depth + 1))
    return sorted(roots)


def describe_input(max_depth=3):
    # Diagnostic: exactly what is mounted under /kaggle/input (directories only).
    out = []
    if not INPUT.exists():
        return ["  /kaggle/input does not exist"]
    stack = [(INPUT, 0)]
    while stack:
        d, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            kids = sorted(d.iterdir())
        except Exception:
            continue
        dirs = [c.name for c in kids if c.is_dir()]
        files = [c.name for c in kids if c.is_file()]
        shown = files[:6] + (["... +%d files" % (len(files) - 6)] if len(files) > 6 else [])
        rel = "." if d == INPUT else str(d.relative_to(INPUT))
        out.append("%s%s/  subdirs=%s  files=%s" % ("  " * depth, rel, dirs[:8], shown))
        for c in kids:
            if c.is_dir():
                stack.append((c, depth + 1))
    return out


def require(path, what):
    p = Path(path)
    if p.exists():
        return
    print("MISSING: %s  (%s)" % (p, what))
    print("what is mounted under /kaggle/input:")
    for line in describe_input():
        print(line)
    print("prior artifact roots found:", find_prior_roots())
    raise SystemExit(
        "The earlier stage's output was not mounted. Run that stage first, and make "
        "sure this push's kernel-metadata.json lists it in kernel_sources - "
        "'kaggle kernels push' mounts a source kernel's /kaggle/working READ-ONLY "
        "under /kaggle/input.")


def do_index():
    extract_zips()
    rows = []
    for root in index_roots():
        rows += scan_dirs(root) + scan_metadata(root)
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("No videos found under /kaggle/input. Attach a video-based dataset "
                         "(see cell 1) and enable Internet.")
    # dedupe: metadata scan may overlap the dir scan
    df = df.drop_duplicates(subset=["path"]).reset_index(drop=True)
    # sort before subsampling/splitting: directory iteration order is not stable,
    # and the caps + grouped split are seeded off row order. Sorting makes the
    # whole manifest reproducible run to run.
    df = df.sort_values("path").reset_index(drop=True)

    print("raw videos per dataset:")
    print(df.groupby(["dataset", "method", "label"]).size().to_string())

    # subsample: cap each fake method, and cap real separately (FF++ is 6:1 fake:real)
    keep = []
    rng = random.Random(SEED)
    for (ds, method, label), sub in df.groupby(["dataset", "method", "label"]):
        cap = MAX_REAL_VIDEOS if label == 0 else MAX_VIDEOS_PER_METHOD
        idx = list(sub.index)
        rng.shuffle(idx)
        keep.extend(sorted(idx[:cap]))
    df = df.loc[sorted(set(keep))].reset_index(drop=True)

    df = grouped_split(df)
    df.to_csv(MANIFEST_CSV, index=False)
    print()
    print("kept:", len(df), "videos ->", MANIFEST_CSV)
    print(df.groupby(["dataset", "split", "label"]).size().to_string())
    print()
    print("NOTE: cross-dataset eval uses EVAL_DATASETS:", EVAL_DATASETS)
    print("      fusion trains on the 'holdout' split of:", TRAIN_DATASETS)


def load_manifest():
    return pd.read_csv(MANIFEST_CSV)
"""

cell_crops = r"""# =============================================================
# CELL 7 - STAGE: crops
# Detects + aligns the largest face per sampled frame, writes lossless crops to
# crops/<video_id>/ and the DCT stats to fstats/<video_id>.npy.
#
# Why compute frequency stats HERE: at inference pipeline.py computes them from the
# in-memory aligned crop. If we recomputed them from JPEG-on-disk crops the frequency
# head would learn JPEG recompression instead of manipulation artifacts.
# =============================================================
import time


def video_id_for(row):
    stem = Path(row["path"]).stem
    return ("%s__%s__%s" % (row["dataset"], row["method"], stem)).replace("/", "_")


def do_crops():
    if not MANIFEST_CSV.exists():
        # Self-heal: the index kernel's output was not mounted. Rebuilding is safe
        # and cheap - the manifest is deterministic (fixed seed + sorted input), so
        # the split comes out identical to what the index kernel produced.
        print("manifest.csv was not mounted. What is under /kaggle/input:")
        for line in describe_input():
            print(line)
        print("rebuilding the manifest locally (deterministic) ...")
        do_index()
    df = load_manifest()
    model = get_face_model()
    t0 = time.time()

    def process_row(row):
        # One video: sample frames, detect+align the largest face in each, write the
        # crops and their DCT stats. Returns a crop_index record, or None when no
        # face was found in any sampled frame (that video is dropped).
        vid = Path(row["path"])
        vid_id = video_id_for(row)
        cdir = CROPS_DIR / vid_id
        fpath = FSTATS_DIR / (vid_id + ".npy")

        if cdir.exists() and any(cdir.glob("*." + CROP_FORMAT)) and fpath.exists():
            return dict(video_id=vid_id, label=int(row["label"]), dataset=row["dataset"],
                        method=row["method"], split=row["split"],
                        n_crops=len(list(cdir.glob("*." + CROP_FORMAT))))

        crops = []
        for frame in sample_frames_cv2(vid, FRAME_BUDGET):
            crop, _ = align_largest_face(frame, size=FACE_SIZE, model=model)
            if crop is not None:
                crops.append(crop)
        if not crops:
            return None

        cdir.mkdir(parents=True, exist_ok=True)
        for fork in sorted(cdir.glob("*")):          # clear stale partial writes
            fork.unlink()
        for j, c in enumerate(crops):
            cv2.imwrite(str(cdir / ("f_%02d.%s" % (j, CROP_FORMAT))),
                        cv2.cvtColor(c, cv2.COLOR_RGB2BGR))
        np.save(fpath, freq_stats_for_batch(crops))
        return dict(video_id=vid_id, label=int(row["label"]), dataset=row["dataset"],
                    method=row["method"], split=row["split"], n_crops=len(crops))

    rows = df.to_dict("records")
    print("videos: %d | workers: %d | ort intra-op threads: %s"
          % (len(rows), N_WORKERS, ORT_INTRA_THREADS or "default"))

    records = []
    if N_WORKERS <= 1:
        for i, row in enumerate(rows, 1):
            rec = process_row(row)
            if rec:
                records.append(rec)
            if i % 100 == 0:
                print("%d/%d  %.1f min" % (i, len(rows), (time.time() - t0) / 60.0))
    else:
        # Threads, not processes. ONNX Runtime releases the GIL inside Run(), so
        # concurrent sessions genuinely overlap, and we keep ONE shared 282 MB model
        # instead of N copies. FaceAnalysis.get() is safe to call concurrently: its
        # only mutable state is a read-through anchor cache keyed by output shape,
        # filled identically by whichever thread gets there first.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
            for i, rec in enumerate(ex.map(process_row, rows), 1):
                if rec:
                    records.append(rec)
                if i % 100 == 0:
                    print("%d/%d  %.1f min" % (i, len(rows), (time.time() - t0) / 60.0))

    idx = pd.DataFrame(records, columns=["video_id", "label", "dataset", "method",
                                         "split", "n_crops"])
    idx.to_csv(CROP_INDEX_CSV, index=False)
    print("videos with usable crops: %d of %d" % (len(idx), len(df)))
    if idx.empty:
        raise SystemExit(
            "No face was detected in ANY sampled frame - there is nothing to train on. "
            "Check the videos are readable and actually contain faces.")
    kept = len(idx) / max(len(df), 1)
    print(idx.groupby(["dataset", "split", "label"]).size().to_string())
    if kept < 0.8:
        # face detection dropping a lot is otherwise silent - it just trains on less
        print()
        print("WARNING: only %.0f%% of videos yielded crops. Misses by dataset:" % (100 * kept))
        have = set(idx["video_id"])
        miss = df.copy()
        miss["video_id"] = [video_id_for(r) for _, r in miss.iterrows()]
        miss["detected"] = miss["video_id"].isin(have)
        print(miss.groupby(["dataset", "detected"]).size().to_string())


def do_bench():
    # Measures REAL end-to-end detection throughput, so the numbers are ground truth
    # rather than a model. Which axis matters depends on the device:
    #   GPU: intra_op stays default, and extra workers mostly add contention
    #   CPU: ORT's one-thread-per-core default is slower per frame than a single
    #        thread, so the win is N concurrent single-threaded sessions
    # Nothing is written.
    global ORT_INTRA_THREADS
    require(MANIFEST_CSV, "produced by the 'index' stage")
    df = load_manifest()

    frames = []
    for _, r in df.head(12).iterrows():
        frames += sample_frames_cv2(Path(r["path"]), FRAME_BUDGET)
        if len(frames) >= 12:
            break
    frames = frames[:12]
    if len(frames) < 4:
        raise SystemExit("could not read enough frames to benchmark")

    on_gpu = ORT_USES_GPU                     # set by cell 3 from a real probe -
    print("device: %s | cores: %d | benchmark frames: %d"   # never from the declared
          % ("GPU" if on_gpu else "CPU", _ORT_CORES, len(frames)))   # provider list
    print()

    from concurrent.futures import ThreadPoolExecutor

    def measure(workers, intra):
        global ORT_INTRA_THREADS          # needed here too - do_bench's global does
        ORT_INTRA_THREADS = intra         # not cover this nested scope
        reset_face_model()
        m = get_face_model()
        m.get(frames[0])                        # warm up, not timed
        t0 = time.time()
        if workers <= 1:
            for f in frames:
                m.get(f)
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(lambda f: m.get(f.copy()), frames))
        return time.time() - t0

    if on_gpu:
        combos = [("1 session (default)", 1, 0)]
        for w in (2, _ORT_CORES):
            if w > 1:
                combos.append(("%d concurrent sessions" % w, w, 0))
    else:
        combos = [("baseline: 1 session, all cores", 1, 0)]
        for w in (1, 2, _ORT_CORES):
            combos.append(("%d sessions x 1 thread" % w, w, 1))

    results = []
    print("| config | sec/frame | detections/sec | vs baseline |")
    print("|---|---|---|---|")
    for label, w, intra in combos:
        try:
            dt = measure(w, intra)
        except Exception as e:                 # a config can OOM or be unsupported
            print("| %s | FAILED | %s: %s | - |"
                  % (label, type(e).__name__, str(e)[:60]))
            continue
        results.append((label, w, intra, dt, len(frames) / dt))

    if not results:
        raise SystemExit("every benchmarked configuration failed")
    base = results[0][4]
    for label, w, intra, dt, thru in results:
        print("| %s | %.3f | %.2f | %.2fx |" % (label, dt / len(frames), thru, thru / base))
    print()

    best = max(results, key=lambda r: r[4])
    print("BEST: %s  ->  ORT_INTRA_THREADS = %d, N_WORKERS = %d  (%.2fx baseline)"
          % (best[0], best[2], best[1], best[4] / base))
    if best[4] / base < 1.15:
        print()
        print("Concurrency buys almost nothing here - keep N_WORKERS small and let the")
        print("device do the work. Do NOT reduce FRAME_BUDGET to compensate.")
    print("Set those two in cell 2, then run STAGE='crops'.")


def load_crop_index():
    return pd.read_csv(CROP_INDEX_CSV)


def crop_files(video_id):
    return sorted((CROPS_DIR / video_id).glob("*." + CROP_FORMAT))


def read_crop(path):
    bgr = cv2.imread(str(path))
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
"""

cell_spatial = r"""# =============================================================
# CELL 8 - STAGE: spatial
# Trains Detector (xception + SpatialHead) -> weights/spatial_xception.pt
#
# The local train.py keeps the backbone FROZEN (it has no GPU). Here we have a GPU,
# so we warm up the head with a frozen backbone and then fine-tune the whole network.
# The saved state_dict still contains backbone.* + spatial.* - exactly what
# pipeline.py's detector.load_state_dict() expects - so nothing downstream changes.
# =============================================================
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler


class CropDataset(Dataset):
    def __init__(self, idx_df, train=False):
        self.items = []
        for _, r in idx_df.iterrows():
            for f in crop_files(r["video_id"]):
                self.items.append((f, int(r["label"])))
        self.tf = TRAIN_TF if train else None

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, label = self.items[i]
        img = read_crop(path)
        if self.tf is not None:
            return self.tf(img), label
        return to_tensor(img), label


@torch.no_grad()
def eval_spatial(model, loader, dev):
    model.eval()
    correct = total = 0
    for img, label in loader:
        img = img.to(dev, non_blocking=True)
        _, logits = model.forward_with_feats(img)
        preds = (torch.sigmoid(logits) >= 0.5).float().cpu()
        correct += (preds == label.float()).sum().item()
        total += label.numel()
    return correct / max(total, 1)


def do_spatial():
    require(CROP_INDEX_CSV, "produced by the 'crops' stage")
    idx = load_crop_index()
    tr = idx[idx["split"].isin(["train"])]
    va = idx[idx["split"] == "val"]
    if len(tr) == 0:
        raise SystemExit("no training videos - run the 'crops' stage first")

    tr_ds = CropDataset(tr, train=True)
    va_ds = CropDataset(va, train=False)
    print("train crops:", len(tr_ds), "| val crops:", len(va_ds))

    labels = np.array([l for _, l in tr_ds.items])
    n_pos = max(int(labels.sum()), 1)
    n_neg = max(int((labels == 0).sum()), 1)
    print("train class balance  real=%d fake=%d" % (n_neg, n_pos))

    sampler = WeightedRandomSampler(
        weights=[1.0 / n_pos if l == 1 else 1.0 / n_neg for l in labels],
        num_samples=len(labels), replacement=True)
    tr_loader = DataLoader(tr_ds, batch_size=SPATIAL_CFG["batch"], sampler=sampler,
                           num_workers=2, pin_memory=True, drop_last=True)
    va_loader = DataLoader(va_ds, batch_size=SPATIAL_CFG["batch"], num_workers=2, pin_memory=True)

    dev = device()
    model = Detector(BACKBONE, pretrained=True, spatial_dropout=SPATIAL_CFG["dropout"]).to(dev)
    lossf = nn.BCEWithLogitsLoss()
    scaler = torch.cuda.amp.GradScaler(enabled=(dev.type == "cuda"))

    best = 0.0
    for ep in range(SPATIAL_CFG["epochs"]):
        frozen = ep < SPATIAL_CFG["frozen_epochs"]
        for p in model.backbone.parameters():
            p.requires_grad = not frozen
        params = ([{"params": model.spatial.parameters(), "lr": SPATIAL_CFG["lr_head"]}]
                  if frozen else
                  [{"params": model.backbone.parameters(), "lr": SPATIAL_CFG["lr_backbone"]},
                   {"params": model.spatial.parameters(), "lr": SPATIAL_CFG["lr_head"]}])
        opt = torch.optim.AdamW(params, weight_decay=5e-4)

        model.train()
        tot = n = 0
        for img, label in tr_loader:
            img = img.to(dev, non_blocking=True)
            lbl = label.float().to(dev)
            with torch.cuda.amp.autocast(enabled=(dev.type == "cuda")):
                _, logits = model.forward_with_feats(img)
                loss = lossf(logits, lbl)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            tot += loss.item()
            n += 1
        acc = eval_spatial(model, va_loader, dev)
        print("epoch %d/%d  %s  loss=%.4f  val_acc=%.4f"
              % (ep + 1, SPATIAL_CFG["epochs"], "frozen" if frozen else "finetune",
                 tot / max(n, 1), acc))
        if acc >= best:
            best = acc
            torch.save({"state_dict": model.state_dict(), "config": {"name": BACKBONE}},
                       WEIGHTS_DIR / "spatial_xception.pt")
            print("   saved (val_acc=%.4f)" % acc)
    print("best val_acc:", round(best, 4))
"""

cell_temporal = r"""# =============================================================
# CELL 9 - STAGE: temporal
# Caches backbone features with the TRAINED detector, then trains the GRU.
# Features must come from the fine-tuned spatial checkpoint because that is what
# pipeline.py uses at inference time.
# =============================================================
FEAT_DIM = {"xception": 2048, "convnext_tiny": 768}


def load_detector(trainable=False):
    dev = device()
    m = Detector(BACKBONE, pretrained=False, spatial_dropout=SPATIAL_CFG["dropout"])
    p = WEIGHTS_DIR / "spatial_xception.pt"
    require(p, "produced by the 'spatial' stage")
    state = torch.load(p, map_location="cpu")
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    m.load_state_dict(state)
    m.to(dev)
    m.train(trainable)
    return m, dev


@torch.no_grad()
def cache_features(video_id, model, dev):
    cpath = FEATCACHE_DIR / (video_id + ".pt")
    if cpath.exists():
        return torch.load(cpath, map_location="cpu")
    files = crop_files(video_id)
    if not files:
        return None
    x = torch.stack([to_tensor(read_crop(f)) for f in files]).to(dev)
    feats, _ = model.forward_with_feats(x)
    feats = feats.cpu()
    torch.save(feats, cpath)
    return feats


def do_temporal():
    require(CROP_INDEX_CSV, "produced by the 'crops' stage")
    idx = load_crop_index()
    model, dev = load_detector()

    cache = {}
    for _, r in idx.iterrows():
        f = cache_features(r["video_id"], model, dev)
        if f is not None:
            cache[r["video_id"]] = f
    print("feature cache entries:", len(cache))

    def seq(vid):
        f = cache.get(vid)
        if f is None:
            return None
        n = f.shape[0]
        if n < FRAME_BUDGET:                       # tile short sequences, like datasets.py
            f = f.repeat((FRAME_BUDGET // n) + 1, 1)[:FRAME_BUDGET]
        return f.unsqueeze(0).to(dev)

    gru = TemporalHead(FEAT_DIM[BACKBONE], hidden=TEMPORAL_CFG["hidden"],
                       layers=TEMPORAL_CFG["layers"], dropout=TEMPORAL_CFG["dropout"]).to(dev)
    opt = torch.optim.AdamW(gru.parameters(), lr=TEMPORAL_CFG["lr"], weight_decay=5e-4)
    lossf = nn.BCEWithLogitsLoss()
    tr_rows = idx[idx["split"] == "train"].to_dict("records")
    va_rows = idx[idx["split"] == "val"].to_dict("records")
    rng = random.Random(SEED)

    best = 0.0
    for ep in range(TEMPORAL_CFG["epochs"]):
        gru.train()
        rng.shuffle(tr_rows)
        tot = n = 0
        for r in tr_rows:
            x = seq(r["video_id"])
            if x is None:
                continue
            loss = lossf(gru(x), torch.tensor([float(r["label"])], device=dev))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item()
            n += 1

        gru.eval()
        correct = total = 0
        with torch.no_grad():
            for r in va_rows:
                x = seq(r["video_id"])
                if x is None:
                    continue
                p = torch.sigmoid(gru(x)).item()
                correct += int(p >= 0.5) == int(r["label"])
                total += 1
        acc = correct / max(total, 1)
        print("epoch %d/%d  loss=%.4f  val_acc=%.4f" % (ep + 1, TEMPORAL_CFG["epochs"],
                                                        tot / max(n, 1), acc))
        if acc >= best:
            best = acc
            torch.save({"state_dict": gru.state_dict(), "config": TEMPORAL_CFG},
                       WEIGHTS_DIR / "temporal_gru.pt")
    print("best val_acc:", round(best, 4))
"""

cell_frequency = r"""# =============================================================
# CELL 10 - STAGE: frequency
# Trains the MLP on the precomputed DCT stats (fstats/*.npy).
# The head averages over T internally, matching pipeline.py.
# =============================================================
def do_frequency():
    require(CROP_INDEX_CSV, "produced by the 'crops' stage")
    idx = load_crop_index()
    dev = device()

    def stats(vid):
        p = FSTATS_DIR / (vid + ".npy")
        if not p.exists():
            return None
        return torch.from_numpy(np.load(p)).float().to(dev)

    model = FrequencyHead(in_dim=256, hidden=FREQUENCY_CFG["hidden"]).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=FREQUENCY_CFG["lr"], weight_decay=5e-4)
    lossf = nn.BCEWithLogitsLoss()
    tr_rows = idx[idx["split"] == "train"].to_dict("records")
    va_rows = idx[idx["split"] == "val"].to_dict("records")
    rng = random.Random(SEED)

    best = 0.0
    for ep in range(FREQUENCY_CFG["epochs"]):
        model.train()
        rng.shuffle(tr_rows)
        tot = n = 0
        for r in tr_rows:
            s = stats(r["video_id"])
            if s is None:
                continue
            # s is (T,256); unsqueeze -> (1,T,256) so the head averages over the T
            # crops into one video score (its (B,T,F) branch). A bare (T,256) is read
            # as B=T and returns T scores - which crashes .item() at inference.
            loss = lossf(model(s.unsqueeze(0)), torch.tensor([float(r["label"])], device=dev))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item()
            n += 1

        model.eval()
        correct = total = 0
        with torch.no_grad():
            for r in va_rows:
                s = stats(r["video_id"])
                if s is None:
                    continue
                p = torch.sigmoid(model(s.unsqueeze(0))).item()
                correct += int(p >= 0.5) == int(r["label"])
                total += 1
        acc = correct / max(total, 1)
        print("epoch %d/%d  loss=%.4f  val_acc=%.4f" % (ep + 1, FREQUENCY_CFG["epochs"],
                                                        tot / max(n, 1), acc))
        if acc >= best:
            best = acc
            torch.save({"state_dict": model.state_dict(), "config": FREQUENCY_CFG},
                       WEIGHTS_DIR / "frequency_mlp.pt")
    print("best val_acc:", round(best, 4))
"""

cell_fusion = r"""# =============================================================
# CELL 11 - STAGE: fusion  (held-out split - the anti-leakage rule)
# Trains ONLY on the 'holdout' split that the branch heads never saw. Otherwise the
# fusion head just memorizes which branch wins on training data and collapses
# cross-dataset.
#
# Input vector (must match pipeline.py):
#   [ mean(backbone feats) (2048) | mean(GRU hidden) (128) | mean(dct stats) (256) ]
# =============================================================
def do_fusion():
    require(CROP_INDEX_CSV, "produced by the 'crops' stage")
    idx = load_crop_index()
    model, dev = load_detector()

    temporal = None
    if (WEIGHTS_DIR / "temporal_gru.pt").exists():
        temporal = TemporalHead(FEAT_DIM[BACKBONE], hidden=TEMPORAL_CFG["hidden"],
                                layers=TEMPORAL_CFG["layers"],
                                dropout=TEMPORAL_CFG["dropout"]).to(dev)
        st = torch.load(WEIGHTS_DIR / "temporal_gru.pt", map_location="cpu")
        temporal.load_state_dict(st.get("state_dict", st))
        temporal.eval()

    def fused_vec(vid):
        f = cache_features(vid, model, dev)          # (T, D)
        if f is None:
            return None
        spatial_v = f.mean(dim=0).numpy()
        temporal_v = np.zeros(TEMPORAL_CFG["hidden"], dtype=np.float32)
        if temporal is not None:
            with torch.no_grad():
                out, _ = temporal.gru(f.unsqueeze(0).to(dev))
                temporal_v = out.mean(dim=1).squeeze(0).cpu().numpy()
        sp = FSTATS_DIR / (vid + ".npy")
        if not sp.exists():
            return None
        freq_v = np.load(sp).mean(axis=0)
        return np.concatenate([spatial_v, temporal_v, freq_v]).astype(np.float32)

    in_dim = FEAT_DIM[BACKBONE] + TEMPORAL_CFG["hidden"] + 256
    fusion = FusionHead(in_dim=in_dim, hidden=FUSION_CFG["hidden"],
                        dropout=FUSION_CFG["dropout"]).to(dev)
    opt = torch.optim.AdamW(fusion.parameters(), lr=FUSION_CFG["lr"], weight_decay=5e-4)
    lossf = nn.BCEWithLogitsLoss()

    tr_rows = idx[idx["split"] == "holdout"].to_dict("records")
    te_rows = idx[idx["split"] == "test"].to_dict("records")
    print("fusion train (holdout):", len(tr_rows), "| fusion val (test):", len(te_rows))
    if not tr_rows:
        raise SystemExit("no holdout rows - re-run the 'index' stage")

    rng = random.Random(SEED)
    best = 0.0
    for ep in range(FUSION_CFG["epochs"]):
        fusion.train()
        rng.shuffle(tr_rows)
        tot = n = 0
        for r in tr_rows:
            v = fused_vec(r["video_id"])
            if v is None:
                continue
            out = fusion(torch.from_numpy(v).unsqueeze(0).to(dev))
            loss = lossf(out, torch.tensor([float(r["label"])], device=dev))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item()
            n += 1

        fusion.eval()
        correct = total = 0
        with torch.no_grad():
            for r in te_rows:
                v = fused_vec(r["video_id"])
                if v is None:
                    continue
                p = torch.sigmoid(fusion(torch.from_numpy(v).unsqueeze(0).to(dev))).item()
                correct += int(p >= 0.5) == int(r["label"])
                total += 1
        acc = correct / max(total, 1)
        print("epoch %d/%d  loss=%.4f  val_acc=%.4f" % (ep + 1, FUSION_CFG["epochs"],
                                                        tot / max(n, 1), acc))
        if acc >= best:
            best = acc
            torch.save({"state_dict": fusion.state_dict(), "config": FUSION_CFG},
                       WEIGHTS_DIR / "fusion_mlp.pt")
    print("best val_acc:", round(best, 4))
"""

cell_eval = r"""# =============================================================
# CELL 12 - STAGE: eval
# ACC / AUC / AP per dataset AND per manipulation method, on held-out identities.
# Cross-dataset numbers (train on FF++, test on Celeb-DF / DFDC) are the ones that
# prove the robustness claim.
# =============================================================
from sklearn.metrics import roc_auc_score, average_precision_score, accuracy_score


def load_all_heads():
    model, dev = load_detector()
    heads = {"detector": model}
    specs = [
        ("temporal", TemporalHead,
         dict(in_dim=FEAT_DIM[BACKBONE], hidden=TEMPORAL_CFG["hidden"],
              layers=TEMPORAL_CFG["layers"], dropout=TEMPORAL_CFG["dropout"]),
         "temporal_gru.pt"),
        ("frequency", FrequencyHead, dict(in_dim=256, hidden=FREQUENCY_CFG["hidden"]),
         "frequency_mlp.pt"),
        ("fusion", FusionHead,
         dict(in_dim=FEAT_DIM[BACKBONE] + TEMPORAL_CFG["hidden"] + 256,
              hidden=FUSION_CFG["hidden"], dropout=FUSION_CFG["dropout"]),
         "fusion_mlp.pt"),
    ]
    for name, cls, kw, fname in specs:
        p = WEIGHTS_DIR / fname
        if not p.exists():
            heads[name] = None
            continue
        h = cls(**kw).to(dev)
        st = torch.load(p, map_location="cpu")
        h.load_state_dict(st.get("state_dict", st))
        h.eval()
        heads[name] = h
    return heads, dev


@torch.no_grad()
def probs_for_video(vid, heads, dev):
    f = cache_features(vid, heads["detector"], dev)     # (T, D)
    if f is None:
        return None
    f = f.to(dev)
    out = {}

    sp_probs = torch.sigmoid(heads["detector"].spatial(f))
    out["spatial"] = float(sp_probs.mean().item())

    if heads["temporal"] is not None:
        x = f.unsqueeze(0)
        if x.shape[1] < FRAME_BUDGET:
            x = x.repeat(1, (FRAME_BUDGET // x.shape[1]) + 1, 1)[:, :FRAME_BUDGET, :]
        out["temporal"] = float(torch.sigmoid(heads["temporal"](x)).item())

    sp = FSTATS_DIR / (vid + ".npy")
    if sp.exists():
        stats_t = torch.from_numpy(np.load(sp)).float().to(dev)
        freq_v = np.load(sp).mean(axis=0)
        if heads["frequency"] is not None:
            out["frequency"] = float(torch.sigmoid(heads["frequency"](stats_t.unsqueeze(0))).item())
        if heads["fusion"] is not None:
            temporal_v = np.zeros(TEMPORAL_CFG["hidden"], dtype=np.float32)
            if heads["temporal"] is not None:
                gru_out, _ = heads["temporal"].gru(f.unsqueeze(0))
                temporal_v = gru_out.mean(dim=1).squeeze(0).cpu().numpy()
            vec = np.concatenate([f.mean(dim=0).cpu().numpy(), temporal_v, freq_v]).astype(np.float32)
            out["fusion"] = float(torch.sigmoid(heads["fusion"](torch.from_numpy(vec).unsqueeze(0).to(dev))).item())
    return out


def metrics(rows, branch):
    y = [r["label"] for r in rows]
    p = [r[branch] for r in rows]
    if len(set(y)) < 2:
        return None
    return dict(acc=accuracy_score(y, [int(v >= 0.5) for v in p]),
                auc=roc_auc_score(y, p), ap=average_precision_score(y, p), n=len(rows))


def do_eval():
    require(CROP_INDEX_CSV, "produced by the 'crops' stage")
    idx = load_crop_index()
    heads, dev = load_all_heads()
    branches = [b for b in ("spatial", "temporal", "frequency", "fusion") if heads.get(b) is not None or b == "spatial"]

    rows = []
    for _, r in idx.iterrows():
        p = probs_for_video(r["video_id"], heads, dev)
        if p is None:
            continue
        rec = dict(dataset=r["dataset"], method=r["method"], split=r["split"],
                   label=int(r["label"]))
        rec.update(p)
        rows.append(rec)

    lines = ["# DeepTrace - evaluation", "",
             "Trained on: %s  |  cross-dataset: %s" % (TRAIN_DATASETS, EVAL_DATASETS), ""]
    for scope in ("test",):
        lines.append("## split: %s" % scope)
        for ds in sorted({r["dataset"] for r in rows}):
            sub = [r for r in rows if r["dataset"] == ds and r["split"] == scope]
            if not sub:
                continue
            lines.append("")
            lines.append("### %s (n=%d)" % (ds, len(sub)))
            lines.append("")
            lines.append("| branch | ACC | AUC | AP | n |")
            lines.append("|---|---|---|---|---|")
            for b in branches:
                m = metrics(sub, b) if all(b in r for r in sub) else None
                if m:
                    lines.append("| %s | %.4f | %.4f | %.4f | %d |" % (b, m["acc"], m["auc"], m["ap"], m["n"]))
            lines.append("")
            lines.append("| manipulation | n | fusion AUC |")
            lines.append("|---|---|---|")
            for meth in sorted({r["method"] for r in sub}):
                msub = [r for r in sub if r["method"] == meth]
                m = metrics(msub, "fusion") if ("fusion" in msub[0]) else None
                if m:
                    lines.append("| %s | %d | %.4f |" % (meth, m["n"], m["auc"]))
    report = "\n".join(lines)
    (WORK / "eval.md").write_text(report, encoding="utf-8")
    print(report)
"""

cell_export = r"""# =============================================================
# CELL 13 - STAGE: export + driver
# Zips the four checkpoints. Download the zip (or save the notebook version and use
# /kaggle/working as an input dataset) and drop the files into ml/weights/ locally.
# =============================================================
REQUIRED = ["spatial_xception.pt", "temporal_gru.pt", "frequency_mlp.pt", "fusion_mlp.pt"]


def restore_prior_artifacts():
    # 'kaggle kernels push' mounts a source kernel's saved /kaggle/working
    # READ-ONLY under /kaggle/input - not at /kaggle/working. Bring it across so
    # the next stage can find it. Each kernel's snapshot is cumulative, so
    # chaining one level (push N mounts push N-1) carries everything forward.
    if not INPUT.exists():
        print("  /kaggle/input does not exist - nothing to restore")
        return
    roots = find_prior_roots()
    if not roots:
        print("  no earlier-stage artifacts found under /kaggle/input")
        for line in describe_input():
            print(line)
        return
    for src in roots:
        print("  source: %s" % src)
        for name in ("crops", "fstats", "feat_cache"):
            s, d = src / name, WORK / name
            # the config cell pre-creates these dirs, so "exists" is not enough -
            # an empty dir still needs restoring
            missing = (not d.exists()) or (d.is_dir() and not any(d.iterdir()))
            if s.exists() and missing:
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
                elif d.exists():
                    d.unlink()
                if COPY_PRIOR_ARTIFACTS:
                    shutil.copytree(s, d)
                    print("    copied %-12s <- %s" % (name, src.name))
                    continue
                try:
                    os.symlink(s, d)
                    print("    linked %-12s <- %s" % (name, src.name))
                except OSError:
                    shutil.copytree(s, d)
                    print("    copied %-12s <- %s" % (name, src.name))
        for name in ("manifest.csv", "crop_index.csv"):
            s, d = src / name, WORK / name
            if s.exists() and not d.exists():
                shutil.copy2(s, d)
                print("    copied %-12s <- %s" % (name, src.name))
        s = src / "weights"
        if s.exists():
            WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
            for f in sorted(s.glob("*.pt")):
                d = WEIGHTS_DIR / f.name
                if not d.exists():
                    shutil.copy2(f, d)
                    print("    copied weights/%s <- %s" % (f.name, src.name))
        s = src / "eval.md"
        if s.exists() and not (WORK / "eval.md").exists():
            shutil.copy2(s, WORK / "eval.md")
            print("    copied eval.md <- %s" % src.name)


def do_export():
    missing = [f for f in REQUIRED if not (WEIGHTS_DIR / f).exists()]
    if missing:
        print("WARNING missing checkpoints:", missing)
    zpath = WORK / "deeptrace_weights.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in REQUIRED:
            p = WEIGHTS_DIR / f
            if p.exists():
                z.write(p, p.name)
                print("packed", f, round(p.stat().st_size / 1e6, 2), "MB")
    print("->", zpath)


ALL_STAGES = ["index", "bench", "crops", "spatial", "temporal", "frequency",
              "fusion", "eval", "export"]

NEXT_STAGE = {"index": "bench", "bench": "crops", "crops": "spatial",
              "spatial": "temporal", "temporal": "frequency",
              "frequency": "fusion", "fusion": "eval", "eval": "export",
              "export": None}

STAGE_RUNNER = {"index": do_index, "bench": do_bench, "crops": do_crops,
                "spatial": do_spatial, "temporal": do_temporal,
                "frequency": do_frequency, "fusion": do_fusion,
                "eval": do_eval, "export": do_export}


def parse_stages():
    if STAGE == "all":
        return list(ALL_STAGES)
    stages = [s.strip() for s in str(STAGE).split(",") if s.strip()]
    bad = [s for s in stages if s not in ALL_STAGES]
    if bad:
        raise SystemExit("unknown STAGE %r - pick from %s (or a comma-separated "
                         "group, or 'all')" % (bad, ALL_STAGES))
    return stages


def main():
    print("restoring artifacts from earlier kernels mounted under /kaggle/input ...")
    restore_prior_artifacts()
    stages = parse_stages()
    for st in stages:
        print("=" * 70)
        print("STAGE:", st)
        print("=" * 70)
        STAGE_RUNNER[st]()

    nxt = NEXT_STAGE.get(stages[-1])
    if STAGE != "all" and nxt:
        print()
        print("=" * 70)
        print("NEXT STAGE: %r" % nxt)
        print("  Only the stages in STAGE (cell 2) run - that is why this push")
        print("  produced only their outputs. To continue:")
        print("    1. set STAGE = %r in cell 2" % nxt)
        print("    2. in kernel-metadata.json give this push its own id (e.g.")
        print('       "kambleom/deeptrace-%s")' % nxt)
        print("    3. add the kernel(s) whose /kaggle/working this stage needs to")
        print('       "kernel_sources" (see ml/kaggle/README.md)')
        print("    4. kaggle kernels push -p .")
        print("=" * 70)


main()
"""

md_handoff = r"""## Handoff - getting the weights into the repo

1. Run `export` (or just download `weights/*.pt` from the notebook Output pane).
2. Locally, copy the four files into `ml/weights/`:

```
ml/weights/spatial_xception.pt
ml/weights/temporal_gru.pt
ml/weights/frequency_mlp.pt
ml/weights/fusion_mlp.pt
```

3. Verify with the real pipeline:

```bat
cd ml
set FFMPEG_BIN=C:\Users\OM\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe
.venv\Scripts\python -m detection.pipeline path\to\test.mp4 --out scratch\result_test
```

The `temporal_score` / `frequency_score` / `confidence` values should now be real model
outputs instead of the untrained priors in `_PRIORS`.

### Sanity checks before you ship the weights

* `spatial_score` should separate a known-real video from a known-fake one.
* Run the pipeline on one FF++ video **and** one Celeb-DF video from your test split -
  if FF++ scores well and Celeb-DF collapses to ~0.5, that is the expected (and
  reportable) cross-dataset gap, not a bug.
* Confirm the local `pipeline.py` `temporal_hidden` fix (see the repo notes) is in
  place, otherwise the fusion head receives zeros at inference while it was trained on
  real GRU states - training and inference would disagree and confidence would be wrong.
"""


def code_cell(src):
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
            "source": src.rstrip("\n").splitlines(keepends=True)}


def md_cell(src):
    return {"cell_type": "markdown", "metadata": {},
            "source": src.rstrip("\n").splitlines(keepends=True)}


cells = [
    md_cell(md_intro),
    md_cell(md_datasets),
    code_cell(cell_config),
    code_cell(cell_install),
    code_cell(cell_lib_a),
    code_cell(cell_lib_b),
    code_cell(cell_manifest),
    code_cell(cell_crops),
    code_cell(cell_spatial),
    code_cell(cell_temporal),
    code_cell(cell_frequency),
    code_cell(cell_fusion),
    code_cell(cell_eval),
    code_cell(cell_export),
    md_cell(md_handoff),
]

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.10"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(nb, indent=1), encoding="utf-8")

# validate
loaded = json.loads(OUT.read_text(encoding="utf-8"))
assert len(loaded["cells"]) == len(cells)
print("wrote", OUT, "cells:", len(loaded["cells"]))
