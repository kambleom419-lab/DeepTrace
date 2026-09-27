import { create } from 'zustand'

interface AuthState {
  token: string | null
  email: string | null
  setAuth: (token: string, email: string) => void
  logout: () => void
}

// Stored alongside the token in lib/api.ts, which also clears it on a rejected token.
// RequireAuth already trusts localStorage across reloads, so the email has to live there
// too - otherwise it resets to null and the shell falls back to "operator" after a refresh.
export const STORED_EMAIL_KEY = 'deeptrace_email'

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  email: localStorage.getItem(STORED_EMAIL_KEY),
  setAuth: (token, email) => {
    localStorage.setItem(STORED_EMAIL_KEY, email)
    set({ token, email })
  },
  logout: () => {
    localStorage.removeItem(STORED_EMAIL_KEY)
    set({ token: null, email: null })
  },
}))
