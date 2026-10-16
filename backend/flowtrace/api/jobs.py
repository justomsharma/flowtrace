"""Background jobs with a replayable event log, streamed to the UI over Server-Sent Events."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

TERMINAL = ("done", "error", "cancelled")
KEEP_FINISHED_S = 600   # a finished job stays replayable this long, then it is dropped from memory


class Cancelled(BaseException):
    """Raised inside a job's work at its next event once the job was cancelled. A BaseException, so a
    broad `except Exception` in the work cannot swallow it."""


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"            # queued | running | done | error | cancelled
    run: str | None = None             # the upload run this job belongs to (case jobs)
    cancelled: bool = False
    cancel_reason: str | None = None   # "reupload" | "shutdown": the UI tells the analyst why a run stopped
    finished_at: float | None = None
    claimed: bool = False              # set once by whichever thread runs it (or by cancel): it runs at most once
    events: list[dict] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    started: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def emit(self, event: dict) -> None:
        if self.cancelled:
            raise Cancelled()
        self._append(event)

    def _append(self, event: dict) -> None:
        with self.lock:
            self.events.append({"seq": len(self.events), "t": round(time.time() - self.started, 3), **event})


JOBS: dict[str, Job] = {}


def create(kind: str, status: str = "running", run: str | None = None) -> Job:
    _prune()
    job = Job(id=f"{kind}-{uuid.uuid4().hex[:8]}", kind=kind, status=status, run=run)
    JOBS[job.id] = job
    return job


def _prune() -> None:
    cutoff = time.time() - KEEP_FINISHED_S
    for jid, j in list(JOBS.items()):
        if j.finished_at is not None and j.finished_at < cutoff:
            JOBS.pop(jid, None)


def _finish(job: Job, status: str, event: dict) -> None:
    # The event goes in before the status changes: a stream that sees a final status has its final event.
    with job.lock:
        if job.status in TERMINAL:
            return
        job._append(event)
        job.status, job.finished_at = status, time.time()


def _cancelled_event(job: Job) -> dict:
    # A result rejected because its upload run moved on is a re-upload too.
    return {"type": "cancelled", "reason": job.cancel_reason or "reupload"}


def cancel(job: Job, reason: str) -> None:
    """Stop a job: a queued one never starts, a running one stops at its next event and its result is dropped."""
    with job.lock:
        if job.status in TERMINAL:
            return
        job.cancelled, job.cancel_reason = True, job.cancel_reason or reason
        if not job.claimed:
            job.claimed = True
            _finish(job, "cancelled", _cancelled_event(job))


def execute(job: Job, fn: Callable[[Callable[[dict], None]], Any], on_done: Callable[[Any], bool | None] | None = None) -> None:
    """Run a job in the current thread, unless another thread already claimed it. on_done may return False
    to reject a stale result (the job then ends cancelled)."""
    with job.lock:
        if job.claimed:
            return
        job.claimed = True
        job.status, job.started = "running", time.time()
    try:
        result = fn(job.emit)
        if job.cancelled or (on_done and on_done(result) is False):
            _finish(job, "cancelled", _cancelled_event(job))
            return
        job.result = result
        _finish(job, "done", {"type": "done"})
    except Cancelled:
        _finish(job, "cancelled", _cancelled_event(job))
    except Exception as e:
        job.error = f"{type(e).__name__}: {e}"
        _finish(job, "error", {"type": "error", "error": job.error, "trace": traceback.format_exc()[-1500:]})


def start(kind: str, fn: Callable[[Callable[[dict], None]], Any], on_done: Callable[[Any], bool | None] | None = None,
          run: str | None = None) -> Job:
    job = create(kind, run=run)
    threading.Thread(target=execute, args=(job, fn, on_done), daemon=True).start()
    return job
