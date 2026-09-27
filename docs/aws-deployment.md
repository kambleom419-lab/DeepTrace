# DeepTrace on AWS

The deployed system, what it costs, how to change it, and how to tear it down.

Region **ap-south-1** (Mumbai) · account **838882524724** · everything below lives in the
default VPC.

---

## What is running

```
                            Internet
                               │  HTTP :80
                               ▼
                 ┌─────────────────────────────┐
                 │  ALB  deeptrace-alb         │   public DNS:
                 │  health: /api/health        │   deeptrace-alb-102696666.ap-south-1.elb.amazonaws.com
                 └──────────────┬──────────────┘
                                │ :8000, only from the ALB's security group
                                ▼
        ┌───────────────────────────────────────────────┐
        │  ECS Fargate cluster "deeptrace"              │
        │                                               │
        │   deeptrace-api   (0.5 vCPU / 1 GB)           │
        │     └── serves the REST API *and* the built   │
        │         React UI, from one origin             │
        │                                               │
        │   deeptrace-worker (1 vCPU / 4 GB)            │
        │     └── claims jobs and runs the 4-branch     │
        │         PyTorch pipeline, no inbound port     │
        └───────┬───────────────┬───────────────┬───────┘
                │               │               │
      ┌─────────▼──────┐ ┌──────▼───────┐ ┌─────▼────────┐
      │ S3             │ │ SQS          │ │ RDS Postgres │
      │ deeptrace-     │ │ deeptrace-   │ │ deeptrace-pg │
      │ 838882524724   │ │ jobs         │ │ db.t4g.micro │
      │ videos/        │ │ 15 min       │ │ Postgres 16  │
      │ heatmaps/      │ │ visibility   │ │              │
      └────────────────┘ └──────────────┘ └──────────────┘

  Neither task has an AWS credential anywhere. They assume an IAM role and boto3 picks it
  up, so there is no key in the image, the environment, or this repository.
```

### Resource inventory

| Thing | Identifier |
|---|---|
| S3 bucket | `deeptrace-838882524724` |
| SQS queue | `deeptrace-jobs` — `https://sqs.ap-south-1.amazonaws.com/838882524724/deeptrace-jobs` |
| ECR repo | `838882524724.dkr.ecr.ap-south-1.amazonaws.com/deeptrace` (1.58 GB compressed) |
| RDS | `deeptrace-pg` — `deeptrace-pg.cf2sis4u640b.ap-south-1.rds.amazonaws.com:5432`, db.t4g.micro, 20 GB gp3, **not** publicly accessible |
| Secrets Manager | `deeptrace/app-LIHCfC` — JSON with `DATABASE_URL` and `JWT_SECRET` |
| ECS cluster | `deeptrace` |
| ECS services | `deeptrace-api`, `deeptrace-worker` (Fargate, `assignPublicIp=ENABLED` because there is no NAT gateway) |
| ALB | `deeptrace-alb` → `deeptrace-alb-102696666.ap-south-1.elb.amazonaws.com` |
| Target group | `deeptrace-api` — HTTP :8000, health check `/api/health` |
| IAM roles | `deeptrace-task-execution` (pull image, write logs, read the secret), `deeptrace-task` (S3 + SQS only) |
| Log group | `/ecs/deeptrace`, 7-day retention |
| VPC | `vpc-0cd011da8ec95bca4` (default), subnets in ap-south-1a/b/c |
| Security groups | `deeptrace-alb` (in :80 from anywhere) → `deeptrace-tasks` (:8000 from alb) → `deeptrace-rds` (:5432 from tasks) |

### Why the security groups are chained

Each one trusts only the group in front of it, so "who can reach the database" is answered by
the graph rather than by a CIDR range someone has to maintain. The database is not reachable
from the internet even though the tasks have public IPs.

---

## Verified behaviour

Run against the public ALB with real face clips from `ml/data/synthetic/`, full pipeline in
`ANALYSIS_MODE=real`:

| clip | expected | verdict | confidence | heatmaps | end to end |
|---|---|---|---|---|---|
| `real/face_astronaut.mp4` | real | `LIKELY_AUTHENTIC` | 0.394 | 0 | 23 s |
| `fake/face_clip_fake.mp4` | fake | `INCONCLUSIVE` | 0.479 | 2 | 17 s |

The upload returned `201` in well under a second, the video landed in S3, the job travelled as
an SQS message, the worker claimed it, wrote the Grad-CAM heatmaps to S3, and the heatmap came
back through the API as `image/jpeg`. So the whole chain is proven: **ALB → API → RDS → S3 →
SQS → worker → S3 → API**.

The fake clip is a genuine miss in the sense that it does not cross the 0.6 threshold, but its
spatial (0.639) and temporal (0.655) branches both lean fake — it is the documented
fixed-threshold calibration problem on out-of-distribution video, not a new fault. These
synthetic clips are not from the ff-c23 / celeb-df training sets.

---

## Changing the deployed code

```powershell
# 1. rebuild and tag
docker build -t deeptrace-app:local .
docker tag deeptrace-app:local 838882524724.dkr.ecr.ap-south-1.amazonaws.com/deeptrace:latest

# 2. push (only changed layers upload)
$aws = "$env:LOCALAPPDATA\Programs\Amazon\AWSCLIV2\aws.exe"
& $aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 838882524724.dkr.ecr.ap-south-1.amazonaws.com
docker push 838882524724.dkr.ecr.ap-south-1.amazonaws.com/deeptrace:latest

# 3. make the services pick it up
& $aws ecs update-service --cluster deeptrace --service deeptrace-api    --force-new-deployment
& $aws ecs update-service --cluster deeptrace --service deeptrace-worker --force-new-deployment
```

