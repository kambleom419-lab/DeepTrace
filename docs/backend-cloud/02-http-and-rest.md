# 02 — HTTP and REST APIs

The browser and the backend only ever have one kind of conversation: a **request** goes one way,
a **response** comes back. This file explains that conversation, then lists the six exact
conversations DeepTrace needs.

---

## Anatomy of a request

```
POST /api/investigations HTTP/1.1                 ← method + path + version
Host: ca-deeptrace-api.azurecontainerapps.io      ┐
Authorization: Bearer eyJhbGciOiJIUzI1...         │ headers (the envelope)
Content-Type: multipart/form-data; boundary=----  ┘
                                                  ← blank line
------WebKitFormBoundary
Content-Disposition: form-data; name="file";     ┐
  filename="interview.mp4"                        │ body (the letter)
<binary video bytes>                              │
------WebKitFormBoundary
Content-Disposition: form-data; name="title"      │
                                                  │
interview.mp4                                     ┘
------WebKitFormBoundary--
```

Four parts:

1. **Method + path** — *what* you're asking for. `POST /api/investigations` = "create a new
   investigation."
2. **Headers** — metadata. Who you are (`Authorization`), what format you sent
   (`Content-Type`).
3. **Body** — the actual data. Optional, but essential for uploads.
4. **Blank line** — separates headers from body. (Yes, really.)

---

## Anatomy of a response

```
HTTP/1.1 201 Created                              ← status code + reason
Content-Type: application/json                    ← header: this is JSON
                                                  ← blank line
{                                                 ┐
  "id": "INV-0007",                                │
  "title": "interview.mp4",                        │ body: JSON
  "status": "queued",                              │
  "progress": { "stage": "ingest", "pct": 5 }      │
}                                                 ┘
```

---

## The HTTP methods you need to know

| Method | Means | Everyday equivalent |
|---|---|---|
| `GET` | Read something, change nothing | Looking at a menu |
| `POST` | Create something new | Placing an order |
| `PUT` / `PATCH` | Update something existing | Changing your order |
| `DELETE` | Remove something | Cancelling your order |

A useful rule: **`GET` must never change anything.** If opening a page could delete your data,
everything breaks (browsers and caches assume GETs are safe to repeat).

---

## Status codes

The first digit tells you the category:

| Range | Meaning | Examples |
|---|---|---|
| **2xx** | It worked | 200 OK, 201 Created, 204 No Content |
| **3xx** | Go somewhere else | 301 Moved Permanently |
| **4xx** | **You** (the client) did something wrong | 400 Bad Request, 401 Unauthorized, 404 Not Found, 422 Unprocessable |
| **5xx** | **The server** broke | 500 Internal Server Error |

The single most useful debugging skill in backend work is reading the status code before
anything else. A 401 means "fix your login", not "your code is broken."

Your frontend turns any non-2xx into a JavaScript error, and reads the human-readable reason
from the `detail` field of the body — which is why **every error we return must include
`detail`**.

---

## Why our API is called "REST"

REST is a style where:

- Each **thing** gets its own address: `/investigations`, `/investigations/INV-0007`.
- You act on things with the standard methods: `GET` to read, `POST` to create.
- The server doesn't remember your previous request. Every request stands alone and carries its
  own proof of who you are (that `Authorization` header).

That third point is why the frontend sends the login token on **every single request** rather
than "logging in" once and staying logged in server-side.

---

## The six endpoints DeepTrace needs

Everything the frontend does is one of these. This list is the agreement between your frontend
and backend — your frontend was literally written against it.

| Method | Path | What it's for | Success |
|---|---|---|---|
| `POST` | `/api/auth/register` | Create a new account | **201** |
| `POST` | `/api/auth/login` | Log in, get a token | **200** |
| `GET` | `/api/investigations` | List my past investigations | 200 |
| `POST` | `/api/investigations` | Upload a video for analysis | **201** |
| `GET` | `/api/investigations/{id}` | Get one investigation (and poll it) | 200 |
| `GET` | `/api/investigations/{id}/evidence/{evidenceId}` | Fetch one heatmap image | 200 |

Note `{id}` in curly braces — that's a **path parameter**, a placeholder filled in with a real
value: `/api/investigations/INV-0007`.

---

## A complete walkthrough: uploading a video

This is the single most important flow in the project. Follow the numbers.

