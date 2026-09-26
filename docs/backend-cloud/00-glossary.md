# 00 — Glossary

Every confusing word, in plain English. Grouped by topic. Each entry says what it means
**and** where it shows up in your DeepTrace project.

Skim it now, then come back whenever a word in the plan doesn't land.

---

## A. The web / API layer

**Server** — A computer program that waits for requests and answers them. It runs somewhere
always-on, unlike your laptop.

**Client** — The program making the request. For us, that's the browser running your React app.

**Backend** — The server-side half of an application: the code that the user never sees but
which holds the data, enforces the rules, and does the heavy work. Your project's backend is
what's missing today.

**API** — *Application Programming Interface*. A defined set of "things you can ask the server
to do." Think of a restaurant menu: it lists exactly what you can order, not a description of
the kitchen.

**Endpoint** — One specific thing on that menu, identified by a path, e.g.
`POST /api/investigations`. "Endpoint" and "route" mean the same thing.

**REST** — A common style for designing APIs where each endpoint represents a *thing*
(investigation, user) and you act on it with standard verbs. Ours is REST-ish, which is all
you need to say.

**HTTP** — The language browsers and servers speak. Every interaction is one **request** and
one **response**.

**HTTP method (verb)** — What you want to do:
`GET` = read, `POST` = create, `PUT`/`PATCH` = update, `DELETE` = remove.

**Request body** — The data you send along with the request (e.g. `{email, password}`).

**Response body** — The data the server sends back, almost always **JSON**.

**JSON** — A text format for structured data:
`{"verdict": "LIKELY_MANIPULATED", "confidence": 0.947}`. It's just how data is written down
so both sides understand it.

**Status code** — A three-digit number summarising what happened. The ones you must know:

| Code | Meaning | In DeepTrace |
|---|---|---|
| **200** | OK | successful login |
| **201** | Created | successful upload, successful registration |
| **401** | Unauthorized — "who are you?" | wrong password, missing/expired token |
| **404** | Not found | asking for an investigation id that doesn't exist |
| **422** | Unprocessable — bad input | registering without an email |
| **500** | Server crashed | our bug |

**Header** — Extra metadata attached to a request/response, separate from the body. Like the
envelope of a letter rather than the letter itself. `Content-Type`, `Authorization` are headers.

**`Content-Type`** — Tells the receiver what format the body is in: `application/json`, or
`multipart/form-data` for file uploads.

**`multipart/form-data`** — The format used when uploading a file. It's how a browser wraps a
binary blob (your video) plus text fields (`title`) into one request.

**Query parameter** — Optional settings added to the end of a URL:
`/videos?limit=10`. We barely use these.

**CORS** — *Cross-Origin Resource Sharing*. A browser security rule: by default a page served
from one address can't call an API on a *different* address unless the API explicitly allows
it. We avoid this whole headache by serving the frontend and the API from the **same address**.

**Proxy** — A middle-man that forwards requests. In local development we point Vite at the
backend so `/api/...` gets forwarded to `localhost:8000`.

**Reverse proxy** — A server-side middle-man sitting in front of your app, handling HTTPS and
routing. In Azure, Container Apps' **ingress** does this for us for free.

**Polling** — Repeatedly asking "is it done yet?" on a timer. Your `Processing.tsx` polls
every 1800 ms. Simple, and perfectly fine at this scale.

**WebSocket** — A permanent two-way connection where the *server* can push updates. Would
remove polling, but is more machinery than we need.

---

## B. Python backend vocabulary

**Framework** — A library that provides the skeleton of an application so you don't write
plumbing from scratch. Ours is FastAPI.

**FastAPI** — A modern Python web framework. You describe your endpoints and data shapes, and
it handles validation, documentation, and routing. Chosen because it's fast to write, it
validates input automatically, and it generates interactive API docs for free.

**Flask / Django** — The two other well-known Python frameworks. Flask is minimalist, Django is
batteries-included. Mentioned so you recognise the names.

**ASGI** — *Asynchronous Server Gateway Interface*. The modern standard that lets a Python web
app handle many requests concurrently. FastAPI is an ASGI app.

**WSGI** — The older, synchronous equivalent (used by Flask/Django traditionally). Won't matter
unless someone asks why we don't use gunicorn the classic way.

