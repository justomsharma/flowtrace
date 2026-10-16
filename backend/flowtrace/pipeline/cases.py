"""Turn flagged payments into ranked cases an analyst can work through.

  flag     a payment opens a case if its calibrated model probability, or the measured precision of a
           detector that fired on it, reaches cases.flag_probability. Weaker detector hits only add
           evidence to cases that are already open.
  group    cases grow outward from the riskiest accounts over flagged payments. Hubs (history degree
           >= cases.hub_degree: merchants, payroll, exchanges) never link two cases; their payments stay
           in the case of the other account. Growth stops at cases.max_accounts.
  score    case_score = 1 - (1 - best model probability) * prod(1 - detector precision), over distinct
           detectors in the case: the chance at least one signal is right (assumes independence).
  tier     the top cases.daily_capacity cases are "today", the rest "lower" (nothing is hidden).
"""
from __future__ import annotations

from collections import deque

from flowtrace.detectors.typology import classify


def batch_of(case_id: str) -> str | None:
    """"B12AB-C007" -> "B12AB": the upload a case id belongs to (ids are "<batch>-C<rank>"); None otherwise."""
    head, sep, num = case_id.rpartition("-C")
    return head if sep and head and num.isdigit() else None


def combine(probs) -> float:
    q = 1.0
    for p in probs:
        q *= 1.0 - p
    return 1.0 - q


def effective_hits(hits: list[dict]) -> list[dict]:
    """GATHER-SCATTER is FAN-IN + FAN-OUT on one account; count it once, not three times."""
    gs = {h["account"] for h in hits if h["detector"] == "GATHER-SCATTER"}
    return [h for h in hits if not (h["detector"] in ("FAN-IN", "FAN-OUT") and h["account"] in gs)]


def flag(tx: list[dict], hits: list[dict], rules: dict) -> list[dict]:
    bar = rules["cases"]["flag_probability"]
    prec = rules["detectors"]["precision"]
    by_id = {t["tx_id"]: t for t in tx}
    strong = {tid for h in hits if prec.get(h["detector"], 0.0) >= bar for tid in h["tx_ids"] if tid in by_id}
    return [t for t in tx if t["p_cal"] >= bar or t["tx_id"] in strong]


def group(flagged: list[dict], risk: dict[str, float], hubs: set[str], max_accounts: int) -> list[dict]:
    def rank(a: str) -> tuple:
        return -risk.get(a, 0.0), a

    adj: dict[str, set[str]] = {}
    for t in flagged:
        a, b = t["src"], t["dst"]
        for x in (a, b):
            if x not in hubs:
                adj.setdefault(x, set())
        if a not in hubs and b not in hubs and a != b:
            adj[a].add(b)
            adj[b].add(a)
    owner: dict[str, int] = {}
    groups: list[dict] = []
    for seed in sorted(adj, key=rank):
        if seed in owner:
            continue
        g = {"members": {seed}, "tx": []}
        owner[seed] = len(groups)
        queue = deque([seed])
        while queue and len(g["members"]) < max_accounts:
            for n in sorted(adj[queue.popleft()], key=rank):
                if n not in owner and len(g["members"]) < max_accounts:
                    g["members"].add(n)
                    owner[n] = len(groups)
                    queue.append(n)
        groups.append(g)
    hub_owner: dict[str, int] = {}  # kept apart so a hub-to-hub case never captures a suspect's payment
    for t in flagged:
        i = owner.get(t["src"], owner.get(t["dst"]))
        if i is None:  # hub to hub: its own case, keyed by the sender
            if t["src"] not in hub_owner:
                hub_owner[t["src"]] = len(groups)
                groups.append({"members": {t["src"]}, "tx": []})
            i = hub_owner[t["src"]]
        groups[i]["tx"].append(t)
    return [g for g in groups if g["tx"]]


def build_cases(tx: list[dict], hits: list[dict], batch_id: str, rules: dict, degrees: dict[str, int]) -> list[dict]:
    c = rules["cases"]
    prec = rules["detectors"]["precision"]
    hits = effective_hits(hits)
    flagged = flag(tx, hits, rules)
    hubs = {a for a, d in degrees.items() if d >= c["hub_degree"]}
    hits_by_acc: dict[str, list[dict]] = {}
    for h in hits:
        hits_by_acc.setdefault(h["account"], []).append(h)
    best: dict[str, float] = {}
    for t in flagged:
        for a in (t["src"], t["dst"]):
            best[a] = max(best.get(a, 0.0), t["p_cal"])
    risk = {a: combine([p] + [prec.get(h["detector"], 0.0) for h in hits_by_acc.get(a, [])]) for a, p in best.items()}

    cases = []
    for g in group(flagged, risk, hubs, c["max_accounts"]):
        ctx = g["tx"]
        # The case is about its own members (the suspects); hubs and accounts across a size cut are only
        # counterparties, so they neither lead the case nor add their detectors to its score.
        members = sorted(g["members"], key=lambda a: (a in hubs, -risk.get(a, 0.0), a))
        others = sorted({a for t in ctx for a in (t["src"], t["dst"])} - g["members"], key=lambda a: (-risk.get(a, 0.0), a))
        accounts = members + others
        dhits = [{k: h[k] for k in ("detector", "account", "strength", "detail")}
                 for a in members if a not in hubs for h in hits_by_acc.get(a, [])]
        p_model = max(t["p_cal"] for t in ctx)
        det = {h["detector"]: prec.get(h["detector"], 0.0) for h in dhits}
        signals = sorted([{"signal": "model", "p": round(float(p_model), 4)}] +
                         [{"signal": d, "p": round(p, 4)} for d, p in det.items()], key=lambda s: -s["p"])
        edges = [{"tx_id": t["tx_id"], "src": t["src"], "dst": t["dst"], "ts": t["ts"], "amount": t["amount_usd"],
                  "fmt": t["payment_format"], "p": round(float(t["p"]), 4)} for t in ctx]
        shape = classify(edges)
        labels = [t["is_laundering"] for t in ctx if t["is_laundering"] is not None]
        cases.append({
            "case_id": "", "batch_id": batch_id, "primary": accounts[0], "accounts": accounts,
            "tx_ids": [t["tx_id"] for t in ctx], "edges": edges,
            "t0": min(t["ts"] for t in ctx), "t1": max(t["ts"] for t in ctx) + 1,
            "ml_score": round(float(max(t["p"] for t in ctx)), 4),
            "case_score": round(combine([p_model] + list(det.values())), 4), "signals": signals,
            "detector_hits": dhits,
            "shape": {"typology": shape.typology, "confidence": shape.confidence, "why": shape.why, "stats": shape.stats},
            "amount": round(sum(t["amount_usd"] for t in ctx), 2),
            # Ground truth is kept for evaluation only and never passed to the agent.
            "ground_truth": (int(max(labels)) if labels else None),
        })
    cases.sort(key=lambda x: (-x["case_score"], -x["amount"]))
    for i, x in enumerate(cases, 1):
        x["case_id"] = f"{batch_id}-C{i:03d}"
        x["tier"] = "today" if i <= c["daily_capacity"] else "lower"
    return cases
