"""Hold planner: which accounts to hold so the case money still in the trail cannot leave. No LLM.

  decision   at the end of the case window (t1): the first moment an end-of-day run can act.
  trail      time-respecting forward trace from the case accounts (graph.trace.expand) plus the alert
             payments, before t1. A cash/crypto or cross-currency payment, or a payment to a hub, is an
             exit: the money has left and nothing beyond it is plannable.
  at rest    r(v) = clip(trail money in - everything v sent after it arrived, 0, trail money in).
             Conservative: any payment out is assumed to carry the case money first.
  network    S -> v_in (r(v)), v_in -> v_out (one hold), u_out -> v_in for each trail payment, and
             v_out -> T when v has an exit or no onward trail payment (end of trace: the money can go anywhere).
             Money at rest is assumed to keep to the routes the case has used (network interdiction).
  holds      a minimum vertex cut S -> T: the fewest accounts that cut every route out. Each costs
             1 - 0.1 * r(v)/R, so among cuts of the same size the one where more money already sits wins.
             Bigger than actions.max_holds_per_case: greedy, the account that blocks the most money next.
             Holds that block less than actions.min_hold_usd are dropped. Hubs are never holdable.

Blocked money = money at rest at held accounts, or with no route to T that avoids them. Inner capacities
are infinite, so this equals R minus the max flow of the network without the holds (computed as reachability).
"""
from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache

import networkx as nx

from flowtrace.graph import queries as Q
from flowtrace.graph.client import ro
from flowtrace.graph.trace import expand
from flowtrace.settings import settings

DAY_MS = 86_400_000

# Case-level proposal for each disposition: the agent's recommendation a person confirms.
CASE_ACTION = {
    "ESCALATE": ("FILE_STR", "The evidence points to laundering: file a suspicious transaction report (STR) with FIU-IND."),
    "REVIEW": ("ESCALATE", "The evidence is mixed: send the case to a senior analyst."),
    "CLOSE": ("CLOSE", "Legitimate explanations outweigh the red flags: close the case as normal activity."),
}


@lru_cache
def fx_rates() -> dict[str, float]:
    """Units of each currency per US dollar, as derived from the data (data/processed/fx_rates.json)."""
    f = settings().processed_dir / "fx_rates.json"
    return json.loads(f.read_text()) if f.exists() else {"US Dollar": 1.0}


# --- the cut ------------------------------------------------------------------------------------

def _nodes(supply: dict, routes: dict) -> set[str]:
    return set(supply) | set(routes) | {v for vs in routes.values() for v in vs}


def _live(supply: dict[str, float], routes: dict[str, set[str]], exits: set[str], held: set[str]) -> set[str]:
    """Accounts that can still reach an exit without passing a held account."""
    back: dict[str, set[str]] = defaultdict(set)
    for u, vs in routes.items():
        for v in vs:
            back[v].add(u)
    stack = [n for n in _nodes(supply, routes) if (n in exits or not routes.get(n)) and n not in held]
    live: set[str] = set()
    while stack:
        n = stack.pop()
        if n in live:
            continue
        live.add(n)
        stack.extend(u for u in back[n] if u not in held and u not in live)
    return live


def _blocked(supply: dict[str, float], routes: dict[str, set[str]], exits: set[str], held: set[str]) -> float:
    """Money at rest that can no longer reach an exit without passing a held account."""
    live = _live(supply, routes, exits, held)
    return sum(r for n, r in supply.items() if n not in live)


def _min_vertex_cut(supply: dict[str, float], routes: dict[str, set[str]], exits: set[str]) -> list[str]:
    total = sum(supply.values())
    g = nx.DiGraph()
    for n in _nodes(supply, routes):
        g.add_edge((n, "in"), (n, "out"), capacity=1.0 - 0.1 * supply.get(n, 0.0) / total)
        if supply.get(n, 0.0) > 0:
            g.add_edge("S", (n, "in"))          # no capacity attribute = infinite
        if n in exits or not routes.get(n):
            g.add_edge((n, "out"), "T")
        for v in routes.get(n, ()):
            g.add_edge((n, "out"), (v, "in"))
    _, (reach, _) = nx.minimum_cut(g, "S", "T")
    return sorted(n for n in _nodes(supply, routes) if (n, "in") in reach and (n, "out") not in reach)


def _marginals(order: list[str], supply, routes, exits) -> list[tuple[str, float]]:
    out, done, held = [], 0.0, set()
    for n in order:
        held.add(n)
        b = _blocked(supply, routes, exits, held)
        out.append((n, round(b - done, 2)))
        done = b
    return out


