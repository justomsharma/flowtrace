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
    return S.propose("ZZ-C001", "HOLD", account="ACC1", hold_amount=5000.0,
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


def test_the_stored_hold_amount_is_the_money_frozen(g):
    a, _ = hold(now=LIVE)
    assert a["hold_amount"] == 5000.0 and "amount_blocked" not in a


def test_mcp_proposals_say_where_they_came_from(g):
    a, _ = S.propose("ZZ-C001", "HOLD", account="ACC9", hold_amount=2000.0, proposed_by="mcp", now=LIVE)
    assert a["proposed_by"] == "mcp" and S.audit()[0]["actor"] == "mcp"
    with pytest.raises(ActionError) as e:
        S.propose("ZZ-C001", "HOLD", account="ACC8", proposed_by="analyst")
    assert e.value.status == 422


def test_the_hold_cap_is_checked_in_the_same_write(g):
    cap = cfg()["actions"]["max_holds_per_case"]
    made = [S.propose("ZZ-C001", "HOLD", account=f"ACC{i}", hold_amount=2000.0, now=LIVE)[0] for i in range(cap)]
    with pytest.raises(ActionError) as e:
        S.propose("ZZ-C001", "HOLD", account="ACC-X", hold_amount=2000.0, now=LIVE)
    assert e.value.status == 409
    again, created = S.propose("ZZ-C001", "HOLD", account="ACC0", hold_amount=2000.0, now=LIVE)
    assert not created and again["id"] == made[0]["id"]          # an existing hold is still returned
    assert len([x for x in S.list_actions(case_id="ZZ-C001") if x["type"] == "HOLD"]) == cap
    S.propose("ZZ-C002", "HOLD", account="ACC-X", hold_amount=2000.0, now=LIVE)  # the cap is per case


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


def test_reupload_withdraws_proposals_and_retires_decisions_so_renumbered_cases_start_clean(g):
    a, _ = hold(now=LIVE)
    b, _ = S.propose("ZZ-C001", "HOLD", account="ACC2", hold_amount=2000.0, now=LIVE)
    c, _ = S.propose("ZZ-C001", "FILE_STR", why="The evidence points to laundering", now=LIVE)
    S.decide(a["id"], True, "Money is still moving to cash", now=LIVE)
    S.decide(c["id"], False, "Not enough to file yet", now=LIVE)
    assert S.retire_batch("ZZ-C", "The file was uploaded again") == {"withdrawn": 1, "retired": 2}
    assert (S.get(a["id"])["status"], S.get(b["id"])["status"]) == ("APPROVED", "EXPIRED")  # the hold is still real
    # The renumbered ZZ-C001 may be a different case: none of the old decisions attach to it.
    assert S.list_actions(case_id="ZZ-C001") == []
    fresh, created = hold(now=LIVE + 1)
    again, created2 = S.propose("ZZ-C001", "FILE_STR", why="The evidence points to laundering", now=LIVE + 1)
    assert created and created2 and fresh["id"] != a["id"] and again["status"] == "PROPOSED"
    assert a["id"] in [x["id"] for x in S.list_actions(status="APPROVED")]  # Results still shows the old hold
    assert [e["event"] for e in S.audit()].count("CASE_REUPLOADED") == 3


def test_approving_one_case_decision_withdraws_the_other_open_ones(g):
    f, _ = S.propose("ZZ-C001", "FILE_STR", why="The evidence points to laundering", now=LIVE)
    c, _ = S.propose("ZZ-C001", "CLOSE", why="Legitimate explanations", now=LIVE)
    h, _ = hold(now=LIVE)
    S.decide(f["id"], True, "Confirmed mule chain", now=LIVE)
    assert (S.get(c["id"])["status"], S.get(h["id"])["status"]) == ("EXPIRED", "PROPOSED")
    assert "SUPERSEDED" in [e["event"] for e in S.audit()]


def test_closing_a_case_withdraws_its_proposed_holds_and_releases_approved_ones(g):
    a, _ = hold(now=LIVE)
    b, _ = S.propose("ZZ-C001", "HOLD", account="ACC2", hold_amount=2000.0, now=LIVE)
    other, _ = S.propose("ZZ-C002", "HOLD", account="ACC3", hold_amount=2000.0, now=LIVE)
    S.decide(a["id"], True, "Money is still moving to cash", now=LIVE)
    c, _ = S.propose("ZZ-C001", "CLOSE", why="Legitimate explanations", now=LIVE + 1)
    S.decide(c["id"], True, "Supplier invoices verified", now=LIVE + 2)
    assert (S.get(a["id"])["status"], S.get(b["id"])["status"]) == ("EXPIRED", "EXPIRED")
    assert S.get(a["id"])["released_at"] == LIVE + 2
    assert S.get(other["id"])["status"] == "PROPOSED"               # other cases are untouched
    events = {(e["action_id"], e["event"]) for e in S.audit()}
    assert {(a["id"], "RELEASED"), (b["id"], "WITHDRAWN")} <= events


def test_a_decision_records_how_the_approver_was_checked(g):
    a, _ = hold(now=LIVE)
    S.decide(a["id"], True, "Money is still moving to cash", by="Priya", auth="approver-token", now=LIVE)
    entry = next(e for e in S.audit() if e["event"] == "APPROVED")
    assert (entry["actor"], entry["auth"]) == ("Priya", "approver-token")


def test_a_released_account_can_be_held_and_released_again(g):
    a, _ = hold(now=LIVE)
    S.decide(a["id"], True, "Money is still moving to cash", now=LIVE)
    r, _ = S.propose("ZZ-C001", "RELEASE", account="ACC1", why="Customer showed the invoice", now=LIVE + 1)
    S.decide(r["id"], True, "Invoice checked with the supplier", now=LIVE + 2)
    b, _ = hold(now=LIVE + 3)
    S.decide(b["id"], True, "New money arrived from the same mule", now=LIVE + 3)
    r2, created = S.propose("ZZ-C001", "RELEASE", account="ACC1", why="Cleared again", now=LIVE + 4)
    assert created and r2["id"] != r["id"]


def test_a_reupload_keeps_cases_with_a_human_verdict_in_case_memory(g):
    seed_cases(("ZZ-C001", "REVIEW", 100), ("ZZ-C002", "ESCALATE", 100))
    a, _ = S.propose("ZZ-C001", "FILE_STR", why="The evidence points to laundering")
    S.decide(a["id"], True, "Confirmed mule chain", by="Priya")
    rw(Q.DELETE_BATCH_CASES, {"prefix": "ZZ-C", "now": 5})
    left = ro("MATCH (c:Case) RETURN c.id AS id, c.retired_id AS was, c.verdict AS v").rows
    assert left == [{"id": "ZZ-C001~5", "was": "ZZ-C001", "v": "ESCALATE"}]
    prior = ro(Q.PRIOR_CASES, {"id": "B", "batch_prefix": "YY-C", "t0": 200}).rows  # a later upload still sees it
    assert (prior[0]["case_id"], prior[0]["decided_by"]) == ("ZZ-C001~5", "human")


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


# --- auto-hold: provisional, confirmed by a person or released ----------------------------------------

def test_an_auto_hold_is_provisional_audited_as_the_agent_and_counts_toward_the_cap(g):
    a, _ = hold(now=LIVE)
    p = S.provisional_hold(a["id"], "Escalated with a high confidence score", now=LIVE)
    assert p["status"] == "PROVISIONAL" and p["held_by"] == S.AUTO_HOLD_ACTOR and "decided_by" not in p
    assert p["provisional_until"] == S.add_business_days(LIVE, cfg()["actions"]["auto_hold"]["confirm_business_days"])
    entry = next(e for e in S.audit() if e["event"] == "PROVISIONAL")
    assert (entry["actor"], entry["from_status"], entry["to_status"]) == ("agent (auto-hold)", "PROPOSED", "PROVISIONAL")
    with pytest.raises(ActionError) as err:  # only a PROPOSED hold can be auto-held, once
        S.provisional_hold(a["id"], "again", now=LIVE + 1)
    assert err.value.status == 409


def test_a_person_confirms_a_provisional_hold_and_it_then_runs_its_normal_term(g):
    a, _ = hold(now=LIVE)
    S.provisional_hold(a["id"], "Escalated with a high confidence score", now=LIVE)
    x = S.decide(a["id"], True, "Checked the trail: mule account", by="Priya", now=LIVE + 5)
    assert x["status"] == "APPROVED" and x["decided_by"] == "Priya"
    assert x["expires_ts"] == S.add_business_days(LIVE + 5, cfg()["actions"]["hold_expiry_business_days"])
    assert S.expire_due(now=x["provisional_until"] + 1) == 0       # confirmed: no early release
    entry = next(e for e in S.audit() if e["event"] == "APPROVED")
    assert entry["from_status"] == "PROVISIONAL"


def test_rejecting_a_provisional_hold_releases_it(g):
    a, _ = hold(now=LIVE)
    S.provisional_hold(a["id"], "Escalated with a high confidence score", now=LIVE)
    x = S.decide(a["id"], False, "Known payroll agent", by="Priya", now=LIVE + 5)
    assert (x["status"], x["released_at"]) == ("REJECTED", LIVE + 5)


def test_an_unconfirmed_provisional_hold_is_released_after_one_business_day(g):
    a, _ = hold(now=LIVE)
    p = S.provisional_hold(a["id"], "Escalated with a high confidence score", now=LIVE)
    assert S.expire_due(now=p["provisional_until"] - 1) == 0
    assert S.expire_due(now=p["provisional_until"]) == 1
    x = S.get(a["id"])
    assert (x["status"], x["released_at"]) == ("EXPIRED", p["provisional_until"])
    entry = next(e for e in S.audit() if e["event"] == "AUTO_RELEASED")
    assert "not confirmed" in entry["reason"] and entry["from_status"] == "PROVISIONAL"


def test_closing_a_case_releases_its_provisional_holds(g):
    a, _ = hold(now=LIVE)
    S.provisional_hold(a["id"], "Escalated with a high confidence score", now=LIVE)
    c, _ = S.propose("ZZ-C001", "CLOSE", why="Legitimate explanations", now=LIVE + 1)
    S.decide(c["id"], True, "Supplier invoices verified", now=LIVE + 2)
    assert (S.get(a["id"])["status"], S.get(a["id"])["released_at"]) == ("EXPIRED", LIVE + 2)


def test_a_release_ends_a_provisional_hold(g):
    a, _ = hold(now=LIVE)
    S.provisional_hold(a["id"], "Escalated with a high confidence score", now=LIVE)
    r, _ = S.propose("ZZ-C001", "RELEASE", account="ACC1", why="Customer showed the invoice", now=LIVE + 1)
    S.decide(r["id"], True, "Invoice checked with the supplier", now=LIVE + 2)
    assert S.get(a["id"])["status"] == "EXPIRED"


def test_the_case_history_says_whether_a_person_or_the_clock_already_acted(g):
    assert S.hold_history("ZZ-C001") == {"verdict": None, "decided_holds": 0}
    a, _ = hold(now=LIVE)
    assert S.hold_history("ZZ-C001")["decided_holds"] == 0          # a proposal is not a decision
    S.decide(a["id"], False, "Known payroll agent", now=LIVE)
    assert S.hold_history("ZZ-C001")["decided_holds"] == 1
    c, _ = S.propose("ZZ-C001", "CLOSE", why="Legitimate explanations", now=LIVE + 1)
    S.decide(c["id"], True, "Supplier invoices verified", now=LIVE + 2)
    assert S.hold_history("ZZ-C001")["verdict"] == "CLOSE"


def test_a_person_deciding_a_hold_stops_any_later_auto_hold_inside_the_write(g):
    """The race: the policy read the history, then a person rejected one hold before the next was auto-held."""
    a, _ = hold(now=LIVE)
    b, _ = S.propose("ZZ-C001", "HOLD", account="ACC2", hold_amount=2000.0, now=LIVE)
    S.decide(a["id"], False, "Known payroll agent", now=LIVE + 1)
    with pytest.raises(ActionError, match="already decided"):
        S.provisional_hold(b["id"], "Escalated with a high confidence score", now=LIVE + 2)
    assert S.get(b["id"])["status"] == "PROPOSED"


def test_one_auto_hold_run_places_all_its_holds_but_an_analyst_verdict_stops_it(g):
    a, _ = hold(now=LIVE)
    b, _ = S.propose("ZZ-C001", "HOLD", account="ACC2", hold_amount=2000.0, now=LIVE)
    S.provisional_hold(a["id"], "r", now=LIVE + 5)
    assert S.provisional_hold(b["id"], "r", now=LIVE + 5)["status"] == "PROVISIONAL"   # same run, same now
    c, _ = S.propose("ZZ-C002", "HOLD", account="ACC3", hold_amount=2000.0, now=LIVE)
    v, _ = S.propose("ZZ-C002", "FILE_STR", why="The evidence points to laundering", now=LIVE)
    S.decide(v["id"], True, "Confirmed mule chain", now=LIVE + 1)
    with pytest.raises(ActionError):
        S.provisional_hold(c["id"], "r", now=LIVE + 6)


def test_a_lapsed_auto_hold_cannot_be_confirmed_late(g):
    a, _ = hold(now=LIVE)
    p = S.provisional_hold(a["id"], "r", now=LIVE)
    with pytest.raises(ActionError) as err:
        S.decide(a["id"], True, "Confirming after the deadline", now=p["provisional_until"] + 1)
    assert err.value.status == 409 and S.get(a["id"])["status"] == "EXPIRED"


def test_a_reupload_releases_provisional_holds(g):
    a, _ = hold(now=LIVE)
    S.provisional_hold(a["id"], "r", now=LIVE)
    assert S.retire_batch("ZZ-C", "The file was uploaded again", now=LIVE + 1)["withdrawn"] == 1
    x = S.get(a["id"])
    assert (x["status"], x["released_at"]) == ("EXPIRED", LIVE + 1)


def test_nearest_escalated_follows_payments_either_way_and_takes_the_shortest(g):
    """Undirected and shortest, on every FalkorDB we run on (v4.22 has no undirected shortestPath())."""
    rw("""CREATE (a:Account {id: 'A'}), (m:Account {id: 'M'}), (x:Account {id: 'X'}), (p:Account {id: 'P'}),
              (q:Account {id: 'Q'}), (far:Account {id: 'F'}), (y:Account {id: 'Y'}), (z:Account {id: 'Z'}),
              (a)-[:TRANSFER {tx_id: 'm1', ts: 1}]->(m), (x)-[:TRANSFER {tx_id: 'm2', ts: 2}]->(m),
              (a)-[:TRANSFER {tx_id: 'p1', ts: 3}]->(p), (p)-[:TRANSFER {tx_id: 'p2', ts: 4}]->(q),
              (q)-[:TRANSFER {tx_id: 'p3', ts: 5}]->(x),
              (far)-[:TRANSFER {tx_id: 'f1', ts: 6}]->(y), (y)-[:TRANSFER {tx_id: 'f2', ts: 7}]->(z),
              (z)-[:TRANSFER {tx_id: 'f3', ts: 8}]->(q)""")
    for cid, acc in (("ZZ-C001", "X"), ("ZZ-C002", "F")):
        rw("MATCH (x:Account {id: $a}) CREATE (:Case {id: $id, disposition: 'ESCALATE', created_ts: 100})-[:ABOUT]->(x)",
           {"a": acc, "id": cid})
    near = ro(Q.NEAREST_ESCALATED, {"id": "A", "batch_prefix": "YY-C", "t0": 200}).rows
    assert [(r["account"], r["hops"]) for r in near] == [("X", 2)]   # A->M<-X beats A->P->Q->X; F is 5 hops away
