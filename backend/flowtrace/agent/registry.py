"""One tool registry for the in-app agent, the API's "act" step and the MCP server.

  case tools     the investigation tools (agent.tools.TOOLS): run inside one case's context and add evidence.
  read tools     get_case, list_cases, plan_holds: read-only (GRAPH.RO_QUERY and saved files).
  propose_action the only write: creates PROPOSED actions. Nothing here can approve; only a person can,
                 through the API the UI calls.

propose_action checks every caller the same way: a known type; a HOLD only after an investigation that
recommends escalation (actions.propose_for), only on an account of the case's money trail that is not a hub,
only for at least actions.min_hold_usd of case money, and at most actions.max_holds_per_case open holds per
case (checked inside the write); a RELEASE only of an approved hold. Each proposal records where it came
from: 'agent' (the app) or 'mcp' (an MCP client).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, Field

from flowtrace import store
from flowtrace.actions import planner, service
from flowtrace.agent.context import CaseContext
from flowtrace.agent.tools import TOOLS, ToolError, run_tool
from flowtrace.settings import cfg


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable
    args: type[BaseModel]
    doc: str
    scope: Literal["case", "global"]
    writes: bool = False


class CaseArgs(BaseModel):
    case_id: str = Field(description="case id, e.g. B1A2B3C-C001")


class ListArgs(BaseModel):
    batch_id: str | None = Field(None, description="only cases of this upload")


class ProposeArgs(BaseModel):
    case_id: str
    type: Literal["HOLD", "RELEASE", "ESCALATE", "FILE_STR", "CLOSE"]
    account: str | None = Field(None, description="the account to hold or release (HOLD and RELEASE only)")
    reason: str = Field(min_length=5, description="why, in one plain sentence an analyst can check")
    evidence_ids: list[str] = Field(default_factory=list, description="evidence ids from the case (E1, E2, ...)")


def _case(case_id: str) -> dict:
    case = store.load_case(case_id)
    if case is None:
        raise ToolError(f"no such case {case_id}")
    return case


def get_case(a: CaseArgs) -> dict:
    """The case: its accounts and alert, the investigation's decision and evidence, and its actions."""
    case = _case(a.case_id)
    r = store.load_result(a.case_id)
    return {
        "case": {k: case.get(k) for k in ("case_id", "primary", "accounts", "t0", "t1", "ml_score", "case_score",
                                          "shape", "detector_hits", "amount", "tx_ids")},
        "result": None if r is None else {
            "disposition": r["disposition"], "headline": r["report"].get("headline"),
            "evidence": [{k: e.get(k) for k in ("id", "kind", "code", "claim", "accounts")} for e in r.get("evidence", [])],
        },
        "actions": service.list_actions(case_id=a.case_id, expire=False),
    }


def list_cases(a: ListArgs) -> list[dict]:
    """Saved cases, newest upload first and ranked, with the investigation's decision if there is one."""
    return store.list_cases(a.batch_id)


def plan_holds(a: CaseArgs) -> dict:
    """Which accounts to hold so the case money still sitting in them cannot leave. A hold is a lien on the
    case money traced into an account, never its whole balance, and never on a hub. frozen_now is what the holds
    freeze today; on_routes is an assumption (money that would have to pass a held account if the mules keep
    their routes). Read-only: it only computes the plan."""
    return planner.plan_holds(_case(a.case_id), cfg(), store.load_result(a.case_id))


def propose_action(a: ProposeArgs, origin: str = "agent") -> dict:
    """Propose an action for a person to approve or reject (HOLD, RELEASE, ESCALATE, FILE_STR, CLOSE).
    It is only a proposal: it changes nothing until a person approves it."""
    return _propose(a, origin=origin)


