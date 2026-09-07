import { create } from 'zustand'

interface AuthState {
  token: string | null
  email: string | null
  setAuth: (token: string, email: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  email: null,
  setAuth: (token, email) => set({ token, email }),
  logout: () => set({ token: null, email: null }),
}))
