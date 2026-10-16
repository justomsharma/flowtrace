"""Offline full-day evaluation: what the case funnel does on a complete day, not a curated sample.

Every payment of the day is scored from features.parquet (the same point-in-time features the
upload pipeline computes), calibrated, and turned into cases with the production case builder.
Hub degree comes from the parquet history window before the day. Nothing is written to the graph.

Detectors are not run here: they need the day loaded in FalkorDB. With the tuned precisions every
detector is below cases.flag_probability, so detectors cannot open a case; they only add evidence and
nudge case scores. Model-only ranking is therefore what an uploaded full day would rank, up to that nudge.

Run:  python -m flowtrace.eval.fullday --day 2022-09-09     (test day; Sep 7-8 are validation)
"""
from __future__ import annotations

import argparse
import json
from datetime import date

import numpy as np
import polars as pl

from flowtrace.eval.funnel import attempt_keys, funnel
from flowtrace.ml.calibration import calibrate
from flowtrace.ml.features import CATEGORICAL
from flowtrace.ml.model_io import load_model
from flowtrace.ml.train import day_of
from flowtrace.pipeline.cases import build_cases
from flowtrace.settings import cfg, settings

DAY_MS = 86_400_000


def hub_degrees(tx: pl.LazyFrame, first: int, days: int) -> dict[str, int]:
    """Degree of every account over the `days` before `first`, as the pipeline's HUB_DEGREES query counts it."""
    w = tx.filter((pl.col("ts") >= first - days * DAY_MS) & (pl.col("ts") < first) & (pl.col("src") != pl.col("dst")))
    deg = pl.concat([w.select(pl.col("src").alias("a")), w.select(pl.col("dst").alias("a"))]).group_by("a").len().collect()
    return dict(zip(deg["a"].to_list(), deg["len"].to_list()))


def day_cases(day: pl.DataFrame, p_raw: np.ndarray, rules: dict, degrees: dict[str, int]) -> list[dict]:
    tx = day.with_columns(pl.Series("p", p_raw), pl.Series("p_cal", calibrate(p_raw, rules))).to_dicts()
    return build_cases(tx, [], "DAY", rules, degrees)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=date.fromisoformat, default=date(2022, 9, 9))
    args = ap.parse_args()
    s, rules = settings(), cfg()
    d = day_of(args.day)
    feats = pl.scan_parquet(s.processed_dir / "features.parquet").filter(pl.col("day") == d).collect()
    bundle = load_model()
    x = feats.select(bundle["features"]).to_pandas()
    for c in CATEGORICAL:
        x[c] = x[c].astype("category")
    p_raw = bundle["model"].predict_proba(x)[:, 1]

    scan = pl.scan_parquet(s.processed_dir / "transactions.parquet")
    day = feats.select("tx_id").join(
        scan.select("tx_id", "src", "dst", "ts", "amount_usd", "payment_format", "is_laundering").collect(),
        on="tx_id", how="left", maintain_order="left")  # keeps the feature row order, so p_raw lines up
    first = int(day["ts"].min())
    degrees = hub_degrees(scan.select("src", "dst", "ts"), first, rules["cases"]["hub_lookback_days"])
    cases = day_cases(day, p_raw, rules, degrees)
    cap = rules["cases"]["daily_capacity"]
    out = {"day": str(args.day), "kind": "full day (offline, no detectors)", "bar": rules["cases"]["flag_probability"],
           "laundering_rate": round(float(day["is_laundering"].mean()), 5),
           **funnel(cases, day.to_dicts(), attempt_keys(), cap)}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
