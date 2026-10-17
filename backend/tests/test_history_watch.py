"""The demo history is reloaded only when FalkorDB answers and the history is really missing, never on an error."""
import pytest
import redis
from fastapi.testclient import TestClient

from flowtrace.api import main as M
from flowtrace.demo import history
from flowtrace.demo.history import GraphUnreachable, HistoryWatch


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def watch(states, clock=None, **kw):
    it = iter(states)

    def state():
        s = next(it)
        if isinstance(s, Exception):
            raise s
        return s
    return HistoryWatch(state=state, clock=clock or Clock(), **kw)


def test_an_unreachable_graph_never_triggers_a_reload():
    w = watch([GraphUnreachable("refused")] * 10)
    assert [w.check() for _ in range(10)] == ["unreachable"] * 10


def test_missing_must_be_seen_three_times_in_a_row():
    w = watch(["missing", "missing", "missing"])
    assert [w.check() for _ in range(3)] == ["missing", "missing", "reload"]


@pytest.mark.parametrize("between", ["loaded", GraphUnreachable("timeout")])
def test_anything_else_in_between_starts_the_count_again(between):
    w = watch(["missing", "missing", between, "missing", "missing", "missing"])
    assert [w.check() for _ in range(6)][-1] == "reload"
    w = watch(["missing", "missing", between, "missing", "missing"])
    assert "reload" not in [w.check() for _ in range(5)]


def test_a_failed_reload_backs_off_and_doubles():
    clock = Clock()
    w = watch(["missing"] * 20, clock=clock, backoff_s=60, max_backoff_s=200)
    assert [w.check() for _ in range(3)][-1] == "reload"
    w.failed()
    assert w.check() == "waiting"
    clock.t = 61
    assert [w.check() for _ in range(3)][-1] == "reload"
    w.failed()
    clock.t = 61 + 119
    assert w.check() == "waiting"           # second wait is 120 s
    clock.t = 61 + 121
    assert [w.check() for _ in range(3)][-1] == "reload"
    w.failed()
    assert w.wait_s == 200                  # capped


def test_a_successful_reload_resets_the_backoff():
    w = watch(["missing"] * 3)
    w.failed()
    w.loaded()
    assert w.wait_s == w.backoff_s and w.next_try == 0


def test_state_reads_errors_as_errors_and_an_empty_key_as_missing(monkeypatch):
    def raises(e):
        def ro(*a, **k):
            raise e
        return ro
    monkeypatch.setattr(history.client, "ro", raises(redis.exceptions.ConnectionError("refused")))
    with pytest.raises(GraphUnreachable):
        history.history_state()
    monkeypatch.setattr(history.client, "ro", raises(redis.exceptions.TimeoutError("slow")))
    with pytest.raises(GraphUnreachable):
        history.history_state()
    monkeypatch.setattr(history.client, "ro", raises(redis.exceptions.ResponseError("WRONGPASS invalid password")))
    with pytest.raises(GraphUnreachable):
        history.history_state()
    monkeypatch.setattr(history.client, "ro", raises(redis.exceptions.ResponseError("Invalid graph operation on empty key")))
    assert history.history_state() == "missing"


def test_ensure_reloads_only_on_the_watchs_word(monkeypatch):
    calls = []
    monkeypatch.setattr(M, "clear_demo_state", lambda: calls.append("clear"))
    monkeypatch.setattr(history, "load", lambda: calls.append("load") or {})
    monkeypatch.setattr(M, "HISTORY_WATCH", watch([GraphUnreachable("x"), "missing", "missing", "missing"]))
    for _ in range(3):
        M.ensure_demo_history()
    assert calls == []
    M.ensure_demo_history()
    assert calls == ["clear", "load"]


def test_a_failing_load_is_logged_and_backs_off(monkeypatch):
    def boom():
        raise redis.exceptions.ConnectionError("dropped mid-load")
    monkeypatch.setattr(M, "clear_demo_state", lambda: None)
    monkeypatch.setattr(history, "load", boom)
    w = watch(["missing"] * 3)
    monkeypatch.setattr(M, "HISTORY_WATCH", w)
    for _ in range(3):
        M.ensure_demo_history()   # no exception
    assert w.next_try > 0


def test_startup_survives_an_unreachable_graph(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("FLOWTRACE_REQUEUE", "0")

    def down():
        raise GraphUnreachable("connection refused")
    monkeypatch.setattr(M, "HISTORY_WATCH", HistoryWatch(state=down))
    monkeypatch.setattr(M, "reset_demo", lambda: pytest.fail("never reset on an error"))
    with TestClient(M.app) as c:
        assert c.get("/healthz").json() == {"ok": True}