**Uvicorn** — The program that actually *runs* our FastAPI app and listens for HTTP traffic.
FastAPI is the recipe; Uvicorn is the oven.

**Pydantic** — A Python library for describing the *shape* of data and validating it. If a
request should contain `{email: str, password: str}` and someone sends a number, Pydantic
rejects it automatically with a 422.

**Schema** — A description of a data shape. We use the word twice: Pydantic schemas (what the
API sends/receives) and database schemas (what tables look like). `backend/app/schemas.py` is
the former.

**Model** — Ambiguous and annoying! In ML it's your neural network. In backend code it's a
Python class representing a database table. When these docs say "model" they mean the database
kind unless they say "ML model".

**ORM** — *Object-Relational Mapper*. Lets you work with database rows as Python objects
(`inv.status = "completed"`) instead of writing SQL strings. SQLAlchemy is our ORM.

**SQLAlchemy** — The most common Python ORM.

**Session** — A short-lived conversation with the database where you queue up changes and then
`commit` them. See "transaction".

**Commit** — "Save my changes." Until you commit, nothing is permanent.

**Rollback** — "Throw away my changes."

**SQL** — The language databases understand: `SELECT * FROM users WHERE email = '...'`.

**PostgreSQL / Postgres** — A powerful, free, open-source relational database. Our real database.

**SQLite** — A tiny database that's just a single file on disk, built into Python. Great for
local development, not for multiple servers. We use it while building, then swap to Postgres.

**Relational database** — A database made of tables with rows and columns, where tables can
reference each other (a *relationship*). Postgres is one.

**Primary key** — The column that uniquely identifies a row. Our investigations are identified
by `id` like `INV-0001`.

**Foreign key** — A column pointing at a row in another table. `videos.investigation_id`
points at an investigation, which is how we know which video belongs to which job.

**Index** — A lookup shortcut that makes searching a column fast, at the cost of a little
storage. Like the index at the back of a textbook.

**Migration** — A versioned script that changes the database structure over time (add a column,
rename a table). We're skipping this at first and just creating tables on startup.

---

## C. Async, jobs and queues

These words matter most for your project. Read slowly.

**Synchronous** — One thing at a time; you wait for each step to finish before starting the
next. A phone call.

**Asynchronous** — Start something and don't wait for it; carry on and check later. A text
message.

**Blocking** — An operation that freezes the current worker until it's done. CPU-heavy ML
inference is blocking.

**CPU-bound** — Work limited by how fast the processor can compute. Your face detection and
PyTorch inference are CPU-bound, so they can't be sped up by waiting cleverly — only by using
more CPUs.

**I/O-bound** — Work limited by waiting for disks or networks. Downloading a file is I/O-bound.

**Concurrency** — Making progress on several things over the same period (interleaved).

**Parallelism** — Actually doing several things at the same instant (needs multiple cores).

**Thread** — A lightweight unit of execution inside one program. Good for waiting (I/O), bad
for heavy computation in Python.

**Process** — A separate running program with its own memory. Real isolation.

**Background task** — Work kicked off so the response doesn't wait for it. FastAPI has a
built-in feature literally called `BackgroundTasks`.

**Job / task** — A unit of work to be done later. "Analyse investigation INV-0007" is a job.

**Queue** — A first-in-first-out to-do list that survives between programs. The API puts jobs
in; the worker takes them out. Our queue lives in Azure Storage.

**Message** — One entry in a queue. Ours is tiny: `{"investigation_id": "INV-0007"}`.

**Producer** — Whoever puts messages into a queue. Ours is the API.

**Consumer** — Whoever takes messages out. Ours is the worker.

**Worker** — A separate program whose only job is to consume the queue and do the heavy work.
This is the heart of our architecture.

**Dequeue** — Take a message off the queue.

**Visibility timeout** — After a worker takes a message, the queue hides it for a while so two
workers don't do the same job. If the worker crashes, the message reappears and gets retried.
This is how queues survive crashes.

**Poison message** — A message that crashes the worker every single time. After N attempts the
queue moves it aside so it stops blocking everything else. (Called the *poison queue*.)

**Idempotent** — An operation you can safely run twice with the same result. Important with
queues, because a message *will* occasionally be delivered twice.

