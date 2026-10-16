"""One tool registry for the in-app agent, the API's "act" step and the MCP server.

  case tools     the investigation tools (agent.tools.TOOLS): run inside one case's context and add evidence.
  read tools     get_case, list_cases, plan_holds: read-only (GRAPH.RO_QUERY and saved files).
  propose_action the only write: creates PROPOSED actions. Nothing here can approve; only a person can,
                 through the API the UI calls.

propose_action checks every caller the same way: a known type, a HOLD only on an account of the case's
money trail that is not a hub, and at most actions.max_holds_per_case open holds per case.
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
    """Which accounts to hold so the case money still in the trail cannot leave: fewest holds (a minimum cut
    of the money trail), never a hub. Read-only: it only computes the plan."""
    return planner.plan_holds(_case(a.case_id), cfg(), store.load_result(a.case_id))


def propose_action(a: ProposeArgs) -> dict:
    """Propose an action for a person to approve or reject (HOLD, RELEASE, ESCALATE, FILE_STR, CLOSE).
    It is only a proposal: it changes nothing until a person approves it."""
    case = _case(a.case_id)
    if a.type in ("HOLD", "RELEASE") and not a.account:
        raise ToolError(f"a {a.type} needs an account")
    numbers: dict = {}
    if a.type == "HOLD":
        plan = planner.plan_holds(case, cfg(), store.load_result(a.case_id))
        node = next((n for n in plan["network"]["nodes"] if n["id"] == a.account), None)
        if a.account in plan["skipped_hubs"] or (node and node["hub"]):
            raise ToolError(f"{a.account} is a hub (a merchant or processor): FlowTrace never holds hubs")
        if node is None:
            raise ToolError(f"{a.account} is not on this case's money trail")
        open_holds = {x["account"] for x in service.list_actions(case_id=a.case_id, expire=False)
                      if x["type"] == "HOLD" and x["status"] in ("PROPOSED", "APPROVED")}
        cap = cfg()["actions"]["max_holds_per_case"]
        if a.account not in open_holds and len(open_holds) >= cap:
            raise ToolError(f"this case already has {len(open_holds)} open holds (the most is {cap})")
        hold = next((h for h in plan["holds"] if h["account"] == a.account), None)
        numbers = {"amount_blocked": hold["amount_blocked"] if hold else node["at_rest"],
                   "frozen_now": hold["frozen_now"] if hold else node["at_rest"],
                   "currency": plan["currency"], "rate": plan["rate"],
                   "tx_ids": hold["tx_ids"] if hold else []}
        if not a.evidence_ids and hold:
            a.evidence_ids = hold["evidence_ids"]
    action, created = service.propose(a.case_id, a.type, account=a.account, why=a.reason,
                                      evidence_ids=a.evidence_ids, **numbers)
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


def call(name: str, args: dict, ctx: CaseContext | None = None):
    """Validate and run one registry tool. Case tools need the case's investigation context."""
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(f"unknown tool {name}")
    if spec.scope == "case":
        if ctx is None:
            raise ToolError(f"{name} runs inside a case investigation")
        return run_tool(ctx, name, args)
    return spec.fn(spec.args.model_validate(args))


def act(case_id: str) -> dict:
    """The agent's act step after an investigation: plan the holds, then propose them and the case-level
    action. Proposals only; a person decides each one."""
    plan = call("plan_holds", {"case_id": case_id})
    store.save_plan(case_id, plan)
    actions = []
    for h in plan["holds"]:
        actions.append(call("propose_action", {"case_id": case_id, "type": "HOLD", "account": h["account"],
                                               "reason": h["why"], "evidence_ids": h["evidence_ids"]})["action"])
    if plan["case_action"] and plan["disposition"]:
        actions.append(call("propose_action", {"case_id": case_id, "type": plan["case_action"]["type"],
                                               "reason": plan["case_action"]["why"]})["action"])
    return {"plan": plan, "actions": actions}
