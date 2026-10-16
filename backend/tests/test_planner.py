"""Hold planner: hold the traced case money that is actually in an account; never a hub.

A hold is a lien on an amount (RBI draft, Sep 2026): min(money at rest in the account, case money traced into
it). The headline is the money frozen now. Money on the case's routes is reported apart, as an assumption.
"""
import pytest

from flowtrace.actions import planner as P
from flowtrace.graph import trace as trace_mod
from flowtrace.settings import cfg
from tests.fakegraph import World

DAY = 86_400_000
H = 3_600_000
T0 = 20 * DAY


def picked(holds):
    return [(h["account"], h["hold"], h["on_routes"]) for h in holds]


# --- choose_holds (pure) ----------------------------------------------------------------------

def test_a_collector_with_nothing_in_it_holds_nothing_so_the_mules_are_held():
    supply = {f"M{i}": 2000.0 for i in range(5)} | {"C": 0.0}
    holds, cut = P.choose_holds(supply, {f"M{i}": {"C"} for i in range(5)}, exits={"C"}, cap=3, min_usd=1000)
    assert picked(holds) == [("M0", 2000.0, 0.0), ("M1", 2000.0, 0.0), ("M2", 2000.0, 0.0)]
    assert cut == 1  # one account-wide freeze (C) would cut every route; a lien on C's 0 freezes nothing


def test_the_cap_keeps_the_largest_amounts():
    supply = {"L1": 5000.0, "L2": 4000.0, "L3": 3000.0, "L4": 2000.0, "L5": 1000.0}
    holds, cut = P.choose_holds(supply, {}, exits=set(), cap=3, min_usd=1000)
    assert [h["account"] for h in holds] == ["L1", "L2", "L3"] and cut == 5


def test_amounts_below_the_minimum_are_never_held():
    holds, _ = P.choose_holds({"A": 5000.0, "B": 300.0}, {}, exits=set(), cap=3, min_usd=1000)
    assert [h["account"] for h in holds] == ["A"]


def test_equal_amounts_prefer_the_account_that_also_sits_on_the_routes():
    """A and B hold the same; A's money can only leave through B, so B also covers it (an assumption)."""
    holds, _ = P.choose_holds({"A": 3000.0, "B": 3000.0}, {"A": {"B"}}, exits={"B"}, cap=1, min_usd=1000)
    assert picked(holds) == [("B", 3000.0, 3000.0)]


def test_parallel_collectors_with_nothing_in_them_leave_the_mules_to_hold():
    supply = {"M0": 5000.0, "M1": 5000.0, "M2": 5000.0, "A": 0.0, "B": 0.0}
    holds, cut = P.choose_holds(supply, {m: {"A", "B"} for m in ("M0", "M1", "M2")}, exits={"A", "B"}, cap=3, min_usd=1000)
    assert [h["account"] for h in holds] == ["M0", "M1", "M2"] and cut == 2


def test_money_in_a_closed_loop_is_held_where_it_sits():
    holds, _ = P.choose_holds({"A": 5000.0, "B": 0.0}, {"A": {"B"}, "B": {"A"}}, exits=set(), cap=3, min_usd=1000)
    assert [h["account"] for h in holds] == ["A"]


def test_nothing_at_rest_means_no_holds():
    assert P.choose_holds({"A": 0.0}, {}, exits=set(), cap=3, min_usd=1000) == ([], 0)


# --- plan_holds on a fake graph -----------------------------------------------------------------

def mule_world(cur: str = "Euro", mixed: bool = False, extra=()) -> World:
    """X splits 60k to 3 mules. M0 sends almost all of it on in cash, M1 keeps it, M2 pays most of it to a
    busy merchant (a hub)."""
    def e(tx, s, d, ts, amt, fmt="ACH", c=cur):
        return {"tx_id": tx, "src": s, "dst": d, "ts": ts, "amount": amt, "fmt": fmt, "cur": c}
    edges = [e("IN1", "SRC", "X", T0 + 1 * H, 60000.0)]
    for i in range(3):
        edges.append(e(f"S{i}", "X", f"M{i}", T0 + 2 * H, 20000.0, c="US Dollar" if mixed and i == 2 else cur))
    edges.append(e("F0", "M0", "Z", T0 + 4 * H, 19500.0, fmt="Cash"))
    edges.append(e("Z1", "Z", "Y", T0 + 6 * H, 19000.0))  # beyond the cash exit: not plannable
    edges.append(e("P2", "M2", "HUB", T0 + 5 * H, 15000.0))
    for i in range(600):
        edges.append(e(f"H{i}", f"cust{i}", "HUB", T0 - 2 * DAY + i * 1000, 50.0))
    edges += [e(*x) for x in extra]
    return World(edges, {})


CASE = {"case_id": "BX-C001", "primary": "X", "accounts": ["X"], "t0": T0, "t1": T0 + DAY, "tx_ids": ["S0", "S1", "S2"], "ml_score": 0.9,
        "edges": [{"tx_id": f"S{i}", "src": "X", "dst": f"M{i}", "ts": T0 + 2 * H, "amount": 20000.0, "fmt": "ACH"} for i in range(3)]}


