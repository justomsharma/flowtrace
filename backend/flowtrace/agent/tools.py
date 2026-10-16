"""Approved investigation tools.

Each tool runs read-only, parameterised Cypher against FalkorDB, computes its facts in Python,
and writes evidence with the transfer IDs that back it. The agent may only call tools listed in
TOOLS, with arguments validated by the pydantic models below, on accounts already in the case.
"""
from __future__ import annotations

from statistics import mean, median, pstdev
from typing import Callable, Literal

from pydantic import BaseModel, Field

from flowtrace.agent.context import DAY_MS, HOUR_MS, CaseContext
from flowtrace.behaviour.recurring import detect_recurrence, infer_purpose
from flowtrace.detectors.alerts import passthrough_pairs
from flowtrace.graph import queries as Q
from flowtrace.graph.trace import Trace, expand


# --- argument models ------------------------------------------------------------------------

class AccountArgs(BaseModel):
    account: str


class DirectionArgs(BaseModel):
    account: str
    direction: Literal["out", "in"] = "out"


class TraceArgs(BaseModel):
    account: str
    direction: Literal["forward", "backward"] = "forward"
    max_hops: int = Field(3, ge=1, le=4)


class GroupArgs(BaseModel):
    accounts: list[str] = Field(min_length=1, max_length=10)
    direction: Literal["out", "in"] = "out"


# --- helpers --------------------------------------------------------------------------------

def _cv(xs: list[float]) -> float:
    xs = [float(x) for x in xs]
    m = mean(xs) if xs else 0.0
    return pstdev(xs) / m if m else 0.0


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def _a(acc: str) -> str:
    """Short, readable account name for sentences."""
    return f"account …{acc.split(':')[-1][-5:]}"


def _window_rows(ctx: CaseContext, account: str, direction: str) -> list[dict]:
    cy = Q.TRANSFERS_OUT if direction == "out" else Q.TRANSFERS_IN
    rows = ctx.q(f"{direction}going transfers of {account}", cy, {"id": account, "t0": ctx.t0, "t1": ctx.t1}).rows
    ctx.add_edges(rows)
    return rows


