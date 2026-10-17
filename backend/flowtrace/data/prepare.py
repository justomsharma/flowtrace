"""Turn the raw IBM AML CSVs into clean, typed parquet files.

Outputs (data/processed/):
  transactions.parquet  one row per transfer, stable tx_id, epoch-ms ts, USD amount
  accounts.parquet      one row per account with owning entity and entity type
  patterns.parquet      tx_id -> laundering attempt id + typology (ground-truth evidence paths)
  fx_rates.json         currency -> units per USD, derived from cross-currency rows

Run:  python -m flowtrace.data.prepare
"""
from __future__ import annotations

import json
import re

import polars as pl

from flowtrace.settings import settings

TRANS_COLS = [
    "timestamp", "from_bank", "from_account", "to_bank", "to_account",
    "amount_received", "receiving_currency", "amount_paid", "payment_currency",
    "payment_format", "is_laundering",
]
TRANS_SCHEMA = {
    "timestamp": pl.Utf8, "from_bank": pl.Utf8, "from_account": pl.Utf8,
    "to_bank": pl.Utf8, "to_account": pl.Utf8, "amount_received": pl.Float64,
    "receiving_currency": pl.Utf8, "amount_paid": pl.Float64,
    "payment_currency": pl.Utf8, "payment_format": pl.Utf8, "is_laundering": pl.Int8,
}
PATTERN_HEADER = re.compile(r"BEGIN LAUNDERING ATTEMPT - ([A-Z-]+)(?::\s*(.*))?")


def account_id(bank: pl.Expr, account: pl.Expr) -> pl.Expr:
    """Account numbers repeat across banks, so the key is bank+account.

    Bank codes are zero-padded in the transactions file ("010") but not in the
    accounts file ("10"), so normalise through int.
    """
    return bank.cast(pl.Int64).cast(pl.Utf8) + ":" + account


def read_transactions(path) -> pl.DataFrame:
    df = pl.read_csv(path, has_header=True, new_columns=TRANS_COLS, schema=TRANS_SCHEMA)
    return df.with_columns(
        pl.col("timestamp").str.strptime(pl.Datetime("ms"), "%Y/%m/%d %H:%M").alias("dt"),
    )


def derive_fx_rates(tx: pl.DataFrame) -> dict[str, float]:
    """Units of each currency per 1 USD, from rows paid in USD and received in another currency.

    The generator uses fixed rates, so the median is stable. Rows paid in X and received in USD
    are used as a fallback for currencies that never appear on the receiving side of a USD payment.
    """
    rates: dict[str, float] = {"US Dollar": 1.0}
    fwd = (
        tx.filter((pl.col("payment_currency") == "US Dollar") & (pl.col("receiving_currency") != "US Dollar"))
        .group_by("receiving_currency")
        .agg((pl.col("amount_received") / pl.col("amount_paid")).median().alias("rate"))
    )
    rates.update(dict(zip(fwd["receiving_currency"], fwd["rate"])))
    back = (
        tx.filter((pl.col("receiving_currency") == "US Dollar") & (pl.col("payment_currency") != "US Dollar"))
        .group_by("payment_currency")
        .agg((pl.col("amount_paid") / pl.col("amount_received")).median().alias("rate"))
    )
    for cur, rate in zip(back["payment_currency"], back["rate"]):
        rates.setdefault(cur, rate)
    missing = set(tx["payment_currency"].unique()) - set(rates)
    if missing:
        raise ValueError(f"No FX rate derivable for {missing}")
    return rates


def build_transactions(raw: pl.DataFrame, rates: dict[str, float]) -> pl.DataFrame:
    rate = pl.col("payment_currency").replace_strict(rates, return_dtype=pl.Float64)
    return (
        raw.with_row_index("row")
        .with_columns(
            ("T" + pl.col("row").cast(pl.Utf8)).alias("tx_id"),
            account_id(pl.col("from_bank"), pl.col("from_account")).alias("src"),
            account_id(pl.col("to_bank"), pl.col("to_account")).alias("dst"),
            pl.col("dt").dt.epoch("ms").alias("ts"),
            (pl.col("amount_paid") / rate).round(2).alias("amount_usd"),
        )
        .select(
            "tx_id", "ts", "src", "dst", "amount_usd",
            "amount_paid", "payment_currency", "amount_received", "receiving_currency",
            "payment_format", "is_laundering",
        )
    )


