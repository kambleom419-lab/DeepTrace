// Mirrors the frozen API contract (see plan §3).

export type Verdict = 'LIKELY_MANIPULATED' | 'LIKELY_AUTHENTIC' | 'INCONCLUSIVE'

export type InvestigationStatus =
  | 'queued'
  | 'processing'
  | 'completed'
  | 'failed'

export type AnalysisStage =
  | 'ingest'
  | 'frames'
  | 'faces'
  | 'spatial'
  | 'temporal'
  | 'frequency'
  | 'fusion'
  | 'done'

export interface Progress {
  stage: AnalysisStage
  pct: number
}

export interface SuspiciousSegment {
  start: number
  end: number
  score: number
}

export interface EvidenceItem {
  id: string
  timestamp: number
  frame_number: number
  evidence_type: 'heatmap' | 'crop' | 'spectrogram' | 'text'
  score: number
  heatmap_url?: string
  description: string
}

export interface AnalysisResult {
  verdict: Verdict
  confidence: number
  spatial_score: number
  temporal_score: number
  frequency_score: number
  suspicious_segments: SuspiciousSegment[]
  frame_scores: number[]
  evidence: EvidenceItem[]
}

export interface VideoMeta {
  filename: string
  duration: number
  fps: number
  resolution: string
  size: number
  sha256: string
}

export interface Investigation {
  id: string
  title: string
  status: InvestigationStatus
  progress?: Progress
  video?: VideoMeta
  result?: AnalysisResult
  created_at: string
  completed_at?: string
}

export interface AuthResponse {
  access_token: string
  token_type: 'bearer'
  user: { id: string; email: string }
}

export interface LoginRequest {
  email: string
  password: string
}

export type RegisterRequest = LoginRequest
