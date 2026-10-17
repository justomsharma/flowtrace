"""End-to-end agent tests on a fake graph: a salary split should close, a mule fan-out should escalate."""
import pytest

from flowtrace.agent import context as ctx_mod
from flowtrace.agent.investigate import investigate, new_context
from flowtrace.graph import trace as trace_mod
from flowtrace.settings import cfg
from tests.fakegraph import World

DAY = 86_400_000
H = 3_600_000
T0 = 20 * DAY  # alert day


def salary_world() -> World:
    """Employee E is paid weekly by Corp C, then pays rent/EMI/card to busy receivers it has paid for weeks."""
    edges, n = [], 0

    def add(src, dst, ts, amt, fmt):
        nonlocal n
        n += 1
        edges.append({"tx_id": f"T{n}", "src": src, "dst": dst, "ts": ts, "amount": amt, "fmt": fmt})

    for wk in range(4):
        day = T0 - (3 - wk) * 7 * DAY
        add("C", "E", day + 9 * H, 2000.0, "ACH")
        for i in range(30):                       # payroll fan-out to other staff
            add("C", f"staff{i}", day + 9 * H, 1900.0 + i, "ACH")
        add("E", "LENDER", day + 11 * H, 550.0, "ACH")
        add("E", "LANDLORD", day + 12 * H, 800.0, "Cheque")
        add("E", "CARD", day + 13 * H, 200.0 + 15 * wk, "Credit Card")
        add("E", "GROCER", day + 14 * H, 90.0 + 5 * wk, "Credit Card")
        add("E", "SIP", day + 15 * H, 150.0, "ACH")
    for hub in ("LENDER", "LANDLORD", "CARD", "GROCER", "SIP"):
        for i in range(250):                      # receivers collect from many customers
            add(f"cust{hub}{i}", hub, T0 - 5 * DAY + i * 1000, 100.0, "ACH")
    acc = {"C": {"entity_type": "Corporation", "entity_id": "c"}, "E": {"entity_type": "Sole Proprietorship", "entity_id": "e"}}
    return World(edges, acc)


def mule_world() -> World:
    """X receives a large sum, splits it evenly to 6 never-seen accounts, which forward to Z in cash within hours."""
    edges = [{"tx_id": "IN1", "src": "SRC", "dst": "X", "ts": T0 + 1 * H, "amount": 60000.0, "fmt": "Wire"}]
    for i in range(6):
        edges.append({"tx_id": f"S{i}", "src": "X", "dst": f"M{i}", "ts": T0 + 2 * H + i * 60_000, "amount": 9900.0, "fmt": "ACH"})
        edges.append({"tx_id": f"F{i}", "src": f"M{i}", "dst": "Z", "ts": T0 + 4 * H + i * 60_000, "amount": 9800.0, "fmt": "Cash"})
    edges.append({"tx_id": "OLD", "src": "X", "dst": "UTIL", "ts": T0 - 6 * DAY, "amount": 50.0, "fmt": "ACH"})
    return World(edges, {"X": {"entity_type": "Corporation", "entity_id": "x"}})


def run(world: World, case: dict, monkeypatch) -> dict:
    monkeypatch.setattr(ctx_mod, "ro", world.ro)
    monkeypatch.setattr(trace_mod, "ro", world.ro)
    ctx = new_context(case, cfg(), emit=lambda e: None)
    return investigate(ctx, policy_name="heuristic", use_llm_report=False, save_memory=False)


def test_salary_split_is_closed(monkeypatch):
    w = salary_world()
    day = T0
    alert = [e for e in w.edges if e["src"] == "E" and day <= e["ts"] < day + DAY]
    case = {"case_id": "SAL", "primary": "E", "accounts": ["E"] + sorted({e["dst"] for e in alert}),
            "tx_ids": [e["tx_id"] for e in alert], "edges": alert, "t0": day, "t1": day + DAY,
            "ml_score": 0.32, "shape": {"typology": "FAN-OUT", "confidence": 0.9}}
    out = run(w, case, monkeypatch)
    codes = {e["code"] for e in out["evidence"]}
    assert "ESTABLISHED_COUNTERPARTIES" in codes
    assert "SALARY_DISTRIBUTION" in codes
    assert out["disposition"]["action"] == "CLOSE", out["disposition"]


