# DeepTrace Backend

The server half of DeepTrace: a FastAPI application that serves the REST API, runs the
trained ML pipeline on uploaded videos, and serves the built React frontend from the same
origin.

**New to backends?** Read [`docs/backend-cloud/`](../docs/backend-cloud/README.md) first —
it explains every concept here from zero, with a glossary.

---

## ⚠️ Two different lists called "stages" — don't mix them up

This trips people up constantly:

| Name | What it is | Where |
|---|---|---|
| **Build phases 1–7** | The order *you* build the backend in, local first, cloud last | this file, below |
| **Analysis stages (8)** | What the *ML pipeline* is doing during one video: `ingest → frames → faces → spatial → temporal → frequency → fusion → done` | `app/stages.py` |

The first is a project plan. The second is runtime progress shown in the Processing screen.
The full plan lives at `~/.commandcode/plans/deeptrace-backend-cloud-deployment.md`.

---

## Build phases

| Phase | What it means | Status |
|---|---|---|
| **1. Contract-complete API, no cloud** | FastAPI + SQLite, all six endpoints, and the frontend talking to the real API instead of its in-browser mocks. | ✅ **Done and verified** |
| **2. Real worker + queue** | Inference moved out of the API process into a worker that claims jobs from a queue. | ✅ **Done and verified** |
| **3. Postgres + Blob, locally** | `docker-compose` with PostgreSQL and the Azurite storage emulator. Real Azure SDKs, no cloud account. | ✅ **Done and verified** |
| **4. Containerise** | Dockerfile; `docker compose up` runs api + worker + postgres + azurite. | ✅ **Done and verified** |
| **5. Provision Azure** | Resource group, ACR, Storage, PostgreSQL, Log Analytics, Container Apps. | ⬜ Next |
| **6. Deploy, verify, load-test** | Public HTTPS URL, autoscaling worker, dashboards and alerts. | ⬜ |
| **7. Report artefacts** | Diagrams, screenshots, sample input/output. | ⬜ |

### How the API and the worker are split

Phase 1 had the API run each analysis itself on a `BackgroundTasks` callback. Simple, but
inference competed with HTTP for the same CPU, a restart lost in-flight work, and a second
replica could not share the load.

Phase 2 put a queue behind it: an upload records the job and hands it to a worker, which is
what lets inference move out of the request path and scale separately. All of that lives in
`app/queue.py`.

Two settings decide how a job reaches a worker, and they are independent:

**Where the message lives** — `QUEUE_BACKEND`:

| Value | Message carrier | Notes |
|---|---|---|
| `database` | the queued row itself | No extra infrastructure, transactional with the data it protects |
| `azure` | an Azure Storage Queue message | Azurite locally; the queue the containers use |

**Who runs the worker loop** — `WORKER_IN_PROCESS`:

| Value | Meaning |
|---|---|
| `true` (default) | A daemon thread inside the API process, so `uvicorn app.main:app` alone is a complete system |
| `false` | The API only queues. Something else must run `python -m app.worker` |

The containers use `azure` + `false`: the worker is its own service that scales independently
of the API, which is the whole point of the split.

### Why the database is still the source of truth

The row's `status` is authoritative even when the queue carries the message, because a real
queue gives you two guarantees that both have to be handled:

- **At-least-once delivery.** The same job can be handed to two workers. Claiming is
  therefore a compare-and-swap on the row: the second delivery finds it already taken and is
  discarded.
- **Messages go missing.** A crash between committing the row and sending the message leaves
  a job nobody will ever hear about, so the worker's periodic sweep re-sends anything that
  has been queued longer than `QUEUE_RECONCILE_MINUTES`. Duplicate messages are harmless for
  the reason above.

That is what makes the two transports genuinely interchangeable rather than two code paths
with different failure modes. It also means a broker outage degrades latency, not correctness.

