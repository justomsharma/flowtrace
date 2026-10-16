"""Process an uploaded batch of transactions (IBM AML CSV format) into ranked cases.

  1 parse + validate        IBM columns; optional "Is Laundering" kept aside, never shown to the agent
  2 score                   triage model with features computed from history + batch (point-in-time)
  3 load into FalkorDB      batch transfers join the history graph, tagged with the batch id
  4 detect                  Cypher typology detectors over the batch window
  5 build cases             suspect-centred, hub-aware, capped, ranked for the team's daily capacity
"""
from __future__ import annotations

import hashlib
import io
import json
import time
from typing import Callable

import redis
import numpy as np
import polars as pl

from flowtrace.data.prepare import TRANS_COLS, build_transactions
from flowtrace.detectors.alerts import run_detectors
from flowtrace.graph import queries as Q
from flowtrace.graph.client import ro, rw
from flowtrace.ml.calibration import calibrate
from flowtrace.ml.features import CATEGORICAL, build_features
from flowtrace.ml.model_io import load_model
from flowtrace.pipeline.cases import build_cases
from flowtrace.settings import cfg, settings

Emit = Callable[[dict], None]


def parse_csv(data: bytes) -> pl.DataFrame:
    head = data.split(b"\n", 1)[0].decode("utf-8", "replace")
    n_cols = len(head.split(","))
    if n_cols not in (10, 11):
        raise ValueError(f"expected the IBM AML transaction format (10 or 11 columns), got {n_cols}")
    cols = TRANS_COLS[:n_cols]
    df = pl.read_csv(io.BytesIO(data), has_header=True, new_columns=cols, infer_schema=False)
    df = df.with_columns(
        pl.col("timestamp").str.strptime(pl.Datetime("ms"), "%Y/%m/%d %H:%M").alias("dt"),
        pl.col("amount_received").cast(pl.Float64), pl.col("amount_paid").cast(pl.Float64),
        (pl.col("is_laundering").cast(pl.Int8) if "is_laundering" in cols else pl.lit(None, pl.Int8)).alias("is_laundering"),
    )
    bad = df.filter(pl.col("dt").is_null() | pl.col("amount_paid").is_null())
    if bad.height:
        raise ValueError(f"{bad.height} rows have an unreadable timestamp or amount")
    return df


ID_COLS = ["ts", "src", "dst", "amount_paid", "payment_currency", "amount_received", "receiving_currency",
           "payment_format"]


def payment_ids(batch: pl.DataFrame) -> list[str]:
    """Stable id from the payment's own fields; a counter keeps genuine repeats in one file apart."""
    seen: dict[str, int] = {}
    out = []
    for row in batch.select(ID_COLS).iter_rows():
        key = "|".join(map(str, row))
        n = seen.get(key, 0)
        seen[key] = n + 1
        out.append("P" + hashlib.sha1(f"{key}|{n}".encode()).hexdigest()[:16])
    return out


def batch_id_for(data: bytes) -> str:
    """Same file, same batch: re-uploading replaces the earlier result instead of adding a new one."""
    return "B" + hashlib.sha1(data).hexdigest()[:6].upper()


def reject_history_overlap(batch: pl.DataFrame) -> None:
    """Payments before the graph's history cutoff are already loaded (with their original ids), so
    uploading them again would double them. Refuse with a way forward instead."""
    rows = ro(Q.HISTORY_UNTIL, {}).rows
    if not rows or rows[0]["until"] is None:
        return  # graph loaded before the cutoff marker existed; reload it to enable this check
    until = rows[0]["until"]
    n = batch.filter(pl.col("ts") < until).height
    if n:
        day = time.strftime("%Y-%m-%d", time.gmtime(until / 1000))
        raise ValueError(f"{n:,} payments are dated before {day} and are already in the graph's history. "
                         f"Upload a later day, or replay this one by reloading the graph up to it: "
                         f"python -m flowtrace.graph.load --until <day> --replace")


