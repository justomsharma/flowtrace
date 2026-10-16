"""The case overview page and the STR draft, built only from what is stored: the case (its flagged payments),
the investigation result, the hold plan and the case's actions. Read-only: no graph query, no write.

  flow      the case's payments, the money the investigation followed (trace/cycle; never the history it read
            for context), and the hold plan's trail payments into accounts proposed for a hold or held.
            Role from that flow: no money in = Sender, no money out = Destination, otherwise Intermediary.
  status    Flagged = a case payment, or one a red flag cites. In trail = money the investigation followed
            (traces, loops) or the plan's trail into a hold. Normal = the rest.
"""
from __future__ import annotations

import re

from collections import defaultdict

from flowtrace.agent.brief import alert_reasons, assess, brief
from flowtrace.agent.report import clean

HOUR = 3_600_000
DAY = 24 * HOUR
FLOW_ROLES = ("alert", "trace", "cycle")  # result graph edges that are case money; "context" is history
OPEN_HOLD = {"PROPOSED": "proposed", "APPROVED": "approved"}


def letters(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


ROLE_ORDER = {"Sender": 0, "Intermediary": 1, "Destination": 2}


def account_names(nodes: list[dict], others: list[str]) -> dict[str, str]:
    """Readable per-case names, "Account A", "Account B"…: senders first, then middle accounts, then receivers,
    then any other account the page mentions. Raw IDs (bank:account) mean nothing to an analyst."""
    order = [n["id"] for n in sorted(nodes, key=lambda n: ROLE_ORDER.get(n["role"], 3))]
    order += [a for a in dict.fromkeys(others) if a not in set(order)]
    return {a: f"Account {letters(i)}" for i, a in enumerate(order)}


def fill(text: str, accounts: list[str], names: dict[str, str]) -> str:
    """Put the evidence's subject account into a short reason ("{A} sent on …")."""
    who = names.get(accounts[0], "The account") if accounts else "The account"
    return text.replace("{A}", who)


def confidence(weight: float, rules: dict) -> str:
    t = rules["ui"]["evidence_confidence"]
    return "High" if weight >= t["high"] else "Medium" if weight >= t["medium"] else "Low"


def priority(score: float, rules: dict) -> str:
    t = rules["ui"]["priority"]
    return "High" if score >= t["high"] else "Medium" if score >= t["medium"] else "Low"


def case_currency(case: dict, plan: dict | None) -> str:
    """Every page shows one currency, US dollars: the graph stores every amount in dollars (converted at the
    dataset's fixed rates) and the investigation's sentences are written in dollars too."""
    return "US Dollar"


def fx_rate(currency: str) -> float:
    """Units of `currency` per US dollar, from the rates derived from the data."""
    from flowtrace.actions.planner import fx_rates
    return 1.0 if currency == "US Dollar" else fx_rates().get(currency, 1.0)


def case_rate(currency: str, plan: dict | None) -> float:
    """Amounts are shown in US dollars as stored, so the rate is always 1."""
    return 1.0


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
            "confidence_pct": round(d["confidence"] * 100) if d.get("confidence") is not None else None,
            "reasons": d.get("reasons", [])}


INDICATORS = (("model", "ml_raw", "model"), ("red_flags", "red_raw", "red_flags"),
              ("shape", "shape_raw", "shape"), ("legitimacy", "legit_raw", "legitimacy"))


def build_overview(case: dict, result: dict | None, plan: dict | None, actions: list[dict], job: dict | None,
                   failed: bool, rules: dict, uploaded_at: float | None = None) -> dict:
    currency = case_currency(case, plan)
    rate = case_rate(currency, plan)
    f = flow(case, result, plan, actions, rules)
    evidence = (result or {}).get("evidence", [])

    others = [a for e in evidence for a in e.get("accounts", [])[:1]] + [p[k] for p in f["payments"] for k in ("src", "dst")]
    names = account_names(f["nodes"], list(case.get("accounts", [])) + others)

    def item(e: dict) -> dict:
        return {"id": e["id"], "code": e["code"], "claim": e["claim"], "brief": fill(brief(e), e.get("accounts", []), names),
                **assess(e), "weight": e["weight"],
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
        "alert_reasons": alert_reasons(case),
        # A guess at the underlying crime from the laundering shape (the data has no crime label).
        "crime_guess": crime_guess(case, evidence, rules),
        "summary": {"headline": clean(rep["headline"]), "text": clean(rep["executive_summary"]["text"]),
                    "points": summary_points(rep, evidence, result, names), "by_ai": rep.get("author") not in (None, "template")}
                   if rep else None,
        "next_steps": [clean(x) for x in (rep or {}).get("next_steps", [])][:3],
        "names": names,
        "story": story(case, evidence, result, names, rules),
        "verdict_basis": verdict_basis(result, evidence, names),
        "steps": (result or {}).get("steps", []),
    }


