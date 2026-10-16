"""Actions: the agent proposes, a person decides with a reason, every change is audited, holds expire.

Graph tests run against a scratch graph that is deleted afterwards, never the main graph.
"""
import re
from datetime import datetime, timezone

import pytest

from flowtrace.actions import service as S
from flowtrace.actions.service import ActionError
from flowtrace.agent import context as ctx_mod
from flowtrace.agent.context import CaseContext
from flowtrace.agent.tools import AccountArgs, case_memory
from flowtrace.graph import queries as Q
from flowtrace.graph.client import graph, ro, rw
from flowtrace.settings import cfg, settings
from tests.fakegraph import World

SCRATCH = "zz_test_actions"
NOW = int(datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc).timestamp() * 1000)  # a Friday
LIVE = S.now_ms()  # graph tests run at the real time, so reads never release their holds early


@pytest.fixture
def g(monkeypatch):
    try:
        ro("RETURN 1 AS ok", timeout=1000)
    except Exception:
        pytest.skip("FalkorDB is not running")
    monkeypatch.setattr(settings(), "graph_name", SCRATCH)
    rw("CREATE (:Meta {id: 'scratch'})")  # reads need the graph to exist
    yield
    try:
        graph(SCRATCH).delete()
    except Exception:
        pass


def hold(**kw):
    return S.propose("ZZ-C001", "HOLD", account="ACC1", amount_blocked=5000.0, frozen_now=5000.0,
                     why="Case money reached this account and has not left yet.", **kw)


# --- business days --------------------------------------------------------------------------------

def test_add_business_days_skips_the_weekend():
    end = S.add_business_days(NOW, 4)  # Fri -> Mon, Tue, Wed, Thu
    assert datetime.fromtimestamp((end + 1) / 1000, tz=timezone.utc) == datetime(2026, 10, 23, tzinfo=timezone.utc)


# --- proposing ------------------------------------------------------------------------------------

def test_propose_twice_returns_the_same_action_and_one_audit_entry(g):
    a, created = hold(now=LIVE)
    b, again = hold(now=LIVE + 5)
    assert created and not again and a["id"] == b["id"]
    assert a["status"] == "PROPOSED" and a["proposed_by"] == "agent"
    assert [x["event"] for x in S.audit()] == ["PROPOSED"]


def test_hold_needs_an_account_and_types_are_checked(g):
    with pytest.raises(ActionError) as e:
        S.propose("ZZ-C001", "HOLD")
    assert e.value.status == 422
    with pytest.raises(ActionError) as e:
        S.propose("ZZ-C001", "APPROVE")
    assert e.value.status == 422


# --- deciding -------------------------------------------------------------------------------------

def test_approve_needs_a_reason(g):
    a, _ = hold(now=LIVE)
    with pytest.raises(ActionError) as e:
        S.decide(a["id"], True, "  ok  ")
    assert e.value.status == 422
    assert S.get(a["id"])["status"] == "PROPOSED"


def test_double_approve_with_the_same_key_replays_and_another_key_conflicts(g):
    a, _ = hold(now=LIVE)
    x = S.decide(a["id"], True, "Money is still moving to cash", by="Priya", key="k1", now=LIVE)
    assert x["status"] == "APPROVED" and x["decided_by"] == "Priya"
    assert x["expires_ts"] == S.add_business_days(LIVE, cfg()["actions"]["hold_expiry_business_days"])
    y = S.decide(a["id"], True, "Money is still moving to cash", by="Priya", key="k1", now=LIVE + 999)
    assert y == x
    with pytest.raises(ActionError) as e:
        S.decide(a["id"], False, "Changed my mind about it", key="k2")
    assert e.value.status == 409
    assert sorted(x["event"] for x in S.audit()) == ["APPROVED", "PROPOSED"]


def test_unknown_action_is_404(g):
    with pytest.raises(ActionError) as e:
        S.decide("A-nope", True, "a good reason")
    assert e.value.status == 404


def test_a_rejected_hold_is_not_proposed_again_for_the_same_case(g):
    a, _ = hold(now=LIVE)
    S.decide(a["id"], False, "Account belongs to a known payroll agent", now=LIVE)
    b, created = hold(now=LIVE + 10)
    assert not created and b["id"] == a["id"] and b["status"] == "REJECTED"


# --- expiry and release ---------------------------------------------------------------------------

def test_expiry_auto_releases_once(g):
    a, _ = hold(now=LIVE)
    x = S.decide(a["id"], True, "Money is still moving to cash", now=LIVE)
    assert S.expire_due(now=x["expires_ts"] - 1) == 0
    assert S.expire_due(now=x["expires_ts"]) == 1
    assert S.expire_due(now=x["expires_ts"] + 60_000) == 0
    assert S.get(a["id"])["status"] == "EXPIRED"
    assert [e["event"] for e in S.audit()].count("AUTO_RELEASED") == 1
    b, created = hold(now=x["expires_ts"] + 1)  # the account can be held again after a release
    assert created and b["id"] != a["id"]


def test_reading_actions_releases_holds_whose_time_is_up(g):
    a, _ = hold(now=LIVE - 30 * 86_400_000)
    S.decide(a["id"], True, "Money is still moving to cash", now=LIVE - 30 * 86_400_000)
    assert [x["status"] for x in S.list_actions(case_id="ZZ-C001")] == ["EXPIRED"]


def test_approving_a_release_ends_the_hold(g):
    a, _ = hold(now=LIVE)
    S.decide(a["id"], True, "Money is still moving to cash", now=LIVE)
    r, _ = S.propose("ZZ-C001", "RELEASE", account="ACC1", why="Customer showed the invoice", now=LIVE + 1)
    S.decide(r["id"], True, "Invoice checked with the supplier", now=LIVE + 2)
    assert S.get(a["id"])["status"] == "EXPIRED"
    assert "RELEASED" in [e["event"] for e in S.audit()]


