"""Queue and worker behaviour.

The suite runs with QUEUE_BACKEND=worker, so nothing claims a job unless a test asks for
it. That makes the claim and requeue assertions below deterministic instead of a race
against a background thread.
"""
from __future__ import annotations

from datetime import timedelta

from app import models, queue, worker
from app.db import SessionLocal


def _upload(client, auth, sample_video) -> str:
    return client.post("/api/investigations", files=sample_video, headers=auth).json()["id"]


def test_a_worker_processes_a_queued_job(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)

    assert worker.run_once("worker-1") is True

    body = client.get(f"/api/investigations/{inv_id}", headers=auth).json()
    assert body["status"] == "completed"


def test_claim_next_takes_a_job_exactly_once(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)

    db = SessionLocal()
    try:
        assert queue.claim_next(db, "worker-a") == inv_id
        # the row is no longer 'queued', so a second claim matches nothing
        assert queue.claim_next(db, "worker-a") is None
    finally:
        db.close()


def test_two_workers_never_claim_the_same_job(client, auth, sample_video):
    first = _upload(client, auth, sample_video)
    second = _upload(client, auth, sample_video)

    db = SessionLocal()
    try:
        a = queue.claim_next(db, "worker-a")
        b = queue.claim_next(db, "worker-b")
        assert a != b, "both workers were handed the same job"
        assert {a, b} == {first, second}
        assert queue.claim_next(db, "worker-c") is None
    finally:
        db.close()


def test_claiming_records_which_worker_took_the_job(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)

    db = SessionLocal()
    try:
        queue.claim_next(db, "worker-a")
    finally:
        db.close()

    db = SessionLocal()
    try:
        row = db.get(models.Investigation, inv_id)
        assert row.status == "processing"
        assert row.worker_id == "worker-a"
        assert row.claimed_at is not None
    finally:
        db.close()


def test_requeue_stale_recovers_a_job_from_a_dead_worker(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)

    db = SessionLocal()
    try:
        queue.claim_next(db, "worker-that-died")
        # pretend the claim has been sitting there for 90 minutes
        row = db.get(models.Investigation, inv_id)
        row.claimed_at = models.utcnow() - timedelta(minutes=90)
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        assert queue.requeue_stale(db, older_than_minutes=30) == 1
    finally:
        db.close()

    body = client.get(f"/api/investigations/{inv_id}", headers=auth).json()
    assert body["status"] == "queued", "the abandoned job went back on the queue"
    assert body["progress"] == {"stage": "ingest", "pct": 5}


def test_requeue_stale_leaves_a_live_claim_alone(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)

    db = SessionLocal()
    try:
        queue.claim_next(db, "worker-alive")
    finally:
        db.close()

    db = SessionLocal()
    try:
        assert queue.requeue_stale(db, older_than_minutes=30) == 0
    finally:
        db.close()

    body = client.get(f"/api/investigations/{inv_id}", headers=auth).json()
    assert body["status"] == "processing", "a healthy worker must keep its job"


def test_worker_reports_no_work_on_an_empty_queue(client, auth, sample_video):
    db = SessionLocal()
    try:
        while queue.claim_next(db, "drainer") is not None:
            pass
    finally:
        db.close()

    assert worker.run_once("worker-1") is False


def test_queue_depth_reports_pending_and_running_work(client, auth, sample_video):
    _upload(client, auth, sample_video)

    db = SessionLocal()
    try:
        assert queue.depth(db)["queued"] == 1
        queue.claim_next(db, "worker-a")
        after = queue.depth(db)
    finally:
        db.close()

    assert after["queued"] == 0
    assert after["processing"] >= 1