A worker that dies mid-analysis would leave its row stuck on `processing` forever, so
`requeue_stale()` returns anything claimed more than `STALE_CLAIM_MINUTES` (30) ago to the
queue. Re-running is safe because analysis replaces its previous result.

### Where the rows, the bytes and the messages live

All three are behind environment variables, so no calling code knows which backend is in use:

| Setting | Development (no cloud account) | Azure |
|---|---|---|
| `STORAGE_BACKEND=local` | a directory under `backend/_storage/` | — |
| `STORAGE_BACKEND=azure` | **Azurite** in Docker | Azure Blob Storage |
| `DATABASE_URL=sqlite://…` | one file, no server | — |
| `DATABASE_URL=postgresql+psycopg://…` | **PostgreSQL** in Docker | Azure Database for PostgreSQL |
| `QUEUE_BACKEND=database` | the row itself is the message | — |
| `QUEUE_BACKEND=azure` | **Azurite** in Docker | Azure Storage Queue |

All three are environment variables, so no calling code knows which backend is in use. The
`docker-compose.yml` at the repo root brings up Postgres and Azurite so the app runs against
the same service shapes as Azure, completely offline. That is the whole point of Phase 3: if
it works here, the only thing left to change for the cloud is a set of connection strings.

Azurite's account name and key are published in Microsoft's documentation — they are **not**
secrets and only ever work against the emulator.

---

## What's built so far

```
backend/
├── app/
│   ├── main.py             FastAPI app, lifespan/startup guards, SPA mount
│   ├── config.py           Settings from environment variables
│   ├── db.py               SQLAlchemy engine, session factory, init_db
│   ├── models.py           Five tables: users, investigations, videos,
│   │                       analysis_results, evidence
│   ├── schemas.py          1:1 mirror of frontend/src/types/index.ts
│   ├── security.py         bcrypt password hashing, JWT issue/verify
│   ├── deps.py             get_current_user (Bearer token) dependency
│   ├── stages.py           the 8 analysis stage names + percentages
│   ├── mlbridge.py         the ONLY place that imports ml/ (+ weights guard)
│   ├── storage.py          Blob/local storage behind one interface
│   ├── serializers.py      database rows -> API response shapes
│   ├── logsetup.py         shared logging config (also silences the Azure SDK)
│   ├── jobs.py             the analysis job itself (transport-agnostic)
│   ├── queue.py            the queue: database or Azure Storage Queue
│   ├── worker.py           the claim-and-run loop; `python -m app.worker`
│   └── api/
│       ├── auth.py         POST /api/auth/register, /login
│       └── investigations.py   list, upload, get, evidence image
├── tests/
│   ├── conftest.py         fixtures; sets test env before importing the app
│   ├── test_contract.py    the shapes the frontend depends on
│   ├── test_flow.py        upload -> queued -> completed -> evidence served
│   ├── test_worker.py      claiming, contention, stale-claim recovery
│   ├── test_queue_transport.py  both transports, including duplicate delivery
│   └── test_storage.py     the storage interface, backend-agnostic
├── requirements.txt
└── .env.example
```

Plus, at the repo root:

| File | Purpose |
|---|---|
| `docker-compose.yml` | postgres + azurite, and from Phase 4 the api + worker containers |
| `Dockerfile` | builds the frontend, then the Python image with ffmpeg and the face model |
| `.dockerignore` | keeps `ml/.venv` and friends out of the build context |

### What is deliberately NOT here yet

| Missing | Arrives in | Why it's deferred |
|---|---|---|
| Any Azure resources | Phase 5 | Everything so far runs offline |
| Autoscaling, monitoring, alerts | Phase 6 | Needs something deployed to observe |

---

## Running it

### Option A — the whole stack in containers

```powershell
cd ..                      # repo root, where docker-compose.yml lives
docker compose up -d --build
docker compose ps          # api healthy, worker up
```

