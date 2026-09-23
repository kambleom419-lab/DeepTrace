# DeepTrace Frontend — Complete Architecture Guide

> Written for **interview + resume prep** and for anyone who needs to explain every file,
> every library, and every design decision with first-principles reasoning.
> Read this alongside the code — every claim cites the exact file and line range.

---

## 0. Big Picture — the "Why" in one paragraph

DeepTrace is a **mock-first, contract-driven SPA**. We built the entire UI *before* any backend
or ML existed, against a **frozen JSON contract** (see `src/types/index.ts`). The browser
intercepts its own network calls with **MSW** and answers them with realistic fixture data, so
the frontend behaves exactly like it will in production. When the real FastAPI backend + ML
pipeline land, we flip one env var and the *same* frontend code talks to real endpoints. No UI
rewrite.

> **Interview line:** "We used an API-contract-first workflow — the TypeScript types *are* the
> contract, MSW let us build and demo the whole product with zero backend, and the swap to real
> APIs is a configuration change, not a code change."

---

## 1. The Full Technology Stack (and why each piece)

### 1.1 Build tooling

| Tool | What it does | Why we chose it | Config file |
|---|---|---|---|
| **Vite 8** | Dev server + production bundler (Rollup/Rolldown-based) | Instant HMR, modern ESM, zero-config TS | `vite.config.ts` |
| **TypeScript 6** | Static type checker on top of JS | Catches bugs at compile time, types ARE our API contract | `tsconfig*.json` |
| **Tailwind CSS v4** | Utility-first CSS framework | Design tokens via CSS, no separate CSS files per component | `src/index.css` (`@theme`) |
| **oxlint** | Linter (`npm run lint`) | Rust-based, ~50-100x faster than ESLint, zero config. **We use oxlint, not eslint** — it ships with Vite's React template, has no config drift, and is fast enough to run on every save | `.oxlintrc.json` |
| **MSW** | Mock Service Worker — intercepts network at the Service Worker layer | Full UI runs without a backend; enables mock-first dev | `src/mocks/*` |

### 1.2 Runtime libraries (in `package.json`)

| Library | Why installed | Where used |
|---|---|---|
| **react / react-dom 19** | Core UI — components, hooks (`useState`, `useEffect`, `useMemo`) | everywhere |
| **react-router-dom 7** | Client-side navigation + route guards | `src/app/router.tsx`, `src/app/guards.tsx` |
| **zustand** | Tiny global state store (auth token/email) — a ~1KB alternative to Redux | `src/lib/auth.ts` |
| **@radix-ui/react-\*** | Headless, accessible primitives (Dialog, Dropdown, Tooltip, Tabs, Progress, Label, Slot) | `src/components/ui/*` |
| **class-variance-authority** | Clean variant API for `cva()` — powers shadcn-style variants | `button.tsx`, `badge.tsx` |
| **clsx + tailwind-merge** | `cn()` helper — merge + dedupe Tailwind classes | `src/lib/utils.ts` |
| **lucide-react** | Tree-shakeable SVG icon set (ShieldCheck, Loader2, AlertTriangle…) | every feature file |
| **recharts** | Charting (the timeline area chart) | `Results.tsx` |
| **msw** (dev) | Mock API layer in the browser | `src/mocks/*` |
| **tailwindcss + @tailwindcss/vite** | Tailwind v4 Vite plugin | `vite.config.ts` |

> **Why lucide and not Font Awesome / an icon font?** lucide-react ships each icon as a tiny
> React component that renders an inline `<svg>`. That means: (1) only the icons you actually
> import end up in the bundle (tree-shaking), (2) icons inherit `currentColor`, so
> `text-neon`/`text-alert` classes color them automatically, (3) no external font request, no
> CLS. Font Awesome loads an entire font file and colors via complex CSS.

### 1.3 The `@/` path alias

Every import like `import { Button } from '@/components/ui/button'` is an **absolute alias**
pointing at `src/`. Configured twice (must match):
- `tsconfig.app.json` → `"paths": { "@/*": ["./src/*"] }`
- `vite.config.ts` → `alias: { '@': import.meta.dirname + '/src' }`

