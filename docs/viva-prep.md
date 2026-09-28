# DeepTrace — Viva & Demo Preparation

Everything you need for the Cloud Computing + AI viva: the story, the exact numbers, where
every byte of data physically lives, console navigation, and prepared answers to the hard
questions.

Region **ap-south-1** (Mumbai) · account **838882524724** · live URL
**http://deeptrace-alb-102696666.ap-south-1.elb.amazonaws.com**

---

# Part 1 — The narrative

## The 60-second pitch (memorise this)

> "DeepTrace is a deepfake detection system. A user uploads a video in the browser. The
> request goes to a load balancer, which forwards it to a containerised API running on AWS
> Fargate. The API does **not** analyse anything — it stores the video in S3, writes a job row
> to a PostgreSQL database on RDS, and puts a message on an SQS queue. It returns `201` in about
> a tenth of a second.
>
> A separate worker container is polling that queue. It picks up the job, downloads the video
> from S3, and runs a four-branch PyTorch pipeline — spatial, temporal, frequency, then a fusion
> head — which produces a verdict, a confidence, and Grad-CAM heatmaps showing *which part of
> the face* drove the decision. The heatmaps go back into S3 and the result into RDS.
>
> The browser polls for the result and renders it. The database is always the source of truth;
> the queue only decides how a worker finds out there is work. That's why the API and the
> worker scale independently — which is the whole reason for this architecture."

## Why this is a *cloud* project, not just an app on a server

Say this early, it frames everything:

> "The point of the design is that each piece is a managed AWS service chosen for a specific
> job, and the application is written so the provider is swappable — the storage and queue
> backends sit behind interfaces, and the same 40 tests pass against local emulators and
> against real AWS."

---

# Part 2 — Where every piece of data lives

**This is the question they will ask. Learn this table.**

| What | Where it physically is | Why there |
|---|---|---|
| **Uploaded videos** | **S3** `s3://deeptrace-838882524724/videos/{INV-0001}/{filename}.mp4` | Object storage: cheap, unlimited, durable, not on the container's disk |
| **Grad-CAM heatmaps** | **S3** `s3://deeptrace-838882524724/heatmaps/{INV-0001}/heatmap_fNNN.jpg` | Same, and it means the API can serve images without the worker being alive |
| **Users, investigations, results, evidence rows** | **RDS PostgreSQL** `deeptrace-pg` | Relational data with transactions and foreign keys |
| **Job messages** | **SQS** `deeptrace-jobs` (transient, seconds to minutes) | Decoupling the API from the worker |
| **Container image (5.25 GB)** | **ECR** `838882524724.dkr.ecr.ap-south-1.amazonaws.com/deeptrace` | Versioned, pulled by Fargate at task start |
| **Database password + JWT signing key** | **Secrets Manager** `deeptrace/app-LIHCfC` | Never in the image, never in the code, never in this document |
| **Application logs** | **CloudWatch Logs** `/ecs/deeptrace` (7-day retention) | Central logs from containers with no disk you can SSH into |
| **Files being analysed right now** | The worker container's **ephemeral disk** (`/tmp`), deleted when the job ends | Only the working copy; nothing durable lives there |

**The sentence that answers the question crisply:**

> "Videos and heatmaps are in S3 as objects. Metadata and analysis results are in RDS
> PostgreSQL as relational rows. The queue carries only an investigation ID as a message. The
> database is the source of truth; S3 holds the bytes; SQS holds the work signal."

## What the database actually contains (5 tables)

| Table | Holds |
|---|---|
| `users` | email, bcrypt password hash |
| `investigations` | id (`INV-0001`), title, **status**, stage, pct, `created_at`, `claimed_at`, `worker_id` |
| `videos` | filename, size, **SHA-256**, duration, fps, resolution, **the S3 key** |
| `analysis_results` | verdict, confidence, the three branch scores, `frame_scores`, `suspicious_segments` |
| `evidence` | one row per heatmap: timestamp, frame number, score, description, **the S3 key** |

Note what is **not** in the database: no video bytes, no image bytes. Only keys pointing at S3.
That is deliberate and worth saying.

---

# Part 3 — AWS services and *why each one*

