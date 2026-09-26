# 04 — Sync, Async, and Background Jobs

**This is the most important file in the guide.** It explains the single architectural decision
your whole backend is built around, and it is the thing most likely to be asked about in a viva.

Take it slowly.

---

## The problem, stated plainly

Analysing one video takes about **20 seconds** of solid computation.

A web request is supposed to be answered in **milliseconds**.

```
      What the user experiences if we analyse during the request:

      t=0s   click Upload
      t=0s   ...nothing happens...
      t=10s  ...still nothing... (is it broken? did it crash?)
      t=20s  page finally loads

      If it takes 60s, the browser or a proxy gives up and shows an error.
      If two people upload at once, the second may wait 40s.
```

This is unacceptable — not because it's slow, but because it's **silent**. The user has no idea
whether anything is happening.

---

## Why "just make it async" doesn't work

This is the part that trips everyone up.

Two completely different meanings of "async" get mixed together:

| | **Waiting** (I/O-bound) | **Computing** (CPU-bound) |
|---|---|---|
| Example | Reading from a database, downloading a file | Running PyTorch on 8 frames |
| What the CPU does | Nothing — it's idle, waiting | 100% busy, crunching numbers |
| Can we do other work meanwhile? | **Yes** | **No** — the CPU is genuinely occupied |
| Python tool | `async def` / `await` | separate process |

`async def` works by letting a single thread switch to other work while one task is *waiting*.
But a PyTorch forward pass never waits — it computes flat out. So there is nothing to switch to;
the single thread is simply busy for 20 seconds.

```
   async def endpoint running CPU work:

   one event loop (the only one)
   ├─ request A  ████████████████████████ 20s of PyTorch
   ├─ request B  ........................ blocked, waiting for the loop
   └─ request C  ........................ blocked
                                          ↑ async gave us NOTHING here
```

**Rule worth memorising:** *async helps with waiting, not with computing.* The only cure for
CPU-bound work is **more CPU** — a separate process, on another core, or another machine.

---

## Solution 1 — Do it during the request (the naive approach)

```python
@app.post("/api/investigations")
def upload(file):
    result = analyse(file)      # 20 seconds
    return result               # finally reply
```

| | |
|---|---|
| ✅ | Simplest possible code |
| ❌ | User stares at a frozen page for 20 s |
| ❌ | One slow request ties up resources; concurrent uploads queue up behind it |
| ❌ | Timeouts on proxies and load balancers |
| ❌ | **No progress indication possible** — you can't report stages while blocked |
| ❌ | If the server restarts mid-analysis, the work is lost with no record |

Verdict: works for a script, not for an application.

---

## Solution 2 — `BackgroundTasks` (the middle ground)

FastAPI can run a function *after* sending the response:

```python
from fastapi import BackgroundTasks

@app.post("/api/investigations")
def upload(file, background: BackgroundTasks):
    inv = create_investigation(file)              # fast
    background.add_task(run_analysis, inv.id)     # runs AFTER the response is sent
    return inv                                    # replies immediately ✅
```

| | |
|---|---|
| ✅ | The user gets an instant response |
| ✅ | Real progress can be written to the database |
| ✅ | Almost no extra infrastructure |
| ❌ | The work happens **inside the API process** — the API container now needs PyTorch, 4 GB of RAM and 2 CPUs |
| ❌ | If you run 3 API replicas, they don't share a work queue — jobs land wherever the request happened to go |
| ❌ | Restarting the API kills in-flight jobs |
| ❌ | Scaling is wrong: you must scale the API for *both* web traffic and heavy jobs, which have completely different needs |

Verdict: **excellent for local development and for the first phase of this project.** Not the
final answer.

---

## Solution 3 — Queue + separate worker (what DeepTrace does)

Split "the backend" into two programs that run the same code but do different jobs.

```
                      ┌──────────────────────────────────────────┐
  Browser ──upload──► │  API  (fast, small)                      │
                      │  - validate, save file, write DB row     │
                      │  - drop a message on the queue           │
                      │  - reply in < 1 second                   │
                      └───────────────┬──────────────────────────┘
                                      │  message: {"investigation_id": "INV-0007"}
                                      ▼
                      ┌──────────────────────────────────────────┐
                      │  QUEUE  (a durable to-do list)           │
                      │  [INV-0007]  [INV-0008]  [INV-0009]      │
                      └───────────────┬──────────────────────────┘
                                      │  worker pulls one message
                                      ▼
                      ┌──────────────────────────────────────────┐
                      │  WORKER  (slow, heavy)  × 0..5 replicas  │
                      │  - download video, run the ML pipeline   │
                      │  - upload heatmaps to storage            │
                      │  - write the verdict into the database   │
                      └──────────────────────────────────────────┘

  Browser meanwhile: "done yet?" → GET /api/investigations/INV-0007  every 1.8 s
```

### Why this is better

