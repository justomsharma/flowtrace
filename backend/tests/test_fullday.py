"""Offline full-day evaluation: same case building as an upload, computed from parquet, no graph."""
import numpy as np
import polars as pl

from flowtrace.eval.fullday import day_cases, hub_degrees

D = 86_400_000


def test_hub_degree_counts_only_the_window_before_the_day():
    tx = pl.DataFrame({"src": ["A", "A", "A", "B", "A", "A"], "dst": ["B", "C", "D", "A", "E", "A"],
                       "ts": [9 * D, 9 * D + 5, 8 * D, 9 * D + 1, 3 * D, 9 * D + 2]})  # A->E too old; A->A self
    deg = hub_degrees(tx.lazy(), first=10 * D, days=2)
    assert deg["A"] == 4 and deg["B"] == 2 and "E" not in deg


def test_day_cases_use_the_production_case_builder():
    day = pl.DataFrame({"tx_id": ["T1", "T2", "T3"], "src": ["A", "C", "E"], "dst": ["B", "D", "F"], "ts": [1, 2, 3],
                        "amount_usd": [10.0, 20.0, 30.0], "payment_format": ["ACH"] * 3, "is_laundering": [1, 0, 0]})
    rules = {"cases": {"flag_probability": 0.5, "max_accounts": 30, "daily_capacity": 1, "hub_degree": 70},
             "detectors": {"precision": {}}, "calibration": {"x": [0.0, 1.0], "y": [0.0, 1.0]}}
    cases = day_cases(day, np.array([0.9, 0.6, 0.1]), rules, degrees={})
    assert [c["tx_ids"] for c in cases] == [["T1"], ["T2"]]
    assert [c["tier"] for c in cases] == ["today", "lower"]
