"""Case building: one bar for every signal, hub-aware grouping, a size cap, noisy-OR score, capacity tiers."""
from flowtrace.pipeline.cases import build_cases, combine, effective_hits

RULES = {"cases": {"flag_probability": 0.3, "max_accounts": 4, "daily_capacity": 2, "hub_degree": 100},
         "detectors": {"precision": {"FAN-OUT": 0.1, "FAN-IN": 0.1, "GATHER-SCATTER": 0.5, "PASS-THROUGH": 0.05,
                                      "SCATTER-GATHER": 0.4, "CYCLE": 0.6}}}


def t(i, s, d, p_cal, lab=0):
    return {"tx_id": f"P{i}", "src": s, "dst": d, "ts": i, "amount_usd": 100.0, "payment_format": "ACH",
            "p": p_cal, "p_cal": p_cal, "is_laundering": lab}


def hit(det, acc, txs):
    return {"detector": det, "account": acc, "strength": 1.0, "detail": "", "tx_ids": txs}


def test_noisy_or():
    assert combine([]) == 0.0
    assert abs(combine([0.5, 0.5]) - 0.75) < 1e-9


def test_gather_scatter_replaces_its_parts():
    hits = [hit(d, "A", []) for d in ("FAN-IN", "FAN-OUT", "GATHER-SCATTER")]
    assert [h["detector"] for h in effective_hits(hits)] == ["GATHER-SCATTER"]


def test_hub_does_not_merge_cases_but_keeps_its_payments():
    tx = [t(1, "A", "HUB", 0.9), t(2, "B", "HUB", 0.8)]
    cases = build_cases(tx, [], "B1", RULES, degrees={"HUB": 500, "A": 3, "B": 3})
    assert len(cases) == 2
    assert all("HUB" in c["accounts"] and len(c["tx_ids"]) == 1 for c in cases)


def test_hub_to_hub_payment_still_lands_in_a_case():
    cases = build_cases([t(1, "H1", "H2", 0.95)], [], "B1", RULES, degrees={"H1": 500, "H2": 500})
    assert len(cases) == 1 and cases[0]["tx_ids"] == ["P1"]


def test_case_size_is_capped_and_split_around_riskiest_accounts():
    chain = [t(i, f"A{i}", f"A{i + 1}", 0.9 - i * 0.01) for i in range(10)]
    cases = build_cases(chain, [], "B1", RULES, degrees={})
    cap = RULES["cases"]["max_accounts"]
    assert len(cases) > 1
    assert all(len(c["accounts"]) <= cap + 1 for c in cases)  # +1: the counterparty across the cut
    assert sorted(x for c in cases for x in c["tx_ids"]) == sorted(x["tx_id"] for x in chain)
    assert cases[0]["primary"] == "A0"


def test_weak_detector_adds_evidence_but_does_not_open_a_case():
    tx = [t(1, "A", "B", 0.05), t(2, "C", "D", 0.9)]
    hits = [hit("PASS-THROUGH", "A", ["P1"]), hit("PASS-THROUGH", "C", ["P2"])]
    cases = build_cases(tx, hits, "B1", RULES, degrees={})
    assert [c["tx_ids"] for c in cases] == [["P2"]]
    assert abs(cases[0]["case_score"] - combine([0.9, 0.05])) < 1e-4
    assert [h["detector"] for h in cases[0]["detector_hits"]] == ["PASS-THROUGH"]


def test_strong_detector_opens_a_case_alone():
    tx = [t(1, "A", "B", 0.01), t(2, "B", "A", 0.01)]
    cases = build_cases(tx, [hit("CYCLE", "A", ["P1", "P2"])], "B1", RULES, degrees={})
    assert len(cases) == 1 and abs(cases[0]["case_score"] - combine([0.01, 0.6])) < 1e-4
    assert cases[0]["signals"][0] == {"signal": "CYCLE", "p": 0.6}


def test_tiers_follow_capacity_and_order():
    tx = [t(1, "A", "B", 0.9), t(2, "C", "D", 0.7), t(3, "E", "F", 0.5)]
    cases = build_cases(tx, [], "B1", RULES, degrees={})
    assert [c["tier"] for c in cases] == ["today", "today", "lower"]
    assert [c["case_id"] for c in cases] == ["B1-C001", "B1-C002", "B1-C003"]


def test_no_flags_no_cases():
    assert build_cases([t(1, "A", "B", 0.01)], [], "B1", RULES, degrees={}) == []


def test_hub_to_hub_payment_listed_first_does_not_glue_cases():
    tx = [t(1, "H1", "H2", 0.9), t(2, "H1", "X", 0.9), t(3, "H1", "Y", 0.8)]
    cases = build_cases(tx, [], "B1", RULES, degrees={"H1": 500, "H2": 500})
    assert sorted(sorted(c["tx_ids"]) for c in cases) == [["P1"], ["P2"], ["P3"]]


def test_primary_is_the_suspect_not_the_hub():
    tx = [t(1, "A", "HUB", 0.95), t(2, "B", "HUB", 0.6)]
    cases = build_cases(tx, [], "B1", RULES, degrees={"HUB": 500})
    assert sorted(c["primary"] for c in cases) == ["A", "B"]


def test_counterparty_detector_scores_only_its_own_case():
    # A..D fill case 1 (cap 4); D -> E crosses the cut, so E is a counterparty in case 1 and owns case 2.
    tx = [t(1, "A", "B", 0.9), t(2, "B", "C", 0.9), t(3, "C", "D", 0.9), t(4, "D", "E", 0.6), t(5, "E", "F", 0.55)]
    cases = build_cases(tx, [hit("FAN-OUT", "E", [])], "B1", RULES, degrees={})
    first = next(c for c in cases if "A" in c["accounts"])
    assert "E" in first["accounts"]
    scored = [c for c in cases if any(s["signal"] == "FAN-OUT" for s in c["signals"])]
    assert len(scored) == 1 and scored[0]["primary"] == "E"


def test_case_payments_carry_their_currency():
    tx = [t(1, "A", "B", 0.9) | {"payment_currency": "Euro"}, t(2, "B", "C", 0.9)]
    edges = {e["tx_id"]: e for e in build_cases(tx, [], "B1", RULES, degrees={})[0]["edges"]}
    assert edges["P1"]["cur"] == "Euro" and "cur" not in edges["P2"]