> **Why?** Relative imports (`../../../components/ui/button`) break the moment you move a file
> and make every import unreadable. The alias keeps imports short, stable, and self-documenting.

---

## 2. File Tree, Explained

```
frontend/
├── index.html            # HTML shell — Vite injects the built app here
├── vite.config.ts        # Vite + Tailwind plugins, @/ alias, dev port
├── package.json          # scripts: dev / build / lint / preview
├── tsconfig*.json        # TS compiler settings for app + node configs
├── public/
│   ├── favicon.svg       # brand mark
│   └── mockServiceWorker.js  # MSW's worker script — must sit at the root so it
│                             # can intercept ALL requests from the page
└── src/
    ├── main.tsx              # ENTRY: mounts React, starts MSW in background
    ├── App.tsx               # Root providers (Tooltip) + global chrome
    ├── index.css             # Tailwind import + @theme design tokens + scanline FX
    ├── app/                  # App-level wiring (router, guards, shell layout)
    ├── components/ui/        # Reusable, framework-agnostic UI primitives
    ├── features/             # One folder PER SCREEN (feature-based structure)
    ├── lib/                  # Cross-cutting logic (api, auth store, utils)
    ├── mocks/                # MSW worker + fixture data + route handlers
    └── types/                # THE API CONTRACT — every screen imports from here
```

---

## 3. First Principles: Why We Split Code Into So Many Files

Ask yourself: *"What happens to a 5000-line `App.tsx`?"* → it's unreadable, unmergeable,
untestable, and any state change re-renders everything.

**The rules we follow:**

1. **One responsibility per file.** `card.tsx` only knows how to draw a card.
2. **Reuse over duplication.** A `Button` with 6 variants is written once in
   `components/ui/button.tsx` and used in 8 screens. The alternative — copy-pasting the same
   `className` string 40 times — guarantees styling drift.
3. **`components/ui` vs `features`:** `components/ui` holds **dumb, presentational** primitives
   with no business logic (they take props, they render). `features` holds **smart** components
   that know about the app: they call the API, read the URL, manage auth.
   > Interview: "I can swap any UI primitive for a different library without touching a single
   > screen — that's the payoff of separating presentational from container components."
4. **Types live in one file (`types/index.ts`)** because they describe the *contract* shared by
   every layer — screens, the API client, and the mocks all import the same shape, so a field
   rename in one place is caught by the compiler everywhere.
5. **Lazy-load heavy routes.** `Results.tsx` imports recharts (~150KB). Loading it eagerly would
   block the first paint for every user. `lazy(() => import(...))` in `router.tsx` splits it into
   its own chunk that only downloads when the user actually opens a result.

---

## 4. Entry Point & Boot Order (`main.tsx` + `App.tsx`)

**`src/main.tsx` — the entry.** This is the first code the browser runs:

1. `createRoot(document.getElementById('root')!)` — grab the `<div id="root">` from `index.html`.
2. `<App />` renders inside `<StrictMode>` (dev-only double-render that surfaces bugs).
3. **Render happens FIRST**, then `enableMocking()` runs in the background.

> Why render before MSW? Because the very first bug in this project was a black screen: we used
> to `await worker.start()` *before* `createRoot()`. When MSW was slow/stale, React never mounted.
> The fix (a hard-won lesson): **never let a non-critical dependency gate the UI.**

**`src/App.tsx` — root providers + chrome.** Wraps the app in:
- `<TooltipProvider>` — Radix tooltips need one provider at the root.
- `.scanlines` div — a global overlay that adds the CRT scanline texture (see `index.css`).

---

## 5. The Router — How Navigation & "Pages" Work (`app/router.tsx`)

React Router v7 with **`createBrowserRouter`** — a *route object* tree instead of `<Route>` JSX.
This is a **layout-route** architecture:

```
/  (ScrollToTop layout — renders <Outlet/>)
├── index           → Landing         (public)
├── login/register  → RedirectIfAuthed layout  (public, but redirects if logged in)
└── (RequireAuth layout)              (private — redirects to /login if no token)
    └── AppShell layout               (header + footer + <Outlet/>)
        ├── dashboard   → Dashboard
        ├── upload      → Upload
        ├── processing/:id → Processing
        ├── results/:id → Results
        └── report/:id  → Report
```