Changing an environment variable means a new **task definition revision**, not just a restart:

```powershell
& $aws ecs register-task-definition --cli-input-json file://api-taskdef.json
& $aws ecs update-service --cluster deeptrace --service deeptrace-api --task-definition deeptrace-api
```

---

## Monitoring

- **Dashboard** `deeptrace` — ALB traffic and errors, SQS backlog, RDS CPU/connections/storage,
  ALB latency and healthy targets, plus a live log view.
- **Alarms** (all notify the `deeptrace-alerts` SNS topic): `deeptrace-alb-5xx`,
  `deeptrace-queue-backlog`, `deeptrace-rds-cpu`, `deeptrace-rds-storage`.
- **Budget** `deeptrace-monthly` — $10/month, alert at 50%.

To get the alarm emails, subscribe your address to the topic once:

```powershell
& $aws sns subscribe --topic-arn arn:aws:sns:ap-south-1:838882524724:deeptrace-alerts `
  --protocol email --notification-endpoint you@example.com
```

Then confirm the link AWS emails you.

### Elasticity

| Service | Range | Scales on |
|---|---|---|
| `deeptrace-worker` | 1 → 4 tasks | SQS `ApproximateNumberOfMessagesVisible`, target 3 |
| `deeptrace-api` | 1 → 3 tasks | average ECS CPU, target 60% |

The worker is the interesting one: scaling on queue depth means inference capacity follows
demand, which is the whole reason the API and the worker are separate services.

---

## Cost

| Item | Per day | Notes |
|---|---|---|
| ALB | ~$0.60 | ~$0.0225/hr, bills even when idle — no off switch but deletion |
| ECS Fargate | ~$1.90 | api 0.5 vCPU/1 GB + worker 1 vCPU/4 GB |
| RDS db.t4g.micro | ~$0.45 | 20 GB gp3 |
| S3 + SQS + ECR + CloudWatch | ~$0.10 | ECR storage is the bulk of it |
| **Total** | **~$3.05/day** | about 3% of the $100 credit per day |

Idle cost is dominated by the ALB and RDS. If you want to pause without tearing down, delete
the ALB and stop RDS; the rest costs almost nothing.

---

## Screenshots worth taking for the report

Take these before tearing anything down — they are the deployment evidence.

1. **ECS → Clusters → deeptrace → Services** — both services `1/1` running, platform Fargate.
2. **ECS → deeptrace-api → Tasks → the task → Containers** — the environment variables and the
   `DATABASE_URL` shown as "Value from Secrets Manager", not plaintext.
3. **EC2 → Load balancers → deeptrace-alb** — DNS name, and the listener on :80.
4. **EC2 → Target groups → deeptrace-api → Targets** — `healthy`.
5. **S3 → deeptrace-838882524724** — the `videos/` and `heatmaps/` prefixes with real objects.
6. **SQS → deeptrace-jobs → Monitoring** — the queue metrics.
7. **RDS → Databases → deeptrace-pg** — engine, class, and "Publicly accessible: No".
8. **CloudWatch → Dashboards → deeptrace** — the whole dashboard in one shot.
9. **CloudWatch → Alarms** — the four alarms.
10. **Auto Scaling → ECS services → deeptrace-worker → Scaling policies** — the backlog policy.
11. **IAM → Roles → deeptrace-task → Permissions** — showing it is S3 + SQS only.
12. **A browser tab on `http://deeptrace-alb-102696666.ap-south-1.elb.amazonaws.com`** logged
    in, showing the Results page with a heatmap — the end product.
13. **ECS → deeptrace-worker → Logs** — `worker up; queue=sqs` and `analysis complete: INV-0002`.

---

## Tearing it down

`deploy/teardown-aws.ps1` does all of this in the right order, with a confirmation prompt.

```powershell
cd deploy
.\teardown-aws.ps1            # asks before deleting anything
```

Order matters, and the script follows it: stop the consumers, then the load balancer, then the
database, then storage, then the permissions. Deleting an IAM role still in use, or a
security group still attached to a database, fails.

Two things the script deliberately does **not** touch:

- **The root account's own access keys** and the `default` profile — those are yours to manage.
- **`deeptrace-admin`**, if you created it.

---

## Bugs this deployment found

Worth putting in the report: every one of these was invisible locally and only appeared
against real cloud services.

| # | Bug | How it showed up | Fix |
|---|---|---|---|
| 1 | `boto3` was in no requirements file | worker crash-looped; the API would have silently dropped every message | added to `backend/requirements.txt` |
| 2 | `pipeline.py` called the fusion head without a batch dimension | **every video containing a face failed at the fusion step — the real path had never once produced a verdict** | added `.unsqueeze(0)`, matching every other call site |
| 3 | The S3 backend treated bucket creation as mandatory | every upload returned 500, because the task role correctly lacks `s3:CreateBucket` | a denial now means "assume it exists, the deployment made it" |
| 4 | ECS service-linked roles absent on a new account | cluster creation failed with `Unable to assume the service linked role` | created `AWSServiceRoleForECS` and friends |
| 5 | Secrets Manager value stored as invalid JSON | tasks failed to start with `invalid character 'D' looking for beginning of object key string` | wrote the payload via a file so no shell quoting could mangle it |

The pattern is consistent: **least-privilege IAM and a real broker expose assumptions that a
single local process never has to confront.** Bug 2 is the headline — the application could
never have worked, and 40 passing tests could not see it because they all ran in `fake` mode.
