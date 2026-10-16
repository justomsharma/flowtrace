"""API for actions: plan (the agent's act step), approve/reject with a reason and an idempotency key, audit."""
import pytest
from fastapi.testclient import TestClient

from flowtrace.actions import service
from flowtrace.actions.service import ActionError
from flowtrace.agent import registry
from flowtrace.api import main as M

client = TestClient(M.app)  # not a context manager: no startup re-queue, no expiry timer
TOKEN = {"X-Approver-Token": "s3cret"}


@pytest.fixture(autouse=True)
def approver_token(monkeypatch):
    """The server's APPROVER_TOKEN (from .env in real use)."""
    monkeypatch.setattr(M, "approver_token", lambda: "s3cret")


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

    def decide(action_id, approve, reason, by="analyst", key=None, auth=None):
        seen.update(id=action_id, approve=approve, reason=reason, by=by, key=key, auth=auth)
        return {"id": action_id, "status": "APPROVED"}
    monkeypatch.setattr(service, "decide", decide)
    r = client.post("/api/actions/A1/approve", json={"reason": "Money still moving", "by": "Priya"},
                    headers={"Idempotency-Key": "k1", **TOKEN})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    assert seen == {"id": "A1", "approve": True, "reason": "Money still moving", "by": "Priya", "key": "k1",
                    "auth": "approver-token"}


@pytest.mark.parametrize("headers", [{}, {"X-Approver-Token": "wrong"}])
@pytest.mark.parametrize("verb", ["approve", "reject"])
def test_a_decision_without_the_approver_token_is_refused(monkeypatch, headers, verb):
    called = []
    monkeypatch.setattr(service, "decide", lambda *a, **k: called.append(1))
    r = client.post(f"/api/actions/A1/{verb}", json={"reason": "Money still moving", "by": "Priya"}, headers=headers)
    assert r.status_code == 401 and called == []


def test_decisions_are_off_when_the_server_has_no_approver_token(monkeypatch):
    monkeypatch.setattr(M, "approver_token", lambda: None)
    r = client.post("/api/actions/A1/approve", json={"reason": "Money still moving", "by": "Priya"}, headers=TOKEN)
    assert r.status_code == 403 and "APPROVER_TOKEN" in r.json()["detail"]


def test_a_decision_needs_the_approvers_name():
    assert client.post("/api/actions/A1/approve", json={"reason": "Money still moving", "by": " "}, headers=TOKEN).status_code == 422


def test_reject_is_a_decision_too(monkeypatch):
    monkeypatch.setattr(service, "decide", lambda action_id, approve, reason, by="analyst", key=None, auth=None:
                        {"id": action_id, "status": "APPROVED" if approve else "REJECTED"})
    r = client.post("/api/actions/A1/reject", json={"reason": "Known payroll agent", "by": "Priya"}, headers=TOKEN)
    assert r.json()["status"] == "REJECTED"


@pytest.mark.parametrize("status", [404, 409, 422])
def test_decision_errors_keep_their_status(monkeypatch, status):
    def decide(*a, **k):
        raise ActionError(status, "nope")
    monkeypatch.setattr(service, "decide", decide)
    r = client.post("/api/actions/A1/approve", json={"reason": "whatever it is", "by": "Priya"}, headers=TOKEN)
    assert r.status_code == status and r.json()["detail"] == "nope"


def test_a_decision_needs_a_reason_field():
    assert client.post("/api/actions/A1/approve", json={"by": "Priya"}, headers=TOKEN).status_code == 422


def test_actions_and_audit_endpoints(monkeypatch):
    monkeypatch.setattr(service, "list_actions", lambda case_id=None, status=None, expire=True: [{"id": "A1", "status": status}])
    monkeypatch.setattr(service, "audit", lambda case_id=None, limit=200: [{"event": "PROPOSED", "case_id": case_id, "n": limit}])
    assert client.get("/api/actions?status=APPROVED").json() == [{"id": "A1", "status": "APPROVED"}]
    assert client.get("/api/audit?case_id=BX-C001&limit=5").json() == [{"event": "PROPOSED", "case_id": "BX-C001", "n": 5}]


def test_get_plan_returns_the_saved_plan_and_current_actions(case, monkeypatch):
    plan = {"case_id": case, "network": {"nodes": [], "edges": []}}
    act = {"id": "A1", "type": "FILE_STR", "status": "PROPOSED"}
    monkeypatch.setattr(M.store, "load_plan", lambda cid: plan)
    monkeypatch.setattr(service, "list_actions", lambda case_id=None, status=None, expire=True: [act])
    r = client.get(f"/api/cases/{case}/plan").json()
    assert {k: r[k] for k in ("plan", "actions", "held_effect", "open_effect")} == {
        "plan": plan, "actions": [act], "held_effect": {"frozen": 0, "on_routes": 0}, "open_effect": {"frozen": 0, "on_routes": 0}}


