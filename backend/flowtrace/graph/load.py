"""Load transaction history into FalkorDB with the binary bulk loader.

Graph model
  (:Account {id, bank, entity_id, entity_type})-[:OWNED_BY]->(:Entity {id, name, type})
  (:Account)-[:TRANSFER {tx_id, ts, amount, fmt, cur, xcur}]->(:Account)

Only history (before the test period) is loaded; test-period batches arrive later through the
upload API, like a bank's daily feed. Self-transfers (mostly "Reinvestment") are left out of the
graph because they carry no relationship information; their count is kept on the account.
Labels are never written to the graph, so nothing the agent reads can leak ground truth.

Run:  python -m flowtrace.graph.load [--until 2022-09-09]
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone

import polars as pl

from flowtrace.graph import queries as Q
from flowtrace.graph.client import graph, rw
from flowtrace.settings import settings

INDEXES = [
    "CREATE INDEX FOR (a:Account) ON (a.id)",
    "CREATE INDEX FOR (e:Entity) ON (e.id)",
    "CREATE INDEX FOR ()-[t:TRANSFER]-() ON (t.ts, t.tx_id)",
]


def export_csvs(until: date) -> dict[str, str]:
    s = settings()
    out = s.processed_dir / "bulk"
    out.mkdir(parents=True, exist_ok=True)
    cutoff = int(datetime(until.year, until.month, until.day, tzinfo=timezone.utc).timestamp() * 1000)

    tx = pl.read_parquet(s.processed_dir / "transactions.parquet").filter(pl.col("ts") < cutoff)
    self_cnt = tx.filter(pl.col("src") == pl.col("dst")).group_by("src").agg(pl.len().alias("self_cnt"))
    edges = tx.filter(pl.col("src") != pl.col("dst"))

    acc = pl.read_parquet(s.processed_dir / "accounts.parquet")
    acc = acc.join(self_cnt.rename({"src": "account_id"}), on="account_id", how="left").with_columns(
        pl.col("self_cnt").fill_null(0)
    )
    paths = {
        "Account": out / "Account.csv",
        "Entity": out / "Entity.csv",
        "TRANSFER": out / "TRANSFER.csv",
        "OWNED_BY": out / "OWNED_BY.csv",
    }
    acc.select(
        pl.col("account_id").alias("id:ID(Account)"),
        pl.col("bank_id").alias("bank:STRING"),
        pl.col("entity_id").alias("entity_id:STRING"),
        pl.col("entity_type").alias("entity_type:STRING"),
        pl.col("self_cnt").alias("self_cnt:INT"),
    ).write_csv(paths["Account"])
    acc.select("entity_id", "entity_name", "entity_type").unique("entity_id").select(
        pl.col("entity_id").alias("id:ID(Entity)"),
        pl.col("entity_name").alias("name:STRING"),
        pl.col("entity_type").alias("type:STRING"),
    ).write_csv(paths["Entity"])
    acc.select(
        pl.col("account_id").alias(":START_ID(Account)"),
        pl.col("entity_id").alias(":END_ID(Entity)"),
    ).write_csv(paths["OWNED_BY"])
    edges.select(
        pl.col("src").alias(":START_ID(Account)"),
        pl.col("dst").alias(":END_ID(Account)"),
        pl.col("tx_id").alias("tx_id:STRING"),
        pl.col("ts").alias("ts:LONG"),
        pl.col("amount_usd").alias("amount:DOUBLE"),
        pl.col("payment_format").alias("fmt:STRING"),
        pl.col("payment_currency").alias("cur:STRING"),
        (pl.col("payment_currency") != pl.col("receiving_currency")).alias("xcur:BOOL"),
    ).write_csv(paths["TRANSFER"])
    print(f"exported {acc.height:,} accounts, {edges.height:,} transfers (before {until})")
    return {k: str(v) for k, v in paths.items()}


def bulk_insert(paths: dict[str, str]) -> None:
    s = settings()
    # falkordb-bulk-loader calls csv.field_size_limit(sys.maxsize) at import, which overflows a
    # C long on Windows. Shrink maxsize for the import only.
    real = sys.maxsize
    sys.maxsize = 2**31 - 1
    try:
        from falkordb_bulk_loader.bulk_insert import bulk_insert as cli
    finally:
        sys.maxsize = real
    auth = f":{s.falkordb_password}@" if s.falkordb_password else ""
    args = [
        s.graph_name, "-u", f"redis://{auth}{s.falkordb_host}:{s.falkordb_port}", "--enforce-schema",
        "-N", "Account", paths["Account"], "-N", "Entity", paths["Entity"],
        "-R", "TRANSFER", paths["TRANSFER"], "-R", "OWNED_BY", paths["OWNED_BY"],
    ]
    cli.main(args=args, standalone_mode=False)


def create_indexes() -> None:
    for q in INDEXES:
        try:
            rw(q)
        except Exception as e:  # already exists
            print(f"  index skipped: {e}")


def index_statuses() -> list[str]:
    return [r["status"] for r in rw("CALL db.indexes() YIELD status RETURN status").rows]


def wait_for_indexes(statuses=index_statuses, poll_s: float = 2.0, timeout_s: float = 900) -> None:
    """FalkorDB builds indexes in the background; until they are OPERATIONAL every lookup by id scans
    the whole graph (an upload of 2,000 payments times out). Return only once they are ready."""
    end = time.monotonic() + timeout_s
    while True:
        pending = [s for s in statuses() if s != "OPERATIONAL"]
        if not pending:
            return
        if time.monotonic() > end:
            raise TimeoutError(f"indexes still building after {timeout_s:.0f}s: {pending}")
        print(f"  waiting for indexes: {pending}")
        time.sleep(poll_s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="2022-09-09", help="load transfers strictly before this date")
    ap.add_argument("--replace", action="store_true", help="delete the existing graph first")
    a = ap.parse_args()
    if a.replace:
        try:
            graph().delete()
        except Exception:
            pass
    paths = export_csvs(date.fromisoformat(a.until))
    bulk_insert(paths)
    create_indexes()
    wait_for_indexes()
    until = date.fromisoformat(a.until)
    rw(Q.SET_HISTORY_UNTIL, {"until": int(datetime(until.year, until.month, until.day, tzinfo=timezone.utc).timestamp() * 1000)})
    print(rw("MATCH (a:Account) RETURN count(a) AS accounts").rows,
          rw("MATCH ()-[t:TRANSFER]->() RETURN count(t) AS transfers").rows)


if __name__ == "__main__":
    main()
