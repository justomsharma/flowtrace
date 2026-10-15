"""Policies that choose the next investigation step.

  LLMPolicy        Claude picks the next approved tool from the case state (the adaptive agent).
  HeuristicPolicy  rule-based adaptive policy, used when Claude is unavailable.
  FixedPolicy      the baseline: the same tools in the same order for every case.
"""
from __future__ import annotations

import json

from flowtrace.agent.context import CaseContext
from flowtrace.agent.llm import structured
from flowtrace.agent.tools import TOOLS

Step = dict  # {"action", "args", "reason", "hypothesis", "policy"}


def _called(ctx: CaseContext, tool: str, **match) -> bool:
    return any(c["tool"] == tool and all(c["args"].get(k) == v for k, v in match.items()) for c in ctx.calls)


class FixedPolicy:
    name = "fixed"

    def next(self, ctx: CaseContext) -> Step | None:
        p = ctx.primary
        plan = [
            ("account_profile", {"account": p}),
            ("counterparty_history", {"account": p, "direction": "out"}),
            ("trace_funds", {"account": p, "direction": "forward", "max_hops": 3}),
            ("counterparty_history", {"account": p, "direction": "in"}),
            ("find_cycles", {"account": p}),
            ("case_memory", {"account": p}),
        ]
        for tool, args in plan:
            if not _called(ctx, tool, **args):
                return {"action": tool, "args": args, "reason": "fixed investigation order", "hypothesis": "both"}
        return None


class HeuristicPolicy:
    """Adaptive without an LLM: each finding decides what is checked next."""
    name = "heuristic"

    def next(self, ctx: CaseContext) -> Step | None:
        p, codes = ctx.primary, ctx.book.codes()

        if not _called(ctx, "account_profile", account=p):
            return self._s("account_profile", {"account": p}, "start with who the account is and how it behaved", "both")
        prof = next(c["summary"] for c in ctx.calls if c["tool"] == "account_profile")
        fans_out = prof.get("out_cp", 0) >= prof.get("in_cp", 0)
        main_dir = "out" if fans_out else "in"
        if not _called(ctx, "counterparty_history", account=p, direction=main_dir):
            return self._s("counterparty_history", {"account": p, "direction": main_dir},
                           f"the account mostly {'pays' if fans_out else 'receives from'} many parties: are they new or long-standing?",
                           "both")
        if ({"ESTABLISHED_COUNTERPARTIES", "RECURRING_FLOWS", "PAYS_HIGH_VOLUME_RECEIVERS"} & codes
                and not _called(ctx, "recurring_flows", account=p)):
            return self._s("recurring_flows", {"account": p},
                           "relationships look established: check whether this is a regular salary / obligation pattern",
                           "legitimate")
        if fans_out and not _called(ctx, "trace_funds", account=p, direction="forward"):
            return self._s("trace_funds", {"account": p, "direction": "forward", "max_hops": 3},
                           "follow where the money went: does it stop or keep moving quickly?", "laundering")
        if not fans_out and not _called(ctx, "trace_funds", account=p, direction="backward"):
            return self._s("trace_funds", {"account": p, "direction": "backward", "max_hops": 2},
                           "follow where the money came from", "laundering")
        if "NEW_COUNTERPARTIES" in codes and len(ctx.accounts) >= 2 and not _called(ctx, "shared_counterparties"):
            others = [a for a in ctx.accounts if a != p][:9]
            return self._s("shared_counterparties", {"accounts": [p] + others, "direction": "out"},
                           "new counterparties: check whether case accounts converge on the same receivers", "laundering")
        if (ctx.shape or {}).get("typology") in ("CYCLE", "SCATTER-GATHER", "STACK") or "ROUND_TRIP" in codes:
            if not _called(ctx, "find_cycles", account=p):
                return self._s("find_cycles", {"account": p}, "the shape suggests money may loop back", "laundering")
        if not _called(ctx, "owner_network", account=p):
            return self._s("owner_network", {"account": p}, "could this be transfers between the owner's own accounts?", "legitimate")
        if not _called(ctx, "case_memory", account=p):
            return self._s("case_memory", {"account": p}, "check prior cases and proximity to escalated accounts", "both")
        return None

    def _s(self, action, args, reason, hyp) -> Step:
        return {"action": action, "args": args, "reason": reason, "hypothesis": hyp}


SYSTEM = """You are the investigation controller of FlowTrace, working like a senior AML analyst with
10+ years of transaction-monitoring experience. You decide the single next investigation step.

Rules:
- Choose ONE approved tool, or "finish" when more queries are unlikely to change the decision.
- Keep two hypotheses open: a legitimate explanation (salary, EMI/rent, merchant payments, own-account
  transfers) and a laundering typology. Prefer the step that best separates them given what is known.
- Never repeat a call with the same arguments. Only use accounts listed as known.
- Facts come only from tool results; you do not compute amounts yourself.
- Return JSON matching the schema. Set unused argument fields to null."""


def _schema() -> dict:
    nullable = lambda t: {"anyOf": [t, {"type": "null"}]}  # noqa: E731
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(TOOLS) + ["finish"]},
            "account": nullable({"type": "string"}),
            "accounts": nullable({"type": "array", "items": {"type": "string"}}),
            "direction": nullable({"type": "string", "enum": ["out", "in", "forward", "backward"]}),
            "max_hops": nullable({"type": "integer"}),
            "hypothesis": {"type": "string", "enum": ["legitimate", "laundering", "both"]},
            "reason": {"type": "string"},
        },
        "required": ["action", "account", "accounts", "direction", "max_hops", "hypothesis", "reason"],
        "additionalProperties": False,
    }


class LLMPolicy:
    name = "claude"

    def __init__(self) -> None:
        self.fallback = HeuristicPolicy()
        self.meta: list[dict] = []

    def next(self, ctx: CaseContext) -> Step | None:
        tools = "\n".join(f"- {n}: {doc.strip()}" for n, (_, _, doc) in TOOLS.items())
        state = {
            "case": {"primary_account": ctx.primary, "case_accounts": ctx.accounts[:15],
                     "model_score": round(ctx.ml_score, 3), "detector_hits": ctx.detector_hits[:8], "shape": ctx.shape},
            "known_accounts": sorted(set(ctx.accounts) | set(ctx.nodes))[:60],
            "calls_so_far": [{"tool": c["tool"], "args": c["args"], "result": c["summary"]} for c in ctx.calls],
            "evidence": [{"id": e.id, "kind": e.kind, "code": e.code, "claim": e.claim} for e in ctx.book.items],
            "steps_left": ctx.rules["agent"]["max_steps"] - len(ctx.calls),
        }
        user = f"Approved tools:\n{tools}\n\nCase state:\n{json.dumps(state, default=str)}\n\nChoose the next step."
        out, meta = structured(SYSTEM, user, _schema(), effort="medium", max_tokens=4000)
        self.meta.append(meta)
        if out is None:
            step = self.fallback.next(ctx)
            if step:
                step["reason"] += f" (rule-based fallback: {meta.get('error')})"
                step["policy"] = "heuristic"
            return step
        if out["action"] == "finish":
            return None
        args = {k: out[k] for k in ("account", "accounts", "direction", "max_hops") if out.get(k) is not None}
        return {"action": out["action"], "args": args, "reason": out["reason"], "hypothesis": out["hypothesis"],
                "policy": "claude"}
