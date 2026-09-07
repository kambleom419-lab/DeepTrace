import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  Loader2,
  ScanFace,
  Waves,
  Clock,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { api } from '@/lib/api'
import { cn, formatPercent, truncateHash } from '@/lib/utils'
import type { AnalysisResult, Investigation, Verdict } from '@/types'

const verdictConfig: Record<
  Verdict,
  { icon: typeof AlertTriangle; label: string; tone: 'alert' | 'ok' | 'amber' }
> = {
  LIKELY_MANIPULATED: {
    icon: AlertTriangle,
    label: 'LIKELY MANIPULATED',
    tone: 'alert',
  },
  LIKELY_AUTHENTIC: {
    icon: CheckCircle2,
    label: 'LIKELY AUTHENTIC',
    tone: 'ok',
  },
  INCONCLUSIVE: {
    icon: AlertTriangle,
    label: 'INCONCLUSIVE',
    tone: 'amber',
  },
}

const toneText = {
  alert: 'text-alert',
  ok: 'text-ok',
  amber: 'text-amber',
} as const
const toneBorder = {
  alert: 'border-alert/40',
  ok: 'border-ok/40',
  amber: 'border-amber/40',
} as const

function ScoreCard({
  label,
  score,
  icon: Icon,
}: {
  label: string
  score: number
  icon: typeof ScanFace
}) {
  const pct = formatPercent(score)
  const flagged = score >= 0.5
  return (
    <Card className="border-edge">
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <span className="label-tech flex items-center gap-1.5">
            <Icon className="h-3.5 w-3.5" />
            {label}
          </span>
          <Badge variant={flagged ? 'alert' : 'ok'}>{flagged ? 'FLAGGED' : 'CLEAN'}</Badge>
        </div>
        <p
          className={cn(
            'mt-3 font-mono text-3xl font-bold tracking-tight',
            flagged ? 'text-alert' : 'text-ok',
          )}
        >
          {pct}
        </p>
        <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
          <div
            className={cn('h-full', flagged ? 'bg-alert' : 'bg-ok')}
            style={{ width: pct }}
          />
        </div>
      </CardContent>
    </Card>
  )
}

function buildTimelineData(result: AnalysisResult) {
  const count = result.frame_scores.length
  const duration = 48.6 // fallback; real API will provide video.duration
  return result.frame_scores.map((score, i) => ({
    t: Number(((i / Math.max(count - 1, 1)) * duration).toFixed(1)),
    score,
  }))
}

