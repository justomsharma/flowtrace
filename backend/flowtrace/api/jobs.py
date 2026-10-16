"""Background jobs with a replayable event log, streamed to the UI over Server-Sent Events."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

TERMINAL = ("done", "error", "cancelled")


class Cancelled(Exception):
    """Raised inside a job's work at its next event once the job was cancelled."""


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"            # queued | running | done | error | cancelled
    run: str | None = None             # the upload run this job belongs to (case jobs)
    cancelled: bool = False
    events: list[dict] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    started: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, event: dict) -> None:
        if self.cancelled:
            raise Cancelled()
        self._append(event)

    def _append(self, event: dict) -> None:
        with self.lock:
            self.events.append({"seq": len(self.events), "t": round(time.time() - self.started, 3), **event})


JOBS: dict[str, Job] = {}


def create(kind: str, status: str = "running", run: str | None = None) -> Job:
    job = Job(id=f"{kind}-{uuid.uuid4().hex[:8]}", kind=kind, status=status, run=run)
    JOBS[job.id] = job
    return job


def cancel(job: Job) -> None:
    """Stop a job: a queued one never starts, a running one stops at its next event and its result is dropped."""
    if job.status in TERMINAL:
        return
    job.cancelled = True
    if job.status == "queued":
        _finish_cancelled(job)


def _finish_cancelled(job: Job) -> None:
    if job.status != "cancelled":
        job.status = "cancelled"
        job._append({"type": "cancelled"})


def execute(job: Job, fn: Callable[[Callable[[dict], None]], Any], on_done: Callable[[Any], bool | None] | None = None) -> None:
    """Run a job in the current thread. on_done may return False to reject a stale result (job ends cancelled)."""
    if job.cancelled:
        _finish_cancelled(job)
        return
    job.status, job.started = "running", time.time()
    try:
        job.result = fn(job.emit)
        if job.cancelled or (on_done and on_done(job.result) is False):
            _finish_cancelled(job)
            return
        job.status = "done"
        job._append({"type": "done"})
    except Cancelled:
        _finish_cancelled(job)
    except Exception as e:
        job.status, job.error = "error", f"{type(e).__name__}: {e}"
        job._append({"type": "error", "error": job.error, "trace": traceback.format_exc()[-1500:]})


def start(kind: str, fn: Callable[[Callable[[dict], None]], Any], on_done: Callable[[Any], bool | None] | None = None,
          run: str | None = None) -> Job:
    job = create(kind, run=run)
    threading.Thread(target=execute, args=(job, fn, on_done), daemon=True).start()
    return job
