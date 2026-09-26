"""Pydantic schemas — a 1:1 mirror of frontend/src/types/index.ts.

Field names and enum values must match that file exactly; the frontend treats it as a
frozen contract.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["LIKELY_MANIPULATED", "LIKELY_AUTHENTIC", "INCONCLUSIVE"]
InvestigationStatus = Literal["queued", "processing", "completed", "failed"]
AnalysisStage = Literal[
    "ingest", "frames", "faces", "spatial", "temporal", "frequency", "fusion", "done"
]
EvidenceType = Literal["heatmap", "crop", "spectrogram", "text"]


class Progress(BaseModel):
    stage: AnalysisStage
    pct: int = Field(ge=0, le=100)


class SuspiciousSegment(BaseModel):
    start: float
    end: float
    score: float


class EvidenceItem(BaseModel):
    id: str
    timestamp: float
    frame_number: int
    evidence_type: EvidenceType
    score: float
    heatmap_url: str | None = None
    description: str


class AnalysisResult(BaseModel):
    verdict: Verdict
    confidence: float
    spatial_score: float
    temporal_score: float
    frequency_score: float
    suspicious_segments: list[SuspiciousSegment]
    frame_scores: list[float]
    evidence: list[EvidenceItem]


class VideoMeta(BaseModel):
    filename: str
    duration: float
    fps: float
    resolution: str
    size: int
    sha256: str


class Investigation(BaseModel):
    id: str
    title: str
    status: InvestigationStatus
    progress: Progress | None = None
    video: VideoMeta | None = None
    result: AnalysisResult | None = None
    created_at: str
    completed_at: str | None = None


class UserOut(BaseModel):
    id: str
    email: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserOut


class LoginRequest(BaseModel):
    email: str
    password: str


RegisterRequest = LoginRequest
