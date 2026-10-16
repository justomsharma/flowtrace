"""How much more often payments with a given warning sign turn out to be laundering, measured on the
training days (Sep 1-8) of the dataset. The case page quotes these so each reason says why it matters.

  lift = laundering rate of payments with the sign / laundering rate of all payments

Writes data/processed/signal_rates.json.   Run:  python -m flowtrace.ml.signal_rates
"""
from __future__ import annotations

import json

import polars as pl

from flowtrace.ml.train import SPLITS
from flowtrace.settings import settings


def main() -> None:
    s = settings()
    a, _ = SPLITS["train"]
    _, b = SPLITS["val"]
    f = pl.read_parquet(s.processed_dir / "features.parquet").filter(pl.col("day").is_between(a, b))
    f = f.filter(pl.col("self_transfer") == 0)
    base = f["is_laundering"].mean()

    def lift(mask: pl.Expr, label: str) -> dict:
        sub = f.filter(mask)
        rate = sub["is_laundering"].mean() if sub.height else 0.0
        return {"label": label, "payments": sub.height, "share": round(sub.height / f.height, 4),
                "rate": round(float(rate), 6), "lift": round(float(rate / base), 1) if base else None}

    # Same-day equal splits: a sender's payments that day all within 5% of their mean (3+ payments).
    splits = (f.group_by("src", "day").agg(pl.col("log_amount").exp().alias("amt"))
              .filter(pl.col("amt").list.len() >= 3)
              .with_columns((pl.col("amt").list.std() / pl.col("amt").list.mean()).alias("cv"))
              .filter(pl.col("cv") < 0.05).select("src", "day").with_columns(pl.lit(True).alias("equal")))
    f = f.join(splits, on=["src", "day"], how="left").with_columns(pl.col("equal").fill_null(False))

    rates = {
        "base_rate": round(float(base), 6),
        "NEW_COUNTERPARTIES": lift(pl.col("pair_prior_cnt") == 0, "first payment between the two accounts"),
        "ESTABLISHED_COUNTERPARTIES": lift(pl.col("pair_prior_cnt") >= 3, "accounts that paid each other 3+ times before"),
        "PASS_THROUGH": lift((pl.col("src_in_sum") > 0) & (pl.col("src_passthrough") >= 0.8),
                             "sender paid out 80%+ of what it received that day"),
        "FAST_ONWARD_TRANSFER": lift((pl.col("dst_in_sum") > 0) & (pl.col("dst_passthrough") >= 0.8),
                                     "receiver paid out 80%+ of what it received that day"),
        "DORMANT_THEN_ACTIVE": lift((pl.col("src_hist_active_days") == 0) & (pl.col("src_out_cnt") >= 3),
                                    "sender had no earlier activity, then 3+ payments in a day"),
        "EQUAL_SPLITS": lift(pl.col("equal"), "sender's payments that day were near-identical amounts"),
        "CONVERGENCE": lift(pl.col("sg_paths") >= 2, "money reached one receiver through 2+ middle accounts"),
        "ROUND_TRIP": lift(pl.col("reciprocal_same_day") == 1, "money went back to the sender the same day"),
        "HIGH_RISK_RAIL": lift(pl.col("payment_format").cast(pl.Utf8).is_in(["Cash", "Bitcoin"]), "paid in cash or Bitcoin"),
    }
    rates["CYCLE"] = rates["ROUND_TRIP"]
    rates["CASH_OR_CRYPTO_EXIT"] = rates["HIGH_RISK_RAIL"]
    (s.processed_dir / "signal_rates.json").write_text(json.dumps(rates, indent=2))
    for k, v in rates.items():
        print(k, v)


if __name__ == "__main__":
    main()
