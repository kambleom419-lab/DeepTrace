import { STORED_EMAIL_KEY } from './auth'
import type {
  AuthResponse,
  Investigation,
  LoginRequest,
  RegisterRequest,
} from '@/types'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

const TOKEN_KEY = 'deeptrace_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json()
    return body.detail ?? body.message ?? res.statusText
  } catch {
    return res.statusText
  }
}

function handleUnauthorized(path: string) {
  // A 401 from /auth/* means "wrong credentials" - that belongs on the form, not a redirect.
  if (path.startsWith('/auth/')) return
  setToken(null)
  localStorage.removeItem(STORED_EMAIL_KEY)
  const { pathname } = window.location
  if (pathname !== '/login' && pathname !== '/register') {
    window.location.assign('/login')
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })

  if (!res.ok) {
    if (res.status === 401) handleUnauthorized(path)
    throw new ApiError(res.status, await errorMessage(res))
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// Both the heatmaps and the source video live behind authenticated routes, and neither an
// <img> nor a <video> tag can send an Authorization header. Fetch the bytes with the token
// and hand the component a blob URL instead.
async function fetchAsBlobUrl(url: string): Promise<string> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(url, { headers })
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res))
  return URL.createObjectURL(await res.blob())
}

export const api = {
  login: (body: LoginRequest) =>
    request<AuthResponse>('/auth/login', { method: 'POST', body: JSON.stringify(body) }),
  register: (body: RegisterRequest) =>
    request<AuthResponse>('/auth/register', { method: 'POST', body: JSON.stringify(body) }),
  getInvestigations: () => request<Investigation[]>('/investigations'),
  getInvestigation: (id: string) => request<Investigation>(`/investigations/${id}`),
  uploadInvestigation: (file: File, title?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (title) form.append('title', title)
    const headers: Record<string, string> = {}
    const token = getToken()
    if (token) headers.Authorization = `Bearer ${token}`
    return fetch(`${API_BASE}/investigations`, { method: 'POST', body: form, headers }).then(
      async (res) => {
        // read `detail` here too, or the UI shows "Unprocessable Entity" instead of the
        // backend's actual reason (e.g. "File exceeds 200 MB limit")
        if (!res.ok) throw new ApiError(res.status, await errorMessage(res))
        return res.json() as Promise<Investigation>
      },
    )
  },
  getEvidenceUrl: (investigationId: string, evidenceId: string) =>
    `${API_BASE}/investigations/${investigationId}/evidence/${evidenceId}`,
  getVideoUrl: (investigationId: string) =>
    `${API_BASE}/investigations/${investigationId}/video`,
  fetchBlob: fetchAsBlobUrl,
}