def test_mule_fan_out_is_escalated(monkeypatch):
    w = mule_world()
    alert = [e for e in w.edges if e["src"] == "X" and e["ts"] >= T0]
    case = {"case_id": "MULE", "primary": "X", "accounts": ["X"] + [f"M{i}" for i in range(6)],
            "tx_ids": [e["tx_id"] for e in alert], "edges": alert, "t0": T0, "t1": T0 + DAY,
            "ml_score": 0.81, "shape": {"typology": "FAN-OUT", "confidence": 0.9}}
    out = run(w, case, monkeypatch)
    codes = {e["code"] for e in out["evidence"]}
    assert {"NEW_COUNTERPARTIES", "EQUAL_SPLITS", "FAST_ONWARD_TRANSFER", "CASH_OR_CRYPTO_EXIT"} <= codes, codes
    assert out["disposition"]["action"] == "ESCALATE", out["disposition"]
    assert out["report"]["validation"]["unsupported"] == 0


def test_tool_rejects_unknown_account(monkeypatch):
    from flowtrace.agent.tools import ToolError, run_tool
    w = mule_world()
    monkeypatch.setattr(ctx_mod, "ro", w.ro)
    ctx = new_context({"case_id": "c", "primary": "X", "accounts": ["X"], "tx_ids": [], "t0": T0, "t1": T0 + DAY,
                       "ml_score": 0.5}, cfg(), emit=lambda e: None)
    with pytest.raises(ToolError):
        run_tool(ctx, "account_profile", {"account": "SOMEONE_ELSE"})
    with pytest.raises(ToolError):
        run_tool(ctx, "drop_database", {})


def test_stats_report_the_policy_that_actually_ran(monkeypatch):
    from flowtrace.agent import policy as policy_mod
    monkeypatch.setattr(policy_mod, "structured", lambda *a, **k: (None, {"error": "no API key"}))
    w = mule_world()
    monkeypatch.setattr(ctx_mod, "ro", w.ro)
    monkeypatch.setattr(trace_mod, "ro", w.ro)
    alert = [e for e in w.edges if e["src"] == "X" and e["ts"] >= T0]
    case = {"case_id": "MULE", "primary": "X", "accounts": ["X"], "tx_ids": [e["tx_id"] for e in alert], "edges": alert,
            "t0": T0, "t1": T0 + DAY, "ml_score": 0.81, "shape": {"typology": "FAN-OUT", "confidence": 0.9}}
    out = investigate(new_context(case, cfg(), emit=lambda e: None), policy_name="llm", use_llm_report=False,
                      save_memory=False)
    assert out["stats"]["policy"] == "heuristic fallback"


def test_case_memory_ignores_its_own_and_same_batch_cases(monkeypatch):
    """Cases of one upload are investigated in parallel: none of them is an "earlier case" for another."""
    from flowtrace.agent.tools import run_tool
    from flowtrace.graph import queries as Q
    seen = []
    w = mule_world()

    def ro(c, p, **k):
        seen.append((c, p))
        return w.ro(c, p)

    monkeypatch.setattr(ctx_mod, "ro", ro)
    ctx = new_context({"case_id": "B1-C001", "primary": "X", "accounts": ["X"], "tx_ids": [], "t0": T0, "t1": T0 + DAY,
                       "ml_score": 0.5}, cfg(), emit=lambda e: None)
    run_tool(ctx, "case_memory", {"account": "X"})
    for q in (Q.PRIOR_CASES, Q.NEAREST_ESCALATED):
        assert "NOT c.id STARTS WITH $batch_prefix" in q
        assert [p["batch_prefix"] for c, p in seen if c == q] == ["B1-C"]
        # Only cases closed before this case's window starts are history: a same-day case from another
        # upload is not an earlier case (it leaked into the eval as a prior escalation).
        assert "c.created_ts < $t0" in q
        assert [p["t0"] for c, p in seen if c == q] == [T0]


