"""Security review fixes for the public demo: who a visitor is, wrong-token lockout, anonymous investigations, the
MCP endpoint's guard, request size limits, MCP sessions and propose_action's limits."""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from flowtrace.agent.registry import ProposeArgs
from flowtrace.api import guard, jobs
from flowtrace.api import main as M
from flowtrace.mcp.server import CaseSessions
from flowtrace.settings import cfg as real_cfg

client = TestClient(M.app)
TOKEN = {"X-Approver-Token": "s3cret"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(M, "approver_token", lambda: "s3cret")
    guard.LIMITS.clear()
    guard.FAILURES.clear()
    yield
    guard.LIMITS.clear()
    guard.FAILURES.clear()


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")


class Req:
    def __init__(self, headers=None, host="10.0.0.1"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": host})()


# --- who is asking ---------------------------------------------------------------------------------

def test_demo_reads_fly_client_ip_and_never_x_forwarded_for(demo):
    assert guard.client_ip(Req({"fly-client-ip": "1.2.3.4", "x-forwarded-for": "9.9.9.9"})) == "1.2.3.4"
    assert guard.client_ip(Req({"x-forwarded-for": "9.9.9.9"}, host="10.0.0.7")) == "10.0.0.7"


def test_outside_demo_mode_only_the_connection_counts(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert guard.client_ip(Req({"fly-client-ip": "1.2.3.4", "x-forwarded-for": "9.9.9.9"}, host="10.0.0.7")) == "10.0.0.7"


def test_an_ipv6_visitor_counts_by_its_64(demo):
    a = guard.client_ip(Req({"fly-client-ip": "2001:db8:1:2:aaaa::1"}))
    b = guard.client_ip(Req({"fly-client-ip": "2001:db8:1:2:bbbb:cccc:dddd:9"}))
    c = guard.client_ip(Req({"fly-client-ip": "2001:db8:1:3::1"}))
    assert a == b == "2001:db8:1:2::/64" and c != a
    assert guard.client_ip(Req({"fly-client-ip": "::ffff:1.2.3.4"})) == "1.2.3.4"


def test_a_forged_x_forwarded_for_does_not_reset_the_limit(demo, monkeypatch):
    monkeypatch.setattr(guard, "cfg", lambda: {"demo": {"investigations_per_ip": {"count": 1, "minutes": 60}}})
    monkeypatch.setattr(M, "CASES", {"BX-C001": {"case_id": "BX-C001"}})
    monkeypatch.setattr(M.RUNNER, "start", lambda *a, **k: jobs.create("case"))
    codes = [client.post("/api/cases/BX-C001/investigate", headers={"X-Forwarded-For": f"9.9.9.{i}"}).status_code
             for i in range(3)]
    assert codes == [200, 429, 429]


def test_investigations_are_limited_to_6_an_hour():
    assert real_cfg()["demo"]["investigations_per_ip"] == {"count": 6, "minutes": 60}


# --- approver token --------------------------------------------------------------------------------

def test_wrong_tokens_lock_the_visitor_out_even_for_the_right_one(monkeypatch):
    monkeypatch.setattr(guard, "cfg", lambda: {"demo": {"approver_failures_per_ip": {"count": 3, "minutes": 15}}})
    codes = [client.post("/api/auth/approver", headers={"X-Approver-Token": f"guess{i}"}).status_code for i in range(3)]
    assert codes == [401, 401, 401]
    assert client.post("/api/auth/approver", headers=TOKEN).status_code == 429


def test_a_missing_token_is_not_counted_as_a_guess(monkeypatch):
    monkeypatch.setattr(guard, "cfg", lambda: {"demo": {"approver_failures_per_ip": {"count": 2, "minutes": 15}}})
    for _ in range(5):
        assert client.post("/api/auth/approver").status_code == 401
    assert client.post("/api/auth/approver", headers=TOKEN).status_code == 200


def test_an_anonymous_approve_is_refused(monkeypatch):
    from flowtrace.actions import service
    monkeypatch.setattr(service, "decide", lambda *a, **k: pytest.fail("never reached without the token"))
    r = client.post("/api/actions/A-1/approve", json={"reason": "looks right", "by": "x"})
    assert r.status_code == 401


# --- investigations on the public demo -----------------------------------------------------------------

@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(M, "CASES", {"BX-C001": {"case_id": "BX-C001"}})
    monkeypatch.setattr(M, "_investigation", lambda cid, policy: policy)
    monkeypatch.setattr(M.RUNNER, "start", lambda cid, fn, on_result, queued=False:
                        calls.append((fn, on_result.keywords["started_by_person"])) or jobs.create("case"))
    return calls


def test_an_anonymous_visitor_gets_the_rules_and_no_auto_hold(demo, started):
    assert client.post("/api/cases/BX-C001/investigate?policy=llm").status_code == 200
    assert started == [("heuristic", False)]


def test_the_signed_in_approver_gets_the_llm_and_the_auto_hold(demo, started):
    assert client.post("/api/cases/BX-C001/investigate?policy=llm", headers=TOKEN).status_code == 200
    assert started == [("llm", True)]


def test_a_wrong_token_on_investigate_is_refused(demo, started):
    assert client.post("/api/cases/BX-C001/investigate", headers={"X-Approver-Token": "nope"}).status_code == 401
    assert started == []


def test_outside_demo_mode_an_investigation_is_a_persons(started, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    client.post("/api/cases/BX-C001/investigate?policy=llm")
    assert started == [("llm", True)]


def test_planning_is_rate_limited(monkeypatch):
    monkeypatch.setattr(guard, "cfg", lambda: {"demo": {"plans_per_ip": {"count": 2, "minutes": 10}}})
    codes = [client.post("/api/cases/NOPE-C001/plan").status_code for _ in range(3)]
    assert codes == [404, 404, 429]


# --- request size ------------------------------------------------------------------------------------

def test_a_body_over_the_limit_is_refused_before_reading(demo):
    r = client.post("/api/batches", content=b"x" * (guard.MAX_UPLOAD_DEMO + 1),
                    headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413
    r = client.post("/api/actions/A-1/approve", content=b"{" + b" " * guard.MAX_BODY + b"}",
                    headers={"Content-Type": "application/json", **TOKEN})
    assert r.status_code == 413


# --- the MCP endpoint ------------------------------------------------------------------------------------

def mcp_app(seen):
    async def inner(scope, receive, send):
        msg = await receive()
        seen.append(msg["body"])
        await JSONResponse({"ok": True})(scope, receive, send)
    return TestClient(Starlette(routes=[Route("/mcp", endpoint=guard.McpGuard(inner, lambda t: t == "s3cret"),
                                              methods=None)]))


def call(tool, i=1):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": tool, "arguments": {}}}


def test_anonymous_propose_action_is_refused_on_the_demo(demo):
    seen = []
    c = mcp_app(seen)
    r = c.post("/mcp", json=call("propose_action"))
    assert r.status_code == 401 and r.json()["error"]["code"] == -32001 and seen == []
    r = c.post("/mcp", json=[call("list_cases"), call("propose_action", 2)])   # a batch cannot hide it
    assert r.status_code == 401 and seen == []


def test_propose_action_with_the_token_and_read_tools_without_it_pass(demo):
    seen = []
    c = mcp_app(seen)
    assert c.post("/mcp", json=call("propose_action"), headers=TOKEN).status_code == 200
    assert c.post("/mcp", json=call("list_cases")).status_code == 200
    assert json.loads(seen[0])["params"]["name"] == "propose_action" and len(seen) == 2


def test_mcp_requests_are_rate_limited_read_tools_too(monkeypatch):
    monkeypatch.setattr(guard, "cfg", lambda: {"demo": {"mcp_per_ip": {"count": 2, "minutes": 10}}})
    c = mcp_app([])
    assert [c.post("/mcp", json=call("list_cases")).status_code for _ in range(3)] == [200, 200, 429]


def test_an_oversized_mcp_message_is_refused():
    c = mcp_app([])
    big = call("list_cases") | {"pad": "x" * guard.MAX_BODY}
    assert c.post("/mcp", content=json.dumps(big).encode()).status_code == 413


def test_wrong_mcp_tokens_count_toward_the_lockout(demo, monkeypatch):
    monkeypatch.setattr(guard, "cfg", lambda: {"demo": {"approver_failures_per_ip": {"count": 2, "minutes": 15}}})
    c = mcp_app([])
    hdr = {"X-Approver-Token": "guess"}
    assert [c.post("/mcp", json=call("propose_action"), headers=hdr).status_code for _ in range(3)] == [401, 401, 429]


def test_propose_action_reason_is_at_most_500_characters():
    ProposeArgs(case_id="B-C001", type="ESCALATE", reason="x" * 500)
    with pytest.raises(ValidationError):
        ProposeArgs(case_id="B-C001", type="ESCALATE", reason="x" * 501)


# --- MCP sessions --------------------------------------------------------------------------------------

class Ctx:
    def __init__(self):
        self.step = 0


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_sessions_are_capped_least_recently_used_first():
    s = CaseSessions(lambda case: Ctx(), cap=2)
    a = s.get("A", {"id": "A"})
    s.get("B", {"id": "B"})
    assert s.get("A", {"id": "A"}) is a        # A used again: B is now the oldest
    s.get("C", {"id": "C"})
    assert list(s.items) == ["A", "C"]


def test_sessions_expire_and_restart():
    clock = Clock()
    s = CaseSessions(lambda case: Ctx(), ttl_s=60, max_steps=3, clock=clock)
    a = s.get("A", {"id": "A"})
    clock.t = 61
    assert s.get("A", {"id": "A"}) is not a    # unused too long
    b = s.get("A", {"id": "A"})
    b.step = 3
    assert s.get("A", {"id": "A"}) is not b    # too many steps
    c = s.get("A", {"id": "A"})
    assert s.get("A", {"id": "A2"}) is not c   # renumbered by a re-upload


def test_the_signed_in_approver_counts_only_toward_the_total(demo, started, monkeypatch):
    monkeypatch.setattr(guard, "cfg", lambda: {"demo": {"investigations_per_ip": {"count": 1, "minutes": 60},
                                                        "investigations_total": {"count": 3, "minutes": 60}}})
    assert client.post("/api/cases/BX-C001/investigate").status_code == 200
    assert client.post("/api/cases/BX-C001/investigate").status_code == 429            # the visitor is out
    assert client.post("/api/cases/BX-C001/investigate", headers=TOKEN).status_code == 200
    assert client.post("/api/cases/BX-C001/investigate", headers=TOKEN).status_code == 200
    assert client.post("/api/cases/BX-C001/investigate", headers=TOKEN).status_code == 429  # the total holds
