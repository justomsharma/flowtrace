"""Hold planner: fewest holds that block the most case money still in the trail; never a hub."""
import pytest

from flowtrace.actions import planner as P
from flowtrace.graph import trace as trace_mod
from flowtrace.settings import cfg
from tests.fakegraph import World

DAY = 86_400_000
H = 3_600_000
T0 = 20 * DAY


# --- choose_holds (pure) ----------------------------------------------------------------------

def test_collector_chokepoint_beats_holding_each_mule():
    supply = {f"M{i}": 2000.0 for i in range(5)} | {"C": 0.0}
    routes = {f"M{i}": {"C"} for i in range(5)}
    holds, method, k = P.choose_holds(supply, routes, exits={"C"}, cap=3, min_usd=1000)
    assert holds == [("C", 10000.0)]
    assert (method, k) == ("min-cut", 1)


def test_cap_falls_back_to_greedy_by_money():
    supply = {"L1": 5000.0, "L2": 4000.0, "L3": 3000.0, "L4": 2000.0, "L5": 1000.0}
    holds, method, k = P.choose_holds(supply, {}, exits=set(), cap=3, min_usd=1000)
    assert holds == [("L1", 5000.0), ("L2", 4000.0), ("L3", 3000.0)]
    assert (method, k) == ("greedy", 5)


def test_holds_below_the_minimum_are_dropped():
    holds, _, _ = P.choose_holds({"A": 5000.0, "B": 300.0}, {}, exits=set(), cap=3, min_usd=1000)
    assert holds == [("A", 5000.0)]


def test_tie_prefers_the_account_where_money_already_sits():
    holds, _, _ = P.choose_holds({"A": 3000.0, "B": 0.0}, {"A": {"B"}}, exits={"B"}, cap=3, min_usd=1000)
    assert holds == [("A", 3000.0)]


def test_nothing_at_rest_means_no_holds():
    assert P.choose_holds({"A": 0.0}, {}, exits=set(), cap=3, min_usd=1000)[0] == []


# --- plan_holds on a fake graph -----------------------------------------------------------------

def mule_world(cur: str = "Euro", mixed: bool = False) -> World:
    """X takes 60k and splits it to 3 mules. M0 sends almost all of it on in cash, M1 keeps it,
    and M2 pays most of it to a busy merchant (a hub)."""
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
    assert all(h["account"] not in ("HUB", "Z", "Y") for h in plan["holds"])


def test_plan_reports_money_in_motion_already_out_and_blocked(world):
    world(mule_world())
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert plan["in_motion"] == pytest.approx(500 + 20000 + 5000)
    assert plan["already_out"] == pytest.approx(19500 + 15000)
    assert plan["blocked"] == pytest.approx(25000)
    assert plan["frozen_now"] == pytest.approx(25000)
    m1 = plan["holds"][0]
    assert m1["amount_blocked"] == pytest.approx(20000) and m1["frozen_now"] == pytest.approx(20000)
    assert m1["expiry_days"] == cfg()["actions"]["hold_expiry_business_days"]


def test_hold_cites_evidence_and_trail_payments(world):
    world(mule_world())
    ev = [{"id": "E2", "kind": "support", "accounts": ["M1"]}, {"id": "E3", "kind": "counter", "accounts": ["X"]}]
    plan = P.plan_holds(CASE, cfg(), result("REVIEW", ev))
    m1 = plan["holds"][0]
    assert m1["evidence_ids"] == ["E2"] and "S1" in m1["tx_ids"] and m1["why"]
    assert plan["case_action"]["type"] == "ESCALATE"


def test_closed_case_gets_no_holds(world):
    world(mule_world())
    plan = P.plan_holds(CASE, cfg(), result("CLOSE"))
    assert plan["holds"] == [] and plan["case_action"]["type"] == "CLOSE"
    assert plan["in_motion"] > 0


def test_escalated_case_proposes_filing_an_str(world):
    world(mule_world())
    assert P.plan_holds(CASE, cfg(), result("ESCALATE"))["case_action"]["type"] == "FILE_STR"


def test_single_currency_plan_carries_its_rate(world):
    world(mule_world())
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert (plan["currency"], plan["rate"]) == ("Euro", 0.85)


def test_mixed_currency_plan_is_in_us_dollars(world):
    world(mule_world(mixed=True))
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert (plan["currency"], plan["rate"]) == ("US Dollar", 1.0)


def test_money_in_a_closed_loop_can_still_leave():
    """A round trip with no exit seen: money can leave from any account in the loop, so it is held where it sits."""
    holds, method, _ = P.choose_holds({"A": 5000.0, "B": 0.0}, {"A": {"B"}, "B": {"A"}}, exits=set(), cap=3, min_usd=1000)
    assert holds == [("A", 5000.0)] and method == "min-cut"


def test_parallel_collectors_are_held_together():
    """Each mule pays both collectors: neither alone blocks anything, the pair blocks it all (review finding)."""
    supply = {"M0": 5000.0, "M1": 5000.0, "M2": 5000.0, "A": 0.0, "B": 0.0}
    routes = {m: {"A", "B"} for m in ("M0", "M1", "M2")}
    holds, method, k = P.choose_holds(supply, routes, exits={"A", "B"}, cap=3, min_usd=1000)
    assert sorted(holds) == [("A", 7500.0), ("B", 7500.0)]
    assert (method, k) == ("min-cut", 2)


def test_parallel_collectors_with_four_mules():
    supply = {f"M{i}": 2000.0 for i in range(4)}
    holds, _, _ = P.choose_holds(supply, {m: {"A", "B"} for m in supply}, exits={"A", "B"}, cap=3, min_usd=1000)
    assert sorted(holds) == [("A", 4000.0), ("B", 4000.0)]


def test_blocked_by_open_holds_is_recomputed_from_the_plan(world):
    world(mule_world())
    plan = P.plan_holds(CASE, cfg(), result("ESCALATE"))
    assert P.blocked_by(plan, ["M1", "M2"]) == pytest.approx(25000)
    assert P.blocked_by(plan, ["M1"]) == pytest.approx(20000)
    assert P.blocked_by(plan, []) == 0
