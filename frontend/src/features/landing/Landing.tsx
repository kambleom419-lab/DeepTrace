import { Link } from 'react-router-dom'
import { ArrowRight, ScanFace, ShieldCheck, Timeline, Waves, Fingerprint } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

const branches = [
  {
    icon: ScanFace,
    title: 'Spatial',
    desc: 'Face-level manipulation artifacts in individual frames',
  },
  {
    icon: Timeline,
    title: 'Temporal',
    desc: 'Cross-frame inconsistencies across the video timeline',
  },
  {
    icon: Waves,
    title: 'Frequency',
    desc: 'Spectral anomalies from resampling, blending and synthesis',
  },
]

export default function Landing() {
  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden">
      {/* backdrop */}
      <div className="bg-grid pointer-events-none absolute inset-0" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,rgba(34,211,167,0.12),transparent_60%)]" />

      {/* top bar */}
      <header className="relative z-10 flex items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-6 w-6 text-neon" />
          <span className="font-mono text-base font-bold tracking-[0.3em] text-ink">
            DEEPTRACE
          </span>
        </div>
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" asChild>
            <Link to="/login">Sign in</Link>
          </Button>
          <Button size="sm" asChild>
            <Link to="/register">Get started</Link>
          </Button>
        </div>
      </header>

      {/* hero */}
      <main className="relative z-10 mx-auto flex w-full max-w-5xl flex-1 flex-col items-center justify-center px-6 text-center">
        <span className="label-tech mb-6 flex items-center gap-2">
          <span className="h-1.5 w-1.5 animate-blink rounded-full bg-neon" />
          AI-POWERED VIDEO FORENSICS
        </span>
        <h1 className="text-4xl font-bold tracking-tight text-ink sm:text-6xl">
          EVERY FRAME
          <span className="block bg-gradient-to-r from-neon to-cyan bg-clip-text text-transparent">
            LEAVES A TRACE.
          </span>
        </h1>
        <p className="mt-6 max-w-2xl text-base text-ink-dim sm:text-lg">
          DeepTrace detects manipulated video by fusing spatial, temporal and
          frequency-domain evidence — then shows you exactly where the artifacts
          are with explainable heatmaps.
        </p>
        <div className="mt-10 flex items-center gap-4">
          <Button size="lg" asChild className="animate-pulse-glow">
            <Link to="/register">
              Start Investigation
              <ArrowRight />
            </Link>
          </Button>
          <Button size="lg" variant="outline" asChild>
            <Link to="/login">Sign in</Link>
          </Button>
        </div>

        {/* forensic branches */}
        <div className="mt-20 grid w-full gap-4 sm:grid-cols-3">
          {branches.map((b) => (
            <Card key={b.title} className="border-edge bg-panel/60 backdrop-blur">
              <CardContent className="p-5 text-left">
                <b.icon className="mb-3 h-5 w-5 text-neon" />
                <h3 className="font-mono text-xs uppercase tracking-[0.2em] text-ink">
                  {b.title}
                </h3>
                <p className="mt-2 text-sm text-ink-dim">{b.desc}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </main>

      <footer className="relative z-10 flex items-center justify-center gap-2 px-6 py-6">
        <Fingerprint className="h-3.5 w-3.5 text-neon" />
        <span className="font-mono text-[0.65rem] uppercase tracking-[0.22em] text-ink-faint">
          Chain of evidence · SHA-256 anchored · Grad-CAM explainable
        </span>
      </footer>
    </div>
  )
}
