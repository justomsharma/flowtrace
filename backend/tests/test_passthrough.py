"""PASS-THROUGH pairs money that arrives with a similar amount leaving soon after, to someone else."""
from flowtrace.detectors import alerts as A
from flowtrace.detectors.alerts import passthrough_pairs
from tests.fakegraph import World

H = 3_600_000
P = {"max_gap_hours": 24, "ratio_low": 0.8, "ratio_high": 1.05, "min_amount_usd": 1000}


def tx(i, cp, ts, amt):
    return {"tx_id": f"T{i}", "cp": cp, "ts": ts, "amount": amt}


def test_pairs_money_that_leaves_soon_in_similar_amount():
    pairs = passthrough_pairs([tx(1, "S", 0, 5000)], [tx(2, "D", 3 * H, 4900)], P)
    assert [(i["tx_id"], o["tx_id"]) for i, o in pairs] == [("T1", "T2")]


def test_rejects_late_small_dissimilar_or_back_to_sender():
    ins = [tx(1, "S", 0, 5000), tx(9, "S2", 0, 500)]
    outs = [tx(2, "D", 30 * H, 5000),    # too late
            tx(3, "D", H, 2000),          # amount not similar
            tx(4, "S", H, 5000),          # back to the sender
            tx(5, "D", -H, 5000),         # before the money arrived
            tx(6, "D", H, 480)]           # pairs only with the 500 inflow, which is below the minimum
    assert passthrough_pairs(ins, outs, P) == []


def test_detector_fires_only_on_time_paired_accounts(monkeypatch):
    e = lambda i, s, d, ts, amt: {"tx_id": f"T{i}", "src": s, "dst": d, "ts": ts, "amount": amt, "fmt": "ACH"}  # noqa: E731
    w = World(edges=[e(1, "S", "MULE", 2 * H, 5000), e(2, "MULE", "D", 4 * H, 4950),       # forwarded in 2h
                     e(3, "P", "SAVER", 2 * H, 5000), e(4, "SAVER", "D", 40 * H, 4950)],   # kept for 38h
              accounts={})
    rules = {"detectors": {"window_hours": 24, "fan_out_min": 5, "fan_in_min": 5, "scatter_gather_min_mid": 3,
                           "passthrough": P}}
    monkeypatch.setattr(A, "ro", w.ro)
    hits = [h for h in A.run_detectors(["MULE", "SAVER"], 0, 48 * H, rules, []) if h["detector"] == "PASS-THROUGH"]
    assert [h["account"] for h in hits] == ["MULE"]
    assert set(hits[0]["tx_ids"]) == {"T1", "T2"}
