"""Backend settings.

Environment-driven so the same image runs locally and in the cloud with no code change.
Field names map to upper-case env vars (database_url -> DATABASE_URL).
"""
from __future__ import annotations

import os
import socket
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./deeptrace.db"

    storage_backend: str = "local"  # local | azure | s3
    local_storage_root: str = str(REPO_ROOT / "backend" / "_storage")
    storage_connection: str = ""
    # Blob container name: lowercase letters, digits and hyphens only.
    storage_container: str = "deeptrace"

    # S3 (AWS in the cloud, Moto locally).
    s3_bucket: str = "deeptrace"
    s3_region: str = "ap-south-1"
    # Empty means real AWS. The local emulator needs its own URL, which also switches
    # boto3 to path-style addressing.
    s3_endpoint_url: str = ""
    # Empty in AWS: the ECS task role supplies credentials, so there are no keys to leak.
    # The local emulator has no roles, so it needs them set.
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # SQS (AWS in the cloud, Moto locally). Each field falls back to its S3 counterpart:
    # Moto serves S3 and SQS on one endpoint, and in AWS both live in one region, so the
    # local .env only has to name the emulator once.
    # Empty queue_url means "look the queue up by name, and create it if it is absent",
    # which is what makes local development zero-config.
    sqs_queue_url: str = ""
    sqs_region: str = ""
    sqs_endpoint_url: str = ""
    sqs_access_key: str = ""
    sqs_secret_key: str = ""

    # How a job gets from the API to a worker.
    #   database - the row with status='queued' *is* the message
    #   azure    - an Azure Storage Queue message, with the row as the source of truth
    #   sqs      - an AWS SQS message, likewise
    queue_backend: str = "database"
    queue_name: str = "jobs"
    # Falls back to storage_connection: one storage account holds blobs and queues.
    queue_connection: str = ""
    # How long a received message stays invisible while a worker analyses it. Must exceed
    # the slowest analysis, or a second worker will pick the job up mid-flight.
    queue_visibility_seconds: int = 900
    # Re-enqueue jobs that have been sitting in 'queued' this long. Covers a message lost
    # between committing the row and sending it.
    queue_reconcile_minutes: int = 5
    # SQS only, and the reason it is a setting: an empty receive blocks server-side for
    # this long, which cuts an idle worker from 60 requests a minute to 3. That is the
    # difference between exceeding the SQS free tier and sitting comfortably inside it.
    # The tests set 0, so draining an empty queue does not stall for 20 s every time.
    queue_wait_seconds: int = 20

    # Whether THIS process also runs the worker loop. True makes `uvicorn app.main:app` a
    # complete system, which is the local development setup. Containers set it false and run
    # `python -m app.worker` as its own service, so inference scales independently of HTTP.
    worker_in_process: bool = True

    # How often an idle worker looks for work, and how often it sweeps for jobs abandoned
    # by a worker that died mid-analysis.
    worker_poll_seconds: float = 1.0
    worker_sweep_seconds: float = 60.0
    stale_claim_minutes: int = 30
    # Blank means "hostname-pid", which is enough to tell workers apart in the logs.
    worker_id: str = ""

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

    @property
    def resolved_worker_id(self) -> str:
        return self.worker_id or f"{socket.gethostname()}-{os.getpid()}"

    @property
    def resolved_queue_connection(self) -> str:
        return self.queue_connection or self.storage_connection

    # The four SQS fallbacks. They exist so the local .env names the emulator endpoint once
    # instead of twice, since Moto serves S3 and SQS from the same port.
    @property
    def resolved_sqs_region(self) -> str:
        return self.sqs_region or self.s3_region

    @property
    def resolved_sqs_endpoint_url(self) -> str:
        return self.sqs_endpoint_url or self.s3_endpoint_url

    @property
    def resolved_sqs_access_key(self) -> str:
        return self.sqs_access_key or self.s3_access_key

    @property
    def resolved_sqs_secret_key(self) -> str:
        return self.sqs_secret_key or self.s3_secret_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