This builds the image (frontend included) and starts four containers — `api`, `worker`,
`postgres`, `azurite`. The app is then on **http://localhost:8000**, serving both the API and
the UI, because the API serves the built frontend on its own origin.

```powershell
docker compose logs -f worker   # watch jobs being claimed
docker compose logs api         # startup config, weights guard, requests
docker compose down             # stop; data survives in the named volumes
docker compose down -v          # stop and delete the data too
```

`ANALYSIS_MODE` defaults to `real` in the containers. Set it to `fake` to exercise the UI
without spending CPU on inference:

```powershell
set ANALYSIS_MODE=fake&& docker compose up -d
```

It is defined once for both containers in the compose file, so the API and the worker can
never disagree about it.

### Option B — backing services in containers, app on the host

Better for iterating on Python: uvicorn reloads, and there is no image rebuild. Start only
the two backing services and run the app against them from `backend/`.

### Start the backing services

```powershell
cd ..                 # repo root, where docker-compose.yml lives
docker compose up -d  # PostgreSQL on 5433, Azurite on 10000-10002
docker compose ps     # wait until postgres reports (healthy)
```

`.env.example` already points at both, so copy it across:

```powershell
cd backend
copy .env.example .env
```

`docker compose down` stops them; `docker compose down -v` also wipes the data volumes.
Postgres is on host port **5433** because this machine already runs its own PostgreSQL on
5432 — change the left-hand side of the mapping in `docker-compose.yml` if that is not your
situation.

### First time

```powershell
cd backend
..\ml\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The venv lives at `ml/.venv` (shared with the ML package). `requirements.txt` includes the
PostgreSQL driver and Azure SDKs used from Phase 3 onward — harmless to install now.

### Start the API

```powershell
cd backend
..\ml\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Then open:

- **`http://localhost:8000/docs`** — interactive API documentation, with a "Try it out"
  button on every endpoint. Screenshot this for your report.
- `http://localhost:8000/api/health` — service status, including whether the model
  checkpoints were found.

Tables are created on startup, so the first request is enough to get going. With the values
in `.env.example` the data lives in the Postgres and Azurite containers, not on disk. Switch
to `DATABASE_URL=sqlite:///…` plus `STORAGE_BACKEND=local` for a Docker-free setup, which
writes `backend/deeptrace.db` and `backend/_storage/` instead — both gitignored.

> **Changed the database schema?** There is no migration tool. With SQLite, delete
> `backend/deeptrace.db`. With PostgreSQL, `docker compose down -v` then `up -d` recreates an
> empty database. Alembic is the obvious next step and is listed under limitations.

### Running the worker

By default (`WORKER_IN_PROCESS=true`) there is nothing extra to do — the API runs the worker
loop itself. To run them as separate processes, the way the containers do:

```powershell
# terminal 1 - the API only queues
$env:WORKER_IN_PROCESS="false"
..\ml\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

# terminal 2 - the worker analyses
$env:ANALYSIS_MODE="real"
..\ml\.venv\Scripts\python.exe -m app.worker

# drain whatever is queued, then exit (handy in scripts)
..\ml\.venv\Scripts\python.exe -m app.worker --once
```

`ANALYSIS_MODE` has to be set on the **worker**, because that is the process doing the
analysis. `/api/health` reports `queue_backend` and `queue_depth`, so a growing `queued`
count is your signal that the worker is not running.

### Two run modes

| Mode | How | When to use |
|---|---|---|
| **real** | `$env:ANALYSIS_MODE="real"` | Default. Runs the trained PyTorch pipeline, ~20 s per video. |
| **fake** | `$env:ANALYSIS_MODE="fake"` | Returns a canned result instantly. Use for UI work and tests — a 20-second CPU wait per upload makes frontend iteration painful. |

