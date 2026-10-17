"""The public demo: sample-only uploads, rate limits, the daily LLM budget, the approver sign-in and the reset."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from flowtrace.agent import llm
from flowtrace.api import guard, jobs
from flowtrace.api import main as M
from flowtrace.demo import reset
from flowtrace.pipeline.batch import batch_id_for

client = TestClient(M.app)  # not a context manager: no startup work, no expiry timer
SAMPLE = sorted(M.SAMPLES.glob("*.csv"))[0]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(M, "approver_token", lambda: "s3cret")
    monkeypatch.setattr(M, "BATCHES", {})
    guard.LIMITS.clear()
    yield
    guard.LIMITS.clear()


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")


@pytest.fixture
def started(monkeypatch):
    """jobs.start without running anything: the work of each job started."""
    calls = []
    monkeypatch.setattr(jobs, "start", lambda kind, fn, on_done=None, run=None:
                        calls.append(getattr(fn, "func", fn)) or jobs.create(kind))
    return calls


def test_demo_refuses_a_file_that_is_not_a_bundled_sample(demo, started):
    r = client.post("/api/batches", files={"file": ("mine.csv", b"a,b\n1,2\n")})
    assert r.status_code == 403 and "bundled sample" in r.json()["detail"]
    assert started == []


def test_demo_accepts_a_bundled_sample_uploaded_as_a_file(demo, started):
    r = client.post("/api/batches", files={"file": ("renamed.csv", SAMPLE.read_bytes())})
    assert r.status_code == 200 and len(started) == 1 and started[0] is not M.reopen_batch


def test_demo_reopens_a_sample_that_is_already_uploaded(demo, started, monkeypatch):
    monkeypatch.setattr(M, "BATCHES", {batch_id_for(SAMPLE.read_bytes()): {"cases": []}})
    r = client.post("/api/batches", data={"sample": SAMPLE.name})
    assert r.status_code == 200 and started == [M.reopen_batch]


def test_reopen_emits_the_batch_and_finished_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "RUNS", tmp_path)
    (tmp_path / "graphs").mkdir()
    (tmp_path / "graphs" / "BX.json").write_text(json.dumps({"links": [{"tx_id": "P1"}]}))
    events = []
    M.reopen_batch("BX", events.append)
    assert events[0] == {"type": "batch", "batch_id": "BX"}
    assert events[1] == {"type": "graph_rows", "links": [{"tx_id": "P1"}]}
    assert [e["stage"] for e in events[2:]] == ["parse", "score", "graph", "detect", "cases"]


def test_outside_demo_mode_any_file_is_processed(started, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    r = client.post("/api/batches", files={"file": ("mine.csv", b"a,b\n1,2\n")})
    assert r.status_code == 200 and len(started) == 1


def test_uploads_are_rate_limited_per_visitor(started, monkeypatch):
    monkeypatch.setattr(guard, "cfg", lambda: {"demo": {"uploads_per_ip": {"count": 2, "minutes": 10}}})
    codes = [client.post("/api/batches", data={"sample": SAMPLE.name}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_rate_limit_window_slides():
    rl = guard.RateLimit()
    assert rl.take("k", 2, 1, now=0) == 0 and rl.take("k", 2, 1, now=10) == 0
    assert rl.take("k", 2, 1, now=20) == pytest.approx(40)
    assert rl.take("k", 2, 1, now=61) == 0


def test_investigations_are_rate_limited_but_joining_one_is_free(monkeypatch):
    monkeypatch.setattr(guard, "cfg", lambda: {"demo": {"investigations_per_ip": {"count": 1, "minutes": 10}}})
    monkeypatch.setattr(M, "CASES", {"BX-C001": {"case_id": "BX-C001"}})
    monkeypatch.setattr(M.RUNNER, "start", lambda *a, **k: jobs.create("case"))
    assert client.post("/api/cases/BX-C001/investigate").status_code == 200
    assert client.post("/api/cases/BX-C001/investigate").status_code == 429
    monkeypatch.setattr(M.RUNNER, "active", lambda cid: object())
    assert client.post("/api/cases/BX-C001/investigate").status_code == 200


def test_the_daily_budget_stops_llm_calls(monkeypatch):
    monkeypatch.setattr(llm, "cfg", lambda: {"llm": {"daily_budget_usd": 0.01,
                                                     "price_per_mtok": {"default": {"input": 1.0, "output": 1.0}}}})
    llm._add_spend({"model": "m", "input_tokens": 5000, "output_tokens": 5000})
    assert llm.spend()["usd"] == pytest.approx(0.01) and llm.spend()["over"]
    out, meta = llm.structured("s", "u", {"type": "object"})
    assert out is None and meta["error"] == "daily LLM budget used"
    assert llm.available() is False


def test_spend_starts_again_each_day(llm_spend_file):
    llm_spend_file.write_text(json.dumps({"day": "2000-01-01", "usd": 99.0, "calls": 9}))
    assert llm.spend()["usd"] == 0.0


def test_listed_models_use_their_own_price(monkeypatch):
    monkeypatch.setattr(llm, "cfg", lambda: {"llm": {"price_per_mtok": {
        "gpt-6-luna": {"input": 0.10, "output": 0.50}, "default": {"input": 5.0, "output": 25.0}}}})
    assert llm.cost_usd("gpt-6-luna", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert llm.cost_usd("unknown", 1_000_000, 0) == pytest.approx(5.0)


@pytest.mark.parametrize("token,code", [("s3cret", 200), ("wrong", 401), (None, 401)])
def test_approver_sign_in_checks_the_token(token, code):
    headers = {"X-Approver-Token": token} if token else {}
    assert client.post("/api/auth/approver", headers=headers).status_code == code


def test_admin_reset_needs_the_token_and_clears_everything(monkeypatch):
    monkeypatch.setattr(M, "CASES", {"BX-C001": {}})
    monkeypatch.setattr(M, "RESULTS", {"BX-C001": {}})
    monkeypatch.setattr(M, "BATCHES", {"BX": {"cases": []}})
    monkeypatch.setattr(reset, "reset_graph", lambda: {})
    monkeypatch.setattr(reset, "reset_runs", lambda: 3)
    assert client.post("/api/admin/reset").status_code == 401
    r = client.post("/api/admin/reset", headers={"X-Approver-Token": "s3cret"})
    assert r.status_code == 200 and r.json()["files_removed"] == 3
    assert M.CASES == {} and M.RESULTS == {} and M.BATCHES == {}


def test_reset_clears_uploads_cases_actions_and_audit():
    q = " ".join(cypher for _, cypher in reset.CLEAN_GRAPH)
    assert "t.batch IS NOT NULL" in q and "n:Case OR n:Action OR n:Audit" in q
    assert set(reset.RUN_KINDS) == {"batches", "cases", "results", "plans", "graphs"}


def test_the_mcp_endpoint_keeps_the_apis_approver_token(monkeypatch):
    from flowtrace.mcp.server import build_server
    monkeypatch.setenv("APPROVER_TOKEN", "kept")
    build_server(drop_approver_token=False)
    assert os.environ["APPROVER_TOKEN"] == "kept"
    build_server()
    assert "APPROVER_TOKEN" not in os.environ


def test_health_reports_demo_mode_and_budget(demo, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    h = client.get("/api/health").json()
    assert h["demo"] is True and set(h["llm_budget"]) == {"usd", "budget_usd", "over"}
    assert client.get("/healthz").json() == {"ok": True}


def test_the_public_demo_runs_no_background_investigations(demo, monkeypatch):
    monkeypatch.setattr(M, "cfg", lambda: {"demo": {"prefetch": False}, "agent": {"prefetch_top": 15}})
    monkeypatch.setattr(M, "BATCHES", {"BX": {"cases": [{"case_id": "BX-C001"}], "window": [0, 1]}})
    assert M.prefetch("BX") == []
    assert client.get("/api/batches/BX").json()["prefetch"]["total"] == 0
