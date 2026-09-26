# 01 — What Is a Backend, and Why Does DeepTrace Need One?

## The restaurant analogy

Think of a restaurant:

| Restaurant | DeepTrace |
|---|---|
| The dining room and menu you see | **Frontend** — the React app in your browser |
| The kitchen where work happens | **Backend** — Python code on a server |
| The waiter taking orders | **API** — the defined list of things you can ask for |
| The pantry and fridge | **Database + cloud storage** |
| The recipe book | **ML models** |

You never walk into the kitchen. You ask a waiter for something from the menu, and eventually
food comes back. The frontend never touches your models directly — it asks the backend, and the
backend does the work.

---

## What your project looks like today

```
   ┌───────────────────────────┐
   │      Browser window       │
   │                           │
   │  React app (the real UI)  │
   │            │              │
   │            ▼              │
   │   MSW fake server  ───────┼──► nothing real. Fake data lives in the browser.
   └───────────────────────────┘

   ml/weights/*.pt   ← 4 trained models sitting on your disk, used by nobody
```

Two halves are finished, but they have never met:

- The **frontend is finished** but talks to a *fake* server that runs inside the browser
  (a library called MSW). Upload a video and it pretends to analyse it, then shows you
  hard-coded results. It looks convincing, and it's how the UI was built before any server
  existed.
- The **ML models are trained** but the only way to use them is to run a command by hand on
  your laptop.

**The backend is the missing middle.** It is what turns "a UI with fake data" and "a model you
run manually" into one working application.

---

## What the backend has to do for DeepTrace

Six jobs, and that's the whole list:

1. **Accept a video upload** from the browser and store it somewhere safe.
2. **Run the ML analysis** on that video — the ~20 second PyTorch job.
3. **Remember everything** — which user uploaded what, what the verdict was, when.
4. **Serve the results** back to the browser in the exact shape the frontend expects.
5. **Know who is asking** — log users in, and show each person only their own investigations.
6. **Serve the frontend files themselves** so there's one web address for the whole app.

Everything in the rest of this guide is detail about these six things.

---

## Why can't the browser just do all this?

This is a genuinely good question, and the answer explains why backends exist at all.

| Reason | Explanation |
|---|---|
| **The models are too heavy** | A browser can't run your PyTorch Xception model and face detector. It would need a multi-gigabyte download and would still be slow. |
| **Secrets** | Your database password and model files must never be shipped to a user's computer. Anything in the browser is readable by anyone who presses F12. |
| **Persistence** | If results only lived in the browser, closing the tab would erase them. You need a shared place that survives. |
| **Sharing** | Two people on two laptops need to see the same investigations. There must be one source of truth. |
| **Trustworthiness** | The verdict comes from a model you control. If the browser produced it, a user could edit the JavaScript and fake their own results. |
| **Video files are big** | Browsers shouldn't hold a 200 MB video in memory while computing. |

The short version: **the browser is the user's machine, and the user's machine is not yours.**
Anything that must be trusted, shared, or kept, lives on your server.

---

## The three-layer picture we're building toward

```
┌────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — Presentation                   (already built ✅)        │
│  React app in the browser. Renders screens, collects input.        │
│  Knows NOTHING about models, databases or secrets.                 │
└──────────────────────────────┬─────────────────────────────────────┘
                               │  HTTPS + JSON  (the "contract")
┌──────────────────────────────▼─────────────────────────────────────┐
│  LAYER 2 — Application / API              (we are building 🔨)      │
│  FastAPI. Validates input, enforces login, stores files,           │
│  checks the database, and answers in exact expected shapes.        │
└───────┬──────────────────────────────────┬─────────────────────────┘
        │                                  │
┌───────▼──────────────┐        ┌──────────▼─────────────────────────┐
│ LAYER 3a — Worker    │        │ LAYER 3b — Data                     │
│ Picks jobs off a     │        │ PostgreSQL: users, investigations,  │
│ queue and runs the   │        │   verdicts, evidence metadata       │
│ ML pipeline (~20 s). │        │ Blob Storage: video files, heatmaps │
└──────────────────────┘        └────────────────────────────────────┘
```

The reason Layer 2 is split into **API** and **Worker** (both "the backend") is the single most
important idea in this whole guide, and file **04** is devoted to it. Short preview: the API must
answer quickly; the model takes 20 seconds. You can't do both in one place.

---

## What you'll have when it's done

```
1. You open  https://ca-deeptrace-api.<something>.azurecontainerapps.io
2. Register, log in.
3. Upload interview_clip.mp4.
4. The page shows a live checklist ticking through:
      ingest → frames → faces → spatial → temporal → frequency → fusion → done
5. ~20 seconds later you land on the results page, showing:
      - a verdict and a confidence score
      - the scores from all three model branches
      - a timeline with the suspicious time ranges highlighted
      - three Grad-CAM heatmap images showing WHERE the model looked
6. Refresh the page tomorrow and the investigation is still in your dashboard,
   because it was saved in a database, not in the browser tab.
```

That last point is the real difference between your current mock version and a real
application.

---

**Next:** [02 — HTTP and REST APIs](02-http-and-rest.md), where we look at the exact
conversation the browser and backend have.
