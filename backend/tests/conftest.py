import pytest

from flowtrace.agent import llm


@pytest.fixture(autouse=True)
def llm_spend_file(tmp_path, monkeypatch):
    """Tests never count toward (or read) the real daily LLM spend in data/runs."""
    f = tmp_path / "llm_spend.json"
    monkeypatch.setattr(llm, "_spend_file", lambda: f)
    return f