**The critical concept: `<Outlet />`.** A layout route renders its children *through* `<Outlet />`.
A layout without an `<Outlet />` renders nothing for its children.

> ⚠️ **This was the black-screen bug.** `ScrollToTop` used to `return null` with no `<Outlet />`,
> so the ENTIRE tree under `/` (including Landing) never rendered. Fix: `return <Outlet />`
> (now at `guards.tsx` line ~28). Remember for interviews: "every layout route must render
> `<Outlet />` for its children to appear."

### Route guards (`app/guards.tsx`) — "protected routes" from first principles

- `RequireAuth` — reads the token from **localStorage** (`getToken()`). No token → `<Navigate to="/login" state={{from: location}}/>`. The `state.from` lets Login bounce you back to where you were.
- `RedirectIfAuthed` — if you ARE logged in and visit `/login`, skip it → `/dashboard`.
- `ScrollToTop` — resets scroll position on route change (otherwise navigating keeps old scroll).

### Why Lazy? (`withSuspense` + `lazy()`)

`Dashboard`, `Upload`, `Processing`, `Results`, `Report` are `lazy()`-loaded. Vite turns each
into a separate `.js` chunk fetched on demand (you saw `Results-KAPzpKXu.js` ≈ 381KB — that's
recharts, isolated). `<Suspense fallback={<PageLoader/>}>` shows a spinner while the chunk loads.
The public pages (Landing/Login/Register) are eager so first paint is instant.

---

## 6. The App Shell (persistent layout) — `app/AppShell.tsx`

The logged-in area shares a **persistent frame** — header + footer — that does NOT re-render on
every route change. The page content flows into `<Outlet />` in `<main>` (line ~57).

Why? Re-rendering the nav on every navigation is wasteful; a persistent layout also keeps the
scroll position and the auth dropdown state intact. This is the SPA equivalent of a shared
`_layout` in server frameworks.

---

## 7. Design System from First Principles (`index.css` + `components/ui`)

### 7.1 Tailwind v4 `@theme` tokens (`src/index.css`)

Tailwind v4 moved config INTO CSS. The `@theme` block defines **design tokens**:

```css
--color-bg: #0a0e14;      /* near-black page background */
--color-neon: #22d3a7;    /* terminal green — primary accent */
--color-cyan: #38bdf8;    /* secondary accent */
--color-alert: #f87171;   /* red = manipulated / danger */
--color-ok: #34d399;      /* green = authentic / success */
```

Any `--color-X` becomes usable as `bg-X`, `text-X`, `border-X`. So the whole app is themed by
editing ~15 variables — no per-file color hunting.

### 7.2 Why shadcn-style components instead of a component library?

A library like Material-UI ships components you **cannot easily restyle**. Instead we built our
own primitives in `components/ui` (the shadcn pattern) — components that are:
- **Styled with Tailwind** (swap classes freely),
- **Accessible** (wrapped around Radix primitives: real dialog focus trap, real tabs semantics),
- **Variant-driven** via `cva()` (Button has `default/secondary/outline/ghost/danger/link`).

`button.tsx` line ~4: `buttonVariants = cva('...base classes...', { variants: {...} })`.
`asChild` (Radix `<Slot>`) lets you turn the button into a `<Link>` while keeping its styles:
`<Button asChild><Link to="/dashboard">…</Link></Button>`.

> Interview: "Instead of fighting a vendor's theme, we own the components. cva gives typed
> variants, Radix gives accessibility, Tailwind gives theming."

---

## 8. Feature-Based Structure & the Screens (`features/`)

We group by **feature (screen)**, not by file type. Why? A new developer opening
`features/results/` immediately sees *everything about results*. Co-locating by type
(`components/`, `hooks/`, `pages/` at root) scatters one feature across 6 folders.

### 8.1 Landing (`features/landing/Landing.tsx`) — pure marketing
Simplest file. No state, no API. It maps over a `branches` array (lines ~7-18) to render the 3
forensic cards — the textbook "data-driven UI" pattern: add a 4th branch and it appears with no
JSX changes.

