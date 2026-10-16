"""The MCP server lists the registry's tools and answers a call (in-process client, no subprocess)."""
import asyncio

from mcp import Client

from flowtrace.actions import service
from flowtrace.agent import registry
from flowtrace.mcp.server import build_server
from tests.test_planner import CASE
from tests.test_registry import runs  # noqa: F401  (fixture)


async def _session(fn):
    async with Client(build_server()) as c:
        return await fn(c)


def test_mcp_lists_the_tools_and_marks_only_propose_action_as_a_write():
    tools = asyncio.run(_session(lambda c: c.list_tools())).tools
    names = {t.name for t in tools}
    assert names == set(registry.REGISTRY)
    writes = [t.name for t in tools if not t.annotations.read_only_hint]
    assert writes == ["propose_action"]


def test_mcp_has_no_way_to_approve():
    tools = asyncio.run(_session(lambda c: c.list_tools())).tools
    assert not any("approve" in t.name or "reject" in t.name or "decide" in t.name for t in tools)


def test_mcp_calls_get_case(runs, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service, "list_actions", lambda case_id=None, status=None, expire=True: [])
    res = asyncio.run(_session(lambda c: c.call_tool("get_case", {"case_id": CASE["case_id"]})))
    assert not res.is_error
    assert res.structured_content["case"]["case_id"] == CASE["case_id"]
    assert res.structured_content["result"]["disposition"]["action"] == "ESCALATE"


def test_mcp_investigation_tool_runs_on_the_case(runs):  # noqa: F811
    res = asyncio.run(_session(lambda c: c.call_tool("account_profile", {"case_id": CASE["case_id"], "args": {"account": "X"}})))
    assert not res.is_error, res.content
    assert res.structured_content["summary"]["out_cnt"] == 3
    assert any(e["code"] == "PROFILE" for e in res.structured_content["evidence"])


def test_mcp_refusal_comes_back_as_a_tool_error(runs, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service, "list_actions", lambda case_id=None, status=None, expire=True: [])
    res = asyncio.run(_session(lambda c: c.call_tool("propose_action", {
        "case_id": CASE["case_id"], "type": "HOLD", "account": "HUB", "reason": "Hold the merchant"})))
    assert res.is_error and "hub" in res.content[0].text.lower()


def test_mcp_proposals_are_recorded_as_mcp(runs, monkeypatch):  # noqa: F811
    made = []
    monkeypatch.setattr(service, "list_actions", lambda case_id=None, status=None, expire=True: [])
    monkeypatch.setattr(service, "propose", lambda case_id, type, **kw: (made.append(kw) or {"id": "A1", **kw}, True))
    res = asyncio.run(_session(lambda c: c.call_tool("propose_action", {
        "case_id": CASE["case_id"], "type": "HOLD", "account": "M1", "reason": "Case money is resting here"})))
    assert not res.is_error and made[0]["proposed_by"] == "mcp"


def test_the_mcp_process_never_holds_the_approver_token(monkeypatch):
    monkeypatch.setenv("APPROVER_TOKEN", "s3cret")
    build_server()
    import os
    assert "APPROVER_TOKEN" not in os.environ
