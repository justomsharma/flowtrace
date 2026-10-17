"""The case overview: roles, payment status, holds, confidence, timeline and currency, all from stored data."""
from flowtrace.api import case_view as V

RULES = {
    "ui": {"evidence_confidence": {"high": 0.5, "medium": 0.35}, "priority": {"high": 0.95, "medium": 0.8},
           "flow_max_accounts": 16},
    "cases": {"flag_probability": 0.5},
    "disposition": {"escalate_at": 0.55, "close_below": 0.25, "confidence_labels": {"high": 0.8, "medium": 0.6}},
    "crime_hypotheses": {"STACK": ["smuggling"]},
    "crime_by_indicator": {"PASS_THROUGH": ["money mule"]},
}
H = 3_600_000


def pay(tx, src, dst, amount, ts=0, fmt="ACH", **kw):
    return {"tx_id": tx, "src": src, "dst": dst, "amount": amount, "ts": ts, "fmt": fmt, **kw}


def case(edges, **kw):
    accts = sorted({a for e in edges for a in (e["src"], e["dst"])})
    return {"case_id": "BX-C001", "batch_id": "BX", "primary": accts[0], "accounts": accts, "edges": edges,
            "t0": min(e["ts"] for e in edges), "t1": max(e["ts"] for e in edges) + 1, "case_score": 0.97,
            "amount": sum(e["amount"] for e in edges), "shape": {"typology": "STACK", "confidence": 0.6},
            "tier": "today", **kw}


def result(graph_edges=(), evidence=(), action="ESCALATE", author="gpt-6-luna"):
    return {
        "graph": {"nodes": [], "edges": list(graph_edges)},
        "evidence": list(evidence),
        "disposition": {"action": action, "risk": 0.7, "confidence_label": "High",
                        "components": {"ml_raw": 0.99, "red_raw": 0.8, "shape_raw": 0.6, "legit_raw": 0.3,
                                       "model": 0.45, "red_flags": 0.28, "shape": 0.12, "legitimacy": -0.12}},
        "report": {"headline": "Money passed through three accounts", "author": author,
                   "executive_summary": {"text": "Fast onward movement.", "evidence_ids": []},
                   "next_steps": ["Ask for source of funds."], "str_narrative": "STR text."},
        "steps": [],
    }


def ev(id, kind, weight, tx_ids=(), accounts=(), code="X", claim="claim"):
    return {"id": id, "kind": kind, "code": code, "claim": claim, "weight": weight, "tx_ids": list(tx_ids),
            "accounts": list(accounts), "escalator": False}


CHAIN = [pay("t1", "A", "B", 100, ts=0), pay("t2", "B", "C", 90, ts=H)]


def nodes(flow):
    return {n["id"]: n for n in flow["nodes"]}


def test_roles_from_flow():
    f = V.flow(case(CHAIN), None, None, [], RULES)
    n = nodes(f)
    assert [n[a]["role"] for a in "ABC"] == ["Sender", "Intermediary", "Destination"]
    assert (n["A"]["amount"], n["B"]["amount"], n["C"]["amount"]) == (100, 100, 90)
    assert n["A"]["sent"] == 100 and n["B"]["received"] == 100 and n["B"]["sent"] == 90


def test_pure_cycle_is_all_intermediaries():
    loop = [pay("t1", "A", "B", 10), pay("t2", "B", "C", 10, ts=1), pay("t3", "C", "A", 10, ts=2)]
    f = V.flow(case(loop), None, None, [], RULES)
    assert {n["role"] for n in f["nodes"]} == {"Intermediary"}


def test_context_edges_excluded_and_traced_money_included():
    r = result(graph_edges=[pay("t1", "A", "B", 100, role="alert"), pay("h1", "Z", "A", 5, role="context"),
                            pay("t3", "C", "D", 80, ts=2 * H, role="trace")])
    f = V.flow(case(CHAIN), r, None, [], RULES)
    assert "Z" not in nodes(f) and "D" in nodes(f)
    assert nodes(f)["C"]["role"] == "Intermediary" and nodes(f)["D"]["role"] == "Destination"
    assert not nodes(f)["D"]["in_case"] and nodes(f)["A"]["in_case"]


