import type { AnalysisStage } from '@/types'

// The analysis pipeline's stage order, mirroring the backend's app/stages.py.
// Kept out of mocks/ so production code never imports the mock layer.
export const STAGES: readonly AnalysisStage[] = [
  'ingest',
  'frames',
  'faces',
  'spatial',
  'temporal',
  'frequency',
  'fusion',
  'done',
]