| | |
|---|---|
| ✅ | Upload replies in under a second, always |
| ✅ | The **API stays light** (no PyTorch, 1 vCPU) while the **worker stays heavy** (2 vCPU, 4 GB). You size and scale each one for its actual job. |
| ✅ | Jobs survive an API restart — they're in the queue, not in memory |
| ✅ | **Add workers to go faster.** 5 replicas ≈ 5 videos analysed at once. That's horizontal scaling, and it's the elasticity demo your rubric wants. |
| ✅ | The worker can scale to **zero** when idle — you pay nothing while nobody is uploading |
| ✅ | A crash means the message returns to the queue and gets retried |

### What it costs you

More moving parts: a queue service, a second container, and the fact that "done" is now
*asynchronous* — nothing is finished when the upload returns, so the frontend must poll.

That trade is worth it here, and the polling was already designed into your frontend.

---

## How a queue actually behaves

A queue is not just a Python list. The cloud version has properties that make it reliable:

```
1. Worker A takes message INV-0007
   → the message becomes INVISIBLE for N seconds (the "visibility timeout")
   → if Worker A finishes and deletes it, done
   → if Worker A crashes, the message REAPPEARS after N seconds and another worker retries it

2. This means a message can be delivered TWICE.
   → so your job must be idempotent: re-running it should not corrupt anything.
   → in practice: stamp the investigation as "processing" and make re-analysis simply
     overwrite the previous result.

3. If a message crashes the worker every single time ("poison message"),
   after a few attempts it gets moved aside to a POISON QUEUE
   → and we mark that investigation as "failed" instead of retrying forever.
```

Those three properties — retry, at-least-once delivery, poison handling — are exactly what you
*don't* get from `BackgroundTasks`. That's the real argument for a queue, more than speed.

---

## Scaling: replicas, and why not more threads

You might ask: "why not just handle 5 videos at once inside one worker using threads?"

Because of the **GIL** (Global Interpreter Lock): in standard Python, threads don't run Python
code simultaneously — only one thread executes Python at a time. Threads help when you're
*waiting* on I/O, but PyTorch's work is CPU-bound, so 5 threads would just take turns and finish
in roughly the same 5×20 seconds. No gain.

The answer is **more processes, on more cores** — which is what replicas are:

```
1 worker replica, 2 vCPU   → 1 video at a time,  ~20 s each
3 worker replicas          → 3 videos at once,   ~20 s each (in parallel)
```

So one worker replica processes **one job at a time**, deliberately. Parallelism comes from
adding replicas, and the platform adds them automatically when the queue gets long.

---

## Autoscaling on queue length

The worker's replica count is driven by a rule you configure:

```
   queue length    replicas
   ────────────    ────────
        0              0      ← idle: pay nothing (this is ELASTICITY)
        1              1
        3              3
       12              5      ← capped at maxReplicas
```

The mechanism is **KEDA** — Kubernetes Event-Driven Autoscaling. Azure Container Apps uses it
under the hood, so you just declare "scale on this queue" and the platform does the rest.

When replicas go from 0 → 5, that is **horizontal scaling**. When they go back to 0, that is
**elasticity**. The screenshot of that graph is one of the best artefacts you can put in your
report.

The API scales on a different signal — concurrent HTTP requests — because its bottleneck is web
traffic, not a queue:

```
   concurrent requests   API replicas
   ───────────────────   ────────────
           < 50               1
            50                2
           100                3
```

---

## The cost of scale-to-zero: cold starts

If the worker is at zero replicas and a job arrives, something has to start it. Pulling your
~3 GB image and importing PyTorch takes time:

```
   API kept at min 1 replica    → always ready, no cold start  (we do this for the API)
   Worker at min 0 replicas     → first job after idle waits ~5–30 s to start
```

That's the trade: **zero cost when idle, versus a delay on the first job.** For the worker, the
delay is invisible — the user is already watching a progress bar. For the API it would be
visible, so we keep the API warm at one replica.

---

## Where your project sits in each phase

The plan deliberately walks through these in order, so you can see the trade-offs rather than
just being told them:

| Phase | Approach | Why |
|---|---|---|
| **Phase 1** | `BackgroundTasks` | Proves the whole contract works locally with the least machinery. Fast to iterate. |
| **Phase 2** | Queue + worker, local | Swap the mechanism without changing any endpoint. |
| **Phase 3** | Cloud queue (Azure Storage) | Same code, real service. |
| **Phase 6** | Autoscaling worker | Turn on scale-to-zero and queue-length scaling. |

By the time you deploy, you'll have *felt* why each step exists, which is exactly what makes a
good viva answer.

---

## The one-sentence version

> The API accepts an upload, saves it, puts a job on a queue and replies immediately; a
> separate worker container takes jobs off the queue, runs the ~20-second ML pipeline on the
> CPU, and writes the result to the database; the browser polls for status every 1.8 seconds
> until it's done. The worker scales from zero to five replicas based on queue length, so we pay
> only for work actually done.

If you can say that without notes, you understand the architecture.

---

**Next:** [05 — Databases and storage](05-database-and-storage.md), where the results actually go.