def test_flagged_vs_trail_status():
    r = result(graph_edges=[pay("t3", "C", "D", 80, ts=2 * H, role="trace"), pay("t4", "C", "E", 5, ts=2 * H, role="trace")],
               evidence=[ev("E1", "support", 0.5, tx_ids=["t4"]), ev("E2", "counter", 0.3, tx_ids=["t3"])])
    f = V.flow(case(CHAIN), r, None, [], RULES)
    status = {p["tx_id"]: p["status"] for p in f["payments"]}
    assert status == {"t1": "flagged", "t2": "flagged", "t3": "trail", "t4": "flagged"}
    edge = {(e["src"], e["dst"]): e for e in f["edges"]}
    assert edge[("C", "D")]["status"] == "trail" and edge[("C", "E")]["status"] == "flagged"
    assert [p["tx_id"] for p in f["payments"]][-1] == "t1"  # newest first


def test_hold_account_downstream_is_in_flow_and_amber():
    plan = {"network": {"edges": [{"tx_id": "t9", "src": "C", "dst": "M", "amount": 70, "ts": 3 * H, "kind": "trail"},
                                  {"tx_id": "t8", "src": "C", "dst": "Q", "amount": 5, "ts": 3 * H, "kind": "trail"}]},
            "currency": "US Dollar", "rate": 1.0}
    actions = [{"id": "A1", "type": "HOLD", "status": "PROPOSED", "account": "M"},
               {"id": "A2", "type": "HOLD", "status": "REJECTED", "account": "Q"}]
    f = V.flow(case(CHAIN), result(), plan, actions, RULES)
    n = nodes(f)
    assert n["M"]["hold"] == "proposed" and n["M"]["role"] == "Destination"
    assert "Q" not in n  # a rejected hold is not on the map
    assert next(e for e in f["edges"] if e["dst"] == "M")["hold"] is True


def test_flow_is_capped_keeping_case_accounts():
    rules = {**RULES, "ui": {**RULES["ui"], "flow_max_accounts": 4}}
    r = result(graph_edges=[pay(f"x{i}", "C", f"D{i}", 10 + i, ts=2 * H, role="trace") for i in range(6)])
    f = V.flow(case(CHAIN), r, None, [], rules)
    ids = set(nodes(f))
    assert len(ids) == 4 and {"A", "B", "C"} <= ids and "D5" in ids  # the largest traced payment stays
    assert f["hidden_accounts"] == 5


def test_confidence_thresholds_from_config():
    assert [V.confidence(w, RULES) for w in (0.7, 0.5, 0.35, 0.34)] == ["High", "High", "Medium", "Low"]
    assert [V.priority(s, RULES) for s in (0.99, 0.95, 0.8, 0.5)] == ["High", "High", "Medium", "Low"]


def test_timeline_hourly_vs_daily():
    t = V.timeline([{"ts": 0, "amount": 10, "status": "flagged"}, {"ts": H + 5, "amount": 4, "status": "trail"},
                    {"ts": H + 9, "amount": 1, "status": "flagged"}])
    assert t["bucket"] == "hour"
    assert t["bins"] == [{"t": 0, "flagged": 10, "other": 0, "n": 1}, {"t": H, "flagged": 1, "other": 4, "n": 2}]
    d = V.timeline([{"ts": 0, "amount": 1, "status": "flagged"}, {"ts": 72 * H, "amount": 2, "status": "normal"}])
    assert d["bucket"] == "day" and [b["t"] for b in d["bins"]] == [0, 24 * H, 48 * H, 72 * H]  # empty days kept
    assert d["bins"][1] == {"t": 24 * H, "flagged": 0, "other": 0, "n": 0}
    assert V.timeline([]) == {"bucket": "hour", "bins": []}


def test_uninvestigated_case_renders():
    o = V.build_overview(case(CHAIN), None, None, [], None, False, RULES)
    assert o["status"]["state"] == "not_started"
    assert o["findings"] == [] and o["explanations"] == [] and o["summary"] is None and o["indicators"] == []
    assert o["kpis"]["payments"] == 2 and o["kpis"]["accounts"] == 3 and o["kpis"]["suspicious_patterns"] is None
    assert o["priority"] == "High" and o["currency"] == "US Dollar"


def test_investigated_case_findings_summary_indicators():
    r = result(evidence=[ev("E1", "support", 0.55, code="FAST_ONWARD_TRANSFER", claim="Passed on in 2 hours."),
                         ev("E2", "support", 0.2), ev("E3", "counter", 0.3, claim="Known counterparty."),
                         ev("E4", "context", 0.0)], author="template")
    o = V.build_overview(case(CHAIN), r, None, [], None, False, RULES)
    assert [(f["id"], f["confidence"]) for f in o["findings"]] == [("E1", "High"), ("E2", "Low")]
    assert [e["id"] for e in o["explanations"]] == ["E3"]
    assert o["kpis"]["suspicious_patterns"] == 2
    assert {k: v for k, v in o["summary"].items() if k != "points"} == {
        "headline": "Money passed through three accounts", "text": "Fast onward movement.", "by_ai": False, "totals_note": None}
    # A result saved without bullet points gets the strongest reasons each way, then the decision.
    assert o["summary"]["points"] == ["Receiving accounts forwarded the funds within 24 hours.", "claim.", "Known counterparty.",
                                      "Recommend escalating to a senior analyst."]
    assert [f["brief"] for f in o["findings"]][0] == "Receiving accounts forwarded the funds within 24 hours"
    assert o["names"]["A"] == "Account A" and o["findings"][0]["tag"] in ("Strong sign", "Moderate sign", "Common in normal accounts", "")
    assert [(i["key"], i["value"]) for i in o["indicators"]] == [("model", 0.99), ("red_flags", 0.8), ("shape", 0.6), ("legitimacy", 0.3)]
    assert o["status"]["state"] == "escalate"


