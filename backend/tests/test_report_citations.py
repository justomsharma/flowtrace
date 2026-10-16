"""The STR narrative keeps its evidence citations, and only citations of evidence that exists."""
from flowtrace.agent.report import known_citations


def test_unknown_evidence_ids_are_dropped_from_the_narrative():
    text, bad = known_citations("A paid B [E1, E9]. B sent it on [E7]. C kept it [E2].", {"E1", "E2"})
    assert text == "A paid B [E1]. B sent it on. C kept it [E2]."
    assert bad == ["E9", "E7"]
