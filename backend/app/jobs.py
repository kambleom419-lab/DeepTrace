"""The analysis job.

Phase 1 runs this via FastAPI BackgroundTasks. Phase 2 moves the same function into a
worker process fed by a queue, with no change to its body.
"""
from __future__ import annotations

import base64
import logging
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import delete

from app import mlbridge, models
from app.config import get_settings
from app.db import SessionLocal
from app.models import utcnow
from app.stages import pct_for
from app.storage import get_storage

logger = logging.getLogger(__name__)

# 1x1 JPEG, only used if OpenCV is unavailable when generating fake-mode placeholders.
_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


def run_analysis_job(investigation_id: str) -> None:
    """Analyse one investigation and persist the result. Never raises."""
    settings = get_settings()
    db = SessionLocal()
    work_dir: Path | None = None

    try:
        inv = db.get(models.Investigation, investigation_id)
        if inv is None:
            logger.warning("job for unknown investigation %s", investigation_id)
            return
        if inv.video is None:
            raise RuntimeError("investigation has no video row")

        inv.status = "processing"
        inv.stage = "frames"
        inv.pct = pct_for("frames")
        db.commit()

        def on_stage(stage: str) -> None:
            # called by the ML pipeline at its real boundaries
            inv.stage = stage
            inv.pct = pct_for(stage)
            db.commit()

        work_dir = Path(tempfile.mkdtemp(prefix=f"deeptrace-{investigation_id}-"))
        out_dir = work_dir / "out"
        out_dir.mkdir(parents=True, exist_ok=True)

        if settings.analysis_mode == "fake":
            payload = _fake_payload(inv, out_dir)
        else:
            video_path = work_dir / inv.video.filename
            video_path.write_bytes(get_storage().get_bytes(inv.video.storage_key))
            payload = mlbridge.analyze_video(video_path, out_dir, on_stage=on_stage)

        _store_heatmaps(inv, payload, out_dir)
        _persist(db, inv, payload)

        inv.status = "completed"
        inv.stage = "done"
        inv.pct = pct_for("done")
        inv.completed_at = utcnow()
        db.commit()
        logger.info("analysis complete: %s", investigation_id)

    except Exception as exc:  # noqa: BLE001 - a failed job must never crash the caller
        logger.exception("analysis failed: %s", investigation_id)
        db.rollback()
        inv = db.get(models.Investigation, investigation_id)
        if inv is not None:
            inv.status = "failed"
            inv.failure_reason = str(exc)[:1000]
            db.commit()
    finally:
        db.close()
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)


def _store_heatmaps(inv: models.Investigation, payload: dict, out_dir: Path) -> None:
    """Upload each evidence image and remember its storage key.

    The pipeline sets `heatmap_url` to a bare filename; we translate that into a real
    storage object here and the API layer turns it into a URL.
    """
    storage = get_storage()
    for item in payload.get("evidence", []):
        name = Path(str(item.get("heatmap_url") or "")).name
        src = out_dir / name
        if not name or not src.exists():
            item["_storage_key"] = None
            continue
        key = f"heatmaps/{inv.id}/{name}"
        storage.put_file(key, src)
        item["_storage_key"] = key


def _persist(db, inv: models.Investigation, payload: dict) -> None:
    """Write the result rows, replacing any previous run (analysis is idempotent)."""
    meta = payload.pop("_meta", {}) or {}

    db.execute(
        delete(models.Evidence).where(models.Evidence.investigation_id == inv.id)
    )
    db.execute(
        delete(models.AnalysisResultRow).where(
            models.AnalysisResultRow.investigation_id == inv.id
        )
    )
    db.expire(inv, ["evidence", "result"])
    db.flush()

    db.add(
        models.AnalysisResultRow(
            investigation_id=inv.id,
            verdict=str(payload["verdict"]),
            confidence=float(payload["confidence"]),
            spatial_score=float(payload["spatial_score"]),
            temporal_score=float(payload["temporal_score"]),
            frequency_score=float(payload["frequency_score"]),
            frame_scores=[float(v) for v in payload.get("frame_scores", [])],
            suspicious_segments=list(payload.get("suspicious_segments", [])),
            meta={
                k: meta.get(k)
                for k in ("filename", "timing_s", "frames_sampled")
                if k in meta
            },
        )
    )

    for item in payload.get("evidence", []):
        db.add(
            models.Evidence(
                id=str(item["id"]),
                investigation_id=inv.id,
                timestamp=float(item.get("timestamp", 0.0)),
                frame_number=int(item.get("frame_number", 0)),
                evidence_type=str(item.get("evidence_type", "heatmap")),
                score=float(item.get("score", 0.0)),
                description=str(item.get("description", "")),
                storage_key=item.get("_storage_key"),
            )
        )
    db.flush()


def _fake_payload(inv: models.Investigation, out_dir: Path) -> dict:
    """Canned result in the exact ML output shape.

    Used by the test suite and for fast UI iteration, where a 20-second CPU analysis per
    upload would make development painful. It deliberately mirrors the real payload -
    including a bare `heatmap_url` filename - so every downstream code path is exercised.
    """
    specs = [(12.9, 310, 0.96, "Grad-CAM: strong activation around jawline blending boundary"),
             (31.7, 762, 0.88, "Grad-CAM: temporal inconsistency at eye region")]
    evidence = []
    for idx, (ts, frame, score, note) in enumerate(specs, start=1):
        name = f"heatmap_f{frame:03d}.jpg"
        _write_placeholder(out_dir / name)
        evidence.append(
            {
                "id": f"ev-{idx:03d}",
                "timestamp": ts,
                "frame_number": frame,
                "evidence_type": "heatmap",
                "score": score,
                "heatmap_url": name,
                "description": note,
            }
        )

    return {
        "verdict": "LIKELY_MANIPULATED",
        "confidence": 0.947,
        "spatial_score": 0.93,
        "temporal_score": 0.87,
        "frequency_score": 0.76,
        "suspicious_segments": [
            {"start": 12.4, "end": 15.8, "score": 0.96},
            {"start": 31.0, "end": 33.5, "score": 0.88},
        ],
        "frame_scores": [0.12, 0.14, 0.31, 0.88, 0.96, 0.71, 0.42, 0.46],
        "evidence": evidence,
        "_meta": {
            "filename": inv.video.filename if inv.video else "unknown",
            "frames_sampled": 8,
            "timing_s": 0.05,
        },
    }


def _write_placeholder(path: Path) -> None:
    """A real, viewable JPEG so the Results page renders in fake mode."""
    try:
        import cv2
        import numpy as np

        img = np.full((224, 224, 3), 34, dtype=np.uint8)
        cv2.rectangle(img, (74, 84), (168, 168), (70, 70, 205), -1)
        cv2.putText(img, "fake mode", (14, 212), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (225, 225, 225), 1)
        cv2.imwrite(str(path), img)
        return
    except Exception:  # noqa: BLE001 - placeholder only, never worth failing a job
        pass
    path.write_bytes(_TINY_JPEG)
