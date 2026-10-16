"""The case overview page and the STR draft, built only from what is stored: the case (its flagged payments),
the investigation result, the hold plan and the case's actions. Read-only: no graph query, no write.

  flow      the case's payments, the money the investigation followed (trace/cycle; never the history it read
            for context), and the hold plan's trail payments into accounts proposed for a hold or held.
            Role from that flow: no money in = Sender, no money out = Destination, otherwise Intermediary.
  status    Flagged = a case payment, or one a red flag cites. In trail = money the investigation followed
            (traces, loops) or the plan's trail into a hold. Normal = the rest.
"""
from __future__ import annotations

from collections import defaultdict

HOUR = 3_600_000
DAY = 24 * HOUR
FLOW_ROLES = ("alert", "trace", "cycle")  # result graph edges that are case money; "context" is history
OPEN_HOLD = {"PROPOSED": "proposed", "APPROVED": "approved"}


def confidence(weight: float, rules: dict) -> str:
    t = rules["ui"]["evidence_confidence"]
    return "High" if weight >= t["high"] else "Medium" if weight >= t["medium"] else "Low"


def priority(score: float, rules: dict) -> str:
    t = rules["ui"]["priority"]
    return "High" if score >= t["high"] else "Medium" if score >= t["medium"] else "Low"


def case_currency(case: dict, plan: dict | None) -> str:
    """The case payments' own currency when they share one; mixed currencies are shown in US dollars, and
    cases stored before payments carried a currency use the hold plan's."""
    curs = {e.get("cur") for e in case.get("edges", []) if e.get("cur")}
    if curs:
        return curs.pop() if len(curs) == 1 else "US Dollar"
    return (plan or {}).get("currency") or "US Dollar"


def fx_rate(currency: str) -> float:
    """Units of `currency` per US dollar, from the rates derived from the data."""
    from flowtrace.actions.planner import fx_rates
    return 1.0 if currency == "US Dollar" else fx_rates().get(currency, 1.0)


def case_rate(currency: str, plan: dict | None) -> float:
    """The plan's rate when the plan is in the same currency, else the data's rate."""
    if currency == "US Dollar":
        return 1.0
    if (plan or {}).get("currency") == currency and plan.get("rate"):
        return plan["rate"]
    return fx_rate(currency)