### 8.2 Auth (`features/auth/Login.tsx`, `Register.tsx`) — form + token
The interesting logic is `handleSubmit` (Login lines ~22-35):
1. `setLoading(true)` → disables button, shows "Authenticating…"
2. `await api.login({email, password})` → POST to `/api/auth/login` (intercepted by MSW)
3. `setToken(res.access_token)` → **persist to localStorage** (route guards read this!)
4. `setAuth(...)` → update in-memory zustand store (header shows email)
5. `navigate(from)` → go to dashboard (or wherever you were headed)

> ⚠️ **Auth bug we fixed:** earlier we only did `setAuth` (memory) and never `setToken`
> (localStorage). The guard read localStorage, saw nothing, and bounced you back to `/login` —
> the "login loop". Lesson: **keep one source of truth for auth persistence and make guards
> read from the same place the login writes.**

### 8.3 Dashboard (`features/dashboard/Dashboard.tsx`) — the data-fetching pattern
The reusable pattern used by every data screen:
1. `useState` for `investigations`, `loading`, `error` (lines ~109-111)
2. `useEffect(() => { api.getInvestigations().then(...).catch(...).finally(...) }, [])` — fetch once on mount (lines ~113-118)
3. Render 4 states: **loading spinner → error banner → empty state → list** (lines ~137-180)

The `InvestigationCard` subcomponent (line ~26) isolates card rendering. The dashboard maps the
list into cards (`investigations.map(inv => <InvestigationCard/>)`).

> Interview: "Every list screen uses the same loading/error/empty/data state machine. By making
> it explicit, the UI never shows a broken half-loaded state."

### 8.4 Upload (`features/upload/Upload.tsx`) — drag-drop from first principles
Native HTML5 drag-drop events: `onDragOver` (preventDefault so drop fires) → highlight →
`onDrop` reads `e.dataTransfer.files[0]`. Clicking the zone triggers a hidden `<input type="file">`
via `inputRef.current?.click()` (line ~31).

Validation happens client-side before any network call: MIME type + extension allowlist
(`ACCEPTED`, line ~8) and a 500MB cap (`MAX_SIZE`). Submit builds `FormData` and posts — the same
API the real backend will receive.

### 8.5 Processing (`features/processing/Processing.tsx`) — polling, the core async pattern
Because ML analysis takes **minutes**, the server can't respond in one HTTP request. The pattern:

- `POST /api/investigations` returns `{id, status:'queued'}` immediately.
- The Processing screen then **polls** `GET /api/investigations/:id` every ~1.8s (`setTimeout(poll, 1800)`, line ~53).
- Each response carries `progress: {stage, pct}` — the screen renders the stage list from the
  shared `STAGES` constant (imported from `mocks/fixtures.ts`).
- On `completed` → `navigate('/results/:id')`. On `failed` → show error.
- `let cancelled = false` + cleanup (`return () => { cancelled = true }`, lines ~64-66) prevents
  setState after unmount — a classic React memory-leak guard.

> Interview: "Analysis is async — we create a job, poll its progress, and only navigate when the
> backend says it's done. This is exactly how we'll talk to the real Celery/Redis worker later —
> the endpoint shapes already match."

### 8.6 Results (`features/results/Results.tsx`) — the money screen
Wires together everything:
- **Derived config maps** — `verdictConfig` (line ~21) maps each possible `Verdict` to its icon,
  label and color. Rendering stays a dumb lookup.
- **`ScoreCard`** (line ~66) — reusable: label + score + color. `flagged = score >= 0.5`
  thresholds red vs green.
- **Recharts timeline** (lines ~270-330) — `frame_scores` (one score per sampled frame) becomes
  `{t, score}` points via `buildTimelineData` (line ~98), rendered as an `<AreaChart>`.
  `<ReferenceArea x1 x2>` shades the suspicious time ranges red.
- **Evidence switcher** — clicking a heatmap chip (`onClick={() => setSelectedEvidence(e.id)}`,
  line ~333) updates `selectedEvidence` state; the heatmap panel re-renders for that evidence.
- **`useMemo`** (line ~132) recomputes the timeline only when `result` changes, not on every render.

> Interview: "The verdict screen is declarative: given an AnalysisResult, the UI is fully
> determined. No imperative DOM — just data in, JSX out."

