"""Case investigations in the background: one job per case, tied to the upload run it belongs to.

Pre-investigations go through a few worker threads (a few at a time, so the LLM's rate limits are
respected). An analyst's click starts at once, and a click on a queued case runs that same job now.
Re-uploading a file renumbers its cases, so a new run cancels the old run's jobs and drops any result
that still arrives for them.

Workers are daemon threads: stopping the server never waits for an LLM call in flight.

Locks: one lock per batch serialises "accept a result" against "start a new run" for that batch (graph
and disk writes happen under it); a short lock guards the dicts. Reads (active, failed) take no lock.
Order is always batch lock, then dict lock.
"""
from __future__ import annotations

import queue
import threading
import uuid
from typing import Any, Callable

from flowtrace.api import jobs
from flowtrace.pipeline.cases import batch_of

Work = Callable[[Callable[[dict], None]], Any]


class CaseRunner:
    def __init__(self, parallel: int) -> None:
        self.queue: queue.Queue = queue.Queue()
        self.workers = [threading.Thread(target=self._work, name=f"case-{i}", daemon=True) for i in range(parallel)]
        for w in self.workers:
            w.start()
        self.lock = threading.RLock()
        self.batch_locks: dict[str, threading.RLock] = {}
        self.case_jobs: dict[str, jobs.Job] = {}
        self.runs: dict[str, str] = {}
        self.tasks: dict[str, tuple[str, Work, Callable[[Any], bool]]] = {}

    def batch_lock(self, batch_id: str) -> threading.RLock:
        with self.lock:
            return self.batch_locks.setdefault(batch_id, threading.RLock())

    def run_of(self, batch_id: str) -> str:
        with self.lock:
            return self.runs.setdefault(batch_id, uuid.uuid4().hex[:8])

    def new_run(self, batch_id: str) -> str:
        """A fresh upload of this batch: cancel every job of the previous run."""
        with self.batch_lock(batch_id), self.lock:
            self.runs[batch_id] = uuid.uuid4().hex[:8]
            for cid, job in list(self.case_jobs.items()):
                if batch_of(cid) == batch_id:
                    jobs.cancel(job, "reupload")
                    del self.case_jobs[cid]
            return self.runs[batch_id]

    def active(self, case_id: str) -> jobs.Job | None:
        j = self.case_jobs.get(case_id)
        return j if j and j.status not in jobs.TERMINAL else None

    def failed(self, case_id: str) -> bool:
        j = self.case_jobs.get(case_id)
        return bool(j and j.status == "error")

    def start(self, case_id: str, fn: Work, on_result: Callable[[Any], None], queued: bool = False) -> jobs.Job:
        """Start (or join) the investigation of a case. on_result runs only if the job's run is still current."""
        batch = batch_of(case_id) or case_id
        with self.lock:
            if (j := self.active(case_id)) is not None:
                if not queued and j.status == "queued":
                    self._run_now(j)          # a click jumps the queue; the worker skips the job later
                return j
            job = jobs.create("case", status="queued" if queued else "running", run=self.run_of(batch))
            self.case_jobs[case_id] = job

            def on_done(result: Any) -> bool:
                with self.batch_lock(batch):  # new_run holds it too: a result is kept, or the run moved on
                    if job.cancelled or self.runs.get(batch) != job.run:
                        return False
                    on_result(result)
                    return True

            self.tasks[job.id] = (case_id, fn, on_done)
            if queued:
                self.queue.put(job)
            else:
                self._run_now(job)
            return job

    def _execute(self, job: jobs.Job) -> None:
        task = self.tasks.get(job.id)
        if task is None:
            return
        case_id, fn, on_done = task
        jobs.execute(job, fn, on_done)
        with self.lock:
            # Done once (a click and a worker may both get here); errors stay listed for failed().
            if job.status in jobs.TERMINAL and self.tasks.pop(job.id, None) is not None:
                if job.status != "error" and self.case_jobs.get(case_id) is job:
                    del self.case_jobs[case_id]

    def _work(self) -> None:
        while (job := self.queue.get()) is not None:
            self._execute(job)

    def _run_now(self, job: jobs.Job) -> None:
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()

    def shutdown(self) -> None:
        """Server stopping: cancel everything; queued work never starts and nothing waits for running work."""
        with self.lock:
            for job in self.case_jobs.values():
                jobs.cancel(job, "shutdown")
        for _ in self.workers:
            self.queue.put(None)