```
STEP 1 — Log in (once)
──────────────────────────────────────────────────────────────────────────────
  Browser → POST /api/auth/login
            body: {"email": "om@example.com", "password": "hunter2"}

  Backend → 200 OK
            body: {
              "access_token": "eyJhbGciOiJIUzI1NiIs...",
              "token_type": "bearer",
              "user": {"id": "3f9c...", "email": "om@example.com"}
            }

  Browser saves access_token in localStorage and attaches it to every future request.


STEP 2 — Upload the video
──────────────────────────────────────────────────────────────────────────────
  Browser → POST /api/investigations
            headers: Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
            body (multipart): file=interview.mp4, title="interview.mp4"

  Backend (this takes under a second — no analysis yet!):
            • saves the video to cloud storage
            • computes sha256 + file size, reads duration/fps/resolution via ffprobe
            • creates a database row: INV-0007, status "queued"
            • drops a message on the queue: {"investigation_id": "INV-0007"}
            • replies immediately

  Backend → 201 Created
            body: {
              "id": "INV-0007",
              "title": "interview.mp4",
              "status": "queued",
              "progress": {"stage": "ingest", "pct": 5},
              "video": {"filename": "interview.mp4", "duration": 32.1,
                        "fps": 30.0, "resolution": "1920x1080",
                        "size": 23400000, "sha256": "9f2c4a1b..."},
              "created_at": "2026-09-25T10:15:03Z"
            }
            ↑ note: NO "result" field. There's nothing to show yet.

  The browser navigates to /processing/INV-0007.


STEP 3 — The browser polls. Meanwhile, the worker works.
──────────────────────────────────────────────────────────────────────────────
  Every 1.8 seconds:  GET /api/investigations/INV-0007

  poll 1 → {"status": "queued",     "progress": {"stage": "ingest",    "pct": 5}}
  poll 2 → {"status": "processing", "progress": {"stage": "frames",    "pct": 20}}
  poll 3 → {"status": "processing", "progress": {"stage": "faces",     "pct": 35}}
  poll 4 → {"status": "processing", "progress": {"stage": "spatial",   "pct": 55}}
  poll 5 → {"status": "processing", "progress": {"stage": "temporal",  "pct": 70}}
  poll 6 → {"status": "processing", "progress": {"stage": "frequency", "pct": 82}}
  poll 7 → {"status": "processing", "progress": {"stage": "fusion",    "pct": 92}}

  In parallel, in the background, the worker is doing the real work:
      downloading the video → extracting 8 frames → detecting + aligning faces
      → running the Xception backbone → running the GRU → computing DCT stats
      → running the fusion MLP → generating Grad-CAM heatmaps → writing heatmaps to storage


STEP 4 — Done
──────────────────────────────────────────────────────────────────────────────
  poll 8 → {
    "id": "INV-0007",
    "status": "completed",
    "progress": {"stage": "done", "pct": 100},
    "completed_at": "2026-09-25T10:15:24Z",
    "result": {
      "verdict": "LIKELY_MANIPULATED",
      "confidence": 0.947,
      "spatial_score": 0.93,
      "temporal_score": 0.87,
      "frequency_score": 0.76,
      "suspicious_segments": [{"start": 12.4, "end": 15.8, "score": 0.96}],
      "frame_scores": [0.12, 0.14, 0.31, 0.88, 0.95, 0.71, 0.42, 0.46],
      "evidence": [
        {"id": "ev-001", "timestamp": 12.9, "frame_number": 310,
         "evidence_type": "heatmap", "score": 0.96,
         "heatmap_url": "/api/investigations/INV-0007/evidence/ev-001",
         "description": "Grad-CAM: strong activation around jawline"}
      ]
    }
  }

  The frontend sees status "completed", stops polling, and navigates to /results/INV-0007.


STEP 5 — Loading the heatmap image
──────────────────────────────────────────────────────────────────────────────
  Browser → GET /api/investigations/INV-0007/evidence/ev-001
  Backend → 200 OK, Content-Type: image/jpeg, <the image bytes>

  (The browser puts this straight into an <img> tag.)
```

---

## The three things people get wrong here

**1. "Why doesn't the upload just return the finished result?"**
Because it takes ~20 seconds. Browsers, proxies and load balancers all give up on long requests.
Also, the user would stare at a frozen page with no progress. The queue + polling design fixes
both — see file 04.

**2. "Why is there no `result` in the upload response?"**
Because `result` genuinely doesn't exist yet. The frontend is written to handle `result` being
absent for `queued`/`processing`/`failed` jobs. Sending an empty or fake result would be lying
to the UI.

**3. "What's the 1.8 second number?"**
It's how often `Processing.tsx` re-asks. That's a frontend choice, already made. The backend
just has to tolerate being asked repeatedly — which is easy, because each `GET` is a cheap
database lookup.

---

## Why polling is fine here (and when it isn't)

Polling is often criticised as wasteful. At your scale it's the right call:

| | Polling (ours) | WebSockets / server push |
|---|---|---|
| Complexity | Very low | High — connections to manage, reconnects, auth |
| Cost | ~11 cheap GETs per analysis | Similar |
| Latency to see an update | up to 1.8 s | instant |
| Works through proxies/firewalls | Always | Occasionally blocked |

For a job that takes 20 seconds and a handful of users, "check again in 1.8 s" is completely
reasonable. Worth saying out loud in a viva if asked — it shows you chose simplicity
deliberately rather than not knowing about WebSockets.

---

**Next:** [03 — FastAPI](03-fastapi.md), the Python framework that implements all of this.