def choose_holds(supply: dict[str, float], routes: dict[str, set[str]], exits: set[str], cap: int,
                 min_usd: float) -> tuple[list[tuple[str, float]], str, int]:
    """Holds in the order chosen with the money each one adds, the method used, and the min-cut size."""
    if sum(supply.values()) <= 0:
        return [], "none", 0
    # A loop with no exit seen (a round trip) still lets money leave from any of its accounts.
    exits = exits | (_nodes(supply, routes) - _live(supply, routes, exits, set()))
    cut = _min_vertex_cut(supply, routes, exits)
    pool, method = (cut, "min-cut") if len(cut) <= cap else (sorted(_nodes(supply, routes)), "greedy")
    order: list[str] = []
    done = 0.0
    while len(order) < cap:
        # max() keeps the first of equals: more money blocked, then more money already there, then name order.
        cands = [n for n in pool if n not in order]
        if not cands:
            break
        best = max(cands, key=lambda n: (_blocked(supply, routes, exits, set(order) | {n}), supply.get(n, 0.0)))
        b = _blocked(supply, routes, exits, set(order) | {best})
        if b - done <= 0:
            break
        order.append(best)
        done = b
    holds = _marginals(order, supply, routes, exits)
    while holds and min(g for _, g in holds) < min_usd:     # dropping one can change the others' share
        weakest = min(holds, key=lambda h: h[1])[0]
        holds = _marginals([n for n, _ in holds if n != weakest], supply, routes, exits)
    return holds, method, len(cut)


# --- the plan -----------------------------------------------------------------------------------

def _hubs(ids: list[str], t0: int, rules: dict) -> set[str]:
    """Every existing hub rule: case grouping's history degree, the tracer's degree, busy receivers."""
    if not ids:
        return set()
    c = rules["cases"]
    h0 = t0 - c["hub_lookback_days"] * DAY_MS
    hist = ro(Q.HUB_DEGREES, {"ids": ids, "h0": h0, "t0": t0}).rows
    hubs = {r["id"] for r in hist if r["in_deg"] + r["out_deg"] >= c["hub_degree"]}
    for r in ro(Q.DEGREES, {"ids": ids}).rows:
        if (r["in_deg"] + r["out_deg"] > rules["trace"]["hub_degree"]
                or r["in_deg"] >= rules["legitimacy"]["hub_receiver_in_degree"]):
            hubs.add(r["id"])
    return hubs


def _why(acc: str, frozen: float, routed_from: int, has_exit: bool, n_in: int) -> str:
    parts = []
    if frozen > 0:
        parts.append(f"Case money reached this account in {n_in} payment{'s' if n_in != 1 else ''} and has not left yet.")
    if routed_from:
        parts.append(f"It is the only way out we saw for money resting in {routed_from} other account"
                     f"{'s' if routed_from != 1 else ''}.")
    if has_exit:
        parts.append("It has already sent money out in cash, crypto or another currency.")
    return " ".join(parts) or "It sits on the money trail."


