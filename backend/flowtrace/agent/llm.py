"""Thin Claude client for structured JSON calls.

Uses the server-side refusal fallback ("default" mode) so a policy decline is retried on
Anthropic's recommended fallback model inside the same call. Callers always have a
deterministic fallback path, so any failure here returns None instead of raising.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

import anthropic

from flowtrace.settings import settings

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@lru_cache
def client() -> anthropic.Anthropic | None:
    # The key may live in .env (read by pydantic-settings, not exported to os.environ), so pass it
    # explicitly; otherwise let the SDK resolve ANTHROPIC_API_KEY / auth token / profile itself.
    key = settings().anthropic_api_key
    c = anthropic.Anthropic(api_key=key, max_retries=2, timeout=120.0) if key else anthropic.Anthropic(max_retries=2, timeout=120.0)
    if not (c.api_key or c.auth_token):
        log.warning("Claude client unavailable: no credentials found")
        return None
    return c


def available() -> bool:
    return client() is not None


def structured(system: str, user: str, schema: dict, effort: str = "medium", max_tokens: int = 16000) -> tuple[dict | None, dict]:
    """Return (parsed JSON or None, call metadata)."""
    c = client()
    meta: dict = {"model": settings().agent_model}
    if c is None:
        meta["error"] = "no Claude credentials"
        return None, meta
    try:
        resp = c.beta.messages.create(
            model=settings().agent_model,
            max_tokens=max_tokens,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.RateLimitError as e:
        meta["error"] = f"rate limited: {e.message}"
        return None, meta
    except anthropic.APIStatusError as e:
        meta["error"] = f"API error {e.status_code}: {e.message}"
        return None, meta
    except anthropic.APIConnectionError as e:
        meta["error"] = f"connection error: {e}"
        return None, meta

    meta.update({"served_by": resp.model, "stop_reason": resp.stop_reason,
                 "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens})
    if resp.stop_reason == "refusal":
        meta["error"] = "refused"
        return None, meta
    if resp.stop_reason == "max_tokens":
        meta["error"] = "hit max_tokens"
        return None, meta
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(text), meta
    except json.JSONDecodeError as e:
        meta["error"] = f"invalid JSON: {e}"
        return None, meta
