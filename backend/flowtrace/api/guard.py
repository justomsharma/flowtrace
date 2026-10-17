"""Public-demo guards: demo mode, rate limits, failed-token lockout, request size limits and the MCP endpoint's
checks (config.yaml, demo).

DEMO_MODE=1 on the server turns on the demo rules: only the bundled samples can be uploaded, a sample that is
already uploaded opens as it is, anonymous investigations use the rule-based policy, and an MCP client needs the
approver token to propose an action. Rate limits always apply; they are sized for a person clicking through the
app, not for scripts.
"""
from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from flowtrace.settings import cfg

MAX_BODY = 64 * 1024                 # every request but uploads (JSON decisions, MCP messages)
MAX_UPLOAD = 51 * 1024 * 1024        # an upload: a CSV of at most 50 MB plus the form around it
MAX_UPLOAD_DEMO = 1024 * 1024        # the bundled samples are under 250 KB


def demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")


def _ip_key(ip: str) -> str:
    """An IPv6 address counts by its /64: one home or phone connection hands out a whole /64."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 4:
        return str(addr)
    if addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    return str(ipaddress.ip_network(f"{addr}/64", strict=False))


def client_ip(request) -> str:
    """Who is asking, for the limits. On the public demo (behind Fly's proxy) it is Fly-Client-IP, which the proxy
    sets itself; anywhere else the connection's own address. X-Forwarded-For is never read: any client can write it."""
    ip = request.headers.get("fly-client-ip") if demo_mode() else None
    ip = (ip or "").strip() or (request.client.host if request.client else "unknown")
    return _ip_key(ip)


class RateLimit:
    """Sliding-window counters, in memory (one server process)."""

    def __init__(self) -> None:
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def _trim(self, key: str, window: float, now: float) -> deque:
        q = self.hits[key]
        while q and q[0] <= now - window:
            q.popleft()
        return q

    def take(self, key: str, count: int, minutes: float, now: float | None = None) -> float:
        """Count one use of key. Returns 0 if allowed, else the seconds until the oldest use leaves the window."""
        now = time.monotonic() if now is None else now
        window = minutes * 60
        with self.lock:
            q = self._trim(key, window, now)
            if len(q) >= count:
                return q[0] + window - now
            q.append(now)
            return 0.0

    def full(self, key: str, count: int, minutes: float, now: float | None = None) -> float:
        """Like take, without counting a use: 0 if there is room, else the seconds to wait."""
        now = time.monotonic() if now is None else now
        window = minutes * 60
        with self.lock:
            q = self._trim(key, window, now)
            return q[0] + window - now if len(q) >= count else 0.0

    def clear(self) -> None:
        with self.lock:
            self.hits.clear()


LIMITS = RateLimit()
FAILURES = RateLimit()


def _too_many(what: str, who: str, wait: float) -> HTTPException:
    return HTTPException(429, f"Too many {what} from {who} on this public demo: try again in "
                              f"{max(1, round(wait / 60))} min.", headers={"Retry-After": str(int(wait) + 1)})


def limit(request: Request, what: str) -> None:
    """Raise 429 when this visitor (or everyone together) used `what` too often. Limits: config.yaml demo."""
    rules = cfg().get("demo") or {}
    for key, rule, who in ((f"{what}:ip:{client_ip(request)}", rules.get(f"{what}_per_ip"), "you"),
                           (f"{what}:total", rules.get(f"{what}_total"), "everyone")):
        if rule and (wait := LIMITS.take(key, rule["count"], rule["minutes"])):
            raise _too_many(what, who, wait)


def token_attempt(request: Request, ok: bool) -> None:
    """Call before acting on an approver token. After too many wrong tokens from one visitor (config.yaml
    demo.approver_failures_per_ip) every attempt is refused until the window passes, even a right one."""
    rule = (cfg().get("demo") or {}).get("approver_failures_per_ip")
    if not rule:
        return
    key = f"approver:ip:{client_ip(request)}"
    if wait := FAILURES.full(key, rule["count"], rule["minutes"]):
        raise _too_many("wrong approver tokens", "you", wait)
    if not ok:
        FAILURES.take(key, rule["count"], rule["minutes"])


class BodyLimit:
    """Refuse a request body over its limit before anything reads it: a declared Content-Length over the limit gets
    413 at once, and a body that streams past it is cut off (the client is treated as gone)."""

    def __init__(self, app) -> None:
        self.app = app

    @staticmethod
    def cap(path: str) -> int:
        if path == "/api/batches":
            return MAX_UPLOAD_DEMO if demo_mode() else MAX_UPLOAD
        return MAX_BODY

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cap = self.cap(scope["path"])
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > cap):
            return await JSONResponse({"detail": f"Request too large ({cap // 1024} KB max)."}, 413)(scope, receive, send)
        seen = 0

        async def counted():
            nonlocal seen
            msg = await receive()
            seen += len(msg.get("body", b""))
            if seen > cap:
                raise ClientDisconnect()
            return msg
        return await self.app(scope, counted, send)


def _calls(body: bytes, tool: str) -> bool:
    """Does this JSON-RPC message (or batch) call the MCP tool `tool`?"""
    try:
        msg = json.loads(body or b"null")
    except ValueError:
        return False
    for m in msg if isinstance(msg, list) else [msg]:
        if isinstance(m, dict) and m.get("method") == "tools/call" and (m.get("params") or {}).get("name") == tool:
            return True
    return False


class McpGuard:
    """In front of the MCP endpoint: every request counts toward the visitor's MCP limit (read tools too), the
    message is at most MAX_BODY, and on the public demo a propose_action call needs the approver token
    (X-Approver-Token), with the same wrong-token lockout as the app. Reading never needs it."""

    def __init__(self, app, token_ok) -> None:
        self.app, self.token_ok = app, token_ok

    async def __call__(self, scope, receive, send):
        if self.app is None:  # the server is not running (or is stopping)
            return await JSONResponse({"detail": "MCP is not available yet."}, 503)(scope, receive, send)
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        try:
            limit(request, "mcp")
        except HTTPException as e:
            return await JSONResponse({"detail": e.detail}, e.status_code, headers=e.headers)(scope, receive, send)
        body, more = b"", True
        while more:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
            if len(body) > MAX_BODY:
                return await JSONResponse({"detail": "MCP message too large."}, 413)(scope, receive, send)
        if demo_mode() and _calls(body, "propose_action"):
            token = request.headers.get("x-approver-token")
            try:
                if not token:
                    raise HTTPException(401, "On the public demo, propose_action needs the approver token "
                                             "(X-Approver-Token header). The read tools need none.")
                ok = self.token_ok(token)
                token_attempt(request, ok)
                if not ok:
                    raise HTTPException(401, "approver token missing or wrong")
            except HTTPException as e:
                err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": e.detail}}
                return await JSONResponse(err, e.status_code, headers=e.headers)(scope, receive, send)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        return await self.app(scope, replay, send)
