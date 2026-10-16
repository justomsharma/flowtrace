"""Hold measurement: money frozen now, money on the case's routes (assumption), hold precision vs base rate."""
from flowtrace.eval.holds import case_row, laundering_before, summarise


def plan(holds, in_motion, on_routes=0.0, out=0.0, candidates=None):
    hs = [{"account": a, "hold": v, "on_routes": 0.0} for a, v in holds]
    return {"in_motion": in_motion, "frozen_now": sum(v for _, v in holds), "on_routes": on_routes, "already_out": out,
            "holds": hs, "candidates": candidates if candidates is not None else [a for a, _ in holds],
            "route_cut_size": len(holds), "currency": "US Dollar"}


def case(cid, truth):
    return {"case_id": cid, "ground_truth": truth, "accounts": ["X"], "t1": 100}


def test_case_row_counts_frozen_money_and_holds_on_laundering_accounts():
    r = case_row(case("C1", 1), "ESCALATE", plan([("A", 900.0), ("B", 100.0)], 1200.0, on_routes=50.0,
                                                 candidates=["A", "B", "C", "D"]), laundering={"A", "C"})
    assert (r["frozen_now"], r["on_routes"], r["in_motion"]) == (1000.0, 50.0, 1200.0)
    assert (r["holds"], r["holds_on_laundering"]) == (2, 1)
    assert (r["candidates"], r["candidates_laundering"]) == (4, 2)


def test_summary_pools_precision_and_the_base_rate():
    rows = [
        case_row(case("C1", 1), "ESCALATE", plan([("A", 5.0), ("B", 5.0)], 10.0, candidates=["A", "B"]), {"A"}),
        case_row(case("C2", 0), "CLOSE", plan([], 50.0, candidates=["Y"]), {"A"}),
        case_row(case("C3", 1), "ESCALATE", plan([("Z", 30.0)], 40.0, candidates=["Z", "W"]), {"Z"}),
    ]
    s = summarise(rows)
    assert s["holds"] == 3 and s["hold_precision"] == 2 / 3
    assert s["base_rate"] == 2 / 5            # laundering share among every account a hold could have gone on
    assert (s["in_motion_total"], s["frozen_total"]) == (100.0, 40.0)
    assert s["cases_with_holds"] == 2 and s["in_motion_median"] == 40.0
    assert "hubs_held" not in s


def test_summary_splits_laundering_and_legitimate_cases():
    rows = [
        case_row(case("C1", 1), "ESCALATE", plan([("A", 5.0)], 10.0), {"A"}),
        case_row(case("C2", 0), "ESCALATE", plan([("B", 7.0)], 7.0), {"A"}),
    ]
    by = summarise(rows)["by_truth"]
    assert (by["laundering"]["cases"], by["laundering"]["hold_precision"], by["laundering"]["frozen_total"]) == (1, 1.0, 5.0)
    assert (by["legitimate"]["holds"], by["legitimate"]["frozen_total"]) == (1, 7.0)


def test_laundering_counts_only_payments_before_the_decision():
    import polars as pl
    tx = pl.DataFrame({"src": ["A", "B"], "dst": ["C", "D"], "ts": [50, 150], "is_laundering": [1, 1]})
    assert laundering_before(tx, 100) == {"A", "C"}
