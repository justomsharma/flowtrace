"""The demo history graph: only the history the bundled samples need, small enough for FalkorDB Cloud's free tier.

The full history (515K accounts, 3.6M transfers) needs about 2.7 GB in FalkorDB; the free tier has 100 MB. Every
query the app runs only reads the history of a few accounts, so the demo graph keeps exactly that:

  record   upload each demo sample into the FULL graph, investigate every case (rules, fixed workflow and, with
           --llm, the LLM agent), plan its holds, and run every agent tool at its widest on every account the case
           has (explore, bundled samples only: the LLM picks its own steps), recording every query and its
           result. The upload is removed afterwards.
  keep     * every history transfer of every account an agent tool, the tracer or the planner asked about
           * every history transfer a query returned (trace hops, cycles, shared counterparties ...)
           * every history transfer a detector matched (the same Cypher, with the tx list not cut at 60)
           * hub checks only compare a count with a threshold (cases.hub_degree, trace.hub_degree,
             legitimacy.hub_receiver_in_degree), so for a hub the demo keeps just enough of its transfers to stay
             over each threshold. Three bank accounts with 7K-139K transfers are hubs this way.
  write    data/demo/history/*.csv.gz (nodes and edges) plus the scoring history: data/demo/processed/
           transactions.parquet and accounts.parquet, only the payments of the samples' accounts, which is all the
           model's features read (pipeline.batch.score).

Load it into any FalkorDB (plain Cypher, so it works on FalkorDB Cloud):
  python -m flowtrace.demo.history load                    into GRAPH_NAME on FALKORDB_HOST
Build it again (needs the full graph and data/processed from the README's data steps):
  python -m flowtrace.demo.history build [--llm]

flowtrace.demo.verify checks that the demo graph gives the same cases, investigations and hold plans.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from flowtrace.graph import client
from flowtrace.graph import queries as Q
from flowtrace.settings import ROOT, cfg, settings

DEMO = ROOT / "data" / "demo"
HISTORY = DEMO / "history"
SAMPLES = ROOT / "data" / "samples"
UNTIL = datetime(2022, 9, 9, tzinfo=timezone.utc)
UNTIL_MS = int(UNTIL.timestamp() * 1000)
DAY_MS = 86_400_000


def demo_samples() -> list[str]:
    """What the public demo can upload (data/samples/*.csv), plus the Sep 9 evaluation slice."""
    return sorted(p.name for p in SAMPLES.glob("*.csv")) + ["eval/day_slice_2022-09-09.csv"]


# --- record ------------------------------------------------------------------------------------

def _full_detectors() -> dict[str, str]:
    """The detector queries with their transfer lists uncut, so every matched transfer is known."""
    from flowtrace.detectors import alerts
    out = {}
    for name in ("FAN_OUT", "FAN_IN", "PASS_THROUGH", "SCATTER_GATHER", "CYCLE"):
        q = getattr(alerts, name)
        out[q] = q.replace("txs[0..60] AS tx_ids", "txs AS tx_ids").replace("(in_tx + out_tx)[0..60]", "(in_tx + out_tx)")
    return out


def record(samples: list[str], policies: list[str], llm_samples: set[str]) -> list[dict]:
    """Run the app's work on the full graph and keep every query with its result."""
    from flowtrace.actions.planner import plan_holds
    from flowtrace.agent.investigate import investigate, new_context
    from flowtrace.pipeline.batch import batch_id_for, process_batch

    names = {v: k for k, v in vars(Q).items() if isinstance(v, str) and k.isupper()}
    full = _full_detectors()
    log: list[dict] = []
    phase = ["?"]
    real = client._run

    def spy(fn, cypher, params, timeout):
        res = real(fn, cypher, params, timeout)
        entry = {"phase": phase[0], "q": names.get(cypher, cypher), "params": params, "rows": res.rows}
        if cypher in full:  # what the detector matched, uncut
            entry["matched"] = real(fn, full[cypher], params, timeout).rows
        log.append(entry)
        return res

    rules, noop = cfg(), (lambda e: None)
    client._run = spy
    try:
        for name in samples:
            data = (SAMPLES / name).read_bytes()
            batch_id = batch_id_for(data)
            phase[0] = "pipeline"
            t = time.time()
            res = process_batch(data, name, noop)
            try:
                for c in res["cases"].values():
                    for p in policies + (["llm"] if name in llm_samples else []):
                        phase[0] = p
                        r = investigate(new_context(c, rules, noop), policy_name=p, use_llm_report=False, save_memory=False)
                        phase[0] = "planner"
                        plan_holds(c, rules, r)
                    if not name.startswith("eval/"):  # what the public demo can upload
                        phase[0] = "explore"
                        explore(new_context(c, rules, noop), rules)
            finally:
                client.rw(Q.DELETE_BATCH, {"batch": batch_id})
            print(f"  recorded {name}: {len(res['cases'])} cases, {len(log)} queries so far ({time.time() - t:.0f} s)")
    finally:
        client._run = real
    return log


def explore(ctx, rules: dict) -> None:
    """Every tool the agent has, at its widest, on every account of the case: traces both ways at the most hops,
    and every account tool. The LLM picks its own steps; in 63 recorded LLM investigations every tool call was on
    a case account (none on an account a trace found), and two traced backward where the rules trace forward."""
    from flowtrace.agent.registry import call
    from flowtrace.agent.tools import ToolError

    def run(name: str, args: dict) -> None:
        try:
            call(name, args, ctx)
        except ToolError:
            pass

    for acc in list(ctx.accounts):
        for direction in ("forward", "backward"):
            run("trace_funds", {"account": acc, "direction": direction, "max_hops": rules["trace"]["max_hops"]})
        for name, args in (("account_profile", {}), ("counterparty_history", {"direction": "out"}),
                           ("counterparty_history", {"direction": "in"}), ("recurring_flows", {}), ("find_cycles", {}),
                           ("owner_network", {}), ("case_memory", {})):
            run(name, {"account": acc, **args})
    for direction in ("out", "in"):
        run("shared_counterparties", {"accounts": list(ctx.accounts)[:10], "direction": direction})


# --- keep ----------------------------------------------------------------------------------------

THRESHOLD_ONLY = {"HUB_DEGREES", "DEGREES", "DEGREES_UNTIL"}  # results are only compared with a threshold
NOT_ANCHORS = {"visited"}


def _anchors(params: dict) -> set[str]:
    out: set[str] = set()
    for k, v in params.items():
        if k in NOT_ANCHORS:
            continue
        if k == "id" and isinstance(v, str):
            out.add(v)
        elif k in ("ids", "cps") and isinstance(v, list):
            out |= {x for x in v if isinstance(x, str)}
        elif k in ("frontier", "since") and isinstance(v, list):
            out |= {f["id"] for f in v}
    return out


def _tx_ids(rows: list[dict]) -> set[str]:
    out: set[str] = set()
    for r in rows:
        for k, v in r.items():
            if k == "tx_id" and isinstance(v, str):
                out.add(v)
            elif k in ("tx_ids", "in_tx", "out_tx") and isinstance(v, list):
                out |= {x for x in v if isinstance(x, str)}
    return out


def requirements(log: list[dict], history: pl.DataFrame, mega: int) -> tuple[set[str], set[str], dict]:
    """(history tx_ids to keep, accounts to keep, stats). history: every transfer before UNTIL, self-transfers out."""
    deg = defaultdict(int)
    for s, d in zip(history["src"].to_list(), history["dst"].to_list()):
        deg[s] += 1
        deg[d] += 1
    exact: set[str] = set()
    counted: dict[str, set[str]] = defaultdict(set)   # threshold-only query -> accounts
    seen_tx: set[str] = set()
    matched: dict[str, set[str]] = defaultdict(set)   # account -> detector-matched tx (megas are capped below)
    accounts: set[str] = set()
    siblings_of: set[str] = set()
    for e in log:
        ids = _anchors(e["params"])
        accounts |= ids
        if e["q"] in THRESHOLD_ONLY:
            counted[e["q"]] |= ids
        elif e["q"] == "INSERT_BATCH":
            accounts |= {x for r in e["params"]["rows"] for x in (r["src"], r["dst"])}
        elif "matched" in e:
            for r in e["matched"]:
                matched[r["account"]] |= _tx_ids([r])
        elif e["q"] not in ("HISTORY_UNTIL", "DELETE_BATCH"):
            exact |= ids
        if e["q"] == "SIBLING_ACCOUNTS":
            siblings_of |= ids
        seen_tx |= _tx_ids(e["rows"])
    megas = {a for a in exact | set(matched) if deg[a] > mega}

    h = history.with_columns(pl.col("ts").alias("t"))
    keep: set[str] = set(seen_tx)
    small = list(exact - megas)
    keep |= set(h.filter(pl.col("src").is_in(small) | pl.col("dst").is_in(small))["tx_id"].to_list())
    for a, txs in matched.items():
        keep |= set(sorted(txs)[:200]) if a in megas else txs

    # Hubs keep just enough transfers to stay over every threshold they are checked against.
    c = cfg()
    hub_window = (UNTIL_MS - c["cases"]["hub_lookback_days"] * DAY_MS, UNTIL_MS)
    need_window, need_total, need_in = c["cases"]["hub_degree"], c["trace"]["hub_degree"] + 1, \
        c["legitimacy"]["hub_receiver_in_degree"]
    thresholded = set().union(*counted.values()) | megas
    pad = h.filter(pl.col("src").is_in(list(thresholded)) | pl.col("dst").is_in(list(thresholded))).sort("ts", descending=True)
    kept = pad.filter(pl.col("tx_id").is_in(list(keep)))
    have = defaultdict(lambda: {"in": 0, "out": 0, "win": 0})
    for s, d, ts in zip(kept["src"].to_list(), kept["dst"].to_list(), kept["ts"].to_list()):
        win = hub_window[0] <= ts < hub_window[1]
        for a, k in ((s, "out"), (d, "in")):
            have[a][k] += 1
            have[a]["win"] += win
    full = defaultdict(lambda: {"in": 0, "out": 0, "win": 0})
    rows = list(zip(pad["tx_id"].to_list(), pad["src"].to_list(), pad["dst"].to_list(), pad["ts"].to_list()))
    for _, s, d, ts in rows:
        win = hub_window[0] <= ts < hub_window[1]
        for a, k in ((s, "out"), (d, "in")):
            full[a][k] += 1
            full[a]["win"] += win
    padded = 0
    for tx, s, d, ts in rows:  # newest first
        if tx in keep:
            continue
        win = hub_window[0] <= ts < hub_window[1]
        for a, k in ((s, "out"), (d, "in")):
            if a not in thresholded:
                continue
            f, g = full[a], have[a]
            short = ((win and f["win"] >= need_window and g["win"] < need_window)
                     or (f["in"] + f["out"] >= need_total and g["in"] + g["out"] < need_total)
                     or (k == "in" and f["in"] >= need_in and g["in"] < need_in)
                     or (k == "out" and f["out"] >= 10 and g["out"] < 10))   # recurring.infer_purpose: payer pays 10+
            if short:
                keep.add(tx)
                padded += 1
                for b, kb in ((s, "out"), (d, "in")):
                    have[b][kb] += 1
                    have[b]["win"] += win
                break

    kept = history.filter(pl.col("tx_id").is_in(list(keep)))
    accounts |= set(kept["src"].to_list()) | set(kept["dst"].to_list())
    stats = {"exact_accounts": len(small), "hubs_capped": sorted(megas), "detector_accounts": len(matched),
             "threshold_accounts": len(thresholded), "padding_transfers": padded, "transfers": kept.height,
             "siblings_of": len(siblings_of)}
    return set(kept["tx_id"].to_list()), accounts | {f"SIBLINGS:{a}" for a in siblings_of}, stats


# --- write ---------------------------------------------------------------------------------------

def write(keep_tx: set[str], accounts: set[str], samples: list[str], stats: dict) -> None:
    from flowtrace.data.prepare import build_transactions
    from flowtrace.pipeline.batch import parse_csv

    s = settings()
    HISTORY.mkdir(parents=True, exist_ok=True)
    siblings_of = {a.split(":", 1)[1] for a in accounts if a.startswith("SIBLINGS:")}
    accounts = {a for a in accounts if not a.startswith("SIBLINGS:")}
    acc = pl.read_parquet(s.processed_dir / "accounts.parquet")
    tx = pl.read_parquet(s.processed_dir / "transactions.parquet")
    hist = tx.filter((pl.col("ts") < UNTIL_MS) & (pl.col("src") != pl.col("dst")))
    self_cnt = tx.filter((pl.col("ts") < UNTIL_MS) & (pl.col("src") == pl.col("dst"))).group_by("src").agg(
        pl.len().alias("self_cnt")).rename({"src": "account_id"})
    # Same-owner accounts: every account of an entity whose accounts the agent listed.
    ents = acc.filter(pl.col("account_id").is_in(list(siblings_of)))["entity_id"].to_list()
    accounts |= set(acc.filter(pl.col("entity_id").is_in(ents))["account_id"].to_list())
    nodes = (acc.filter(pl.col("account_id").is_in(list(accounts)))
             .join(self_cnt, on="account_id", how="left").with_columns(pl.col("self_cnt").fill_null(0)))
    edges = hist.filter(pl.col("tx_id").is_in(list(keep_tx)))

    _gz(nodes.select(pl.col("account_id").alias("id"), pl.col("bank_id").alias("bank"), "entity_id", "entity_type",
                     "self_cnt").sort("id"), HISTORY / "accounts.csv.gz")
    _gz(nodes.select("entity_id", "entity_name", "entity_type").unique("entity_id").sort("entity_id")
        .select(pl.col("entity_id").alias("id"), pl.col("entity_name").alias("name"), pl.col("entity_type").alias("type")),
        HISTORY / "entities.csv.gz")
    _gz(edges.sort("ts", "tx_id").select(
        "tx_id", "src", "dst", "ts", pl.col("amount_usd").alias("amount"), pl.col("payment_format").alias("fmt"),
        pl.col("payment_currency").alias("cur"),
        (pl.col("payment_currency") != pl.col("receiving_currency")).cast(pl.Int8).alias("xcur")),
        HISTORY / "transfers.csv.gz")

    # Scoring history: every earlier payment of the samples' accounts (exactly what batch.score reads).
    # score() reads every payment before the upload's first one, so a sample starting after midnight also reads
    # that day's earlier payments: keep each sample's accounts up to its own start.
    rates = json.loads((s.processed_dir / "fx_rates.json").read_text())
    sample_accounts: set[str] = set()
    scoring_parts = []
    for name in samples:
        b = build_transactions(parse_csv((SAMPLES / name).read_bytes()), rates)
        accs = list(set(b["src"].to_list()) | set(b["dst"].to_list()))
        sample_accounts |= set(accs)
        scoring_parts.append(tx.with_row_index("_row").filter(
            (pl.col("ts") < int(b["ts"].min())) & (pl.col("src").is_in(accs) | pl.col("dst").is_in(accs))))
    out = DEMO / "processed"
    out.mkdir(parents=True, exist_ok=True)
    scoring = pl.concat(scoring_parts).unique("_row").sort("_row").drop("_row")  # the full file's row order
    scoring.write_parquet(out / "transactions.parquet", compression="zstd")
    # Every account of the owners involved: the features count how many accounts an owner holds.
    involved = sample_accounts | accounts | set(scoring["src"].to_list()) | set(scoring["dst"].to_list())
    owners = acc.filter(pl.col("account_id").is_in(list(involved)))["entity_id"].unique().to_list()
    acc.filter(pl.col("entity_id").is_in(owners)).write_parquet(out / "accounts.parquet", compression="zstd")
    # The model the app scores with, written from the trained bundle (LF line endings: LightGBM cannot read CRLF).
    from flowtrace.ml.model_io import load_model
    bundle = load_model()
    (DEMO / "model").mkdir(exist_ok=True)
    with open(DEMO / "model" / "triage_lgbm.txt", "w", newline="\n") as f:
        f.write(bundle["model"].booster_.model_to_string())
    with open(DEMO / "model" / "triage_features.json", "w", newline="\n") as f:
        f.write(json.dumps({"features": bundle["features"], "threshold": bundle["threshold"]}, indent=2))
    stats |= {"accounts": nodes.height, "transfers": edges.height, "scoring_payments": scoring.height,
              "samples": samples, "history_until": UNTIL.date().isoformat(),
              "full_graph": {"accounts": acc.height, "transfers": hist.height}}
    (DEMO / "history.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


def _gz(df: pl.DataFrame, path: Path) -> None:
    with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as f:  # mtime=0: the same data gives the same file
        df.write_csv(f)


# --- load ----------------------------------------------------------------------------------------

LOAD_ACCOUNTS = """
UNWIND $rows AS r
CREATE (:Account {id: r.id, bank: r.bank, entity_id: r.entity_id, entity_type: r.entity_type, self_cnt: r.self_cnt})
"""
LOAD_ENTITIES = """
UNWIND $rows AS r
CREATE (:Entity {id: r.id, name: r.name, type: r.type})
"""
LOAD_OWNED_BY = """
UNWIND $rows AS r
MATCH (a:Account {id: r.id}), (e:Entity {id: r.entity_id})
CREATE (a)-[:OWNED_BY]->(e)
"""
LOAD_TRANSFERS = """
UNWIND $rows AS r
MATCH (s:Account {id: r.src}), (d:Account {id: r.dst})
CREATE (s)-[:TRANSFER {tx_id: r.tx_id, ts: r.ts, amount: r.amount, fmt: r.fmt, cur: r.cur, xcur: r.xcur}]->(d)
"""


def _rows(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load(replace: bool = True, chunk: int = 2000) -> dict:
    """Create the demo history graph in GRAPH_NAME with plain Cypher (works on FalkorDB Cloud)."""
    from flowtrace.graph.load import INDEXES, wait_for_indexes
    t = time.time()
    if replace:
        try:
            client.graph().delete()
        except Exception:  # no graph yet
            pass
    for q in INDEXES[:2]:      # node indexes first: the edge inserts look accounts up by id
        client.rw(q)
    wait_for_indexes()
    accounts = [{**r, "self_cnt": int(r["self_cnt"])} for r in _rows(HISTORY / "accounts.csv.gz")]
    entities = _rows(HISTORY / "entities.csv.gz")
    transfers = [{**r, "ts": int(r["ts"]), "amount": float(r["amount"]), "xcur": r["xcur"] == "1"}
                 for r in _rows(HISTORY / "transfers.csv.gz")]
    for q, rows in ((LOAD_ACCOUNTS, accounts), (LOAD_ENTITIES, entities),
                    (LOAD_OWNED_BY, [{"id": a["id"], "entity_id": a["entity_id"]} for a in accounts]),
                    (LOAD_TRANSFERS, transfers)):
        for i in range(0, len(rows), chunk):
            client.rw(q, {"rows": rows[i:i + chunk]})
    client.rw(INDEXES[2])
    wait_for_indexes()
    client.rw(Q.SET_HISTORY_UNTIL, {"until": UNTIL_MS})
    return {"accounts": len(accounts), "entities": len(entities), "transfers": len(transfers),
            "seconds": round(time.time() - t, 1)}


class GraphUnreachable(Exception):
    """FalkorDB did not answer the history check: a connection error, a timeout, a refused login, any error."""


def history_state() -> str:
    """"loaded" or "missing", from FalkorDB's own answer. The history cutoff marker is written last, so it means a
    complete load; "missing" is a graph that does not exist or has no marker. Anything else raises GraphUnreachable:
    an error is never read as an empty graph (that would wipe the demo whenever the network blips)."""
    import redis
    try:
        rows = client.ro(Q.HISTORY_UNTIL, {}, timeout=5000).rows
    except redis.exceptions.ResponseError as e:
        if "empty key" in str(e):  # FalkorDB: no graph under this name
            return "missing"
        raise GraphUnreachable(str(e)) from e
    except Exception as e:
        raise GraphUnreachable(str(e)) from e
    return "loaded" if rows and rows[0]["until"] is not None else "missing"


class HistoryWatch:
    """Decides when to load the demo history: only when FalkorDB answers that it is missing on `confirm` checks in
    a row. An error, or the history being there, starts the count again. A failed load waits backoff_s before the
    next try, doubling up to max_backoff_s.

    check() -> "loaded" | "missing" (not yet confirmed) | "unreachable" | "waiting" (backing off) | "reload"."""

    def __init__(self, state=history_state, confirm: int = 3, backoff_s: float = 60, max_backoff_s: float = 1800,
                 clock=time.monotonic) -> None:
        self.state, self.confirm, self.clock = state, confirm, clock
        self.backoff_s, self.max_backoff_s = backoff_s, max_backoff_s
        self.streak = 0
        self.wait_s = backoff_s
        self.next_try = 0.0

    def check(self) -> str:
        if self.clock() < self.next_try:
            return "waiting"
        try:
            s = self.state()
        except GraphUnreachable:
            self.streak = 0
            return "unreachable"
        if s != "missing":
            self.streak = 0
            return s
        self.streak += 1
        return "reload" if self.streak >= self.confirm else "missing"

    def loaded(self) -> None:
        self.streak, self.wait_s, self.next_try = 0, self.backoff_s, 0.0

    def failed(self) -> None:
        self.streak = 0
        self.next_try = self.clock() + self.wait_s
        self.wait_s = min(self.wait_s * 2, self.max_backoff_s)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="record on the full graph and write data/demo")
    b.add_argument("--llm", action="store_true", help="also record LLM investigations of the bundled samples")
    b.add_argument("--mega", type=int, default=5000, help="accounts with more history transfers keep only a capped share")
    sub.add_parser("load", help="load data/demo/history into GRAPH_NAME")
    a = ap.parse_args()
    if a.cmd == "build":
        samples = demo_samples()
        llm = {n for n in samples if not n.startswith("eval/")} if a.llm else set()
        log = record(samples, ["heuristic", "fixed"], llm)
        s = settings()
        tx = pl.read_parquet(s.processed_dir / "transactions.parquet")
        hist = tx.filter((pl.col("ts") < UNTIL_MS) & (pl.col("src") != pl.col("dst")))
        keep, accounts, stats = requirements(log, hist, a.mega)
        write(keep, accounts, samples, stats)
    else:
        print(json.dumps(load(), indent=2))


if __name__ == "__main__":
    main()
