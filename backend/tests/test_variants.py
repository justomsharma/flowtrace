"""The scoring-variant harness: B is reproduced as first pushed, and the winner follows the rule written in advance."""
from flowtrace.agent.evidence import EvidenceBook
from flowtrace.eval import variants as V
from flowtrace.settings import cfg


def book_with_weak_escalator() -> EvidenceBook:
    b = EvidenceBook()
    # B measured FAST_ONWARD_TRANSFER at lift 0.8 -> strength 0.27 < 0.5: it no longer escalated.
    b.add(kind="support", code="FAST_ONWARD_TRANSFER", claim="c", weight=0.5, tool="trace_funds", step=1, escalator=True)
    b.add(kind="counter", code="ESTABLISHED_COUNTERPARTIES", claim="c", weight=0.5, tool="t", step=1)
    return b


def test_b_switches_off_the_weak_escalator_and_a_does_not():
    b = book_with_weak_escalator()
    assert not any("escalator fired" in r for r in V.score("B", b, 0.5, 0.5, cfg()).reasons)
    assert any("escalator fired" in r for r in V.score("A", b, 0.5, 0.5, cfg()).reasons)
    assert b.items[0].escalator  # the shared book is never changed


def row(recall_fixed, recall_heur, legit, frozen):
    return {"policies": {"fixed": {"escalate_recall": recall_fixed}, "heuristic": {"escalate_recall": recall_heur}},
            "legit_escalated_total": legit, "money_wrongly_frozen": frozen}


def test_pick_needs_recall_within_002_of_a_then_fewest_legit_then_least_money():
    t = {"A": row(0.90, 0.90, 30, 9e7), "B": row(0.70, 0.90, 5, 1e5), "C": row(0.89, 0.885, 20, 5e7)}
    assert V.pick(t)[0] == "C"                              # B escalates fewer legit cases but loses recall
    t["C"] = row(0.90, 0.90, 30, 9e7)
    assert V.pick(t)[0] == "A"                              # a full tie stays with A
    t["C"] = row(0.90, 0.90, 30, 1e6)
    assert V.pick(t)[0] == "C"                              # same legit count, less money wrongly frozen


def test_auto_hold_summary_counts_cases_and_wrongly_frozen_money():
    rows = [{"case_id": "C1", "truth": 1, "auto_hold": True, "holds": 2, "holds_on_laundering": 1, "frozen_now": 10.0},
            {"case_id": "C2", "truth": 0, "auto_hold": True, "holds": 1, "holds_on_laundering": 0, "frozen_now": 2e6},
            {"case_id": "C3", "truth": 0, "auto_hold": False, "holds": 1, "holds_on_laundering": 0, "frozen_now": 5.0}]
    s = V.auto_hold_summary(rows)
    assert (s["cases"], s["precision"], s["hold_precision"]) == (2, 0.5, 1 / 3)
    assert (s["wrongly_frozen_total"], s["wrongly_frozen_max_case"]) == (2e6, 2e6)
    assert V.auto_hold_summary([])["precision"] is None
