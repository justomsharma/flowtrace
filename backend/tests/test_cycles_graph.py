"""Loops on a real FalkorDB: the agent's find_cycles query and the CYCLE detector return each loop in path order,
time-ordered, on every FalkorDB we run on. (FalkorDB v4.22, the Cloud, can return a closed path's relationships(p)
or nodes(p) reversed.)"""
import pytest

from flowtrace.detectors import alerts as A
from flowtrace.graph import queries as Q
from flowtrace.graph.client import graph, ro, rw
from flowtrace.settings import settings

SCRATCH = "zz_test_cycles"


@pytest.fixture
def g(monkeypatch):
    try:
        ro("RETURN 1 AS ok", timeout=1000)
    except Exception:
        pytest.skip("FalkorDB is not running")
    monkeypatch.setattr(settings(), "graph_name", SCRATCH)
    # A->B->C->A in time order (a real loop); A->B->A twice over with one loop in time order (e1 then e2) and one
    # backwards in time (e4 at 4 before e3 at 3 cannot loop: it is e3 then e4 that does).
    rw("""CREATE (a:Account {id: 'A'}), (b:Account {id: 'B'}), (c:Account {id: 'C'}), (d:Account {id: 'D'}),
                 (a)-[:TRANSFER {tx_id: 'ab', ts: 10, amount: 100.0}]->(b),
                 (b)-[:TRANSFER {tx_id: 'bc', ts: 11, amount: 90.0}]->(c),
                 (c)-[:TRANSFER {tx_id: 'ca', ts: 12, amount: 80.0}]->(a),
                 (a)-[:TRANSFER {tx_id: 'ad', ts: 20, amount: 50.0}]->(d),
                 (d)-[:TRANSFER {tx_id: 'da', ts: 21, amount: 40.0}]->(a),
                 (a)-[:TRANSFER {tx_id: 'ad2', ts: 31, amount: 5.0}]->(d),
                 (d)-[:TRANSFER {tx_id: 'da2', ts: 30, amount: 4.0}]->(a)""")
    yield
    try:
        graph(SCRATCH).delete()
    except Exception:
        pass


# Every time-ordered loop of A with 2-4 transfers: the triangle, and A->D->A twice (ad then da, ad then da2).
LOOPS = {("ab", "bc", "ca"), ("ad", "da"), ("ad", "da2")}


def test_find_cycles_returns_loops_in_path_and_time_order(g):
    rows = ro(Q.CYCLES, {"id": "A", "t0": 0, "t1": 100}).rows
    got = {tuple(r["tx_ids"]): r for r in rows}
    assert set(got) == LOOPS
    assert got[("ab", "bc", "ca")]["accounts"] == ["A", "B", "C", "A"]
    assert got[("ad", "da")]["accounts"] == ["A", "D", "A"]
    assert got[("ab", "bc", "ca")]["amounts"] == [100.0, 90.0, 80.0]
    assert all(r["ts"] == sorted(r["ts"]) for r in rows)


def test_the_cycle_detector_sees_the_same_loops(g):
    rows = ro(A.CYCLE, {"ids": ["A"], "t0": 0, "t1": 100}).rows
    assert {tuple(r["tx_ids"]) for r in rows} == LOOPS
    assert all(r["hops"] == len(r["tx_ids"]) for r in rows)
