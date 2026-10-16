"""Background case runner: a bounded pool, one job per case, and re-uploads cancel the old run's jobs."""
import threading
import time

from flowtrace.api import jobs
from flowtrace.api.runner import CaseRunner, batch_of


def wait(pred, s=5.0):
    end = time.time() + s
    while time.time() < end:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_batch_of_reads_the_upload_from_the_case_id():
    assert batch_of("B12AB-C007") == "B12AB"


def test_queued_cases_never_run_more_than_parallel_at_once():
    r, gate, lock = CaseRunner(parallel=2), threading.Event(), threading.Lock()
    live, peak, got = [0], [0], []

    def fn(emit):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        gate.wait(5)
        with lock:
            live[0] -= 1
        return {"ok": 1}

    js = [r.start(f"B1-C{i:03d}", fn, got.append, queued=True) for i in range(5)]
    wait(lambda: live[0] == 2)
    time.sleep(0.1)
    assert peak[0] == 2 and sum(j.status == "queued" for j in js) == 3
    gate.set()
    wait(lambda: len(got) == 5)
    assert peak[0] == 2 and all(j.status == "done" for j in js)


def test_starting_a_case_twice_returns_the_same_job():
    r, gate = CaseRunner(parallel=1), threading.Event()
    a = r.start("B1-C001", lambda emit: gate.wait(5), lambda res: None, queued=True)
    b = r.start("B1-C001", lambda emit: None, lambda res: None)
    assert a is b and r.active("B1-C001") is a
    gate.set()
    wait(lambda: a.status == "done")
    assert r.active("B1-C001") is None


def test_reupload_cancels_queued_jobs_and_discards_running_results():
    r, gate, got = CaseRunner(parallel=1), threading.Event(), []

    def fn(emit):
        emit({"type": "step_start"})
        gate.wait(5)
        emit({"type": "step_done"})
        return {"old": True}

    running = r.start("B1-C001", fn, got.append, queued=True)
    queued = r.start("B1-C002", fn, got.append, queued=True)
    other = r.start("B2-C001", lambda emit: {"other": True}, got.append)
    wait(lambda: running.status == "running")

    r.new_run("B1")
    assert queued.status == "cancelled"
    gate.set()
    wait(lambda: running.status in jobs.TERMINAL)
    assert running.status == "cancelled"
    wait(lambda: other.status == "done")
    assert got == [{"other": True}]                      # other uploads are untouched

    fresh = r.start("B1-C001", lambda emit: {"new": True}, got.append)
    wait(lambda: fresh.status == "done")
    assert fresh is not running and got[-1] == {"new": True}


def test_a_result_that_finishes_after_a_reupload_is_discarded():
    """No emit after the re-upload: the result itself must be checked against the current run."""
    r, gate, got = CaseRunner(parallel=1), threading.Event(), []
    j = r.start("B1-C001", lambda emit: gate.wait(5) and {"old": True}, got.append)
    wait(lambda: j.status == "running")
    r.new_run("B1")
    gate.set()
    wait(lambda: j.status in jobs.TERMINAL)
    assert got == [] and j.status == "cancelled"
