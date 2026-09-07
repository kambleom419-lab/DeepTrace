import { http, HttpResponse, delay } from 'msw'
import {
  mockInvestigations,
  mockResult,
  mockVideoMeta,
  STAGES,
} from './fixtures'
import type { AuthResponse, Investigation } from '@/types'

// Simulated in-memory state per session so polling behaves like a real backend.
let activeInvestigation: Investigation | null = null
let stageIndex = 0

function makeToken(email: string): string {
  // mock JWT — base64 header.payload.signature, no real crypto needed
  const enc = (o: object) => btoa(JSON.stringify(o))
  return `${enc({ alg: 'HS256', typ: 'JWT' })}.${enc({ sub: email, iat: Date.now() })}.mock`
}

function authResponse(email: string): AuthResponse {
  return {
    access_token: makeToken(email),
    token_type: 'bearer',
    user: { id: 'usr-1', email },
  }
}

export const handlers = [
  // ── auth ────────────────────────────────────────────────
  http.post('/api/auth/register', async ({ request }) => {
    await delay(400)
    const { email } = (await request.json()) as { email: string }
    if (!email) return HttpResponse.json({ detail: 'Email is required' }, { status: 422 })
    return HttpResponse.json(authResponse(email), { status: 201 })
  }),

  http.post('/api/auth/login', async ({ request }) => {
    await delay(400)
    const { email, password } = (await request.json()) as {
      email: string
      password: string
    }
    if (!email || !password) {
      return HttpResponse.json({ detail: 'Invalid credentials' }, { status: 401 })
    }
    return HttpResponse.json(authResponse(email))
  }),

  // ── investigations ──────────────────────────────────────
  http.get('/api/investigations', async () => {
    await delay(300)
    const list = activeInvestigation
      ? [activeInvestigation, ...mockInvestigations]
      : mockInvestigations
    return HttpResponse.json(list)
  }),

  http.post('/api/investigations', async ({ request }) => {
    await delay(600)
    const form = await request.formData()
    const file = form.get('file') as File | null
    const title = (form.get('title') as string | null) ?? file?.name ?? 'untitled.mp4'

    activeInvestigation = {
      id: 'INV-0004',
      title,
      status: 'queued',
      progress: { stage: 'ingest', pct: 5 },
      video: { ...mockVideoMeta, filename: title, size: file?.size ?? 0 },
      created_at: new Date().toISOString(),
    }
    stageIndex = 0
    return HttpResponse.json(activeInvestigation, { status: 201 })
  }),

  http.get('/api/investigations/:id', async ({ params }) => {
    await delay(400)
    const id = String(params.id)
    if (id === 'INV-0004' && activeInvestigation) {
      // advance one stage per poll until done
      stageIndex = Math.min(stageIndex + 1, STAGES.length - 1)
      const stage = STAGES[stageIndex]
      const pct = Math.round((stageIndex / (STAGES.length - 1)) * 100)
      const done = stage === 'done'
      activeInvestigation = {
        ...activeInvestigation,
        status: done ? 'completed' : 'processing',
        progress: { stage, pct },
        result: done ? mockResult : undefined,
        completed_at: done ? new Date().toISOString() : undefined,
      }
      return HttpResponse.json(activeInvestigation)
    }
    const found = mockInvestigations.find((i) => i.id === id)
    if (!found) return HttpResponse.json({ detail: 'Not found' }, { status: 404 })
    return HttpResponse.json(found)
  }),
]
