"""Build demo upload files from the held-out test days, in the original IBM CSV format.

  demo_mixed_2022-09-09.csv     laundering attempts that start on Sep 9 + busy legitimate accounts
                                that look like fan-out / pass-through (false-positive candidates)
                                + random background transfers
  legit_lookalikes_2022-09-09.csv  only legitimate accounts that a naive fan-out rule would flag

Every chosen account contributes its complete day, so same-day features match training.
The "Is Laundering" column is kept so results can be scored; the agent never sees it.

Run:  python -m flowtrace.data.samples
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from flowtrace.settings import settings

DAY = date(2022, 9, 9)


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def to_ibm(df: pl.DataFrame) -> pl.DataFrame:
    """Back to the raw 11-column layout (bank and account split again)."""
    return df.sort("ts").select(
        pl.from_epoch("ts", time_unit="ms").dt.strftime("%Y/%m/%d %H:%M").alias("Timestamp"),
        pl.col("src").str.split(":").list.get(0).str.zfill(3).alias("From Bank"),
        pl.col("src").str.split(":").list.get(1).alias("Account"),
        pl.col("dst").str.split(":").list.get(0).str.zfill(3).alias("To Bank"),
        pl.col("dst").str.split(":").list.get(1).alias("Account_2"),
        pl.col("amount_received").round(2).alias("Amount Received"),
        pl.col("receiving_currency").alias("Receiving Currency"),
        pl.col("amount_paid").round(2).alias("Amount Paid"),
        pl.col("payment_currency").alias("Payment Currency"),
        pl.col("payment_format").alias("Payment Format"),
        pl.col("is_laundering").alias("Is Laundering"),
    )


def write(df: pl.DataFrame, name: str) -> None:
    out = settings().raw_dir.parent / "samples"
    out.mkdir(parents=True, exist_ok=True)
    csv = to_ibm(df).write_csv()
    # The IBM header repeats "Account" for the receiver.
    csv = csv.replace("Account_2", "Account", 1)
    (out / name).write_text(csv, encoding="utf-8")
    print(f"{name}: {df.height:,} rows, {int(df['is_laundering'].sum())} laundering, "
          f"{pl.concat([df['src'], df['dst']]).n_unique():,} accounts")


def main() -> None:
    s = settings()
    tx = pl.read_parquet(s.processed_dir / "transactions.parquet")
    pat = pl.read_parquet(s.processed_dir / "patterns.parquet")
    d0, d1 = _ms(DAY), _ms(DAY) + 86_400_000
    day = tx.filter(pl.col("ts").is_between(d0, d1, closed="left") & (pl.col("src") != pl.col("dst")))

    # Laundering attempts that start on the sample day: one or two of each typology.
    att = (pat.join(tx.select("tx_id", "ts"), on="tx_id").group_by("attempt_id")
           .agg(pl.col("ts").min().alias("start"), pl.col("typology").first())
           .filter(pl.col("start").is_between(d0, d1, closed="left")).sort("attempt_id"))
    chosen = att.group_by("typology", maintain_order=True).head(2)["attempt_id"]
    att_tx = pat.filter(pl.col("attempt_id").is_in(chosen.to_list())).join(tx, on="tx_id").select(tx.columns)
    bad_accts = pl.concat([att_tx["src"], att_tx["dst"]]).unique()

    # Legitimate look-alikes: accounts with no laundering that fan out to 5+ receivers today,
    # most of whom they have paid before.
    prior = tx.filter(pl.col("ts") < d0).group_by("src", "dst").agg(pl.len().alias("prior"))
    fan = (day.join(prior, on=["src", "dst"], how="left").group_by("src")
           .agg(pl.col("dst").n_unique().alias("k"), (pl.col("prior").fill_null(0) > 0).mean().alias("est"),
                pl.col("is_laundering").max().alias("bad"))
           .filter((pl.col("k").is_between(5, 25)) & (pl.col("bad") == 0) & (pl.col("est") >= 0.6))
           .sort("k", descending=True))
    lookalike = fan.head(12)["src"]

    focus = pl.concat([bad_accts, lookalike]).unique()
    focus_day = day.filter(pl.col("src").is_in(focus.to_list()) | pl.col("dst").is_in(focus.to_list()))
    background = day.filter(~pl.col("src").is_in(focus.to_list()) & ~pl.col("dst").is_in(focus.to_list())).sample(1500, seed=7)
    mixed = pl.concat([att_tx, focus_day, background]).unique("tx_id")
    write(mixed, f"demo_mixed_{DAY}.csv")

    # Small quick-test file: three schemes (split, loop, collect-then-scatter) + two honest look-alikes.
    small_att = (att.filter(pl.col("attempt_id").is_in(chosen.to_list()))
                 .filter(pl.col("typology").is_in(["FAN-OUT", "CYCLE", "GATHER-SCATTER"]))
                 .group_by("typology", maintain_order=True).head(1)["attempt_id"])
    small_tx = pat.filter(pl.col("attempt_id").is_in(small_att.to_list())).join(tx, on="tx_id").select(tx.columns)
    small_focus = pl.concat([small_tx["src"], small_tx["dst"], lookalike.head(2)]).unique().to_list()
    small_day = day.filter(pl.col("src").is_in(small_focus) | pl.col("dst").is_in(small_focus))
    small_bg = day.filter(~pl.col("src").is_in(small_focus) & ~pl.col("dst").is_in(small_focus)).sample(60, seed=11)
    write(pl.concat([small_tx, small_day, small_bg]).unique("tx_id"), f"small_test_{DAY}.csv")

    look_day = day.filter(pl.col("src").is_in(lookalike.to_list()) | pl.col("dst").is_in(lookalike.to_list()))
    write(look_day, f"legit_lookalikes_{DAY}.csv")
    print(f"attempts included: {dict(att.filter(pl.col('attempt_id').is_in(chosen.to_list())).group_by('typology').len().iter_rows())}")


if __name__ == "__main__":
    main()
