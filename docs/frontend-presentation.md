# DeepTrace Frontend — 1-Page Interview Cheat Sheet

> DeepTrace: AI-powered deepfake video forensics. React SPA that detects manipulated video by
> fusing spatial / temporal / frequency-domain evidence, with explainable heatmaps + forensic reports.

---

## Stack (memorize the one-liners)

| Tech | One-liner |
|---|---|
| **React 19** | Components + hooks (`useState`, `useEffect`, `useMemo`) |
| **TypeScript 6** | Types ARE our API contract — compiler enforces the shape across screens, client & mocks |
| **Vite 8** | Dev server + bundler; instant HMR; lazy route chunks via `import()` |
| **Tailwind CSS v4** | Design tokens as CSS variables — retheme the app by editing ~15 vars in `@theme` |
| **React Router 7** | Layout-route architecture with `<Outlet/>` + route guards |
| **zustand** | ~1KB global store (auth) vs Redux boilerplate; selector subscriptions |
| **Radix + shadcn pattern** | Headless accessible primitives + `cva()` variants — we OWN the components, no vendor lock |
| **recharts** | Timeline area chart (frame-level manipulation scores) |
| **lucide-react** | Tree-shakeable inline-SVG icons that inherit color |
| **MSW** | Mock Service Worker — fake backend in the browser; full product demo with zero backend |
| **oxlint** | Rust-fast linting, zero config (Vite default — not eslint) |

---

## Architecture (the 3 ideas to explain)

1. **Contract-first.** `src/types/index.ts` defines every API shape. `api.ts`, all screens and
   MSW fixtures import it → a field rename fails compilation everywhere. Backend team implements
   against the same schema → zero integration surprise.
2. **Mock-first (MSW).** Screens call `api.getInvestigations()` → `fetch('/api/...')`. In dev,
   MSW's Service Worker intercepts and answers with `mocks/fixtures.ts`. Point
   `VITE_API_BASE` at FastAPI → same code hits the real backend. **The mock→real swap is config,
   not code.**
3. **Component split.** `components/ui/` = dumb presentational primitives (no business logic).
   `features/` = smart screens (API, routing, auth). Swappable + reusable.

---

## Folder map (10 seconds)

```
app/          router + route guards (RequireAuth/RedirectIfAuthed) + AppShell layout
components/ui Button/Card/Badge/Input/Progress... (shadcn-style, cva variants)
features/     one folder per screen: landing, auth, dashboard, upload, processing, results, report
lib/          api client (single fetch wrapper) · zustand auth store · utils/cn/formatters
mocks/        MSW worker + fixtures (mock data) + handlers (mock routes w/ in-memory state)
types/        the frozen API contract (AnalysisResult, Investigation, ...)
```

---

## Data flow (the money answer)

**Login:** form → `api.login()` → MSW returns fake JWT → `setToken()` (localStorage, read by
guards) + zustand `setAuth()` (email in header) → navigate `/dashboard`.

**Analysis:** upload (drag-drop, client-side validation) → `POST` creates job `{status:'queued'}`
→ **Processing screen polls** `GET /investigations/:id` every ~1.8s → stage list advances
(`ingest → frames → faces → spatial → temporal → frequency → fusion → done`) → auto-navigate to
Results → renders verdict, 3 score cards, recharts timeline with red suspicious regions →
Report screen with JSON export + print/PDF.

**Logout:** `setToken(null)` + `logout()` → home.

---

## 4-state screen pattern (used by every data screen)

`loading spinner → error banner → empty state → data grid` — driven by three `useState`s
(data / loading / error) + one `useEffect` fetch. UI never shows a broken half-loaded state.

---

## Two bugs I fixed (great interview stories)

1. **Black screen:** a layout route (`ScrollToTop`) returned `null` without `<Outlet/>` → whole
   route tree below it never rendered. Lesson: *every layout route must render `<Outlet/>`.*
2. **Login loop:** guard read localStorage but login wrote only the zustand store → bounced back
   to `/login`. Lesson: *one source of truth for auth persistence; guards read what login writes.*

---

## The mock → real pipeline handoff

| Concern | Mock today | Real tomorrow |
|---|---|---|
| Auth | fake JWT in `handlers.ts` | FastAPI JWT + bcrypt |
| Data | `mocks/fixtures.ts` | PostgreSQL (users, investigations, results, evidence) |
| Analysis job | `stageIndex` advances per poll | Celery + Redis → worker writes progress |
| ML result | `mockResult` | `ml.pipeline.analyze()` returns an `AnalysisResult`-shaped JSON |
| Videos/heatmaps | placeholders | MinIO/S3 presigned URLs |

**ML contract (from `types/index.ts`):** emit `{verdict, confidence, spatial_score,
temporal_score, frequency_score, suspicious_segments, frame_scores, evidence[]}` → Results &
Report render it with zero frontend changes.

---

## Project highlights for the resume line

- Built a complete 7-screen product UI **before any backend existed** (mock-first, contract-driven)
- Own design system: Tailwind v4 tokens + shadcn-pattern components on Radix primitives
- Real async UX: polling job-progress screen mirroring a Celery/Redis worker flow
- Lazy-loaded routes (per-screen chunks) and a single typed API client with auth + error handling
