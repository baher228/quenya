import argparse
import logging
import os
import signal
import socket
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from threading import Event, Thread
from typing import Any

from app import db, jobs
from app.observability import init_sentry

logger = logging.getLogger(__name__)
_stop = Event()


def _run_job(job: dict[str, Any]) -> None:
    started = time.monotonic()
    heartbeat_stop = Event()

    def heartbeat_loop() -> None:
        while not heartbeat_stop.wait(30):
            try:
                if not jobs.heartbeat(job["id"]):
                    return
            except Exception:
                logger.exception("Job heartbeat failed id=%s", job["id"])

    heartbeat_thread = Thread(
        target=heartbeat_loop,
        name=f"job-heartbeat-{job['id'][:8]}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        jobs.run_job(job)
    except Exception:
        logger.exception("Job runner failed id=%s", job["id"])
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join()
        try:
            latest = jobs.get(job["id"], job.get("owner_id"))
            status = latest["status"] if latest else "missing"
        except Exception:
            logger.exception("Unable to read final job status id=%s", job["id"])
            status = "unknown"
        logger.info(
            "job id=%s kind=%s latency_s=%.3f status=%s",
            job["id"],
            job["kind"],
            time.monotonic() - started,
            status,
        )


def _handle_signal(signum, _frame) -> None:
    logger.info("Received signal %s; stopping job claims", signum)
    _stop.set()


def run_worker(concurrency: int) -> None:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    _stop.clear()
    init_sentry("worker")
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    db.init_db()

    worker_id = (
        f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    pending: set[Future] = set()
    next_requeue = 0.0
    with ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="quenya-job",
    ) as executor:
        while not _stop.is_set() or pending:
            done, pending = wait(
                pending,
                timeout=0,
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                future.result()
            if _stop.is_set():
                if pending:
                    wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                continue
            if time.monotonic() >= next_requeue:
                try:
                    jobs.requeue_stale()
                except Exception:
                    logger.exception("Unable to requeue stale jobs")
                next_requeue = time.monotonic() + 60
            while len(pending) < concurrency and not _stop.is_set():
                try:
                    job = jobs.claim_next(["outfits"], worker_id)
                except Exception:
                    logger.exception("Unable to claim next job")
                    break
                if job is None:
                    break
                pending.add(executor.submit(_run_job, job))
            if not pending:
                _stop.wait(1)
            else:
                wait(pending, timeout=1, return_when=FIRST_COMPLETED)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Quenya background jobs")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.getenv("QUENYA_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_worker(args.concurrency)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