```powershell
$env:ANALYSIS_MODE="fake"
..\ml\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

### Configuration

Copy `.env.example` to `.env` and edit. Every setting is read from the environment, which is
what lets the *same* container image run locally and in Azure with no code changes.

The ones that matter most:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./deeptrace.db` | A file by default; `.env.example` points it at PostgreSQL |
| `STORAGE_BACKEND` | `local` | `azure` = Blob: Azurite locally, Azure in the cloud |
| `STORAGE_CONNECTION` | *(empty)* | Required when `STORAGE_BACKEND=azure` |
| `STORAGE_CONTAINER` | `deeptrace` | Blob container name |
| `QUEUE_BACKEND` | `database` | `azure` uses Azure Storage Queue (Azurite locally) |
| `WORKER_IN_PROCESS` | `true` | `false` runs the worker as its own process (see above) |
| `QUEUE_NAME` | `jobs` | Queue name; `QUEUE_CONNECTION` falls back to `STORAGE_CONNECTION` |
| `JWT_SECRET` | `dev-secret-change-me` | **Must be changed.** In Azure it comes from a Container Apps secret. |
| `ANALYSIS_MODE` | `real` | `fake` for tests/UI work |
| `REQUIRE_WEIGHTS` | `true` | Refuse to start without the 4 checkpoints |
| `STALE_CLAIM_MINUTES` | `30` | How long a claim may sit idle before another worker takes the job back |

---

## Testing

```powershell
cd backend
..\ml\.venv\Scripts\python.exe -m pytest tests -q
```

**40 tests, all passing — on both stacks.** They cover five things:

- **`test_contract.py`** — the promise made in `frontend/src/types/index.ts`. Status codes,
  field names, that `_meta` never leaks into a result, that `progress.stage` is always one of
  the 8 known values, that a queued job carries no `result`.
- **`test_flow.py`** — upload → queued → completed → evidence image served as JPEG, that the
  API itself never analyses anything, and that a raising job ends `failed` rather than stuck.
- **`test_worker.py`** — claiming takes a job exactly once, two workers never get the same
  job, a dead worker's claim is recovered, and a live worker keeps its job.
- **`test_queue_transport.py`** — both transports behave identically, including the case that
  matters: a duplicate delivery must not run the job twice.
- **`test_storage.py`** — the storage interface, deliberately backend-agnostic so it passes
  against local files *and* Azurite.

Tests run in `ANALYSIS_MODE=fake` with a throwaway database and storage, so they need no GPU,
no network and no model files. They set `WORKER_IN_PROCESS=false` and drive `worker.run_once()`
themselves — single-threaded, so the claim assertions are deterministic rather than a race
against a background thread.

Because `conftest.py` only *defaults* `DATABASE_URL`, `STORAGE_BACKEND` and `QUEUE_BACKEND`,
the same suite runs against the cloud service shapes with no code changes:

```powershell
# SQLite + local files + database queue (fast, no Docker needed)
..\ml\.venv\Scripts\python.exe -m pytest tests -q

# PostgreSQL + Azurite (Blob *and* Azure Storage Queue)
$env:DATABASE_URL="postgresql+psycopg://deeptrace:deeptrace@127.0.0.1:5433/deeptrace_test"
$env:STORAGE_BACKEND="azure"
$env:STORAGE_CONNECTION="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
$env:QUEUE_BACKEND="azure"
..\ml\.venv\Scripts\python.exe -m pytest tests -q
```

The same 40 passing on both is the evidence that the two storage backends, the two queue
transports and the two databases really are interchangeable — which is what makes the move to
Azure a set of connection strings rather than a rewrite.

### Manual end-to-end check

```powershell
# register + log in
curl -X POST http://localhost:8000/api/auth/register -H "Content-Type: application/json" -d "{\"email\":\"me@example.com\",\"password\":\"pw123456\"}"
```

Then use `/docs` to upload a video and watch `progress.stage` advance through the 8 stages.

---

## The API at a glance

Base path `/api`. Every error returns `{"detail": "..."}` because the frontend reads that
field to display the message.

