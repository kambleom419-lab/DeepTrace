"""The analysis worker.

    python -m app.worker            # poll forever
    python -m app.worker --once     # drain the queue and exit (used by the tests)

It runs the same app.jobs.run_analysis_job the API used to run inline; the only difference
is which process it lives in. QUEUE_BACKEND=inline starts this loop on a daemon thread
inside the API process, which keeps `uvicorn app.main:app` a one-command dev setup. In a
container the worker becomes its own Container App, so inference scales independently of
the API that serves HTTP.
"""
from __future__ import annotations

import argparse
import logging
import signal
import threading
import time

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.jobs import run_analysis_job
from app.queue import get_transport

logger = logging.getLogger(__name__)


def run_once(worker_id: str) -> bool:
    """Claim and run a single job. True if a job was processed."""
    transport = get_transport()
    db = SessionLocal()
    try:
        job = transport.receive(db, worker_id)
    finally:
        db.close()

    if job is None:
        return False

    logger.info("[%s] claimed %s", worker_id, job.investigation_id)
    try:
        run_analysis_job(job.investigation_id)
    finally:
        # Tell the transport the job is done either way. run_analysis_job records its own
        # terminal state, and a message left behind would only be redelivered and then
        # discarded as a duplicate - but it would also look like work in the queue.
        job.ack()
    return True


def sweep() -> None:
    """Recover abandoned jobs and re-send any message that went missing."""
    transport = get_transport()
    db = SessionLocal()
    try:
        transport.reconcile(db)
    finally:
        db.close()


def run_forever(stop: threading.Event, worker_id: str) -> None:
    settings = get_settings()
    init_db()
    sweep()

    logger.info(
        "[%s] worker up; queue=%s, polling every %.1fs, sweeping every %.0fs",
        worker_id, get_transport().name,
        settings.worker_poll_seconds, settings.worker_sweep_seconds,
    )
    last_sweep = time.monotonic()

    while not stop.is_set():
        if run_once(worker_id):
            continue  # keep draining while there is work; no point sleeping

        stop.wait(settings.worker_poll_seconds)

        if time.monotonic() - last_sweep >= settings.worker_sweep_seconds:
            last_sweep = time.monotonic()
            sweep()

    logger.info("[%s] worker stopped", worker_id)


def start_background_worker() -> threading.Event:
    """Run the worker loop on a daemon thread inside this process.

    Returns the event that stops it. Daemon=True means it dies with the API, which is
    exactly right for the local single-process mode.
    """
    stop = threading.Event()
    worker_id = f"{get_settings().resolved_worker_id}-inline"
    threading.Thread(
        target=run_forever,
        args=(stop, worker_id),
        name="deeptrace-inline-worker",
        daemon=True,
    ).start()
    return stop


def main() -> int:
    parser = argparse.ArgumentParser(description="DeepTrace analysis worker")
    parser.add_argument("--once", action="store_true", help="drain the queue and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    worker_id = get_settings().resolved_worker_id

    if args.once:
        init_db()
        processed = 0
        while run_once(worker_id):
            processed += 1
        logger.info("[%s] drained %d job(s)", worker_id, processed)
        return 0

    stop = threading.Event()

    def handle_signal(signum, _frame):
        # Container Apps and Ctrl-C both send SIGTERM/SIGINT; finish the job in flight
        # rather than leaving a row stuck on 'processing'.
        logger.info("[%s] signal %s received; exiting after the current job", worker_id, signum)
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    run_forever(stop, worker_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
