"""Classify the shape of a money-movement subgraph into the AMLworld laundering typologies.

Input is the set of transfers retrieved from FalkorDB for a case. The rules mirror how the
typologies are defined (Altman et al. 2023, Fig. 3):
  FAN-OUT         one source pays many sinks
  FAN-IN          many sources pay one sink
  SCATTER-GATHER  one source -> several intermediaries -> one sink
  GATHER-SCATTER  many sources -> one hub -> many sinks
  CYCLE           money returns to where it started
  BIPARTITE       a set of sources pays a set of sinks, no intermediaries
  STACK           two bipartite layers stacked (sources -> middle layer -> sinks)
  RANDOM          a chain or irregular walk with no other shape
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

TYPOLOGIES = ["FAN-OUT", "FAN-IN", "SCATTER-GATHER", "GATHER-SCATTER", "CYCLE", "BIPARTITE", "STACK", "RANDOM"]


@dataclass
class ShapeResult:
    typology: str
    confidence: float
    why: str
    stats: dict


def _graph(edges: list[dict]) -> nx.DiGraph:
    g = nx.DiGraph()
    for e in edges:
        if e["src"] == e["dst"]:
            continue
        if g.has_edge(e["src"], e["dst"]):
            g[e["src"]][e["dst"]]["n"] += 1
        else:
            g.add_edge(e["src"], e["dst"], n=1)
    return g


def classify(edges: list[dict]) -> ShapeResult:
    g = _graph(edges)
    n = g.number_of_nodes()
    if n < 2:
        return ShapeResult("RANDOM", 0.2, "too few accounts to form a shape", {"accounts": n})

    src = [v for v in g if g.in_degree(v) == 0]
    snk = [v for v in g if g.out_degree(v) == 0]
    mid = [v for v in g if g.in_degree(v) > 0 and g.out_degree(v) > 0]
    stats = {"accounts": n, "transfers": len(edges), "sources": len(src), "sinks": len(snk),
             "intermediaries": len(mid)}

    if n <= 2 and len(edges) <= 2:
        stats["degenerate"] = True

    cycles = list(nx.simple_cycles(g, length_bound=12))
    longest = max((len(c) for c in cycles), default=0)
    stats["longest_cycle"] = longest
    if longest >= 3 and longest / n >= 0.6:
        return ShapeResult("CYCLE", round(0.6 + 0.4 * longest / n, 2),
                           f"funds return to the origin through a {longest}-account loop", stats)

    # A hub that both gathers and redistributes; it may also pay some senders back.
    hub = max(g, key=lambda v: g.in_degree(v) + g.out_degree(v))
    touching = sum(d["n"] for u, v, d in g.edges(data=True) if hub in (u, v))
    total = sum(d["n"] for _, _, d in g.edges(data=True))
    if g.in_degree(hub) >= 2 and g.out_degree(hub) >= 2 and touching / total >= 0.8:
        return ShapeResult("GATHER-SCATTER", 0.85,
                           f"{g.in_degree(hub)} accounts pay one hub that redistributes to {g.out_degree(hub)} accounts", stats)

    if not mid:
        if len(src) == 1 and len(snk) >= 2:
            return ShapeResult("FAN-OUT", 0.9, f"one account pays {len(snk)} receivers", stats)
        if len(snk) == 1 and len(src) >= 2:
            return ShapeResult("FAN-IN", 0.9, f"{len(src)} accounts pay one receiver", stats)
        if len(src) >= 2 and len(snk) >= 2:
            return ShapeResult("BIPARTITE", 0.8, f"{len(src)} senders pay {len(snk)} receivers directly", stats)

    if len(src) == 1 and len(snk) == 1 and len(mid) >= 2:
        s, t = src[0], snk[0]
        relay = sum(1 for m in mid if g.has_edge(s, m) and g.has_edge(m, t))
        if relay >= 2:
            return ShapeResult("SCATTER-GATHER", round(0.6 + 0.4 * relay / len(mid), 2),
                               f"one account splits funds over {relay} intermediaries that all pay one receiver", stats)

    hubs = [m for m in mid if g.in_degree(m) >= 2 and g.out_degree(m) >= 2]
    if len(hubs) == 1 and len(mid) <= 2:
        h = hubs[0]
        return ShapeResult("GATHER-SCATTER", 0.85,
                           f"{g.in_degree(h)} accounts pay one hub that redistributes to {g.out_degree(h)} accounts", stats)

    if len(src) >= 2 and len(snk) >= 2 and len(mid) >= 2 and nx.is_directed_acyclic_graph(g):
        depth = nx.dag_longest_path_length(g)
        if depth == 2:
            return ShapeResult("STACK", 0.75,
                               f"{len(src)} senders -> {len(mid)} middle accounts -> {len(snk)} receivers", stats)

    if longest == 2 and n == 2:
        return ShapeResult("CYCLE", 0.5, "two accounts send money back and forth", stats)
    if all(g.in_degree(v) <= 1 and g.out_degree(v) <= 1 for v in g):
        return ShapeResult("RANDOM", 0.6, f"funds hop along a chain of {n} accounts", stats)

    if len(src) == 1 and len(snk) >= 2:
        return ShapeResult("FAN-OUT", 0.55, f"one origin, funds spread to {len(snk)} end accounts", stats)
    if len(snk) == 1 and len(src) >= 2:
        return ShapeResult("FAN-IN", 0.55, f"{len(src)} origins converge on one account", stats)
    return ShapeResult("RANDOM", 0.4, "irregular movement with no dominant shape", stats)
