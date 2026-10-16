"""How much more often payments with a given warning sign turn out to be laundering, measured on the training
days (Sep 1-6) of the dataset, each sign the way its evidence code defines it. The validation days (Sep 7-8)
are left out, so the scoring variants can be compared on them honestly.

  lift = laundering rate of payments with the sign / laundering rate of the same payments overall

  NEW_COUNTERPARTIES          the first payment between the two accounts
  ESTABLISHED_COUNTERPARTIES  3+ earlier payments between the two, the first 3+ days before
  PASS_THROUGH                a payment out within 24 h of a payment in of $1,000+, at 0.80-1.05x its size,
                              to someone else than the sender (detectors.passthrough)
  FAST_ONWARD_TRANSFER        the receiver sends on 50%+ of the payment within 24 h (escalators.fast_forward_*)
  DORMANT_THEN_ACTIVE         no earlier outgoing payment, then 3+ in a day (days with 3+ days of history only)
  EQUAL_SPLITS                3+ receivers that day, per-receiver totals within 5% (escalators.equal_split_amount_cv)
  CONVERGENCE                 one beneficiary paid the same day by 2+ middle accounts that one sender paid
  ROUND_TRIP                  money back to the sender within 4 hops, each hop within 72 h (trace.max_gap_hours)
  CYCLE                       a time-ordered loop of 2-4 hops back to the sender within 4 days (find_cycles)
  HIGH_RISK_RAIL              paid in cash or Bitcoin
  CASH_OR_CRYPTO_EXIT         the receiver pays cash or Bitcoin out within 72 h

Loops, convergence and onward sums skip hub accounts (more payments than hub_degree), as the tracer does.

The file keeps raw counts. The disposition reads them through lift(): used only with
disposition.measured_weights.min_laundering labelled laundering payments or more, and shrunk toward 1 with
prior_laundering pseudo laundering payments at the base rate.

Writes data/processed/signal_rates.json.   Run:  python -m flowtrace.ml.signal_rates
"""
from __future__ import annotations

import json
import warnings
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

import polars as pl

from flowtrace.settings import cfg, settings

HOUR = 3_600_000
DAY = 24 * HOUR
TRAIN = (date(2022, 9, 1), date(2022, 9, 6))
DORMANT_MIN_HISTORY_DAYS = 3


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


# --- loader (shared by the disposition and the case page) ---------------------------------------

def rates_path() -> Path:
    return settings().processed_dir / "signal_rates.json"


@lru_cache
def rates() -> dict:
    p = rates_path()
    if not p.exists():
        raise FileNotFoundError(f"{p} is missing but disposition.measured_weights is on: build it with "
                                f"python -m flowtrace.ml.signal_rates, or switch measured_weights off")
    return json.loads(p.read_text())


def enabled(rules: dict) -> bool:
    mw = rules["disposition"].get("measured_weights") or {}
    return bool(mw.get("enabled")) if isinstance(mw, dict) else bool(mw)


def lift(code: str, mw: dict) -> float | None:
    """Shrunk lift of a sign, or None when it is unmeasured or has too few laundering examples to trust."""
    r = rates().get("codes", {}).get(code)
    if not r or r["laundering"] < mw["min_laundering"] or not r["base_rate"]:
        return None
    base, a = r["base_rate"], mw["prior_laundering"]
    return ((r["laundering"] + a) / (r["payments"] + a / base)) / base


# --- measurement (pure functions over a transactions frame) -------------------------------------

def stats(f: pl.DataFrame, col: str, label: str) -> dict:
    n = f.height
    sub = f.filter(pl.col(col))
    k = int(sub["is_laundering"].sum()) if sub.height else 0
    base = float(f["is_laundering"].mean()) if n else 0.0
    return {"label": label, "payments": sub.height, "laundering": k, "share": round(sub.height / n, 6) if n else 0.0,
            "base_rate": round(base, 8), "lift_raw": round((k / sub.height) / base, 3) if sub.height and base else None}


def _degree(t: pl.DataFrame) -> pl.DataFrame:
    return (pl.concat([t.select(pl.col("src").alias("acct")), t.select(pl.col("dst").alias("acct"))])
            .group_by("acct").agg(pl.len().alias("deg")))


def _flag(t: pl.DataFrame, ids: pl.Series, col: str) -> pl.DataFrame:
    return t.with_columns(pl.col("tx_id").is_in(ids.implode()).alias(col))


