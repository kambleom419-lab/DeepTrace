import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, FileVideo, Loader2, Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { api } from '@/lib/api'
import { formatBytes, formatDuration, formatPercent } from '@/lib/utils'
import type { Investigation, InvestigationStatus } from '@/types'

const statusVariant: Record<InvestigationStatus, 'ok' | 'cyan' | 'amber' | 'alert'> = {
  completed: 'ok',
  processing: 'cyan',
  queued: 'amber',
  failed: 'alert',
}

function InvestigationCard({ inv }: { inv: Investigation }) {
  const isDone = inv.status === 'completed'
  return (
    <Card className="transition-colors hover:border-neon/40">
      <CardContent className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <FileVideo className="mt-0.5 h-5 w-5 shrink-0 text-neon" />
            <div className="min-w-0">
              <p className="truncate font-mono text-sm text-ink">{inv.title}</p>
              <p className="mt-0.5 font-mono text-[0.65rem] uppercase tracking-widest text-ink-faint">
                {inv.id} · {new Date(inv.created_at).toLocaleDateString()}
              </p>
            </div>
          </div>
          <Badge variant={statusVariant[inv.status]}>
            {inv.status === 'processing'
              ? `${inv.progress?.stage ?? 'running'}`
              : inv.status}
          </Badge>
        </div>

        {inv.video && (
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 font-mono text-[0.7rem] text-ink-dim">
            <span>{inv.video.filename}</span>
            <span>{formatDuration(inv.video.duration)}</span>
            <span>{inv.video.resolution}</span>
            <span>{formatBytes(inv.video.size)}</span>
          </div>
        )}

        {inv.status === 'processing' && inv.progress && (
          <div className="mt-4">
            <div className="mb-1 flex justify-between font-mono text-[0.65rem] uppercase tracking-widest text-ink-dim">
              <span>{inv.progress.stage}</span>
              <span>{inv.progress.pct}%</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
              <div
                className="h-full bg-neon transition-all duration-500"
                style={{ width: `${inv.progress.pct}%` }}
              />
            </div>
          </div>
        )}

        {isDone && inv.result && (
          <div className="mt-4 flex items-center justify-between">
            <div className="flex gap-3 font-mono text-[0.7rem] uppercase tracking-widest">
              <span className="text-ink-dim">CONF</span>
              <span className="text-neon">{formatPercent(inv.result.confidence)}</span>
              <span
                className={
                  inv.result.verdict === 'LIKELY_MANIPULATED' ? 'text-alert' : 'text-ok'
                }
              >
                {inv.result.verdict.replace('_', ' ')}
              </span>
            </div>
            <Button variant="outline" size="sm" asChild>
              <Link to={`/results/${inv.id}`}>
                View report
                <ArrowRight />
              </Link>
            </Button>
          </div>
        )}

        {inv.status === 'queued' && (
          <div className="mt-4 flex items-center gap-2 font-mono text-[0.7rem] uppercase tracking-widest text-ink-faint">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Waiting for worker
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export default function Dashboard() {
  const [investigations, setInvestigations] = useState<Investigation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getInvestigations()
      .then(setInvestigations)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-ink">Investigations</h1>
          <p className="mt-1 text-sm text-ink-dim">
            Case files and their forensic analysis status
          </p>
        </div>
        <Button asChild>
          <Link to="/upload">
            <Plus />
            New analysis
          </Link>
        </Button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-24 text-ink-dim">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          Loading case files…
        </div>
      ) : error ? (
        <div className="rounded-md border border-alert/30 bg-alert/10 p-4 font-mono text-xs text-alert">
          LOAD_ERROR :: {error}
        </div>
      ) : investigations.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-edge-2 py-24 text-center">
          <p className="font-mono text-sm text-ink-dim">No investigations yet</p>
          <p className="mt-1 text-xs text-ink-faint">
            Upload a video to begin your first forensic analysis
          </p>
          <Button className="mt-6" asChild>
            <Link to="/upload">
              <Plus />
              Start analysis
            </Link>
          </Button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {investigations.map((inv) => (
            <InvestigationCard key={inv.id} inv={inv} />
          ))}
        </div>
      )}
    </div>
  )
}
