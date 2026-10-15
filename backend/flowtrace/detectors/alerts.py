"""Stage A alert detectors: typology patterns found with Cypher over the batch window.

Every detector is a read-only query over the TRANSFER graph, bounded by the batch time window,
and returns the accounts it fired on plus the transfer IDs that triggered it.
"""
from __future__ import annotations

from flowtrace.graph.client import ro

HOUR_MS = 3_600_000

FAN_OUT = """
UNWIND $ids AS id
MATCH (a:Account {id: id})-[t:TRANSFER]->(b:Account)
WHERE t.ts >= $t0 AND t.ts < $t1 AND b.id <> a.id
WITH a, count(DISTINCT b) AS k, sum(t.amount) AS total, collect(t.tx_id) AS txs
WHERE k >= $min
RETURN a.id AS account, k, total, txs[0..60] AS tx_ids
"""

FAN_IN = """
UNWIND $ids AS id
MATCH (b:Account)-[t:TRANSFER]->(a:Account {id: id})
WHERE t.ts >= $t0 AND t.ts < $t1 AND b.id <> a.id
WITH a, count(DISTINCT b) AS k, sum(t.amount) AS total, collect(t.tx_id) AS txs
WHERE k >= $min
RETURN a.id AS account, k, total, txs[0..60] AS tx_ids
"""

PASS_THROUGH = """
UNWIND $ids AS id
MATCH (s:Account)-[i:TRANSFER]->(a:Account {id: id})
WHERE i.ts >= $t0 AND i.ts < $t1 AND s.id <> a.id
WITH a, sum(i.amount) AS inflow, collect(i.tx_id) AS in_tx, min(i.ts) AS first_in
MATCH (a)-[o:TRANSFER]->(d:Account)
WHERE o.ts >= first_in AND o.ts < $t1 AND d.id <> a.id
WITH a, inflow, in_tx, sum(o.amount) AS outflow, collect(o.tx_id) AS out_tx
WHERE inflow > 0 AND outflow / inflow >= $ratio
RETURN a.id AS account, inflow, outflow, outflow / inflow AS ratio, (in_tx + out_tx)[0..60] AS tx_ids
"""

SCATTER_GATHER = """
UNWIND $ids AS id
MATCH (s:Account {id: id})-[t1:TRANSFER]->(m:Account)-[t2:TRANSFER]->(d:Account)
WHERE t1.ts >= $t0 AND t1.ts < $t1 AND t2.ts >= t1.ts AND t2.ts <= t1.ts + $gap
  AND d.id <> s.id AND m.id <> s.id AND m.id <> d.id
WITH s, d, count(DISTINCT m) AS mids, collect(t1.tx_id) + collect(t2.tx_id) AS txs
WHERE mids >= $min
RETURN s.id AS account, d.id AS sink, mids, txs[0..60] AS tx_ids
LIMIT 200
"""

CYCLE = """
UNWIND $ids AS id
MATCH p = (a:Account {id: id})-[r:TRANSFER*2..4]->(a)
WHERE ALL(x IN r WHERE x.ts >= $t0 AND x.ts < $t1)
  AND ALL(i IN range(0, size(r) - 2) WHERE r[i].ts <= r[i + 1].ts)
RETURN a.id AS account, size(r) AS hops, [x IN r | x.tx_id] AS tx_ids
LIMIT 100
"""


def run_detectors(accounts: list[str], t0: int, t1: int, rules: dict, cycle_accounts: list[str],
                  log: list[dict] | None = None) -> list[dict]:
    """Return hits: {detector, account, strength, detail, tx_ids}."""
    d = rules["detectors"]
    window_t0 = t0 - d["window_hours"] * HOUR_MS  # include the day before so windows are not cut at midnight
    log = log if log is not None else []
    hits: list[dict] = []

    def q(name, cypher, params):
        res = ro(cypher, params, timeout=30_000)
        log.append({"detector": name, "cypher": cypher.strip(), "ms": round(res.ms, 1), "rows": len(res.rows)})
        return res.rows

    ids = accounts
    for r in q("FAN-OUT", FAN_OUT, {"ids": ids, "t0": window_t0, "t1": t1, "min": d["fan_out_min"]}):
        hits.append({"detector": "FAN-OUT", "account": r["account"], "strength": min(1.0, r["k"] / (3 * d["fan_out_min"])),
                     "detail": f"paid {r['k']} distinct accounts (${r['total']:,.0f})", "tx_ids": list(r["tx_ids"])})
    for r in q("FAN-IN", FAN_IN, {"ids": ids, "t0": window_t0, "t1": t1, "min": d["fan_in_min"]}):
        hits.append({"detector": "FAN-IN", "account": r["account"], "strength": min(1.0, r["k"] / (3 * d["fan_in_min"])),
                     "detail": f"received from {r['k']} distinct accounts (${r['total']:,.0f})", "tx_ids": list(r["tx_ids"])})
    for r in q("PASS-THROUGH", PASS_THROUGH, {"ids": ids, "t0": window_t0, "t1": t1, "ratio": d["passthrough_ratio"]}):
        hits.append({"detector": "PASS-THROUGH", "account": r["account"], "strength": min(1.0, r["ratio"]) * 0.8,
                     "detail": f"paid out {r['ratio']:.0%} of ${r['inflow']:,.0f} received", "tx_ids": list(r["tx_ids"])})
    for r in q("SCATTER-GATHER", SCATTER_GATHER, {"ids": ids, "t0": window_t0, "t1": t1, "min": d["scatter_gather_min_mid"],
                                                  "gap": d["window_hours"] * HOUR_MS}):
        hits.append({"detector": "SCATTER-GATHER", "account": r["account"], "strength": min(1.0, r["mids"] / 6),
                     "detail": f"{r['mids']} intermediaries carry funds to {r['sink']}", "tx_ids": list(r["tx_ids"])})
    if cycle_accounts:
        for r in q("CYCLE", CYCLE, {"ids": cycle_accounts, "t0": window_t0, "t1": t1}):
            hits.append({"detector": "CYCLE", "account": r["account"], "strength": 0.9,
                         "detail": f"time-ordered loop of {r['hops']} transfers", "tx_ids": list(r["tx_ids"])})
    # Gather-scatter = the same account is both a fan-in and a fan-out.
    fin = {h["account"]: h for h in hits if h["detector"] == "FAN-IN"}
    for h in [h for h in hits if h["detector"] == "FAN-OUT" and h["account"] in fin]:
        hits.append({"detector": "GATHER-SCATTER", "account": h["account"],
                     "strength": max(h["strength"], fin[h["account"]]["strength"]),
                     "detail": "collects from many accounts and redistributes to many",
                     "tx_ids": (fin[h["account"]]["tx_ids"] + h["tx_ids"])[:60]})
    return hits
