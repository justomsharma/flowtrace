"""Eval slice: the real day's top laundering and top legitimate cases, with every payment of their accounts."""
import polars as pl

from flowtrace.eval.slice import pick, slice_rows


def test_pick_takes_the_highest_ranked_cases_of_each_class_in_rank_order():
    cases = [{"case_id": f"C{i}", "ground_truth": g} for i, g in enumerate([1, 1, 0, 1, 0, 0, 1])]
    got = pick(cases, n_pos=2, n_neg=2)
    assert [c["case_id"] for c in got] == ["C0", "C1", "C2", "C4"]


def test_pick_counts_unlabelled_cases_as_legitimate():
    got = pick([{"case_id": "A", "ground_truth": None}, {"case_id": "B", "ground_truth": 1}], n_pos=1, n_neg=1)
    assert [c["case_id"] for c in got] == ["A", "B"]


def test_slice_keeps_every_payment_that_touches_a_chosen_account():
    day = pl.DataFrame({"tx_id": ["1", "2", "3", "4"], "src": ["A", "X", "Y", "B"], "dst": ["X", "B", "Z", "B"]})
    got = slice_rows(day, [{"accounts": ["A"]}, {"accounts": ["B"]}])
    assert sorted(got["tx_id"].to_list()) == ["1", "2"]          # self-transfers are dropped, as in samples