def test_every_page_uses_us_dollars():
    """One currency everywhere: the graph stores dollars and the investigation writes dollars."""
    euro = [pay("t1", "A", "B", 100, cur="Euro"), pay("t2", "B", "C", 90, ts=H, cur="Euro")]
    assert V.case_currency(case(euro), None) == "US Dollar"
    assert V.case_currency(case(CHAIN), {"currency": "Brazil Real"}) == "US Dollar"
    assert V.case_rate("US Dollar", {"currency": "Euro", "rate": 0.9}) == 1.0


def test_status_from_job_and_disposition():
    c = case(CHAIN)
    st = lambda r, job, failed: V.build_overview(c, r, None, [], job, failed, RULES)["status"]["state"]
    assert st(None, {"id": "J", "status": "queued"}, False) == "queued"
    assert st(None, {"id": "J", "status": "running"}, False) == "investigating"
    assert st(None, None, True) == "failed"
    assert st(result(action="REVIEW"), None, False) == "review"
    assert st(result(action="CLOSE"), {"id": "J", "status": "running"}, False) == "investigating"  # a re-run


def test_distribution_by_receiving_account():
    o = V.build_overview(case(CHAIN), None, None, [], None, False, RULES)
    assert o["distribution"] == [{"account": "B", "amount": 100, "share": 100 / 190},
                                 {"account": "C", "amount": 90, "share": 90 / 190}]


# --- STR draft and endpoints -----------------------------------------------------------------

def full_result():
    r = result(evidence=[ev("E1", "support", 0.55, claim="Passed on within 2 hours."),
                         ev("E2", "counter", 0.3, claim="Known counterparty for 6 days.")])
    r["report"] |= {"subject_profile": {"text": "Account A, a corporation.", "evidence_ids": []},
                    "money_trail": [{"text": "A paid B, B paid C.", "evidence_ids": []}],
                    "typology_assessment": {"text": "Layered relay.", "evidence_ids": []},
                    "red_flags": [], "mitigating_factors": [], "unknowns": ["KYC not available."]}
    r["crime_hypotheses"] = [{"crime": "smuggling", "basis": "STACK shape"}]
    return r


def test_report_md_has_sections():
    actions = [{"id": "A1", "type": "HOLD", "status": "PROPOSED", "account": "B", "hold_amount": 90.0,
                "currency": "Euro", "rate": 0.5, "proposed_by": "agent"},
               {"id": "A2", "type": "FILE_STR", "status": "APPROVED", "decided_by": "Asha", "reason": "Clear layering",
                "proposed_by": "agent"}]
    md = V.str_markdown(case(CHAIN), full_result(), None, actions)
    for part in ("# STR draft: Case 1", "Case reference: BX-C001", "Money passed through three accounts", "## Red flags",
                 "Passed on within 2 hours.", "## Legitimate explanations", "Known counterparty for 6 days.",
                 "## Proposed actions", "Hold account B: $90 (waiting for a decision)",
                 "File an STR: approved by Asha (Clear layering)", "## STR narrative", "STR text.",
                 "smuggling", "KYC not available.", "| From | To |"):
        assert part in md, part
    assert "₹" not in md


def test_report_md_marks_rules_reports():
    r = full_result()
    r["report"]["author"] = "template"
    assert "written by the rule-based fallback" in V.str_markdown(case(CHAIN), r, None, [])


