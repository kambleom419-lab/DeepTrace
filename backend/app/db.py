"""Database engine and session factory."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # background jobs run on a different thread than the request
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


_settings = get_settings()
engine = create_engine(_settings.database_url, **_engine_kwargs(_settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401  (register mappers before create_all)

    Base.metadata.create_all(bind=engine)