def test_stats_report_llm_time_and_the_report_asks_for_low_effort(monkeypatch):
    from flowtrace.agent import policy as policy_mod, report as report_mod
    monkeypatch.setattr(policy_mod, "structured", lambda *a, **k: (None, {"error": "x", "ms": 1500}))
    efforts = []

    def report_llm(system, user, schema, **k):
        efforts.append(k.get("effort"))
        return None, {"error": "y", "ms": 2500}
    monkeypatch.setattr(report_mod, "structured", report_llm)
    w = mule_world()
    monkeypatch.setattr(ctx_mod, "ro", w.ro)
    monkeypatch.setattr(trace_mod, "ro", w.ro)
    alert = [e for e in w.edges if e["src"] == "X" and e["ts"] >= T0]
    case = {"case_id": "MULE", "primary": "X", "accounts": ["X"], "tx_ids": [e["tx_id"] for e in alert], "edges": alert,
            "t0": T0, "t1": T0 + DAY, "ml_score": 0.81, "shape": {"typology": "FAN-OUT", "confidence": 0.9}}
    out = investigate(new_context(case, cfg(), emit=lambda e: None), policy_name="llm", save_memory=False)
    steps = out["stats"]["llm_calls"] - 1
    assert out["stats"]["llm_seconds"] == round((steps * 1500 + 2500) / 1000, 2)
    assert out["stats"]["llm_errors"] == ["x"] * steps + ["y"]
    assert efforts == ["low"]   # medium cost ~7 s per case for no gain in validity (measured 2026-10-16)


def test_one_busy_receiver_does_not_count_for_all():
    """C005's shape: B got $11.8K and sent $1.27M on within hours; the other receivers sent nothing on."""
    from flowtrace.agent.tools import fast_forward_share

    hop1 = [{"tx_id": "B_IN", "src": "X", "dst": "B", "ts": T0 + 1 * H, "amount": 11_800.0}]
    hop1 += [{"tx_id": f"R{i}_IN", "src": "X", "dst": f"R{i}", "ts": T0 + 1 * H, "amount": 6_040.0} for i in range(5)]
    hop2 = [{"tx_id": f"B_OUT{i}", "src": "B", "dst": f"Y{i}", "ts": T0 + 3 * H, "amount": 127_000.0} for i in range(10)]
    share, fast = fast_forward_share(hop1, hop2, window_ms=24 * H)
    assert share == pytest.approx(11_800 / 42_000)          # about 28%, not 100%
    assert len(fast) == 10


def test_forwarded_share_counts_only_money_sent_after_it_arrived():
    from flowtrace.agent.tools import fast_forward_share

    hop1 = [{"tx_id": "IN", "src": "X", "dst": "M", "ts": T0 + 5 * H, "amount": 1_000.0}]
    hop2 = [{"tx_id": "EARLY", "src": "M", "dst": "Y", "ts": T0 + 1 * H, "amount": 900.0},
            {"tx_id": "LATE", "src": "M", "dst": "Y", "ts": T0 + 6 * H, "amount": 400.0}]
    share, fast = fast_forward_share(hop1, hop2, window_ms=24 * H)
    assert share == pytest.approx(0.4)
    assert [e["tx_id"] for e in fast] == ["LATE"]


def test_the_report_prompt_names_the_fiu_from_config(monkeypatch):
    from flowtrace.agent import policy as policy_mod, report as report_mod
    monkeypatch.setattr(policy_mod, "structured", lambda *a, **k: (None, {"error": "x"}))
    systems = []
    monkeypatch.setattr(report_mod, "structured", lambda system, *a, **k: systems.append(system) or (None, {"error": "y"}))
    w = mule_world()
    monkeypatch.setattr(ctx_mod, "ro", w.ro)
    monkeypatch.setattr(trace_mod, "ro", w.ro)
    alert = [e for e in w.edges if e["src"] == "X" and e["ts"] >= T0]
    case = {"case_id": "MULE", "primary": "X", "accounts": ["X"], "tx_ids": [e["tx_id"] for e in alert], "edges": alert,
            "t0": T0, "t1": T0 + DAY, "ml_score": 0.81, "shape": {"typology": "FAN-OUT", "confidence": 0.9}}
    rules = {**cfg(), "actions": {**cfg()["actions"], "str_fiu": "FIU-TEST"}}
    investigate(new_context(case, rules, emit=lambda e: None), policy_name="llm", save_memory=False)
    assert "FIU-TEST" in systems[0] and "FIU-IND" not in systems[0]