def result(action: str, evidence=()) -> dict:
    return {"disposition": {"action": action}, "evidence": list(evidence)}


@pytest.fixture
def world(monkeypatch):
    def use(w: World):
        monkeypatch.setattr(P, "ro", w.ro)
        monkeypatch.setattr(trace_mod, "ro", w.ro)
        monkeypatch.setattr(P, "fx_rates", lambda: {"US Dollar": 1.0, "Euro": 0.85})
    return use


def test_plan_holds_the_mules_with_money_and_never_the_hub(world):
    world(mule_world())
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert [h["account"] for h in plan["holds"]] == ["M1", "M2"]
    assert "HUB" in plan["skipped_hubs"]


def test_the_headline_is_the_money_frozen_now(world):
    world(mule_world())
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert plan["frozen_now"] == pytest.approx(20000 + 5000)       # what the two liens freeze today
    assert plan["in_motion"] == pytest.approx(500 + 20000 + 5000)   # traced case money still at rest anywhere
    assert plan["on_routes"] == 0 and plan["already_out"] == pytest.approx(19500 + 15000)
    m1 = plan["holds"][0]
    assert (m1["hold"], m1["on_routes"]) == (pytest.approx(20000), 0)
    assert m1["expiry_days"] == cfg()["actions"]["hold_expiry_business_days"]


def test_a_case_account_holds_only_the_case_money_traced_into_it(world):
    """A busy company account: a million of ordinary receipts sits there, but only the flagged 30k is case money."""
    world(mule_world(extra=[("BIG", "CORP", "X", T0 + 1 * H, 1_000_000.0), ("FLAG", "SRC2", "X", T0 + 1 * H, 30000.0)]))
    case = {**CASE, "edges": CASE["edges"] + [{"tx_id": "FLAG", "src": "SRC2", "dst": "X", "ts": T0 + 1 * H,
                                               "amount": 30000.0, "fmt": "ACH"}]}
    plan = P.plan_holds(case, cfg(), result("ESCALATE"))
    x = next(h for h in plan["holds"] if h["account"] == "X")
    assert x["hold"] == pytest.approx(30000)  # not the million at rest


def test_a_case_account_with_no_case_money_in_it_is_not_held(world):
    world(mule_world(extra=[("BIG", "CORP", "X", T0 + 1 * H, 1_000_000.0)]))
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert "X" not in [h["account"] for h in plan["holds"]]
    assert plan["in_motion"] == pytest.approx(25500)


def test_hold_cites_evidence_and_trail_payments(world):
    world(mule_world())
    ev = [{"id": "E2", "kind": "support", "accounts": ["M1"]}, {"id": "E3", "kind": "counter", "accounts": ["X"]}]
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE", ev))
    m1 = plan["holds"][0]
    assert m1["evidence_ids"] == ["E2"] and "S1" in m1["tx_ids"] and m1["why"]


def test_review_gets_no_holds_only_a_senior_look(world):
    """REVIEW is mixed evidence, below reasonable grounds to suspect: holds only after ESCALATE."""
    world(mule_world())
    plan = P.plan_holds(CASE, cfg(), result("REVIEW"))
    assert plan["holds"] == [] and plan["case_action"]["type"] == "ESCALATE" and plan["note"]


def test_closed_case_gets_no_holds(world):
    world(mule_world())
    plan = P.plan_holds(CASE, cfg(), result("CLOSE"))
    assert plan["holds"] == [] and plan["case_action"]["type"] == "CLOSE"
    assert plan["in_motion"] > 0


def test_escalated_case_proposes_filing_an_str(world):
    world(mule_world())
    assert P.plan_holds(CASE, cfg(), result("ESCALATE"))["case_action"]["type"] == "FILE_STR"


def test_busy_only_after_the_decision_is_not_a_hub(world):
    """Hub degrees count only payments before the decision time."""
    later = [(f"L{i}", f"late{i}", "M1", T0 + 3 * DAY + i, 10.0) for i in range(600)]
    world(mule_world(extra=later))
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert "M1" in [h["account"] for h in plan["holds"]] and "M1" not in plan["skipped_hubs"]


def test_single_currency_plan_carries_its_rate(world):
    world(mule_world())
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert (plan["currency"], plan["rate"]) == ("Euro", 0.85)


def test_mixed_currency_plan_is_in_us_dollars(world):
    world(mule_world(mixed=True))
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert (plan["currency"], plan["rate"]) == ("US Dollar", 1.0)


def test_effect_of_the_open_holds_is_recomputed_from_the_plan(world):
    world(mule_world())
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert P.effect(plan, ["M1", "M2"]) == {"frozen": pytest.approx(25000), "on_routes": 0}
    assert P.effect(plan, ["M1"]) == {"frozen": pytest.approx(20000), "on_routes": 0}
    assert P.effect(plan, []) == {"frozen": 0, "on_routes": 0}