### 8.7 Report (`features/report/Report.tsx`) — printable document + export
A forensic report must leave the app. Two export paths:
- **JSON**: `downloadJson()` (lines ~47-54) — builds a `Blob`, creates an object URL, clicks a
  hidden `<a download>`, revokes the URL. No server needed.
- **PDF**: `window.print()` — the browser's print dialog; the report card carries a `print-area`
  class so print CSS can isolate it.

---

## 9. The "Lib" Layer — Reusable Logic (`lib/`)

### 9.1 `lib/utils.ts`
- `cn(...)` — the **class combiner**. `clsx` joins conditional classes; `tailwind-merge`
  dedupes conflicts (`"px-4 px-6"` → keeps the last). Used by every UI component.
- `formatPercent` / `formatDuration` / `formatBytes` / `truncateHash` — pure formatters so the
  same formatting logic isn't copy-pasted across screens.

### 9.2 `lib/api.ts` — the ONE place the frontend talks to the outside world
Single responsibility: every screen calls `api.getInvestigations()`, never `fetch()` directly.
- `request<T>` (line ~29) — one generic function that: sets JSON headers, attaches
  `Authorization: Bearer <token>` from localStorage, parses JSON, and on non-2xx throws a typed
  `ApiError` with the backend's `detail` message.
- `API_BASE = import.meta.env.VITE_API_BASE ?? '/api'` — **this is the magic swap point.** In
  dev, `/api` is answered by MSW. Point `VITE_API_BASE` at the real backend and every call goes
  to FastAPI. Zero code changes.

> Interview: "All network logic — headers, auth, error handling, base URL — lives in one client.
> Screens are agnostic about where data comes from; that's what makes the mock→real swap trivial."

### 9.3 `lib/auth.ts` — zustand store
Why zustand instead of React Context? Context re-renders *every* consumer on any change and
causes "provider hell" as state grows. Zustand is ~1KB, has no provider, and components
subscribe to only the slice they need (`useAuthStore(s => s.email)`). For one token + email it's
the pragmatic choice over Redux boilerplate.

> **Two stores of truth to know:** zustand = in-memory (email shown in header, instant);
> localStorage = persistent (survives refresh, read by guards). Login writes both.

---

## 10. Types — The Frozen Contract (`types/index.ts`)

This file is the **single source of truth** for the API shape. Every `AnalysisResult`,
`Investigation`, `Verdict` etc. is defined here, and `api.ts`, all screens, and the MSW fixtures
import it. The comments even reference the plan doc ("see plan §3").

Why does this matter for the ML pipeline? When the backend returns its JSON, if a field is
missing, TypeScript fails to compile. The contract is enforced on BOTH sides of the fence —
this is what lets three people build frontend / backend / ML independently against one agreed
schema.

---

## 11. MSW — Why It Exists & How It Works (`mocks/`)

### The problem it solves
The real system needs: React frontend + FastAPI backend + Redis/Celery queue + ML workers +
PostgreSQL + MinIO. That's a month of backend work before the UI is testable. **We refused to
wait.**

### How it works
1. `public/mockServiceWorker.js` — a real Service Worker script (served at site root, so it can
   intercept every request).
2. `mocks/browser.ts` — `setupWorker(...handlers)` registers the handlers.
3. `main.tsx` starts it in dev only (`import.meta.env.DEV`), unless `VITE_USE_MOCKS=false`.
4. When the app calls `fetch('/api/investigations')`, the Service Worker intercepts it and
   `handlers.ts` answers with fixture data — no network leaves the browser.

### The three files
- **`fixtures.ts`** — the *data*: `mockResult` (the full 94.7% manipulated result), 3 seeded
  investigations, `STAGES`. Realistic enough that screens look production-ready.
- **`handlers.ts`** — the *routes*: `http.post('/api/auth/login')` returns a fake JWT
  (`makeToken`, base64 — fine for dev, never for prod). Crucially it keeps **in-memory state**
  (`activeInvestigation`, `stageIndex`) so the processing flow is *interactive*: each poll of
  `INV-0004` advances one stage (lines ~85-99) until `done`, mimicking a real async worker.
- **`browser.ts`** — wires handlers into the worker.

