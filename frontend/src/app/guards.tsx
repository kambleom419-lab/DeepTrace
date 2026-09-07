import { useEffect } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { getToken } from '@/lib/api'

export function RequireAuth() {
  const location = useLocation()
  const token = getToken()

  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  return <Outlet />
}

export function RedirectIfAuthed() {
  const token = getToken()
  if (token) {
    return <Navigate to="/dashboard" replace />
  }
  return <Outlet />
}

export function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [pathname])
  return <Outlet />
}
