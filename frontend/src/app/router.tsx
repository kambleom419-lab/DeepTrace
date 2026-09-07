import { lazy, Suspense } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { AppShell } from './AppShell'
import { RedirectIfAuthed, RequireAuth, ScrollToTop } from './guards'
import Landing from '@/features/landing/Landing'
import Login from '@/features/auth/Login'
import Register from '@/features/auth/Register'

const Dashboard = lazy(() => import('@/features/dashboard/Dashboard'))
const Upload = lazy(() => import('@/features/upload/Upload'))
const Processing = lazy(() => import('@/features/processing/Processing'))
const Results = lazy(() => import('@/features/results/Results'))
const Report = lazy(() => import('@/features/report/Report'))

function PageLoader() {
  return (
    <div className="flex items-center justify-center py-24 text-ink-dim">
      <Loader2 className="mr-2 h-5 w-5 animate-spin" />
      Loading…
    </div>
  )
}

function withSuspense(el: React.ReactNode) {
  return <Suspense fallback={<PageLoader />}>{el}</Suspense>
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <ScrollToTop />,
    children: [
      { index: true, element: <Landing /> },
      {
        element: <RedirectIfAuthed />,
        children: [
          { path: 'login', element: <Login /> },
          { path: 'register', element: <Register /> },
        ],
      },
      {
        element: <RequireAuth />,
        children: [
          {
            element: <AppShell />,
            children: [
              { path: 'dashboard', element: withSuspense(<Dashboard />) },
              { path: 'upload', element: withSuspense(<Upload />) },
              { path: 'processing/:id', element: withSuspense(<Processing />) },
              { path: 'results/:id', element: withSuspense(<Results />) },
              { path: 'report/:id', element: withSuspense(<Report />) },
            ],
          },
        ],
      },
    ],
  },
])

export function AppRouter() {
  return <RouterProvider router={router} />
}
