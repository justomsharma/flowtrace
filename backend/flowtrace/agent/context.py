"""Per-case investigation state shared by the tools, the policy and the report."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from flowtrace.agent.evidence import EvidenceBook
from flowtrace.graph.client import QueryResult, ro

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


@dataclass
class CaseContext:
    case_id: str
    primary: str                      # account the alert is about
    accounts: list[str]               # accounts in the alerted subgraph
    tx_ids: list[str]                 # alerted transfers
    t0: int                           # alert window start (epoch ms)
    t1: int                           # alert window end
    rules: dict                       # config.yaml
    ml_score: float = 0.0
    detector_hits: list[dict] = field(default_factory=list)
    shape: dict | None = None
    alert_edges: list[dict] = field(default_factory=list)
    emit: Callable[[dict], None] = lambda e: None

    book: EvidenceBook = field(default_factory=EvidenceBook)
    step: int = 0
    queries: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)       # tool, args, summary
    nodes: dict[str, dict] = field(default_factory=dict)  # account -> {role, entity_type}
    edges: dict[str, dict] = field(default_factory=dict)  # tx_id -> edge

    @property
    def history_start(self) -> int:
        return self.t0 - self.rules["agent"].get("history_days", 8) * DAY_MS

    def known(self, account: str) -> bool:
        return account in self.nodes or account in self.accounts

    def q(self, label: str, cypher: str, params: dict) -> QueryResult:
        """Run a read-only query, log it, and stream it to the UI."""
        res = ro(cypher, params, timeout=self.rules["agent"]["query_timeout_ms"])
        entry = {"step": self.step, "label": label, "cypher": cypher.strip(), "params": _short(params),
                 "ms": round(res.ms, 1), "rows": len(res.rows)}
        self.queries.append(entry)
        self.emit({"type": "query", **entry})
        return res

    def add_edges(self, rows: list[dict], role: str = "context") -> None:
        new_e, new_n = [], []
        for r in rows:
            if not r.get("tx_id") or r["tx_id"] in self.edges:
                continue
            e = {k: r.get(k) for k in ("tx_id", "src", "dst", "ts", "amount", "fmt")}
            e["role"] = role
            self.edges[r["tx_id"]] = e
            new_e.append(e)
            for a in (r["src"], r["dst"]):
                if a not in self.nodes:
                    self.nodes[a] = {"id": a, "role": "subject" if a in self.accounts else "related"}
                    new_n.append(self.nodes[a])
        if new_e or new_n:
            self.emit({"type": "graph", "nodes": new_n, "edges": new_e})


def _short(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        if isinstance(v, list) and len(v) > 5:
            out[k] = v[:5] + [f"... +{len(v) - 5}"]
        else:
            out[k] = v
    return out
