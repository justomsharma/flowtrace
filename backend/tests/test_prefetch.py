"""After an upload the top cases are investigated in the background; the UI joins them instead of waiting."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

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


def batch_result(gen):
    cases = {cid: {"case_id": cid, "gen": gen} for cid in IDS}
    return {"summary": {"batch_id": "B1", "cases": [{"case_id": cid} for cid in IDS], "window": [0, 1]},
            "cases": cases, "graph": []}


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
    state = {"gate": threading.Event(), "policies": [], "memory": []}
    state["gate"].set()
    monkeypatch.setattr(M, "save_case_memory", lambda case, rules: state["memory"].append(case["case_id"]))

    def investigation(case_id, policy):
        gen = M.CASES[case_id]["gen"]

        def run(emit):
            state["policies"].append(policy)
            emit({"type": "step_start", "step": 1})
            state["gate"].wait(5)
            return {"case_id": case_id, "gen": gen,
                    "disposition": {"action": "CLOSE", "risk": 0.1, "confidence_label": "Low"}}
        return run

    monkeypatch.setattr(M, "_investigation", investigation)
    yield state
    # Drain this test's background jobs so none finishes into the next test's state.
    state["gate"].set()
    wait(lambda: all(j.status in jobs.TERMINAL for j in M.RUNNER.case_jobs.values()))
    M.RUNNER.pool.shutdown(wait=True)


def test_upload_investigates_only_the_top_cases_with_rules_when_there_is_no_key(app):
    M.prefetch("B1")
    wait(lambda: len(M.RESULTS) == TOP)
    time.sleep(0.1)
    assert sorted(M.RESULTS) == IDS[:TOP]
    assert app["policies"] == ["heuristic"] * TOP
    assert sorted(app["memory"]) == IDS[:TOP]


def test_with_a_working_key_the_llm_policy_runs(app, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    M.prefetch("B1")
    wait(lambda: len(M.RESULTS) == TOP)
    assert set(app["policies"]) == {"llm"}


def test_opening_a_case_joins_its_running_job_instead_of_starting_another(app):
    app["gate"].clear()
    M.prefetch("B1")
    c = TestClient(M.app)
    job = c.get(f"/api/cases/{IDS[0]}").json()["job"]
    assert job["status"] in ("queued", "running")
    assert c.post(f"/api/cases/{IDS[0]}/investigate").json()["job_id"] == job["id"]
    waiting = c.get(f"/api/cases/{IDS[2]}").json()["job"]
    assert waiting["status"] == "queued"                          # 2 at a time: the third waits its turn
    app["gate"].set()


def test_batch_reports_how_many_of_the_top_cases_are_investigated(app):
    c = TestClient(M.app)
    app["gate"].clear()
    M.prefetch("B1")
    b = c.get("/api/batches/B1").json()
    assert b["prefetch"] == {"total": TOP, "done": 0}
    assert b["cases"][0]["job"]["status"] in ("queued", "running") and b["cases"][TOP]["job"] is None
    app["gate"].set()
    wait(lambda: len(M.RESULTS) == TOP and all(M.RUNNER.active(cid) is None for cid in IDS))
    b = c.get("/api/batches/B1").json()
    assert b["prefetch"] == {"total": TOP, "done": TOP} and b["cases"][0]["job"] is None


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


def test_startup_requeues_top_cases_without_a_finished_result(app):
    M.RESULTS[IDS[0]] = {"case_id": IDS[0], "gen": 1, "disposition": {"action": "CLOSE", "risk": 0.1, "confidence_label": "Low"}}
    started = M.requeue_unfinished()
    assert sorted(j_cid for j_cid in started) == IDS[1:TOP]
    wait(lambda: len(M.RESULTS) == TOP)


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