### The mock → real handoff (THE key architectural point)
Nothing in `features/` knows MSW exists. Screens call `api.*`, which fetches `/api/...`. MSW
answers in dev; FastAPI answers in prod. The **same payload shapes** come from
`mocks/fixtures.ts` today and from the ML pipeline tomorrow.

**To go live:**
1. `VITE_API_BASE=http://localhost:8000` (or set the backend behind `/api` via nginx/CORS).
2. Backend implements exactly the routes in `handlers.ts` with the types in `types/index.ts`.
3. ML pipeline returns exactly an `AnalysisResult`-shaped JSON (verdict, confidence,
   spatial/temporal/frequency scores, suspicious_segments, frame_scores, evidence with URLs).
4. Evidence `heatmap_url`s point to MinIO/presigned URLs served by FastAPI.

> Interview: "MSW is a compile-time-safe fake backend. The contract in TypeScript + the routes in
> handlers.ts are the spec the FastAPI team implements — that's parallel development with zero
> integration surprise."

---

## 12. The Complete Data Flow (one read-through)

**Login:**
1. User submits → `Login.tsx handleSubmit` → `api.login()` → `fetch('/api/auth/login')`
2. MSW intercepts → returns `{access_token, user}` → Login stores token (localStorage + zustand)
3. `navigate('/dashboard')` → `RequireAuth` sees token → renders `AppShell` → `Dashboard`
4. Dashboard mounts → `useEffect` → `api.getInvestigations()` → MSW returns 3 seeded cases → cards render

**New analysis (the money flow):**
1. Upload a file → `api.uploadInvestigation(file)` posts multipart → MSW creates `INV-0004` (status: queued) → navigate to `/processing/INV-0004`
2. Processing polls `GET /investigations/INV-0004` every 1.8s → MSW advances `stageIndex` each call → stage list + progress bar update live
3. When stage = `done`, status = `completed`, result = `mockResult` → auto-navigate to `/results/INV-0004`
4. Results fetches the investigation → renders verdict banner, 3 score cards, recharts timeline with red ReferenceAreas, evidence chips
5. "Forensic report" → `/report/INV-0004` → printable doc + JSON export

**Logout:** `AppShell` `handleLogout` → `setToken(null)` (localStorage) + `logout()` (zustand) → navigate to `/`.

---

## 13. Line-Number Code Walkthroughs (read these with the file open)

### `main.tsx` (all 25 lines)
- **1-4** imports StrictMode, createRoot, CSS, App
- **7-16** `enableMocking()` — dev-only, best-effort, wrapped in try/catch
- **19-22** `createRoot(rootEl).render(...)` — mount FIRST
- **25** `void enableMocking()` — fire-and-forget, never blocks paint

### `app/guards.tsx`
- **5-13** `RequireAuth` — no token → `<Navigate to="/login" .../>`; else `<Outlet/>`
- **15-21** `RedirectIfAuthed` — token present on /login → `/dashboard`
- **23-30** `ScrollToTop` — scroll reset + **`<Outlet/>`** (the fix)

### `app/router.tsx`
- **10-14** lazy imports of the 5 heavy screens
- **17-23** `PageLoader` spinner fallback
- **29-59** the route tree: `/` → public index → `RedirectIfAuthed` (login/register) → `RequireAuth` → `AppShell` → 5 routes. Note the nested layout without a `path` — those are pure layout wrappers.
- **61-63** `RouterProvider` mounts the router

### `lib/api.ts`
- **9-18** token helpers (localStorage `deeptrace_token`)
- **29-46** `request<T>` — headers, auth, error normalization (`body.detail`), typed response
- **48-68** `api` object — the only place URLs + methods are named
- **70-83** upload via FormData (multipart)

### `features/dashboard/Dashboard.tsx`
- **8** `statusVariant` — maps status → badge color (declarative)
- **26** `InvestigationCard` — presentational subcomponent
- **109-111** three `useState`s: data / loading / error
- **113-118** fetch-on-mount `useEffect`
- **137-180** 4-state render: loading → error → empty → grid

### `features/processing/Processing.tsx`
- **49-58** `poll()` recursion with `setTimeout` 1800ms
- **61-67** `useEffect` + `cancelled` cleanup flag
- **78-118** stage list render — `STAGES.map`, `isDone/isActive` derive styling

