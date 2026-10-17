"""Thin LLM client for structured JSON calls: OpenAI (default) or Anthropic, chosen by LLM_PROVIDER.

Both providers are asked for output that matches a strict JSON schema. Callers always have a
deterministic fallback path, so any failure here (no key, refusal, truncation, rate limit, API or
connection error, invalid JSON) returns None with meta["error"] instead of raising.

Spend: every call's tokens are priced (config.yaml llm.price_per_mtok) and added to today's total (UTC), kept in
data/runs/llm_spend.json so a restart does not reset it. Once the total reaches llm.daily_budget_usd, calls return
None ("daily LLM budget used") and available() is False, so the agent runs its rule-based policy until midnight UTC.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache

import openai

from flowtrace.settings import cfg, settings

log = logging.getLogger(__name__)

DEFAULT_MODEL = {"openai": "gpt-6-luna", "anthropic": "claude-sonnet-5-5"}
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
PROBE_TTL_S = 300        # a working key is re-checked every 5 minutes
PROBE_FAIL_TTL_S = 30    # a failed check is retried sooner (network blip, outage)
_probe_cache: dict[tuple, tuple[float, bool]] = {}
_MODEL_PREFIX = {"openai": "claude-", "anthropic": "gpt-"}  # model ids that belong to the *other* provider


def model_name() -> str:
    s = settings()
    m = s.agent_model
    if m and m.startswith(_MODEL_PREFIX[s.llm_provider]):
        # e.g. AGENT_MODEL=claude-sonnet-5-5 left over from an older .env with LLM_PROVIDER=openai
        log.warning("AGENT_MODEL=%s does not belong to LLM_PROVIDER=%s; using %s", m, s.llm_provider,
                    DEFAULT_MODEL[s.llm_provider])
        m = None
    return m or DEFAULT_MODEL[s.llm_provider]


@lru_cache
def _openai(key: str) -> openai.OpenAI:
    return openai.OpenAI(api_key=key, max_retries=2, timeout=120.0)


@lru_cache
def _anthropic(key: str | None):
    # The key may live in .env (read by pydantic-settings, not exported to os.environ), so pass it
    # explicitly; otherwise let the SDK resolve ANTHROPIC_API_KEY / auth token / profile itself.
    import anthropic
    c = anthropic.Anthropic(api_key=key, max_retries=2, timeout=120.0) if key else anthropic.Anthropic(max_retries=2, timeout=120.0)
    return c if (c.api_key or c.auth_token) else None


def _client():
    s = settings()
    if s.llm_provider == "openai":
        return _openai(s.openai_api_key) if s.openai_api_key else None
    return _anthropic(s.anthropic_api_key)


# --- daily spend cap -----------------------------------------------------------------------------

_spend_lock = threading.Lock()


def _spend_file():
    return settings().raw_dir.parent / "runs" / "llm_spend.json"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def spend() -> dict:
    """Today's LLM spend (UTC day) and the cap: {"day", "usd", "calls", "budget_usd", "over"}."""
    budget = float((cfg().get("llm") or {}).get("daily_budget_usd") or 0)
    with _spend_lock:
        try:
            d = json.loads(_spend_file().read_text())
        except (OSError, ValueError):
            d = {}
    if d.get("day") != _today():
        d = {"day": _today(), "usd": 0.0, "calls": 0}
    return d | {"budget_usd": budget, "over": bool(budget) and d["usd"] >= budget}


def cost_usd(model: str, input_tokens: int | None, output_tokens: int | None) -> float:
    prices = (cfg().get("llm") or {}).get("price_per_mtok") or {}
    p = prices.get(model) or prices.get("default") or {"input": 0.0, "output": 0.0}
    return ((input_tokens or 0) * p["input"] + (output_tokens or 0) * p["output"]) / 1e6


def _add_spend(meta: dict) -> None:
    usd = cost_usd(meta.get("model", ""), meta.get("input_tokens"), meta.get("output_tokens"))
    meta["cost_usd"] = round(usd, 6)
    with _spend_lock:
        f = _spend_file()
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            d = {}
        if d.get("day") != _today():
            d = {"day": _today(), "usd": 0.0, "calls": 0}
        d["usd"] = round(d["usd"] + usd, 6)
        d["calls"] += 1
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d))


def available() -> bool:
    """True only when the key is accepted by the API (cheap model lookup, no tokens; cached) and today's spend is
    under the daily budget.

    A rate-limited key still counts as working. Note: an OpenAI restricted key without the
    "Models: Read" permission fails this lookup even though it can call the Responses API."""
    c, model = _client(), model_name()
    if c is None or spend()["over"]:
        return False
    k = (settings().llm_provider, model, id(c))
    hit = _probe_cache.get(k)
    if hit and time.monotonic() - hit[0] < (PROBE_TTL_S if hit[1] else PROBE_FAIL_TTL_S):
        return hit[1]
    try:
        c.with_options(max_retries=0).models.retrieve(model, timeout=5.0)  # health must not stall on retries
        ok = True
    except Exception as e:  # noqa: BLE001 - any failure means "not usable"; the reason goes to the log
        ok = getattr(e, "status_code", None) == 429
        if not ok:
            log.warning("LLM unavailable (%s): %s", model, _describe(e))
    _probe_cache[k] = (time.monotonic(), ok)
    return ok


