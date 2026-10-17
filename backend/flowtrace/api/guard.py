"""Public-demo guards: demo mode and rate limits (config.yaml, demo).

DEMO_MODE=1 on the server turns on the demo rules: only the bundled samples can be uploaded, and a sample that is
already uploaded opens as it is instead of being processed again. Rate limits always apply; they are sized for
a person clicking through the app, not for scripts.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from flowtrace.settings import cfg


def demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")


def client_ip(request: Request) -> str:
    """The visitor's address. Behind Fly's proxy it is in Fly-Client-IP; elsewhere the first X-Forwarded-For."""
    h = request.headers
    ip = h.get("fly-client-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
    return ip or (request.client.host if request.client else "unknown")


class RateLimit:
    """Sliding-window counters, in memory (one server process)."""

    def __init__(self) -> None:
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def take(self, key: str, count: int, minutes: float, now: float | None = None) -> float:
        """Count one use of key. Returns 0 if allowed, else the seconds until the oldest use leaves the window."""
        now = time.monotonic() if now is None else now
        window = minutes * 60
        with self.lock:
            q = self.hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= count:
                return q[0] + window - now
            q.append(now)
            return 0.0

    def clear(self) -> None:
        with self.lock:
            self.hits.clear()


LIMITS = RateLimit()


def limit(request: Request, what: str) -> None:
    """Raise 429 when this visitor (or everyone together) used `what` too often. Limits: config.yaml demo."""
    rules = cfg().get("demo") or {}
    checks = [(f"{what}:ip:{client_ip(request)}", rules.get(f"{what}_per_ip")),
              (f"{what}:total", rules.get(f"{what}_total"))]
    for key, rule in checks:
        if not rule:
            continue
        wait = LIMITS.take(key, rule["count"], rule["minutes"])
        if wait:
            who = "everyone" if key.endswith(":total") else "you"
            raise HTTPException(429, f"Too many {what} from {who} on this public demo: try again in "
                                     f"{max(1, round(wait / 60))} min.", headers={"Retry-After": str(int(wait) + 1)})