def pass_through(t: pl.DataFrame, p: dict | None = None, hub_degree: int | None = None) -> pl.DataFrame:
    p = p or cfg()["detectors"]["passthrough"]
    gap = p["max_gap_hours"] * HOUR
    hubs = _degree(t).filter(pl.col("deg") > (hub_degree or cfg()["trace"]["hub_degree"]))["acct"].implode()
    ins = t.filter((pl.col("amount_usd") >= p["min_amount_usd"]) & ~pl.col("dst").is_in(hubs)).select(
        pl.col("dst").alias("acct"), pl.col("src").alias("from"), pl.col("ts").alias("ts_in"), pl.col("amount_usd").alias("amt_in"))
    outs = t.select("tx_id", pl.col("src").alias("acct"), pl.col("dst").alias("to"), "ts", "amount_usd")
    pairs = outs.join(ins, on="acct").filter(
        (pl.col("ts") >= pl.col("ts_in")) & (pl.col("ts") <= pl.col("ts_in") + gap) & (pl.col("to") != pl.col("from"))
        & (pl.col("amount_usd") >= p["ratio_low"] * pl.col("amt_in")) & (pl.col("amount_usd") <= p["ratio_high"] * pl.col("amt_in")))
    return _flag(t, pairs["tx_id"].unique(), "PASS_THROUGH")


def _asof(left: pl.DataFrame, right: pl.DataFrame, **kw) -> pl.DataFrame:
    """join_asof on frames sorted by time; polars cannot check sortedness within "by" groups and warns."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return left.join_asof(right, **kw)


def _sent_by(t: pl.DataFrame) -> pl.DataFrame:
    """Each account's outgoing payments in time order with the running total, for window sums by as-of joins."""
    return (t.select(pl.col("src").alias("acct"), "ts", "amount_usd").sort("acct", "ts")
            .with_columns(pl.col("amount_usd").cum_sum().over("acct").alias("cum")).drop("amount_usd").sort("ts"))


def fast_onward(t: pl.DataFrame, hours: float | None = None, share: float | None = None,
                hub_degree: int | None = None) -> pl.DataFrame:
    esc = cfg()["escalators"]
    hours, share = hours or esc["fast_forward_hours"], share or esc["fast_forward_share"]
    hub_degree = hub_degree or cfg()["trace"]["hub_degree"]
    sent = _sent_by(t)
    # Total B had sent strictly before the payment arrived, and by the end of the window.
    base = t.select("tx_id", pl.col("dst").alias("acct"), "ts", "amount_usd").with_columns(
        (pl.col("ts") - 1).alias("t_before"), (pl.col("ts") + int(hours * HOUR)).alias("t_end")).sort("t_before")
    a = _asof(base, sent.rename({"ts": "t_before"}), on="t_before", by="acct", strategy="backward").rename({"cum": "c0"}).sort("t_end")
    b = _asof(a, sent.rename({"ts": "t_end", "cum": "c1"}), on="t_end", by="acct", strategy="backward")
    hubs = _degree(t).filter(pl.col("deg") > hub_degree)["acct"]
    fast = b.filter(~pl.col("acct").is_in(hubs.implode())).with_columns(
        (pl.col("c1").fill_null(0) - pl.col("c0").fill_null(0)).alias("fwd")).filter(
        pl.min_horizontal(pl.col("fwd") / pl.col("amount_usd"), pl.lit(1.0)) >= share)
    return _flag(t, fast["tx_id"], "FAST_ONWARD_TRANSFER")


def cash_exit(t: pl.DataFrame, formats: list[str] | None = None, hours: float | None = None) -> pl.DataFrame:
    formats = formats or cfg()["escalators"]["high_risk_formats"]
    hours = hours or cfg()["trace"]["max_gap_hours"]
    exits = t.filter(pl.col("payment_format").is_in(formats)).select(pl.col("src").alias("acct"), pl.col("ts").alias("t_exit")).sort("t_exit")
    base = t.select("tx_id", pl.col("dst").alias("acct"), "ts").with_columns(pl.col("ts").alias("t_exit")).sort("t_exit")
    j = _asof(base, exits.with_columns(pl.col("t_exit").alias("hit")), on="t_exit", by="acct", strategy="forward")
    ok = j.filter(pl.col("hit").is_not_null() & (pl.col("hit") <= pl.col("ts") + int(hours * HOUR)))
    return _flag(t, ok["tx_id"], "CASH_OR_CRYPTO_EXIT")