def parse_patterns(path) -> pl.DataFrame:
    """Parse the BEGIN/END blocks into rows tagged with attempt id and typology."""
    rows, attempt, typology, detail = [], -1, None, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = PATTERN_HEADER.match(line)
            if m:
                attempt += 1
                typology, detail = m.group(1), (m.group(2) or "").strip()
                continue
            if line.startswith("END LAUNDERING ATTEMPT"):
                typology = None
                continue
            if typology:
                rows.append(line.split(",") + [attempt, typology, detail])
    df = pl.DataFrame(
        rows, orient="row",
        schema=TRANS_COLS + ["attempt_id", "typology", "typology_detail"],
    )
    return df.with_columns(
        pl.col("timestamp").str.strptime(pl.Datetime("ms"), "%Y/%m/%d %H:%M").dt.epoch("ms").alias("ts"),
        account_id(pl.col("from_bank"), pl.col("from_account")).alias("src"),
        account_id(pl.col("to_bank"), pl.col("to_account")).alias("dst"),
        pl.col("amount_paid").cast(pl.Float64),
        pl.col("attempt_id").cast(pl.Int32),
    )


def link_patterns(tx: pl.DataFrame, pat: pl.DataFrame) -> pl.DataFrame:
    """Map pattern rows to tx_ids by exact (ts, src, dst, amount_paid, format) match."""
    keys = ["ts", "src", "dst", "amount_paid", "payment_format"]
    linked = pat.join(tx.select(keys + ["tx_id"]), on=keys, how="left")
    unmatched = linked["tx_id"].null_count()
    if unmatched:
        print(f"  warning: {unmatched} pattern rows not found in transactions")
    # A duplicate transaction row would match twice; keep one tx per pattern row.
    return linked.drop_nulls("tx_id").unique(["attempt_id", "tx_id"]).select(
        "tx_id", "attempt_id", "typology", "typology_detail"
    )


def build_accounts(path, tx: pl.DataFrame) -> pl.DataFrame:
    acc = pl.read_csv(path, schema_overrides={"Bank ID": pl.Utf8, "Account Number": pl.Utf8})
    acc = acc.select(
        account_id(pl.col("Bank ID"), pl.col("Account Number")).alias("account_id"),
        pl.col("Bank ID").cast(pl.Int64).cast(pl.Utf8).alias("bank_id"),
        pl.col("Bank Name").alias("bank_name"),
        pl.col("Entity ID").alias("entity_id"),
        pl.col("Entity Name").alias("entity_name"),
        pl.col("Entity Name").str.replace(r"\s*#.*$", "").alias("entity_type"),
    ).unique("account_id")
    seen = pl.concat([tx.select(pl.col("src").alias("account_id")), tx.select(pl.col("dst").alias("account_id"))]).unique()
    out = seen.join(acc, on="account_id", how="left")
    missing = out["entity_id"].null_count()
    print(f"  accounts in transactions: {out.height:,}  without accounts-file entry: {missing:,}")
    return out


def main() -> None:
    s = settings()
    s.processed_dir.mkdir(parents=True, exist_ok=True)
    ds = s.dataset

    print("reading transactions ...")
    raw = read_transactions(s.raw_dir / f"{ds}_Trans.csv")
    rates = derive_fx_rates(raw)
    tx = build_transactions(raw, rates)
    del raw
    tx.write_parquet(s.processed_dir / "transactions.parquet")
    (s.processed_dir / "fx_rates.json").write_text(json.dumps(rates, indent=2))

    print("parsing patterns ...")
    pat = link_patterns(tx, parse_patterns(s.raw_dir / f"{ds}_Patterns.txt"))
    pat.write_parquet(s.processed_dir / "patterns.parquet")

    print("building accounts ...")
    acc = build_accounts(s.raw_dir / f"{ds}_accounts.csv", tx)
    acc.write_parquet(s.processed_dir / "accounts.parquet")

    lo, hi = tx["ts"].min(), tx["ts"].max()
    print(
        f"\ntransactions {tx.height:,} | laundering {tx['is_laundering'].sum():,} "
        f"({tx['is_laundering'].mean():.4%}) | accounts {acc.height:,}\n"
        f"time range {pl.from_epoch(pl.Series([lo, hi]), time_unit='ms').to_list()}\n"
        f"pattern-linked tx {pat.height:,} in {pat['attempt_id'].n_unique()} attempts\n"
        f"typologies {dict(pat.group_by('typology').len().iter_rows())}"
    )


if __name__ == "__main__":
    main()
