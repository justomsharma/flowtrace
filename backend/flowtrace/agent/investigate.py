"""The bounded investigation loop.

  for each step (max_steps, max_queries, case_timeout):
      policy picks an approved tool  ->  tool runs read-only Cypher  ->  evidence is recorded
      ->  risk is recomputed and streamed
  then: deterministic disposition -> senior-analyst report -> case memory written to the graph
"""
from __future__ import annotations

import time
from typing import Callable

from flowtrace.agent.context import DAY_MS, CaseContext
from flowtrace.agent.disposition import decide
from flowtrace.agent.policy import FixedPolicy, HeuristicPolicy, LLMPolicy
from flowtrace.agent.report import write_report
from flowtrace.agent.tools import ToolError, run_tool
from flowtrace.graph import queries as Q
from flowtrace.graph.client import rw

POLICIES = {"claude": LLMPolicy, "heuristic": HeuristicPolicy, "fixed": FixedPolicy}


def crime_hypotheses(ctx: CaseContext, rules: dict) -> list[dict]:
    """Map the observed shape to predicate crimes the data generator models. Hypotheses, not findings."""
    out = []
    shape = (ctx.shape or {}).get("typology")
    for crime in rules["crime_hypotheses"].get(shape, []):
        out.append({"crime": crime, "basis": f"{shape} shape"})
    if {"PASS_THROUGH", "FAST_ONWARD_TRANSFER"} & ctx.book.codes():
        for crime in rules["crime_hypotheses"].get("PASS-THROUGH", []):
            out.append({"crime": crime, "basis": "funds pass through quickly (mule behaviour)"})
    seen, uniq = set(), []
    for h in out:
        if h["crime"] not in seen:
            seen.add(h["crime"])
            uniq.append(h)
    return uniq


def policy_ran(chosen: str, calls: list[dict]) -> str:
    """The policy that actually picked the steps: Claude falls back to rules when it has no key or fails."""
    used = {c.get("policy", chosen) for c in calls}
    if not used or used == {chosen}:
        return chosen
    if chosen not in used:
        return f"{next(iter(used))} fallback" if len(used) == 1 else " + ".join(sorted(used))
    return " + ".join([chosen] + sorted(used - {chosen})) + " fallback"


def investigate(ctx: CaseContext, policy_name: str = "claude", use_llm_report: bool = True,
                save_memory: bool = True) -> dict:
    rules = ctx.rules
    policy = POLICIES[policy_name]()
    started = time.perf_counter()
    ctx.emit({"type": "case_start", "case_id": ctx.case_id, "primary": ctx.primary, "accounts": ctx.accounts,
              "policy": policy.name, "ml_score": ctx.ml_score, "shape": ctx.shape})
    ctx.nodes.update({a: {"id": a, "role": "subject"} for a in ctx.accounts if a not in ctx.nodes})
    ctx.emit({"type": "graph", "nodes": list(ctx.nodes.values()), "edges": []})
    ctx.add_edges(ctx.alert_edges, role="alert")

    shape_conf = (ctx.shape or {}).get("confidence", 0.0)
    stop_reason = "policy finished"
    for i in range(rules["agent"]["max_steps"]):
        if time.perf_counter() - started > rules["agent"]["case_timeout_s"]:
            stop_reason = "time budget used"
            break
        if len(ctx.queries) >= rules["agent"]["max_queries"]:
            stop_reason = "query budget used"
            break
        ctx.step = i + 1
        step = policy.next(ctx)
        if step is None:
            break
        ctx.emit({"type": "step_start", "step": ctx.step, "tool": step["action"], "args": step["args"],
                  "reason": step["reason"], "hypothesis": step.get("hypothesis"),
                  "policy": step.get("policy", policy.name)})
        before = len(ctx.book.items)
        t0 = time.perf_counter()
        try:
            summary = run_tool(ctx, step["action"], step["args"])
            error = None
        except ToolError as e:
            summary, error = {}, str(e)
        except Exception as e:  # a failing query must not kill the case
            summary, error = {}, f"{type(e).__name__}: {e}"
        new_ev = [e.as_dict() for e in ctx.book.items[before:]]
        ctx.calls.append({"step": ctx.step, "tool": step["action"], "args": step["args"], "summary": summary,
                          "error": error, "reason": step["reason"], "ms": round((time.perf_counter() - t0) * 1000, 1),
                          "policy": step.get("policy", policy.name)})
        interim = decide(ctx.book, ctx.ml_score, shape_conf, rules)
        ctx.emit({"type": "step_done", "step": ctx.step, "tool": step["action"], "summary": summary, "error": error,
                  "evidence": new_ev, "ms": ctx.calls[-1]["ms"], "risk": interim.risk, "action": interim.action})
    else:
        stop_reason = "step budget used"

    disp = decide(ctx.book, ctx.ml_score, shape_conf, rules)
    ctx.emit({"type": "decision", **disp.as_dict(), "stop_reason": stop_reason})
    ctx.emit({"type": "report_start"})
    report = write_report(ctx, disp, use_llm=use_llm_report and policy_name == "claude")
    hyps = crime_hypotheses(ctx, rules)

    case = {
        "case_id": ctx.case_id, "primary": ctx.primary, "accounts": ctx.accounts, "alert_tx": ctx.tx_ids,
        "window": [ctx.t0, ctx.t1], "ml_score": ctx.ml_score, "detector_hits": ctx.detector_hits,
        "shape": ctx.shape, "crime_hypotheses": hyps, "disposition": disp.as_dict(), "report": report,
        "evidence": [e.as_dict() for e in ctx.book.items], "steps": ctx.calls, "queries": ctx.queries,
        "graph": {"nodes": list(ctx.nodes.values()), "edges": list(ctx.edges.values())},
        "stats": {"steps": len(ctx.calls), "queries": len(ctx.queries),
                  "query_ms": round(sum(q["ms"] for q in ctx.queries), 1),
                  "seconds": round(time.perf_counter() - started, 2), "stop_reason": stop_reason,
                  "policy": policy_ran(policy.name, ctx.calls),
                  "llm_calls": len(getattr(policy, "meta", [])) + (1 if report.get("llm") else 0)},
    }
    if save_memory:
        try:
            rw(Q.SAVE_CASE, {
                "case_id": ctx.case_id, "disposition": disp.action, "typology": (ctx.shape or {}).get("typology"),
                "crime": ", ".join(h["crime"] for h in hyps), "score": disp.risk, "confidence": disp.confidence,
                "created_ts": ctx.t1, "summary": report.get("headline", ""), "tx_ids": ctx.tx_ids[:50],
                "expires_ts": ctx.t1 + rules["disposition"]["close_expiry_days"] * DAY_MS,
                "accounts": ctx.accounts[:20],
            })
            case["stats"]["memory_saved"] = True
        except Exception as e:
            case["stats"]["memory_saved"] = f"failed: {e}"
    ctx.emit({"type": "report", "case": case})
    return case


def new_context(case: dict, rules: dict, emit: Callable[[dict], None]) -> CaseContext:
    return CaseContext(
        case_id=case["case_id"], primary=case["primary"], accounts=case["accounts"], tx_ids=case["tx_ids"],
        t0=case["t0"], t1=case["t1"], rules=rules, ml_score=case["ml_score"],
        detector_hits=case.get("detector_hits", []), shape=case.get("shape"),
        alert_edges=case.get("edges", []), emit=emit,
    )
