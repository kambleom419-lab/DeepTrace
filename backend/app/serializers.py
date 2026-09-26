"""Convert database rows into the exact shapes the frontend expects."""
from __future__ import annotations

from datetime import datetime, timezone

from app import models, schemas


def iso(value: datetime | None) -> str | None:
    """ISO-8601 with an explicit UTC marker.

    SQLite hands back naive datetimes, and `new Date("2026-09-25T10:15:03")` in the
    browser would be read as *local* time. Attaching UTC keeps timestamps honest.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def evidence_url(investigation_id: str, evidence_id: str) -> str:
    return f"/api/investigations/{investigation_id}/evidence/{evidence_id}"


def investigation_to_schema(inv: models.Investigation) -> schemas.Investigation:
    video = None
    if inv.video is not None:
        video = schemas.VideoMeta(
            filename=inv.video.filename,
            duration=inv.video.duration,
            fps=inv.video.fps,
            resolution=inv.video.resolution,
            size=inv.video.size,
            sha256=inv.video.sha256,
        )

    result = None
    if inv.status == "completed" and inv.result is not None:
        result = schemas.AnalysisResult(
            verdict=inv.result.verdict,
            confidence=inv.result.confidence,
            spatial_score=inv.result.spatial_score,
            temporal_score=inv.result.temporal_score,
            frequency_score=inv.result.frequency_score,
            suspicious_segments=[
                schemas.SuspiciousSegment(**seg) for seg in (inv.result.suspicious_segments or [])
            ],
            frame_scores=[float(v) for v in (inv.result.frame_scores or [])],
            evidence=[
                schemas.EvidenceItem(
                    id=ev.id,
                    timestamp=ev.timestamp,
                    frame_number=ev.frame_number,
                    evidence_type=ev.evidence_type,
                    score=ev.score,
                    # the ML pipeline emits a bare filename; the API exposes a URL
                    heatmap_url=evidence_url(inv.id, ev.id) if ev.storage_key else None,
                    description=ev.description,
                )
                for ev in sorted(inv.evidence, key=lambda e: e.id)
            ],
        )

    return schemas.Investigation(
        id=inv.id,
        title=inv.title,
        status=inv.status,
        progress=schemas.Progress(stage=inv.stage, pct=inv.pct),
        video=video,
        result=result,
        created_at=iso(inv.created_at) or "",
        completed_at=iso(inv.completed_at),
    )