def score(batch: pl.DataFrame) -> np.ndarray:
    """Model score per batch transfer, with features computed exactly as in training."""
    s = settings()
    bundle = load_model()
    t0 = batch["ts"].min()
    accts = pl.concat([batch["src"], batch["dst"]]).unique()
    hist = (pl.scan_parquet(s.processed_dir / "transactions.parquet")
            .filter((pl.col("ts") < t0) & (pl.col("src").is_in(accts.to_list()) | pl.col("dst").is_in(accts.to_list())))
            .collect())
    acc = pl.read_parquet(s.processed_dir / "accounts.parquet")
    both = pl.concat([hist, batch.with_columns(pl.col("is_laundering").fill_null(0))], how="vertical_relaxed")
    feats = build_features(both, acc).filter(pl.col("tx_id").is_in(batch["tx_id"].to_list()))
    order = feats.select("tx_id").with_row_index("i")
    x = feats.select(bundle["features"]).to_pandas()
    for c in CATEGORICAL:
        x[c] = x[c].astype("category")
    p = bundle["model"].predict_proba(x)[:, 1]
    lookup = dict(zip(order["tx_id"].to_list(), p))
    return np.array([lookup.get(t, 0.0) for t in batch["tx_id"].to_list()])


def load_into_graph(batch: pl.DataFrame, batch_id: str, emit: Emit) -> float:
    rows = batch.select(
        "tx_id", "src", "dst", "ts", pl.col("amount_usd").alias("amount"), pl.col("payment_format").alias("fmt"),
        pl.col("payment_currency").alias("cur"),
        (pl.col("payment_currency") != pl.col("receiving_currency")).alias("xcur"),
    ).to_dicts()
    t0 = time.perf_counter()
    for i in range(0, len(rows), 2000):
        rw(Q.INSERT_BATCH, {"rows": rows[i:i + 2000], "batch": batch_id})
        emit({"type": "stage", "stage": "graph", "status": "running", "done": min(i + 2000, len(rows)), "total": len(rows)})
    return (time.perf_counter() - t0) * 1000