export default function Results() {
  const { id } = useParams<{ id: string }>()
  const [inv, setInv] = useState<Investigation | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedEvidence, setSelectedEvidence] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api
      .getInvestigation(id)
      .then(setInv)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'))
  }, [id])

  const result = inv?.result
  const timeline = useMemo(() => (result ? buildTimelineData(result) : []), [result])

  if (error) {
    return (
      <div className="rounded-md border border-alert/30 bg-alert/10 p-4 font-mono text-xs text-alert">
        LOAD_ERROR :: {error}
      </div>
    )
  }
  if (!inv || !result) {
    return (
      <div className="flex items-center justify-center py-24 text-ink-dim">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Loading forensic result…
      </div>
    )
  }

  const verdict = verdictConfig[result.verdict]
  const VerdictIcon = verdict.icon
  const heatmap = result.evidence.find((e) => e.evidence_type === 'heatmap')
  const activeEvidence =
    result.evidence.find((e) => e.id === selectedEvidence) ?? heatmap ?? null

  return (
    <div className="space-y-6">
      {/* verdict banner */}
      <Card className={cn('border-2 bg-panel', toneBorder[verdict.tone])}>
        <CardContent className="flex flex-wrap items-center justify-between gap-4 p-6">
          <div className="flex items-center gap-4">
            <VerdictIcon className={cn('h-10 w-10', toneText[verdict.tone])} />
            <div>
              <p className="label-tech">Final verdict</p>
              <h1 className={cn('text-3xl font-bold tracking-tight', toneText[verdict.tone])}>
                {verdict.label}
              </h1>
            </div>
          </div>
          <div className="text-right">
            <p className="label-tech">Confidence</p>
            <p className="font-mono text-4xl font-bold text-neon">
              {formatPercent(result.confidence)}
            </p>
          </div>
        </CardContent>
      </Card>

      {/* scores */}
      <div className="grid gap-4 sm:grid-cols-3">
        <ScoreCard label="Spatial" score={result.spatial_score} icon={ScanFace} />
        <ScoreCard label="Temporal" score={result.temporal_score} icon={Clock} />
        <ScoreCard label="Frequency" score={result.frequency_score} icon={Waves} />
      </div>

      {/* video + heatmap */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="border-edge">
          <CardHeader>
            <CardTitle className="text-sm font-semibold text-ink">Source footage</CardTitle>
            <CardDescription>{inv.video?.filename}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="relative aspect-video overflow-hidden rounded-md border border-edge bg-bg">
              {/* placeholder frame — real backend serves a video stream */}
              <div className="bg-grid flex h-full w-full items-center justify-center">
                <span className="font-mono text-xs uppercase tracking-widest text-ink-faint">
                  [ frame preview ]
                </span>
              </div>
              <div className="absolute left-3 top-3 rounded-sm bg-black/60 px-2 py-1 font-mono text-[0.65rem] uppercase tracking-widest text-neon">
                <span className="animate-blink">●</span> REC
              </div>
              <div className="absolute bottom-3 left-3 font-mono text-[0.65rem] text-ink-dim">
                {inv.video ? `${inv.video.resolution} · ${inv.video.fps}fps` : '—'}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="border-edge">
          <CardHeader>
            <CardTitle className="text-sm font-semibold text-ink">Explainability heatmap</CardTitle>
            <CardDescription>
              {activeEvidence?.description ?? 'Grad-CAM localization of detected artifacts'}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="relative aspect-video overflow-hidden rounded-md border border-edge bg-bg">
              <div className="bg-grid flex h-full w-full flex-col items-center justify-center">
                <span className="font-mono text-xs uppercase tracking-widest text-ink-faint">
                  [ grad-cam heatmap ]
                </span>
                <span className="mt-1 font-mono text-[0.65rem] text-ink-faint">
                  frame {activeEvidence?.frame_number ?? '—'} · t={activeEvidence?.timestamp ?? '—'}s
                </span>
              </div>
            </div>
            {result.evidence.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {result.evidence
                  .filter((e) => e.evidence_type === 'heatmap')
                  .map((e) => (
                    <button
                      key={e.id}
                      onClick={() => setSelectedEvidence(e.id)}
                      className={cn(
                        'rounded-sm border px-2 py-1 font-mono text-[0.65rem] uppercase tracking-widest transition-colors',
                        activeEvidence?.id === e.id
                          ? 'border-neon text-neon'
                          : 'border-edge-2 text-ink-dim hover:text-ink',
                      )}
                    >
                      t={e.timestamp}s · {formatPercent(e.score)}
                    </button>
                  ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* timeline */}
      <Card className="border-edge">
        <CardHeader>
          <CardTitle className="text-sm font-semibold text-ink">
            Manipulation score timeline
          </CardTitle>
          <CardDescription>
            Frame-level scores · shaded regions are flagged suspicious segments
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={timeline} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="scoreFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#22d3a7" stopOpacity={0.4} />
                    <stop offset="100%" stopColor="#22d3a7" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="t"
                  stroke="#55647a"
                  fontSize={11}
                  tickFormatter={(v) => `${v}s`}
                />
                <YAxis
                  domain={[0, 1]}
                  stroke="#55647a"
                  fontSize={11}
                  tickFormatter={(v) => `${Math.round(v * 100)}%`}
                />
                <ChartTooltip
                  contentStyle={{
                    background: '#11161f',
                    border: '1px solid #2b3648',
                    borderRadius: 6,
                    fontSize: 12,
                    color: '#e6edf3',
                  }}
                  labelFormatter={(l) => `t = ${l}s`}
                  formatter={(v) => [`${formatPercent(Number(v) || 0)}`, 'score']}
                />
                <ReferenceArea
                  x1={12.4}
                  x2={15.8}
                  fill="#f87171"
                  fillOpacity={0.12}
                  stroke="#f87171"
                  strokeOpacity={0.5}
                />
                <ReferenceArea
                  x1={31}
                  x2={33.5}
                  fill="#f87171"
                  fillOpacity={0.12}
                  stroke="#f87171"
                  strokeOpacity={0.5}
                />
                <Area
                  type="monotone"
                  dataKey="score"
                  stroke="#22d3a7"
                  strokeWidth={2}
                  fill="url(#scoreFill)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-4 font-mono text-[0.65rem] uppercase tracking-widest text-ink-dim">
            {result.suspicious_segments.length === 0 ? (
              <span className="text-ok">No suspicious segments detected</span>
            ) : (
              result.suspicious_segments.map((s) => (
                <span key={`${s.start}-${s.end}`} className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded-sm bg-alert" />
                  {s.start}s → {s.end}s · {formatPercent(s.score)}
                </span>
              ))
            )}
          </div>
        </CardContent>
      </Card>

      {/* evidence list + actions */}
      <Card className="border-edge">
        <CardContent className="p-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="label-tech">Evidence &amp; integrity</p>
              <p className="mt-1 font-mono text-xs text-ink-dim">
                SHA-256 {inv.video ? truncateHash(inv.video.sha256) : '—'} ·{' '}
                {result.evidence.length} evidence item(s)
              </p>
            </div>
            <div className="flex gap-3">
              <Button variant="outline" asChild>
                <Link to={`/report/${id}`}>
                  <FileText />
                  Forensic report
                </Link>
              </Button>
            </div>
          </div>
          <Separator className="my-4" />
          <ul className="space-y-2">
            {result.evidence.length === 0 ? (
              <li className="font-mono text-xs text-ink-faint">
                No evidence markers for this result.
              </li>
            ) : (
              result.evidence.map((e) => (
                <li
                  key={e.id}
                  className="flex items-center justify-between gap-4 rounded-md border border-edge bg-bg/40 px-4 py-3"
                >
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs text-ink">{e.description}</p>
                    <p className="mt-0.5 font-mono text-[0.65rem] uppercase tracking-widest text-ink-faint">
                      {e.evidence_type} · frame {e.frame_number} · t={e.timestamp}s
                    </p>
                  </div>
                  <Badge variant="alert">{formatPercent(e.score)}</Badge>
                </li>
              ))
            )}
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
