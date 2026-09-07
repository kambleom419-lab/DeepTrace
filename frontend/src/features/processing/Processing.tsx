import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Check, Loader2, XCircle } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { AnalysisStage, Investigation } from '@/types'
import { STAGES } from '@/mocks/fixtures'

const stageLabels: Record<AnalysisStage, string> = {
  ingest: 'Video ingestion',
  frames: 'Frame extraction',
  faces: 'Face detection',
  spatial: 'Spatial analysis',
  temporal: 'Temporal analysis',
  frequency: 'Frequency analysis',
  fusion: 'Feature fusion',
  done: 'Complete',
}

export default function Processing() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [inv, setInv] = useState<Investigation | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    let cancelled = false

    const poll = async () => {
      try {
        const data = await api.getInvestigation(id)
        if (cancelled) return
        setInv(data)
        if (data.status === 'completed') {
          navigate(`/results/${id}`, { replace: true })
        } else if (data.status === 'failed') {
          setError('Analysis failed. Please try again.')
        } else {
          setTimeout(poll, 1800)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to fetch status')
        }
      }
    }

    poll()
    return () => {
      cancelled = true
    }
  }, [id, navigate])

  const currentIndex = inv?.progress
    ? STAGES.indexOf(inv.progress.stage)
    : -1

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <span className="label-tech">Job {inv?.id ?? id}</span>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-ink">
          Processing Analysis
        </h1>
        <p className="mt-1 text-sm text-ink-dim">
          {inv?.title ?? 'Submitting video to analysis pipeline…'}
        </p>
      </div>

      <Card className="border-edge-2">
        <CardContent className="p-6">
          {error ? (
            <div className="flex flex-col items-center gap-3 py-12 text-center">
              <XCircle className="h-10 w-10 text-alert" />
              <p className="font-mono text-sm text-alert">ANALYSIS_FAILED</p>
              <p className="text-xs text-ink-dim">{error}</p>
            </div>
          ) : (
            <div className="space-y-6">
              {/* pipeline stages */}
              <ol className="space-y-3">
                {STAGES.map((stage, i) => {
                  const isDone = currentIndex > i || stage === 'done'
                  const isActive = currentIndex === i && stage !== 'done'
                  return (
                    <li
                      key={stage}
                      className={cn(
                        'flex items-center gap-3 rounded-md border px-4 py-3 font-mono text-xs uppercase tracking-widest transition-colors',
                        isDone
                          ? 'border-neon/30 bg-neon/5 text-neon'
                          : isActive
                            ? 'border-cyan/40 bg-cyan/5 text-cyan'
                            : 'border-edge bg-bg/30 text-ink-faint',
                      )}
                    >
                      <span className="flex h-5 w-5 items-center justify-center">
                        {isDone ? (
                          <Check className="h-4 w-4" />
                        ) : isActive ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <span className="h-1.5 w-1.5 rounded-full bg-current opacity-50" />
                        )}
                      </span>
                      {stageLabels[stage]}
                      {isActive && (
                        <span className="ml-auto animate-blink text-[0.6rem] text-cyan">
                          RUNNING
                        </span>
                      )}
                    </li>
                  )
                })}
              </ol>

              {/* progress bar */}
              <div className="space-y-2">
                <div className="flex justify-between font-mono text-[0.65rem] uppercase tracking-widest text-ink-dim">
                  <span>Overall progress</span>
                  <span>{inv?.progress?.pct ?? 0}%</span>
                </div>
                <Progress value={inv?.progress?.pct ?? 0} />
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <p className="text-center font-mono text-[0.65rem] uppercase tracking-widest text-ink-faint">
        Pipeline: FFmpeg → RetinaFace → ConvNeXt → GRU → FFT/MLP → Fusion
      </p>
    </div>
  )
}
