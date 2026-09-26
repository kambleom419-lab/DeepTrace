# 06 — Authentication

How the app knows who you are, and why it never stores your password.

---

## Authentication vs authorisation

Two words that sound alike and mean different things:

| | Question | Example |
|---|---|---|
| **Authentication** | *Who are you?* | "Log in with your email and password." |
| **Authorisation** | *Are you allowed to do this?* | "You may only see **your own** investigations." |

Both are needed. A user successfully logging in (authentication) must still be stopped from
reading someone else's investigations (authorisation). In our code that second part is one line
in the list endpoint: *filter by the current user's id, always.*

---

## Step 1 — Passwords are never stored

If your database is ever stolen, plain-text passwords expose every user — and people reuse
passwords, so you'd be exposing their email accounts too.

So we store a **hash**:

```
   "hunter2"  ──[ bcrypt ]──►  "$2b$12$K3Jd9vQ...Qx7"     ← what goes in the database
```

Properties of a good password hash:

- **One-way.** You cannot turn the hash back into the password.
- **Salted.** The same password produces a different hash for different users, so an attacker
  can't spot that two people share a password, and can't use a precomputed table.
- **Deliberately slow.** bcrypt is designed to take ~100 ms. Irritating for a human logging in
  once; ruinous for anyone trying billions of guesses.

Because hashing is one-way, we can never "look up your password". To check a login we hash the
attempt and compare the *hashes*:

```python
def verify_password(attempt, stored_hash):
    return bcrypt.checkpw(attempt.encode(), stored_hash.encode())
```

This is why "forgot password" systems send you a **reset link** rather than your password. Nobody
can retrieve it — including you.

---

## Step 2 — After login, a token instead of a password

Sending the password with every request would be terrible: it would have to be kept in the
browser, and would be exposed on every call. Instead, log in **once** and receive a **token**:

```
  POST /api/auth/login   {email, password}
        │
        ▼
  200 OK  {"access_token": "eyJhbGciOiJIUzI1NiIs...", "token_type": "bearer", ...}
        │
        ▼
  Browser stores it (localStorage, key "deeptrace_token")
        │
        ▼
  Every later request:  Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

The token is like a wristband at an event. You showed your ID once at the door; now you show the
wristband.

---

## What's inside a JWT

That long string is a **JWT** — JSON Web Token. It has three parts separated by dots:

```
eyJhbGciOiJIUzI1NiJ9 . eyJzdWIiOiIzZjljLi4uIiwiZXhwIjoxNzY0fQ . 4sPq9wXm2...
└──── header ───────┘   └──────────── payload ─────────────┘   └─ signature ─┘
   the algorithm            the actual claims                     the tamper seal
```

The **payload** is just base64 — readable by anyone who copies it. Ours contains:

```json
{ "sub": "3f9c-...",           // subject = which user
  "iat": 1764000000,           // issued at
  "exp": 1764043200 }          // expires at
```

Two consequences you should be able to state:

1. **Never put secrets in a JWT.** It is signed, not encrypted. Anyone can read it. You can only
   verify that it hasn't been *changed*.
2. **The signature is what makes it trustworthy.** Only your server knows `JWT_SECRET`. If
   someone edits the payload to say `"sub": "someone-elses-id"`, the signature no longer matches
   and the server rejects it.

The `exp` claim is why tokens expire. Ours lasts 12 hours — long enough that a demo session
doesn't get interrupted.

---

## What the server does on every request

```
  Authorization: Bearer eyJhbGc...
          │
          ▼
  1. Is the header present?                        no  → 401 "Not authenticated"
  2. Decode and verify the signature with JWT_SECRET
  3. Is it expired?                                yes → 401 "Invalid token"
  4. Load the user with id = payload["sub"]        missing → 401 "Invalid token"
  5. Attach the user to the request
          │
          ▼
  your endpoint function finally runs
```

All five steps live in one place — `backend/app/deps.py::get_current_user`. Endpoints declare
that they need it:

```python
def list_investigations(user: User = Depends(get_current_user)):
```

and FastAPI guarantees the check happens before your code runs. You cannot forget it.

---

## Why not sessions (the classic alternative)

The traditional approach stores session state on the server: "user 42 is logged in, here's a
cookie." That requires **shared session storage** across all your API replicas — another service
to run and pay for.

A JWT carries the proof inside itself, so any replica can verify it independently with the shared
secret. Nothing to look up. That property is called being **stateless**, and it's exactly why
tokens suit cloud deployments that scale to multiple replicas.

---

## The honest caveats for your project

Two things to know so you're not caught out:

**1. Your frontend doesn't check token expiry.** `guards.tsx` only asks "is there a token in
localStorage?" It never validates it, and it has no interceptor that notices a 401 response and
redirects to login. So a tab left open past the 12-hour expiry will start getting 401s and show
a generic error rather than a friendly "please log in again". This is a known, small frontend
gap — worth mentioning rather than hiding.

**2. The token is in `localStorage`, which is readable by any JavaScript running on the page.**
The more secure pattern uses an httpOnly cookie that JS cannot read. localStorage is simpler and
entirely standard for a project like this, but a security-conscious examiner may ask. The honest
answer: *"we used a Bearer token in localStorage for simplicity; the httpOnly cookie pattern
would harden it against XSS, and we'd move to it before handling real evidence."*

**3. Our login is real, unlike the mock.** The MSW mock accepted *any* email and password and
issued a fake token. The real backend hashes passwords with bcrypt and signs real JWTs, so the
demo is genuinely authenticated.

---

## Safety notes for the deployment

- `JWT_SECRET` must never be committed to git, and must not be the default dev value in Azure.
  It goes in Container Apps **secrets** and is injected as an environment variable.
- The PostgreSQL admin password likewise: a secret, not a literal in a script that could end up
  in your report screenshots. Blur it if you screenshot the portal.
- The demo firewall rule opens Postgres to "Azure services" for convenience. That is acceptable
  for a college project **if you say so out loud** in the report and name the stronger
  alternative (VNet integration / private endpoints).

---

**Next:** [07 — Docker and containers](07-docker-containers.md).