def _holds(actions: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in actions:
        if a.get("type") == "HOLD" and a.get("account") and a.get("status") in OPEN_HOLD:
            if out.get(a["account"]) != "approved":
                out[a["account"]] = OPEN_HOLD[a["status"]]
    return out


def flow(case: dict, result: dict | None, plan: dict | None, actions: list[dict], rules: dict) -> dict:
    holds = _holds(actions)
    case_tx = {e["tx_id"] for e in case.get("edges", [])}
    cited = {t for e in (result or {}).get("evidence", []) if e.get("kind") == "support" for t in e.get("tx_ids", [])}

    pays: dict[str, dict] = {}
    def add(e: dict, origin: str) -> None:
        if e["tx_id"] not in pays:
            pays[e["tx_id"]] = {k: e.get(k) for k in ("tx_id", "ts", "src", "dst", "amount", "fmt")} | {"origin": origin}

    for e in case.get("edges", []):
        add(e, "case")
    for e in (result or {}).get("graph", {}).get("edges", []):
        if e.get("role") in FLOW_ROLES:
            add(e, "case" if e["tx_id"] in case_tx else "trail")  # money the investigation followed
    hold_tx = set()
    for e in (plan or {}).get("network", {}).get("edges", []):
        if e.get("kind") == "trail" and e["dst"] in holds:
            hold_tx.add(e["tx_id"])
            add(e, "trail")

    for p in pays.values():
        origin = p.pop("origin")
        p["status"] = ("flagged" if p["tx_id"] in case_tx or p["tx_id"] in cited
                       else "trail" if origin == "trail" else "normal")

    # Cap the accounts: case accounts and hold accounts always stay, then the largest payments.
    cap = rules["ui"]["flow_max_accounts"]
    everyone = {a for p in pays.values() for a in (p["src"], p["dst"])}
    keep = {a for a in case.get("accounts", []) if a in everyone} | (set(holds) & everyone)
    for p in sorted(pays.values(), key=lambda p: -p["amount"]):
        new = {p["src"], p["dst"]} - keep
        if len(keep) + len(new) <= cap:
            keep |= new
    shown = [p for p in pays.values() if p["src"] in keep and p["dst"] in keep]

    sent: dict[str, float] = defaultdict(float)
    received: dict[str, float] = defaultdict(float)
    pairs: dict[tuple[str, str], dict] = {}
    rank = {"flagged": 0, "trail": 1, "normal": 2}
    for p in shown:
        if p["src"] == p["dst"]:
            continue
        sent[p["src"]] += p["amount"]
        received[p["dst"]] += p["amount"]
        e = pairs.setdefault((p["src"], p["dst"]), {"src": p["src"], "dst": p["dst"], "amount": 0.0, "count": 0,
                                                    "status": "normal", "hold": False, "first_ts": p["ts"], "last_ts": p["ts"]})
        e["amount"] += p["amount"]
        e["count"] += 1
        e["status"] = min(e["status"], p["status"], key=rank.get)
        e["hold"] |= p["tx_id"] in hold_tx
        e["first_ts"], e["last_ts"] = min(e["first_ts"], p["ts"]), max(e["last_ts"], p["ts"])

    types = {n["id"]: n.get("entity_type") for n in (result or {}).get("graph", {}).get("nodes", [])}
    order = [a for a in case.get("accounts", []) if a in keep] + sorted(keep - set(case.get("accounts", [])))
    nodes = []
    for a in order:
        if a not in sent and a not in received:
            continue
        role = "Sender" if a not in received else "Destination" if a not in sent else "Intermediary"
        nodes.append({"id": a, "role": role, "amount": sent[a] if role == "Sender" else received[a],
                      "sent": sent.get(a, 0.0), "received": received.get(a, 0.0), "in_case": a in case.get("accounts", []),
                      "hold": holds.get(a), "entity_type": types.get(a)})
    return {"nodes": nodes, "edges": list(pairs.values()),
            "payments": sorted(shown, key=lambda p: (-p["ts"], p["tx_id"])),
            "hidden_accounts": len(everyone - keep)}


def timeline(payments: list[dict]) -> dict:
    """Payment amounts per UTC hour (flows up to 48 hours) or per UTC day, flagged apart from the rest.
    Empty buckets are kept so the bars sit on a real time axis."""
    if not payments:
        return {"bucket": "hour", "bins": []}
    lo, hi = min(p["ts"] for p in payments), max(p["ts"] for p in payments)
    size = HOUR if hi - lo <= 48 * HOUR else DAY
    bins = {t: {"t": t, "flagged": 0, "other": 0, "n": 0} for t in range(lo // size * size, hi // size * size + 1, size)}
    for p in payments:
        b = bins[p["ts"] // size * size]
        b["flagged" if p["status"] == "flagged" else "other"] += p["amount"]
        b["n"] += 1
    return {"bucket": "hour" if size == HOUR else "day", "bins": list(bins.values())}


def distribution(payments: list[dict], top: int = 6) -> list[dict]:
    """Where the money went: each payment's amount counted once, at the account that received it."""
    by: dict[str, float] = defaultdict(float)
    for p in payments:
        if p["src"] != p["dst"]:
            by[p["dst"]] += p["amount"]
    total = sum(by.values())
    rows = sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))
    out = [{"account": a, "amount": v, "share": v / total} for a, v in rows[:top]]
    if len(rows) > top:
        rest = sum(v for _, v in rows[top:])
        out.append({"account": None, "amount": rest, "share": rest / total, "accounts": len(rows) - top})
    return out


def _status(result: dict | None, job: dict | None, failed: bool) -> dict:
    if job:
        state = "investigating" if job.get("status") == "running" else "queued"
    elif result:
        state = result["disposition"]["action"].lower()
    elif failed:
        state = "failed"
    else:
        state = "not_started"
    d = (result or {}).get("disposition") or {}
    return {"state": state, "job": job, "risk": d.get("risk"), "confidence": d.get("confidence_label"),
            "reasons": d.get("reasons", [])}


INDICATORS = (("model", "ml_raw", "model"), ("red_flags", "red_raw", "red_flags"),
              ("shape", "shape_raw", "shape"), ("legitimacy", "legit_raw", "legitimacy"))


def build_overview(case: dict, result: dict | None, plan: dict | None, actions: list[dict], job: dict | None,
                   failed: bool, rules: dict, uploaded_at: float | None = None) -> dict:
    currency = case_currency(case, plan)
    rate = case_rate(currency, plan)
    f = flow(case, result, plan, actions, rules)
    evidence = (result or {}).get("evidence", [])

    def item(e: dict) -> dict:
        return {"id": e["id"], "code": e["code"], "claim": e["claim"], "weight": e["weight"],
                "confidence": confidence(e["weight"], rules), "escalator": e.get("escalator", False),
                "accounts": e.get("accounts", [])[:6], "tx_ids": e.get("tx_ids", [])[:20]}

    by_weight = lambda kind: sorted((e for e in evidence if e["kind"] == kind), key=lambda e: -e["weight"])
    comp = ((result or {}).get("disposition") or {}).get("components") or {}
    rep = (result or {}).get("report")
    connected = len(f["nodes"]) + f["hidden_accounts"]
    return {
        "case_id": case["case_id"], "batch_id": case.get("batch_id"), "primary": case.get("primary"),
        "typology": (case.get("shape") or {}).get("typology"), "tier": case.get("tier"),
        "case_score": case.get("case_score"), "ml_score": case.get("ml_score"),
        "priority": priority(case.get("case_score") or case.get("ml_score") or 0.0, rules),
        "window": [case.get("t0"), case.get("t1")], "uploaded_at": uploaded_at,
        "currency": currency, "rate": rate,
        "status": _status(result, job, failed),
        "kpis": {"amount": case.get("amount", 0.0), "accounts": len(case.get("accounts", [])),
                 "payments": len(case.get("edges", [])), "connected_accounts": connected,
                 "traced_accounts": max(connected - len(case.get("accounts", [])), 0),
                 "suspicious_patterns": sum(e["kind"] == "support" for e in evidence) if result else None},
        "flow": {k: f[k] for k in ("nodes", "edges", "hidden_accounts")},
        "transactions": f["payments"],
        "timeline": timeline(f["payments"]),
        "distribution": distribution(f["payments"]),
        "findings": [item(e) for e in by_weight("support")],
        "explanations": [item(e) for e in by_weight("counter")],
        "indicators": [{"key": k, "value": comp[raw], "contribution": comp.get(part, 0.0)}
                       for k, raw, part in INDICATORS if raw in comp],
        "summary": {"headline": rep["headline"], "text": rep["executive_summary"]["text"],
                    "by_ai": rep.get("author") not in (None, "template")} if rep else None,
        "next_steps": (rep or {}).get("next_steps", []),
        "steps": (result or {}).get("steps", []),
    }


# --- STR draft (Markdown) ----------------------------------------------------------------------

CODES = {"US Dollar": "USD", "Euro": "EUR", "UK Pound": "GBP", "Yuan": "CNY", "Yen": "JPY", "Rupee": "INR",
         "Ruble": "RUB", "Canadian Dollar": "CAD", "Australian Dollar": "AUD", "Swiss Franc": "CHF",
         "Mexican Peso": "MXN", "Shekel": "ILS", "Brazil Real": "BRL", "Saudi Riyal": "SAR", "Bitcoin": "BTC"}
ACTION_TITLE = {"HOLD": "Hold account", "RELEASE": "Release the hold on account", "ESCALATE": "Send to a senior analyst",
                "FILE_STR": "File an STR", "CLOSE": "Close as normal activity"}


def money(usd: float, currency: str | None = "US Dollar", rate: float | None = 1.0) -> str:
    """Amount in the data's own currency as an ISO code. Stored amounts are US dollars; rate = units per dollar."""
    code = CODES.get(currency or "US Dollar")
    if not code:
        return f"USD {usd:,.0f}"
    v = usd * (rate or 1.0)
    return f"{code} {v:,.4f}" if code == "BTC" else f"{code} {v:,.0f}"


def _utc(ms: int | None) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ms else ""


def _decision(a: dict) -> str:
    by = f" by {a['decided_by']}" if a.get("decided_by") else ""
    why = f" ({a['reason']})" if a.get("reason") else ""
    return {"PROPOSED": "waiting for a decision", "APPROVED": f"approved{by}{why}",
            "REJECTED": f"rejected{by}{why}"}.get(a.get("status")) or ("released" if a.get("released_at") else "withdrawn")


def str_markdown(case: dict, result: dict, plan: dict | None, actions: list[dict]) -> str:
    """The STR draft a person reviews before filing: the report, the evidence, the agent's proposed actions
    with what people decided, and the case payments."""
    rep, d = result["report"], result["disposition"]
    cur = case_currency(case, plan)
    rate = case_rate(cur, plan)
    m = lambda usd: money(usd, cur, rate)
    author = rep.get("author")
    by = ("written by the rule-based fallback (no AI model)" if author in (None, "template")
          else f"written by AI ({author})")
    evidence = result.get("evidence", [])
    out = [f"# STR draft: {case['case_id']}", "",
           f"Draft for a person to review before filing. The narrative was {by}; every figure comes from the "
           f"payment graph. Amounts in {cur}.", "",
           "## Summary", "", f"**{rep['headline']}**", "", rep["executive_summary"]["text"], "",
           f"Recommendation: **{d['action']}** (risk {d['risk']:.2f}, confidence {d.get('confidence_label', '')}). "
           f"Case amount {m(case.get('amount', 0.0))} across {len(case.get('accounts', []))} accounts and "
           f"{len(case.get('edges', []))} payments, {_utc(case.get('t0'))} to {_utc(case.get('t1'))}.", ""]
    if rep.get("subject_profile"):
        out += ["## Subject", "", rep["subject_profile"]["text"], ""]
    if rep.get("money_trail"):
        out += ["## Money trail", ""] + [f"- {t['text']}" for t in rep["money_trail"]] + [""]
    if rep.get("typology_assessment"):
        out += ["## Typology", "", rep["typology_assessment"]["text"], ""]
        if hyp := result.get("crime_hypotheses"):
            out += ["Possible predicate crime (a hypothesis from the shape, not a finding): "
                    + "; ".join(f"{h['crime']} ({h['basis']})" for h in hyp) + ".", ""]
    for title, kind in (("Red flags", "support"), ("Legitimate explanations", "counter")):
        items = sorted((e for e in evidence if e["kind"] == kind), key=lambda e: -e["weight"])
        out += [f"## {title}", ""]
        out += [f"- {e['claim']} ({e['id']}, weight {e['weight']:.2f})" for e in items] or ["- None found."]
        out += [""]
    if rep.get("unknowns"):
        out += ["## Unknowns", ""] + [f"- {u}" for u in rep["unknowns"]] + [""]
    out += ["## Proposed actions", "", "The agent only proposes; a person approves or rejects each one with a reason.", ""]
    for a in actions:
        title = ACTION_TITLE.get(a["type"], a["type"])
        if a["type"] in ("HOLD", "RELEASE") and a.get("account"):
            amount = money(a.get("hold_amount") or 0.0, a.get("currency"), a.get("rate"))
            out.append(f"- {title} {a['account']}: {amount} ({_decision(a)})")
        else:
            out.append(f"- {title}: {_decision(a)}")
    if not actions:
        out.append("- No action proposed.")
    out += [""]
    if rep.get("next_steps"):
        out += ["## Next steps", ""] + [f"- {s}" for s in rep["next_steps"]] + [""]
    out += ["## STR narrative", "", rep.get("str_narrative") or "Not written: the rule-based report has no narrative.", ""]
    out += ["## Payments in the case", "", "| Time | From | To | Amount | Format |", "|---|---|---|---|---|"]
    out += [f"| {_utc(e['ts'])} | {e['src']} | {e['dst']} | {m(e['amount'])} | {e.get('fmt') or ''} |"
            for e in sorted(case.get("edges", []), key=lambda e: e["ts"])]
    return "\n".join(out) + "\n"