def plan_holds(case: dict, rules: dict, result: dict | None = None) -> dict:
    """The hold plan for one case (read-only). result = its finished investigation, if there is one."""
    act = rules["actions"]
    t0, t1 = case["t0"], case["t1"]
    exit_fmts = list(rules["escalators"]["high_risk_formats"])
    seeds = list(dict.fromkeys(case["accounts"]))

    trace = expand([{"id": a, "ts": t0} for a in seeds], "forward", rules["trace"])
    by_tx = {e["tx_id"]: e for e in case.get("edges", [])} | trace.edges
    edges = sorted((e for e in by_tx.values() if e["ts"] < t1), key=lambda e: e["ts"])
    hubs = _hubs(sorted({a for e in edges for a in (e["src"], e["dst"])} | set(seeds)), t0, rules)

    # Walk the plannable trail: stop at exits (hard-to-trace or cross-currency payments) and at hubs.
    out_edges: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        out_edges[e["src"]].append(e)
    trail = [s for s in seeds if s not in hubs]
    seen, routes, exits = set(trail), defaultdict(set), set()
    plan_edges, exit_edges, hub_edges = [], [], []
    stack = list(trail)
    while stack:
        u = stack.pop()
        for e in out_edges[u]:
            v = e["dst"]
            if e.get("fmt") in exit_fmts or e.get("xcur"):
                exits.add(u)
                exit_edges.append(e)
            elif v in hubs:
                exits.add(u)
                hub_edges.append(e)
            elif v != u:
                routes[u].add(v)
                plan_edges.append(e)
                if v not in seen:
                    seen.add(v)
                    trail.append(v)
                    stack.append(v)

    arrived = {s: t0 for s in seeds if s in seen}
    trail_in: dict[str, float] = defaultdict(float)
    for e in plan_edges:
        if e["dst"] not in seeds:
            arrived[e["dst"]] = min(arrived.get(e["dst"], e["ts"]), e["ts"])
        trail_in[e["dst"]] += e["amount"]
    flows = {r["id"]: r for r in ro(Q.HOLD_FLOWS, {
        "ids": trail, "since": [{"id": a, "ts": arrived[a]} for a in trail], "t1": t1, "exit_fmts": exit_fmts}).rows} if trail else {}
    supply = {}
    for a in trail:
        f = flows.get(a, {})
        got = f.get("in_sum", 0.0) if a in seeds else trail_in[a]
        supply[a] = round(min(max(got - f.get("out_sum", 0.0), 0.0), got), 2)
        if f.get("exit_sum", 0.0) > 0:
            exits.add(a)
    in_motion = round(sum(supply.values()), 2)
    exits |= _nodes(supply, dict(routes)) - _live(supply, dict(routes), exits, set())  # loops with no exit seen

    disposition = (result or {}).get("disposition", {}).get("action")
    gated = disposition is not None and disposition not in act["propose_for"]
    chosen, method, k = ([], "none", 0) if gated else choose_holds(
        supply, dict(routes), exits, act["max_holds_per_case"], act["min_hold_usd"])
    held = {a for a, _ in chosen}
    # Accounts whose money is blocked only because every route out of them passes a hold.
    via_route = {u for u, r in supply.items() if r > 0 and u not in held and _blocked({u: r}, dict(routes), exits, held) > 0}

    evidence = (result or {}).get("evidence", [])
    holds = []
    for a, amount in chosen:
        frozen = round(min(supply[a], amount), 2)
        into = sorted((e for e in plan_edges if e["dst"] == a), key=lambda e: -e["amount"])
        upstream = sum(1 for u in via_route if _reaches(u, a, routes))
        has_exit = any(x["src"] == a for x in exit_edges) or flows.get(a, {}).get("exit_sum", 0.0) > 0
        holds.append({
            "account": a, "amount_blocked": amount, "frozen_now": frozen,
            "why": _why(a, frozen, upstream, has_exit, len(into) or 1),
            "evidence_ids": [x["id"] for x in evidence if x.get("kind") == "support" and a in x.get("accounts", [])],
            "tx_ids": [e["tx_id"] for e in into[:10]],
            "expiry_days": act["hold_expiry_business_days"],
        })

    curs = {e.get("cur") for e in plan_edges if e.get("cur")}
    currency = curs.pop() if len(curs) == 1 else "US Dollar"
    rate = fx_rates().get(currency, 1.0) if currency != "US Dollar" else 1.0
    ca = CASE_ACTION.get(disposition)
    blocked = round(sum(h["amount_blocked"] for h in holds), 2)
    note = ("Not investigated yet: these holds are not proposed until the investigation recommends them."
            if disposition is None else
            f"No holds: the investigation recommends {disposition.lower()}." if gated else "")
    return {
        "case_id": case["case_id"], "decided_at": t1, "disposition": disposition,
        "in_motion": in_motion,
        "already_out": round(sum(e["amount"] for e in exit_edges + hub_edges), 2),
        "blocked": blocked, "frozen_now": round(sum(h["frozen_now"] for h in holds), 2),
        "method": method, "min_cut_size": k, "holds": holds,
        "case_action": {"type": ca[0], "why": ca[1]} if ca else None,
        "skipped_hubs": sorted(hubs & ({e["dst"] for e in hub_edges} | set(seeds))),
        "currency": currency, "rate": rate, "note": note,
        "network": {
            "nodes": [{"id": a, "at_rest": supply[a], "held": a in held, "exit": a in exits, "hub": False} for a in trail]
                     + [{"id": h, "at_rest": 0.0, "held": False, "exit": True, "hub": True} for h in sorted({e["dst"] for e in hub_edges})],
            "edges": [{"tx_id": e["tx_id"], "src": e["src"], "dst": e["dst"], "amount": e["amount"], "ts": e["ts"], "kind": kind}
                      for kind, es in (("trail", plan_edges), ("exit", exit_edges), ("hub", hub_edges)) for e in es],
        },
    }


def _reaches(u: str, target: str, routes: dict[str, set[str]]) -> bool:
    stack, seen = [u], set()
    while stack:
        n = stack.pop()
        if n == target:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(routes.get(n, ()))
    return False
