"""The job queue: how a job gets from the API process to a worker process.

Three transports, chosen by QUEUE_BACKEND:

| Value      | Message carrier                | Works offline via |
|------------|--------------------------------|-------------------|
| `database` | the investigation row itself   | —                 |
| `azure`    | an Azure Storage Queue message | Azurite           |
| `sqs`      | an AWS SQS message             | Moto              |

The database is the **source of truth** in all three cases. The queue only decides how a
worker finds out there is work, which is what makes them interchangeable - and what makes
losing a message survivable rather than fatal.

Two properties of a real queue drive the design:

* **At-least-once delivery.** A message can be handed to a worker twice. Claiming a job is
  therefore a compare-and-swap on the row's status: the second delivery finds the row
  already taken and is discarded.
* **Messages can go missing.** A crash between committing the row and sending the message
  leaves a job nobody will ever hear about, so `reconcile()` re-sends anything stuck in
  `queued` for a while. Duplicate messages are harmless for the reason above.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.stages import STAGE_PCT, STAGES

logger = logging.getLogger(__name__)

FIRST_STAGE = STAGES[0]


@dataclass(frozen=True)
class Job:
    """A claimed job. `ack()` tells the transport it is finished."""

    investigation_id: str
    _ack: Callable[[], None] | None = None

    def ack(self) -> None:
        if self._ack is not None:
            self._ack()


# ── job state, shared by both transports ──────────────────────────────────────


def claim_job(db: Session, investigation_id: str, worker_id: str) -> bool:
    """Compare-and-swap one specific job. False if it is not (or no longer) queued."""
    result = db.execute(
        update(models.Investigation)
        .where(
            models.Investigation.id == investigation_id,
            models.Investigation.status == "queued",
        )
        .values(
            status="processing",
            stage=FIRST_STAGE,
            pct=STAGE_PCT[FIRST_STAGE],
            claimed_at=models.utcnow(),
            worker_id=worker_id,
            failure_reason=None,
        )
    )
    db.commit()
    return result.rowcount == 1


def claim_next(db: Session, worker_id: str) -> str | None:
    """Take the oldest queued job. Returns its id, or None if there is nothing to do."""
    candidate = db.scalar(
        select(models.Investigation.id)
        .where(models.Investigation.status == "queued")
        .order_by(models.Investigation.created_at)
        .limit(1)
    )
    if candidate is None:
        return None
    return candidate if claim_job(db, candidate, worker_id) else None


def requeue_stale(db: Session, older_than_minutes: int) -> int:
    """Return jobs abandoned by a dead worker to the queue.

    Without this a worker killed mid-analysis leaves its row stuck on 'processing' forever
    and the UI polls a job that will never finish. Re-running is safe: analysis replaces
    any previous result rather than appending to it.
    """
    cutoff = models.utcnow() - timedelta(minutes=older_than_minutes)
    result = db.execute(
        update(models.Investigation)
        .where(
            models.Investigation.status == "processing",
            models.Investigation.claimed_at.is_not(None),
            models.Investigation.claimed_at < cutoff,
        )
        .values(
            status="queued",
            stage=FIRST_STAGE,
            pct=STAGE_PCT[FIRST_STAGE],
            claimed_at=None,
            worker_id=None,
        )
    )
    db.commit()

    count = result.rowcount or 0
    if count:
        logger.warning(
            "requeued %d job(s) abandoned by a dead worker (>%d min)", count, older_than_minutes
        )
    return count


def queued_ids_older_than(db: Session, minutes: int) -> list[str]:
    """Jobs that have been waiting long enough that their message was probably lost."""
    cutoff = models.utcnow() - timedelta(minutes=minutes)
    return list(
        db.scalars(
            select(models.Investigation.id).where(
                models.Investigation.status == "queued",
                models.Investigation.created_at < cutoff,
            )
        )
    )


def depth(db: Session) -> dict[str, int]:
    """Queue depth, surfaced by /api/health."""

    def count_for(status: str) -> int:
        return db.scalar(
            select(func.count())
            .select_from(models.Investigation)
            .where(models.Investigation.status == status)
        ) or 0

    return {"queued": count_for("queued"), "processing": count_for("processing")}


# ── transports ────────────────────────────────────────────────────────────────


class Transport(Protocol):
    name: str

    def enqueue(self, investigation_id: str) -> None: ...

    def receive(self, db: Session, worker_id: str) -> Job | None: ...

    def reconcile(self, db: Session) -> int: ...


class DatabaseTransport:
    """No message to send: the queued row *is* the message."""

    name = "database"

    def enqueue(self, investigation_id: str) -> None:
        return None

    def receive(self, db: Session, worker_id: str) -> Job | None:
        investigation_id = claim_next(db, worker_id)
        return Job(investigation_id) if investigation_id else None

    def reconcile(self, db: Session) -> int:
        return requeue_stale(db, get_settings().stale_claim_minutes)


class MessageQueueTransport:
    """Behaviour shared by every transport that carries an explicit message.

    Azure Queues and SQS differ only in how a message is sent, received and deleted.
    Everything else is a property of *any* at-least-once queue - recovering jobs abandoned
    by a dead worker, re-sending messages that went missing, dropping duplicate deliveries -
    so it is written once here instead of twice. The line count is the smaller reason: this
    is also what stops the two transports drifting apart as one of them gets fixed.
    """

    name = ""

    def enqueue(self, investigation_id: str) -> None:
        raise NotImplementedError

    def _delete(self, message) -> None:
        raise NotImplementedError

    def reconcile(self, db: Session) -> int:
        recovered = requeue_stale(db, get_settings().stale_claim_minutes)

        # A message is lost if the API died between committing the row and sending it, so
        # re-send anything that has been queued long enough to have been delivered by now.
        # Duplicates are harmless - the claim compares and swaps.
        for investigation_id in queued_ids_older_than(db, get_settings().queue_reconcile_minutes):
            logger.warning("re-enqueueing %s (its message never arrived)", investigation_id)
            self.enqueue(investigation_id)

        return recovered

    def _discard_duplicate(self, worker_id: str, investigation_id: str, message) -> None:
        """Drop a message the database says is not ours: a duplicate, or a stale leftover.

        Deleting it rather than letting the visibility timeout run out keeps the queue depth
        honest - otherwise /api/health would report work that no worker will ever do.
        """
        logger.info("[%s] discarding duplicate message for %s", worker_id, investigation_id)
        self._delete(message)


class AzureQueueTransport(MessageQueueTransport):
    """Azure Storage Queue. Locally this is the Azurite queue service."""

    name = "azure"

    def __init__(self, connection_string: str, queue_name: str) -> None:
        from azure.storage.queue import QueueClient

        self.queue_name = queue_name
        self._client = QueueClient.from_connection_string(connection_string, queue_name)
        self._ready = False

    def _ensure_queue(self) -> None:
        if self._ready:
            return

        from azure.core.exceptions import ResourceExistsError

        try:
            self._client.create_queue()
        except ResourceExistsError:
            pass
        self._ready = True

    def enqueue(self, investigation_id: str) -> None:
        self._ensure_queue()
        self._client.send_message(investigation_id)

    def receive(self, db: Session, worker_id: str) -> Job | None:
        self._ensure_queue()
        visibility = get_settings().queue_visibility_seconds

        for message in self._client.receive_messages(
            messages_per_page=1, visibility_timeout=visibility
        ):
            investigation_id = (message.content or "").strip()

            if claim_job(db, investigation_id, worker_id):
                return Job(investigation_id, _ack=lambda: self._delete(message))

            self._discard_duplicate(worker_id, investigation_id, message)

        return None

    def _delete(self, message) -> None:
        try:
            self._client.delete_message(message)
        except Exception:  # noqa: BLE001 - the visibility timeout will expire it anyway
            logger.exception("could not delete queue message %s", getattr(message, "id", "?"))


class SqsQueueTransport(MessageQueueTransport):
    """AWS SQS. Locally this is the Moto emulator, on the same endpoint as S3.

    SQS standard queues are explicitly at-least-once, which is precisely the guarantee the
    claim compare-and-swap was written for: a redelivered message finds the row already
    taken and is discarded. Nothing here depends on a message arriving only once, because
    SQS makes no such promise.
    """

    name = "sqs"

    def __init__(
        self,
        queue_name: str,
        region: str,
        queue_url: str = "",
        endpoint_url: str = "",
        access_key: str = "",
        secret_key: str = "",
    ) -> None:
        import boto3

        kwargs: dict = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key

        self.queue_name = queue_name
        self._client = boto3.client("sqs", **kwargs)
        # In AWS the deployment creates the queue and passes its URL in. Locally it is
        # discovered on first use, and created if this is the very first run.
        self._queue_url = queue_url

    def _ensure_queue(self) -> str:
        if self._queue_url:
            return self._queue_url

        from botocore.exceptions import ClientError

        try:
            self._queue_url = self._client.get_queue_url(QueueName=self.queue_name)["QueueUrl"]
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in ("AWS.SimpleQueueService.NonExistentQueue", "QueueDoesNotExist"):
                raise
            self._queue_url = self._client.create_queue(QueueName=self.queue_name)["QueueUrl"]

        return self._queue_url

    def enqueue(self, investigation_id: str) -> None:
        self._client.send_message(QueueUrl=self._ensure_queue(), MessageBody=investigation_id)

    def receive(self, db: Session, worker_id: str) -> Job | None:
        settings = get_settings()

        # WaitTimeSeconds is long polling: an empty queue blocks here rather than returning
        # an empty response, which is what keeps an idle worker inside the free tier. It
        # also means shutdown waits up to this long for the call to return, so it is kept
        # shorter than the container's stop timeout.
        response = self._client.receive_message(
            QueueUrl=self._ensure_queue(),
            MaxNumberOfMessages=1,
            VisibilityTimeout=settings.queue_visibility_seconds,
            WaitTimeSeconds=settings.queue_wait_seconds,
        )

        for message in response.get("Messages", []):
            investigation_id = (message.get("Body") or "").strip()

            if claim_job(db, investigation_id, worker_id):
                return Job(investigation_id, _ack=lambda m=message: self._delete(m))

            self._discard_duplicate(worker_id, investigation_id, message)

        return None

    def _delete(self, message) -> None:
        try:
            self._client.delete_message(
                QueueUrl=self._ensure_queue(), ReceiptHandle=message["ReceiptHandle"]
            )
        except Exception:  # noqa: BLE001 - the visibility timeout will expire it anyway
            logger.exception("could not delete queue message %s", message.get("MessageId", "?"))


@lru_cache
def get_transport() -> Transport:
    settings = get_settings()

    if settings.queue_backend == "database":
        return DatabaseTransport()

    if settings.queue_backend == "azure":
        if not settings.resolved_queue_connection:
            raise RuntimeError(
                "QUEUE_BACKEND=azure needs QUEUE_CONNECTION or STORAGE_CONNECTION"
            )
        return AzureQueueTransport(settings.resolved_queue_connection, settings.queue_name)

    if settings.queue_backend == "sqs":
        return SqsQueueTransport(
            queue_name=settings.queue_name,
            region=settings.resolved_sqs_region,
            queue_url=settings.sqs_queue_url,
            endpoint_url=settings.resolved_sqs_endpoint_url,
            access_key=settings.resolved_sqs_access_key,
            secret_key=settings.resolved_sqs_secret_key,
        )

    raise ValueError(
        f"unknown QUEUE_BACKEND={settings.queue_backend!r} (database | azure | sqs)"
    )
