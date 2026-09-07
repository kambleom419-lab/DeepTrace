import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Download, FileJson, FileText, Loader2, Printer } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { api } from '@/lib/api'
import { cn, formatBytes, formatPercent, truncateHash } from '@/lib/utils'
import type { Investigation } from '@/types'

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-6 py-1.5 font-mono text-xs">
      <span className="uppercase tracking-widest text-ink-faint">{k}</span>
      <span className="text-right text-ink">{v}</span>
    </div>
  )
}

export default function Report() {
  const { id } = useParams<{ id: string }>()
  const [inv, setInv] = useState<Investigation | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api
      .getInvestigation(id)
      .then(setInv)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'))
  }, [id])

  if (error) {
    return (
      <div className="rounded-md border border-alert/30 bg-alert/10 p-4 font-mono text-xs text-alert">
        LOAD_ERROR :: {error}
      </div>
    )
  }
  if (!inv?.result) {
    return (
      <div className="flex items-center justify-center py-24 text-ink-dim">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Compiling report…
      </div>
    )
  }

  const r = inv.result

  const downloadJson = () => {
    const blob = new Blob([JSON.stringify({ investigation: inv, result: r }, null, 2)], {
      type: 'application/json',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${inv.id}-report.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const print = () => window.print()

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <Button variant="ghost" size="sm" asChild>
          <Link to={`/results/${id}`}>
            <ArrowLeft />
            Back to results
          </Link>
        </Button>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={downloadJson}>
            <FileJson />
            Export JSON
          </Button>
          <Button variant="outline" size="sm" onClick={print}>
            <Printer />
            Print / PDF
          </Button>
          <Button size="sm">
            <Download />
            Export PDF
          </Button>
        </div>
      </div>

      {/* printable report body */}
      <Card className="print-area border-edge-2">
        <CardHeader className="border-b border-edge pb-5 text-center">
          <p className="label-tech">Forensic Analysis Report</p>
          <CardTitle className="font-mono text-xl tracking-[0.2em] text-ink">
            DEEPTRACE · CASE {inv.id}
          </CardTitle>
          <CardDescription className="text-xs">
            Generated {new Date(inv.completed_at ?? Date.now()).toLocaleString()} · AI-assisted
            video forensics
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-8 p-6">
          {/* video information */}
          <section>
            <h2 className="label-tech mb-3">1 · Video Information</h2>
            <div className="rounded-md border border-edge bg-bg/40 px-4 py-2">
              <Row k="Filename" v={inv.video?.filename ?? '—'} />
              <Row k="Duration" v={`${inv.video?.duration ?? '—'}s`} />
              <Row k="Resolution" v={inv.video?.resolution ?? '—'} />
              <Row k="Frame rate" v={`${inv.video?.fps ?? '—'} fps`} />
              <Row k="Size" v={inv.video ? formatBytes(inv.video.size) : '—'} />
              <Row k="SHA-256" v={inv.video ? truncateHash(inv.video.sha256, 24) : '—'} />
            </div>
          </section>

          {/* verdict */}
          <section>
            <h2 className="label-tech mb-3">2 · Final Verdict</h2>
            <div className="flex items-center justify-between rounded-md border border-edge bg-bg/40 px-4 py-4">
              <span
                className={cn(
                  'font-mono text-lg font-bold uppercase tracking-widest',
                  r.verdict === 'LIKELY_MANIPULATED'
                    ? 'text-alert'
                    : r.verdict === 'LIKELY_AUTHENTIC'
                      ? 'text-ok'
                      : 'text-amber',
                )}
              >
                {r.verdict.replace('_', ' ')}
              </span>
              <span className="font-mono text-2xl font-bold text-neon">
                {formatPercent(r.confidence)}
              </span>
            </div>
          </section>

          {/* branch scores */}
          <section>
            <h2 className="label-tech mb-3">3 · Model Evidence</h2>
            <div className="grid gap-2 sm:grid-cols-3">
              {(
                [
                  ['Spatial', r.spatial_score],
                  ['Temporal', r.temporal_score],
                  ['Frequency', r.frequency_score],
                ] as const
              ).map(([label, score]) => (
                <div key={label} className="rounded-md border border-edge bg-bg/40 p-3 text-center">
                  <p className="label-tech mb-2">{label}</p>
                  <p
                    className={cn(
                      'font-mono text-2xl font-bold',
                      score >= 0.5 ? 'text-alert' : 'text-ok',
                    )}
                  >
                    {formatPercent(score)}
                  </p>
                </div>
              ))}
            </div>
          </section>

          {/* suspicious segments */}
          <section>
            <h2 className="label-tech mb-3">4 · Suspicious Segments</h2>
            {r.suspicious_segments.length === 0 ? (
              <p className="font-mono text-xs text-ink-faint">
                No suspicious segments detected.
              </p>
            ) : (
              <ul className="space-y-2">
                {r.suspicious_segments.map((s) => (
                  <li
                    key={`${s.start}-${s.end}`}
                    className="flex items-center justify-between rounded-md border border-edge bg-bg/40 px-4 py-2 font-mono text-xs"
                  >
                    <span className="text-ink">
                      {s.start}s → {s.end}s
                    </span>
                    <Badge variant="alert">{formatPercent(s.score)}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* evidence */}
          <section>
            <h2 className="label-tech mb-3">5 · Evidence &amp; Heatmaps</h2>
            {r.evidence.length === 0 ? (
              <p className="font-mono text-xs text-ink-faint">No evidence markers.</p>
            ) : (
              <ul className="space-y-2">
                {r.evidence.map((e) => (
                  <li
                    key={e.id}
                    className="rounded-md border border-edge bg-bg/40 px-4 py-3"
                  >
                    <div className="flex items-center justify-between gap-4">
                      <span className="font-mono text-xs text-ink">{e.description}</span>
                      <Badge variant="alert">{formatPercent(e.score)}</Badge>
                    </div>
                    <p className="mt-1 font-mono text-[0.65rem] uppercase tracking-widest text-ink-faint">
                      {e.evidence_type} · frame {e.frame_number} · t={e.timestamp}s
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <Separator />

          {/* model info */}
          <section>
            <h2 className="label-tech mb-3">6 · Model Information</h2>
            <div className="flex flex-wrap gap-2">
              <Badge variant="cyan">RetinaFace</Badge>
              <Badge variant="cyan">ConvNeXt-Tiny</Badge>
              <Badge variant="cyan">Temporal GRU</Badge>
              <Badge variant="cyan">FFT/DCT + MLP</Badge>
              <Badge variant="cyan">Fusion MLP</Badge>
              <Badge variant="secondary">Grad-CAM v1</Badge>
            </div>
            <p className="mt-4 font-mono text-[0.65rem] leading-relaxed text-ink-faint">
              This report was produced by an AI-assisted forensic pipeline. Findings should be
              reviewed by a trained analyst and are not admissible as sole evidence.
            </p>
            <div className="mt-4 flex items-center gap-2 font-mono text-[0.65rem] uppercase tracking-widest text-ink-faint">
              <FileText className="h-3.5 w-3.5" />
              Report integrity · SHA-256 anchored (optional module)
            </div>
          </section>
        </CardContent>
      </Card>
    </div>
  )
}
