# 05 — Databases and Storage

Your backend has to remember things. There are two very different kinds of "remember", and
using the wrong one for the wrong data causes most beginner pain.

---

## The two kinds of remembering

```
┌──────────────────────────────────────────────────────────────────────────┐
│ DATABASE (PostgreSQL)              │  OBJECT STORAGE (Azure Blob)         │
│ Small, structured, searchable      │  Big, opaque files                   │
├────────────────────────────────────┼──────────────────────────────────────┤
│ users                              │  the uploaded video (200 MB)         │
│ investigations                     │  Grad-CAM heatmap images (100 KB)    │
│ verdicts and scores                │                                      │
│ evidence metadata                  │  retrieved by a KEY (a file path)    │
│                                    │  you never search inside a blob      │
├────────────────────────────────────┼──────────────────────────────────────┤
│ "Find all failed investigations    │  "Give me the bytes of               │
│  from last week, newest first"     │   heatmaps/INV-0007/heatmap_f005.jpg"│
│ rows and columns                   │  whole files                         │
└────────────────────────────────────┴──────────────────────────────────────┘
```

**The rule of thumb:** if you need to *search, filter, sort or join* it, it belongs in the
database. If it's *a file you fetch by name*, it belongs in object storage.

**Never store video files inside a database.** A common beginner mistake. Databases are built to
find small rows fast; stuffing 200 MB blobs into them makes every backup, query and replication
painfully slow. Store the *file*, keep its *path* in the database.

---

## Our five tables

```
   ┌──────────────┐
   │    users     │          id, email, password_hash, created_at
   └──────┬───────┘
          │ one user has many investigations
          ▼
   ┌──────────────────┐
   │ investigations   │      id (INV-0007), user_id, title, status, stage, pct,
   └───┬────┬─────────┘      created_at, completed_at, failure_reason
       │    │
       │    └────────────────────┐
       ▼ 1:1                     ▼ 1:many
   ┌──────────┐            ┌──────────────┐
   │  videos  │            │   evidence   │   ev-001, timestamp, frame_number,
   └──────────┘            └──────────────┘   evidence_type, score,
   filename, size, sha256,                    description, storage_key
   duration, fps, resolution,
   storage_key                    ▲
                                  │ 1:1
                        ┌──────────────────────┐
                        │  analysis_results    │  verdict, confidence,
                        └──────────────────────┘  spatial/temporal/frequency_score,
                                                  frame_scores, suspicious_segments
```

Why separate tables instead of one enormous `investigations` table with 40 columns?

- A `queued` investigation has **no** result yet. If results lived in the same table, half the
  columns would be empty for most rows.
- A failed upload has a video but no result. A result can't exist without an investigation.
  Separate tables let the data reflect reality.
- Video metadata is a distinct concern from the verdict. Keeping them apart means changing one
  doesn't disturb the other.

This separation is called **normalisation** — a word worth knowing. It means "don't store the
same fact twice, and don't put unrelated facts in one place."

---

## Why a relational database and not a file or a spreadsheet?

| Approach | Why not |
|---|---|
| A JSON file on disk | Two requests writing at once corrupt it. No searching. No concurrent access. |
| SQLite (a local file DB) | Actually fine — and we use it in Phase 1! But it lives on *one machine's disk*, so it breaks the moment you run more than one API replica. |
| Spreadsheet | Not programmatically reliable, no transactions, no concurrency. |
| **PostgreSQL** | Handles many simultaneous connections, transactions, constraints and indexes. It's a real cloud service so all your replicas share one source of truth. |

**Postgres isn't a nicety here — it's forced by the architecture.** With the API running up to 3
replicas, a poll might land on a *different* replica from the one that accepted the upload. If
the status lived in one container's memory, the second replica would answer "not found". The
shared database is what makes multiple replicas possible at all.

---

## Transactions: all or nothing

When you accept an upload you do several things:

1. insert the `investigations` row
2. insert the `videos` row
3. put a message on the queue

If step 2 fails, you must not be left with an investigation that has no video. A
**transaction** groups changes so they all succeed or all fail:

```python
db.add(investigation)
db.add(video)
db.commit()          # ← both rows are written, or neither is
```

In SQLAlchemy (our ORM) you work with Python objects and call `commit()`:

```python
inv.status = "completed"
inv.completed_at = datetime.now(timezone.utc)
db.commit()          # no SQL string written by hand
```

You can still drop down to raw SQL when needed, but for ordinary reads and writes the ORM is
less error-prone and protects you from SQL injection.

---

## What the frontend contract has to be told about

Some fields the UI needs are **not** produced by the ML model at all. The backend must compute
them:

| Field | Where it comes from |
|---|---|
| `video.size`, `video.sha256` | computed while streaming the upload to storage |
| `video.duration`, `fps`, `resolution` | `ffprobe`, run on the uploaded file (fast — it reads headers only) |
| `investigation.id` | generated by us, format `INV-0001` |
| `created_at`, `completed_at` | clock times |
| `progress.stage`, `progress.pct` | written by the worker as it advances through the pipeline |
| `evidence[].id`, `description` | packaged by us around the model's raw output |

And one that must be **removed**: the ML pipeline adds a `_meta` block (timing, frame count)
that is *not* part of the frontend's type. We keep it in the database for our own records and
strip it before sending to the browser.

---

## The result is stored twice, on purpose

```
Postgres                                  Blob Storage
────────────────────────────              ─────────────────────────────
analysis_results.row                      heatmaps/INV-0007/heatmap_f005.jpg
  verdict = LIKELY_MANIPULATED
  confidence = 0.947
  frame_scores = [0.12, 0.14, ...]        the actual image pixels
  suspicious_segments = [...]

evidence row
  id = ev-001
  storage_key = heatmaps/INV-0007/heatmap_f005.jpg   ← the pointer
```

The database holds the *facts*; storage holds the *pixels*. The `storage_key` column is the
bridge. This is why `evidence[].heatmap_url` matters: the ML pipeline writes a bare filename
like `heatmap_f005.jpg`, and the backend rewrites it to a servable address
`/api/investigations/INV-0007/evidence/ev-001`. That URL is what the `<img>` tag in your Results
page will use — which is also why the Results page currently shows placeholders, since nothing
serves those images yet.

---

## Why not store the frames too?

The pipeline extracts 8 frames per video and writes them to a temporary folder. We deliberately
**do not** upload the frames anywhere:

- they're only needed during analysis,
- they'd multiply storage costs,
- the Grad-CAM heatmaps already show the interesting ones.

Temporary data lives on the worker's **ephemeral storage** (`/tmp`) and is deleted the moment the
job finishes. "Ephemeral" means "vanishes when the container stops" — perfect for scratch files,
useless for anything you want to keep.

---

**Next:** [06 — Authentication](06-auth.md).
