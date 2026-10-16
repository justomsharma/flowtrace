"""Eval slice from a real day's ranking, for comparing investigation policies on realistic cases.

The curated demo files hold ~29x a real day's laundering and almost no legitimate cases. This file
takes the highest-ranked laundering cases and the highest-ranked legitimate cases of the full day
(ranked offline exactly as an upload of the whole day would be), and keeps every payment of their
accounts that day, so the uploaded slice rebuilds those cases with same-day features.

Run:  python -m flowtrace.eval.slice --day 2022-09-09 --pos 20 --neg 20
      python -m flowtrace.eval.compare --sample eval/day_slice_2022-09-09.csv --cases 40
"""
from __future__ import annotations

import argparse
from datetime import date

import polars as pl

from flowtrace.data.samples import write
from flowtrace.eval.fullday import rank_day
from flowtrace.settings import cfg, settings


def pick(cases: list[dict], n_pos: int, n_neg: int) -> list[dict]:
    """The top n_pos labelled-laundering and top n_neg other cases, in rank order."""
    pos = [c for c in cases if c.get("ground_truth")][:n_pos]
    neg = [c for c in cases if not c.get("ground_truth")][:n_neg]
    keep = {id(c) for c in pos + neg}
    return [c for c in cases if id(c) in keep]


def slice_rows(day_tx: pl.DataFrame, chosen: list[dict]) -> pl.DataFrame:
    accts = sorted({a for c in chosen for a in c["accounts"]})
    return day_tx.filter((pl.col("src") != pl.col("dst")) & (pl.col("src").is_in(accts) | pl.col("dst").is_in(accts)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=date.fromisoformat, default=date(2022, 9, 9))
    ap.add_argument("--pos", type=int, default=20)
    ap.add_argument("--neg", type=int, default=20)
    args = ap.parse_args()
    s = settings()
    cases, day = rank_day(args.day, cfg())
    chosen = pick(cases, args.pos, args.neg)
    tx = pl.scan_parquet(s.processed_dir / "transactions.parquet").filter(pl.col("tx_id").is_in(day["tx_id"].to_list())).collect()
    print(f"{len(cases)} cases on {args.day}; chose {sum(1 for c in chosen if c.get('ground_truth'))} laundering + "
          f"{sum(1 for c in chosen if not c.get('ground_truth'))} legitimate (ranks {[cases.index(c) + 1 for c in chosen]})")
    write(slice_rows(tx, chosen), f"day_slice_{args.day}.csv", s.raw_dir.parent / "samples" / "eval")


if __name__ == "__main__":
    main()