def test_overview_and_report_endpoints(monkeypatch):
    from fastapi.testclient import TestClient
    from flowtrace.api import main as M
    client = TestClient(M.app)
    monkeypatch.setattr(M, "CASES", {"BX-C001": case(CHAIN)})
    monkeypatch.setattr(M, "RESULTS", {})
    monkeypatch.setattr(M, "BATCHES", {})
    monkeypatch.setattr(M.store, "load_plan", lambda cid: None)
    monkeypatch.setattr(M.service, "list_actions", lambda **kw: [])
    assert client.get("/api/cases/BX-C999/overview").status_code == 404
    o = client.get("/api/cases/BX-C001/overview").json()
    assert o["status"]["state"] == "not_started" and len(o["flow"]["nodes"]) == 3
    assert client.get("/api/cases/BX-C001/report.md").status_code == 409

    M.RESULTS["BX-C001"] = full_result()
    r = client.get("/api/cases/BX-C001/report.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert 'filename="BX-C001-STR-draft.md"' in r.headers["content-disposition"]
    assert client.get("/api/cases/BX-C001/overview").json()["status"]["state"] == "escalate"


def test_overview_survives_the_graph_being_down(monkeypatch):
    from fastapi.testclient import TestClient
    from flowtrace.api import main as M
    monkeypatch.setattr(M, "CASES", {"BX-C001": case(CHAIN)})
    monkeypatch.setattr(M, "RESULTS", {})
    monkeypatch.setattr(M.store, "load_plan", lambda cid: None)
    def down(**kw):
        raise ConnectionError("graph down")
    monkeypatch.setattr(M.service, "list_actions", down)
    assert TestClient(M.app).get("/api/cases/BX-C001/overview").status_code == 200


def test_batch_cases_show_us_dollars(monkeypatch):
    from fastapi.testclient import TestClient
    from flowtrace.api import main as M
    euro = case([pay("t1", "A", "B", 100, cur="Euro")])
    monkeypatch.setattr(M, "CASES", {"BX-C001": euro})
    monkeypatch.setattr(M, "RESULTS", {})
    monkeypatch.setattr(M, "BATCHES", {"BX": {"batch_id": "BX", "cases": [{"case_id": "BX-C001", "amount": 100}]}})
    monkeypatch.setattr(M, "top_cases", lambda bid: [])
    monkeypatch.setattr(V, "fx_rate", lambda cur: 0.9)
    c = TestClient(M.app).get("/api/batches/BX").json()["cases"][0]
    assert (c["currency"], c["rate"]) == ("US Dollar", 1.0)


def test_overview_and_report_never_write(monkeypatch):
    """Listing actions must not run the expiry write: the case page polls this endpoint."""
    from fastapi.testclient import TestClient
    from flowtrace.api import main as M
    monkeypatch.setattr(M, "CASES", {"BX-C001": case(CHAIN)})
    monkeypatch.setattr(M, "RESULTS", {"BX-C001": full_result()})
    monkeypatch.setattr(M.store, "load_plan", lambda cid: None)
    calls = []
    monkeypatch.setattr(M.service, "list_actions", lambda **kw: calls.append(kw) or [])
    client = TestClient(M.app)
    client.get("/api/cases/BX-C001/overview")
    client.get("/api/cases/BX-C001/report.md")
    assert calls and all(kw.get("expire") is False for kw in calls)


def test_followed_cycle_payments_are_in_trail():
    r = result(graph_edges=[pay("c1", "C", "A", 50, ts=3 * H, role="cycle"), pay("t1", "A", "B", 100, role="cycle")])
    status = {p["tx_id"]: p["status"] for p in V.flow(case(CHAIN), r, None, [], RULES)["payments"]}
    assert status["c1"] == "trail" and status["t1"] == "flagged"


def test_expired_hold_and_unnamed_decider_read_plainly():
    md = V.str_markdown(case(CHAIN), full_result(), None, [
        {"id": "A1", "type": "HOLD", "status": "EXPIRED", "account": "B", "hold_amount": 10.0},
        {"id": "A3", "type": "HOLD", "status": "EXPIRED", "account": "C", "hold_amount": 5.0, "released_at": 1},
        {"id": "A2", "type": "FILE_STR", "status": "REJECTED", "reason": "Not enough"}])
    assert "Hold account B: $10 (withdrawn)" in md and "Hold account C: $5 (released)" in md
    assert "File an STR: rejected (Not enough)" in md and "None" not in md


def test_queue_shows_us_dollars_even_with_a_plan(monkeypatch):
    from flowtrace.api import main as M
    monkeypatch.setattr(M, "CASES", {"BX-C001": case(CHAIN)})
    monkeypatch.setattr(M.store, "load_plan", lambda cid: {"currency": "Euro", "rate": 0.9})
    assert M._currency("BX-C001") == {"currency": "US Dollar", "rate": 1.0}


def test_report_text_never_shows_evidence_ids():
    from flowtrace.agent.report import clean
    assert clean("A paid 6 accounts (E1, E3). Then E4 shows [E2] fast moves.") == "A paid 6 accounts. Then shows fast moves."


def test_short_reasons_stay_short():
    from flowtrace.agent.brief import alert_reasons, brief
    e = {"code": "NEW_COUNTERPARTIES", "metrics": {"new": 6, "counterparties": 6}, "claim": "6 of the 6 accounts..."}
    assert brief(e) == "6 of 6 counterparties are new to {A}"
    assert V.fill(brief(e), ["X"], {"X": "Account B"}) == "6 of 6 counterparties are new to Account B"
    assert len(brief({"code": "UNKNOWN", "claim": " ".join(["word"] * 30)}).split()) <= 12
    c = {"detector_hits": [{"detector": "FAN-OUT", "strength": 0.9, "detail": "paid 9 distinct accounts ($12,000)"}],
         "signals": [{"signal": "model", "p": 0.82}]}
    assert alert_reasons(c) == ["Paid 9 different accounts in a short period", "The risk model scores these payments as high risk"]


def test_risk_and_confidence_levels_follow_config_and_never_show_a_percentage():
    r = result()
    r["disposition"]["confidence"] = 0.85
    st = V.build_overview(case(CHAIN), r, None, [], None, False, RULES)["status"]
    assert (st["risk_level"], st["confidence"]) == ("High", "High") and "confidence_pct" not in st
    stricter = {**RULES, "disposition": {"escalate_at": 0.8, "close_below": 0.25, "confidence_labels": {"high": 0.9, "medium": 0.6}}}
    st = V.build_overview(case(CHAIN), r, None, [], None, False, stricter)["status"]
    assert (st["risk_level"], st["confidence"]) == ("Medium", "Medium")


def test_crime_types_only_for_escalated_cases():
    found = [ev("E1", "support", 0.5, code="PASS_THROUGH")]
    o = lambda action: V.build_overview(case(CHAIN), result(evidence=found, action=action), None, [], None, False, RULES)  # noqa: E731
    assert o("ESCALATE")["crime_guess"] == ["smuggling", "money mule"]
    assert o("REVIEW")["crime_guess"] == [] and o("CLOSE")["crime_guess"] == []


def test_each_payment_keeps_its_own_currency_next_to_the_usd_equivalent(monkeypatch):
    monkeypatch.setattr(V, "orig_rate", lambda cur: 0.9 if cur == "Euro" else 1.0)
    euro = [pay("t1", "A", "B", 100, cur="Euro"), pay("t2", "B", "C", 90, ts=H, cur="US Dollar")]
    pays = {p["tx_id"]: p for p in V.build_overview(case(euro), None, None, [], None, False, RULES)["transactions"]}
    assert (pays["t1"]["amount"], pays["t1"]["cur"], pays["t1"]["amount_orig"]) == (100, "Euro", 90.0)
    assert pays["t2"]["cur"] == "US Dollar"
    md = V.str_markdown(case(euro), full_result(), None, [], RULES)
    assert "| $100 | EUR 90 |" in md and "Amount (USD equivalent) | Paid in" in md


def test_str_points_and_trail_cite_their_evidence_and_crimes_wait_for_escalation():
    r = full_result()
    r["report"]["money_trail"] = [{"text": "A paid B, B paid C.", "evidence_ids": ["E1"]}]
    md = V.str_markdown(case(CHAIN), r, None, [], RULES)
    assert "- A paid B, B paid C. (E1)" in md
    assert any(line.startswith("- ") and line.endswith("(E1)") for line in md.splitlines()[:20])  # summary points
    r["disposition"]["action"] = "REVIEW"
    assert "smuggling" not in V.str_markdown(case(CHAIN), r, None, [], RULES)


def test_a_provisional_auto_hold_reads_plainly_in_the_str_and_marks_the_account():
    actions = [{"id": "A1", "type": "HOLD", "status": "PROVISIONAL", "account": "B", "hold_amount": 90.0,
                "provisional_until": 86_400_000, "proposed_by": "agent"}]
    md = V.str_markdown(case(CHAIN), full_result(), None, actions, RULES)
    assert "held provisionally by the agent (auto-hold) until 1970-01-02 00:00 UTC" in md
    assert nodes(V.flow(case(CHAIN), None, None, actions, RULES))["B"]["hold"] == "provisional"


def test_an_unknown_currency_rate_leaves_the_original_amount_out(monkeypatch):
    monkeypatch.setattr(V, "orig_rate", lambda cur: None)
    p = V.build_overview(case([pay("t1", "A", "B", 100, cur="Yen")]), None, None, [], None, False, RULES)["transactions"][0]
    assert (p["cur"], p["amount_orig"]) == ("Yen", None)
