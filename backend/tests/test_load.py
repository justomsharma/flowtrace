"""The loader only returns once FalkorDB has finished building its indexes (queries scan the graph until then)."""
from flowtrace.graph.load import wait_for_indexes


def test_waits_until_every_index_is_operational():
    states = iter([["[Indexing] 10/50: UNDER CONSTRUCTION", "OPERATIONAL"],
                   ["[Indexing] 40/50: UNDER CONSTRUCTION", "OPERATIONAL"],
                   ["OPERATIONAL", "OPERATIONAL"]])
    calls = []

    def statuses():
        calls.append(1)
        return next(states)

    wait_for_indexes(statuses, poll_s=0, timeout_s=5)
    assert len(calls) == 3
