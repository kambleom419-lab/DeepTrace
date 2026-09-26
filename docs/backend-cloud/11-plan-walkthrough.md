# 11 — Reading the Actual Plan

A plain-English tour of `~/.commandcode/plans/deeptrace-backend-cloud-deployment.md`, so you can
open that document and know what every section is telling you to do.

Read files 00–10 first; this one assumes you know the vocabulary.

---

## The plan section by section

| Plan section | In plain English | Why it matters |
|---|---|---|
| **1. Decisions locked** | The four choices you already made: Azure, managed containers with autoscaling, queue + worker, frontend served by the API. | These are *decisions*, not options. Everything after this follows from them. |
| **2. Target Azure architecture** | The diagram of boxes and arrows. | This is the diagram for your report's "Proposed System" and "Cloud Architecture" sections. |
| **3. Repository layout** | Every file we're going to create in `backend/`, with a one-line purpose for each. | Your map. Each file maps to a concept from the earlier docs. |
| **4. Backend design** | The actual engineering: settings, database tables, the six endpoints, progress stages, the worker, the ML bridge, startup guards. | The core of the work. |
| **5. Frontend changes** | Four small edits to the React app. | Easy to forget, and without them your screenshots will show mock data instead of real results. |
| **6. Container image** | The Dockerfile, and four specific traps it avoids. | Where "it works on my machine" gets solved. |
| **7. Build order** | Phases 1 → 7, local first, cloud second. | **Read this before starting anything.** It's the actual sequence of work. |
| **8. Azure provisioning runbook** | The `az` commands, in order. | Your deployment step-by-step. Becomes the report's "Cloud Deployment Steps". |
| **9. Verification** | How you prove it works, including the sample-video problem. | Your testing section. |
| **10. Monitoring / autoscaling / load test** | Dashboards, alerts, and how to produce the elasticity screenshot. | Feeds the "monitoring" marks and the best artefact you'll have. |
| **11. Cost** | What each service costs and roughly what the month adds up to. | "Pay-as-you-use" marks, plus a sanity check you don't overspend. |
| **12. Cloud concepts table** | Each rubric concept mapped to your implementation. | Straight into the report. Same content as docs file 08. |
| **13. Consolidated traps** | 12 specific ways this codebase fails *silently*. | The most valuable list in the document. Read it twice. |
| **14. Definition of done** | A checklist of what "finished" means. | Tick it off as you go; it's also your demo script. |
| **15. Out of scope** | What we deliberately didn't build. | Answer "why didn't you do X?" with confidence. |

---

## Section 4 in detail — the backend design

This is the section you'll spend the most time in. Sub-sections, decoded:

**4.1 Configuration** — all settings come from **environment variables**, not hard-coded values.
This is what lets the *same* container image run on your laptop (SQLite, local files) and in Azure
(Postgres, Blob Storage) with zero code changes. You just set different variables.

**4.2 Data model** — the five database tables. Notice the note that Postgres is *mandatory*: with
more than one API replica, in-memory state breaks. That's a consequence of a decision you made in
section 1, not an arbitrary choice.

**4.3 API contract** — the six endpoints, endpoint by endpoint, with exact status codes and the
`detail` error field. This table is the promise to your frontend. Everything else is
implementation.

**4.4 Progress staging** — the 8 stage names, and the small change to the ML pipeline so progress
is *real*. This is worth pausing on: the easy-but-dishonest alternative is a timer that fakes
progress. The plan refuses that, and adds an optional callback to the pipeline instead. Six lines
of code, and your progress bar tells the truth.

**4.5 Worker** — the loop: take a message, mark processing, download the video, analyse it,
save results, mark complete. Plus what happens when things go wrong. Note the deliberate choice
that one worker replica handles **one** job at a time — parallelism comes from replicas, not
threads (file 04 explains why).

**4.6 ML bridge** — how the backend calls your trained models, and three specific gotchas:
strip `_meta`; `heatmap_url` is a bare filename that must be rewritten; the fixed thresholds
mislabel out-of-distribution video so raw scores are returned alongside the verdict.

**4.7 Storage layout** — exactly which file goes to which path. Temporary frames never leave the
worker.

**4.8 Startup guards** — refuse to start if the model files are missing. This exists because the
alternative is genuinely dangerous: a missing checkpoint makes the pipeline return scores around
0.10–0.15 **with no error at all**. You'd demo a confidently wrong system.

---

## Section 7 in detail — the build order

This is the part to actually follow. Don't skip phases; each one makes the next debuggable.

