# 09 — Azure Services We Use

One section per service: what it is, why we chose it, and roughly what it costs.

Azure has over 200 services. We use **nine**. Knowing which ones you *didn't* use, and why, is
just as important.

---

## The services at a glance

```
   ┌────────────────────────────── resource group: rg-deeptrace ──────────────────────────────┐
   │                                                                                          │
   │  ┌─ Azure Container Registry ─┐     stores the one Docker image both apps run from        │
   │  └────────────────────────────┘                                                          │
   │                                                                                          │
   │  ┌─ Container Apps Environment ─────────────────────────────────────┐                    │
   │  │   ca-deeptrace-api      FastAPI + serves the React app   min 1 │                    │
   │  │   ca-deeptrace-worker   queue consumer + PyTorch         min 0 │                    │
   │  └────────────────────────────────────────────────────────────────┘                    │
   │                                                                                          │
   │  ┌─ Storage Account ────────────────────────────────────────────────┐                    │
   │  │   Blob: videos/  heatmaps/          Queue: jobs                 │                    │
   │  └────────────────────────────────────────────────────────────────┘                    │
   │                                                                                          │
   │  ┌─ PostgreSQL Flexible Server ─┐   users, investigations, results                        │
   │  └──────────────────────────────┘                                                        │
   │                                                                                          │
   │  ┌─ Log Analytics + Application Insights + Azure Monitor ─┐   logs, metrics, alerts       │
   │  └────────────────────────────────────────────────────────┘                              │
   └──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Azure Container Apps — the compute

**What it is.** A managed service that runs your container image and handles all the server
management: starting it, load-balancing traffic to it, giving it an HTTPS address, and adding or
removing copies based on your rules.

**Analogy.** A restaurant kitchen you rent by the hour. You bring the recipe (your image); they
provide the building, the electricity, and extra cooks when it gets busy.

**Why this instead of a VM.** A VM would mean you patch the OS, configure nginx, obtain TLS
certificates, write systemd services and build your own autoscaling. None of that is your
project's contribution, and all of it is easy to get wrong. Container Apps gives you HTTPS,
autoscaling and rolling updates by declaration.

**Why not Azure Kubernetes Service (AKS).** AKS is the industry-standard container platform, and
it is genuinely the "right" answer at scale — but it requires managing a cluster, node pools,
ingress controllers and manifests. For two services, AKS is weeks of work and a larger bill to
solve a problem you don't have. Being able to say *why* you skipped it is a good sign.

**Our configuration.**

| App | Purpose | CPU / RAM | Replicas | Scaling signal | Public? |
|---|---|---|---|---|---|
| `ca-deeptrace-api` | REST API + serves the React build | 1 vCPU / 2 GiB | 1 → 3 | concurrent HTTP requests | yes |
| `ca-deeptrace-worker` | runs the ML pipeline | 2 vCPU / 4 GiB | 0 → 5 | queue length | no |

The worker gets double the CPU and RAM because PyTorch is hungry; the API stays small because it
only does database lookups.

**Cost.** Charged per vCPU-second and GiB-second while replicas exist. The API kept warm is the
main cost; the worker is near-free because it sleeps.

---

## 2. Azure Container Registry (ACR) — the image warehouse

**What it is.** A private place to store your Docker images.

**Analogy.** A warehouse for your shipping containers. Build once, deliver to any number of places.

**Why.** Container Apps needs to pull the image from somewhere. ACR sits inside Azure, so the
pull is fast and stays on the private network.

**Nice trick:** `az acr build` builds the image *in Azure*. You don't need Docker Desktop installed
at all — you upload the source folder and Azure builds it. Useful on a laptop with limited disk.

**Cost.** Basic tier is a small flat monthly fee (~$5).

---

## 3. Azure Storage Account — Blob + Queue

One account, two very different jobs. This is the resource-sharing example from concept 7.

### 3a. Blob Storage — the files

**What it is.** Object storage: whole files, addressed by a key, designed to be durable and huge.

**Analogy.** A shelf of numbered boxes. You know the box number, you fetch the box. You never
search inside all the boxes.

**Our layout.**
```
videos/INV-0007/interview.mp4                 ← what the user uploaded
heatmaps/INV-0007/heatmap_f005.jpg            ← what Grad-CAM produced
```

**Why not the container's disk.** Container disks are **ephemeral** — wiped when the replica
stops. Also, with several worker replicas, a file saved by replica A is invisible to replica B.
Shared external storage removes both problems.

### 3b. Queue Storage — the to-do list

**What it is.** A durable first-in-first-out list of small messages, with retry semantics.

**Analogy.** A ticket rail in a kitchen. The waiter clips an order on; whichever cook is free
takes the next ticket.

**Why Storage Queue and not Service Bus.** Azure Service Bus is the more capable messaging
service (sessions, ordering, richer dead-lettering) and costs a little more. Our messages are
trivial — just `{"investigation_id": "INV-0007"}` — and Storage Queue is essentially free at our
volume *and* lives in the storage account we already have. That's the resource-sharing argument
again, and it's a clean answer if asked.

**Why not just `BackgroundTasks`.** Covered fully in file 04: the queue survives restarts, works
across replicas, retries failures, and gives us something to scale on.

**Cost.** Storage is per GB plus per operation — pennies at this scale.

---

## 4. Azure Database for PostgreSQL Flexible Server — the database

**What it is.** A managed PostgreSQL database: Microsoft runs the server, patches it, backs it up
and monitors it.

**Analogy.** A filing cabinet with a full-time librarian. You ask for a file; you never oil the
drawers.

**Why managed.** Self-hosting Postgres inside a container means *you* own backups, upgrades and
disk exhaustion. That is not where your project's value lies.

**Our tier.** Burstable **B1ms** — the cheapest tier that's genuinely usable. "Burstable" means it
has a baseline performance with the ability to spike briefly, which suits an application that's
idle most of the time and busy for 20 seconds at a stretch. Perfect for this workload.

**Why this is the one always-on cost.** The worker can scale to zero; a database can't. It must
be reachable the instant a request arrives.

**Cost.** ~$12–15/month. It is the largest fixed line in the budget and worth naming in the
report.

---

## 5. Log Analytics Workspace — the log bucket

**What it is.** A central store where logs from many services land, queried with KQL (a
SQL-like query language).

**Analogy.** A shared filing system for every note anyone in the building wrote.

**Why.** Without it, debugging means opening three different portal blades looking for output.
With it, one query shows API and worker logs interleaved in time order — which is exactly what
you need when a job mysteriously fails.

**Sample query you'll actually use.**
```kql
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "ca-deeptrace-worker"
| where Log_s contains "INV-0007"
| order by TimeGenerated desc
```

**Cost.** First 5 GB/month ingested is free. We are nowhere near that.

---

## 6. Application Insights — the application view

**What it is.** An APM (Application Performance Monitoring) service: it records every HTTP
request, how long it took, and whether it failed, with a dependency map.

**Analogy.** A flight recorder for your API.

**Why.** Azure Monitor tells you the container restarted; Application Insights tells you *which
endpoint* was slow and *which request* caused the error. It also gives you the response-time chart
for your report.

---

## 7. Azure Monitor — metrics, dashboards and alerts

**What it is.** The umbrella service that collects metrics, renders dashboards and fires alerts.

**Metrics you'll track and screenshot:**

- replicas running for api and worker
- **queue length** ← the elasticity story
- request count and response time
- failed requests
- CPU and memory per replica

**Alerts to configure:**

| Alert | Condition | Why |
|---|---|---|
| Queue backlog | queue length > 20 for 5 min | workers are falling behind, or dead |
| Failure spike | failed requests > 5/min | something is broken |

---

## 8. Resource Group — the container for everything

**What it is.** A logical folder holding every resource belonging to one project.

**Why it matters more than it sounds.** It makes the pay-as-you-use model tangible:

```bash
az group delete -n rg-deeptrace --yes --no-wait
```

Everything we created — container apps, database, storage, registry, logs — is deleted and
billing stops. You cannot accidentally leave a forgotten database running. Say this in the
report; it shows you thought about cost hygiene.

---

## 9. Ingress / networking — for free, mostly

**What it is.** Container Apps includes a managed HTTP ingress: a public HTTPS endpoint with
automatic TLS certificates and load balancing across replicas.

**Why we barely discuss it.** Normally you'd build this yourself with a load balancer, a reverse
proxy and certbot. Container Apps does it by declaration. The rubric's "networking service" is
satisfied by this managed ingress (plus the platform's internal networking and the PostgreSQL
firewall rule). Azure Front Door or Application Gateway would be the upgrade if a custom domain,
CDN or WAF were required.

---

## What we deliberately did NOT use

Being able to justify omissions is a strong signal in a viva.

| Service | Why not |
|---|---|
| **Azure Kubernetes Service (AKS)** | Full cluster orchestration. Powerful, and far beyond what two services need. Container Apps gives the same scale-to-zero behaviour with a fraction of the operational surface. |
| **Virtual Machines** | Give you full control and full responsibility. We'd have to manage the OS, TLS, service supervision and scaling ourselves. |
| **Azure Functions** | Serverless functions are excellent for short event-driven work. Our workload takes ~20 s of heavy CPU with a multi-gigabyte dependency set, which fights the model's execution limits and cold-start assumptions. Better suited to our *queue trigger* aspirations than to the inference itself. |
| **Service Bus** | Better messaging, unnecessary capability for a one-field message. Storage Queue is free-er and already present. |
| **Key Vault** | The correct home for secrets. Deferred because Container Apps secrets already keep them out of git; Key Vault adds managed-identity wiring we don't need for a demo. Named as the next hardening step. |
| **Front Door / WAF** | A CDN and web firewall. We have no custom domain and modest traffic. |
| **Blob CDN for the SPA** | Would let us deploy the UI without rebuilding the image. We chose a single origin and no CORS instead. |

---

**Next:** [10 — CI/CD](10-ci-cd.md).
