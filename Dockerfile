# DeepTrace application image: the API and the worker run the same code, so they share
# one image and differ only in their command.
#
# Build from the REPOSITORY ROOT (the ML package and the frontend both have to be in the
# build context):
#     docker compose build
#     docker build -t deeptrace-app .

# ── stage 1: build the React app ──────────────────────────────────────────────
# In the image rather than copied from a local dist/ so that `docker compose build` is
# self-contained: forgetting to run `npm run build` cannot silently ship an API with no UI.
FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── stage 2: the application ──────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg provides BOTH ffmpeg and ffprobe. video_utils.py shells out to `ffprobe` by name
# and is NOT affected by FFMPEG_BIN, so the binary must be on PATH - hence the apt package
# rather than a downloaded static build.
# build-essential: insightface builds Cython extensions from source.
# libglib2.0-0: runtime dependency pulled in by the ONNX/OpenCV stack.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        build-essential \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch/torchvision MUST come from the CPU index. The default PyPI wheels are the CUDA
# builds and drag in several GB of nvidia-* packages a CPU container can never use.
COPY ml/requirements.txt /app/ml/requirements.txt
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Everything else from the ML requirements, minus the three lines installed above.
# opencv-python (not -headless) pulls in GUI libraries that do not exist on a slim image.
RUN grep -vE '^(torch|torchvision|opencv-python)' /app/ml/requirements.txt > /tmp/ml-reqs.txt \
    && pip install -r /tmp/ml-reqs.txt opencv-python-headless

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt

# Bake the face model in at build time. Otherwise the first analysis in the cloud downloads
# ~300 MB to ~/.insightface and needs outbound network - a cold-start failure waiting to
# happen, in the one code path that is hardest to debug in production.
RUN python -c "\
from insightface.app import FaceAnalysis; \
app = FaceAnalysis(name='buffalo_l'); \
app.prepare(ctx_id=-1, det_size=(640, 640)); \
print('buffalo_l baked in')"

# ml/weights/*.pt are gitignored, so the image is built from the local working tree.
# .dockerignore deliberately does NOT exclude them.
COPY ml/ /app/ml/
COPY backend/ /app/backend/
COPY --from=frontend /app/dist /app/frontend/dist

WORKDIR /app/backend

EXPOSE 8000
# app.config derives these from the file location, so they resolve without configuration:
#   ML_PACKAGE_ROOT   = /app/ml
#   frontend/dist     = /app/frontend/dist
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