| Service | What it does here | Why this and not something else |
|---|---|---|
| **ECS Fargate** | Runs the API and worker containers; no servers to patch | Serverless containers — no EC2 instances to manage, and I only pay per task-second |
| **ALB** | Public entry point, health checks, routes to healthy tasks | Needs to be a stable public DNS name while task IPs change constantly |
| **S3** | Videos + heatmaps | Object storage; a container's disk dies with the container |
| **RDS PostgreSQL** | Users, results, job state | Managed: automated setup, patching, backups; a database file inside a container would be lost |
| **SQS** | Carries job messages | Decouples API from worker, buffers load, enables autoscaling on backlog |
| **ECR** | Stores the 5.25 GB image | Fargate has nowhere local to get an image from |
| **Secrets Manager** | Database URL + JWT secret | So no credential is ever baked into an image |
| **IAM roles** | Permissions for the running tasks | No access keys exist anywhere in the app |
| **CloudWatch** | Logs, metrics, alarms, dashboard | The only way to see inside a container |
| **Application Auto Scaling** | Worker 1→4, API 1→3 | Eleasticity: capacity follows demand |
| **SNS** | Delivers alarm notifications | Fan-out for alerts |
| **AWS Budgets** | Alerts at 50% of a $10 ceiling | Cost governance |
| **VPC + security groups** | Network isolation | The database is not reachable from the internet |

## The VPC and security-group chain

```
Internet ──:80──▶ [deeptrace-alb SG]  ──:8000──▶ [deeptrace-tasks SG] ──:5432──▶ [deeptrace-rds SG]
```

> "Each security group trusts only the group in front of it, so who can reach the database is
> answered by the graph rather than by an IP range someone has to maintain. RDS is not
> publicly accessible — I can prove that in the console."

Default VPC `vpc-0cd011da8ec95bca4`, subnets in **ap-south-1a, 1b, 1c**.

---

# Part 4 — Console walkthrough: exactly where to click

**Open these before the viva so nothing is slow to load.** Type `ap-south-1` in the region
selector first — everything is in Mumbai.

### 1. Show S3 (the most likely ask)

Console → **S3** → bucket **`deeptrace-838882524724`**
- Click **`videos/`** → `INV-0004/` → `01__kitchen_still.mp4` — *"this is the uploaded file, 5.8 MB."*
- Click **`heatmaps/`** → `INV-0004/` → `heatmap_f004.jpg` → **Object URL / Open** — *"this is a
  Grad-CAM heatmap the worker produced."*
- Point at the **Properties** tab → *"versioning off, encryption default, public access fully
  blocked."*
- CLI equivalent, if they like commands:
  `aws s3 ls s3://deeptrace-838882524724/videos/ --recursive`

**Say this:** *"The database stores only the S3 key, never the bytes. That is why the API and
the worker can both be stateless containers — if either restarts, the data is still here."*

### 2. Show the compute

Console → **Elastic Container Service** → Clusters → **`deeptrace`**
- **Services** tab → `deeptrace-api` **1/1** and `deeptrace-worker` **1/1**
- Click `deeptrace-worker` → **Configuration** → *"1 vCPU, 4 GB, launch type FARGATE, no load
  balancer — it has no inbound port at all, it only pulls from SQS."*
- Click `deeptrace-api` → the **task** → **Containers** → show the environment variables, and
  point at `DATABASE_URL` showing **Value from Secrets Manager** rather than plaintext. That is
  a strong 10 seconds.
- **Logs** tab → the worker stream → *"`worker up; queue=sqs` … `claimed INV-0004` …
  `analysis complete: INV-0004` — this is the queue handoff happening."*

### 3. Show the load balancer

Console → **EC2** → **Load Balancers** → **`deeptrace-alb`**
- *"Public DNS name, listener on HTTP:80."*
- **Target groups** → `deeptrace-api` → **Targets** → one target, **healthy**.

### 4. Show the database

Console → **RDS** → Databases → **`deeptrace-pg`**
- Engine PostgreSQL 16.15, class **db.t4g.micro**, 20 GB gp3
- **Connectivity & security** → **Publicly accessible: No** ← point at this deliberately
- *"It's only reachable from the ECS tasks' security group."*

### 5. Show the queue

Console → **SQS** → **`deeptrace-jobs`**
- **Monitoring** tab → messages visible / in flight, oldest message age
- *"If you upload during the demo, the visible count briefly goes to 1 and then to 0 as the
  worker takes it."*

### 6. Show monitoring and elasticity

