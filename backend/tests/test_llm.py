"""structured() with a mocked OpenAI client: every failure returns (None, meta with an error), never raises."""
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from flowtrace.agent import llm
from flowtrace.agent.policy import _schema as policy_schema
from flowtrace.agent.report import SCHEMA as REPORT_SCHEMA
from flowtrace.settings import Settings

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
REQ = httpx.Request("POST", "https://api.openai.com/v1/responses")


def usage(i=120, o=30, r=10):
    return NS(input_tokens=i, output_tokens=o, output_tokens_details=NS(reasoning_tokens=r))


def message(*content, status="completed", reason=None):
    return NS(model="gpt-6-luna-2026-09-01", status=status, usage=usage(),
              incomplete_details=NS(reason=reason) if reason else None,
              output=[NS(type="reasoning"), NS(type="message", content=list(content))])


def text(s):
    return NS(type="output_text", text=s)


class FakeClient:
    def __init__(self, result):
        self.result, self.calls, self.options = result, [], []
        self.responses = NS(create=self.create)

    def with_options(self, **kw):
        self.options.append(kw)
        return self

    def create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def use(monkeypatch):
    """Point llm at a fake OpenAI client and an isolated settings object (no .env)."""
    llm._probe_cache.clear()
    yield _make_use(monkeypatch)
    llm._probe_cache.clear()


def _make_use(monkeypatch):
    def _use(result, **env):
        env.setdefault("openai_api_key", "sk-test")
        s = Settings(_env_file=None, **env)
        fake = FakeClient(result)
        monkeypatch.setattr(llm, "settings", lambda: s)
        monkeypatch.setattr(llm, "_openai", lambda key: fake)
        return fake
    return _use


def auth_error():
    return openai.AuthenticationError("Incorrect API key provided: sk-proj-****abcd",
                                      response=httpx.Response(401, request=REQ), body=None)


def test_success_returns_parsed_json_and_usage(use):
    fake = use(message(text('{"ok": true}')))
    out, meta = llm.structured("sys", "user", SCHEMA, effort="low", max_tokens=500)
    assert out == {"ok": True}
    assert "error" not in meta
    assert meta["provider"] == "openai" and meta["model"] == "gpt-6-luna"
    assert meta["served_by"] == "gpt-6-luna-2026-09-01"
    assert (meta["input_tokens"], meta["output_tokens"], meta["reasoning_tokens"]) == (120, 30, 10)
    assert isinstance(meta["ms"], int) and meta["ms"] >= 0      # wall time of the call, for latency reports


def test_request_uses_strict_json_schema_effort_and_token_cap(use):
    fake = use(message(text('{"ok": true}')))
    llm.structured("sys", "user", SCHEMA, effort="low", max_tokens=500)
    kw = fake.calls[0]
    assert kw["model"] == "gpt-6-luna"
    assert kw["instructions"] == "sys" and kw["input"] == "user"
    assert kw["text"]["format"] == {"type": "json_schema", "name": "output", "schema": SCHEMA, "strict": True}
    assert kw["reasoning"] == {"effort": "low"}
    assert kw["max_output_tokens"] == 500
    assert kw["store"] is False          # AML account data must not be kept by OpenAI


def test_agent_model_overrides_the_default(use):
    fake = use(message(text('{"ok": true}')), agent_model="gpt-6.1-sol")
    _, meta = llm.structured("s", "u", SCHEMA)
    assert fake.calls[0]["model"] == "gpt-6.1-sol" and meta["model"] == "gpt-6.1-sol"


def test_refusal_returns_none(use):
    use(message(NS(type="refusal", refusal="I can't help with that.")))
    out, meta = llm.structured("s", "u", SCHEMA)
    assert out is None
    assert meta["error"].startswith("refused")


def test_invalid_json_returns_none(use):
    use(message(text('{"ok": tru')))
    out, meta = llm.structured("s", "u", SCHEMA)
    assert out is None
    assert meta["error"].startswith("invalid JSON")


def test_truncation_returns_none(use):
    use(message(text('{"ok"'), status="incomplete", reason="max_output_tokens"))
    out, meta = llm.structured("s", "u", SCHEMA)
    assert out is None
    assert meta["error"] == "hit max_output_tokens"
    assert meta["output_tokens"] == 30


def test_no_key_returns_none_without_calling_the_api(use):
    fake = use(message(text('{"ok": true}')), openai_api_key=None)
    out, meta = llm.structured("s", "u", SCHEMA)
    assert out is None and meta["error"] == "no OpenAI API key"
    assert fake.calls == []


@pytest.mark.parametrize("exc, prefix", [
    (openai.RateLimitError("slow down", response=httpx.Response(429, request=REQ), body=None), "rate limited"),
    (auth_error(), "API error 401"),
    (openai.APIConnectionError(request=REQ), "connection error"),
    (openai.APITimeoutError(request=REQ), "connection error"),
])
def test_api_failures_return_none(use, exc, prefix):
    use(exc)
    out, meta = llm.structured("s", "u", SCHEMA)
    assert out is None
    assert meta["error"].startswith(prefix)


