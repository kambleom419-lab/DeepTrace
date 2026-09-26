# DeepTrace — Backend & Cloud, Explained From Zero

You built a frontend and trained ML models. This folder explains **everything else** —
the backend and the cloud — assuming you have never written a server before.

No prior knowledge needed. Every jargon word you'll meet in the plan is defined in
plain English in file **00**.

---

## Read them in this order

| # | File | What you'll understand after |
|---|---|---|
| 00 | [Glossary](00-glossary.md) | Every confusing word, in one place. **Start here, come back often.** |
| 01 | [What is a backend?](01-what-is-a-backend.md) | Why your project needs one, and what it does |
| 02 | [HTTP and REST APIs](02-http-and-rest.md) | Requests, responses, status codes, and our 6 endpoints |
| 03 | [FastAPI](03-fastapi.md) | The Python framework we'll use, and why |
| 04 | [Sync, async, and background jobs](04-async-and-jobs.md) | **The most important file.** Why a 20-second video can't block a web request |
| 05 | [Databases and storage](05-database-and-storage.md) | Where the videos, results and users actually live |
| 06 | [Authentication](06-auth.md) | Login, passwords, tokens — how the app knows who you are |
| 07 | [Docker and containers](07-docker-containers.md) | How your code gets packaged to run anywhere |
| 08 | [Cloud computing concepts](08-cloud-concepts.md) | The 10 concepts your rubric marks you on |
| 09 | [Azure services we use](09-azure-services.md) | Each Azure service, with an everyday analogy |
| 10 | [CI/CD](10-ci-cd.md) | Automatic testing and deploying — what it is (we're not doing it yet) |
| 11 | [Reading the actual plan](11-plan-walkthrough.md) | A plain-English tour of the plan document, section by section |

---

## The whole project in one paragraph

A user uploads a video in your React app. The **browser** sends that file over the internet
to a **server** (the *backend*) running in Microsoft Azure. The server can't analyse a video
in a split second, so instead of making the user wait it drops the video into a **queue**
(a to-do list) and immediately replies *"got it, job queued — check back later."* A separate
program called a **worker** picks the job off the queue, runs your trained PyTorch models on
the CPU (about 20 seconds), and saves the verdict into a **database** and the heatmap images
into **cloud storage**. Meanwhile the browser keeps asking *"done yet?"* every 1.8 seconds —
until the answer is yes, and it shows the results screen.

That's the entire backend. Everything else in these docs is detail about *how*.

---

## Where we are right now

| Part | Status |
|---|---|
| Frontend (React) | ✅ Built — but currently talks to **fake** in-browser mocks, not a real server |
| ML models | ✅ Trained, 4 checkpoint files sitting in `ml/weights/` |
| Backend | 🔨 Started — config, database models, schemas and password/JWT helpers written |
| Cloud deployment | ⬜ Not started |

So the next big job is finishing the backend, then moving it to Azure.

---

## How to use these docs with your report

- **File 08** maps one-to-one onto rubric section 8 ("Cloud Computing Concepts Used").
- **File 09** gives you the service list for rubric sections 5 and 6.
- **File 02** gives you the endpoint list if you're asked "what does your API do?"
- **File 04** is the answer to "why is your architecture asynchronous?" — a favourite question.
- **File 00** is your safety net. If an examiner uses a word you don't know, it's in there.

---

## A note on honesty

Several of these docs say *"we are not doing this yet"* — for example CI/CD, private
networking, and Key Vault. That is deliberate and it is a **strength**, not a weakness: in a
viva, "we chose to defer this because it wasn't on the critical path" is a much better answer
than silence, or than claiming you built something you didn't.
