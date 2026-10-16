"""In-memory stand-in for FalkorDB used by unit tests.

Each query constant from flowtrace.graph.queries is answered by a small Python function over an
edge list, so tool/policy/disposition logic can be tested without a running database. The real
Cypher is exercised against FalkorDB by `python -m flowtrace.graph.smoke`.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from flowtrace.detectors import alerts as A
from flowtrace.detectors.alerts import passthrough_pairs
from flowtrace.graph import queries as Q
from flowtrace.graph.client import QueryResult


@dataclass
class World:
    edges: list[dict]                  # tx_id, src, dst, ts, amount, fmt
    accounts: dict[str, dict]          # id -> {entity_type, entity_id}

    def out(self, a, t0=None, t1=None):
        return [e for e in self.edges if e["src"] == a and (t0 is None or t0 <= e["ts"] < t1)]

    def inc(self, a, t0=None, t1=None):
        return [e for e in self.edges if e["dst"] == a and (t0 is None or t0 <= e["ts"] < t1)]

    def deg(self, a):
        return len(self.out(a)), len(self.inc(a))

    def ro(self, cypher: str, params: dict, timeout: int = 0) -> QueryResult:
        rows = self.answer(cypher, params)
        return QueryResult(rows=rows, cypher=cypher, params=params, ms=0.1)

    def _tx(self, e):
        return {"tx_id": e["tx_id"], "src": e["src"], "dst": e["dst"], "ts": e["ts"], "amount": e["amount"],
                "fmt": e["fmt"], "cur": e.get("cur", "US Dollar"), "xcur": e.get("xcur", False)}

    def answer(self, c: str, p: dict) -> list[dict]:
        acc = self.accounts
        if c == Q.ACCOUNT_CARD:
            a = acc.get(p["id"], {})
            return [{"id": p["id"], "bank": "1", "entity_type": a.get("entity_type"), "self_transfers": 0,
                     "entity_id": a.get("entity_id"), "entity_name": None}]
        if c == Q.TRANSFERS_OUT:
            return [self._tx(e) for e in self.out(p["id"], p["t0"], p["t1"])]
        if c == Q.TRANSFERS_IN:
            return [self._tx(e) for e in self.inc(p["id"], p["t0"], p["t1"])]
        if c == Q.ACCOUNT_DAILY_HISTORY:
            by = defaultdict(lambda: [0, 0.0])
            for e in self.out(p["id"], p["h0"], p["t0"]):
                by[e["ts"] // 86_400_000][0] += 1
                by[e["ts"] // 86_400_000][1] += e["amount"]
            return [{"day": d, "n": v[0], "total": v[1]} for d, v in sorted(by.items())]
        if c in (Q.PAIR_HISTORY_OUT, Q.PAIR_HISTORY_IN):
            rows = []
            for cp in p["cps"]:
                es = [e for e in self.edges if e["ts"] < p["before"] and (
                    (e["src"], e["dst"]) == ((p["id"], cp) if c == Q.PAIR_HISTORY_OUT else (cp, p["id"])))]
                if es:
                    rows.append({"cp": cp, "prior_cnt": len(es), "first_ts": min(e["ts"] for e in es),
                                 "ts_list": [e["ts"] for e in es], "amounts": [e["amount"] for e in es]})
            return rows
        if c == Q.DEGREES_UNTIL:
            return [{"id": i, "out_deg": len([e for e in self.out(i) if e["ts"] < p["t1"]]),
                     "in_deg": len([e for e in self.inc(i) if e["ts"] < p["t1"]])} for i in p["ids"]]
        if c == Q.HUB_DEGREES:
            return [{"id": i, "out_deg": len(self.out(i, p["h0"], p["t0"])), "in_deg": len(self.inc(i, p["h0"], p["t0"]))}
                    for i in p["ids"]]
        if c in (Q.CP_INFO, Q.DEGREES):
            rows = []
            for i in p["ids"]:
                o, n = self.deg(i)
                a = acc.get(i, {})
                rows.append({"id": i, "entity_type": a.get("entity_type"), "entity_id": a.get("entity_id"),
                             "out_deg": o, "in_deg": n})
            return rows
        if c == Q.FLOW_SERIES:
            groups = defaultdict(list)
            for e in self.edges:
                if e["ts"] >= p["before"]:
                    continue
                if e["src"] == p["id"]:
                    groups[(e["dst"], "out", e["fmt"])].append(e)
                elif e["dst"] == p["id"]:
                    groups[(e["src"], "in", e["fmt"])].append(e)
            return [{"cp": k[0], "dir": k[1], "fmt": k[2], "ts_list": [e["ts"] for e in v],
                     "amounts": [e["amount"] for e in v], "n": len(v)} for k, v in groups.items()]
        if c in (Q.TRACE_FORWARD_HOP, Q.TRACE_BACKWARD_HOP):
            rows = []
            for f in p["frontier"]:
                for e in self.edges:
                    if c == Q.TRACE_FORWARD_HOP and e["src"] == f["id"] and f["ts"] <= e["ts"] <= f["ts"] + p["max_gap_ms"] \
                            and e["dst"] not in p["visited"]:
                        rows.append(self._tx(e))
                    if c == Q.TRACE_BACKWARD_HOP and e["dst"] == f["id"] and f["ts"] - p["max_gap_ms"] <= e["ts"] <= f["ts"] \
                            and e["src"] not in p["visited"]:
                        rows.append(self._tx(e))
            return sorted(rows, key=lambda r: -r["amount"])
        if c == Q.SHARED_DESTINATIONS:
            by = defaultdict(list)
            for s in p["ids"]:
                for e in self.out(s, p["t0"], p["t1"]):
                    if e["dst"] not in p["ids"]:
                        by[e["dst"]].append(e)
            return [{"dst": d, "senders": len({e["src"] for e in es}), "n": len(es),
                     "total": sum(e["amount"] for e in es), "tx_ids": [e["tx_id"] for e in es]}
                    for d, es in by.items() if len({e["src"] for e in es}) >= 2]
        if c == Q.SHARED_SOURCES:
            return []
        if c == Q.SIBLING_ACCOUNTS:
            eid = acc.get(p["id"], {}).get("entity_id")
            return [{"id": a, "bank": "1"} for a, v in acc.items() if eid and v.get("entity_id") == eid and a != p["id"]]
        if c == Q.HOLD_FLOWS:
            rows = []
            for f in p["since"]:
                outs = [e for e in self.out(f["id"], f["ts"], p["t1"])]
                ins = self.inc(f["id"], f["ts"], p["t1"])
                rows.append({"id": f["id"], "out_cnt": len(outs), "out_sum": sum(e["amount"] for e in outs),
                             "exit_sum": sum(e["amount"] for e in outs if e["fmt"] in p["exit_fmts"] or e.get("xcur")),
                             "in_cnt": len(ins), "in_sum": sum(e["amount"] for e in ins)})
            return rows
        if c in (Q.CYCLES, Q.PRIOR_CASES, Q.NEAREST_ESCALATED):
            return []
        if c in (A.FAN_OUT, A.FAN_IN):
            rows = []
            for a in p["ids"]:
                es = self.out(a, p["t0"], p["t1"]) if c == A.FAN_OUT else self.inc(a, p["t0"], p["t1"])
                k = len({e["dst"] if c == A.FAN_OUT else e["src"] for e in es} - {a})
                if k >= p["min"]:
                    rows.append({"account": a, "k": k, "total": sum(e["amount"] for e in es),
                                 "tx_ids": [e["tx_id"] for e in es][:60]})
            return rows
        if c == A.PASS_THROUGH:
            rule = {"max_gap_hours": p["gap"] / 3_600_000, "ratio_low": p["lo"], "ratio_high": p["hi"],
                    "min_amount_usd": p["min_amount"]}
            rows = []
            for a in p["ids"]:
                ins = [{**e, "cp": e["src"]} for e in self.inc(a, p["t0"], p["t1"]) if e["src"] != a]
                outs = [{**e, "cp": e["dst"]} for e in self.out(a, p["day0"], p["t1"]) if e["dst"] != a]
                pairs = passthrough_pairs(ins, outs, rule)
                if pairs:
                    i_tx = list(dict.fromkeys(i["tx_id"] for i, _ in pairs))
                    o_tx = list(dict.fromkeys(o["tx_id"] for _, o in pairs))
                    rows.append({"account": a, "pairs": len(pairs), "n_in": len(i_tx), "n_out": len(o_tx),
                                 "tx_ids": i_tx + o_tx})
            return rows
        if c in (A.SCATTER_GATHER, A.CYCLE):
            return []
        raise AssertionError(f"fake graph has no answer for query:\n{c[:120]}")
