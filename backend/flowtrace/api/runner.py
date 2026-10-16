"""Case investigations in the background: one job per case, tied to the upload run it belongs to.

Pre-investigations go through a small pool (a few at a time, so the LLM's rate limits are respected);
an analyst's click starts at once. Re-uploading a file renumbers its cases, so a new run cancels the
old run's jobs and drops any result that still arrives for them.
"""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from flowtrace.api import jobs


def batch_of(case_id: str) -> str:
    return case_id.rpartition("-C")[0] or case_id


class CaseRunner:
    def __init__(self, parallel: int) -> None:
        self.pool = ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="case")
        self.lock = threading.RLock()
        self.case_jobs: dict[str, jobs.Job] = {}
        self.runs: dict[str, str] = {}

    def run_of(self, batch_id: str) -> str:
        with self.lock:
            return self.runs.setdefault(batch_id, uuid.uuid4().hex[:8])

    def new_run(self, batch_id: str) -> str:
        """A fresh upload of this batch: cancel every job of the previous run."""
        with self.lock:
            self.runs[batch_id] = uuid.uuid4().hex[:8]
            for cid, job in list(self.case_jobs.items()):
                if batch_of(cid) == batch_id:
                    jobs.cancel(job)
                    del self.case_jobs[cid]
            return self.runs[batch_id]

    def active(self, case_id: str) -> jobs.Job | None:
        with self.lock:
            j = self.case_jobs.get(case_id)
            return j if j and j.status not in jobs.TERMINAL else None

    def start(self, case_id: str, fn: Callable[[Callable[[dict], None]], Any], on_result: Callable[[Any], None],
              queued: bool = False) -> jobs.Job:
        """Start (or join) the investigation of a case. on_result runs only if the job's run is still current."""
        batch = batch_of(case_id)
        with self.lock:
            if (j := self.active(case_id)) is not None:
                return j
            job = jobs.create("case", status="queued" if queued else "running", run=self.run_of(batch))
            self.case_jobs[case_id] = job

        def on_done(result: Any) -> bool:
            with self.lock:  # new_run takes the same lock: a result is kept, or the run moved on, never both
                if job.cancelled or self.runs.get(batch) != job.run:
                    return False
                on_result(result)
                return True

        if queued:
            self.pool.submit(jobs.execute, job, fn, on_done)
        else:
            threading.Thread(target=jobs.execute, args=(job, fn, on_done), daemon=True).start()
        return job
