"""End-to-end flow: upload -> queued -> processed -> completed -> evidence served."""
from __future__ import annotations

import time


def _wait_for_terminal(client, auth, inv_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        body = client.get(f"/api/investigations/{inv_id}", headers=auth).json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError(f"investigation never finished: {body}")


def test_analysis_completes_and_advances_through_the_stages(client, auth, sample_video):
    created = client.post("/api/investigations", files=sample_video, headers=auth).json()
    assert created["status"] == "queued"

    final = _wait_for_terminal(client, auth, created["id"])

    assert final["status"] == "completed", final.get("failure_reason")
    assert final["progress"] == {"stage": "done", "pct": 100}
    assert final["id"] == created["id"]
    assert final["video"]["sha256"] == created["video"]["sha256"]


def test_evidence_images_are_served_as_jpeg(client, auth, sample_video):
    inv_id = client.post("/api/investigations", files=sample_video,
                         headers=auth).json()["id"]
    final = _wait_for_terminal(client, auth, inv_id)

    evidence = final["result"]["evidence"]
    assert evidence, "expected at least one evidence item"

    for item in evidence:
        assert item["heatmap_url"] == f"/api/investigations/{inv_id}/evidence/{item['id']}"
        img = client.get(item["heatmap_url"], headers=auth)
        assert img.status_code == 200
        assert img.headers["content-type"] == "image/jpeg"
        assert img.content[:2] == b"\xff\xd8", "not a JPEG"


def test_unknown_evidence_id_is_404(client, auth, sample_video):
    inv_id = client.post("/api/investigations", files=sample_video,
                         headers=auth).json()["id"]
    _wait_for_terminal(client, auth, inv_id)
    res = client.get(f"/api/investigations/{inv_id}/evidence/ev-999", headers=auth)
    assert res.status_code == 404


def test_investigation_appears_in_the_list_after_upload(client, auth, sample_video):
    inv_id = client.post("/api/investigations", files=sample_video,
                         headers=auth).json()["id"]
    listed = client.get("/api/investigations", headers=auth).json()
    assert inv_id in [i["id"] for i in listed]
    assert listed[0]["id"] == inv_id, "newest investigation should be first"


def test_two_uploads_get_distinct_ids(client, auth, sample_video):
    first = client.post("/api/investigations", files=sample_video, headers=auth).json()
    second = client.post("/api/investigations", files=sample_video, headers=auth).json()
    assert first["id"] != second["id"]


def test_faceless_video_ends_failed_not_stuck(client, auth):
    """A real pipeline raises 'No face detected'; the job must surface that as `failed`.

    In fake mode we can't produce that condition, so this asserts the failure *contract*
    by pointing the job at a video row whose storage object is missing.
    """
    from app.db import SessionLocal
    from app.jobs import run_analysis_job
    from app import models

    inv_id = client.post("/api/investigations",
                         files={"file": ("ghost.mp4", b"x" * 128, "video/mp4")},
                         headers=auth).json()["id"]

    db = SessionLocal()
    try:
        video = db.get(models.Video, inv_id)
        video.storage_key = "videos/does-not-exist.mp4"
        db.commit()
    finally:
        db.close()

    # re-run the job directly; it must record the failure rather than raise
    from app.config import get_settings
    original = get_settings().analysis_mode
    get_settings().analysis_mode = "real"
    try:
        run_analysis_job(inv_id)
    finally:
        get_settings().analysis_mode = original

    body = client.get(f"/api/investigations/{inv_id}", headers=auth).json()
    assert body["status"] == "failed"
    assert "result" not in body
