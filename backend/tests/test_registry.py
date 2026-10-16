"""One tool registry: the in-app agent, the API's act step and the MCP server use the same tools."""
import json

import pytest

from flowtrace import store
from flowtrace.actions import planner as P
from flowtrace.actions import service
from flowtrace.agent import context as ctx_mod
from flowtrace.agent import policy, registry
from flowtrace.agent.tools import ToolError
from flowtrace.graph import client
from flowtrace.graph import trace as trace_mod
from tests.test_planner import CASE, mule_world, result


@pytest.fixture
def runs(tmp_path, monkeypatch):
    """data/runs in a temp folder, holding the fake mule case and its ESCALATE result."""
    monkeypatch.setattr(store, "RUNS", tmp_path)
    for kind, obj in (("cases", CASE), ("results", {**result("ESCALATE"), "case_id": CASE["case_id"],
                                                     "report": {"headline": "Mule split"}})):
        (tmp_path / kind).mkdir()
        (tmp_path / kind / f"{CASE['case_id']}.json").write_text(json.dumps(obj))
    (tmp_path / "batches").mkdir()
    (tmp_path / "batches" / "BX.json").write_text(json.dumps(
        {"batch_id": "BX", "cases": [{"case_id": CASE["case_id"], "tier": "today", "case_score": 0.9,
                                      "accounts": ["X"], "amount": 60000.0, "shape": {"typology": "FAN-OUT"}}]}))
    w = mule_world()
    monkeypatch.setattr(P, "ro", w.ro)
    monkeypatch.setattr(trace_mod, "ro", w.ro)
    monkeypatch.setattr(ctx_mod, "ro", w.ro)
    monkeypatch.setattr(P, "fx_rates", lambda: {"US Dollar": 1.0, "Euro": 0.85})
    return tmp_path


@pytest.fixture
def proposals(monkeypatch):
    """Record proposals instead of writing them; no open actions to start with."""
    made, open_ = [], []
    monkeypatch.setattr(service, "list_actions", lambda case_id=None, status=None, expire=True: list(open_))

    def propose(case_id, type, **kw):
        a = {"id": f"A{len(made)}", "case_id": case_id, "type": type, "status": "PROPOSED", "proposed_by": "agent", **kw}
        made.append(a)
        return a, True
    monkeypatch.setattr(service, "propose", propose)
    return made, open_


def test_policy_tools_come_from_the_registry():
    enum = policy._schema()["properties"]["action"]["enum"]
    assert enum == list(registry.investigation_tools()) + ["finish"]
    assert set(registry.investigation_tools()) >= {"account_profile", "trace_funds", "counterparty_history",
                                                   "shared_counterparties", "find_cycles", "case_memory"}


def test_only_propose_action_writes():
    assert [n for n, s in registry.REGISTRY.items() if s.writes] == ["propose_action"]


def test_propose_action_holds_a_trail_account_with_the_plan_numbers(runs, proposals):
    made, _ = proposals
    out = registry.call("propose_action", {"case_id": CASE["case_id"], "type": "HOLD", "account": "M1",
                                           "reason": "Case money is resting here"})
    assert out["created"] and made[0]["account"] == "M1" and made[0]["amount_blocked"] == pytest.approx(20000)
    assert made[0]["currency"] == "Euro"


@pytest.mark.parametrize("account", ["HUB", "Y", "NOPE"])
def test_propose_action_refuses_hubs_and_accounts_off_the_trail(runs, proposals, account):
    with pytest.raises(ToolError):
        registry.call("propose_action", {"case_id": CASE["case_id"], "type": "HOLD", "account": account,
                                         "reason": "Hold it please"})
    assert proposals[0] == []


def test_propose_action_refuses_more_holds_than_the_cap(runs, proposals):
    made, open_ = proposals
    open_ += [{"type": "HOLD", "status": "APPROVED", "account": f"Q{i}"} for i in range(3)]
    with pytest.raises(ToolError, match="already has 3"):
        registry.call("propose_action", {"case_id": CASE["case_id"], "type": "HOLD", "account": "M2",
                                         "reason": "Hold it please"})


def test_propose_action_on_an_unknown_case(runs, proposals):
    with pytest.raises(ToolError, match="no such case"):
        registry.call("propose_action", {"case_id": "BX-C999", "type": "ESCALATE", "reason": "Looks bad to me"})


def test_act_plans_then_proposes_holds_and_the_case_action(runs, proposals):
    made, _ = proposals
    out = registry.act(CASE["case_id"])
    assert [(a["type"], a.get("account")) for a in made] == [("HOLD", "M1"), ("HOLD", "M2"), ("FILE_STR", None)]
    assert out["plan"]["blocked"] == pytest.approx(25000) and store.load_plan(CASE["case_id"]) == out["plan"]


def test_read_tools_never_write(runs, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a read-only tool wrote to the graph")
    monkeypatch.setattr(client, "rw", boom)
    monkeypatch.setattr(service, "rw", boom)
    monkeypatch.setattr(service, "ro", lambda *a, **k: client.QueryResult(rows=[], cypher="", params={}, ms=0))
    assert registry.call("get_case", {"case_id": CASE["case_id"]})["case"]["case_id"] == CASE["case_id"]
    assert registry.call("plan_holds", {"case_id": CASE["case_id"]})["holds"]
    assert registry.call("list_cases", {})[0]["case_id"] == CASE["case_id"]
