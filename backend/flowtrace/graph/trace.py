"""Time-respecting money tracing, one Cypher query per hop.

Running hop by hop (instead of one variable-length query) keeps every query bounded, lets us
stop at hub accounts, and emits one event per wave so the UI can animate the traversal live.
`bidirectional_trace` expands forward from a source and backward from a sink and reports where
the two searches meet, which is the money path an analyst wants to see.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from flowtrace.graph import queries as Q
from flowtrace.graph.client import ro

HOUR_MS = 3_600_000

Emit = Callable[[dict], None]


@dataclass
class Trace:
    edges: dict[str, dict] = field(default_factory=dict)   # tx_id -> edge
    visited: dict[str, int] = field(default_factory=dict)  # account -> hop discovered
    hubs: set[str] = field(default_factory=set)
    queries: list[dict] = field(default_factory=list)


def _log(trace: Trace, res, label: str) -> None:
    trace.queries.append({"label": label, "cypher": res.cypher.strip(), "params": res.params,
                          "ms": round(res.ms, 1), "rows": len(res.rows)})


def _hubs(ids: list[str], hub_degree: int, trace: Trace) -> set[str]:
    if not ids:
        return set()
    res = ro(Q.DEGREES, {"ids": ids})
    _log(trace, res, "degree check")
    return {r["id"] for r in res.rows if r["in_deg"] + r["out_deg"] > hub_degree}


def expand(
    seeds: list[dict], direction: str, rules: dict, emit: Emit | None = None, trace: Trace | None = None,
) -> Trace:
    """seeds: [{"id": account, "ts": epoch_ms}] — money position and time it arrived/left.

    direction 'forward' follows money out of the seeds (ts = earliest it could move on);
    'backward' follows money into the seeds (ts = latest it could have arrived).
    """
    trace = trace or Trace()
    hop_q = Q.TRACE_FORWARD_HOP if direction == "forward" else Q.TRACE_BACKWARD_HOP
    gap = int(rules["max_gap_hours"] * HOUR_MS)
    frontier = seeds
    for s in seeds:
        trace.visited.setdefault(s["id"], 0)

    for hop in range(1, rules["max_hops"] + 1):
        if not frontier:
            break
        res = ro(hop_q, {"frontier": frontier, "max_gap_ms": gap, "visited": list(trace.visited)})
        _log(trace, res, f"{direction} hop {hop}")
        new_nodes: dict[str, int] = {}
        wave = []
        for r in res.rows:
            if r["tx_id"] in trace.edges:
                continue
            e = {**r, "hop": hop, "direction": direction}
            trace.edges[r["tx_id"]] = e
            wave.append(e)
            nxt = r["dst"] if direction == "forward" else r["src"]
            # Forward: money can leave nxt only after it arrived. Backward: it must have arrived earlier.
            t = r["ts"]
            if nxt not in trace.visited:
                new_nodes[nxt] = min(new_nodes.get(nxt, t), t) if direction == "forward" else max(new_nodes.get(nxt, t), t)
        hubs = _hubs(list(new_nodes), rules["hub_degree"], trace)
        trace.hubs |= hubs
        for n in new_nodes:
            trace.visited[n] = hop
        if emit:
            emit({"type": "trace_wave", "direction": direction, "hop": hop, "edges": wave,
                  "new_accounts": list(new_nodes), "hubs": sorted(hubs), "ms": round(res.ms, 1)})
        ranked = sorted((n for n in new_nodes if n not in hubs),
                        key=lambda n: -sum(e["amount"] for e in wave if n in (e["src"], e["dst"])))
        frontier = [{"id": n, "ts": new_nodes[n]} for n in ranked[: rules["frontier_cap"]]]
    return trace


def bidirectional_trace(source: dict, sink: dict, rules: dict, emit: Emit | None = None) -> dict:
    """Expand forward from source and backward from sink; return accounts where they meet."""
    fwd = expand([source], "forward", rules, emit)
    bwd = expand([sink], "backward", rules, emit)
    meet = sorted(set(fwd.visited) & set(bwd.visited))
    if emit:
        emit({"type": "trace_meet", "accounts": meet})
    return {"forward": fwd, "backward": bwd, "meeting_points": meet}