def process_batch(data: bytes, filename: str, emit: Emit) -> dict:
    rules = cfg()
    s = settings()
    batch_id = batch_id_for(data)
    emit({"type": "batch", "batch_id": batch_id})  # the UI opens exactly this batch when the job is done
    timings: dict[str, float] = {}

    def stage(name, status, **kw):
        emit({"type": "stage", "stage": name, "status": status, **kw})

    t = time.perf_counter()
    stage("parse", "running")
    raw = parse_csv(data)
    rates = json.loads((s.processed_dir / "fx_rates.json").read_text())
    batch = build_transactions(raw, rates)
    batch = batch.with_columns(pl.Series("tx_id", payment_ids(batch)))
    reject_history_overlap(batch)
    timings["parse_ms"] = (time.perf_counter() - t) * 1000
    stage("parse", "done", rows=batch.height, accounts=pl.concat([batch["src"], batch["dst"]]).n_unique(),
          ms=round(timings["parse_ms"]))
    # Stream the payments so the UI can draw the graph while the rest of the pipeline runs.
    emit({"type": "graph_rows", "links": [
        {"tx_id": t, "source": s_, "target": d_, "amount": round(a_, 2)}
        for t, s_, d_, a_ in zip(batch["tx_id"].to_list(), batch["src"].to_list(), batch["dst"].to_list(),
                                 batch["amount_usd"].to_list())]})

    t = time.perf_counter()
    stage("score", "running")
    p = score(batch)
    p_cal = calibrate(p, rules)
    bar = rules["cases"]["flag_probability"]
    timings["score_ms"] = (time.perf_counter() - t) * 1000
    stage("score", "done", ms=round(timings["score_ms"]), above_bar=int((p_cal >= bar).sum()),
          histogram=np.histogram(p_cal, bins=10, range=(0, 1))[0].tolist())
    emit({"type": "scores", "unusual": [{"tx_id": t, "p": round(float(v), 3)} for t, v in zip(batch["tx_id"].to_list(), p_cal.tolist())
                                         if v >= bar]})

    stage("graph", "running", done=0, total=batch.height)
    try:
        timings["graph_ms"] = load_into_graph(batch, batch_id, emit)
    except (ConnectionError, OSError, redis.exceptions.ConnectionError) as e:
        raise RuntimeError("FalkorDB is not reachable. Start it with `docker compose up -d` and load history with "
                           "`python -m flowtrace.graph.load --replace`.") from e
    stage("graph", "done", ms=round(timings["graph_ms"]), rows=batch.height)

    t = time.perf_counter()
    stage("detect", "running")
    accounts = pl.concat([batch["src"], batch["dst"]]).unique().to_list()
    top = batch.with_columns(pl.Series("p", p)).sort("p", descending=True).head(30)
    cycle_accounts = list(dict.fromkeys(top["src"].to_list()))[:30]
    log: list[dict] = []
    hits = run_detectors(accounts, int(batch["ts"].min()), int(batch["ts"].max()) + 1, rules, cycle_accounts, log)
    timings["detect_ms"] = (time.perf_counter() - t) * 1000
    stage("detect", "done", ms=round(timings["detect_ms"]), hits=len(hits),
          by_detector={k: sum(1 for h in hits if h["detector"] == k) for k in sorted({h["detector"] for h in hits})},
          queries=log)
    emit({"type": "hits", "hits": [{"detector": h["detector"], "account": h["account"], "tx_ids": h["tx_ids"]} for h in hits]})

    t = time.perf_counter()
    tx = batch.with_columns(pl.Series("p", p), pl.Series("p_cal", p_cal)).to_dicts()
    # Degree over the history window before the batch: accounts at or above cases.hub_degree are hubs.
    first = int(batch["ts"].min())
    h0 = first - rules["cases"]["hub_lookback_days"] * 86_400_000
    degrees = {r["id"]: r["out_deg"] + r["in_deg"]
               for r in ro(Q.HUB_DEGREES, {"ids": accounts, "h0": h0, "t0": first}, timeout=60_000).rows}
    cases = build_cases(tx, hits, batch_id, rules, degrees)
    timings["cases_ms"] = (time.perf_counter() - t) * 1000
    stage("cases", "done", cases=len(cases))

    # Every payment of the batch for the 3D view: amount, model score and which case it belongs to.
    in_case = {t: c["case_id"] for c in cases for t in c["tx_ids"]}
    graph = [{"tx_id": t, "source": s_, "target": d_, "amount": round(a_, 2), "p": round(float(p_), 3), "case": in_case.get(t)}
             for t, s_, d_, a_, p_ in zip(batch["tx_id"].to_list(), batch["src"].to_list(), batch["dst"].to_list(),
                                          batch["amount_usd"].to_list(), p.tolist())]

    labelled = batch["is_laundering"].null_count() < batch.height
    summary = {
        "batch_id": batch_id, "filename": filename, "rows": batch.height,
        "accounts": len(accounts), "window": [int(batch["ts"].min()), int(batch["ts"].max())],
        "volume_usd": round(float(batch["amount_usd"].sum()), 2),
        "score_histogram": np.histogram(p, bins=20, range=(0, 1))[0].tolist(),
        "flagged_tx": len({t for c in cases for t in c["tx_ids"]}),
        "capacity": rules["cases"]["daily_capacity"],
        "detector_hits": len(hits), "timings_ms": {k: round(v) for k, v in timings.items()},
        "has_labels": bool(labelled),
        "labelled_laundering": int(batch["is_laundering"].fill_null(0).sum()) if labelled else None,
        "cases": [{k: c[k] for k in ("case_id", "primary", "accounts", "ml_score", "case_score", "tier", "signals", "shape",
                                     "amount", "detector_hits", "ground_truth")} | {"n_tx": len(c["tx_ids"])} for c in cases],
    }
    # internals: what eval.funnel needs to re-run case building at other bars; the API does not store it.
    return {"summary": summary, "cases": {c["case_id"]: c for c in cases}, "graph": graph,
            "internals": {"tx": tx, "hits": hits, "degrees": degrees}}