**Cold start** — The delay when a service that had scaled down to zero has to start up again.
Our worker sleeps at zero replicas, so the first job after idle wakes it up — a few seconds.

---

## D. Containers and packaging

**Container** — Your app plus everything it needs to run (Python version, libraries, ffmpeg),
frozen into one box that behaves identically on any machine.

**Image** — The *recipe* for a container. A container is a running image. (Class vs object; or
cake tin vs the cake — everyone has a different metaphor. Pick one.)

**Docker** — The most popular tool for building and running containers.

**Dockerfile** — A text file of instructions for building an image: "start from Python 3.11,
install ffmpeg, copy my code, run this command."

**Base image** — The image you start from, e.g. `python:3.11-slim`.

**Layer** — Each Dockerfile instruction creates a cached layer. Changing code only rebuilds the
later layers, which makes rebuilds fast.

**Registry** — A warehouse for images. Azure Container Registry (**ACR**) is ours.

**Repository (registry sense)** — A named collection of image versions, e.g. `deeptrace:v1`.

**Tag** — The version label on an image (`v1`, `latest`).

**Ephemeral storage** — Disk space inside a container that vanishes when the container stops.
Perfect for temporary video frames, useless for anything you want to keep.

**Why your image is ~3 GB** — PyTorch, ONNX Runtime, OpenCV, insightface and the face-detection
model all add up. That's normal for an AI app and worth stating in your report.

---

## E. Cloud vocabulary

**Cloud computing** — Renting someone else's computers by the hour instead of buying your own.
You get servers, storage and databases on demand, and stop paying when you stop.

**Region** — The physical data centre location you choose, e.g. `centralindia`. Lower latency
and legal/data-residency implications.

**Subscription** — Your billing account with the cloud provider. A student one comes with
free credit.

**Resource group** — A folder that holds everything belonging to one project. Ours is
`rg-deeptrace`. Deleting the folder deletes (and stops billing for) everything inside — the
single most useful cost-control trick.

**Compute** — Anything that runs your code: virtual machines, containers, functions.

**Virtual machine (VM)** — A computer in the cloud. You manage the operating system. Maximum
control, maximum work.

**Managed service** — A service where the provider handles the operating system, patching and
scaling, and you just supply your app. Container Apps and managed Postgres are examples.

**Container Apps (Azure)** — A managed service that runs your container image, gives it a
public HTTPS address, and scales the number of copies up and down automatically.

**Replica** — One running copy of your container. If you have 3 replicas of the API, three
copies are serving users simultaneously.

**Instance** — Basically the same as replica. Also used for database sizes.

**Scaling** — Changing how many replicas you run.

**Vertical scaling** — Give one machine more power (bigger CPU/RAM). "Scale up."

**Horizontal scaling** — Add more machines. "Scale out." Usually the better answer.

**Autoscaling** — Letting the platform decide the replica count from real signals (CPU load,
queue length, request count).

**Elasticity** — Scaling *down* as well as up. Our worker goes to zero replicas when idle and
wakes when jobs appear.

**Scale to zero** — Running zero replicas when there's no work, so you pay nothing. You trade
a cold start for the savings.

**KEDA** — *Kubernetes Event-Driven Autoscaling*. The machinery Cloud-native platforms use
under the hood to scale on things like "how many messages are in this queue." Azure Container
Apps uses it, which is how our worker knows to wake up.

**Ingress** — The front door of a cloud app: receives internet traffic and routes it in.
Container Apps' ingress also terminates HTTPS for us automatically.

**FQDN** — *Fully Qualified Domain Name*. The public address of our app, something like
`ca-deeptrace-api.thankful-stone-1234.centralindia.azurecontainerapps.io`.

**TLS / HTTPS** — The padlock. Encryption for traffic in transit. Required, not optional.

**Object storage** — Storage for whole files ("blobs") accessed by a key, rather than rows in a
table. Azure Blob Storage. Right for videos and images.

**Blob** — One file in object storage. Also *Binary Large OBject*.

**Connection string** — A long string containing everything needed to reach a cloud resource:
address, account name, and a secret key. Convenient and dangerous — treat it as a password.

**Environment variable** — A setting passed to your program from outside the code, e.g.
`DATABASE_URL=...`. Keeps secrets out of the source code.

