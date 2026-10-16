"""FlowTrace MCP server: the agent's tools for any MCP client (Claude Desktop, an IDE, another agent).

Every tool comes from flowtrace.agent.registry, the same implementation the in-app agent uses.
  read-only   the investigation tools, get_case, list_cases, plan_holds. Cypher runs through GRAPH.RO_QUERY,
              so FalkorDB itself rejects any write.
  one write   propose_action: creates a PROPOSED action. There is no approve tool: only a person approves,
              in the FlowTrace UI.

Investigation tools keep one context per case for the session, so an account found by trace_funds can be
profiled in the next call (the same rule as in the app: only accounts already seen in the case).

Run (from backend/):  python -m flowtrace.mcp.server              stdio, for Claude Desktop and most clients
                      python -m flowtrace.mcp.server --http       streamable HTTP on 127.0.0.1:8765/mcp
"""
# No `from __future__ import annotations`: the MCP SDK reads these signatures to build the tool schemas.
import argparse
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, ValidationError

from flowtrace import store
from flowtrace.agent import registry
from flowtrace.agent.context import CaseContext
from flowtrace.agent.tools import ToolError
from flowtrace.settings import cfg

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = """FlowTrace investigates anti-money-laundering cases on a FalkorDB transaction graph.
Start with list_cases, then get_case. Investigation tools take a case_id and only accounts already seen in
that case. plan_holds says which accounts to hold to stop the case money leaving. propose_action only
proposes: a person approves or rejects every action in the FlowTrace app, with a reason."""


def _run(name: str, args: dict, ctx: CaseContext | None = None):
    try:
        return registry.call(name, args, ctx)
    except (ToolError, ValidationError) as e:
        raise MCPToolError(str(e)) from e


def build_server() -> MCPServer:
    server = MCPServer("flowtrace", instructions=INSTRUCTIONS)
    sessions: dict[str, tuple[dict, CaseContext]] = {}

    def session(case_id: str) -> CaseContext:
        case = store.load_case(case_id)
        if case is None:
            raise MCPToolError(f"no such case {case_id}")
        if case_id not in sessions or sessions[case_id][0] != case:  # new, or renumbered by a re-upload
            from flowtrace.agent.investigate import new_context  # imports the policies, which import the registry
            sessions[case_id] = (case, new_context(case, cfg(), emit=lambda e: None))
        return sessions[case_id][1]

    def case_tool(name: str, model: type[BaseModel]):
        def tool(case_id: str, args: model) -> dict[str, Any]:
            ctx = session(case_id)
            before = len(ctx.book.items)
            ctx.step += 1
            summary = _run(name, args.model_dump(), ctx)
            return {"summary": summary, "evidence": [e.as_dict() for e in ctx.book.items[before:]]}
        return tool

    for name, spec in registry.investigation_tools().items():
        server.add_tool(case_tool(name, spec.args), name=name, description=spec.doc.strip(), annotations=READ,
                        structured_output=True)

    def get_case(case_id: str) -> dict[str, Any]:
        return _run("get_case", {"case_id": case_id})

    def list_cases(batch_id: str | None = None) -> list[dict[str, Any]]:
        return _run("list_cases", {"batch_id": batch_id})

    def plan_holds(case_id: str) -> dict[str, Any]:
        return _run("plan_holds", {"case_id": case_id})

    def propose_action(case_id: str, type: Literal["HOLD", "RELEASE", "ESCALATE", "FILE_STR", "CLOSE"], reason: str,
                       account: str | None = None, evidence_ids: list[str] | None = None) -> dict[str, Any]:
        return _run("propose_action", {"case_id": case_id, "type": type, "account": account, "reason": reason,
                                       "evidence_ids": evidence_ids or []})

    for fn in (get_case, list_cases, plan_holds, propose_action):
        spec = registry.REGISTRY[fn.__name__]
        server.add_tool(fn, name=spec.name, description=spec.doc.strip(), annotations=WRITE if spec.writes else READ,
                        structured_output=True)
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    server = build_server()
    if a.http:
        server.run(transport="streamable-http", host="127.0.0.1", port=a.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
