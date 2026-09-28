# DeepTrace

**Deepfake detection as a real cloud service.** Upload a video in a browser; a fleet of
containers analyses it with a four-branch PyTorch pipeline and returns a verdict, a confidence,
and Grad-CAM heatmaps showing *which part of the face* drove the decision.

Deployed on AWS ECS Fargate in `ap-south-1`, with S3 for media, RDS PostgreSQL as the source of
truth, SQS between the API and the workers, and autoscaling that follows the queue.

**Live demo:** http://deeptrace-alb-102696666.ap-south-1.elb.amazonaws.com

> The demo runs on a personal AWS account and may be torn down. `docs/aws-deployment.md` has the
> full provisioning and teardown procedure.

---

## Diagrams at a glance

Every diagram below renders inline on GitHub. Two more are interactive HTML files you open in a
browser — trace a path, focus a node, step through named views:

| Diagram | Where |
|---|---|
| System architecture | [below](#1-system-architecture) |
| One upload, end to end | [below](#2-one-upload-end-to-end) |
| A job's life cycle | [below](#3-a-jobs-life-cycle) |
| The four-branch ML pipeline | [below](#4-the-four-branch-ml-pipeline) |
| Where every byte lives | [below](#5-where-every-byte-lives) |
| Network isolation and trust | [`docs/diagrams/aws-architecture.html`](docs/diagrams/aws-architecture.html) |
| The same journey as a sequence | [`docs/diagrams/upload-sequence.html`](docs/diagrams/upload-sequence.html) |

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Using it](#using-it)
- [The ML pipeline](#the-ml-pipeline)
- [Testing](#testing)
- [AWS deployment](#aws-deployment)
- [Project layout](#project-layout)
- [Configuration](#configuration)
- [Bugs this project found the hard way](#bugs-this-project-found-the-hard-way)
- [Known limitations](#known-limitations)
- [Documentation](#documentation)

---

## What it does

Upload a video and DeepTrace answers three questions:

1. **Is it manipulated?** — `LIKELY_MANIPULATED`, `LIKELY_AUTHENTIC`, or `INCONCLUSIVE`
2. **How confident is that?** — a fusion score, plus the three branch scores behind it
3. **Where did it look?** — Grad-CAM heatmaps over the suspicious frames

The system is deliberately honest about uncertainty. A detector that never says "I don't know"
is overclaiming, and for a forensic tool a wrong confident answer is worse than an honest
uncertain one — so anything between the two thresholds reports `INCONCLUSIVE` rather than
rounding to a guess.

**The design point worth noticing:** the API never analyses anything. It stores bytes, writes a
row, and hands a message to a queue, then answers `201` in about a tenth of a second. A separate
worker does the inference. That split lets inference scale independently of HTTP — and it is why
the same code runs as two containers on one laptop and as two autoscaling ECS services in the
cloud.

---

## 1. System architecture

```mermaid
flowchart LR
  B["Operator browser<br/>React SPA + video player"]
  ALB["Application Load Balancer<br/>public :80 · /api/health"]
  API["ECS Fargate — API<br/>uvicorn · 0.5 vCPU / 1 GB"]
  WK["ECS Fargate — Worker<br/>python -m app.worker · 1 vCPU / 4 GB"]
  S3[("S3 bucket<br/>videos/ + heatmaps/")]
  RDS[("RDS PostgreSQL<br/>db.t4g.micro")]
  SQS["SQS queue<br/>deeptrace-jobs"]
  SM["Secrets Manager<br/>DB URL + JWT secret"]
  IAM["IAM task roles<br/>one bucket + one queue"]
  ECR["ECR<br/>5.25 GB image"]

  B -->|"HTTP :80"| ALB
  ALB -->|":8000 · ALB security group only"| API
  API -->|"put video · get heatmap"| S3
  API -->|"job row is the truth"| RDS
  API -->|"enqueue an investigation id"| SQS
  SQS -->|"20 s long poll"| WK
  WK -->|"put heatmaps"| S3
  WK -->|"claim + result"| RDS
  SM -.->|"injected at start"| API
  IAM -.->|"assumed at runtime"| WK
  ECR -.->|"image pull"| API
```

**Read it as three lanes:** the browser on the left, the two containers in the middle, and the
four stores on the right. The API and the worker never talk to each other — they only meet
through S3, RDS and SQS, which is what makes them independently replaceable and independently
scalable.

The interactive version, which also shows the security-group chain and lets you trace a path:
**[`docs/diagrams/aws-architecture.html`](docs/diagrams/aws-architecture.html)**.

### Why each service

| Service | Role | Why not something simpler |
|---|---|---|
| **ECS Fargate** | runs both containers, no servers to patch | Lambda is the wrong shape: inference takes 20–30 s and the worker is a long-running poller, not a request handler |
| **ALB** | stable public entry point | task IPs change on every deployment; the DNS name does not |
| **S3** | videos and heatmaps | a container's disk dies with the container |
| **RDS PostgreSQL** | users, results, job state | relational data with transactions and foreign keys |
| **SQS** | carries job messages | decouples the API from inference, absorbs bursts, and gives autoscaling something to scale on |
| **Secrets Manager** | database URL, JWT signing key | so no credential is ever baked into an image |
| **IAM task roles** | permissions for the running containers | temporary credentials are assumed at runtime, so **no access keys exist anywhere in the app** |
| **ECR** | holds the 5.25 GB image | Fargate has nowhere local to pull from |
| **CloudWatch** | logs, dashboard, alarms | the only way to see inside a container |
| **Application Auto Scaling** | worker 1→4, API 1→3 | capacity follows demand |

---

## 2. One upload, end to end

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant A as API · Fargate
    participant S as S3
    participant D as RDS
    participant Q as SQS
    participant W as Worker · Fargate

    B->>A: POST /api/investigations
    A->>S: put the video
    A->>D: insert a row, status = queued
    A->>Q: enqueue INV-0005
    A-->>B: 201 Created, in about 0.1 s
    Note over B: the UI polls every 1.8 s from here

    Q->>W: deliver the message
    W->>D: claim it — UPDATE ... WHERE status = 'queued'
    D-->>W: rowcount = 1, so this worker won
    W->>S: get the video back
    W->>W: run the four-branch pipeline
    W->>S: put the Grad-CAM heatmaps
    W->>D: save verdict, scores, evidence
    W->>Q: delete the message

    B->>A: poll GET /api/investigations/INV-0005
    A-->>B: completed, verdict, evidence
    B->>A: GET .../evidence/ev-001, with the token
    A-->>B: image/jpeg, drawn from a blob URL
```

The same journey in prose, if you prefer text:

1. The API streams the upload to a temp file, checks the size, hashes it, and probes it.
2. It puts the video in S3 and inserts a row with `status = "queued"`. **That row is the job** —
   committing it is the enqueue.
3. It sends the id to SQS and answers `201`. Step 3 is best-effort: if the send fails it is only
   logged, because the row already exists and the worker's sweep will re-send it.
4. A worker long-polls the queue, receives the id, and claims the row with a compare-and-swap.
5. It pulls the video from S3 and runs the pipeline, writing `progress.stage` as it goes.
6. Heatmaps go to S3, the verdict and evidence rows go to RDS, and the message is deleted.
7. The browser's next poll sees `completed` and renders the result.

The interactive version: **[`docs/diagrams/upload-sequence.html`](docs/diagrams/upload-sequence.html)**.

---

## 3. A job's life cycle

```mermaid
stateDiagram-v2
    direction LR
    [*] --> queued : upload commits the row
    queued --> processing : a worker claims it
    processing --> completed : the pipeline succeeds
    processing --> failed : the pipeline raises
    processing --> queued : the claim went stale
    completed --> [*]
    failed --> [*]

    note right of processing
        Claiming is a compare-and-swap:
        a duplicate message finds the row
        already taken and is discarded.
        Analysis is idempotent, so a re-run
        replaces the previous result.
    end note
```

Two recovery paths exist because two different things can go wrong:

| What went wrong | How it is detected | How it recovers |
|---|---|---|
| The message never arrived | the row sits in `queued` longer than `QUEUE_RECONCILE_MINUTES` (5) | the worker's sweep re-sends it — the transactional-outbox pattern |
| The worker died mid-analysis | the row sits in `processing` with `claimed_at` older than `STALE_CLAIM_MINUTES` (30) | the row goes back to `queued` and another worker takes it |
| The message was delivered twice | the claim's `WHERE status = 'queued'` matches nothing | the duplicate is discarded, not analysed twice |

**The queue is never the record of what work exists — the database row is.** The queue only
decides how a worker finds out. That is the whole reason a lost or duplicated message is
survivable rather than fatal.

---

## 4. The four-branch ML pipeline

```mermaid
flowchart LR
    V["Uploaded video"] --> F["Sample 8 frames<br/>frame_budget"]
    F --> FA["Detect and align faces<br/>insightface · 224×224"]
    FA --> SP["Spatial<br/>Xception"]
    FA --> TE["Temporal<br/>GRU · 128 hidden"]
    FA --> FR["Frequency<br/>256 DCT stats → MLP"]
    SP --> FU["Fusion MLP<br/>2048 + 128 + 256 → 1"]
    TE --> FU
    FR --> FU
    FU --> VD["Verdict + confidence"]
    SP --> GC["Grad-CAM<br/>top 3 suspicious frames"]
    GC --> OUT["Results page"]
    VD --> OUT

    classDef branch fill:#e8f4ff,stroke:#3b82f6,color:#0b2545
    classDef warn fill:#fff4e5,stroke:#f59e0b,color:#5a3800
    class SP,TE,FR,FU branch
    class GC,VD,OUT warn
```

| Branch | Model | What it looks for |
|---|---|---|
| **Spatial** | Xception (timm, ImageNet-pretrained then fine-tuned) | blending seams and texture inconsistencies, per aligned face crop |
| **Temporal** | GRU over backbone features | flicker and inconsistency *between* frames — deepfakes often stutter |
| **Frequency** | MLP over 256 DCT statistics | high-frequency spectral traces left by upsampling and generation |
| **Fusion** | MLP over the concatenated features | learns how much to trust each branch |

**Why four?** Because they fail differently. On one test clip the spatial branch scored a frame
**0.9993** while the fusion head returned only **0.455** — the temporal branch disagreed, and the
fusion head had learned to weigh it. One model alone would have reported false certainty.

The eight runtime stages the UI shows are driven by a single source of truth in
`backend/app/stages.py`, so the frontend and backend cannot disagree:

```
ingest → frames → faces → spatial → temporal → frequency → fusion → done
```

> **Not to be confused with build phases.** "Stages" here are what the pipeline does to one
> video at runtime. Build phases are the order the project was constructed in.
> `backend/README.md` opens with a warning about this because it trips everyone up.

Thresholds, all in `ml/config/defaults.yaml`:

| Score | Meaning |
|---|---|
| fusion ≥ 0.60 | `LIKELY_MANIPULATED` |
| fusion ≤ 0.40 | `LIKELY_AUTHENTIC` |
| in between | `INCONCLUSIVE` — said out loud, not rounded to a guess |
| frame ≥ 0.50 | suspicious, so eligible for a heatmap (at most 3 rendered) |

### Running the pipeline on its own

No backend, no queue — one video in, JSON and heatmaps out:

```bash
cd ml
python -m detection.pipeline path/to/clip.mp4 --out scratch/results --json scratch/result.json
```

### Training

Trained on Kaggle with no local GPU, on **4,690 videos** from FaceForensics++ (c23) and Celeb-DF
v2. Stages train in order, because each head consumes the previous stage's features:

```bash
cd ml
python -m detection.train --stage spatial     # then temporal, frequency, fusion
python -m detection.train --stage all
```

| Metric | Value |
|---|---|
| Fusion AUC, ff-c23 test split (n=320) | **0.954** |

**Read that number sceptically, and say so.** The evaluation has a documented dataset-leakage
problem: Celeb-DF v2 appears in the training data, so its "cross-dataset" score comes out
*higher* than the training distribution — impossible for a genuinely held-out set. The full
analysis is in [`docs/kaggle-pipeline-brief.md`](docs/kaggle-pipeline-brief.md), and the fix is a
retrain with strictly disjoint splits. The API returns all four raw scores so this caveat stays
visible, and `confidence` should not be presented as calibrated.

`ml/kaggle/` holds the notebook, the dataset registry and `push.py`, which syncs checkpoints
between Kaggle and `ml/weights/`.

---

## 5. Where every byte lives

```mermaid
flowchart TB
    subgraph app["The application — stateless"]
        API["API container"]
        WK["Worker container"]
    end

    subgraph media["Media — the bytes"]
        S3[("S3 bucket<br/>videos/INV-0005/clip.mp4<br/>heatmaps/INV-0005/*.jpg")]
    end

    subgraph facts["Facts — the records"]
        RDS[("RDS PostgreSQL<br/>users · investigations<br/>videos · analysis_results · evidence")]
    end

    subgraph work["Work — the signal"]
        Q["SQS queue<br/>just: INV-0005"]
    end

    API -->|"write the video"| S3
    WK -->|"write the heatmaps"| S3
    API -->|"insert the job row"| RDS
    WK -->|"claim · save the result"| RDS
    API -->|"enqueue"| Q
    Q -->|"deliver"| WK

    classDef store fill:#f3f0ff,stroke:#7c3aed,color:#2a1065
    class S3,RDS,Q store
```

The point of this picture: **the database stores keys, never bytes.** The videos and heatmaps are
S3 objects; RDS holds the rows that describe them. Nothing durable lives on a container's disk,
which is why either container can be killed and replaced at any moment without losing data.

| What | Where | Contains |
|---|---|---|
| Video files | **S3** `videos/INV-0005/…` | the uploaded bytes |
| Heatmaps | **S3** `heatmaps/INV-0005/…` | Grad-CAM JPEGs |
| Job state | **RDS** `investigations` | status, stage, pct, `claimed_at`, `worker_id` |
| Video metadata | **RDS** `videos` | sha256, duration, fps, resolution, **the S3 key** |
| Verdicts | **RDS** `analysis_results` | verdict, confidence, three branch scores, frame scores, segments |
| Evidence rows | **RDS** `evidence` | frame number, timestamp, score, **the S3 key** |
| Accounts | **RDS** `users` | email, bcrypt hash |
| The work signal | **SQS** `deeptrace-jobs` | one investigation id per message, transient |
| Database password, JWT key | **Secrets Manager** | never in the image, never in the repo |
| Working files mid-analysis | the worker's `/tmp` | deleted when the job ends |

---

## Quick start

### Prerequisites

| Need | For |
|---|---|
| **Docker Desktop** | the whole stack in containers |
| **Python 3.11** | the ML pipeline and the backend |
| **Node 22+** | the frontend |
| **ffmpeg** on `PATH` | video probing and frame extraction |

The ML checkpoints (`ml/weights/*.pt`) are **not** in the repository — they are build artefacts.
See [training](#training) to produce them. Without them the API refuses to start by design: the
pipeline would otherwise substitute placeholder scores and return confident nonsense.

### Option A — everything in Docker (recommended)

```bash
git clone <this repo> && cd deeptrace
docker compose up -d --build      # builds the image, starts four containers
docker compose ps                 # api healthy, worker up, postgres healthy, moto up
```

Open **http://localhost:8000** — the API serves the built React UI from the same origin.

```bash
docker compose logs -f worker     # watch jobs being claimed
docker compose down               # stop; data survives in named volumes
docker compose down -v            # stop and delete the data too
```

Four containers come up: `api`, `worker`, `postgres` (published on **5433**, not 5432, because
this machine already had a PostgreSQL there) and `moto` (both the S3 and SQS emulators on
**5000**). Real Postgres, a real S3 API, a real SQS API — and no cloud account needed.

### Option B — backing services in Docker, app on the host

Better for iterating on Python: uvicorn reloads and there is no image rebuild.

```bash
docker compose up -d              # just postgres and moto
cd backend
cp .env.example .env
../ml/.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

### Option C — no Docker at all

SQLite and a local directory instead of Postgres and S3. Good for a quick look at the API.

```bash
cd backend
# in .env:
#   DATABASE_URL=sqlite:///./deeptrace.db
#   STORAGE_BACKEND=local
#   QUEUE_BACKEND=database
../ml/.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

### The frontend on its own

```bash
cd frontend
npm install
npm run dev          # Vite with MSW mocks — no backend needed, for UI work
npm run dev:api      # Vite against the real API on :8000
npm run build        # tsc -b && vite build
npm run lint         # oxlint
```

---

## Using it

1. **Register** at `/register`, then sign in. Auth is JWT; the token is kept in `localStorage`
   and sent as a `Bearer` header.
2. **Upload** a video on `/upload` — up to 200 MB, `.mp4` / `.mov` / `.webm`. One clear frontal
   face, 2–8 seconds, decent lighting gives the best results.
3. **Watch it process.** The UI polls and the Processing screen advances through the eight
   analysis stages as the worker reports them.
4. **Results** at `/results/{id}` — verdict, confidence, the three branch scores, a frame-level
   score timeline with suspicious segments shaded, the source video (playable and seekable), and
   the Grad-CAM heatmaps. Click a thumbnail to switch between them.
5. **Forensic report** at `/report/{id}` — a printable document with the heatmaps embedded.
   **Export PDF** opens the browser's print dialog with the destination set to "Save as PDF"; a
   print stylesheet drops the app chrome and repaints the report light. **Export JSON** gives the
   same result as machine-readable data.

### What a good result looks like

| Clip | Label | Verdict | Confidence | Heatmaps |
|---|---|---|---|---|
| `face_astronaut.mp4` | real | `LIKELY_AUTHENTIC` | 0.394 | 0 (no suspicious frames) |
| `01_02__exit_phone_room__…mp4` (DFDC) | — | `LIKELY_MANIPULATED` | 0.715 | 3 |
| `01__kitchen_still.mp4` (DFDC) | — | `INCONCLUSIVE` | 0.455 | 3 |

The third row is the interesting one: individual frames score **0.9993** before the fusion head
weighs them against a dissenting temporal branch. A saturated per-frame score is not a certain
verdict, which is exactly what the four-branch design is for.

> **Why a heatmap says 100%.** The Grad-CAM map is min-max normalised per frame
> (`(cam - cam.min()) / (cam.max() - cam.min())`), so its hottest pixel is always 1.0 regardless
> of absolute activation. It shows *where the model looked within that frame*, not how confident
> it is. The percentage badge beside a heatmap is a different number: that frame's spatial score.

**A video with no detectable face fails cleanly** with `No face detected in any sampled frame`
rather than being handed an invented verdict. That is deliberate.

---

## The ML pipeline

See [diagram 4](#4-the-four-branch-ml-pipeline) above for the shape of it. Faces are detected and
aligned with `insightface` (buffalo_l, RetinaFace-based detection) at 224×224, and eight frames
are sampled per video — a CPU budget rather than a temporal-window limit.

---

## Testing

```bash
cd backend
../ml/.venv/Scripts/python.exe -m pytest tests -q
```

**48 tests, all passing — on every stack.** They cover six areas:

| File | What it pins down |
|---|---|
| `test_contract.py` | the promise made in `frontend/src/types/index.ts` — status codes, field names, that `_meta` never leaks, that `progress.stage` is always one of the 8 |
| `test_flow.py` | upload → queued → completed → evidence served, that the API itself never analyses anything, and that a raising job ends `failed` rather than stuck |
| `test_worker.py` | claiming takes a job exactly once, two workers never get the same one, a dead worker's claim is recovered, a live worker keeps its job |
| `test_queue_transport.py` | all three transports behave identically, including that a duplicate delivery must not run the job twice |
| `test_storage.py` | the storage interface, backend-agnostic so it passes against local files, Moto *and* Azurite |
| `test_video.py` | the source-video route: bytes come back identical, all three shapes of HTTP range request work, an unsatisfiable range is 416 not a crash, and one user cannot fetch another's footage |

Tests run in `ANALYSIS_MODE=fake` with a throwaway database, so they need no GPU, no network and
no model files. `conftest.py` only *defaults* `DATABASE_URL`, `STORAGE_BACKEND` and
`QUEUE_BACKEND`, so the same suite runs against real service shapes:

```bash
# SQLite + local files + database queue (fastest, no Docker)
pytest tests -q

# PostgreSQL + Moto (S3 and SQS) — the stack the containers run
$env:DATABASE_URL="postgresql+psycopg://deeptrace:deeptrace@127.0.0.1:5433/deeptrace_test"
$env:STORAGE_BACKEND="s3"; $env:QUEUE_BACKEND="sqs"; $env:QUEUE_NAME="deeptrace-tests"
pytest tests -q
```

> **One caveat about `fake` mode.** It bypasses the ML entirely, so it cannot catch a bug in the
> pipeline. That limitation had consequences — see below.

---

## AWS deployment

Everything is provisioned through the AWS CLI; there is no console clicking in the procedure.
[`docs/aws-deployment.md`](docs/aws-deployment.md) is the full runbook, and
[`deploy/teardown-aws.ps1`](deploy/teardown-aws.ps1) removes it all in the right order.

| Resource | Identifier |
|---|---|
| Region | `ap-south-1` (Mumbai) |
| ALB | `deeptrace-alb-102696666.ap-south-1.elb.amazonaws.com` |
| ECS | cluster `deeptrace`; services `deeptrace-api` (0.5 vCPU / 1 GB) and `deeptrace-worker` (1 vCPU / 4 GB) |
| RDS | `deeptrace-pg`, `db.t4g.micro`, PostgreSQL 16, **not publicly accessible** |
| S3 | `deeptrace-838882524724` — `videos/` and `heatmaps/` |
| SQS | `deeptrace-jobs` |
| ECR | 5.25 GB image, 1.58 GB compressed |
| Secrets | `deeptrace/app` — `DATABASE_URL`, `JWT_SECRET` |
| IAM | `deeptrace-task-execution`, `deeptrace-task` (one bucket + one queue only) |
| Monitoring | dashboard `deeptrace`, 4 alarms, SNS topic `deeptrace-alerts`, $10 budget alert |
| Autoscaling | worker 1→4 on SQS backlog (target 3); API 1→3 on CPU (target 60%) |

### Network isolation

```mermaid
flowchart LR
    NET(["Internet"]) -->|":80"| ALB["deeptrace-alb SG<br/>accepts :80 from anywhere"]
    ALB -->|":8000"| TASK["deeptrace-tasks SG<br/>accepts only from the ALB SG"]
    TASK -->|":5432"| RDS["deeptrace-rds SG<br/>accepts only from the tasks SG"]
    RDS --> DB[("RDS PostgreSQL<br/>no public address")]
    TASK --> OUT(["S3 · SQS · Secrets Manager<br/>via the IAM task role"])

    classDef open fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef shut fill:#dcfce7,stroke:#16a34a,color:#052e16
    class NET open
    class DB,OUT shut
```

Each security group trusts only the group in front of it, so "who can reach the database?" is
answered by the graph rather than by an IP range somebody has to maintain. The tasks have public
IPs — there is no NAT gateway, which would cost more than the rest of the stack — but they accept
no inbound traffic except from the ALB, and RDS is not reachable from the internet at all.

### Cost

About **$3.05/day** running continuously — ALB $0.60, Fargate $1.90, RDS $0.45, storage and
monitoring about $0.10. Idle cost is dominated by the ALB and RDS; deleting the ALB and stopping
RDS is the cheap way to pause. `deploy/teardown-aws.ps1` removes everything.

---

## Project layout

```
.
├── frontend/                     React 19 + Vite + Tailwind 4 single-page app
│   └── src/
│       ├── features/             landing, auth, upload, processing, results, report, dashboard
│       ├── components/ui/        the design system primitives
│       ├── lib/                  api client, auth store, stage names, utils
│       ├── mocks/                MSW handlers and fixtures, for `npm run dev`
│       └── types/index.ts        THE API contract — the backend mirrors this exactly
│
├── backend/                      FastAPI application, API and worker
│   ├── app/
│   │   ├── main.py               app, lifespan guards, SPA mount
│   │   ├── config.py             every setting, from environment variables
│   │   ├── models.py             five tables: users, investigations, videos,
│   │   │                         analysis_results, evidence
│   │   ├── schemas.py            1:1 mirror of the frontend's types
│   │   ├── security.py / deps.py bcrypt, JWT, get_current_user
│   │   ├── stages.py             the 8 analysis stages and their percentages
│   │   ├── mlbridge.py           the ONLY module that imports ml/ (+ the weights guard)
│   │   ├── storage.py            S3 / Azure Blob / local, behind one interface
│   │   ├── queue.py              database / SQS / Azure Queues, behind one interface
│   │   ├── jobs.py               the analysis job itself
│   │   ├── worker.py             the claim-and-run loop
│   │   ├── serializers.py        rows -> API response shapes
│   │   └── api/                  auth.py, investigations.py
│   ├── tests/                    48 tests
│   └── README.md                 the backend in depth
│
├── ml/                           the detection package
│   ├── detection/
│   │   ├── pipeline.py           the analyzer: stages, branches, verdict, Grad-CAM
│   │   ├── train.py              staged training
│   │   ├── evaluate.py           AUC / ACC / AP reporting
│   │   ├── models/               backbone, GRU, frequency MLP, fusion MLP, DCT features
│   │   ├── explain/gradcam.py    channel-average gradient-weighted activation
│   │   └── data/                 video utils and datasets
│   ├── config/defaults.yaml      thresholds, backbone, frame budget
│   ├── kaggle/                   notebook, dataset registry, push.py
│   └── weights/                  4 checkpoints (gitignored)
│
├── docs/                         see the documentation index below
│   └── diagrams/                 interactive architecture + sequence diagrams (HTML)
│
├── deploy/teardown-aws.ps1       removes the whole AWS stack, in order
├── Dockerfile                    multi-stage: builds the UI, then the Python image
└── docker-compose.yml            postgres, moto, api, worker (azurite behind a profile)
```

---

## Configuration

Every setting is an environment variable, which is what lets the same image run locally and in
the cloud with no code change. `backend/.env.example` documents all of them; the ones that
matter:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./deeptrace.db` | Postgres in Docker and in AWS |
| `STORAGE_BACKEND` | `local` | `s3` \| `azure` \| `local` |
| `S3_BUCKET` / `S3_REGION` | `deeptrace` / `ap-south-1` | |
| `S3_ENDPOINT_URL` | *(empty)* | points at Moto locally; **empty in AWS** |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | *(empty)* | dummy values for Moto; **empty in AWS**, where the task role supplies credentials |
| `QUEUE_BACKEND` | `database` | `sqs` \| `azure` \| `database` |
| `QUEUE_NAME` | `jobs` | `deeptrace-jobs` in AWS |
| `QUEUE_VISIBILITY_SECONDS` | `900` | must exceed the slowest analysis |
| `QUEUE_WAIT_SECONDS` | `20` | SQS long polling; keeps an idle worker inside the free tier |
| `QUEUE_RECONCILE_MINUTES` | `5` | re-send a message that went missing |
| `STALE_CLAIM_MINUTES` | `30` | recover a job from a worker that died |
| `WORKER_IN_PROCESS` | `true` | `false` runs the worker as its own process — what the containers do |
| `ANALYSIS_MODE` | `real` | `fake` returns a canned result: for tests and UI work |
| `REQUIRE_WEIGHTS` | `true` | refuse to start without the four checkpoints |
| `MAX_UPLOAD_MB` | `200` | |
| `JWT_SECRET` | `dev-secret-change-me` | **must be changed**; comes from Secrets Manager in AWS |

### Provider independence

Storage and the queue each sit behind a small interface, chosen by environment variable:

| | Local, no cloud account | AWS | Azure |
|---|---|---|---|
| `STORAGE_BACKEND` | `local` — a directory | `s3` — via Moto locally | `azure` — via Azurite locally |
| `QUEUE_BACKEND` | `database` — the row itself | `sqs` — via Moto locally | `azure` — via Azurite locally |
| `DATABASE_URL` | SQLite, or Postgres in Docker | RDS | Azure Database for PostgreSQL |

All 48 tests pass against every combination. That is the evidence that a change of cloud provider
is a set of connection strings rather than a rewrite.

---

## Bugs this project found the hard way

Kept in the README because the pattern is the interesting part — every one of these was invisible
locally and only appeared against real services or real video.

| # | Bug | Impact | Fix |
|---|---|---|---|
| 1 | **`pipeline.py` called the fusion head without a batch dimension** | `FusionHead` opens with a `BatchNorm1d`, so **every video containing a detectable face crashed** — the real inference path had never once produced a verdict. 48 passing tests could not see it, because they all run in `fake` mode. | `.unsqueeze(0)`, matching every other call site (`train.py`, the Kaggle notebook) |
| 2 | `boto3` was in no requirements file | the worker crash-looped and the API silently dropped every message — `enqueue` is wrapped in a `try/except` so a broker outage never breaks an upload, which hid it | added to `backend/requirements.txt` |
| 3 | The S3 backend treated bucket creation as mandatory | every cloud upload returned 500, because the task role correctly lacks `s3:CreateBucket` | a permission denial now means "assume the deployment created it" |
| 4 | ECS service-linked roles are absent on a new account | cluster creation failed with `Unable to assume the service linked role` | created `AWSServiceRoleForECS` and friends |
| 5 | A Secrets Manager value was stored as invalid JSON | tasks refused to start: `invalid character 'D' looking for beginning of object key string` | wrote the payload via a file so shell quoting could not mangle it |

The lesson is consistent: **least-privilege IAM, a real message broker, and real video expose
assumptions that a single local process never has to confront.** Bug 1 is the headline — the
application could never have worked, and no amount of mocked testing would have found it.

---

## Known limitations

Stated plainly, because a project that hides these is harder to trust:

1. **Served over HTTP, not HTTPS.** A trusted TLS certificate requires a domain you control; ACM
   will not issue for `elb.amazonaws.com`. CloudFront in front of the ALB fixes it for free with
   a valid `*.cloudfront.net` certificate.
2. **The model's evaluation has dataset leakage.** Celeb-DF v2 appears in the training data, so
   its cross-dataset score is not a cross-dataset score. Needs a retrain with disjoint splits.
   See [`docs/kaggle-pipeline-brief.md`](docs/kaggle-pipeline-brief.md).
3. **Fixed decision thresholds** misclassify out-of-distribution video. Clips that are not from
   ff-c23 / Celeb-DF often land on `INCONCLUSIVE`.
4. **The image is 5.25 GB**, mostly PyTorch, which makes a new ECS task slow to start (~1–2
   minutes to pull).
5. **No migrations.** A schema change means recreating the database; Alembic is the standard fix.
6. **`/video` reads the whole object into memory** before slicing out a range. Fine at the 200 MB
   upload limit; a streaming accessor on the storage interface is the right fix for larger files.
7. **No retry on transient failure.** A job that raises is marked `failed` immediately; only
   *interrupted* jobs (stale claims) are re-run automatically.
8. **The report page's model badges name ConvNeXt-Tiny**, but the trained backbone is Xception.
   A cosmetic mismatch in `frontend/src/features/report/Report.tsx`.
9. **The AWS deployment used root account credentials.** A dedicated IAM user with MFA is the
   correct practice.
10. **The local test clips in `ml/data/synthetic/` are smoke-test data**, mostly without a
    detectable face. Real evaluation needs clips from the training datasets.

---

## Documentation

| Read this | For |
|---|---|
| [`backend/README.md`](backend/README.md) | the backend in depth: build phases, the queue's guarantees, the endpoint table |
| [`docs/aws-deployment.md`](docs/aws-deployment.md) | provisioning, redeploying, monitoring, cost, teardown |
| [`docs/diagrams/`](docs/diagrams) | interactive architecture and sequence diagrams (open the HTML) |
| [`docs/viva-prep.md`](docs/viva-prep.md) | prepared answers, console walkthroughs, a demo script |
| [`docs/ml-architecture.md`](docs/ml-architecture.md) · [`docs/ml-guide.md`](docs/ml-guide.md) | the pipeline's design and how to work on it |
| [`docs/kaggle-pipeline-brief.md`](docs/kaggle-pipeline-brief.md) | training on Kaggle, the results, and the leakage analysis |
| [`docs/frontend-guide.md`](docs/frontend-guide.md) · [`docs/frontend-presentation.md`](docs/frontend-presentation.md) | the UI and its design system |
| [`docs/backend-cloud/`](docs/backend-cloud/README.md) | backend and cloud concepts explained from zero, with a glossary |

---

## Credits

Verification videos come from the **Deepfake Detection Challenge** (DFDC) sample set; training
uses FaceForensics++ (c23) and Celeb-DF v2. Face detection and alignment use
[insightface](https://github.com/deepinsight/insightface); the spatial backbone comes from
[timm](https://github.com/huggingface/pytorch-image-models). The interactive diagrams were
produced with [Archify](https://github.com/tt-a1i/archify).