Console → **CloudWatch** → **Dashboards** → **`deeptrace`** — the whole system on one screen
- **CloudWatch → Alarms** → four alarms
- **Application Auto Scaling** → ECS services → `deeptrace-worker` → *"scales out when the
  queue backlog exceeds 3 messages."*
- **ECR** → repository `deeptrace` → the image, 1.58 GB compressed, tagged `latest`

---

# Part 5 — The AI side

## The four branches (know this cold)

| Branch | Model | What it looks for |
|---|---|---|
| **Spatial** | Xception (pretrained, fine-tuned) | Per-face-crop pixel artifacts: blending seams, texture inconsistencies |
| **Temporal** | GRU over backbone features | Flicker and inconsistency *between* frames — deepfakes often stutter |
| **Frequency** | MLP over DCT statistics | High-frequency spectral artifacts left by upsampling and generation |
| **Fusion** | MLP over concatenated features (2048 + 128 + 256) | Learns how much to trust each branch |

## The 8 processing stages (the UI shows these live)

`ingest → frames → faces → spatial → temporal → frequency → fusion → done`

Percentages come from a single source of truth (`app/stages.py`) so the UI and the backend can
never disagree.

## Training

- **4,690 videos**: FaceForensics++ (c23) + Celeb-DF v2
- Trained on **Kaggle** (no local GPU), cross-dataset testing
- Reported: fusion **AUC 0.954** on the ff-c23 test split (n=320)

## Verdict thresholds

| Confidence | Verdict |
|---|---|
| **≥ 0.60** | `LIKELY_MANIPULATED` |
| **≤ 0.40** | `LIKELY_AUTHENTIC` |
| in between | `INCONCLUSIVE` |

> "The middle band is deliberate. A detector that never says 'I don't know' is overclaiming, and
> for a forensic tool a wrong confident answer is worse than an honest uncertain one."

---

# Part 6 — Your demo numbers (tested, real)

Run against the **deployed** system through the public ALB, `ANALYSIS_MODE=real`:

| Clip | Verdict | Confidence | Frame scores | Heatmaps |
|---|---|---|---|---|
| `01_02__exit_phone_room__YVGY8LOK.mp4` (INV-0003) | **LIKELY_MANIPULATED** | **0.7146** | mostly 0.99–1.00 | 3 |
| `01__kitchen_still.mp4` (INV-0004) | INCONCLUSIVE | 0.4553 | 0.42, 0.03, **0.9993**, 0.9987, 0.9909, 0.11, 0.9895, 0.02 | 3 |
| `face_astronaut.mp4` (INV-0001) | LIKELY_AUTHENTIC | 0.3941 | all ≈ 0.00 | 0 |

**Use INV-0003 as your headline** — `LIKELY_MANIPULATED` at 0.7146 with temporal at 0.9998 is a
decisive, clean result.

**Before the viva:** these are DFDC clips, and DFDC ships a `metadata.json` mapping filename →
REAL/FAKE. Look up both filenames. If they are labelled FAKE, then INV-0003 being flagged
`LIKELY_MANIPULATED` is a **correct classification against ground truth** — say that, it is the
strongest possible demo moment.

### What to show, in order (about 8 minutes)

1. Open the live URL, register, log in — *"nothing here is localhost."*
2. Upload the exit_phone_room clip. While it runs (20–30 s), talk through the 8 stages and the
   API/worker split.
3. Results: verdict, confidence, the three branch scores, the timeline with its shaded
   suspicious segment.
4. **Play the source footage.** It streams from S3 back through the API — scrub it and point
   out that seeking works because the route honours HTTP range requests.
5. Click each heatmap thumbnail — *"red is where the model looked; it's concentrated on the
   jaw and neck, which is exactly where deepfake blending artifacts appear."*
6. Open **Forensic report** → click **Export PDF**. The browser's print dialog opens with the
   destination already set to "Save as PDF"; the print stylesheet drops the app chrome and
   repaints the report light, so the PDF is a clean document with the heatmap images embedded.
   Also show **Export JSON** — the same result as machine-readable output.
7. Upload the kitchen clip and let it process while you move to the console.
8. Console: S3 → show the video you **just** uploaded and its heatmaps. Live, not a screenshot.
9. CloudWatch → the worker log showing `claimed INV-xxxx` then `analysis complete`.
10. Show the dashboard, the alarms, and the autoscaling policy.
11. The known-limitation slide, then cost.

---

# Part 7 — Questions they will ask, with answers

## Cloud computing

