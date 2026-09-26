# 08 — Cloud Computing Concepts (Rubric Section 8)

Your college marks you on ten specific cloud concepts. This file explains each one and — more
usefully — shows exactly where it appears in **your** project, plus a sentence you can lift
straight into the report.

Don't learn these as definitions. Learn them as *"this is what my project does."*

---

## 1. On-demand resource provisioning

**What it means.** You don't buy a server up front. You ask the cloud for resources when you need
them and they appear in seconds. Nothing is reserved in advance; capacity is created in response
to demand.

**In DeepTrace.** Nobody provisioned a machine for a video upload. When a job appears on the
queue, Container Apps creates worker replicas on demand. When the queue empties, they're removed.
The API replicas are likewise created by the platform from your declared rules, not by you
logging into a server.

> *"Resources are provisioned on demand: worker replicas exist only while jobs are queued. No
> server is pre-allocated to video analysis, so idle capacity is never paid for."*

---

## 2. Virtual machines and containers

**What it means.** Two ways of renting compute. A **VM** is a whole simulated computer — you
manage the OS. A **container** packages just your app and its dependencies and shares the host
kernel — lighter, faster to start, more portable.

**In DeepTrace.** Both appear:

- **Containers:** our API and worker run as containers built from one Docker image. They're the
  right choice because our dependency set (PyTorch, ffmpeg, insightface) is fiddly and version-
  sensitive — a container freezes it exactly.
- **Managed VMs underneath:** Azure Database for PostgreSQL Flexible Server *is* a virtual machine
  — Microsoft just patches, backs up and manages it for us. That's the "managed service" pattern:
  you get the VM without the sysadmin work.

> *"Application compute runs in containers for portability and fast startup; the database is a
> managed VM service, where the provider handles patching, backups and availability."*

---

## 3. Cloud storage

**What it means.** Durable, effectively unlimited file storage, accessed over the network by a
key, paid for per gigabyte. Not tied to any one server.

**In DeepTrace.** Azure Blob Storage holds the uploaded videos and the generated Grad-CAM heatmap
images. This matters architecturally: because files aren't on a container's local disk, **any**
worker replica can process any job — they all see the same storage. Delete a worker and the files
are still there.

> *"Uploaded videos and generated heatmaps are stored in Azure Blob Storage. Because object
> storage is external to the compute, any worker replica can process any job, which is what makes
> horizontal scaling possible."*

---

## 4. Cloud database

**What it means.** A managed database service: the provider handles backups, patching, storage
and availability, and you just connect and use it.

**In DeepTrace.** Azure Database for PostgreSQL Flexible Server stores users, investigations,
video metadata, analysis results and evidence records.

Be ready for the obvious question — *"why not store data in the container?"* Because we run up to
three API replicas behind a load balancer. A status held in one replica's memory is invisible to
the others, so a polling request would randomly get "not found". **A shared database is a
prerequisite for having more than one replica at all**, not just a convenience.

> *"A managed PostgreSQL instance is the single source of truth for user accounts and analysis
> metadata. This is required for horizontal scaling: with multiple API replicas, job status
> cannot live in any one container's memory."*

---

## 5. Scalability

**What it means.** The ability to handle more load by adding resources — usually more instances
(*horizontal*) rather than one bigger machine (*vertical*).

**In DeepTrace.** Two independent scaling rules, because the two components have different
bottlenecks:

| Component | Signal that drives scaling | Range |
|---|---|---|
| API | concurrent HTTP requests | 1 → 3 replicas |
| Worker | **queue length** | 0 → 5 replicas |

Five workers process five videos simultaneously instead of one video five times slower. This is
the point of the queue.

> *"Scalability is horizontal and independent per component: the API scales on HTTP concurrency,
> while the worker scales on queue length, so the two bottlenecks scale separately rather than
> being coupled."*

---

## 6. Elasticity

**What it means.** Scaling **down** as well as up. The system matches resources to current demand
and releases what isn't needed. Scalability is "can grow"; elasticity is "grows and shrinks by
itself."

**In DeepTrace.** The worker's minimum replica count is **zero**. With no uploads there is no
worker running at all — no charge, no idle capacity. The moment a message lands on the queue,
KEDA brings replicas up; when the queue drains, they return to zero.

This is also where the trade-off lives: scaling to zero means the *first* job after an idle
period waits for a cold start (~5–30 s to pull a 3 GB image and import PyTorch). We accept that
for the worker, because the user is already watching a progress bar, and we deliberately **avoid**
it for the API by keeping one replica warm.

