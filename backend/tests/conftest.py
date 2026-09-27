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
os.environ["WORKER_IN_PROCESS"] = "false"  # tests drive worker.run_once() themselves
os.environ["ANALYSIS_MODE"] = "fake"
os.environ["REQUIRE_WEIGHTS"] = "false"
os.environ["JWT_SECRET"] = "test-secret-long-enough-for-hs256-signing"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app import models, worker  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

EMAIL = "tester@example.com"
PASSWORD = "correct-horse"

# How many messages the drain below is willing to consume. It is a fixed count rather than a
# "keep going while there is work" loop on purpose: receive() returns None both for an empty
# queue and for a duplicate it has just discarded, so such a loop cannot tell "finished" from
# "skipped that one" and would exit with messages still queued - which is the bug that
# produced this comment. Every test clears the queue, so the leftovers never exceed what one
# test can enqueue, which is two uploads; 25 is deliberate headroom. Each call is an HTTP
# round trip to the emulator, so a much larger number would be paid for on every test.
DRAIN_MESSAGES = 25


def _delete_pending_rows() -> None:
    """Delete rows a test left mid-flight, along with their children.

    test_worker.py deliberately claims and requeues jobs by calling queue.claim_next and
    queue.requeue_stale directly, which bypasses the transport: the row goes back to
    'queued' while its message was consumed long ago, so no receive can ever find it again.
    Left alone it leaks into the next test, and claim_next then hands back the wrong job.

    That is exactly how the SQS transport first failed here, after the database transport
    had hidden the problem for the whole project - for that transport "drain the queue" and
    "find every queued row" are the same operation, so the divergence could never show.

    Children go first because the foreign keys are not declared ON DELETE CASCADE, so this
    has to stay correct on PostgreSQL as well as on SQLite.
    """
    db = SessionLocal()
    try:
        pending = select(models.Investigation.id).where(
            models.Investigation.status.in_(("queued", "processing"))
        )
        for child in (models.Evidence, models.AnalysisResultRow, models.Video):
            db.execute(delete(child).where(child.investigation_id.in_(pending)))
        db.execute(
            delete(models.Investigation).where(
                models.Investigation.status.in_(("queued", "processing"))
            )
        )
        db.commit()
    finally:
        db.close()


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

    Two steps, because either one alone leaves something behind. test_worker.py claims and
    requeues rows by calling queue.claim_next and queue.requeue_stale directly, which
    bypasses the transport and leaves a pending row whose message was consumed long ago.
    No receive can ever find that row, so it would survive into the next test and make
    claim_next hand back the wrong job.
    """
    yield
    # Consume whatever the transport still holds. Bounded rather than "while there is work",
    # for the reason given on DRAIN_MESSAGES.
    for _ in range(DRAIN_MESSAGES):
        worker.run_once("test-drainer")
    # Then clear rows the loop could not reach: the ones whose message was consumed by a
    # test calling claim_next directly. See _delete_pending_rows.
    _delete_pending_rows()


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
