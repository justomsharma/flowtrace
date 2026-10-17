"""Signal strength measured the way each evidence code defines it (variant C), and how the disposition uses it."""
import polars as pl
import pytest

from flowtrace.agent import disposition as D
from flowtrace.agent.evidence import EvidenceBook
from flowtrace.ml import signal_rates as S
from flowtrace.settings import cfg

H = 3_600_000
DAY = 24 * H


def tx(rows):
    """rows: (src, dst, ts, amount, fmt, laundering)."""
    return pl.DataFrame([{"tx_id": f"T{i}", "src": s, "dst": d, "ts": t, "amount_usd": a, "payment_format": f,
                          "is_laundering": lab} for i, (s, d, t, a, f, lab) in enumerate(rows)],
                        schema_overrides={"is_laundering": pl.Int8})


def marked(frame: pl.DataFrame, col: str) -> set[str]:
    return set(frame.filter(pl.col(col))["tx_id"].to_list())


def test_pass_through_is_a_similar_payment_out_within_24h_of_one_in():
    t = tx([("X", "A", 0, 5000.0, "ACH", 1),            # T0 in
            ("A", "Y", 2 * H, 4500.0, "ACH", 1),        # T1 0.90x, 2h later: pass-through
            ("A", "Z", 3 * H, 1000.0, "ACH", 0),        # T2 too small
            ("A", "X", 4 * H, 5000.0, "ACH", 0),        # T3 back to the sender: not "someone else"
            ("A", "W", 30 * H, 4800.0, "ACH", 0),       # T4 too late
            ("Q", "B", 0, 500.0, "ACH", 0),             # T5 in under $1,000
            ("B", "R", H, 480.0, "ACH", 0)])            # T6 not counted
    assert marked(S.pass_through(t), "PASS_THROUGH") == {"T1"}


def test_fast_onward_means_the_receiver_sends_half_on_within_24h():
    t = tx([("A", "B", 0, 1000.0, "ACH", 1),            # T0: B sends 600 on within 24h -> fast
            ("B", "C", 5 * H, 600.0, "ACH", 1),
            ("A", "D", 0, 1000.0, "ACH", 0),            # T2: D sends 300 (30%) -> not fast
            ("D", "E", H, 300.0, "ACH", 0),
            ("A", "F", 0, 1000.0, "ACH", 0),            # T4: F sends on after 30h -> not fast
            ("F", "G", 30 * H, 1000.0, "ACH", 0)])
    assert marked(S.fast_onward(t), "FAST_ONWARD_TRANSFER") == {"T0"}


def test_loops_round_trip_and_cycle_have_their_own_time_rules():
    t = tx([("A", "B", 0, 100.0, "ACH", 1),             # T0 A->B->C->A, gaps 40h + 40h: both
            ("B", "C", 40 * H, 100.0, "ACH", 1),
            ("C", "A", 80 * H, 100.0, "ACH", 1),
            ("P", "Q", 0, 100.0, "ACH", 0),             # T3 P->Q->P after 80h: a cycle (within 4 days), not a trip (gap > 72h)
            ("Q", "P", 80 * H, 100.0, "ACH", 0),
            ("M", "N", 10 * H, 100.0, "ACH", 0),        # T5 M's money came back before it left: no loop for M,
            ("N", "M", 5 * H, 100.0, "ACH", 0),         # T6 but N->M->N is in time order
            ("U", "V", 0, 100.0, "ACH", 0),             # T7 back after 100h: too long for a cycle
            ("V", "U", 100 * H, 100.0, "ACH", 0)])
    f = S.loops(t, hub_degree=500)
    assert marked(f, "ROUND_TRIP") == {"T0", "T6"}
    assert marked(f, "CYCLE") == {"T0", "T3", "T6"}


def test_equal_splits_needs_three_receivers_with_near_identical_totals():
    t = tx([("A", "B", 0, 1000.0, "ACH", 1), ("A", "C", H, 1010.0, "ACH", 1), ("A", "D", 2 * H, 995.0, "ACH", 1),
            ("E", "F", 0, 1000.0, "ACH", 0), ("E", "F", H, 1000.0, "ACH", 0), ("E", "F", 2 * H, 1000.0, "ACH", 0),
            ("G", "H", 0, 1000.0, "ACH", 0), ("G", "I", H, 3000.0, "ACH", 0), ("G", "J", 2 * H, 50.0, "ACH", 0)])
    assert marked(S.equal_splits(t), "EQUAL_SPLITS") == {"T0", "T1", "T2"}


