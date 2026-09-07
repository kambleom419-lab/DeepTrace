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

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })

  if (!res.ok) {
    let message = res.statusText
    try {
      const body = await res.json()
      message = body.detail ?? body.message ?? message
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, message)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
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
        if (!res.ok) throw new ApiError(res.status, res.statusText)
        return res.json() as Promise<Investigation>
      },
    )
  },
  getEvidenceUrl: (investigationId: string, evidenceId: string) =>
    `${API_BASE}/investigations/${investigationId}/evidence/${evidenceId}`,
}
