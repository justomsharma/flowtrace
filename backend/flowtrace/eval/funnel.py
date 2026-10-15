"""Measure the case funnel on one uploaded day: payments -> flagged -> cases, and recall at capacity.

Recall at capacity (as Google Cloud AML AI backtests it): a laundering attempt from
HI-Small_Patterns.txt counts as found in the top N when any of its payments sits in a top-N case.
The batch is deleted from the graph afterwards.

Run (graph loaded up to the sample's day):
  python -m flowtrace.eval.funnel ../data/samples/demo_mixed_2022-09-09.csv [--bars 0.1,0.2,0.3]
"""
from __future__ import annotations

import argparse
import copy
import json

import polars as pl

from flowtrace.graph import queries as Q
from flowtrace.graph.client import rw
from flowtrace.pipeline.batch import process_batch
from flowtrace.pipeline.cases import build_cases
from flowtrace.settings import cfg, settings


def attempt_keys() -> dict[tuple, int]:
    s = settings()
    tx = pl.read_parquet(s.processed_dir / "transactions.parquet", columns=["tx_id", "src", "dst", "ts"])
    pat = pl.read_parquet(s.processed_dir / "patterns.parquet").join(tx, on="tx_id")
    return {(r["src"], r["dst"], r["ts"]): r["attempt_id"] for r in pat.iter_rows(named=True)}


def funnel(cases: list[dict], tx: list[dict], keys: dict[tuple, int], capacity: int) -> dict:
    rank: dict[tuple, int] = {}
    for i, c in enumerate(cases, 1):
        for e in c["edges"]:
            rank.setdefault((e["src"], e["dst"], e["ts"]), i)
    best: dict[int, int] = {}
    for t in tx:
        k = (t["src"], t["dst"], t["ts"])
        if k in keys:
            best[keys[k]] = min(best.get(keys[k], 10 ** 9), rank.get(k, 10 ** 9))
    top = lambda n: sum(1 for v in best.values() if v <= n)  # noqa: E731
    return {
        "payments": len(tx), "flagged": len({t for c in cases for t in c["tx_ids"]}), "cases": len(cases),
        "largest_case_accounts": max((len(c["accounts"]) for c in cases), default=0),
        "largest_case_payments": max((len(c["tx_ids"]) for c in cases), default=0),
        "attempts": len(best), f"attempts_top{capacity}": top(capacity), "attempts_top50": top(50),
        "attempts_any_case": top(10 ** 8),
        f"laundering_cases_top{capacity}": sum(1 for c in cases[:capacity] if c["ground_truth"] == 1),
        "laundering_payments": sum(1 for t in tx if t["is_laundering"] == 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--bars", default="", help="comma-separated flag_probability values to sweep")
    ap.add_argument("--list", type=int, default=0, help="print the top N cases")
    args = ap.parse_args()
    data = open(args.csv, "rb").read()
    res = process_batch(data, args.csv, lambda e: None)
    try:
        rules = cfg()
        cap = rules["cases"]["daily_capacity"]
        keys = attempt_keys()
        it = res["internals"]
        cases = sorted(res["cases"].values(), key=lambda c: c["case_id"])
        out = {"file": args.csv, "bar": rules["cases"]["flag_probability"],
               "detector_hits": res["summary"]["detector_hits"], **funnel(cases, it["tx"], keys, cap)}
        print(json.dumps(out))
        for i, c in enumerate(cases[:args.list], 1):
            print(f"  {i:3d} {c['case_score']:.3f} {c['tier']:5s} acc={len(c['accounts']):3d} pay={len(c['tx_ids']):3d} "
                  f"gt={c['ground_truth']} {c['shape']['typology']:14s} {[s['signal'] for s in c['signals']]}")
        for b in [float(x) for x in args.bars.split(",") if x]:
            r = copy.deepcopy(rules)
            r["cases"]["flag_probability"] = b
            swept = build_cases(it["tx"], it["hits"], "SWEEP", r, it["degrees"])
            print(json.dumps({"bar": b, **funnel(swept, it["tx"], keys, cap)}))
    finally:
        rw(Q.DELETE_BATCH, {"batch": res["summary"]["batch_id"]})


if __name__ == "__main__":
    main()
