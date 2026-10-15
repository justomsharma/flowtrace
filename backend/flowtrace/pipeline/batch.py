"""Process an uploaded batch of transactions (IBM AML CSV format) into ranked cases.

  1 parse + validate        IBM columns; optional "Is Laundering" kept aside, never shown to the agent
  2 score                   triage model with features computed from history + batch (point-in-time)
  3 load into FalkorDB      batch transfers join the history graph, tagged with the batch id
  4 detect                  Cypher typology detectors over the batch window
  5 build cases             alerted transfers grouped into connected subgraphs, shape-classified
"""
from __future__ import annotations

import io
import json
import time
import uuid
from typing import Callable

import networkx as nx
import redis
import numpy as np
import polars as pl

from flowtrace.data.prepare import TRANS_COLS, build_transactions
from flowtrace.detectors.alerts import run_detectors
from flowtrace.detectors.typology import classify
from flowtrace.graph import queries as Q
from flowtrace.graph.client import rw
from flowtrace.ml.features import CATEGORICAL, build_features
from flowtrace.ml.model_io import load_model
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


def build_cases(batch: pl.DataFrame, p: np.ndarray, hits: list[dict], batch_id: str, rules: dict) -> list[dict]:
    cand_score = rules["disposition"]["candidate_score"]
    tx = batch.with_columns(pl.Series("p", p)).to_dicts()
    by_id = {t["tx_id"]: t for t in tx}
    hit_tx = {tid for h in hits for tid in h["tx_ids"] if tid in by_id}
    flagged = [t for t in tx if t["p"] >= cand_score or t["tx_id"] in hit_tx]

    g = nx.Graph()
    for t in flagged:
        g.add_edge(t["src"], t["dst"])
    hits_by_acc: dict[str, list[dict]] = {}
    for h in hits:
        hits_by_acc.setdefault(h["account"], []).append(h)

    cases = []
    for comp in nx.connected_components(g):
        ctx_tx = [t for t in flagged if t["src"] in comp]
        if not ctx_tx:
            continue
        weight: dict[str, float] = {}
        for t in ctx_tx:
            for a in (t["src"], t["dst"]):
                weight[a] = weight.get(a, 0) + t["p"] + 0.2
        for a in comp:
            for h in hits_by_acc.get(a, []):
                weight[a] = weight.get(a, 0) + h["strength"]
        primary = max(weight, key=weight.get)
        edges = [{"tx_id": t["tx_id"], "src": t["src"], "dst": t["dst"], "ts": t["ts"], "amount": t["amount_usd"],
                  "fmt": t["payment_format"], "p": round(float(t["p"]), 4)} for t in ctx_tx]
        shape = classify(edges)
        dhits = [{k: h[k] for k in ("detector", "account", "strength", "detail")} for a in comp for h in hits_by_acc.get(a, [])]
        ml = max(t["p"] for t in ctx_tx)
        priority = 0.6 * ml + 0.4 * max([h["strength"] for h in dhits], default=0.0)
        labels = [t["is_laundering"] for t in ctx_tx if t["is_laundering"] is not None]
        cases.append({
            "case_id": "", "batch_id": batch_id, "primary": primary, "accounts": sorted(comp, key=lambda a: -weight.get(a, 0)),
            "tx_ids": [t["tx_id"] for t in ctx_tx], "edges": edges,
            "t0": min(t["ts"] for t in ctx_tx), "t1": max(t["ts"] for t in ctx_tx) + 1,
            "ml_score": round(float(ml), 4), "detector_hits": dhits, "priority": round(priority, 4),
            "shape": {"typology": shape.typology, "confidence": shape.confidence, "why": shape.why, "stats": shape.stats},
            "amount": round(sum(t["amount_usd"] for t in ctx_tx), 2),
            # Ground truth is kept for evaluation only and never passed to the agent.
            "ground_truth": (int(max(labels)) if labels else None),
        })
    cases.sort(key=lambda c: -c["priority"])
    for i, c in enumerate(cases, 1):
        c["case_id"] = f"{batch_id}-C{i:03d}"
    return cases


def process_batch(data: bytes, filename: str, emit: Emit) -> dict:
    rules = cfg()
    s = settings()
    batch_id = "B" + uuid.uuid4().hex[:6].upper()
    timings: dict[str, float] = {}

    def stage(name, status, **kw):
        emit({"type": "stage", "stage": name, "status": status, **kw})

    t = time.perf_counter()
    stage("parse", "running")
    raw = parse_csv(data)
    rates = json.loads((s.processed_dir / "fx_rates.json").read_text())
    batch = build_transactions(raw, rates).with_columns(
        (pl.lit(f"{batch_id}-") + pl.col("tx_id").str.slice(1)).alias("tx_id"))
    timings["parse_ms"] = (time.perf_counter() - t) * 1000
    stage("parse", "done", rows=batch.height, accounts=pl.concat([batch["src"], batch["dst"]]).n_unique(),
          ms=round(timings["parse_ms"]))

    t = time.perf_counter()
    stage("score", "running")
    p = score(batch)
    timings["score_ms"] = (time.perf_counter() - t) * 1000
    stage("score", "done", ms=round(timings["score_ms"]), above_candidate=int((p >= rules["disposition"]["candidate_score"]).sum()),
          histogram=np.histogram(p, bins=10, range=(0, 1))[0].tolist())

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

    t = time.perf_counter()
    cases = build_cases(batch, p, hits, batch_id, rules)
    timings["cases_ms"] = (time.perf_counter() - t) * 1000
    stage("cases", "done", cases=len(cases))

    labelled = batch["is_laundering"].null_count() < batch.height
    summary = {
        "batch_id": batch_id, "filename": filename, "rows": batch.height,
        "accounts": len(accounts), "window": [int(batch["ts"].min()), int(batch["ts"].max())],
        "volume_usd": round(float(batch["amount_usd"].sum()), 2),
        "score_histogram": np.histogram(p, bins=20, range=(0, 1))[0].tolist(),
        "flagged_tx": int(sum(len(c["tx_ids"]) for c in cases)),
        "detector_hits": len(hits), "timings_ms": {k: round(v) for k, v in timings.items()},
        "has_labels": bool(labelled),
        "labelled_laundering": int(batch["is_laundering"].fill_null(0).sum()) if labelled else None,
        "cases": [{k: c[k] for k in ("case_id", "primary", "accounts", "ml_score", "priority", "shape", "amount",
                                     "detector_hits", "ground_truth")} | {"n_tx": len(c["tx_ids"])} for c in cases],
    }
    return {"summary": summary, "cases": {c["case_id"]: c for c in cases}}