PATTERN = {
    "FAN-OUT": "{A} distributed the funds to several accounts in a short period.",
    "FAN-IN": "Funds from several accounts were collected into {A}.",
    "GATHER-SCATTER": "{A} collected funds from several accounts and paid them out to others.",
    "SCATTER-GATHER": "Funds from {A} were routed through intermediary accounts to a single beneficiary.",
    "CYCLE": "Funds left {A} and returned to it through other accounts.",
    "BIPARTITE": "A group of accounts paid another group of accounts directly.",
    "STACK": "Funds passed through layers of intermediary accounts.",
    "RANDOM": "Funds moved through a chain of accounts with no regular pattern.",
}
OUTCOME = {"ESCALATE": "escalate to a senior analyst", "REVIEW": "manual review", "CLOSE": "close as normal activity"}


def _when(ms: int | None) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%d %b %H:%M").lstrip("0") if ms else ""


def story(case: dict, evidence: list[dict], result: dict | None, names: dict[str, str], rules: dict) -> list[str]:
    """Two or three plain sentences: what moved, the pattern, and what the investigation concluded."""
    edges = case.get("edges", [])
    n = len(case.get("accounts", []))
    out = [f"{n} accounts moved {money(case.get('amount', 0.0))} in {len(edges)} payment{'s' if len(edges) != 1 else ''} "
           f"between {_when(case.get('t0'))} and {_when((case.get('t1') or 1) - 1)} UTC."]
    pattern = PATTERN.get((case.get("shape") or {}).get("typology"))
    if pattern:
        out.append(pattern.replace("{A}", names.get(case.get("primary"), "the main account")))
    if result:
        tags = [assess(e)["tag"] for e in evidence if e["kind"] == "support"]
        strong = sum(t == "Strong sign" for t in tags)
        normal = sum(e["kind"] == "counter" for e in evidence)
        action = (result.get("disposition") or {}).get("action")
        out.append(f"The investigation found {strong} strong and {len(tags) - strong} weaker indicator{'s' if len(tags) - strong != 1 else ''}, "
                   f"and {normal} legitimate explanation{'s' if normal != 1 else ''}. Recommendation: {OUTCOME.get(action, 'review')}.")
    return out


def verdict_basis(result: dict | None, evidence: list[dict], names: dict[str, str]) -> str | None:
    """One sentence naming what decided the case, weighed by measured strength, so the verdict never
    looks at odds with the explanations listed beside it."""
    if not result:
        return None
    action = (result.get("disposition") or {}).get("action")
    rank = {"Strong sign": 0, "Moderate sign": 1, "": 2, "Common in normal accounts": 3}
    sup = sorted((e for e in evidence if e["kind"] == "support"), key=lambda e: (rank.get(assess(e)["tag"], 2), -e["weight"]))
    cnt = sorted((e for e in evidence if e["kind"] == "counter"), key=lambda e: -e["weight"])
    line = lambda e: fill(brief(e), e.get("accounts", []), names)  # noqa: E731
    n = len(cnt)
    if action == "ESCALATE" and sup:
        top, a = sup[0], assess(sup[0])
        strength = f" ({a['why'].rstrip('.')})" if a["tag"] in ("Strong sign", "Moderate sign") else ""
        outweighs = f", which outweighs {n} legitimate explanation{'s' if n != 1 else ''}" if n else ""
        return f"Escalated because {line(top)[0].lower() + line(top)[1:]}{strength}{outweighs}."
    if action == "CLOSE" and cnt:
        weak = " The indicators found are weak on their own." if sup else ""
        return f"Closed because {line(cnt[0])[0].lower() + line(cnt[0])[1:]}.{weak}"
    if action == "REVIEW":
        if sup and cnt:
            return f"Mixed: {line(sup[0])[0].lower() + line(sup[0])[1:]}, but {line(cnt[0])[0].lower() + line(cnt[0])[1:]}. An analyst should decide."
        return "The indicators are not strong enough to escalate or close. An analyst should decide."
    return None