**Q: Why not simply run everything on one EC2 instance?**
> "It would be cheaper, but it's one process death away from total outage, the ML inference
> would compete with HTTP for the same CPU, and I'd have to patch the OS. Here the API and the
> worker scale independently — if ten uploads arrive at once, the worker scales out and the API
> is unaffected. That's the reason the queue exists at all."

**Q: Why Fargate and not EC2 or Lambda?**
> "Lambda is out because inference takes 20–30 seconds and loads a 5 GB PyTorch stack — a cold
> start and a package-size problem, and the worker is a long-running poller which doesn't fit
> the request/response model at all. EC2 means managing instances. Fargate gives me containers
> with no servers and per-second billing."

**Q: Why is the database not inside a container?**
> "A container's filesystem is ephemeral. If the task restarts, the data is gone. RDS gives me a
> managed, durable, backed-up database with a real connection string."

**Q: Where exactly is my uploaded video stored?**
> "In S3 — `s3://deeptrace-838882524724/videos/INV-0004/01__kitchen_still.mp4`. It's an object,
> not a row. The database stores only that key. Let me show you." *(then open S3)*

**Q: What if the worker crashes mid-analysis?**
> "The row is left in `processing` with a `claimed_at` timestamp. The worker's periodic sweep
> re-queues anything claimed more than 30 minutes ago, because re-analysis is idempotent — it
> replaces the previous result rather than appending."

**Q: What if the same message is delivered twice?**
> "SQS standard queues are at-least-once, so that's expected. Claiming a job is a
> compare-and-swap — `UPDATE … WHERE id = ? AND status = 'queued'` — so the second delivery
> finds the row already taken and is discarded. A duplicate must never analyse twice."

**Q: What if a message is lost?**
> "Then the row sits in `queued` forever, which is why `reconcile()` re-sends messages for
> anything queued longer than 5 minutes. That's the transactional-outbox pattern: the database
> is the source of truth and the queue is retried until it agrees."

**Q: How does autoscaling work here?**
> "The worker scales on SQS `ApproximateNumberOfMessagesVisible` with a target of 3 — so
> backlog drives capacity. The API scales on average CPU at 60%. Both have a minimum of 1 so
> there's no cold start on the first request."

**Q: How do the containers get AWS permissions? Do you have access keys in the image?**
> "No keys anywhere. Each task assumes an IAM role and boto3 picks up temporary credentials
> automatically. The task role can only touch one S3 bucket and one SQS queue — I can show you
> the policy."

**Q: Why is the site HTTP and not HTTPS?**
> See *Known limitations* below — answer this honestly and early.

**Q: What does it cost?**
> "About $3.05 a day: ALB $0.60, Fargate $1.90, RDS $0.45, storage and monitoring about
> $0.10. It's running on a $100 credit, and there's a budget alarm at 50% of $10."

**Q: What was the hardest part?**
> "Making it work in the cloud when it already worked locally. Least-privilege IAM and a real
> message broker expose assumptions a single local process never has to confront." *(then tell
> them the fusion bug and the S3 bug below — this is your best material)*

## AI / ML

**Q: How does it detect a deepfake?**
> "Four independent signals. The spatial model looks at each face crop for blending artifacts.
> The temporal model looks at consistency between frames. The frequency model looks for
> spectral traces of upsampling. A fusion head learns how much to trust each."

**Q: Why Grad-CAM?**
> "A verdict alone isn't usable in a forensic setting. Grad-CAM shows which region of the face
> drove the decision, so a human can sanity-check it instead of trusting a number."

**Q: Is your accuracy good?**
> "Fusion AUC is 0.954 on the ff-c23 test split. But that's the training distribution, so I
> don't claim it generalises — on out-of-distribution clips the honest answer is often
> INCONCLUSIVE, and the system says so."

**Q: Your heatmap shows 100% — does that mean certain?**
> No, and this is worth getting right. Two different things are called 100%:

> 1. **The Grad-CAM colour scale is always 100% somewhere.** The CAM is min-max normalized per
>    frame — `(cam - cam.min()) / (cam.max() - cam.min())` — so the hottest pixel is 1.0 by
>    construction, even if the absolute activation is tiny. It's a *relative* map: it shows
>    where the model looked *within that frame*, not how confident it is.
> 2. **The evidence badge shows the frame's spatial score**, which can genuinely be 0.9993.
>    That means the spatial head is very confident *that frame* is manipulated — but the verdict
>    comes from the fusion head, which may weight it down. In `01__kitchen_still.mp4` the spatial
>    head hits 0.9993 on several frames while the final verdict is only 0.4553, INCONCLUSIVE.