| Method | Path | Purpose | Success |
|---|---|---|---|
| POST | `/api/auth/register` | Create an account | 201 |
| POST | `/api/auth/login` | Log in, get a Bearer token | 200 |
| GET | `/api/investigations` | List my investigations, newest first | 200 |
| POST | `/api/investigations` | Upload a video (multipart `file` + optional `title`) | 201 |
| GET | `/api/investigations/{id}` | One investigation — **this is what the UI polls** | 200 |
| GET | `/api/investigations/{id}/evidence/{evidenceId}` | One Grad-CAM heatmap image | 200 |
| GET | `/api/health` | Service + model-checkpoint status | 200 |

### How a request flows

```
POST /api/investigations
   ├─ stream the upload to a temp file, enforcing the size limit
   ├─ compute sha256 + size; ffprobe for duration/fps/resolution
   ├─ insert the investigation row with status "queued"   <-- this IS the enqueue
   └─ return 201 in well under a second — no result yet

then, in a worker (its own process, or a thread inside the API):
   claim_next() flips the row to "processing" — a compare-and-swap, so only one
   worker can win it
   run the ML pipeline, writing progress.stage as it goes
   upload the heatmaps, save the result, set status "completed"

meanwhile the browser polls:
   GET /api/investigations/{id}     every 1.8 s, until status is completed
```

This is why the upload response has no `result` field: there isn't one yet.

---

## Two safeguards worth knowing about

**1. Missing checkpoints stop the server.** If any of the four files in `ml/weights/` is
absent, the ML pipeline silently substitutes placeholder scores around **0.10–0.15** and keeps
going — you'd get confident, meaningless verdicts with no error. `app/mlbridge.py` therefore
refuses to start. If you ever see confidences clustered around 0.10–0.15, that's the tell.

**2. `_meta` is stripped.** The pipeline adds a `_meta` block (timing, frame count) that is
not part of the frontend's type. It's stored in `analysis_results.meta` for our records and
removed from the API response. A test asserts this.

---

## Known limitations

- **Real inference hasn't been exercised over HTTP yet** — the repository contains no video
  with a face. Needs one real clip from the ff-c23 or celeb-df datasets. The fake-mode path is
  fully verified, and the real path is verified at the model-loading level.
- **No migrations.** A schema change means dropping and recreating the database, so there is
  no way to evolve a deployed database in place. Alembic is the standard answer.
- **The worker polls.** Azure Storage Queue has no push delivery, so a worker calls
  `receive_messages` once a second. That is inherent to the service rather than a shortcut,
  and it is one cheap HTTP call per second per idle worker.
- **The image is 5.2 GB**, against the plan's estimate of 2.5–3.5 GB. Two contributors: the
  single-stage build keeps `build-essential` in the final layer, and torch's CPU wheel plus
  onnxruntime are large. Purging `build-essential` in the same `RUN` that uses it would claw
  some back, at the cost of re-installing on every dependency change. It matters because
  image size drives Container Apps cold-start time.
- **No retry on transient failure.** A job that raises is marked `failed` immediately; only
  *interrupted* jobs (stale claims) get re-run automatically.
- **Fixed decision thresholds misclassify out-of-distribution video** — a known ML issue
  documented in `docs/kaggle-pipeline-brief.md`. The API returns all four raw scores so the
  caveat stays visible; don't present `confidence` as calibrated.

---

## Where to go next

| Want to… | Read |
|---|---|
| Understand any concept here from scratch | [`docs/backend-cloud/README.md`](../docs/backend-cloud/README.md) |
| See the full build plan and the trap list | `~/.commandcode/plans/deeptrace-backend-cloud-deployment.md` |
| Understand the ML pipeline this calls | [`docs/ml-guide.md`](../docs/ml-guide.md) |
| Understand the frontend contract | [`docs/frontend-guide.md`](../docs/frontend-guide.md) |