def test_reupload_expires_open_proposals_but_keeps_approved_holds(g):
    a, _ = hold(now=LIVE)
    b, _ = S.propose("ZZ-C001", "HOLD", account="ACC2", amount_blocked=2000.0, now=LIVE)
    S.decide(a["id"], True, "Money is still moving to cash", now=LIVE)
    assert S.expire_proposals("ZZ-C", "The file was uploaded again") == 1
    assert (S.get(a["id"])["status"], S.get(b["id"])["status"]) == ("APPROVED", "EXPIRED")
    assert "CASE_REUPLOADED" in [e["event"] for e in S.audit()]


def test_open_holds_counts_proposed_and_approved(g):
    a, _ = hold(now=LIVE)
    S.propose("ZZ-C001", "HOLD", account="ACC2", now=LIVE)
    S.decide(a["id"], True, "Money is still moving to cash", now=LIVE)
    assert S.open_holds("ZZ-C001") == 2


# --- verdicts and case memory -----------------------------------------------------------------------

def seed_cases(*cases):
    rw("CREATE (:Account {id: 'A'})-[:TRANSFER {tx_id: 't1', ts: 50, amount: 10.0}]->(:Account {id: 'B'})")
    for cid, disp, created in cases:
        rw("MATCH (b:Account {id: 'B'}) CREATE (:Case {id: $id, disposition: $d, created_ts: $c, summary: 'agent note'})-[:ABOUT]->(b)",
           {"id": cid, "d": disp, "c": created})


def test_approving_file_str_writes_the_verdict_and_case_memory_prefers_it(g):
    seed_cases(("ZZ-C001", "REVIEW", 100))
    a, _ = S.propose("ZZ-C001", "FILE_STR", why="The evidence points to laundering")
    S.decide(a["id"], True, "Confirmed mule chain", by="Priya")
    case = ro("MATCH (c:Case {id: 'ZZ-C001'}) RETURN c.verdict AS v, c.verdict_by AS by, c.disposition AS d").rows[0]
    assert case == {"v": "ESCALATE", "by": "Priya", "d": "REVIEW"}  # the agent's disposition is kept
    prior = ro(Q.PRIOR_CASES, {"id": "B", "batch_prefix": "YY-C", "t0": 200}).rows
    assert (prior[0]["disposition"], prior[0]["decided_by"]) == ("ESCALATE", "human")
    near = ro(Q.NEAREST_ESCALATED, {"id": "A", "batch_prefix": "YY-C", "t0": 200}).rows
    assert (near[0]["account"], near[0]["decided_by"]) == ("B", "human")
    assert ro(Q.PRIOR_CASES, {"id": "B", "batch_prefix": "YY-C", "t0": 100}).rows == []  # created_ts < t0 still holds
    assert "VERDICT" in [e["event"] for e in S.audit()]


def test_a_human_close_overrides_an_agent_escalation(g):
    seed_cases(("ZZ-C001", "ESCALATE", 100))
    a, _ = S.propose("ZZ-C001", "CLOSE", why="Legitimate explanations")
    S.decide(a["id"], True, "Supplier invoices verified", by="Priya")
    assert ro(Q.NEAREST_ESCALATED, {"id": "A", "batch_prefix": "YY-C", "t0": 200}).rows == []
    prior = ro(Q.PRIOR_CASES, {"id": "B", "batch_prefix": "YY-C", "t0": 200}).rows
    assert (prior[0]["disposition"], prior[0]["decided_by"]) == ("CLOSE", "human")


def test_prior_cases_list_human_verdicts_first(g):
    seed_cases(("ZZ-C001", "REVIEW", 100), ("ZZ-C002", "ESCALATE", 150))
    a, _ = S.propose("ZZ-C001", "CLOSE", why="Legitimate explanations")
    S.decide(a["id"], True, "Supplier invoices verified")
    prior = ro(Q.PRIOR_CASES, {"id": "B", "batch_prefix": "YY-C", "t0": 200}).rows
    assert [p["case_id"] for p in prior] == ["ZZ-C001", "ZZ-C002"]


# --- no database --------------------------------------------------------------------------------

def test_audit_entries_are_only_ever_created():
    for name in dir(Q):
        q = getattr(Q, name)
        if not isinstance(q, str) or ":Audit" not in q:
            continue
        for var in re.findall(r"\((\w+):Audit", q):
            assert not re.search(rf"\b(SET|REMOVE)\s+{var}\.|\bDELETE\s+{var}\b", q), name
        if re.search(r"MATCH\s*\(\w*:Audit", q):  # a query that finds audit entries only reads them
            assert not re.search(r"\b(SET|DELETE|REMOVE|MERGE|CREATE)\b", q), name


class PriorWorld(World):
    def answer(self, c, p):
        if c == Q.PRIOR_CASES:
            return [{"case_id": "K1", "disposition": "ESCALATE", "decided_by": "human", "typology": "FAN-OUT",
                     "score": 0.7, "created_ts": 1, "summary": "Confirmed mule chain"}]
        return super().answer(c, p)


def test_case_memory_says_when_an_analyst_confirmed_it(monkeypatch):
    w = PriorWorld([], {})
    monkeypatch.setattr(ctx_mod, "ro", w.ro)
    ctx = CaseContext(case_id="BX-C001", primary="X", accounts=["X"], tx_ids=[], t0=10, t1=20, rules=cfg())
    case_memory(ctx, AccountArgs(account="X"))
    claim = next(e.claim for e in ctx.book.items if e.code == "PRIOR_ESCALATION")
    assert "analyst" in claim