### `features/results/Results.tsx`
- **21-36** `verdictConfig` — verdict → {icon, label, tone}
- **66-100** `ScoreCard` + threshold logic
- **105-111** `buildTimelineData` — raw scores → chart points
- **132-134** `useMemo` over result
- **270-330** recharts AreaChart + 2 `ReferenceArea` red bands
- **330-344** evidence chip selection

### `features/report/Report.tsx`
- **12-20** `Row` presentational helper
- **47-54** `downloadJson` — Blob + object URL + hidden anchor
- **58** `print` → `window.print()`

### `mocks/handlers.ts`
- **16-28** fake JWT + auth response factory
- **37-41** `POST /register` (delay simulates network)
- **44-52** `POST /login`
- **61-69** `GET /investigations` (prepends active one)
- **73-86** `POST /investigations` (creates INV-0004, resets stage)
- **88-103** `GET /investigations/:id` — the **stateful polling** heart: advances one stage per call

---

## 14. Interview One-Liners (memorize these)

- **Why TypeScript?** "The type file is our API contract — the compiler enforces it across
  screens, client, and mocks, so the backend team implements against a schema that can't drift."
- **Why Vite?** "Instant HMR and a bundler that outputs per-route chunks via lazy imports."
- **Why Tailwind v4?** "Design tokens as CSS variables — retheme the whole app by editing 15
  variables."
- **Why component splitting?** "Presentational primitives in `ui/`, smart feature components in
  `features/` — swappable, testable, and no duplication."
- **Why zustand over Redux?** "1KB, no provider, selector-based subscriptions. Redux is
  overkill for one token."
- **Why MSW?** "We demoed a full product with zero backend; the same API client now points at
  FastAPI. Parallel development with a frozen contract."
- **Why polling (not WebSocket) for progress?** "The mock already matches the real Celery job
  flow — poll a status endpoint; if we later need real-time we can swap in SSE/WebSocket behind
  the same hook."
- **Why oxlint?** "It's the default in Vite's template, Rust-fast, and needs zero config —
  faster feedback than ESLint with the same catches."
- **Why lucide-react?** "Tree-shakeable inline SVGs that inherit color — small bundle, easy
  theming, no icon-font dependency."

---

## 15. Common "Gotchas" We Hit (good war stories)

1. **`ScrollToTop` returning `null`** → black screen (no `<Outlet/>`). Every layout route must
   render `<Outlet/>`.
2. **Login loop** → guard read localStorage, login wrote only zustand. Single source of truth.
3. **`NODE_ENV=production` on your machine** → npm silently skipped devDependencies → `tsc`
   missing. Fix: `set NODE_ENV=development&& npm install` or remove the global var.
4. **TypeScript 6 deprecated `baseUrl`** → use `paths` with `./src/*` and no baseUrl.
5. **Stale MSW service worker** → old worker cached in browser kept intercepting after code
   changed. Unregister in DevTools → Application → Service Workers.

---

## 16. What Connects to Backend / ML / DB (the future)

| Frontend concern | Mock today | Real tomorrow |
|---|---|---|
| Auth endpoints | `handlers.ts` fake JWT | FastAPI `/api/auth/*` (JWT + bcrypt) |
| Investigation CRUD | in-memory `activeInvestigation` | PostgreSQL tables (`users`, `investigations`, `videos`, `analysis_results`, `evidence`, `reports`, `jobs`) |
| Video storage | none (mock) | MinIO/S3 (upload returns storage path) |
| Analysis job | `stageIndex` advancing per poll | Celery worker → Redis queue; worker writes `progress` rows |
| ML result | `mockResult` | `ml/detection/pipeline.py::analyze()` returns `AnalysisResult`-shaped JSON |
| Heatmap images | placeholder | Grad-CAM PNGs served from MinIO via presigned URLs |

**The handoff contract (from `types/index.ts`):** the ML pipeline must emit
`{ verdict, confidence, spatial_score, temporal_score, frequency_score, suspicious_segments,
frame_scores, evidence[] }`. Build it once, and Results/Report render it with zero frontend changes.
