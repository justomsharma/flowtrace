"""After an upload the top cases are investigated in the background; the UI joins them instead of waiting."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from flowtrace.agent import investigate as inv_mod
from flowtrace.agent import llm
from flowtrace.api import jobs
from flowtrace.api import main as M
from flowtrace.api.runner import CaseRunner
from flowtrace.settings import cfg as real_cfg

TOP = 3
IDS = [f"B1-C{i:03d}" for i in range(1, 6)]


def wait(pred, s=5.0):
    end = time.time() + s
    while time.time() < end:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def batch_result(gen, batch="B1", window=(0, 1)):
    ids = [i.replace("B1", batch) for i in IDS]
    return {"summary": {"batch_id": batch, "cases": [{"case_id": cid} for cid in ids], "window": list(window)},
            "cases": {cid: {"case_id": cid, "gen": gen} for cid in ids}, "graph": []}


@pytest.fixture
def app(monkeypatch, tmp_path):
    rules = real_cfg()
    monkeypatch.setattr(M, "cfg", lambda: {**rules, "agent": {**rules["agent"], "prefetch_top": TOP, "prefetch_parallel": 2}})
    monkeypatch.setattr(M, "RUNS", tmp_path)
    monkeypatch.setattr(M, "BATCHES", {"B1": batch_result(1)["summary"]})
    monkeypatch.setattr(M, "CASES", batch_result(1)["cases"])
    monkeypatch.setattr(M, "RESULTS", {})
    monkeypatch.setattr(M, "RUNNER", CaseRunner(parallel=2))
    monkeypatch.setattr(M, "rw", lambda *a, **k: None)
    monkeypatch.setattr(llm, "available", lambda: False)
    state = {"gate": threading.Event(), "policies": [], "memory": [], "fail": set()}
    state["gate"].set()
    monkeypatch.setattr(M, "save_case_memory", lambda case, rules: state["memory"].append((case["case_id"], case["gen"])))

    # The real _investigation runs; only the investigation itself is faked.
    monkeypatch.setattr(inv_mod, "new_context", lambda case, rules, emit: (case, emit))

    def investigate(ctx, policy_name, save_memory=True, **k):
        case, emit = ctx
        assert save_memory is False                      # memory is written only for accepted results
        state["policies"].append(policy_name)
        emit({"type": "step_start", "step": 1})
        state["gate"].wait(5)
        if case["case_id"] in state["fail"]:
            raise RuntimeError("graph timeout")
        return {"case_id": case["case_id"], "gen": case["gen"],
                "disposition": {"action": "CLOSE", "risk": 0.1, "confidence_label": "Low"}}
    monkeypatch.setattr(inv_mod, "investigate", investigate)
    yield state
    # Drain this test's background jobs so none finishes into the next test's state.
    state["gate"].set()
    wait(lambda: all(j.status in jobs.TERMINAL for j in M.RUNNER.case_jobs.values()))
    M.RUNNER.shutdown()


def test_upload_investigates_only_the_top_cases_with_rules_when_there_is_no_key(app):
    M.prefetch("B1")
    wait(lambda: len(M.RESULTS) == TOP)
    time.sleep(0.1)
    assert sorted(M.RESULTS) == IDS[:TOP]
    assert app["policies"] == ["heuristic"] * TOP
    assert sorted(app["memory"]) == [(cid, 1) for cid in IDS[:TOP]]


def test_with_a_working_key_the_llm_policy_runs(app, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    M.prefetch("B1")
    wait(lambda: len(M.RESULTS) == TOP)
    assert set(app["policies"]) == {"llm"}


def test_the_queued_jobs_exist_before_the_upload_is_announced(app, monkeypatch):
    slow = threading.Event()
    monkeypatch.setattr(llm, "available", lambda: slow.wait(5) or False)   # a slow key check must not delay them
    app["gate"].clear()
    M.accept_batch(batch_result(1), "file.csv")
    assert all(M._job_brief(cid) for cid in IDS[:TOP]) and M._job_brief(IDS[TOP]) is None
    slow.set()


def test_opening_a_case_joins_its_running_job_instead_of_starting_another(app):
    app["gate"].clear()
    M.prefetch("B1")
    c = TestClient(M.app)
    job = c.get(f"/api/cases/{IDS[0]}").json()["job"]
    assert job["status"] in ("queued", "running")
    assert c.post(f"/api/cases/{IDS[0]}/investigate").json()["job_id"] == job["id"]
    assert c.get(f"/api/cases/{IDS[2]}").json()["job"]["status"] == "queued"   # 2 at a time: the third waits


def test_batch_reports_how_many_of_the_top_cases_are_investigated(app):
    c = TestClient(M.app)
    app["gate"].clear()
    M.prefetch("B1")
    b = c.get("/api/batches/B1").json()
    assert b["prefetch"] == {"total": TOP, "done": 0, "failed": 0}
    assert b["cases"][0]["job"]["status"] in ("queued", "running") and b["cases"][TOP]["job"] is None
    app["gate"].set()
    wait(lambda: len(M.RESULTS) == TOP and all(M.RUNNER.active(cid) is None for cid in IDS))
    b = c.get("/api/batches/B1").json()
    assert b["prefetch"] == {"total": TOP, "done": TOP, "failed": 0} and b["cases"][0]["job"] is None


def test_a_failed_case_is_counted_and_can_be_run_again(app):
    app["fail"].add(IDS[1])
    M.prefetch("B1")
    wait(lambda: len(M.RESULTS) == TOP - 1 and M.RUNNER.failed(IDS[1]))
    c = TestClient(M.app)
    b = c.get("/api/batches/B1").json()
    assert b["prefetch"] == {"total": TOP, "done": TOP - 1, "failed": 1}
    assert b["cases"][1]["failed"] is True and b["cases"][0]["failed"] is False
    app["fail"].clear()
    c.post(f"/api/cases/{IDS[1]}/investigate")
    wait(lambda: IDS[1] in M.RESULTS)
    assert c.get("/api/batches/B1").json()["prefetch"]["failed"] == 0


def test_reupload_during_a_run_never_attaches_old_results_to_renumbered_cases(app):
    app["gate"].clear()
    M.prefetch("B1")
    wait(lambda: app["policies"])
    old = [M.RUNNER.case_jobs[cid] for cid in IDS[:TOP]]

    M.accept_batch(batch_result(2), "same_file.csv")              # the same file again: cases renumbered
    assert all(j.cancelled for j in old)
    app["gate"].set()
    wait(lambda: len(M.RESULTS) == TOP and all(j.status in jobs.TERMINAL for j in old))
    time.sleep(0.1)
    assert {r["gen"] for r in M.RESULTS.values()} == {2}
    assert all(j.status == "cancelled" for j in old)
    assert {gen for _, gen in app["memory"]} == {2}               # no case memory from the cancelled run


def test_startup_requeues_top_cases_without_a_result_newest_upload_first(app, monkeypatch):
    newer = batch_result(1, batch="B2", window=(5, 6))
    monkeypatch.setitem(M.BATCHES, "B2", {**newer["summary"], "uploaded_at": 2.0})
    M.BATCHES["B1"]["uploaded_at"] = 1.0
    M.CASES.update(newer["cases"])
    M.RESULTS[IDS[0]] = {"case_id": IDS[0], "gen": 1, "disposition": {"action": "CLOSE", "risk": 0.1, "confidence_label": "Low"}}
    started = M.requeue_unfinished()
    assert started == ["B2-C001", "B2-C002", "B2-C003"] + IDS[1:TOP]
    wait(lambda: len(M.RESULTS) == 2 * TOP)


def test_the_app_requeues_on_startup(app, monkeypatch):
    called = threading.Event()
    monkeypatch.setattr(M, "requeue_unfinished", lambda: called.set())
    with TestClient(M.app):
        assert called.wait(2)


def test_event_stream_stays_open_while_a_job_is_queued():
    job = jobs.create("case", status="queued")

    def later():
        time.sleep(0.3)
        jobs.execute(job, lambda emit: emit({"type": "step_start"}))
    threading.Thread(target=later, daemon=True).start()
    types = []
    with TestClient(M.app).stream("GET", f"/api/jobs/{job.id}/events") as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                types.append(json.loads(line[5:])["type"])
    assert types == ["step_start", "done"]


def test_requeue_runs_only_in_the_serving_process(monkeypatch):
    monkeypatch.delenv("FLOWTRACE_REQUEUE", raising=False)
    assert M.should_requeue(parent=None) is True
    assert M.should_requeue(parent=object()) is False           # uvicorn --reload / --workers child process
    monkeypatch.setenv("FLOWTRACE_REQUEUE", "1")
    assert M.should_requeue(parent=object()) is True
    monkeypatch.setenv("FLOWTRACE_REQUEUE", "0")
    assert M.should_requeue(parent=None) is False


def test_requeue_orders_by_upload_time_not_data_window(app, monkeypatch):
    older_data = batch_result(1, batch="B2", window=(-5, -4))
    monkeypatch.setitem(M.BATCHES, "B2", {**older_data["summary"], "uploaded_at": 200.0})
    M.BATCHES["B1"]["uploaded_at"] = 100.0
    M.CASES.update(older_data["cases"])
    assert M.requeue_unfinished()[:TOP] == ["B2-C001", "B2-C002", "B2-C003"]
    wait(lambda: len(M.RESULTS) == 2 * TOP)


def test_accepted_uploads_record_their_upload_time(app):
    M.accept_batch(batch_result(1), "file.csv")
    assert M.BATCHES["B1"]["uploaded_at"] > 0
