"""End-to-end flow: upload -> queued -> worker claims it -> completed -> evidence served."""
from __future__ import annotations

import time

from app import jobs, models, worker
from app.db import SessionLocal


def _upload(client, auth, sample_video) -> str:
    res = client.post("/api/investigations", files=sample_video, headers=auth)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_analysis_completes_and_advances_through_the_stages(client, auth, sample_video,
                                                            wait_for_terminal):
    created = client.post("/api/investigations", files=sample_video, headers=auth).json()
    assert created["status"] == "queued"

    final = wait_for_terminal(created["id"], auth)

    assert final["status"] == "completed", final.get("failure_reason")
    assert final["progress"] == {"stage": "done", "pct": 100}
    assert final["id"] == created["id"]
    assert final["video"]["sha256"] == created["video"]["sha256"]


def test_evidence_images_are_served_as_jpeg(client, auth, sample_video, wait_for_terminal):
    inv_id = _upload(client, auth, sample_video)
    final = wait_for_terminal(inv_id, auth)

    evidence = final["result"]["evidence"]
    assert evidence, "expected at least one evidence item"

    for item in evidence:
        assert item["heatmap_url"] == f"/api/investigations/{inv_id}/evidence/{item['id']}"
        img = client.get(item["heatmap_url"], headers=auth)
        assert img.status_code == 200
        assert img.headers["content-type"] == "image/jpeg"
        assert img.content[:2] == b"\xff\xd8", "not a JPEG"


def test_unknown_evidence_id_is_404(client, auth, sample_video, wait_for_terminal):
    inv_id = _upload(client, auth, sample_video)
    wait_for_terminal(inv_id, auth)
    res = client.get(f"/api/investigations/{inv_id}/evidence/ev-999", headers=auth)
    assert res.status_code == 404


def test_investigation_appears_in_the_list_after_upload(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)
    listed = client.get("/api/investigations", headers=auth).json()
    assert inv_id in [i["id"] for i in listed]
    assert listed[0]["id"] == inv_id, "newest investigation should be first"


def test_two_uploads_get_distinct_ids(client, auth, sample_video):
    first = client.post("/api/investigations", files=sample_video, headers=auth).json()
    second = client.post("/api/investigations", files=sample_video, headers=auth).json()
    assert first["id"] != second["id"]


def test_api_queues_the_job_but_does_not_run_it(client, auth, sample_video):
    """The point of the worker split: the API never analyses anything itself.

    QUEUE_BACKEND=worker here, so no worker thread is running in the API process. An
    upload must therefore sit untouched until some worker asks for it.
    """
    inv_id = _upload(client, auth, sample_video)
    time.sleep(0.3)  # give a hypothetical in-process worker every chance to fire

    body = client.get(f"/api/investigations/{inv_id}", headers=auth).json()
    assert body["status"] == "queued", "the API must not process jobs itself"
    assert body["progress"] == {"stage": "ingest", "pct": 5}
    assert "result" not in body

    # ...and only a worker moves it
    assert worker.run_once("test-worker") is True
    assert client.get(f"/api/investigations/{inv_id}", headers=auth).json()["status"] == "completed"


def test_a_raising_job_ends_failed_not_stuck(client, auth, sample_video,
                                            wait_for_terminal, monkeypatch):
    """A job that raises must record the failure, not leave the row on 'processing'.

    The real pipeline raises LookupError('No face detected') on faceless input. Faking
    that here keeps the test fast and independent of torch.
    """
    inv_id = _upload(client, auth, sample_video)
    wait_for_terminal(inv_id, auth)  # complete it normally first

    def boom(*_args, **_kwargs):
        raise RuntimeError("No face detected in any sampled frame")

    monkeypatch.setattr(jobs.mlbridge, "analyze_video", boom)
    monkeypatch.setattr(jobs.get_settings(), "analysis_mode", "real")

    jobs.run_analysis_job(inv_id)  # must not raise

    body = client.get(f"/api/investigations/{inv_id}", headers=auth).json()
    assert body["status"] == "failed"
    assert "result" not in body

    # the reason is kept for diagnosis even though it is not part of the API contract
    db = SessionLocal()
    try:
        row = db.get(models.Investigation, inv_id)
        assert "No face detected" in (row.failure_reason or "")
    finally:
        db.close()