def equal_splits(t: pl.DataFrame, cv_max: float | None = None) -> pl.DataFrame:
    cv_max = cv_max or cfg()["escalators"]["equal_split_amount_cv"]
    day = (pl.col("ts") // DAY).alias("day")
    per = t.with_columns(day).group_by("src", "day", "dst").agg(pl.col("amount_usd").sum().alias("tot"))
    groups = per.group_by("src", "day").agg(pl.len().alias("n"), (pl.col("tot").std(ddof=0) / pl.col("tot").mean()).alias("cv"))
    eq = groups.filter((pl.col("n") >= 3) & (pl.col("cv") < cv_max)).select("src", "day")
    ids = t.with_columns(day).join(eq, on=["src", "day"])["tx_id"]
    return _flag(t, ids, "EQUAL_SPLITS")


def convergence(t: pl.DataFrame, hub_degree: int = 50) -> pl.DataFrame:
    day = (pl.col("ts") // DAY).alias("day")
    d = t.with_columns(day)
    busy = (pl.concat([d.select(pl.col("src").alias("x"), "day"), d.select(pl.col("dst").alias("x"), "day")])
            .group_by("x", "day").agg(pl.len().alias("deg")).filter(pl.col("deg") > hub_degree).select("x", "day"))
    first = d.select("day", "src", pl.col("dst").alias("x")).unique().join(busy, on=["x", "day"], how="anti")
    second = d.select("tx_id", "day", pl.col("src").alias("x"), "dst")
    paths = first.join(second, on=["day", "x"]).filter((pl.col("src") != pl.col("dst")) & (pl.col("x") != pl.col("dst")))
    groups = paths.group_by("day", "src", "dst").agg(pl.col("x").n_unique().alias("mids")).filter(pl.col("mids") >= 2)
    ids = paths.join(groups.select("day", "src", "dst"), on=["day", "src", "dst"])["tx_id"]
    return _flag(t, ids, "CONVERGENCE")


def loops(t: pl.DataFrame, hub_degree: int | None = None, max_hops: int = 4, gap_ms: int | None = None,
          span_ms: int = 4 * DAY) -> pl.DataFrame:
    """ROUND_TRIP (each hop within gap_ms of the one before) and CYCLE (the whole loop within span_ms): the first
    payment of a time-ordered path of up to max_hops that comes back to its sender. Hubs are never passed through."""
    hub_degree = hub_degree or cfg()["trace"]["hub_degree"]
    gap_ms = gap_ms or int(cfg()["trace"]["max_gap_hours"] * HOUR)
    codes = pl.concat([t["src"], t["dst"]]).unique().to_frame("acct").with_row_index("k")
    e = (t.select("tx_id", "src", "dst", "ts")
         .join(codes.rename({"acct": "src", "k": "s"}), on="src").join(codes.rename({"acct": "dst", "k": "d"}), on="dst")
         .with_row_index("i").select("i", "tx_id", "s", "d", "ts"))
    hubs = _degree(t).filter(pl.col("deg") > hub_degree).join(codes, on="acct")["k"]
    nxt = e.filter(~pl.col("s").is_in(hubs.implode())).select(pl.col("s").alias("cur"), pl.col("d").alias("to"), pl.col("ts").alias("t2"))
    out = {}
    for code, step_ok in (("ROUND_TRIP", lambda: pl.col("t2") <= pl.col("last") + gap_ms),
                          ("CYCLE", lambda: pl.col("t2") <= pl.col("first") + span_ms)):
        paths = e.select("i", pl.col("s").alias("origin"), pl.col("d").alias("cur"), pl.col("ts").alias("first"),
                         pl.col("ts").alias("last")).filter(pl.col("cur") != pl.col("origin"))
        home: set[int] = set()
        for _ in range(max_hops - 1):
            step = paths.join(nxt, on="cur").filter((pl.col("t2") >= pl.col("last")) & step_ok())
            home |= set(step.filter(pl.col("to") == pl.col("origin"))["i"].to_list())
            # Only the earliest arrival per (start, account) matters for the next hop of a span-bound loop; a
            # gap-bound one keeps each distinct arrival time.
            paths = (step.filter(pl.col("to") != pl.col("origin"))
                     .select("i", "origin", pl.col("to").alias("cur"), "first", pl.col("t2").alias("last")))
            paths = (paths.group_by("i", "origin", "cur", "first").agg(pl.col("last").min()) if code == "CYCLE"
                     else paths.unique())
            if paths.is_empty():
                break
        out[code] = e.filter(pl.col("i").is_in(sorted(home)))["tx_id"]
    return _flag(_flag(t, out["ROUND_TRIP"], "ROUND_TRIP"), out["CYCLE"], "CYCLE")


def measure(t: pl.DataFrame, feats: pl.DataFrame, first_day_ms: int) -> dict:
    """t: transactions of the measured days (self-transfers removed); feats: the same rows' pair and history features."""
    rules = cfg()
    leg = rules["legitimacy"]["established_relationship"]
    f = t.join(feats.select("tx_id", "pair_prior_cnt", "pair_age_h", "src_hist_out_cnt", "src_out_cnt"), on="tx_id", how="left")
    f = f.with_columns(
        (pl.col("pair_prior_cnt") == 0).alias("NEW_COUNTERPARTIES"),
        ((pl.col("pair_prior_cnt") >= leg["min_prior_tx"]) & (pl.col("pair_age_h") >= 24 * leg["min_age_days"])).alias("ESTABLISHED_COUNTERPARTIES"),
        ((pl.col("src_hist_out_cnt") == 0) & (pl.col("src_out_cnt") >= 3)).alias("DORMANT_THEN_ACTIVE"),
        pl.col("payment_format").is_in(rules["escalators"]["high_risk_formats"]).alias("HIGH_RISK_RAIL"),
    )
    for fn in (pass_through, fast_onward, cash_exit, equal_splits, convergence, loops):
        f = fn(f)
    dormant_days = f.filter(pl.col("ts") >= first_day_ms + DORMANT_MIN_HISTORY_DAYS * DAY)
    labels = {
        "NEW_COUNTERPARTIES": "first payment between the two accounts",
        "ESTABLISHED_COUNTERPARTIES": "3+ earlier payments between the two accounts, the first 3+ days before",
        "PASS_THROUGH": "paid out within 24 h of receiving a similar amount ($1,000+) from someone else",
        "FAST_ONWARD_TRANSFER": "the receiver sent 50%+ of the payment on within 24 h",
        "DORMANT_THEN_ACTIVE": "no earlier outgoing payment, then 3+ payments in a day",
        "EQUAL_SPLITS": "3+ receivers that day with near-identical totals",
        "CONVERGENCE": "one beneficiary reached through 2+ middle accounts of one sender, the same day",
        "ROUND_TRIP": "money back to the sender within 4 hops, each within 72 h",
        "CYCLE": "a time-ordered loop back to the sender within 4 days",
        "HIGH_RISK_RAIL": "paid in cash or Bitcoin",
        "CASH_OR_CRYPTO_EXIT": "the receiver paid out in cash or Bitcoin within 72 h",
    }
    codes = {c: stats(dormant_days if c == "DORMANT_THEN_ACTIVE" else f, c, lab) for c, lab in labels.items()}
    return {"days": [str(TRAIN[0]), str(TRAIN[1])], "payments": f.height, "laundering": int(f["is_laundering"].sum()),
            "base_rate": round(float(f["is_laundering"].mean()), 8), "codes": codes}


def main() -> None:
    s = settings()
    t0, t1 = _ms(TRAIN[0]), _ms(TRAIN[1]) + DAY
    t = (pl.scan_parquet(s.processed_dir / "transactions.parquet")
         .filter((pl.col("ts") >= t0) & (pl.col("ts") < t1) & (pl.col("src") != pl.col("dst")))
         .select("tx_id", "src", "dst", "ts", "amount_usd", "payment_format", "is_laundering").collect())
    feats = (pl.scan_parquet(s.processed_dir / "features.parquet").filter((pl.col("ts") >= t0) & (pl.col("ts") < t1))
             .select("tx_id", "pair_prior_cnt", "pair_age_h", "src_hist_out_cnt", "src_out_cnt").collect())
    out = measure(t, feats, t0)
    rates_path().write_text(json.dumps(out, indent=2))
    mw = cfg()["disposition"]["measured_weights"]
    rates.cache_clear()
    print(f"{out['payments']:,} payments, {out['laundering']:,} laundering, base rate {out['base_rate']:.5f}")
    for k, v in out["codes"].items():
        print(f"  {k:28s} payments={v['payments']:>9,} laundering={v['laundering']:>5,} raw lift={v['lift_raw']}"
              f"  used lift={lift(k, mw)}")


if __name__ == "__main__":
    main()
