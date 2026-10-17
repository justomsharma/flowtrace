"""FalkorDB connection helpers.

Agent tools must call `ro()` so FalkorDB itself rejects any write (GRAPH.RO_QUERY).
Only the loader and case-memory writer use `rw()`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from falkordb import FalkorDB

from flowtrace.settings import settings

DEFAULT_TIMEOUT_MS = 10_000


@lru_cache
def db() -> FalkorDB:
    s = settings()
    return FalkorDB(host=s.falkordb_host, port=s.falkordb_port, username=s.falkordb_username,
                    password=s.falkordb_password)


def graph(name: str | None = None):
    return db().select_graph(name or settings().graph_name)


@dataclass
class QueryResult:
    rows: list[dict[str, Any]]
    cypher: str
    params: dict[str, Any]
    ms: float
    stats: dict[str, Any] = field(default_factory=dict)


def _run(fn, cypher: str, params: dict[str, Any] | None, timeout: int) -> QueryResult:
    params = params or {}
    t0 = time.perf_counter()
    res = fn(cypher, params, timeout=timeout)
    ms = (time.perf_counter() - t0) * 1000
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    rows = [dict(zip(cols, r)) for r in res.result_set]
    return QueryResult(rows=rows, cypher=cypher, params=params, ms=ms)


def ro(cypher: str, params: dict[str, Any] | None = None, timeout: int = DEFAULT_TIMEOUT_MS) -> QueryResult:
    """Read-only query. FalkorDB raises if the query tries to write."""
    return _run(graph().ro_query, cypher, params, timeout)


def rw(cypher: str, params: dict[str, Any] | None = None, timeout: int = 60_000) -> QueryResult:
    return _run(graph().query, cypher, params, timeout)
