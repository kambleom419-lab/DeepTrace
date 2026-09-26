"""ORM models.

Mirrors the frontend contract: everything the UI reads that the ML pipeline does not
produce (ids, status, progress, video metadata) lives here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(16), default="ingest")
    pct: Mapped[int] = mapped_column(Integer, default=5)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    video: Mapped[Video | None] = relationship(
        back_populates="investigation", uselist=False, cascade="all, delete-orphan"
    )
    result: Mapped[AnalysisResultRow | None] = relationship(
        back_populates="investigation", uselist=False, cascade="all, delete-orphan"
    )
    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )


class Video(Base):
    __tablename__ = "videos"

    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id"), primary_key=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    resolution: Mapped[str] = mapped_column(String(32), default="")
    storage_key: Mapped[str] = mapped_column(String(1024))

    investigation: Mapped[Investigation] = relationship(back_populates="video")


class AnalysisResultRow(Base):
    __tablename__ = "analysis_results"

    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id"), primary_key=True
    )
    verdict: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    spatial_score: Mapped[float] = mapped_column(Float)
    temporal_score: Mapped[float] = mapped_column(Float)
    frequency_score: Mapped[float] = mapped_column(Float)
    frame_scores: Mapped[list] = mapped_column(JSON, default=list)
    suspicious_segments: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    investigation: Mapped[Investigation] = relationship(back_populates="result")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id"), primary_key=True, index=True
    )
    timestamp: Mapped[float] = mapped_column(Float)
    frame_number: Mapped[int] = mapped_column(Integer)
    evidence_type: Mapped[str] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text, default="")
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    investigation: Mapped[Investigation] = relationship(back_populates="evidence")
