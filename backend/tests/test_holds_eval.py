"""Hold measurement: money in motion, money blocked, hold precision against labelled laundering accounts."""
from flowtrace.eval.holds import case_row, summarise


def plan(holds, in_motion, blocked, out=0.0, method="min-cut"):
    return {"in_motion": in_motion, "blocked": blocked, "already_out": out, "method": method, "min_cut_size": len(holds),
            "holds": [{"account": a, "amount_blocked": v} for a, v in holds], "skipped_hubs": [], "currency": "US Dollar"}


def test_case_row_counts_holds_on_laundering_accounts_and_hubs():
    r = case_row({"case_id": "C1", "ground_truth": 1, "accounts": ["X"]}, "ESCALATE",
                 plan([("A", 900.0), ("B", 100.0)], 1200.0, 1000.0), laundering={"A"}, hubs={"B"})
    assert (r["holds"], r["holds_on_laundering"], r["hubs_held"]) == (2, 1, 1)
    assert r["blocked_share"] == 1000.0 / 1200.0


def test_summary_pools_hold_precision_over_all_holds():
    rows = [
        case_row({"case_id": "C1", "ground_truth": 1, "accounts": ["X"]}, "ESCALATE", plan([("A", 5.0), ("B", 5.0)], 10.0, 10.0), {"A"}, set()),
        case_row({"case_id": "C2", "ground_truth": 0, "accounts": ["Y"]}, "CLOSE", plan([], 50.0, 0.0), {"A"}, set()),
        case_row({"case_id": "C3", "ground_truth": 1, "accounts": ["Z"]}, "REVIEW", plan([("Z", 30.0)], 40.0, 30.0), {"Z"}, set()),
    ]
    s = summarise(rows)
    assert s["holds"] == 3 and s["hold_precision"] == 2 / 3
    assert (s["in_motion_total"], s["blocked_total"]) == (100.0, 40.0)
    assert s["cases_with_holds"] == 2 and s["hubs_held"] == 0
    assert s["in_motion_median"] == 40.0


def test_summary_splits_laundering_and_legitimate_cases():
    rows = [
        case_row({"case_id": "C1", "ground_truth": 1, "accounts": ["X"]}, "ESCALATE", plan([("A", 5.0)], 10.0, 5.0), {"A"}, set()),
        case_row({"case_id": "C2", "ground_truth": 0, "accounts": ["Y"]}, "ESCALATE", plan([("B", 7.0)], 7.0, 7.0), {"A"}, set()),
    ]
    by = summarise(rows)["by_truth"]
    assert (by["laundering"]["cases"], by["laundering"]["hold_precision"], by["laundering"]["blocked_total"]) == (1, 1.0, 5.0)
    assert (by["legitimate"]["holds"], by["legitimate"]["hold_precision"]) == (1, 0.0)