**Secret** — A sensitive environment variable (password, key). Stored in a dedicated secret
store, never in git.

**Managed identity** — A cloud identity for your app so it can access other cloud resources
*without* storing a password at all. The modern, safer approach. We're deferring it.

**Key Vault** — Azure's dedicated secret store. More controlled than plain secrets, slightly
more setup. Deferred.

**Resource limits** — CPU and memory assigned to a replica. Our worker gets 2 vCPU / 4 GiB
because PyTorch is hungry.

**Consumption billing / pay-as-you-go** — You pay per second of CPU used, per GB stored, per
request. No upfront cost, no charge when idle.

---

## F. Monitoring and reliability

**Log** — A line of text your program writes about what it's doing. Essential for debugging.

**Log Analytics workspace** — Azure's central bucket where all your logs land, queryable with a
SQL-like language.

**Metric** — A number tracked over time: CPU %, replica count, requests per minute, queue length.

**Azure Monitor** — The umbrella service that collects metrics and logs, and shows dashboards.

**Application Insights** — The application-level view: which endpoint was slow, which request
failed and why.

**Dashboard** — A screen of charts. You'll screenshot one for your report.

**Alert** — A rule that notifies you when a metric crosses a threshold: "email me if the queue
has more than 20 messages for 5 minutes." An alert *firing* is excellent evidence for the
marking scheme.

**Health check / probe** — A URL the platform calls to ask "are you alive?" Used to restart
broken replicas and to decide when a new one is ready for traffic.

**Latency** — How long a single request takes. Our analysis job is ~20 s; a normal API call is
milliseconds.

**Throughput** — How many requests you can handle per second.

**Uptime / availability** — The fraction of time the service is working.

**Failover** — Automatically switching to a backup when the primary breaks.

---

## G. CI/CD and DevOps

**CI — Continuous Integration** — Every time you push code, a robot checks out your project,
installs it and runs your tests. Catches breakage before a human does.

**CD — Continuous Delivery/Deployment** — After CI passes, the robot also builds and deploys
your app automatically.

**Pipeline (CI sense)** — The defined sequence of automated steps: install → lint → test →
build → deploy. Unrelated to the ML *inference pipeline*, which is annoying but standard. Our
ML package has a pipeline too; context tells you which is meant.

**GitHub Actions** — GitHub's built-in CI/CD system. Configured with YAML files in
`.github/workflows/`. Your repo has none today.

**Workflow / job / step** — The GitHub Actions words for a pipeline, a group of steps, and one
command.

**Artefact** — A file a pipeline produces and keeps: a container image, a test report, a
coverage summary.

**Lint** — Automatic style/error checking. Your frontend already uses `oxlint`.

**Regression** — Something that used to work and now doesn't. The thing CI exists to catch.

---

## H. Words specific to *this* project

**Contract** — The agreed shape of the data between frontend and backend:
`frontend/src/types/index.ts`. The frontend was built against it, so the backend must match it
exactly. Treat it as frozen.

**MSW (Mock Service Worker)** — A library that fakes the backend *inside the browser*, so the
frontend could be built before any server existed. Turn it off with `VITE_USE_MOCKS=false`.

**The 8 stages** — `ingest → frames → faces → spatial → temporal → frequency → fusion → done`.
The Processing screen renders its checklist from this list, so the exact names and order are
load-bearing.

**Checkpoint / weights** — The four trained `.pt` files in `ml/weights/`. They are the model's
learned parameters.

**`_meta`** — An extra block the ML pipeline adds to its output (timing, frame count). It is
**not** part of the frontend contract and must be stripped before sending to the browser.

**Priors fallback** — If a checkpoint file is missing, the pipeline silently substitutes fake
scores around 0.10–0.15 and keeps going. Dangerous: you get confident nonsense with no error.
Hence our startup check that refuses to run without all four files.

**Threshold miscalibration** — A known honesty issue: on out-of-distribution real video, the
fixed 0.6/0.4 decision cut mislabels genuine footage as manipulated. The API returns the raw
scores so the caveat stays visible.

**Rubric section 8** — The list of cloud concepts your college marks you on (on-demand
provisioning, elasticity, pay-as-you-use…). File 08 of this guide maps each one to your project.