> *"Elasticity is demonstrated by the worker service scaling from zero replicas when idle to five
> under load, driven automatically by queue length. Scaling to zero eliminates idle cost; we
> accept a cold-start delay on the first job as the trade-off."*

That sentence — including the trade-off — is a strong answer.

---

## 7. Resource sharing

**What it means.** Multiple consumers using one pool of resources efficiently, rather than each
holding its own copy.

**In DeepTrace.** Several distinct examples:

- **One image, two services.** The API and the worker are two containers from the *same* Docker
  image, differing only in their start command. One build artefact to maintain.
- **One storage account, two services.** Blob storage and the job queue both live in a single
  Azure Storage account — one credential, one connection string, fewer resources.
- **One observability workspace.** Both services ship logs to the same Log Analytics workspace,
  so logs from the API and the worker can be queried together.
- **Shared container apps environment.** Both apps share a network boundary and ingress
  infrastructure instead of each getting their own.

> *"Resource sharing appears at several levels: a single container image backs both the API and
> the worker; a single storage account provides both blob storage and the job queue; and both
> services log into one shared workspace."*

---

## 8. Remote accessibility

**What it means.** The application is reachable over the network from anywhere, not just from the
machine it runs on.

**In DeepTrace.** Container Apps' managed **ingress** exposes the API on a public HTTPS address:

```
https://ca-deeptrace-api.<random-name>.centralindia.azurecontainerapps.io
```

TLS certificates are provisioned and renewed automatically — no nginx, no certbot. Your examiner
can open that URL on their phone. That's the whole point, and it's also a great screenshot.

> *"The application is publicly reachable over HTTPS via the platform's managed ingress, with TLS
> certificates provisioned automatically, making it accessible from any device without VPN or
> local installation."*

---

## 9. Monitoring

**What it means.** Collecting logs, metrics and traces so you can see what the system is doing,
and getting alerted when it misbehaves.

**In DeepTrace.** Three layers:

| Layer | Service | Answers |
|---|---|---|
| Logs | Log Analytics | "What did the worker print when INV-0007 failed?" |
| Metrics | Azure Monitor | "How many replicas are running? How long is the queue?" |
| Application traces | Application Insights | "Which endpoint is slow? What's the failure rate?" |

Plus **alerts** — rules that email you when a metric crosses a threshold (queue backlog, failed
job rate). An alert that actually fired during testing is far better evidence than a screenshot
of a rule definition.

> *"Monitoring uses Azure Monitor and Log Analytics for container logs and platform metrics, with
> Application Insights for request-level tracing. Metric alerts notify on queue backlog and
> analysis failure rate, and a dashboard tracks replica count, queue length and response time."*

---

## 10. Pay-as-you-use model

**What it means.** You pay for consumption — per second of CPU, per GB stored, per request — with
no upfront licence and nothing charged while idle.

**In DeepTrace.** Concretely:

| Resource | Billing basis |
|---|---|
| Container Apps | per vCPU-second + GiB-second, only while replicas exist |
| Blob Storage | per GB stored + per operation |
| PostgreSQL | per hour the server exists (the one always-on cost) |
| Log Analytics | per GB ingested, first 5 GB/month free |
| Container Registry | flat monthly fee for the Basic tier |

The elastic worker means the most expensive part of the system (2 vCPU / 4 GiB of PyTorch) costs
**nothing when nobody is uploading**. And the entire project can be stopped with one command:

```bash
az group delete -n rg-deeptrace --yes --no-wait
```

That command is itself the best illustration of the model — everything you created, gone, and the
billing stops.

> *"Billing is consumption-based: container compute is charged per vCPU-second of actual replica
> lifetime, storage per gigabyte, and the scale-to-zero worker incurs no charge while idle. The
> complete environment can be deallocated with a single resource-group deletion, after which no
> further charges accrue."*

---

## One-paragraph summary you can memorise

> DeepTrace is deployed on Microsoft Azure using a container-based, event-driven architecture. A
> FastAPI container serves the React frontend and the REST API through managed ingress with
> automatic HTTPS. Uploads are stored in Blob Storage and metadata in a managed PostgreSQL
> database, and a job is placed on a Storage Queue. A separate worker container — built from the
> same image — consumes the queue and runs the PyTorch inference pipeline on CPU, writing results
> back to the database and heatmaps to storage. The worker scales from zero to five replicas based
> on queue length, and the API scales on HTTP concurrency, so compute is provisioned on demand and
> released when idle. All services log to a shared Log Analytics workspace with metric-based
> alerts, and the entire environment is billed on a pay-as-you-use basis.

---

**Next:** [09 — Azure services we use](09-azure-services.md), one section per service.