def test_anthropic_provider_without_key_returns_none(use, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    use(None, llm_provider="anthropic", openai_api_key=None)
    monkeypatch.setattr(llm, "_anthropic", lambda key: None)
    out, meta = llm.structured("s", "u", SCHEMA)
    assert out is None and meta["provider"] == "anthropic" and meta["model"] == "claude-sonnet-5-5"
    assert meta["error"] == "no Anthropic credentials"


def _strict_ok(node, path="$"):
    """OpenAI strict mode: every object lists all its properties in required and forbids extras; no root anyOf."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            props = set(node.get("properties", {}))
            assert node.get("additionalProperties") is False, f"{path}: additionalProperties must be false"
            assert set(node.get("required", [])) == props, f"{path}: every property must be required"
        for k, v in node.items():
            _strict_ok(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _strict_ok(v, f"{path}[{i}]")


@pytest.mark.parametrize("schema", [policy_schema(), REPORT_SCHEMA], ids=["policy", "report"])
def test_every_schema_we_send_is_valid_for_strict_mode(schema):
    assert schema["type"] == "object" and "anyOf" not in schema
    _strict_ok(schema)


def test_available_is_false_without_a_key(use):
    use(None, openai_api_key=None)
    assert llm.available() is False


def models(fake, fn):
    calls = []

    def retrieve(model, **kw):
        calls.append(model)
        return fn(model)
    fake.models = NS(retrieve=retrieve)
    return calls


def raise_(e):
    raise e


def test_available_checks_the_key_once_without_retries(use):
    fake = use(None)
    calls = models(fake, lambda m: NS(id=m))
    assert llm.available() is True
    assert llm.available() is True
    assert calls == ["gpt-6-luna"]                  # second call served from the cache
    assert fake.options == [{"max_retries": 0}]     # health must not stall on retries


def test_available_is_false_for_a_rejected_key(use):
    fake = use(None)
    models(fake, lambda m: raise_(auth_error()))
    assert llm.available() is False


def test_rate_limited_key_still_counts_as_available(use):
    fake = use(None)
    models(fake, lambda m: raise_(openai.RateLimitError("slow down", response=httpx.Response(429, request=REQ), body=None)))
    assert llm.available() is True


def test_a_failed_probe_is_retried_sooner_than_a_good_one(use, monkeypatch):
    fake = use(None)
    now = [1000.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: now[0])
    calls = models(fake, lambda m: raise_(openai.APIConnectionError(request=REQ)))
    assert llm.available() is False
    now[0] += llm.PROBE_FAIL_TTL_S + 1
    assert llm.available() is False
    assert len(calls) == 2


def test_auth_error_message_hides_the_key_tail(use):
    use(auth_error())
    _, meta = llm.structured("s", "u", SCHEMA)
    assert meta["error"] == "API error 401: invalid API key"
    assert "sk-" not in meta["error"]


def test_failed_response_reports_its_status(use):
    r = message(text(""), status="failed")
    r.error = NS(message="server_error")
    use(r)
    out, meta = llm.structured("s", "u", SCHEMA)
    assert out is None and meta["error"] == "status failed: server_error"


def test_content_filter_truncation_is_reported(use):
    use(message(text('{"ok"'), status="incomplete", reason="content_filter"))
    out, meta = llm.structured("s", "u", SCHEMA)
    assert out is None and meta["error"] == "incomplete: content_filter"


def test_a_model_id_from_the_other_provider_falls_back_to_the_default(use):
    use(None, agent_model="claude-sonnet-5-5")                     # left over from the old .env.example
    assert llm.model_name() == "gpt-6-luna"
    use(None, llm_provider="anthropic", agent_model="gpt-6-luna")
    assert llm.model_name() == "claude-sonnet-5-5"


def test_model_name_resolves_per_provider(use):
    use(None)
    assert llm.model_name() == "gpt-6-luna"
    use(None, llm_provider="anthropic")
    assert llm.model_name() == "claude-sonnet-5-5"


def test_any_other_openai_error_returns_none(use):
    use(openai.OpenAIError("client misconfigured"))
    out, meta = llm.structured("s", "u", SCHEMA)
    assert out is None and meta["error"].startswith("OpenAI error")


def test_org_and_project_ids_never_reach_meta(use):
    msg = "You exceeded your quota for org-AbC123xyz in project proj_9fK2LmN0 (req_77aa)"
    use(openai.PermissionDeniedError(msg, response=httpx.Response(403, request=REQ), body=None))
    _, meta = llm.structured("s", "u", SCHEMA)
    assert "org-AbC123xyz" not in meta["error"] and "proj_9fK2LmN0" not in meta["error"]
    assert meta["error"].startswith("API error 403")
    use(openai.RateLimitError(msg, response=httpx.Response(429, request=REQ), body=None))
    _, meta = llm.structured("s", "u", SCHEMA)
    assert "org-AbC123xyz" not in meta["error"] and meta["error"].startswith("rate limited")