```
Phase 1  Contract-complete API, no cloud
         FastAPI + SQLite + BackgroundTasks. All six endpoints work.
         Switch the frontend off mocks and onto it.
         → You now have a working full-stack app on one laptop. Most valuable phase.

Phase 2  Real worker + queue (still local)
         Move inference out of the API into a worker process.

Phase 3  Postgres + Blob, locally
         docker-compose with postgres and the Azurite emulator.
         → Your code now uses the real Azure SDKs against local emulators.

Phase 4  Containerise
         Dockerfile + docker compose up. Whole stack in containers locally.

Phase 5  Provision Azure
         Run the az commands from section 8.

Phase 6  Deploy, verify, load-test, screenshot

Phase 7  Report artefacts
```

**Why local-first matters so much:** if something breaks in Azure, you must already know the code
works locally, or you're debugging two unknowns at once. That single discipline saves most of the
misery in cloud projects.

---

## Section 8 in detail — the `az` commands

`az` is the Azure CLI: a command-line tool that creates cloud resources. Instead of clicking
through the portal, you type commands — which means your deployment is **reproducible and
documented**, and you can paste it into your report as evidence.

The runbook follows the natural order of dependencies:

```
1. resource group          the folder that holds everything
2. container registry      where the image will live
3. storage account         blob (videos/heatmaps) + queue (jobs)
4. postgres                the database
5. log analytics           where logs will go
6. container apps env      the shared boundary for both apps
7. the API app             public, min 1 replica, scales on HTTP
8. the worker app          private, min 0 replicas, scales on queue length
```

Two things to notice:

- **`<uniq>`** appears in several names. Storage accounts and registries need *globally unique*
  names across all of Azure, so you'll append something random, e.g. `deeptracest7391`.
- **Secrets are never typed into these commands literally.** `--secrets db-url='...'` stores them
  in Container Apps' secret store, and `secretref:db-url` injects them as environment variables.
  Don't screenshot the terminal at that moment, or blur the password.

Finally, in your own environment, run the commands one at a time and check each succeeded. The
runbook is written as a single script for readability; running it blindly is how you end up with
five resources and no idea which one failed.

---

## Section 13 in detail — the traps

The twelve traps are all of one kind: **things that fail silently and produce plausible-looking
wrong answers.** They're the highest-value content in the plan, and they came from actually
reading your code rather than from generic advice.

Grouped, so they're easier to remember:

**The dangerous one — wrong answers, no error**
- Missing model weights → the pipeline substitutes fake scores (~0.10–0.15) and carries on.
  Hence the startup guard.

**Contract mismatches — the UI shows nothing / the wrong thing**
- `_meta` isn't part of the frontend's type and must be stripped.
- `heatmap_url` is a bare filename, not a URL — the Results page will show empty images until
  the backend rewrites it.
- The 8 stage strings are load-bearing; rename one and the Processing checklist breaks.

**Container build traps**
- `ffprobe` ignores the `FFMPEG_BIN` setting, so the image must provide both binaries.
- `opencv-python` (GUI build) breaks slim images — use the headless package.
- The face model downloads on first run — bake it in.
- `.dockerignore` must not exclude `ml/weights/` (they're gitignored but required at build time).

**Architecture traps**
- In-memory job state breaks with more than one API replica → Postgres is required.
- `ml/scratch/` is derived from the package location → the worker must pass an explicit `/tmp`
  output directory and clean up.

**Honesty traps**
- Fixed thresholds misclassify real out-of-distribution video — return raw scores and say so.
- The repo contains no video with a face, so the demo needs real clips from your datasets.

---

## How to actually work through this

A rhythm that works well:

1. **Read the phase you're about to do** in the plan, plus the matching docs file.
2. **Do it.**
3. **Verify it** — run the tests, or curl the endpoint. Don't move on because it "looks right".
4. **Commit.** Small commits with clear messages.
5. **Screenshot as you go.** Portal dashboards, `/docs`, the working app. You *will* forget what
   it looked like, and you don't want to be re-creating the deployment the night before the
   submission just to take pictures.
6. **Write a paragraph in your report while it's fresh.** The report is far easier to assemble
   from notes taken during the work than reconstructed afterwards.

---

## What to do right now

1. Read files **00** (glossary) and **04** (async) properly — those two carry the most weight.
2. Skim the plan's **section 7** (build order) and **section 13** (traps).
3. Start **Phase 1**: the FastAPI skeleton with all six endpoints working locally and the
   frontend switched off mocks.

Everything after that is repetition of the same pattern with more infrastructure underneath.