def crime_guess(case: dict, evidence: list[dict], rules: dict) -> list[str]:
    """Likely crimes from the laundering shape, plus money-mule crimes when money passed straight through.
    Read from config.yaml each time, so stored cases follow the current mapping."""
    m = rules.get("crime_hypotheses", {})
    by_indicator = rules.get("crime_by_indicator", {})
    out = list(m.get((case.get("shape") or {}).get("typology"), []) or [])
    # Strongest indicators first, so the most telling crime leads the list.
    found = sorted((e for e in evidence if e.get("kind") == "support"), key=lambda e: -e.get("weight", 0))
    for e in found:
        out += by_indicator.get(e.get("code"), [])
    return list(dict.fromkeys(out))[:3]


DECISION_POINT = {"ESCALATE": "Recommend escalating to a senior analyst.",
                  "REVIEW": "Mixed signals: a person should review it.",
                  "CLOSE": "Looks like normal activity: recommend closing."}


def summary_points(rep: dict, evidence: list[dict], result: dict | None, names: dict[str, str] | None = None) -> list[str]:
    """The report's bullet points; results saved before reports had them get the strongest reasons each way."""
    names = names or {}
    by_id = {e["id"]: e for e in evidence}
    subject = lambda ids: next((by_id[i].get("accounts", []) for i in ids if i in by_id), [])  # noqa: E731
    # Rule-written points are rebuilt from the evidence each time, so they always use the current wording.
    by_ai = rep.get("author") not in (None, "template")
    pts = [fill(clean(p["text"]), subject(p.get("evidence_ids", [])), names)
           for p in (rep.get("summary_points") or [] if by_ai else []) if p.get("text")]
    if not pts:
        top = lambda kind, n: sorted((e for e in evidence if e["kind"] == kind), key=lambda e: -e["weight"])[:n]  # noqa: E731
        pts = [fill(brief(e).rstrip(".…") + ".", e.get("accounts", []), names) for e in top("support", 3) + top("counter", 2)]
        action = ((result or {}).get("disposition") or {}).get("action")
        if action in DECISION_POINT:
            pts.append(DECISION_POINT[action])
    return pts[:6]


# --- STR draft (Markdown) ----------------------------------------------------------------------

CODES = {"US Dollar": "USD", "Euro": "EUR", "UK Pound": "GBP", "Yuan": "CNY", "Yen": "JPY", "Rupee": "INR",
         "Ruble": "RUB", "Canadian Dollar": "CAD", "Australian Dollar": "AUD", "Swiss Franc": "CHF",
         "Mexican Peso": "MXN", "Shekel": "ILS", "Brazil Real": "BRL", "Saudi Riyal": "SAR", "Bitcoin": "BTC"}
ACTION_TITLE = {"HOLD": "Hold account", "RELEASE": "Release the hold on account", "ESCALATE": "Send to a senior analyst",
                "FILE_STR": "File an STR", "CLOSE": "Close as normal activity"}


def money(usd: float, currency: str | None = "US Dollar", rate: float | None = 1.0) -> str:
    """Amounts are stated in US dollars everywhere, as stored in the graph."""
    return f"${usd:,.0f}"


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
    no = case["case_id"].rsplit("-C", 1)[-1].lstrip("0") or case["case_id"]
    out = [f"# STR draft: Case {no}", "", f"Case reference: {case['case_id']}", "",
           f"Draft for a person to review before filing. The narrative was {by}; every figure comes from the "
           f"payment graph. Amounts in US dollars.", "",
           "## Summary", "", f"**{clean(rep['headline'])}**", ""]
    f = flow(case, result, plan, actions, {"ui": {"flow_max_accounts": 10_000}})
    names = account_names(f["nodes"], list(case.get("accounts", [])))
    out += [f"- {p}" for p in summary_points(rep, evidence, result, names)] + ["",
           f"Recommendation: **{d['action'].capitalize()}** (risk {'High' if d['risk'] >= 0.55 else 'Medium' if d['risk'] >= 0.25 else 'Low'}, "
           f"confidence {round((d.get('confidence') or 0) * 100)}%). "
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
        out += [f"- {clean(e['claim'])}" for e in items] or ["- None found."]
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
    # Generated text says "1 accounts"; an STR should not.
    return re.sub(r"\b1 (account|payment|receiver|sender)s\b", r"1 \1", "\n".join(out)) + "\n"
