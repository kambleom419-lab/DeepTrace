"""Test fixtures.

Environment variables must be set BEFORE app.config is imported anywhere, because
Settings and the SQLAlchemy engine are created at import time.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TMP = Path(tempfile.mkdtemp(prefix="deeptrace-tests-"))

# Behaviour is forced, so the suite stays fast and hermetic no matter what is in .env.
# The backing services are only defaults, so passing DATABASE_URL / STORAGE_BACKEND in the
# environment runs this exact suite against PostgreSQL and Azurite instead of SQLite and
# local files.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(_TMP / 'test.db').as_posix()}")
os.environ.setdefault("LOCAL_STORAGE_ROOT", str(_TMP / "storage"))
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("QUEUE_BACKEND", "database")  # set to azure to test the queue service
os.environ["WORKER_IN_PROCESS"] = "false"  # tests drive worker.run_once() themselves
os.environ["ANALYSIS_MODE"] = "fake"
os.environ["REQUIRE_WEIGHTS"] = "false"
os.environ["JWT_SECRET"] = "test-secret-long-enough-for-hs256-signing"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import worker  # noqa: E402
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


@pytest.fixture(autouse=True)
def drain_queue():
    """Leave no queued rows behind, so tests never inherit each other's work.

    Runs the real worker function rather than a thread, which keeps the suite
    single-threaded and therefore deterministic.
    """
    yield
    while worker.run_once("test-drainer"):
        pass


@pytest.fixture
def wait_for_terminal(client: TestClient):
    """Drive the worker until an investigation reaches a terminal state.

    Tests call the same app.worker.run_once the container runs, so this exercises the
    real claim-then-process path instead of poking at the database directly.
    """

    def _wait(inv_id: str, headers: dict[str, str], timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        body: dict = {}
        while time.time() < deadline:
            worker.run_once("test-worker")
            body = client.get(f"/api/investigations/{inv_id}", headers=headers).json()
            if body["status"] in ("completed", "failed"):
                return body
            time.sleep(0.02)
        raise AssertionError(f"investigation never finished: {body}")

    return _wait
