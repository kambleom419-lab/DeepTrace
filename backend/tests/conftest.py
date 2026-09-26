"""Test fixtures.

Environment variables must be set BEFORE app.config is imported anywhere, because
Settings and the SQLAlchemy engine are created at import time.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TMP = Path(tempfile.mkdtemp(prefix="deeptrace-tests-"))

os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["LOCAL_STORAGE_ROOT"] = str(_TMP / "storage")
os.environ["QUEUE_BACKEND"] = "inline"
os.environ["ANALYSIS_MODE"] = "fake"
os.environ["REQUIRE_WEIGHTS"] = "false"
os.environ["JWT_SECRET"] = "test-secret-long-enough-for-hs256-signing"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

EMAIL = "tester@example.com"
PASSWORD = "correct-horse"


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth(client: TestClient) -> dict[str, str]:
    client.post("/api/auth/register", json={"email": EMAIL, "password": PASSWORD})
    res = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture
def sample_video() -> dict:
    return {"file": ("clip.mp4", b"\x00\x01\x02\x03" * 512, "video/mp4")}
