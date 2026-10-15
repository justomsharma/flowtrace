"""Run every Cypher query FlowTrace depends on against the loaded graph and check the answers.

Run after `python -m flowtrace.graph.load`:   python -m flowtrace.graph.smoke
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import polars as pl

from flowtrace.detectors import alerts as A
from flowtrace.graph import queries as Q
from flowtrace.graph.client import ro
from flowtrace.settings import cfg, settings

DAY = 86_400_000


def main() -> int:
    s, rules = settings(), cfg()
    tx = pl.read_parquet(s.processed_dir / "transactions.parquet", columns=["tx_id", "src", "dst", "ts"])
    cutoff = int(datetime(2022, 9, 9, tzinfo=timezone.utc).timestamp() * 1000)
    hist = tx.filter((pl.col("ts") < cutoff) & (pl.col("src") != pl.col("dst")))
    # A busy-but-not-huge account with repeated transfers to the same receiver (parallel edges).
    pick = (hist.group_by("src", "dst").len().filter(pl.col("len") >= 3)
            .join(hist.group_by("src").len().rename({"len": "deg"}), on="src")
            .filter(pl.col("deg").is_between(10, 60)).sort("deg").row(0, named=True))
    acc, cp = pick["src"], pick["dst"]
    t1 = cutoff
    t0 = t1 - 2 * DAY
    expect_out = hist.filter((pl.col("src") == acc) & pl.col("ts").is_between(t0, t1, closed="left")).height

    params = {
        "ACCOUNT_CARD": {"id": acc},
        "ACCOUNT_DAILY_HISTORY": {"id": acc, "h0": t1 - 8 * DAY, "t0": t1},
        "CP_INFO": {"ids": [acc, cp]},
        "SIBLING_ACCOUNTS": {"id": acc},
        "TRANSFERS_OUT": {"id": acc, "t0": t0, "t1": t1},
        "TRANSFERS_IN": {"id": acc, "t0": t0, "t1": t1},
        "TRACE_FORWARD_HOP": {"ids": [acc], "frontier": [{"id": acc, "ts": t0}], "max_gap_ms": 72 * 3_600_000,
                              "visited": [acc]},
        "TRACE_BACKWARD_HOP": {"ids": [acc], "frontier": [{"id": acc, "ts": t1}], "max_gap_ms": 72 * 3_600_000,
                               "visited": [acc]},
        "DEGREES": {"ids": [acc, cp]},
        "PAIR_HISTORY_OUT": {"id": acc, "cps": [cp], "before": t1},
        "PAIR_HISTORY_IN": {"id": cp, "cps": [acc], "before": t1},
        "FLOW_SERIES": {"id": acc, "before": t1},
        "SHARED_DESTINATIONS": {"ids": [acc, cp], "t0": t0, "t1": t1},
        "SHARED_SOURCES": {"ids": [acc, cp], "t0": t0, "t1": t1},
        "CYCLES": {"id": acc, "t0": t0, "t1": t1},
        "NEAREST_ESCALATED": {"id": acc},
        "PRIOR_CASES": {"id": acc},
    }
    detectors = {
        "FAN_OUT": {"ids": [acc], "t0": t0, "t1": t1, "min": 2},
        "FAN_IN": {"ids": [cp], "t0": t0, "t1": t1, "min": 1},
        "PASS_THROUGH": {"ids": [acc, cp], "t0": t0, "t1": t1, "ratio": 0.1},
        "SCATTER_GATHER": {"ids": [acc], "t0": t0, "t1": t1, "min": 1, "gap": DAY},
        "CYCLE": {"ids": [acc], "t0": t0, "t1": t1},
    }
    failures = 0
    print(f"probe account {acc} (repeat receiver {cp}); expecting {expect_out} outgoing transfers in window\n")
    for group, mod, ps in (("queries", Q, params), ("detectors", A, detectors)):
        for name, p in ps.items():
            try:
                r = ro(getattr(mod, name), p, timeout=rules["agent"]["query_timeout_ms"] * 3)
                print(f"  ok   {group}.{name:22s} {r.ms:8.1f} ms  {len(r.rows):4d} rows")
            except Exception as e:
                failures += 1
                print(f"  FAIL {group}.{name:22s} {type(e).__name__}: {e}")

    got = len(ro(Q.TRANSFERS_OUT, params["TRANSFERS_OUT"]).rows)
    ph = ro(Q.PAIR_HISTORY_OUT, params["PAIR_HISTORY_OUT"]).rows
    expect_pair = hist.filter((pl.col("src") == acc) & (pl.col("dst") == cp)).height
    print(f"\n  parallel-edge check: TRANSFERS_OUT {got} (expected {expect_out}); "
          f"pair count {ph[0]['prior_cnt'] if ph else 0} (expected {expect_pair})")
    if got != min(expect_out, 300) or not ph or ph[0]["prior_cnt"] != expect_pair:
        failures += 1
        print("  FAIL counts differ from the parquet source")
    print(f"\n{'all queries passed' if not failures else f'{failures} failure(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
