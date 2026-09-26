# 07 — Docker and Containers

How your code gets packaged so it runs identically on your laptop and in Azure.

---

## The problem containers solve

Your ML pipeline depends on: a specific Python version, PyTorch, torchvision, timm, opencv,
insightface, onnxruntime, numpy, scipy… and a working `ffmpeg` binary on PATH.

Now imagine explaining all of that to a fresh Ubuntu server in Azure. You'd write a long setup
script, and it would break in some new way every time.

> "It works on my machine" is the most famous sentence in software, and this is the fix.

A **container** freezes your app *and its entire environment* into one box. That box runs the same
way on Windows, on your friend's Mac, and in an Azure data centre.

---

## Image vs container

The distinction that confuses everyone:

| | Image | Container |
|---|---|---|
| What | A read-only **template** — your code, libraries, OS bits | A **running instance** of an image |
| Analogy | A class | An object |
| Analogy | A recipe | The cooked dish |
| Analogy | A `.docx` file | The document open in Word |
| How many | One | Many, all started from the same image |

You build **one image** (`deeptrace:v1`) and run **two different containers** from it — the API
and the worker — just with different start commands. That's a nice thing to point at when the
rubric mentions *resource sharing*.

---

## The Dockerfile

A **Dockerfile** is the recipe. Ours, annotated:

```dockerfile
FROM python:3.11-slim
#    └─ the starting point: a small Debian image with Python 3.11 already installed

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \                 # video frame extraction - ALSO provides ffprobe
      libgl1 libglib2.0-0 \    # system libraries opencv needs
      build-essential          # compilers, in case a package must be built from source
 && rm -rf /var/lib/apt/lists/*
#    └─ install system-level dependencies that pip cannot provide

RUN pip install --no-cache-dir torch torchvision \
      --index-url https://download.pytorch.org/whl/cpu
#    └─ the CPU-only PyTorch build. MUCH smaller than the default CUDA build,
#       and we have no GPU. This single line saves several GB.

COPY ml/requirements.txt /app/ml/requirements.txt
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/ml/requirements.txt -r /app/backend/requirements.txt
#    └─ Python libraries. Copied first so this slow step is CACHED:
#       editing your code does not re-install PyTorch.

RUN python -c "from insightface.app import FaceAnalysis; \
               FaceAnalysis(name='buffalo_l').prepare(ctx_id=0)"
#    └─ download the face-detection model AT BUILD TIME and bake it in.
#       Otherwise the first analysis in Azure downloads ~300 MB and needs internet.
#       Baking it in makes cold starts predictable.

COPY ml/ /app/ml/
COPY backend/ /app/backend/
#    └─ your actual code LAST, including ml/weights/*.pt

WORKDIR /app/backend
ENV PYTHONPATH=/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
#    └─ the default command. The WORKER container overrides this with:
#       python -m app.worker
```

**`--host 0.0.0.0`** deserves a note: `127.0.0.1` means "only accept connections from inside this
machine", which would make the container unreachable from the internet. `0.0.0.0` means "accept
from anywhere". A classic first-container mistake.

---

## Layers and caching

Every instruction creates a **layer**. Docker reuses unchanged layers, which is why the order
above matters:

```
  FROM python:3.11-slim        ┐
  RUN apt-get ... ffmpeg       │  these change rarely
  RUN pip install torch        │  → cached, not re-run
  COPY requirements.txt        │
  RUN pip install -r ...       │
  ─────────────────────────────┘
  COPY ml/                     ┐  these change every time you edit code
  COPY backend/                │  → only these are rebuilt
  CMD ...                      ┘
```

If you copied your code *first*, every tiny edit would trigger a 10-minute PyTorch reinstall.
This is why the Dockerfile is ordered the way it is — a genuinely useful thing to explain.

---

## Registries

An image has to live somewhere Azure can pull it from: a **container registry**.

```
   your laptop                Azure Container Registry (ACR)           Azure
   ───────────                ─────────────────────────────           ─────
   docker build  ──push──►    deeptraceacr1234.azurecr.io/deeptrace:v1  ──pull──► api
                                                                        ──pull──► worker
```

You can build locally with Docker Desktop and push, or — nicer for you — let Azure build it in
the cloud with `az acr build`. That way you don't need Docker installed at all; you upload the
source and Azure builds the image for you.

`deeptrace:v1` is a **tag** — the version label. When you change things, build `v2` and update
the container apps to point at it. That's how you release a new version, and how you'd roll back.

---

## Why the image is ~3 GB (and why that's fine)

```
  PyTorch (CPU)            ~800 MB
  torchvision, timm        ~100 MB
  opencv, onnxruntime      ~300 MB
  insightface + buffalo_l  ~400 MB
  scipy, numpy, pandas     ~200 MB
  Debian + Python          ~150 MB
  your code + weights      ~90 MB
                          ─────────
                          ≈ 2.5-3.5 GB
```

That is normal for an AI application. It does have one real consequence worth knowing: **cold
starts are slow**, because a machine with no copy of the image must download ~3 GB before your
process can even start. That's the reason the API is kept at one replica (always warm) while only
the worker is allowed to scale to zero.

---

## The frontend does not need a container

You might expect a second image for the React app. We don't need one, and understanding why is
useful.

The frontend **builds down to static files** — plain HTML, CSS and JavaScript:

```bash
cd frontend
npm run build        # produces frontend/dist/  →  index.html, assets/*.js, assets/*.css
```

There is no React server at runtime. Those are just files a browser downloads. So the backend
serves them:

```python
# app/main.py
app.mount("/assets", StaticFiles(directory="frontend/dist/assets"))
# ...and any other path returns index.html, so client-side routes like /dashboard work
```

Two benefits:

1. **One web address for everything.** The API is at `/api/...` and the app is at `/`, on the same
   origin — so there is no **CORS** problem at all. CORS is the browser rule that blocks a page
   from calling a *different* domain; same-origin means the rule never applies.
2. **One deployment.** One image, two containers, one URL to screenshot.

The trade-off: a frontend-only change needs a new image build, whereas a dedicated static host
(Azure Static Web Apps, or Blob + CDN) would let you deploy the UI in seconds. For this project,
one address and no CORS is worth more than that.

---

## What you'll run locally

Phase 4 of the plan gets the whole stack running on your laptop before Azure is involved:

```
   docker compose up
   ├─ api        :8000   the FastAPI app
   ├─ worker             the queue consumer
   ├─ postgres   :5432   the database
   └─ azurite    :10000  a LOCAL EMULATOR of Azure Blob + Queue storage
```

**Azurite** is worth knowing about: Microsoft's free emulator for Azure Storage. It lets your
code use the real Azure SDKs with no cloud account, so Phase 3 tests the *actual* storage code
paths offline. A `docker-compose.yml` file wires all four together with one command.

At that point "works on my machine" becomes "works in the same shape as production", and the
only remaining unknown is Azure itself.

---

**Next:** [08 — Cloud computing concepts](08-cloud-concepts.md) — the rubric section.