def _cp_info(ctx: CaseContext, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    rows = ctx.q("counterparty owner type and degree", Q.CP_INFO, {"ids": ids[:100]}).rows
    for r in rows:
        if r["id"] in ctx.nodes:
            ctx.nodes[r["id"]]["entity_type"] = r["entity_type"]
    return {r["id"]: r for r in rows}


# --- tools ----------------------------------------------------------------------------------

def account_profile(ctx: CaseContext, a: AccountArgs) -> dict:
    """Who owns the account, what it did in the alert window, and how that compares with its own history."""
    acc = a.account
    card = ctx.q("account card", Q.ACCOUNT_CARD, {"id": acc}).rows
    out_rows = _window_rows(ctx, acc, "out")
    in_rows = _window_rows(ctx, acc, "in")
    hist = ctx.q("daily history (baseline)", Q.ACCOUNT_DAILY_HISTORY,
                 {"id": acc, "h0": ctx.history_start, "t0": ctx.t0}).rows

    out_sum = sum(r["amount"] for r in out_rows)
    in_sum = sum(r["amount"] for r in in_rows)
    days = max((ctx.t1 - ctx.t0) / DAY_MS, 1.0)
    rate = len(out_rows) / days
    daily = [r["n"] for r in hist]
    hist_days = (ctx.t0 - ctx.history_start) / DAY_MS
    # Days with no outgoing transfers count as zero in the baseline.
    padded = daily + [0] * max(int(hist_days) - len(daily), 0)
    med = median(padded) if padded else 0.0
    mad = median([abs(x - med) for x in padded]) if padded else 0.0
    z = (rate - med) / (1.4826 * mad + 1.0)
    info = card[0] if card else {}
    if acc in ctx.nodes:
        ctx.nodes[acc]["entity_type"] = info.get("entity_type")
        ctx.nodes[acc]["entity_id"] = info.get("entity_id")

    tx_out = [r["tx_id"] for r in out_rows]
    tx_in = [r["tx_id"] for r in in_rows]
    ctx.book.add(kind="context", code="PROFILE", tool="account_profile", step=ctx.step, accounts=[acc],
                 claim=(f"{_a(acc).capitalize()} ({info.get('entity_type') or 'owner type unknown'}) sent {_usd(out_sum)} in "
                        f"{len(out_rows)} payments to {len({r['dst'] for r in out_rows})} accounts, and received {_usd(in_sum)} "
                        f"from {len({r['src'] for r in in_rows})} accounts, in the period checked."),
                 weight=0.0, tx_ids=(tx_out + tx_in)[:40],
                 metrics={"out_cnt": len(out_rows), "out_sum": out_sum, "in_cnt": len(in_rows), "in_sum": in_sum})

    rules = ctx.rules
    if not daily and len(out_rows) >= 3:
        ctx.book.add(kind="support", code="DORMANT_THEN_ACTIVE", tool="account_profile", step=ctx.step,
                     claim=f"{_a(acc).capitalize()} sent nothing for {int(hist_days)} days, then suddenly made {len(out_rows)} payments.",
                     weight=0.35, tx_ids=tx_out[:20], accounts=[acc], escalator=True,
                     metrics={"prior_days": int(hist_days), "window_out": len(out_rows)})
    elif daily and z > rules["legitimacy"]["baseline_z_max"]:
        ctx.book.add(kind="support", code="ACTIVITY_SPIKE", tool="account_profile", step=ctx.step,
                     claim=f"{_a(acc).capitalize()} made {rate:.1f} payments a day, far more than its usual {med:.1f} a day.",
                     weight=0.35, tx_ids=tx_out[:20], accounts=[acc], metrics={"z": round(z, 2), "median": med})
    elif len(daily) >= 3:
        ctx.book.add(kind="counter", code="CONSISTENT_WITH_BASELINE", tool="account_profile", step=ctx.step,
                     claim=f"{_a(acc).capitalize()} made {rate:.1f} payments a day, which is normal for it (usually {med:.1f} a day).",
                     weight=0.3, tx_ids=tx_out[:10], accounts=[acc], metrics={"z": round(z, 2), "median": med})

    pt = rules["detectors"]["passthrough"]
    pairs = passthrough_pairs([{**r, "cp": r["src"]} for r in in_rows], [{**r, "cp": r["dst"]} for r in out_rows], pt)
    ratio = out_sum / in_sum if in_sum else 0.0
    if pairs:
        fwd = {o["tx_id"]: o for _, o in pairs}
        ctx.book.add(kind="support", code="PASS_THROUGH", tool="account_profile", step=ctx.step,
                     claim=(f"Money did not stay: {_a(acc)} sent on {_usd(sum(o['amount'] for o in fwd.values()))} "
                            f"within {pt['max_gap_hours']} hours of receiving it, in similar amounts."),
                     weight=0.45, tx_ids=list(dict.fromkeys(t["tx_id"] for pr in pairs for t in pr))[:30],
                     accounts=[acc], metrics={"pairs": len(pairs), "ratio": round(ratio, 3)})
    risky = [r for r in out_rows if r["fmt"] in rules["escalators"]["high_risk_formats"]]
    if risky:
        ctx.book.add(kind="support", code="HIGH_RISK_RAIL", tool="account_profile", step=ctx.step,
                     claim=f"{len(risky)} payments ({_usd(sum(r['amount'] for r in risky))}) were made in hard-to-trace form: "
                           f"{', '.join(sorted({r['fmt'] for r in risky}))}.",
                     weight=0.2, tx_ids=[r["tx_id"] for r in risky][:20], accounts=[acc])
    return {"entity_type": info.get("entity_type"), "out_cnt": len(out_rows), "out_cp": len({r['dst'] for r in out_rows}),
            "in_cnt": len(in_rows), "in_cp": len({r['src'] for r in in_rows}), "passthrough": round(ratio, 2),
            "baseline_z": round(z, 2), "history_days_active": len(daily)}


def counterparty_history(ctx: CaseContext, a: DirectionArgs) -> dict:
    """For each counterparty in the window: how long and how often they dealt before, and on what schedule."""
    acc, d = a.account, a.direction
    rows = _window_rows(ctx, acc, d)
    key = "dst" if d == "out" else "src"
    cps = sorted({r[key] for r in rows} - {acc})
    if not cps:
        return {"counterparties": 0}
    cy = Q.PAIR_HISTORY_OUT if d == "out" else Q.PAIR_HISTORY_IN
    hist = {r["cp"]: r for r in ctx.q(f"relationship history ({d})", cy, {"id": acc, "cps": cps, "before": ctx.t0}).rows}
    info = _cp_info(ctx, cps)
    leg = ctx.rules["legitimacy"]

    established, new, recurring = [], [], []
    for cp in cps:
        h = hist.get(cp)
        win_ts = [r["ts"] for r in rows if r[key] == cp]
        win_amt = [r["amount"] for r in rows if r[key] == cp]
        if not h or h["prior_cnt"] == 0:
            new.append(cp)
            continue
        age_days = (ctx.t0 - h["first_ts"]) / DAY_MS
        if h["prior_cnt"] >= leg["established_relationship"]["min_prior_tx"] and age_days >= leg["established_relationship"]["min_age_days"]:
            established.append(cp)
        rec = detect_recurrence(list(h["ts_list"]) + win_ts, list(h["amounts"]) + win_amt, leg["recurring"])
        if rec:
            ci = info.get(cp, {})
            g = infer_purpose(d, next((r["fmt"] for r in rows if r[key] == cp), ""), rec, ci.get("entity_type"),
                              ci.get("in_deg", 0), ci.get("out_deg", 0), False, False, leg["hub_receiver_in_degree"])
            recurring.append({"cp": cp, "purpose": g.purpose, "cadence": rec.cadence, "amount": rec.amount_mean,
                              "because": g.because})

    n = len(cps)
    tx_all = [r["tx_id"] for r in rows]
    if established and len(established) / n >= 0.5:
        ctx.book.add(kind="counter", code="ESTABLISHED_COUNTERPARTIES", tool="counterparty_history", step=ctx.step,
                     claim=f"{len(established)} of the {n} people paid are long-standing contacts: {_a(acc)} had paid them several times before.",
                     weight=round(0.5 * len(established) / n, 2), accounts=[acc] + established[:10],
                     tx_ids=[r["tx_id"] for r in rows if r[key] in established][:30],
                     metrics={"established": len(established), "counterparties": n})
    esc = ctx.rules["escalators"]
    if new and len(new) / n >= 0.5:
        ctx.book.add(kind="support", code="NEW_COUNTERPARTIES", tool="counterparty_history", step=ctx.step,
                     claim=f"{len(new)} of the {n} accounts involved had never dealt with {_a(acc)} before.",
                     weight=round(0.5 * len(new) / n, 2), accounts=[acc] + new[:10], escalator=len(new) / n >= esc["new_counterparty_share"] and n >= 3,
                     tx_ids=[r["tx_id"] for r in rows if r[key] in new][:30], metrics={"new": len(new), "counterparties": n})
    if n >= 3:
        totals = [sum(r["amount"] for r in rows if r[key] == cp) for cp in cps]
        cv = _cv(totals)
        if cv < esc["equal_split_amount_cv"]:
            ctx.book.add(kind="support", code="EQUAL_SPLITS", tool="counterparty_history", step=ctx.step,
                         claim=f"The money was split into near-identical amounts across {n} accounts, a common way to avoid attention.",
                         weight=0.4, escalator=True, accounts=[acc] + cps[:10], tx_ids=tx_all[:30], metrics={"cv": round(cv, 4)})
    if d == "out":
        hubs = [cp for cp in cps if info.get(cp, {}).get("in_deg", 0) >= leg["hub_receiver_in_degree"]]
        if hubs and len(hubs) / n >= 0.5:
            ctx.book.add(kind="counter", code="PAYS_HIGH_VOLUME_RECEIVERS", tool="counterparty_history", step=ctx.step,
                         claim=f"{len(hubs)} of the {n} receivers are paid by {leg['hub_receiver_in_degree']}+ customers each "
                               f"(merchant, lender or utility-like).",
                         weight=0.3, accounts=hubs[:10], tx_ids=[r["tx_id"] for r in rows if r[key] in hubs][:20])
    if recurring:
        kinds = sorted({r["purpose"] for r in recurring})
        ctx.book.add(kind="counter", code="RECURRING_FLOWS", tool="counterparty_history", step=ctx.step,
                     claim=f"{len(recurring)} of the {n} payments repeat on a regular schedule, like salary, rent or bills.",
                     weight=min(0.6, 0.2 + 0.1 * len(recurring)), accounts=[r["cp"] for r in recurring][:10],
                     tx_ids=[r["tx_id"] for r in rows if r[key] in {x['cp'] for x in recurring}][:30],
                     metrics={"recurring": recurring[:10]})
    return {"counterparties": n, "established": len(established), "new": len(new),
            "recurring": [{"cp": r["cp"], "purpose": r["purpose"], "cadence": r["cadence"]} for r in recurring[:8]]}


def recurring_flows(ctx: CaseContext, a: AccountArgs) -> dict:
    """Behaviour memory: every counterparty series of this account, checked for a regular schedule and a likely purpose."""
    acc = a.account
    series = ctx.q("payment series per counterparty", Q.FLOW_SERIES, {"id": acc, "before": ctx.t1}).rows
    cps = [r["cp"] for r in series]
    info = _cp_info(ctx, cps)
    leg = ctx.rules["legitimacy"]
    found = []
    for r in series:
        rec = detect_recurrence(list(r["ts_list"]), list(r["amounts"]), leg["recurring"])
        if not rec:
            continue
        ci = info.get(r["cp"], {})
        g = infer_purpose(r["dir"], r["fmt"], rec, ci.get("entity_type"), ci.get("in_deg", 0), ci.get("out_deg", 0),
                          ci.get("entity_id") is not None and ci.get("entity_id") == ctx.nodes.get(acc, {}).get("entity_id"),
                          False, leg["hub_receiver_in_degree"])
        found.append({"cp": r["cp"], "dir": r["dir"], "purpose": g.purpose, "confidence": g.confidence,
                      "cadence": rec.cadence, "amount": rec.amount_mean, "n": rec.occurrences, "because": g.because})
    inflow = [f for f in found if f["dir"] == "in"]
    outflow = [f for f in found if f["dir"] == "out"]
    if found:
        ctx.book.add(kind="counter", code="BEHAVIOUR_MEMORY", tool="recurring_flows", step=ctx.step, accounts=[acc],
                     claim=f"{_a(acc).capitalize()} has {len(inflow)} regular incoming and {len(outflow)} regular outgoing payments "
                           f"({', '.join(sorted({f['purpose'] for f in found}))}).",
                     weight=min(0.5, 0.15 + 0.07 * len(found)), metrics={"series": found[:12]})
    payroll = [f for f in inflow if f["purpose"] in ("PAYROLL", "RECURRING_INCOME")]
    obligations = [f for f in outflow if f["purpose"] in ("FIXED_OBLIGATION", "BILL_PAYMENT", "STANDING_ORDER", "RECURRING_PAYMENT")]
    if payroll and obligations:
        ctx.book.add(kind="counter", code="SALARY_DISTRIBUTION", tool="recurring_flows", step=ctx.step, accounts=[acc],
                     claim=f"Regular income ({payroll[0]['purpose'].lower()}, {payroll[0]['cadence']}) is followed by "
                           f"{len(obligations)} regular obligations: the split looks like ordinary salary use.",
                     weight=0.7, metrics={"income": payroll[:3], "obligations": obligations[:6]})
    return {"series": len(series), "recurring": [{k: f[k] for k in ("cp", "dir", "purpose", "cadence")} for f in found[:10]]}


def trace_funds(ctx: CaseContext, a: TraceArgs) -> dict:
    """Follow the money hop by hop in time order (forward = where it went, backward = where it came from)."""
    tr = ctx.rules["trace"]
    rules = {**tr, "max_hops": a.max_hops}
    seed_ts = ctx.t0 if a.direction == "forward" else ctx.t1
    trace = Trace()
    expand([{"id": a.account, "ts": seed_ts}], a.direction, rules, emit=ctx.emit, trace=trace)
    for qlog in trace.queries:
        entry = {"step": ctx.step, **qlog, "params": {k: v for k, v in qlog["params"].items() if k != "visited"}}
        ctx.queries.append(entry)
        ctx.emit({"type": "query", **entry})
    edges = list(trace.edges.values())
    ctx.add_edges(edges, role="trace")
    esc = ctx.rules["escalators"]
    summary = {"hops": max((e["hop"] for e in edges), default=0), "accounts": len(trace.visited) - 1,
               "transfers": len(edges), "hubs": len(trace.hubs)}

    if a.direction == "forward":
        hop1 = [e for e in edges if e["hop"] == 1]
        hop2 = [e for e in edges if e["hop"] == 2]
        recv = {}
        for e in hop1:
            recv.setdefault(e["dst"], []).append(e)
        fwd_amt, recv_amt, fast = 0.0, sum(e["amount"] for e in hop1), []
        for e in hop2:
            arrivals = recv.get(e["src"], [])
            if arrivals and any(0 <= e["ts"] - x["ts"] <= esc["fast_forward_hours"] * HOUR_MS for x in arrivals):
                fwd_amt += e["amount"]
                fast.append(e)
        share = min(fwd_amt / recv_amt, 1.0) if recv_amt else 0.0
        summary["forwarded_share_fast"] = round(share, 2)
        if fast and share >= esc["fast_forward_share"]:
            ctx.book.add(kind="support", code="FAST_ONWARD_TRANSFER", tool="trace_funds", step=ctx.step,
                         claim=f"The receivers passed {share:.0%} of the money on to others within {esc['fast_forward_hours']} hours "
                               f"({len(fast)} onward transfers).",
                         weight=0.5, escalator=True, accounts=sorted({e['src'] for e in fast})[:10],
                         tx_ids=[e["tx_id"] for e in hop1[:10] + fast[:20]], metrics={"share": round(share, 3)})
        elif hop1 and share < 0.2:
            ctx.book.add(kind="counter", code="FUNDS_STOP", tool="trace_funds", step=ctx.step,
                         claim=f"The money stopped with the receivers: only {share:.0%} was passed on quickly.",
                         weight=0.3, accounts=sorted(recv)[:10], tx_ids=[e["tx_id"] for e in hop1[:20]],
                         metrics={"share": round(share, 3)})
        exits = [e for e in edges if e["hop"] >= 2 and e["fmt"] in esc["high_risk_formats"]]
        if exits:
            ctx.book.add(kind="support", code="CASH_OR_CRYPTO_EXIT", tool="trace_funds", step=ctx.step,
                         claim=f"Further down the chain, {_usd(sum(e['amount'] for e in exits))} left in {len(exits)} hard-to-trace payments: "
                               f"{', '.join(sorted({e['fmt'] for e in exits}))}.",
                         weight=0.35, tx_ids=[e["tx_id"] for e in exits[:20]], accounts=sorted({e['dst'] for e in exits})[:10])
        if a.account in {e["dst"] for e in edges}:
            back = [e for e in edges if e["dst"] == a.account]
            ctx.book.add(kind="support", code="ROUND_TRIP", tool="trace_funds", step=ctx.step,
                         claim=f"Money sent by {_a(a.account)} came back to it after passing through other accounts.",
                         weight=0.55, escalator=True, tx_ids=[e["tx_id"] for e in back[:10]], accounts=[a.account])
    else:
        origins = {e["src"] for e in edges if e["hop"] == 1}
        if len(origins) >= ctx.rules["detectors"]["fan_in_min"]:
            ctx.book.add(kind="support", code="MANY_SOURCES", tool="trace_funds", step=ctx.step,
                         claim=f"{_a(a.account).capitalize()} received money from {len(origins)} different accounts.",
                         weight=0.3, tx_ids=[e["tx_id"] for e in edges if e["hop"] == 1][:20], accounts=sorted(origins)[:10])
    if trace.hubs:
        ctx.book.add(kind="context", code="HUBS_REACHED", tool="trace_funds", step=ctx.step,
                     claim=f"The money reached {len(trace.hubs)} very busy accounts (likely shops, payment processors or exchanges).",
                     weight=0.0, accounts=sorted(trace.hubs)[:10])
    return summary


def shared_counterparties(ctx: CaseContext, a: GroupArgs) -> dict:
    """Do several case accounts pay the same receivers (or get paid by the same senders)?"""
    cy = Q.SHARED_DESTINATIONS if a.direction == "out" else Q.SHARED_SOURCES
    rows = ctx.q(f"shared {'destinations' if a.direction == 'out' else 'sources'}", cy,
                 {"ids": a.accounts, "t0": ctx.t0, "t1": ctx.t1}).rows
    key, cnt = ("dst", "senders") if a.direction == "out" else ("src", "receivers")
    if rows:
        top = rows[0]
        ctx.book.add(kind="support", code="CONVERGENCE", tool="shared_counterparties", step=ctx.step,
                     claim=f"{top[cnt]} of the case accounts {'paid' if a.direction == 'out' else 'were paid by'} "
                           f"{top[key]} ({_usd(top['total'])} in {top['n']} transfers).",
                     weight=0.4, tx_ids=list(top["tx_ids"]), accounts=[top[key]] + a.accounts[:9])
    return {"shared": [{key: r[key], cnt: r[cnt], "total": round(r["total"], 2)} for r in rows[:5]]}


def find_cycles(ctx: CaseContext, a: AccountArgs) -> dict:
    """Look for short, time-ordered loops that bring money back to the account."""
    t0 = ctx.t0 - 3 * DAY_MS
    rows = ctx.q("time-ordered cycles", Q.CYCLES, {"id": a.account, "t0": t0, "t1": ctx.t1}).rows
    for r in rows:
        accs = r["accounts"]
        ctx.add_edges([{"tx_id": t, "src": accs[i], "dst": accs[i + 1], "ts": r["ts"][i], "amount": r["amounts"][i]}
                       for i, t in enumerate(r["tx_ids"])], role="cycle")
    if rows:
        best = rows[0]
        kept = best["amounts"][-1] / best["amounts"][0] if best["amounts"][0] else 0
        ctx.book.add(kind="support", code="CYCLE", tool="find_cycles", step=ctx.step,
                     claim=f"Money went round in a loop: it left {_a(a.account)}, passed through "
                           f"{len(best['accounts']) - 2} other accounts and {kept:.0%} of it came back.",
                     weight=0.55, escalator=True, tx_ids=list(best["tx_ids"]), accounts=list(best["accounts"]),
                     metrics={"cycles": len(rows), "amount_kept": round(kept, 3)})
    return {"cycles": len(rows)}


def owner_network(ctx: CaseContext, a: AccountArgs) -> dict:
    """Other accounts held by the same owner, and whether case money only moves between them."""
    sib = [r["id"] for r in ctx.q("same-owner accounts", Q.SIBLING_ACCOUNTS, {"id": a.account}).rows]
    out_rows = [e for e in ctx.edges.values() if e["src"] == a.account and e["dst"] in sib]
    if sib:
        ctx.book.add(kind="context", code="OWNER_ACCOUNTS", tool="owner_network", step=ctx.step,
                     claim=f"The owner of {_a(a.account)} holds {len(sib)} other account(s).", weight=0.0, accounts=sib[:10])
    if out_rows:
        ctx.book.add(kind="counter", code="INTERNAL_TRANSFER", tool="owner_network", step=ctx.step,
                     claim=f"{len(out_rows)} payments go to other accounts of the same owner (moving money between their own accounts).",
                     weight=0.35, tx_ids=[e["tx_id"] for e in out_rows][:20], accounts=sib[:10])
    return {"sibling_accounts": len(sib), "internal_transfers": len(out_rows)}


def case_memory(ctx: CaseContext, a: AccountArgs) -> dict:
    """Prior cases on this account, and distance to accounts already escalated."""
    prior = ctx.q("prior cases", Q.PRIOR_CASES, {"id": a.account, "case_id": ctx.case_id}).rows
    near = ctx.q("nearest escalated account", Q.NEAREST_ESCALATED, {"id": a.account, "case_id": ctx.case_id}).rows
    if any(p["disposition"] == "ESCALATE" for p in prior):
        ctx.book.add(kind="support", code="PRIOR_ESCALATION", tool="case_memory", step=ctx.step, accounts=[a.account],
                     claim=f"{_a(a.account).capitalize()} was already escalated in an earlier case.", weight=0.5, escalator=True)
    live_close = [p for p in prior if p["disposition"] == "CLOSE" and (p.get("created_ts") or 0) > ctx.t0 - 90 * DAY_MS]
    if live_close:
        ctx.book.add(kind="counter", code="PRIOR_CLOSURE", tool="case_memory", step=ctx.step, accounts=[a.account],
                     claim=f"An earlier alert on {_a(a.account)} was checked and closed as normal ({live_close[0]['summary'] or 'no note'}).",
                     weight=0.25)
    if near and near[0]["hops"] <= 2:
        ctx.book.add(kind="support", code="NEAR_ESCALATED", tool="case_memory", step=ctx.step,
                     accounts=[a.account, near[0]["account"]], escalator=True,
                     claim=f"{_a(a.account).capitalize()} is {near[0]['hops']} step(s) away from {_a(near[0]['account'])}, which was escalated before.",
                     weight=0.45)
    return {"prior_cases": len(prior), "nearest_escalated_hops": near[0]["hops"] if near else None}


ToolFn = Callable[[CaseContext, BaseModel], dict]

TOOLS: dict[str, tuple[ToolFn, type[BaseModel], str]] = {
    "account_profile": (account_profile, AccountArgs, account_profile.__doc__),
    "counterparty_history": (counterparty_history, DirectionArgs, counterparty_history.__doc__),
    "recurring_flows": (recurring_flows, AccountArgs, recurring_flows.__doc__),
    "trace_funds": (trace_funds, TraceArgs, trace_funds.__doc__),
    "shared_counterparties": (shared_counterparties, GroupArgs, shared_counterparties.__doc__),
    "find_cycles": (find_cycles, AccountArgs, find_cycles.__doc__),
    "owner_network": (owner_network, AccountArgs, owner_network.__doc__),
    "case_memory": (case_memory, AccountArgs, case_memory.__doc__),
}


class ToolError(ValueError):
    pass


def run_tool(ctx: CaseContext, name: str, raw_args: dict) -> dict:
    """Validate and execute one approved tool call."""
    if name not in TOOLS:
        raise ToolError(f"unknown tool {name}")
    fn, model, _ = TOOLS[name]
    args = model.model_validate({k: v for k, v in raw_args.items() if v is not None})
    accounts = [getattr(args, "account", None)] + list(getattr(args, "accounts", []) or [])
    for acc in filter(None, accounts):
        if not ctx.known(acc):
            raise ToolError(f"account {acc} is not part of this investigation")
    return fn(ctx, args)
