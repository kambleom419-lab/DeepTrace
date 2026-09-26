# 10 — CI/CD

*Continuous Integration / Continuous Delivery* — robots that test and deploy your code for you.

**You are not doing this yet.** This file exists so the term isn't a mystery when it appears in
the plan (it's on the "deferred" list), and so you can answer if asked.

---

## The problem it solves

Imagine your workflow without it:

```
  You change one file in backend/app/api/investigations.py
        │
        ▼
  ...and now you must remember to:
    1. run the backend tests
    2. run the frontend type check and build
    3. build a new Docker image
    4. push it to Azure Container Registry
    5. tell Container Apps to use the new tag
    6. notice that you forgot step 1, and you broke login four days ago
```

Two failure modes: **human forgetfulness** (you skip the tests when in a hurry) and **wasted
effort** (doing these six steps by hand after every change). CI/CD fixes both by making them
automatic and mandatory.

---

## CI — Continuous Integration

**Every time you push code, a robot does this:**

```
  git push
     │
     ▼
  ┌──────────────────────────────────────────────┐
  │  GitHub Actions runner (a fresh VM)          │
  │                                              │
  │   1. check out your repository               │
  │   2. install Python + dependencies           │
  │   3. run the backend tests          ← gate   │
  │   4. run the frontend type check + build     │
  │   5. report pass / fail on the commit        │
  └──────────────────────────────────────────────┘
     │
     ▼
  ✅ green  → safe to deploy
  ❌ red    → you get an email; do not deploy
```

The word *Integration* is historical: it means "check that the code I just merged still works
together with everyone else's code." For a solo project it means simply **"do the tests pass on a
clean machine?"**

That last part is the real value. Tests passing on your laptop proves little — your laptop has
your `.venv`, your environment variables and your forgotten config. A CI runner is a blank
machine, so it catches the "works only for me" bugs.

---

## CD — Continuous Delivery / Deployment

Once CI is green, the same robot can do the release:

```
  CI passed
     │
     ▼
   6. docker build  →  push to Azure Container Registry (tagged with the commit)
     │
     ▼
   7. az containerapp update --image ...:newtag
     │
     ▼
  Container Apps performs a ROLLING UPDATE:
     starts new replicas → waits for them to be healthy → shifts traffic → stops the old ones
     → users see no downtime
```

**Delivery vs Deployment** — a distinction worth knowing:
- *Continuous Delivery*: the pipeline prepares a release, but a human clicks "go".
- *Continuous Deployment*: it goes out automatically.

For a college project you'd want *Delivery* — automatic build and deploy to a staging slot, with
a manual approval for production.

---

## What a GitHub Actions file looks like

Concrete and short:

```yaml
# .github/workflows/ci.yml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install
        run: pip install -r backend/requirements.txt

      - name: Run backend tests
        run: pytest backend/tests -q
        env:
          ANALYSIS_MODE: fake          # don't load PyTorch in CI
          REQUIRE_WEIGHTS: "false"
          DATABASE_URL: sqlite:///./ci.db

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Frontend type check + build
        run: |
          cd frontend
          npm ci
          npm run build
```

That is a complete, real CI job. The `ANALYSIS_MODE: fake` setting is why it's fast — it skips the
ML model entirely and returns a canned result, so CI finishes in seconds instead of downloading
gigabytes of PyTorch.

---

## Why we're deferring it (the honest version)

| Reason | Explanation |
|---|---|
| **Not on the critical path** | The rubric asks you to *deploy* the application, not to automate deployment. |
| **The ML weights aren't in git** | `ml/weights/*.pt` is gitignored, so a CI runner has no checkpoints. A cloud build would need to fetch them from Blob Storage first — real work, and separate work. |
| **The image is huge** | Building a 3 GB image on every push is slow and burns CI minutes. |
| **Manual first teaches you the steps** | Running `az acr build` by hand once means you understand what a pipeline would be automating. Automating something you don't understand is how you get stuck when it breaks. |

**This is a good answer, not an excuse.** Deferring CI/CD deliberately, and being able to name the
blocker (weights not in git) and the fix (fetch them from Blob during the build), demonstrates
more understanding than ticking the box.

---

## If you have time at the end

This is the cleanest addition, in order:

1. **CI only** — `.github/workflows/ci.yml` with the backend tests and frontend build. Cheap, fast,
   and gives you a green checkmark to screenshot.
2. **A weight-fetch step** — a step that downloads `deeptrace_weights.zip` from Blob Storage before
   `docker build`, using a repository secret. This unlocks the full CD pipeline.
3. **CD** — `az acr build` plus `az containerapp update`, authenticated with a service principal
   stored in GitHub secrets.

Even step 1 alone is a meaningful "future work" contribution, and it's the kind of thing that
distinguishes a project that was *engineered* from one that was merely *made*.

---

## Vocabulary recap

| Term | Meaning |
|---|---|
| **CI** | Robot runs your tests on every push |
| **CD** | Robot also builds and deploys |
| **Pipeline** | The whole automated sequence of steps |
| **Workflow** | GitHub Actions' name for one pipeline file |
| **Runner** | The fresh machine the pipeline executes on |
| **Gate** | A step that must pass before the next runs |
| **Artefact** | A file the pipeline produces and keeps (an image, a test report) |
| **Rolling update** | Replacing replicas gradually so there's no downtime |
| **Service principal** | A non-human identity used by automation to authenticate to Azure |

---

**Next:** [11 — Reading the actual plan](11-plan-walkthrough.md).
