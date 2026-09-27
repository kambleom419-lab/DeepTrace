"""The queue transport, exercised against whichever backend is configured.

Runs on the database transport by default and on the Azure Storage Queue transport when
QUEUE_BACKEND=azure. Both must satisfy the same behaviour, which is what lets the queue be
swapped without touching the API or the worker:

    pytest tests                                  # database transport
    QUEUE_BACKEND=azure ... pytest tests          # Azure Storage Queue (Azurite locally)

The interesting case is duplicates. Azure Storage Queue guarantees at-least-once delivery,
so the same job can be handed to two workers; the compare-and-swap on the row is what stops
it being analysed twice.
"""
from __future__ import annotations

from app import models
from app import queue as queue_mod
from app.db import SessionLocal
from app.queue import get_transport


def _upload(client, auth, sample_video) -> str:
    return client.post("/api/investigations", files=sample_video, headers=auth).json()["id"]


def test_the_api_enqueues_and_a_worker_receives(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)

    transport = get_transport()
    db = SessionLocal()
    try:
        job = transport.receive(db, "worker-a")
        assert job is not None, "the upload did not produce a message"
        assert job.investigation_id == inv_id
        job.ack()
    finally:
        db.close()


def test_a_job_is_claimed_exactly_once(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)
    transport = get_transport()

    db = SessionLocal()
    try:
        first = transport.receive(db, "worker-a")
        assert first is not None
        first.ack()

        # the row is now 'processing', so a second worker finds nothing to do
        assert transport.receive(db, "worker-b") is None
    finally:
        db.close()


def test_claim_job_is_a_compare_and_swap(client, auth, sample_video):
    """The guard that makes at-least-once delivery safe."""
    inv_id = _upload(client, auth, sample_video)

    db = SessionLocal()
    try:
        assert queue_mod.claim_job(db, inv_id, "worker-a") is True
        assert queue_mod.claim_job(db, inv_id, "worker-b") is False, "claimed twice"
    finally:
        db.close()


def test_a_redelivered_job_is_discarded(client, auth, sample_video):
    """A duplicate delivery must not be analysed again."""
    inv_id = _upload(client, auth, sample_video)
    transport = get_transport()

    db = SessionLocal()
    try:
        first = transport.receive(db, "worker-a")
        assert first is not None
        first.ack()

        # force the same job back onto the transport, as a retry or redelivery would
        transport.enqueue(inv_id)
        assert transport.receive(db, "worker-b") is None
    finally:
        db.close()


def test_reconcile_recovers_a_job_whose_message_was_lost(client, auth, sample_video):
    """The API can die between committing the row and sending the message.

    reconcile() re-sends anything that has been queued long enough, so the job still runs.
    """
    inv_id = _upload(client, auth, sample_video)

    # simulate the lost message by claiming (which consumes it) and putting the row back
    db = SessionLocal()
    try:
        job = get_transport().receive(db, "worker-a")
        assert job is not None
        job.ack()

        row = db.get(models.Investigation, inv_id)
        row.status = "queued"
        row.claimed_at = None
        row.worker_id = None
        # pretend it has been sitting there longer than the reconcile window
        row.created_at = row.created_at.replace(year=row.created_at.year - 1)
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        get_transport().reconcile(db)
    finally:
        db.close()

    db = SessionLocal()
    try:
        recovered = get_transport().receive(db, "worker-b")
        assert recovered is not None and recovered.investigation_id == inv_id
        recovered.ack()
    finally:
        db.close()