> **So: Grad-CAM is working; the 100% is the normalization. And a saturated per-frame score is
> not the same as a certain verdict.**

**Q: Why is `01__kitchen_still.mp4` inconclusive when some frames score 0.999?**
> "Because the fusion head is trained on all three branches, and on that clip the temporal
> branch disagreed — it scored 0.36. The fusion head learns when not to trust a single branch.
> That's the point of having four."

**Q: What are the limitations of your model?**
> "Fixed decision thresholds misclassify out-of-distribution video. The weights were trained on
> ff-c23 and Celeb-DF, and I have evidence of leakage in my own evaluation — celeb-df scored
> *higher* than the training split, which shouldn't happen, because celeb-df appears in the
> training data. That's documented in `docs/kaggle-pipeline-brief.md`. The correct fix is a
> retrain with strictly disjoint splits."

**Q: What would you do differently?**
> "Strict dataset separation before training, not after evaluating. And the real inference path
> had a bug that made it impossible for the app to ever produce a verdict — it was invisible
> because every test ran in a fake mode that skips the model. I'd have exercised the real path
> from day one."

## The five bugs found during deployment (excellent material)

| # | Bug | Impact | Fix |
|---|---|---|---|
| 1 | `boto3` missing from `requirements.txt` | Worker crash-looped; API silently dropped every message | Added to requirements |
| 2 | **Fusion head called without a batch dimension** | **Every video containing a face failed — the real path had never once produced a verdict** | `.unsqueeze(0)`, matching every other call site |
| 3 | S3 backend assumed it could create buckets | Every cloud upload returned 500 — the task role correctly lacked `s3:CreateBucket` | A denial now means "assume the deployment made it" |
| 4 | ECS service-linked roles absent on a new account | Cluster creation failed | Created the roles |
| 5 | Secrets Manager value stored as invalid JSON | Tasks refused to start | Wrote the payload via a file, not shell-quoted |

> "Bug 2 is the one I'd emphasise. 40 passing tests couldn't see it, because they run in fake
> mode. It was only found by running real video through the real pipeline — which is precisely
> the argument for testing against real services instead of mocks."

---

# Part 8 — Known limitations (say these before you're asked)

Saying these first is a strength, not a weakness:

1. **The site is HTTP, not HTTPS.** A trusted TLS certificate needs a domain I control; ACM
   won't issue for `elb.amazonaws.com`. The fix is CloudFront in front of the ALB, which gives
   a valid `*.cloudfront.net` certificate with no domain purchase.
2. **The image is 5.25 GB**, largely PyTorch, which makes cold starts slow (~1–2 minutes to
   pull on a new task).
3. **The database password is generated once and stored in Secrets Manager** — no rotation
   configured.
4. **No migrations.** A schema change means recreating the database; Alembic is the answer.
5. **The deployment used root account credentials.** The correct practice is a dedicated IAM
   user with MFA. This is the weakest part of the setup and I'd fix it first.
6. **`/video` reads the whole object into memory** before slicing out a range. That's fine at
   this app's 200 MB limit, but a streaming accessor on the storage interface is the right fix
   for larger files.
7. **Model calibration** — fixed thresholds, and dataset leakage in the evaluation. See above.

---

# Part 9 — One-line cheat sheet

- **Where are videos stored?** S3, `videos/INV-xxxx/` — objects, not database rows.
- **Where are heatmaps?** S3, `heatmaps/INV-xxxx/`.
- **Where is metadata/results?** RDS PostgreSQL, 5 tables.
- **What carries the job?** An SQS message containing just the investigation ID.
- **What's the source of truth?** The database row, always. The queue is only a signal.
- **Who can reach the database?** Only the ECS tasks' security group. Not the internet.
- **How do containers get credentials?** IAM task role, assumed at runtime. No keys exist.
- **How does it scale?** Worker on SQS backlog 1→4; API on CPU 1→3.
- **What does it cost?** ~$3.05/day.
- **Hottest ML result?** `LIKELY_MANIPULATED` 0.7146 on the DFDC clip, temporal 0.9998.
- **Why does a heatmap say 100%?** The CAM is min-max normalized per frame, so its peak is
  always 1.0. It shows where, not how sure.
