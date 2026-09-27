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
os.environ.setdefault("QUEUE_BACKEND", "database")  # set to azure or sqs to test that queue
# Forced, not defaulted: SQS long polling would block every empty receive for its full
# duration, and the drain fixture below receives once per test, so the suite would spend
# twenty seconds per test waiting for nothing.
os.environ["QUEUE_WAIT_SECONDS"] = "0"
# The other two windows normally stop a worker being handed a live job, and stop reconcile
# re-sending a message that is merely still in flight. At 0 they mean "recover everything",
# which is what the drain fixture needs - see its docstring.
os.environ["QUEUE_RECONCILE_MINUTES"] = "0"
os.environ["STALE_CLAIM_MINUTES"] = "0"
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
def drain_queue(client):
    """Leave no queued rows behind, so tests never inherit each other's work.

    Runs the real worker function rather than a thread, which keeps the suite
    single-threaded and therefore deterministic.

    Depends on `client` on purpose: that is what runs the app's lifespan and therefore
    `init_db()`. Without the dependency, running a single file that does not otherwise
    touch the app - `pytest tests/test_storage.py` - fails here in teardown with
    "no such table: investigations" instead of passing.

    Sweeps before receiving, and that order is load-bearing. test_worker.py claims and
    requeues rows by calling queue.claim_next and queue.requeue_stale directly, which
    bypasses the transport and leaves a pending row whose message was consumed long ago.
    No receive can ever find that row, so it would survive into the next test and make
    claim_next hand back the wrong job. The database transport hid this for the whole
    project, because for it "drain the queue" and "find every queued row" are the same
    operation. sweep() re-sends a message for anything still pending - production's own
    recovery path - which makes this correct for all three transports rather than one.
    """
    yield
    worker.sweep()
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
