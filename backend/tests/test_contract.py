"""Contract tests: the shapes the frontend depends on.

These assert the promise made in frontend/src/types/index.ts, so a backend change that
would break the UI fails here instead of in the browser.
"""
from __future__ import annotations

from app.stages import STAGES

ANALYSIS_RESULT_KEYS = {
    "verdict",
    "confidence",
    "spatial_score",
    "temporal_score",
    "frequency_score",
    "suspicious_segments",
    "frame_scores",
    "evidence",
}
INVESTIGATION_KEYS = {
    "id",
    "title",
    "status",
    "progress",
    "video",
    "result",
    "created_at",
    "completed_at",
}


def test_register_returns_201_and_bearer_token(client):
    res = client.post("/api/auth/register",
                      json={"email": "new@example.com", "password": "pw12345"})
    assert res.status_code == 201
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "new@example.com"
    assert set(body) == {"access_token", "token_type", "user"}


def test_register_without_email_is_422_with_detail(client):
    res = client.post("/api/auth/register", json={"email": "", "password": "pw"})
    assert res.status_code == 422
    assert res.json()["detail"] == "Email is required"


def test_register_duplicate_email_is_rejected(client):
    payload = {"email": "dupe@example.com", "password": "pw"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    res = client.post("/api/auth/register", json=payload)
    assert res.status_code == 409
    assert "detail" in res.json()


def test_login_success_and_failure(client):
    client.post("/api/auth/register", json={"email": "login@example.com", "password": "pw"})
    ok = client.post("/api/auth/login", json={"email": "login@example.com", "password": "pw"})
    assert ok.status_code == 200
    assert ok.json()["user"]["email"] == "login@example.com"

    bad = client.post("/api/auth/login",
                      json={"email": "login@example.com", "password": "wrong"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Invalid credentials"


def test_unknown_email_login_is_401_not_404(client):
    res = client.post("/api/auth/login",
                      json={"email": "nobody@example.com", "password": "pw"})
    assert res.status_code == 401


def test_investigations_require_auth(client):
    assert client.get("/api/investigations").status_code == 401
    assert client.get("/api/investigations/INV-9999").status_code == 401


def test_invalid_token_is_401(client):
    res = client.get("/api/investigations", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401


def test_list_starts_as_an_array(client, auth):
    res = client.get("/api/investigations", headers=auth)
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_upload_returns_201_queued_and_no_result(client, auth, sample_video):
    res = client.post("/api/investigations", files=sample_video, headers=auth)
    assert res.status_code == 201, res.text
    body = res.json()

    assert set(body) <= INVESTIGATION_KEYS
    assert body["id"].startswith("INV-")
    assert body["status"] == "queued"
    assert "result" not in body, "a queued job must not carry a result"
    assert body["progress"]["stage"] == "ingest"
    assert isinstance(body["progress"]["pct"], int)

    video = body["video"]
    assert set(video) == {"filename", "duration", "fps", "resolution", "size", "sha256"}
    assert video["filename"] == "clip.mp4"
    assert video["size"] == 2048
    assert len(video["sha256"]) == 64


def test_upload_defaults_the_title_to_the_filename(client, auth, sample_video):
    res = client.post("/api/investigations", files=sample_video, headers=auth)
    assert res.json()["title"] == "clip.mp4"


def test_upload_accepts_an_explicit_title(client, auth, sample_video):
    res = client.post("/api/investigations", files=sample_video, headers=auth,
                      data={"title": "Evidence clip A"})
    assert res.json()["title"] == "Evidence clip A"


def test_upload_rejects_an_empty_file(client, auth):
    res = client.post("/api/investigations",
                      files={"file": ("empty.mp4", b"", "video/mp4")}, headers=auth)
    assert res.status_code == 422
    assert res.json()["detail"] == "Uploaded file is empty"


def test_unknown_investigation_is_404_with_detail(client, auth):
    res = client.get("/api/investigations/INV-9999", headers=auth)
    assert res.status_code == 404
    assert res.json()["detail"] == "Not found"


def test_progress_stage_is_always_a_known_stage(client, auth, sample_video):
    inv_id = client.post("/api/investigations", files=sample_video,
                         headers=auth).json()["id"]
    body = client.get(f"/api/investigations/{inv_id}", headers=auth).json()
    assert body["progress"]["stage"] in STAGES
    assert 0 <= body["progress"]["pct"] <= 100


def test_completed_result_matches_the_typescript_interface(client, auth, sample_video):
    inv_id = client.post("/api/investigations", files=sample_video,
                         headers=auth).json()["id"]
    body = client.get(f"/api/investigations/{inv_id}", headers=auth).json()

    assert body["status"] == "completed"
    assert "completed_at" in body
    result = body["result"]

    assert set(result) == ANALYSIS_RESULT_KEYS, "_meta or other keys leaked into the result"
    assert result["verdict"] in {"LIKELY_MANIPULATED", "LIKELY_AUTHENTIC", "INCONCLUSIVE"}
    assert 0.0 <= result["confidence"] <= 1.0
    for seg in result["suspicious_segments"]:
        assert set(seg) == {"start", "end", "score"}
    for item in result["evidence"]:
        assert set(item) <= {"id", "timestamp", "frame_number", "evidence_type",
                             "score", "heatmap_url", "description"}
        assert item["evidence_type"] in {"heatmap", "crop", "spectrogram", "text"}
