"""Investigation endpoints - the core of the API."""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import mlbridge, models, schemas
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.jobs import run_analysis_job
from app.serializers import investigation_to_schema
from app.stages import STAGE_PCT
from app.storage import get_storage

logger = logging.getLogger(__name__)
router = APIRouter()

FIRST_STAGE = "ingest"


def _next_investigation_id(db: Session) -> str:
    """Sequential INV-0001 style ids, matching the format the frontend was built with."""
    last = db.scalar(select(func.max(models.Investigation.id)))
    if not last:
        return "INV-0001"
    try:
        return "INV-%04d" % (int(last.split("-")[1]) + 1)
    except (IndexError, ValueError):
        return "INV-%04d" % (len(db.scalars(select(models.Investigation.id)).all()) + 1)


def _owned(db: Session, investigation_id: str, user: models.User) -> models.Investigation:
    inv = db.get(models.Investigation, investigation_id)
    if inv is None or inv.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return inv


@router.get("", response_model=list[schemas.Investigation],
            response_model_exclude_none=True)
def list_investigations(
    user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[schemas.Investigation]:
    rows = db.scalars(
        select(models.Investigation)
        .where(models.Investigation.user_id == user.id)
        .order_by(models.Investigation.created_at.desc())
    ).all()
    return [investigation_to_schema(r) for r in rows]


@router.get("/{investigation_id}", response_model=schemas.Investigation,
            response_model_exclude_none=True)
def get_investigation(
    investigation_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.Investigation:
    return investigation_to_schema(_owned(db, investigation_id, user))


@router.get("/{investigation_id}/evidence/{evidence_id}")
def get_evidence(
    investigation_id: str,
    evidence_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    _owned(db, investigation_id, user)
    ev = db.scalar(
        select(models.Evidence).where(
            models.Evidence.investigation_id == investigation_id,
            models.Evidence.id == evidence_id,
        )
    )
    if ev is None or not ev.storage_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        data = get_storage().get_bytes(ev.storage_key)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(content=data, media_type="image/jpeg")


@router.post("", response_model=schemas.Investigation, status_code=status.HTTP_201_CREATED,
             response_model_exclude_none=True)
async def create_investigation(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.Investigation:
    """Accept an upload, store it, and queue the analysis.

    Returns immediately: nothing is analysed yet, so there is deliberately no `result`.
    """
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024

    tmp_dir = Path(tempfile.mkdtemp(prefix="deeptrace-upload-"))
    try:
        safe_name = Path(file.filename or "upload.bin").name
        tmp_path = tmp_dir / safe_name

        size = 0
        with tmp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds {settings.max_upload_mb} MB limit",
                    )
                out.write(chunk)

        if size == 0:
            raise HTTPException(status_code=422, detail="Uploaded file is empty")

        sha256, measured = mlbridge.sha256_and_size(tmp_path)
        if settings.analysis_mode == "real":
            probed = mlbridge.probe_video(tmp_path)
        else:
            # fake mode exists so the API can be exercised without the ML stack at all
            probed = {"duration": 0.0, "fps": 0.0, "resolution": "unknown"}

        # id generation can collide if two uploads race; retry a few times
        inv = None
        for _ in range(5):
            candidate = _next_investigation_id(db)
            inv = models.Investigation(
                id=candidate,
                user_id=user.id,
                title=(title or safe_name)[:512],
                status="queued",
                stage=FIRST_STAGE,
                pct=STAGE_PCT[FIRST_STAGE],
            )
            db.add(inv)
            try:
                db.flush()
                break
            except IntegrityError:
                db.rollback()
                inv = None
        if inv is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail="Could not allocate an investigation id")

        key = f"videos/{inv.id}/{safe_name}"
        get_storage().put_file(key, tmp_path)

        db.add(
            models.Video(
                investigation_id=inv.id,
                filename=safe_name,
                size=measured,
                sha256=sha256,
                duration=probed["duration"],
                fps=probed["fps"],
                resolution=probed["resolution"],
                storage_key=key,
            )
        )
        db.commit()
        db.refresh(inv)

        logger.info("queued %s (%s, %d bytes)", inv.id, safe_name, measured)
        background.add_task(run_analysis_job, inv.id)
        return investigation_to_schema(inv)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
