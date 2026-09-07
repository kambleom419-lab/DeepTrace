import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { Activity, LogOut, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/lib/auth'
import { setToken } from '@/lib/api'

const navItems = [
  { to: '/dashboard', label: 'Investigations' },
  { to: '/upload', label: 'New Analysis' },
]

export function AppShell() {
  const navigate = useNavigate()
  const { email, logout } = useAuthStore()

  const handleLogout = () => {
    setToken(null)
    logout()
    navigate('/')
  }

  return (
    <div className="min-h-screen bg-bg">
      <header className="sticky top-0 z-40 border-b border-edge bg-bg/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4">
          <Link to="/dashboard" className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-neon" />
            <span className="font-mono text-sm font-bold tracking-[0.25em] text-ink">
              DEEPTRACE
            </span>
          </Link>

          <nav className="flex items-center gap-1">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'rounded-md px-3 py-1.5 text-xs font-medium uppercase tracking-widest transition-colors',
                    isActive ? 'text-neon' : 'text-ink-dim hover:text-ink',
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
            <span className="mx-2 h-4 w-px bg-edge-2" />
            <span className="hidden font-mono text-xs text-ink-faint sm:inline">
              {email ?? 'operator'}
            </span>
            <Button variant="ghost" size="icon" onClick={handleLogout} title="Sign out">
              <LogOut className="h-4 w-4" />
            </Button>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8">
        <Outlet />
      </main>

      <footer className="border-t border-edge py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4">
          <span className="font-mono text-[0.65rem] uppercase tracking-[0.22em] text-ink-faint">
            DeepTrace · AI Video Forensics
          </span>
          <span className="flex items-center gap-1.5 font-mono text-[0.65rem] uppercase tracking-[0.22em] text-ink-faint">
            <Activity className="h-3 w-3 text-neon" />
            System nominal
          </span>
        </div>
      </footer>
    </div>
  )
}
