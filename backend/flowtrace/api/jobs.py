"""Background jobs with a replayable event log, streamed to the UI over Server-Sent Events."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"            # running | done | error
    events: list[dict] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    started: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, event: dict) -> None:
        with self.lock:
            self.events.append({"seq": len(self.events), "t": round(time.time() - self.started, 3), **event})


JOBS: dict[str, Job] = {}


def start(kind: str, fn: Callable[[Callable[[dict], None]], Any], on_done: Callable[[Any], None] | None = None) -> Job:
    job = Job(id=f"{kind}-{uuid.uuid4().hex[:8]}", kind=kind)
    JOBS[job.id] = job

    def run():
        try:
            job.result = fn(job.emit)
            if on_done:
                on_done(job.result)
            job.status = "done"
            job.emit({"type": "done"})
        except Exception as e:
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
            job.emit({"type": "error", "error": job.error, "trace": traceback.format_exc()[-1500:]})

    threading.Thread(target=run, daemon=True).start()
    return job
