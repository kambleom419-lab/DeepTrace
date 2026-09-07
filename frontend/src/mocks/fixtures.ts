import type {
  AnalysisResult,
  Investigation,
  VideoMeta,
} from '@/types'

export const mockVideoMeta: VideoMeta = {
  filename: 'press-conference_swapped.mp4',
  duration: 48.6,
  fps: 30,
  resolution: '1920x1080',
  size: 23_400_000,
  sha256: '9f2c4a1b8e7d3f6a5c0b9e8d7f6a5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d',
}

export const mockResult: AnalysisResult = {
  verdict: 'LIKELY_MANIPULATED',
  confidence: 0.947,
  spatial_score: 0.93,
  temporal_score: 0.87,
  frequency_score: 0.76,
  suspicious_segments: [
    { start: 12.4, end: 15.8, score: 0.96 },
    { start: 31.0, end: 33.5, score: 0.88 },
  ],
  frame_scores: [
    0.12, 0.14, 0.11, 0.18, 0.16, 0.21, 0.19, 0.24, 0.28, 0.22,
    0.31, 0.35, 0.38, 0.42, 0.51, 0.48, 0.55, 0.61, 0.58, 0.63,
    0.72, 0.78, 0.81, 0.85, 0.88, 0.9, 0.93, 0.94, 0.96, 0.95,
    0.92, 0.9, 0.88, 0.91, 0.89, 0.86, 0.82, 0.79, 0.75, 0.71,
    0.68, 0.64, 0.6, 0.57, 0.53, 0.5, 0.46, 0.42,
  ],
  evidence: [
    {
      id: 'ev-001',
      timestamp: 12.9,
      frame_number: 310,
      evidence_type: 'heatmap',
      score: 0.96,
      heatmap_url: '/mock/heatmap-1.jpg',
      description: 'Grad-CAM: strong activation around jawline and mouth blending boundary',
    },
    {
      id: 'ev-002',
      timestamp: 31.7,
      frame_number: 762,
      evidence_type: 'heatmap',
      score: 0.88,
      heatmap_url: '/mock/heatmap-2.jpg',
      description: 'Grad-CAM: temporal inconsistency at eye region — iris shape changes',
    },
    {
      id: 'ev-003',
      timestamp: 14.2,
      frame_number: 355,
      evidence_type: 'crop',
      score: 0.82,
      description: 'Aligned face crop showing interpolation artifacts at the hairline',
    },
  ],
}

export const mockInvestigations: Investigation[] = [
  {
    id: 'INV-0003',
    title: 'press-conference_swapped.mp4',
    status: 'completed',
    video: mockVideoMeta,
    result: mockResult,
    created_at: '2026-08-31T14:22:00Z',
    completed_at: '2026-08-31T14:27:31Z',
  },
  {
    id: 'INV-0002',
    title: 'interview_authentic.mp4',
    status: 'completed',
    video: {
      ...mockVideoMeta,
      filename: 'interview_authentic.mp4',
      duration: 32.1,
      sha256: '1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091',
    },
    result: {
      ...mockResult,
      verdict: 'LIKELY_AUTHENTIC',
      confidence: 0.91,
      spatial_score: 0.14,
      temporal_score: 0.09,
      frequency_score: 0.18,
      suspicious_segments: [],
      frame_scores: [0.1, 0.08, 0.12, 0.09, 0.11, 0.07, 0.13, 0.1],
      evidence: [],
    },
    created_at: '2026-08-29T10:05:00Z',
    completed_at: '2026-08-29T10:09:40Z',
  },
  {
    id: 'INV-0001',
    title: 'suspect_footage_clip.mov',
    status: 'failed',
    video: { ...mockVideoMeta, filename: 'suspect_footage_clip.mov', size: 91_200_000 },
    created_at: '2026-08-27T18:44:00Z',
  },
]

// stages in order — used by the mock polling to advance progress
export const STAGES = [
  'ingest',
  'frames',
  'faces',
  'spatial',
  'temporal',
  'frequency',
  'fusion',
  'done',
] as const
