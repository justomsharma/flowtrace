"""API for actions: plan (the agent's act step), approve/reject with a reason and an idempotency key, audit."""
import pytest
from fastapi.testclient import TestClient

from flowtrace.actions import service
from flowtrace.actions.service import ActionError
from flowtrace.agent import registry
from flowtrace.api import main as M

client = TestClient(M.app)  # not a context manager: no startup re-queue, no expiry timer


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(M, "CASES", {"BX-C001": {"case_id": "BX-C001"}})
    monkeypatch.setattr(M, "RESULTS", {})
    return "BX-C001"


def test_plan_needs_a_finished_investigation(case):
    r = client.post(f"/api/cases/{case}/plan")
    assert r.status_code == 409 and "investigat" in r.json()["detail"]


def test_plan_on_an_unknown_case_is_404(case):
    assert client.post("/api/cases/BX-C999/plan").status_code == 404


def test_plan_runs_the_agents_act_step(case, monkeypatch):
    M.RESULTS[case] = {"case_id": case}
    calls = []
    monkeypatch.setattr(registry, "act", lambda cid: calls.append(cid) or {"plan": {"blocked": 5.0}, "actions": [{"id": "A1"}]})
    r = client.post(f"/api/cases/{case}/plan", headers={"Idempotency-Key": "k1"})
    assert r.status_code == 200 and r.json() == {"plan": {"blocked": 5.0}, "actions": [{"id": "A1"}]}
    assert calls == [case]


def test_approve_passes_the_reason_name_and_key(monkeypatch):
    seen = {}

    def decide(action_id, approve, reason, by="analyst", key=None):
        seen.update(id=action_id, approve=approve, reason=reason, by=by, key=key)
        return {"id": action_id, "status": "APPROVED"}
    monkeypatch.setattr(service, "decide", decide)
    r = client.post("/api/actions/A1/approve", json={"reason": "Money still moving", "by": "Priya"},
                    headers={"Idempotency-Key": "k1"})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    assert seen == {"id": "A1", "approve": True, "reason": "Money still moving", "by": "Priya", "key": "k1"}


def test_reject_is_a_decision_too(monkeypatch):
    monkeypatch.setattr(service, "decide", lambda action_id, approve, reason, by="analyst", key=None:
                        {"id": action_id, "status": "APPROVED" if approve else "REJECTED"})
    assert client.post("/api/actions/A1/reject", json={"reason": "Known payroll agent"}).json()["status"] == "REJECTED"


@pytest.mark.parametrize("status", [404, 409, 422])
def test_decision_errors_keep_their_status(monkeypatch, status):
    def decide(*a, **k):
        raise ActionError(status, "nope")
    monkeypatch.setattr(service, "decide", decide)
    r = client.post("/api/actions/A1/approve", json={"reason": "whatever it is"})
    assert r.status_code == status and r.json()["detail"] == "nope"


def test_a_decision_needs_a_reason_field():
    assert client.post("/api/actions/A1/approve", json={}).status_code == 422


def test_actions_and_audit_endpoints(monkeypatch):
    monkeypatch.setattr(service, "list_actions", lambda case_id=None, status=None, expire=True: [{"id": "A1", "status": status}])
    monkeypatch.setattr(service, "audit", lambda case_id=None, limit=200: [{"event": "PROPOSED", "case_id": case_id, "n": limit}])
    assert client.get("/api/actions?status=APPROVED").json() == [{"id": "A1", "status": "APPROVED"}]
    assert client.get("/api/audit?case_id=BX-C001&limit=5").json() == [{"event": "PROPOSED", "case_id": "BX-C001", "n": 5}]


def test_get_plan_returns_the_saved_plan_and_current_actions(case, monkeypatch):
    monkeypatch.setattr(M.store, "load_plan", lambda cid: {"case_id": cid})
    monkeypatch.setattr(service, "list_actions", lambda case_id=None, status=None, expire=True: [{"id": "A1"}])
    assert client.get(f"/api/cases/{case}/plan").json() == {"plan": {"case_id": case}, "actions": [{"id": "A1"}]}


def test_a_stored_result_triggers_the_act_step_and_survives_its_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "RUNS", tmp_path)
    monkeypatch.setattr(M, "RESULTS", {})
    monkeypatch.setattr(M, "save_case_memory", lambda r, rules: None)
    acted = []

    def act(cid):
        acted.append(cid)
        raise RuntimeError("graph down")
    monkeypatch.setattr(registry, "act", act)
    M._store_result("BX-C001", {"case_id": "BX-C001"})
    assert acted == ["BX-C001"] and "BX-C001" in M.RESULTS
