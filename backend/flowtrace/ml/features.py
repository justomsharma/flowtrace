"""Transaction-level features for the triage model.

Point-in-time rule: a transaction on day D only sees transactions up to the end of day D.
That matches end-of-day batch monitoring, which is how bank transaction monitoring runs.
Nothing from day D+1 or later is used, and account IDs are never features.

Feature families (after IBM's Graph Feature Preprocessor, simplified):
  edge       amount, format, currency/bank crossing, self-transfer, round amount, hour
  pair       prior history between the same two accounts (strongest legitimacy signal)
  account    same-day fan-in/out, volumes and pass-through for sender and receiver
  history    prior-day activity of sender and receiver (baseline)
  2-hop      same-day scatter-gather paths and reciprocal (2-cycle) flows
  entity     owner type, same-owner transfers, owner's account count
"""
from __future__ import annotations

import polars as pl

DAY_MS = 86_400_000
HUB_CAP = 50  # intermediaries busier than this per day are skipped for 2-hop counts

CATEGORICAL = ["payment_format", "src_entity_type", "dst_entity_type"]


def _account_day(tx: pl.DataFrame) -> pl.DataFrame:
    """Per (account, day) send/receive aggregates, self-transfers excluded."""
    t = tx.filter(pl.col("src") != pl.col("dst"))
    out = t.group_by("src", "day").agg(
        pl.len().alias("out_cnt"),
        pl.col("dst").n_unique().alias("out_distinct"),
        pl.col("amount_usd").sum().alias("out_sum"),
    ).rename({"src": "acct"})
    inc = t.group_by("dst", "day").agg(
        pl.len().alias("in_cnt"),
        pl.col("src").n_unique().alias("in_distinct"),
        pl.col("amount_usd").sum().alias("in_sum"),
    ).rename({"dst": "acct"})
    ad = out.join(inc, on=["acct", "day"], how="full", coalesce=True).fill_null(0)
    ad = ad.with_columns(
        (pl.col("out_sum") / (pl.col("in_sum") + 1.0)).alias("passthrough"),
    )
    # History: cumulative activity strictly before this day.
    ad = ad.sort("acct", "day").with_columns(
        (pl.col("out_cnt").cum_sum().over("acct") - pl.col("out_cnt")).alias("hist_out_cnt"),
        (pl.col("in_cnt").cum_sum().over("acct") - pl.col("in_cnt")).alias("hist_in_cnt"),
        (pl.col("day").cum_count().over("acct") - 1).alias("hist_active_days"),
    )
    return ad


def _two_hop(tx: pl.DataFrame, ad: pl.DataFrame) -> pl.DataFrame:
    """Same-day count of intermediaries x with src->x->dst (scatter-gather / layering)."""
    pairs = tx.filter(pl.col("src") != pl.col("dst")).select("day", "src", "dst").unique()
    busy = ad.filter((pl.col("in_cnt") + pl.col("out_cnt")) > HUB_CAP).select(
        pl.col("acct").alias("x"), "day"
    )
    first = pairs.rename({"dst": "x"}).join(busy, on=["x", "day"], how="anti")
    second = pairs.rename({"src": "x"})
    paths = first.join(second, on=["day", "x"]).filter(
        (pl.col("src") != pl.col("dst")) & (pl.col("x") != pl.col("dst"))
    )
    return paths.group_by("day", "src", "dst").agg(pl.len().alias("sg_paths"))


def build_features(tx: pl.DataFrame, accounts: pl.DataFrame) -> pl.DataFrame:
    """tx needs the transactions.parquet columns; returns tx_id, day, label and features."""
    tx = tx.sort("ts").with_columns(
        (pl.col("ts") // DAY_MS).alias("day"),
        pl.from_epoch("ts", time_unit="ms").dt.hour().alias("hour"),
    )

    ad = _account_day(tx)
    sg = _two_hop(tx, ad)

    # Pair history: earlier transfers between the same ordered pair (strictly earlier by row order).
    tx = tx.with_columns(
        pl.col("ts").cum_count().over("src", "dst").sub(1).alias("pair_prior_cnt"),
        pl.col("ts").cum_count().over("src", "dst", "amount_paid").sub(1).alias("pair_same_amt_prior"),
        ((pl.col("ts") - pl.col("ts").min().over("src", "dst")) / 3_600_000).alias("pair_age_h"),
    )
    # Reciprocal flow on the same day (2-cycle A->B->A).
    rev = tx.select(pl.col("dst").alias("src"), pl.col("src").alias("dst"), "day").unique().with_columns(
        pl.lit(1, pl.Int8).alias("reciprocal_same_day")
    )
    tx = tx.join(rev, on=["src", "dst", "day"], how="left")

    src_cols = {c: f"src_{c}" for c in ad.columns if c not in ("acct", "day")}
    dst_cols = {c: f"dst_{c}" for c in ad.columns if c not in ("acct", "day")}
    tx = tx.join(ad.rename({"acct": "src", **src_cols}), on=["src", "day"], how="left")
    tx = tx.join(ad.rename({"acct": "dst", **dst_cols}), on=["dst", "day"], how="left")
    tx = tx.join(sg, on=["day", "src", "dst"], how="left")

    acc = accounts.select("account_id", "entity_id", "entity_type")
    n_acc = acc.group_by("entity_id").agg(pl.len().alias("entity_n_accounts"))
    acc = acc.join(n_acc, on="entity_id", how="left")
    tx = tx.join(acc.rename({c: f"src_{c}" for c in acc.columns if c != "account_id"}),
                 left_on="src", right_on="account_id", how="left")
    tx = tx.join(acc.rename({c: f"dst_{c}" for c in acc.columns if c != "account_id"}),
                 left_on="dst", right_on="account_id", how="left")

    feats = tx.select(
        "tx_id", "day", "ts", "src", "dst", "is_laundering",
        pl.col("amount_usd").log1p().alias("log_amount"),
        pl.col("payment_format").cast(pl.Categorical),
        (pl.col("payment_currency") != pl.col("receiving_currency")).cast(pl.Int8).alias("cross_currency"),
        (pl.col("src").str.split(":").list.first() != pl.col("dst").str.split(":").list.first())
        .cast(pl.Int8).alias("cross_bank"),
        (pl.col("src") == pl.col("dst")).cast(pl.Int8).alias("self_transfer"),
        ((pl.col("amount_paid") % 100) == 0).cast(pl.Int8).alias("round_100"),
        "hour",
        "pair_prior_cnt", "pair_same_amt_prior", "pair_age_h",
        pl.col("reciprocal_same_day").fill_null(0),
        pl.col("sg_paths").fill_null(0),
        *[pl.col(c) for c in src_cols.values()],
        *[pl.col(c) for c in dst_cols.values()],
        pl.col("src_entity_type").cast(pl.Categorical),
        pl.col("dst_entity_type").cast(pl.Categorical),
        (pl.col("src_entity_id") == pl.col("dst_entity_id")).cast(pl.Int8).alias("same_entity"),
        "src_entity_n_accounts", "dst_entity_n_accounts",
    )
    numeric = [c for c, t in feats.schema.items()
               if c not in FEATURE_EXCLUDE and t.is_numeric() and c != "hour"]
    return feats.with_columns(pl.col(numeric).cast(pl.Float32))


FEATURE_EXCLUDE = {"tx_id", "day", "ts", "src", "dst", "is_laundering"}


def feature_columns(df: pl.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in FEATURE_EXCLUDE]