def test_convergence_is_one_beneficiary_reached_through_two_middle_accounts():
    t = tx([("S", "X1", 0, 500.0, "ACH", 1), ("S", "X2", 0, 500.0, "ACH", 1),
            ("X1", "Z", H, 490.0, "ACH", 1), ("X2", "Z", H, 490.0, "ACH", 1),
            ("K", "L", 0, 500.0, "ACH", 0), ("L", "Z", H, 490.0, "ACH", 0)])
    assert marked(S.convergence(t, hub_degree=50), "CONVERGENCE") == {"T2", "T3"}


def test_cash_or_crypto_exit_is_the_receiver_paying_out_in_cash_soon_after():
    t = tx([("A", "B", 0, 900.0, "ACH", 1), ("B", "C", 10 * H, 800.0, "Cash", 1),
            ("A", "D", 0, 900.0, "ACH", 0), ("D", "E", 80 * H, 800.0, "Bitcoin", 0)])
    assert marked(S.cash_exit(t, ["Cash", "Bitcoin"]), "CASH_OR_CRYPTO_EXIT") == {"T0"}


def test_stats_count_laundering_examples_and_share():
    f = pl.DataFrame({"is_laundering": [1, 0, 0, 0, 1, 0], "X": [True, True, False, False, False, False]})
    r = S.stats(f, "X", "label")
    assert (r["payments"], r["laundering"], r["share"]) == (2, 1, pytest.approx(1 / 3, abs=1e-4))
    assert r["base_rate"] == pytest.approx(2 / 6)


# --- strength (the disposition side) -----------------------------------------------------------

RATES = {"base_rate": 0.001, "codes": {
    "FAST_ONWARD_TRANSFER": {"payments": 100_000, "laundering": 50, "base_rate": 0.001},   # raw lift 0.5
    "ROUND_TRIP": {"payments": 1_000, "laundering": 100, "base_rate": 0.001},              # raw lift 100
    "EQUAL_SPLITS": {"payments": 2_000, "laundering": 0, "base_rate": 0.001},              # 0 examples
    "ESTABLISHED_COUNTERPARTIES": {"payments": 1_000_000, "laundering": 200, "base_rate": 0.001},
}}


def rules(on=True):
    r = cfg()
    return {**r, "disposition": {**r["disposition"], "measured_weights": {"enabled": on, "min_laundering": 30,
                                                                          "prior_laundering": 10}}}


@pytest.fixture
def rates(monkeypatch):
    monkeypatch.setattr(S, "rates", lambda: RATES)


def test_lift_is_shrunk_toward_one_and_needs_30_examples(rates):
    mw = rules()["disposition"]["measured_weights"]
    assert S.lift("EQUAL_SPLITS", mw) is None                       # 0 laundering examples: not used
    assert S.lift("UNMEASURED", mw) is None
    assert S.lift("ROUND_TRIP", mw) == pytest.approx((100 + 10) / (1_000 + 10 / 0.001) / 0.001)
    assert 0.5 < S.lift("FAST_ONWARD_TRANSFER", mw) < 1.0            # pulled toward 1


def test_no_lift_ever_switches_off_an_escalator(rates):
    book = EvidenceBook()
    book.add(kind="support", code="FAST_ONWARD_TRANSFER", claim="c", weight=0.5, tool="trace_funds", step=1, escalator=True)
    book.add(kind="counter", code="ESTABLISHED_COUNTERPARTIES", claim="c", weight=0.5, tool="t", step=1)
    d = D.decide(book, 0.5, 0.5, rules())
    assert any("escalator fired: FAST_ONWARD_TRANSFER" in r for r in d.reasons)
    assert D.strength(book.items[0], rules()) < 1.0                  # it still counts for less


def test_measured_weights_off_counts_every_indicator_in_full(rates):
    book = EvidenceBook()
    e = book.add(kind="support", code="ROUND_TRIP", claim="c", weight=0.55, tool="t", step=1, escalator=True)
    assert D.strength(e, rules(on=False)) == 1.0


def test_missing_rates_file_fails_loudly_when_measured_weights_are_on(monkeypatch, tmp_path):
    S.rates.cache_clear()
    monkeypatch.setattr(S, "rates_path", lambda: tmp_path / "missing.json")
    book = EvidenceBook()
    book.add(kind="support", code="ROUND_TRIP", claim="c", weight=0.55, tool="t", step=1)
    with pytest.raises(FileNotFoundError, match="signal_rates"):
        D.decide(book, 0.5, 0.5, rules(on=True))
    assert D.decide(book, 0.5, 0.5, rules(on=False)).action  # off: the file is not needed
    S.rates.cache_clear()