def _propose(a: ProposeArgs, plan: dict | None = None, origin: str = "agent") -> dict:
    """plan: the case's hold plan when the caller already has it (the act step), so it is computed once."""
    case = _case(a.case_id)
    if a.type in ("HOLD", "RELEASE") and not a.account:
        raise ToolError(f"a {a.type} needs an account")
    current = service.list_actions(case_id=a.case_id, expire=False) if a.type in ("HOLD", "RELEASE") else []
    if a.type == "RELEASE" and not any(x["type"] == "HOLD" and x["status"] == "APPROVED" and x.get("account") == a.account
                                       for x in current):
        raise ToolError(f"there is no approved hold on {a.account} in this case to release")
    numbers: dict = {}
    if a.type == "HOLD":
        result = store.load_result(a.case_id)
        disposition = (result or {}).get("disposition", {}).get("action")
        if disposition not in cfg()["actions"]["propose_for"]:
            raise ToolError("a hold needs an investigation that recommends escalation; "
                            + (f"this one recommends {disposition.lower()}" if disposition else "this case is not investigated yet"))
        plan = plan or planner.plan_holds(case, cfg(), result)
        node = next((n for n in plan["network"]["nodes"] if n["id"] == a.account), None)
        if a.account in plan["skipped_hubs"] or (node and node["hub"]):
            raise ToolError(f"{a.account} is a hub (a merchant or processor): FlowTrace never holds hubs")
        if node is None:
            raise ToolError(f"{a.account} is not on this case's money trail")
        floor = cfg()["actions"]["min_hold_usd"]
        if node["at_rest"] < floor:
            raise ToolError(f"only {node['at_rest']:,.0f} of case money is in {a.account}, under the minimum hold of {floor:,.0f}")
        hold = next((h for h in plan["holds"] if h["account"] == a.account), None)
        numbers = {"hold_amount": node["at_rest"], "on_routes": hold["on_routes"] if hold else 0.0,
                   "currency": plan["currency"], "rate": plan["rate"],
                   "tx_ids": hold["tx_ids"] if hold else []}
        if not a.evidence_ids and hold:
            a.evidence_ids = hold["evidence_ids"]
    try:
        action, created = service.propose(a.case_id, a.type, account=a.account, why=a.reason,
                                          evidence_ids=a.evidence_ids, proposed_by=origin, **numbers)
    except service.ActionError as e:  # e.g. the case is at its hold cap (checked inside the write)
        raise ToolError(str(e)) from e
    return {"action": action, "created": created}


REGISTRY: dict[str, ToolSpec] = {
    **{n: ToolSpec(n, fn, model, doc, "case") for n, (fn, model, doc) in TOOLS.items()},
    "get_case": ToolSpec("get_case", get_case, CaseArgs, get_case.__doc__, "global"),
    "list_cases": ToolSpec("list_cases", list_cases, ListArgs, list_cases.__doc__, "global"),
    "plan_holds": ToolSpec("plan_holds", plan_holds, CaseArgs, plan_holds.__doc__, "global"),
    "propose_action": ToolSpec("propose_action", propose_action, ProposeArgs, propose_action.__doc__, "global", writes=True),
}


def investigation_tools() -> dict[str, ToolSpec]:
    return {n: s for n, s in REGISTRY.items() if s.scope == "case"}


def call(name: str, args: dict, ctx: CaseContext | None = None, origin: str = "agent"):
    """Validate and run one registry tool. Case tools need the case's investigation context. origin says who
    is calling ('agent' = the app, 'mcp' = an MCP client) and is recorded on proposals."""
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(f"unknown tool {name}")
    if spec.scope == "case":
        if ctx is None:
            raise ToolError(f"{name} runs inside a case investigation")
        return run_tool(ctx, name, args)
    if spec.writes:
        return spec.fn(spec.args.model_validate(args), origin=origin)
    return spec.fn(spec.args.model_validate(args))


def act(case_id: str) -> dict:
    """The agent's act step after an investigation: plan the holds, then propose them and the case-level
    action. Proposals only; a person decides each one."""
    plan = call("plan_holds", {"case_id": case_id})
    store.save_plan(case_id, plan)
    actions = []
    for h in plan["holds"]:
        args = ProposeArgs(case_id=case_id, type="HOLD", account=h["account"], reason=h["why"], evidence_ids=h["evidence_ids"])
        actions.append(_propose(args, plan)["action"])
    if plan["case_action"] and plan["disposition"]:
        actions.append(call("propose_action", {"case_id": case_id, "type": plan["case_action"]["type"],
                                               "reason": plan["case_action"]["why"]})["action"])
    return {"plan": plan, "actions": actions, "auto_held": auto_hold(case_id, actions)}


AUTO_BY = "FlowTrace agent (auto-hold policy)"


def auto_hold(case_id: str, actions: list[dict]) -> list[str]:
    """Critical cases: apply the proposed holds at once instead of waiting for an analyst, so the money cannot
    leave while the case sits in the queue. Only after ESCALATE at actions.auto_hold.min_confidence or above.
    The hold is the same time-limited lien on the case money (it lapses after hold_expiry_business_days), it
    is audited like any decision, and an analyst who closes the case releases it. Filing an STR or escalating
    is never automatic: that stays a person's decision."""
    rule = cfg()["actions"].get("auto_hold") or {}
    if not rule.get("enabled"):
        return []
    d = (store.load_result(case_id) or {}).get("disposition", {})
    conf = d.get("confidence") or 0.0
    if d.get("action") != "ESCALATE" or conf < rule["min_confidence"]:
        return []
    reason = (f"Automatic hold: escalated at {round(conf * 100)}% confidence (policy: {round(rule['min_confidence'] * 100)}% "
              f"or more). An analyst confirms by filing, or releases it by closing the case.")
    held = []
    for a in actions:
        if a.get("type") == "HOLD" and a.get("status") == "PROPOSED":
            try:
                service.decide(a["id"], True, reason, by=AUTO_BY, key=f"auto-{a['id']}", auth="auto-hold policy")
                held.append(a["id"])
            except service.ActionError:
                pass  # decided by a person in the meantime
    return held
