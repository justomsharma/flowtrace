"""eval.funnel must never delete a batch it did not create (e.g. the same demo file uploaded from the UI)."""
from flowtrace.eval import funnel as F


def test_keeps_a_batch_that_was_already_in_the_graph(monkeypatch):
    deleted = []
    monkeypatch.setattr(F, "rw", lambda c, p, **k: deleted.append(p["batch"]))
    F.cleanup("BAAA111", existed_before=True)
    assert deleted == []
    F.cleanup("BAAA111", existed_before=False)
    assert deleted == ["BAAA111"]
