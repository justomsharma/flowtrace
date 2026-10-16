"""Run every Cypher query FlowTrace depends on against the loaded graph and check the answers.

Run after `python -m flowtrace.graph.load`:   python -m flowtrace.graph.smoke            read-only
                                              python -m flowtrace.graph.smoke --write    also checks writes
The --write checks use their own SMOKE ids and always remove them, even when a check fails.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import polars as pl

from flowtrace.detectors import alerts as A
from flowtrace.graph import queries as Q
from flowtrace.graph.client import ro, rw
from flowtrace.settings import cfg, settings

DAY = 86_400_000


def write_checks(acc: str, cp: str, t1: int) -> int:
    """Upload idempotency and case-memory replacement. Everything written uses SMOKE ids and is deleted."""
    failures = 0
    rows = [{"tx_id": f"PSMOKE{i}", "src": acc, "dst": cp, "ts": t1 + i, "amount": 1.0, "fmt": "ACH",
             "cur": "US Dollar", "xcur": False} for i in range(2)]
    case = {"case_id": "SMOKE-C001", "disposition": "REVIEW", "typology": None, "crime": "", "score": 0.0,
            "confidence": "low", "created_ts": t1, "summary": "", "tx_ids": [], "expires_ts": t1}
    try:
        for _ in range(2):
            rw(Q.INSERT_BATCH, {"rows": rows, "batch": "SMOKE"})
        n = ro("MATCH ()-[t:TRANSFER {batch: 'SMOKE'}]->() RETURN count(t) AS n", {}).rows[0]["n"]
        rw(Q.SAVE_CASE, {**case, "accounts": [acc, cp]})
        rw(Q.SAVE_CASE, {**case, "accounts": [acc]})
        links = ro("MATCH (c:Case {id: 'SMOKE-C001'})-[r:ABOUT]->() RETURN count(r) AS n", {}).rows[0]["n"]
        # Case memory counts only earlier cases: not the same upload, and not one whose window ends after
        # the current case starts. SMOKE-C002 (escalated) was created at t1.
        rw(Q.SAVE_CASE, {**case, "case_id": "SMOKE-C002", "disposition": "ESCALATE", "accounts": [cp]})
        mem = {}
        for label, prefix, t0 in (("same batch", "SMOKE-C", t1 + DAY), ("same day", "SMOKEOTHER-C", t1),
                                  ("next day", "SMOKEOTHER-C", t1 + DAY)):
            p = {"batch_prefix": prefix, "t0": t0}
            prior = {r["case_id"] for r in ro(Q.PRIOR_CASES, {"id": cp, **p}).rows}
            near = {r["case_id"] for r in ro(Q.NEAREST_ESCALATED, {"id": acc, **p}).rows}
            mem[label] = ("SMOKE-C002" in prior, "SMOKE-C002" in near)
    finally:
        rw(Q.DELETE_BATCH, {"batch": "SMOKE"})
        rw(Q.DELETE_BATCH_CASES, {"prefix": "SMOKE-C"})
    print(f"  idempotent insert: {n} edges after inserting 2 payments twice (expected 2)")
    print(f"  case memory: {links} account link(s) after re-saving a case with 1 account (expected 1)")
    if n != 2:
        failures += 1
        print("  FAIL re-inserting a batch duplicated edges")
    if links != 1:
        failures += 1
        print("  FAIL re-saving a case kept its old account links")
    expect = {"same batch": (False, False), "same day": (False, False), "next day": (True, True)}
    for label, want in expect.items():
        print(f"  case memory, {label + ':':11s} prior/nearest see SMOKE-C002 = {mem[label]} (expected {want})")
    if mem != expect:
        failures += 1
        print("  FAIL case memory does not separate the current upload from earlier ones")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="also run the write checks (cleaned up afterwards)")
    args = ap.parse_args()
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
        "HUB_DEGREES": {"ids": [acc, cp], "h0": t0, "t0": t1},
        "PAIR_HISTORY_OUT": {"id": acc, "cps": [cp], "before": t1},
        "PAIR_HISTORY_IN": {"id": cp, "cps": [acc], "before": t1},
        "FLOW_SERIES": {"id": acc, "before": t1},
        "SHARED_DESTINATIONS": {"ids": [acc, cp], "t0": t0, "t1": t1},
        "SHARED_SOURCES": {"ids": [acc, cp], "t0": t0, "t1": t1},
        "CYCLES": {"id": acc, "t0": t0, "t1": t1},
        "NEAREST_ESCALATED": {"id": acc, "batch_prefix": "SMOKE-C", "t0": t1},
        "PRIOR_CASES": {"id": acc, "batch_prefix": "SMOKE-C", "t0": t1},
    }
    detectors = {
        "FAN_OUT": {"ids": [acc], "t0": t0, "t1": t1, "min": 2},
        "FAN_IN": {"ids": [cp], "t0": t0, "t1": t1, "min": 1},
        "PASS_THROUGH": {"ids": [acc, cp], "t0": t0, "t1": t1, "day0": t0, "gap": DAY, "lo": 0.0, "hi": 10.0,
                         "min_amount": 0},
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
    until = ro(Q.HISTORY_UNTIL, {}).rows
    print(f"  history cutoff marker: {until[0]['until'] if until else 'missing (reload the graph to add it)'}")
    if args.write:
        failures += write_checks(acc, cp, t1)
    print(f"\n{'all queries passed' if not failures else f'{failures} failure(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
