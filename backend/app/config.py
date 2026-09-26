"""Backend settings.

Environment-driven so the same image runs locally and in Azure with no code change.
Field names map to upper-case env vars (database_url -> DATABASE_URL).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./deeptrace.db"

    storage_backend: str = "local"  # local | azure
    local_storage_root: str = str(REPO_ROOT / "backend" / "_storage")
    storage_connection: str = ""

    queue_backend: str = "inline"  # inline | azure
    queue_name: str = "jobs"

    jwt_secret: str = "dev-secret-change-me"
    jwt_expire_hours: int = 12

    max_upload_mb: int = 200

    # "fake" returns a canned result without touching torch - used by the test suite
    # and for fast UI iteration when a 20 s CPU analysis is not wanted.
    analysis_mode: str = "real"

    # Refuse to start if the trained checkpoints are missing. Without them the pipeline
    # silently substitutes _PRIORS and returns meaningless verdicts.
    require_weights: bool = True

    ml_package_root: str = str(REPO_ROOT / "ml")
    ffmpeg_bin: str = ""

    @property
    def weights_dir(self) -> Path:
        return Path(self.ml_package_root) / "weights"

    @property
    def checkpoint_names(self) -> tuple[str, ...]:
        return (
            "spatial_xception.pt",
            "temporal_gru.pt",
            "frequency_mlp.pt",
            "fusion_mlp.pt",
        )

    def missing_checkpoints(self) -> list[str]:
        return [n for n in self.checkpoint_names if not (self.weights_dir / n).exists()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
