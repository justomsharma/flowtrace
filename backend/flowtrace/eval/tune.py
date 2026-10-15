"""Fit the numbers case building depends on, from the validation days only (Sep 7-8).

  calibration          isotonic map raw model score -> probability, on every validation payment
  detectors.precision  share of each detector's hits whose transfers include labelled laundering
                       (add-one smoothed),
                       running the production Cypher detectors over a random sample of each day's
                       active accounts (a ratio, so a uniform sample estimates it without bias)
  cases.hub_degree     99th percentile of account degree over the last hub_lookback_days training days

Detectors only read transfers with ts < end of day, so the graph loaded up to Sep 9 is valid here.
Prints a block to paste into config.yaml and writes the counts behind it to data/runs/tune.json.

Run (FalkorDB loaded):  python -m flowtrace.eval.tune [--accounts N]
"""
from __future__ import annotations

import argparse
import json
import random
import time
from datetime import date, datetime, timezone

import numpy as np
import polars as pl

from flowtrace.detectors.alerts import run_detectors
from flowtrace.ml.calibration import fit
from flowtrace.ml.features import CATEGORICAL
from flowtrace.ml.model_io import load_model
from flowtrace.ml.train import SPLITS
from flowtrace.settings import cfg, settings

VAL_DAYS = [date(2022, 9, 7), date(2022, 9, 8)]
DAY_MS = 86_400_000


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def calibration() -> tuple[dict, dict]:
    s = settings()
    bundle = load_model()
    a, b = SPLITS["val"]
    val = pl.scan_parquet(s.processed_dir / "features.parquet").filter(pl.col("day").is_between(a, b)).collect()
    x = val.select(bundle["features"]).to_pandas()
    for c in CATEGORICAL:
        x[c] = x[c].astype("category")
    p = bundle["model"].predict_proba(x)[:, 1]
    y = val["is_laundering"].to_numpy()
    return fit(p, y), {"n": int(len(y)), "positives": int(y.sum())}


def hub_degree() -> tuple[int, dict]:
    """p99 of account degree over the last hub_lookback_days training days: the same window length the
    pipeline's HUB_DEGREES query counts before each batch, so the threshold means the same at runtime."""
    _, b = SPLITS["train"]
    a = b - cfg()["cases"]["hub_lookback_days"] + 1
    tx = (pl.scan_parquet(settings().processed_dir / "transactions.parquet")
          .filter((pl.col("ts") >= a * DAY_MS) & (pl.col("ts") < (b + 1) * DAY_MS) & (pl.col("src") != pl.col("dst"))))
    deg = pl.concat([tx.select(pl.col("src").alias("a")), tx.select(pl.col("dst").alias("a"))]).group_by("a").len().collect()
    q = int(deg["len"].quantile(0.99))
    return q, {"accounts": deg.height, "p99": q, "max": int(deg["len"].max()), "days": [a, b]}


def detector_precision(n_accounts: int, chunk: int = 2000) -> tuple[dict, dict]:
    s, rules = settings(), cfg()
    tx = pl.scan_parquet(s.processed_dir / "transactions.parquet")
    bad = set(tx.filter(pl.col("is_laundering") == 1).select("tx_id").collect()["tx_id"].to_list())
    hits_n: dict[str, int] = {}
    true_n: dict[str, int] = {}
    errors = []
    rng = random.Random(0)
    for d in VAL_DAYS:
        d0, d1 = _ms(d), _ms(d) + DAY_MS
        day = tx.filter((pl.col("ts") >= d0) & (pl.col("ts") < d1) & (pl.col("src") != pl.col("dst")))
        active = sorted(set(day.select("src").collect()["src"].to_list()) | set(day.select("dst").collect()["dst"].to_list()))
        ids = rng.sample(active, min(n_accounts, len(active)))
        t = time.perf_counter()
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            try:
                hits = run_detectors(part, d0, d1, rules, part)
            except Exception as e:  # a timeout on one chunk is reported, not hidden
                errors.append(f"{d} chunk {i}: {type(e).__name__}: {e}")
                continue
            for h in hits:
                hits_n[h["detector"]] = hits_n.get(h["detector"], 0) + 1
                true_n[h["detector"]] = true_n.get(h["detector"], 0) + int(any(x in bad for x in h["tx_ids"]))
        print(f"  {d}: {len(ids):,} of {len(active):,} active accounts in {time.perf_counter() - t:.0f}s")
    # Laplace (add-one) estimate: rare detectors with few hits are pulled toward 1/2 by little, never to 0 or 1.
    prec = {k: round((true_n[k] + 1) / (hits_n[k] + 2), 4) for k in hits_n}
    return prec, {"hits": hits_n, "true": true_n, "errors": errors, "estimate": "(true + 1) / (hits + 2)"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", type=int, default=1_000_000, help="cap per day; default covers every active account")
    args = ap.parse_args()
    print("calibration on validation payments ...")
    cal, cal_info = calibration()
    print("hub degree on training days ...")
    hub, hub_info = hub_degree()
    print("detector precision on validation days ...")
    prec, prec_info = detector_precision(args.accounts)
    for name in ("FAN-OUT", "FAN-IN", "GATHER-SCATTER", "PASS-THROUGH", "SCATTER-GATHER", "CYCLE"):
        prec.setdefault(name, 0.0)

    report = {"calibration": cal, "calibration_data": cal_info, "hub_degree": hub_info,
              "detector_precision": prec, "detector_counts": prec_info}
    out = settings().raw_dir.parent / "runs" / "tune.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}\n\n# paste into config.yaml")
    print(f"calibration: {{x: {cal['x']}, y: {cal['y']}}}")
    print(f"detectors.precision: {{{', '.join(f'{k}: {v}' for k, v in prec.items())}}}")
    print(f"cases.hub_degree: {hub}")
    print(f"\ncounts: {json.dumps(prec_info)}")


if __name__ == "__main__":
    main()