def test_get_plan_before_any_plan_exists(case, monkeypatch):
    monkeypatch.setattr(M.store, "load_plan", lambda cid: None)
    monkeypatch.setattr(service, "list_actions", lambda case_id=None, status=None, expire=True: [])
    r = client.get(f"/api/cases/{case}/plan").json()
    assert (r["plan"], r["actions"], r["held_effect"], r["open_effect"]) == (None, [], None, None)


def test_a_stored_result_triggers_the_act_step_and_survives_its_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "RUNS", tmp_path)
    monkeypatch.setattr(M, "RESULTS", {})
    monkeypatch.setattr(M, "save_case_memory", lambda r, rules: None)
    acted = []

    def act(cid, **kw):
        acted.append((cid, kw))
        raise RuntimeError("graph down")
    monkeypatch.setattr(registry, "act", act)
    monkeypatch.setattr(M, "approver_token", lambda: None)
    M._store_result("BX-C001", {"case_id": "BX-C001"})                          # a background run
    M._store_result("BX-C001", {"case_id": "BX-C001"}, started_by_person=True)   # a person clicked Investigate
    # Only the person-started run may auto-hold, and approvals are on only when the server has a token.
    assert acted == [("BX-C001", {"auto": False, "approvals_on": False}), ("BX-C001", {"auto": True, "approvals_on": False})]
    assert "BX-C001" in M.RESULTS


def test_a_manual_replan_never_auto_holds(case, monkeypatch):
    seen = []
    monkeypatch.setattr(M, "RESULTS", {case: {"case_id": case}})
    monkeypatch.setattr(registry, "act", lambda cid, **kw: seen.append(kw) or {"plan": None, "actions": [], "auto_held": []})
    assert client.post(f"/api/cases/{case}/plan").status_code == 200
    assert seen == [{}]


def test_get_plan_says_what_the_open_holds_block(case, monkeypatch):
    plan = {"case_id": case, "network": {"nodes": [], "edges": []}}
    monkeypatch.setattr(M.store, "load_plan", lambda cid: plan)
    monkeypatch.setattr(service, "list_actions", lambda case_id=None, status=None, expire=True: [
        {"id": "A1", "type": "HOLD", "status": "APPROVED", "account": "M1"},
        {"id": "A2", "type": "HOLD", "status": "REJECTED", "account": "M2"},
        {"id": "A3", "type": "HOLD", "status": "PROVISIONAL", "account": "M3"},
        {"id": "A4", "type": "HOLD", "status": "PROPOSED", "account": "M4"}])
    seen = []
    monkeypatch.setattr(M.planner, "effect", lambda p, held: seen.append((p, held)) or {"frozen": 1.0, "on_routes": 2.0})
    r = client.get(f"/api/cases/{case}/plan").json()
    assert r["open_effect"] == {"frozen": 1.0, "on_routes": 2.0}
    # In place now (approved or provisional), then what approving the proposals too would freeze.
    assert seen == [(plan, ["M1", "M3"]), (plan, ["M1", "M3", "M4"])]


@pytest.mark.parametrize("enabled,token,active", [(True, "t", True), (True, None, False), (False, "t", False)])
def test_the_ui_is_told_exactly_when_auto_hold_can_act(monkeypatch, enabled, token, active):
    rules = {**M.cfg(), "actions": {**M.cfg()["actions"], "auto_hold": {"enabled": enabled, "min_confidence": 0.9,
                                                                        "confirm_business_days": 1}}}
    monkeypatch.setattr(M, "cfg", lambda: rules)
    monkeypatch.setattr(M, "approver_token", lambda: token)
    assert M.auto_hold_policy() == {"active": active, "min_confidence": 0.9, "confirm_business_days": 1}


def test_only_an_investigation_a_person_started_may_auto_hold(monkeypatch):
    starts = []
    monkeypatch.setattr(M, "CASES", {"BX-C001": {"case_id": "BX-C001"}})
    monkeypatch.setattr(M.RUNNER, "start", lambda cid, work, done, queued=False: starts.append(done) or type("J", (), {"id": "J1"})())
    client.post("/api/cases/BX-C001/investigate")
    assert starts[0].keywords == {"started_by_person": True}
    monkeypatch.setattr(M, "BATCHES", {"BX": {"cases": [{"case_id": "BX-C001"}]}})
    monkeypatch.setattr(M, "RESULTS", {})
    monkeypatch.setattr(M.RUNNER, "active", lambda cid: False)
    M.prefetch("BX")
    assert starts[1].keywords == {}                 # background prefetch only proposes