_ACCOUNT_IDS = re.compile(r"\b(?:org|proj|user)[-_][A-Za-z0-9]+")


def _scrub(text: str) -> str:
    """Provider error messages can name the organisation / project / user; keep those out of meta and logs."""
    return _ACCOUNT_IDS.sub("[id]", text)


def _describe(e: Exception) -> str:
    """Error text for meta/logs. Auth errors are summarised: their message echoes the key's tail."""
    status = getattr(e, "status_code", None)
    if status == 401:
        return "API error 401: invalid API key"
    if status == 429:
        return f"rate limited: {_scrub(str(getattr(e, 'message', e)))}"
    if status is not None:
        return f"API error {status}: {_scrub(str(getattr(e, 'message', e)))}"
    if type(e).__name__ in ("APIConnectionError", "APITimeoutError"):  # same names in both SDKs
        return f"connection error: {_scrub(str(e))}"
    return f"{'OpenAI' if isinstance(e, openai.OpenAIError) else 'LLM'} error: {_scrub(str(e))}"


def structured(system: str, user: str, schema: dict, effort: str = "medium", max_tokens: int = 16000) -> tuple[dict | None, dict]:
    """Return (parsed JSON or None, call metadata)."""
    s = settings()
    meta: dict = {"provider": s.llm_provider, "model": model_name()}
    if spend()["over"]:
        meta["error"] = "daily LLM budget used"
        return None, meta
    call = _openai_structured if s.llm_provider == "openai" else _anthropic_structured
    t0 = time.perf_counter()
    out, meta = call(system, user, schema, effort, max_tokens, meta)
    meta["ms"] = round((time.perf_counter() - t0) * 1000)
    if meta.get("input_tokens") or meta.get("output_tokens"):
        _add_spend(meta)
    return out, meta


def _parse(text: str, meta: dict) -> tuple[dict | None, dict]:
    try:
        return json.loads(text), meta
    except json.JSONDecodeError as e:
        meta["error"] = f"invalid JSON: {e}"
        return None, meta


def _openai_structured(system, user, schema, effort, max_tokens, meta):
    key = settings().openai_api_key
    if not key:
        meta["error"] = "no OpenAI API key"
        return None, meta
    try:
        resp = _openai(key).responses.create(
            model=meta["model"],
            instructions=system,
            input=user,
            text={"format": {"type": "json_schema", "name": "output", "schema": schema, "strict": True}},
            reasoning={"effort": effort},
            max_output_tokens=max_tokens,  # includes reasoning tokens
            store=False,                   # account data must not be kept on OpenAI's side
        )
    except openai.OpenAIError as e:        # status, rate limit, connection/timeout, and anything else from the SDK
        meta["error"] = _describe(e)
        return None, meta

    u = resp.usage
    meta.update({"served_by": resp.model, "status": resp.status,
                 "input_tokens": u.input_tokens if u else None, "output_tokens": u.output_tokens if u else None,
                 "reasoning_tokens": getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", None)})
    content = [c for o in resp.output if o.type == "message" for c in o.content]
    refusal = next((c.refusal for c in content if c.type == "refusal"), None)
    if refusal is not None:
        meta["error"] = f"refused: {refusal}"
        return None, meta
    if resp.status == "incomplete":
        reason = getattr(resp.incomplete_details, "reason", None)
        meta["error"] = "hit max_output_tokens" if reason == "max_output_tokens" else f"incomplete: {reason}"
        return None, meta
    if resp.status != "completed":  # failed / cancelled
        meta["error"] = f"status {resp.status}: {getattr(resp.error, 'message', None)}"
        return None, meta
    return _parse("".join(c.text for c in content if c.type == "output_text"), meta)


def _anthropic_structured(system, user, schema, effort, max_tokens, meta):
    """Server-side refusal fallback ("default" mode) retries a policy decline on Anthropic's
    recommended fallback model inside the same call."""
    c = _anthropic(settings().anthropic_api_key)
    if c is None:
        meta["error"] = "no Anthropic credentials"
        return None, meta
    import anthropic
    try:
        resp = c.beta.messages.create(
            model=meta["model"],
            max_tokens=max_tokens,
            betas=[ANTHROPIC_FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AnthropicError as e:
        meta["error"] = _describe(e)
        return None, meta

    meta.update({"served_by": resp.model, "stop_reason": resp.stop_reason,
                 "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens})
    if resp.stop_reason == "refusal":
        meta["error"] = "refused"
        return None, meta
    if resp.stop_reason == "max_tokens":
        meta["error"] = "hit max_tokens"
        return None, meta
    return _parse("".join(b.text for b in resp.content if b.type == "text"), meta)
