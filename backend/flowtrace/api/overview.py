"""Dataset overview for the landing dashboard, computed once from the processed parquet."""
from __future__ import annotations

import json

import polars as pl

from flowtrace.settings import settings


def build_overview() -> dict:
    s = settings()
    path = s.processed_dir / "overview.json"
    if path.exists():
        return json.loads(path.read_text())

    tx = pl.read_parquet(
        s.processed_dir / "transactions.parquet",
        columns=["ts", "src", "dst", "amount_usd", "payment_format", "is_laundering"],
    )
    pat = pl.read_parquet(s.processed_dir / "patterns.parquet")
    acc = pl.read_parquet(s.processed_dir / "accounts.parquet", columns=["entity_type"])

    daily = (
        tx.with_columns(pl.from_epoch("ts", time_unit="ms").dt.date().cast(pl.Utf8).alias("date"))
        .group_by("date").agg(pl.len().alias("transactions"), pl.col("is_laundering").sum().alias("laundering"))
        .sort("date")
    )
    fmt = (
        tx.group_by("payment_format")
        .agg(pl.len().alias("transactions"), pl.col("is_laundering").sum().alias("laundering"))
        .with_columns((pl.col("laundering") / pl.col("transactions") * 100).alias("rate_pct"))
        .sort("transactions", descending=True)
    )
    typ = (
        pat.group_by("typology")
        .agg(pl.col("attempt_id").n_unique().alias("attempts"), pl.len().alias("transactions"))
        .sort("transactions", descending=True)
    )
    ent = acc.group_by("entity_type").agg(pl.len().alias("accounts")).sort("accounts", descending=True)

    # Relationship history vs laundering rate: the core false-positive insight.
    pair = tx.filter(pl.col("src") != pl.col("dst")).group_by("src", "dst").agg(
        pl.len().alias("n"), pl.col("is_laundering").sum().alias("bad")
    )
    pair = pair.with_columns(
        pl.when(pl.col("n") == 1).then(pl.lit("1"))
        .when(pl.col("n") <= 3).then(pl.lit("2-3"))
        .when(pl.col("n") <= 10).then(pl.lit("4-10"))
        .otherwise(pl.lit(">10")).alias("bucket")
    )
    rel = (
        pair.group_by("bucket")
        .agg(pl.col("n").sum().alias("transactions"), pl.col("bad").sum().alias("laundering"))
        .with_columns((pl.col("laundering") / pl.col("transactions") * 100).alias("rate_pct"))
    )
    order = {"1": 0, "2-3": 1, "4-10": 2, ">10": 3}
    rel_rows = sorted(rel.to_dicts(), key=lambda r: order[r["bucket"]])

    out = {
        "totals": {
            "transactions": tx.height,
            "laundering": int(tx["is_laundering"].sum()),
            "accounts": int(pl.concat([tx["src"], tx["dst"]]).n_unique()),
            "volume_usd": float(tx["amount_usd"].sum()),
            "pattern_attempts": int(pat["attempt_id"].n_unique()),
            "pattern_transactions": pat.height,
        },
        "daily": daily.to_dicts(),
        "by_format": fmt.to_dicts(),
        "typologies": typ.to_dicts(),
        "entity_types": ent.to_dicts(),
        "relationship_history": rel_rows,
    }
    path.write_text(json.dumps(out, indent=2))
    return out
