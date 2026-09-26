# 03 — FastAPI

## What it is

**FastAPI is a Python library for building web servers.** You write small Python functions, label
each one with the URL it should answer, and FastAPI handles all the plumbing: reading the
request, checking the data, calling your function, and turning the return value into JSON.

It's the "backend framework" for your project.

---

## Why FastAPI and not something else?

You'll be asked this. The honest answer:

| Option | Why not |
|---|---|
| **Flask** | Simpler, but you validate input by hand and get no API docs. Fine for tiny apps, more boilerplate for six endpoints with strict data shapes. |
| **Django** | Huge, opinionated, comes with an admin panel and its own ORM. Overkill — you already have a frontend and a fixed contract. |
| **Node/Express** | Perfectly good, but your models are Python. Calling PyTorch from JavaScript would mean running a second service. |
| **FastAPI** | Python (so it can call your ML code **in the same process**), automatic input validation, automatic interactive docs, and it's the modern default for ML-serving APIs. |

That first point is the real reason: **your ML pipeline is Python, so the backend should be
Python.** Otherwise the backend has to shell out to a second program just to run a model.

---

## A minimal FastAPI app, line by line

```python
# main.py
from fastapi import FastAPI

app = FastAPI()                       # 1

@app.get("/api/hello")                # 2
def hello():                          # 3
    return {"message": "hi"}          # 4
```

1. Create the application object. Everything hangs off this.
2. **A decorator.** `@app.get("/api/hello")` means: *"when a `GET` request arrives for
   `/api/hello`, run the function below."* A decorator is just a way of attaching metadata to a
   function — it doesn't call it, it registers it.
3. A normal Python function. Whatever it returns becomes the response body.
4. Returning a dict → FastAPI converts it to JSON automatically.

Run it with:

```bash
uvicorn main:app --reload
```

- `main` = the file `main.py`
- `app` = the object inside it
- `--reload` = restart automatically when you edit code (development only)

Now `http://localhost:8000/api/hello` returns `{"message":"hi"}`.

**Uvicorn is the thing that actually listens on the network.** FastAPI describes your endpoints;
Uvicorn runs them. Recipe vs oven.

---

## Sending data in: Pydantic validation

The magic that saves you the most work:

```python
from pydantic import BaseModel

class LoginRequest(BaseModel):
    email: str
    password: str

@app.post("/api/auth/login")
def login(body: LoginRequest):
    return {"email": body.email}
```

You declared the shape once. FastAPI now automatically:

- parses the incoming JSON,
- checks `email` and `password` are present and are strings,
- returns a **422** with a helpful message if not,
- gives you a real object with `body.email` (with autocomplete),
- and documents the shape in the API docs.

Without this you'd write the same validation by hand in every endpoint.

---

## Growing past one file: routers

Six endpoints in one file gets messy, so FastAPI has **routers** — a way to group endpoints and
attach them under a shared prefix:

```python
# app/api/auth.py
from fastapi import APIRouter
router = APIRouter()                # instead of app

@router.post("/login")
def login(...): ...

# app/main.py
from app.api import auth
app.include_router(auth.router, prefix="/api/auth")
```

`prefix="/api/auth"` + `"/login"` = the final path `/api/auth/login`. This is exactly how the
real project is organised: one file for auth, one for investigations.

---

## Reusable setup: dependencies

Some things every endpoint needs: a database session, or "the currently logged-in user".

FastAPI's `Depends` lets you declare that requirement, and it does the work for you:

```python
from fastapi import Depends

def get_current_user(...) -> User:
    # read the Authorization header, verify the token, load the user
    ...

@app.get("/api/investigations")
def list_investigations(user: User = Depends(get_current_user)):
    # if we got here, the user is authenticated
    return investigations_for(user.id)
```

If the token is missing or invalid, `get_current_user` raises a 401 and **your endpoint function
never runs**. You don't write a single `if not logged_in` check. This is deliberate design: the
authentication rule lives in one place and cannot be forgotten in a new endpoint.

---

## Free interactive documentation

Because FastAPI knows every endpoint, its input shapes and its output shapes, it generates a
live documentation page:

```
http://localhost:8000/docs
```

You can expand any endpoint and **click "Try it out"** to call it from the browser. This is
enormously useful, and it's also a free win for your report — a screenshot of `/docs` listing
your six endpoints looks professional.

---

## Where our actual code lives

Now that you know the concepts, here's the real structure. You can open these files and it should
look familiar rather than mysterious:

| File | What's in it | Concept from this page |
|---|---|---|
| `backend/app/main.py` | Creates `app`, includes routers, serves the frontend | the `FastAPI()` object |
| `backend/app/api/auth.py` | `/auth/register`, `/auth/login` | a router |
| `backend/app/api/investigations.py` | the four investigation endpoints | a router |
| `backend/app/schemas.py` | `LoginRequest`, `Investigation`, `AnalysisResult`… | Pydantic models |
| `backend/app/deps.py` | `get_current_user` | a dependency |
| `backend/app/models.py` | database tables (different meaning of "model"!) | — |
| `backend/app/config.py` | settings read from environment variables | — |
| `backend/app/db.py` | database connection and sessions | — |

A useful habit: when the plan says "implement `GET /api/investigations/{id}`", you now know that
means writing roughly ten lines in `api/investigations.py` plus a schema — not something exotic.

---

## Two things that surprise beginners

**1. FastAPI is not a server by itself.** You always run it *with* Uvicorn. Saying "I ran
FastAPI" is like saying "I ran a recipe."

**2. `async def` is not a magic speed-up.** Writing

```python
@app.post("/api/analyse")
async def analyse(...):
    result = run_pytorch_model(video)     # ← 20 seconds of pure CPU work
```

does **not** make it fast, and it's actively harmful: it blocks the one event loop that every
other request needs. Async helps when your endpoint spends its time *waiting* (on a database, an
API, a disk). PyTorch spends its time *computing*, which is different. This is exactly why the
heavy work goes to a worker — file 04 explains it properly.

---

**Next:** [04 — Sync, async, and background jobs](04-async-and-jobs.md) — the most important
file in this guide.
