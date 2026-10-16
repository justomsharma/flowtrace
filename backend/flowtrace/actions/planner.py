"""Hold planner: which accounts to hold so the case money still sitting in them cannot leave. No LLM.

  decision   at the end of the case window (t1): the first moment an end-of-day run can act.
  trail      time-respecting forward trace from the case accounts (graph.trace.expand) plus the alert
             payments, before t1. A cash/crypto or cross-currency payment, or a payment to a hub, is an
             exit: that money has left and nothing beyond it is plannable.
  hold       a lien on an amount, not a freeze of the account (RBI draft KYC Amendment Directions, Sep 2026;
             Indian courts limit a lien to the disputed amount):
                 hold(v) = min(money at rest in v since the case money arrived, case money traced into v)
             Case money traced into v = the case's payments into v (the alert payments and the trail).
             So a busy company that is a case account holds only the flagged money, not its day's receipts.
  pick       the accounts that freeze the most money now, at most actions.max_holds_per_case, none below
             actions.min_hold_usd, never a hub. Equal amounts prefer the account that also sits on more routes.
  headline   frozen_now = the money the holds freeze today.
  on routes  an assumption, reported apart: money resting upstream whose every route seen so far passes a held
             account. It only counts if the mules do not pay someone new.
  route cut  the minimum vertex cut (networkx) of the trail network, kept as context: how many account-wide
             freezes would cut every route. A lien on an account with nothing in it freezes nothing, so the
             cut does not choose the holds.
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


def _attribute(held: set[str], supply, routes, exits) -> dict[str, float]:
    """Money each hold blocks: what sits in it, plus an equal share of the money resting upstream whose every
    route out reaches the holds (split among the holds that money can reach). Adds up to the blocked total."""
    amount = {h: supply.get(h, 0.0) for h in held}
    live = _live(supply, routes, exits, held)
    for u, r in supply.items():
        if r <= 0 or u in held or u in live:
            continue
        reached, stack, seen = set(), [u], {u}
        while stack:
            for v in routes.get(stack.pop(), ()):
                if v in seen:
                    continue
                seen.add(v)
                if v in held:
                    reached.add(v)
                else:
                    stack.append(v)
        for h in reached:
            amount[h] += r / len(reached)
    return amount


def choose_holds(supply: dict[str, float], routes: dict[str, set[str]], exits: set[str], cap: int,
                 min_usd: float) -> tuple[list[dict], int]:
    """supply = what a hold on each account would freeze. Returns the holds, largest first, as
    {account, hold, on_routes}, and the size of the minimum vertex cut of the routes (context only)."""
    if sum(supply.values()) <= 0:
        return [], 0
    # A loop with no exit seen (a round trip) still lets money leave from any of its accounts.
    exits = exits | (_nodes(supply, routes) - _live(supply, routes, exits, set()))
    cut = _min_vertex_cut(supply, routes, exits)
    chosen: list[str] = []
    cands = sorted(a for a, v in supply.items() if v >= min_usd)
    while cands and len(chosen) < cap:
        # max() keeps the first of equals: more money frozen, then more money covered on the routes, then name.
        best = max(cands, key=lambda a: (supply[a], _blocked(supply, routes, exits, set(chosen) | {a})))
        chosen.append(best)
        cands.remove(best)
    covered = _attribute(set(chosen), supply, routes, exits)
    holds = [{"account": a, "hold": round(supply[a], 2), "on_routes": round(covered[a] - supply[a], 2)} for a in chosen]
    return sorted(holds, key=lambda h: (-h["hold"], h["account"])), len(cut)


def _network(plan: dict) -> tuple[dict[str, float], dict[str, set[str]], set[str]]:
    """What a hold on each account would freeze, the routes and the exits of a saved plan (no graph queries)."""
    nodes = {n["id"]: n for n in plan["network"]["nodes"] if not n["hub"]}
    routes: dict[str, set[str]] = defaultdict(set)
    for e in plan["network"]["edges"]:
        if e["kind"] == "trail" and e["src"] in nodes and e["dst"] in nodes:
            routes[e["src"]].add(e["dst"])
    return {a: n["at_rest"] for a, n in nodes.items()}, dict(routes), {a for a, n in nodes.items() if n["exit"]}


def effect(plan: dict, held: list[str]) -> dict:
    """What these held accounts freeze now, and the money on the case's routes they would also stop (an
    assumption), from a saved plan. Used once a person has decided some holds, so the open ones are recounted."""
    supply, routes, exits = _network(plan)
    exits |= _nodes(supply, routes) - _live(supply, routes, exits, set())
    held_set = set(held) & set(supply)
    frozen = sum(supply[a] for a in held_set)
    return {"frozen": round(frozen, 2), "on_routes": round(_blocked(supply, routes, exits, held_set) - frozen, 2)}


# --- the plan -----------------------------------------------------------------------------------

def _hubs(ids: list[str], t0: int, t1: int, rules: dict) -> set[str]:
    """Every existing hub rule: case grouping's history degree, the tracer's degree, busy receivers. Only
    payments before the decision time (t1) count."""
    if not ids:
        return set()
    c = rules["cases"]
    h0 = t0 - c["hub_lookback_days"] * DAY_MS
    hist = ro(Q.HUB_DEGREES, {"ids": ids, "h0": h0, "t0": t0}).rows
    hubs = {r["id"] for r in hist if r["in_deg"] + r["out_deg"] >= c["hub_degree"]}
    for r in ro(Q.DEGREES_UNTIL, {"ids": ids, "t1": t1}).rows:
        if (r["in_deg"] + r["out_deg"] > rules["trace"]["hub_degree"]
                or r["in_deg"] >= rules["legitimacy"]["hub_receiver_in_degree"]):
            hubs.add(r["id"])
    return hubs


def _why(hold: float, routed_from: int, has_exit: bool, n_in: int) -> str:
    parts = [f"Case money reached this account in {n_in} payment{'s' if n_in != 1 else ''} and has not left yet."]
    if routed_from:
        parts.append(f"If the mules keep to the routes seen so far, money resting in {routed_from} other account"
                     f"{'s' if routed_from != 1 else ''} would also have to pass through it.")
    if has_exit:
        parts.append("It has already sent money out in cash, crypto or another currency.")
    return " ".join(parts)


def plan_holds(case: dict, rules: dict, result: dict | None = None) -> dict:
    """The hold plan for one case (read-only). result = its finished investigation, if there is one."""
    act = rules["actions"]
    t0, t1 = case["t0"], case["t1"]
    exit_fmts = list(rules["escalators"]["high_risk_formats"])
    seeds = list(dict.fromkeys(case["accounts"]))

    trace = expand([{"id": a, "ts": t0} for a in seeds], "forward", rules["trace"])
    alert = {e["tx_id"]: e for e in case.get("edges", []) if e["ts"] < t1}
    by_tx = alert | {k: e for k, e in trace.edges.items() if e["ts"] < t1}
    edges = sorted(by_tx.values(), key=lambda e: e["ts"])
    hubs = _hubs(sorted({a for e in edges for a in (e["src"], e["dst"])} | set(seeds)), t0, t1, rules)

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

    # Case money traced into each account: the trail's payments and the case's own (alert) payments into it.
    into: dict[str, dict[str, dict]] = defaultdict(dict)
    for e in plan_edges + [e for e in alert.values() if e["dst"] in seen]:
        into[e["dst"]][e["tx_id"]] = e
    traced = {a: sum(e["amount"] for e in into[a].values()) for a in trail}
    arrived = {a: min((e["ts"] for e in into[a].values()), default=t0) for a in trail}
    flows = {r["id"]: r for r in ro(Q.HOLD_FLOWS, {
        "ids": trail, "since": [{"id": a, "ts": arrived[a]} for a in trail], "t1": t1, "exit_fmts": exit_fmts}).rows} if trail else {}
    supply = {}
    for a in trail:
        f = flows.get(a, {})
        at_rest = max(f.get("in_sum", 0.0) - f.get("out_sum", 0.0), 0.0)
        supply[a] = round(min(at_rest, traced[a]), 2)   # a lien on the case money, never the whole balance
        if f.get("exit_sum", 0.0) > 0:
            exits.add(a)
    exits |= _nodes(supply, dict(routes)) - _live(supply, dict(routes), exits, set())  # loops with no exit seen

    disposition = (result or {}).get("disposition", {}).get("action")
    gated = disposition is not None and disposition not in act["propose_for"]
    chosen, cut = choose_holds(supply, dict(routes), exits, act["max_holds_per_case"], act["min_hold_usd"])
    if gated:
        chosen = []
    held = {h["account"] for h in chosen}
    # Accounts whose money would be stopped only because every route seen out of them passes a hold.
    via_route = {u for u, r in supply.items() if r > 0 and u not in held and _blocked({u: r}, dict(routes), exits, held) > 0}

    evidence = (result or {}).get("evidence", [])
    holds = []
    for h in chosen:
        a = h["account"]
        pays = sorted(into[a].values(), key=lambda e: -e["amount"])
        has_exit = any(x["src"] == a for x in exit_edges) or flows.get(a, {}).get("exit_sum", 0.0) > 0
        holds.append({
            **h, "why": _why(h["hold"], sum(1 for u in via_route if _reaches(u, a, routes)), has_exit, len(pays) or 1),
            "evidence_ids": [x["id"] for x in evidence if x.get("kind") == "support" and a in x.get("accounts", [])],
            "tx_ids": [e["tx_id"] for e in pays[:10]],
            "expiry_days": act["hold_expiry_business_days"],
        })

    curs = {e.get("cur") for e in plan_edges if e.get("cur")}
    currency = curs.pop() if len(curs) == 1 else "US Dollar"
    rate = fx_rates().get(currency, 1.0) if currency != "US Dollar" else 1.0
    ca = CASE_ACTION.get(disposition)
    note = ("Not investigated yet: no hold is proposed until the investigation recommends escalation."
            if disposition is None else
            f"No holds: the investigation recommends {disposition.lower()}, and holds follow only an escalation." if gated else "")
    return {
        "case_id": case["case_id"], "decided_at": t1, "disposition": disposition,
        "in_motion": round(sum(supply.values()), 2),
        "frozen_now": round(sum(h["hold"] for h in holds), 2),
        "on_routes": round(sum(h["on_routes"] for h in holds), 2),
        "already_out": round(sum(e["amount"] for e in exit_edges + hub_edges), 2),
        "route_cut_size": cut, "holds": holds,
        "candidates": sorted(a for a, v in supply.items() if v >= act["min_hold_usd"]),
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
