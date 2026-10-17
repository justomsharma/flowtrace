"""Re-uploading a batch forgets its old cases, results and case memory, so nothing stale attaches to new cases."""
import json

from flowtrace.api import main as M
from flowtrace.graph import queries as Q


def test_reupload_forgets_old_cases_results_and_case_nodes(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "RUNS", tmp_path)
    monkeypatch.setattr(M, "CASES", {"BX-C001": {"case_id": "BX-C001"}, "BY-C001": {"case_id": "BY-C001"}})
    monkeypatch.setattr(M, "RESULTS", {"BX-C001": {"case_id": "BX-C001"}})
    for kind in ("cases", "results"):
        (tmp_path / kind).mkdir()
        (tmp_path / kind / "BX-C001.json").write_text(json.dumps({}))
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "BX-C001.json").write_text(json.dumps({}))
    calls, withdrawn = [], []
    monkeypatch.setattr(M, "rw", lambda c, p, **k: calls.append((c, p)))
    monkeypatch.setattr(M.service, "retire_batch", lambda prefix, reason: withdrawn.append(prefix) or {})

    M.forget_batch("BX")

    assert list(M.CASES) == ["BY-C001"] and M.RESULTS == {}
    assert not (tmp_path / "cases" / "BX-C001.json").exists() and not (tmp_path / "results" / "BX-C001.json").exists()
    assert not (tmp_path / "plans" / "BX-C001.json").exists()
    assert [(c, p["prefix"]) for c, p in calls] == [(Q.DELETE_BATCH_CASES, "BX-C")]
    assert withdrawn == ["BX-C"]  # proposals withdrawn; decided actions retired from the renumbered cases


def test_save_case_replaces_its_account_links():
    assert "DELETE old" in Q.SAVE_CASE


def test_only_files_from_the_samples_folder_count_as_curated(monkeypatch, tmp_path):
    (tmp_path / "demo_mixed_2022-09-09.csv").write_text("x")
    monkeypatch.setattr(M, "SAMPLES", tmp_path)
    assert M.is_curated_sample("demo_mixed_2022-09-09.csv")
    assert not M.is_curated_sample("bank_feed_2022-09-09.csv")
    assert not M.is_curated_sample("../config.yaml")


def test_too_large_upload_points_to_the_offline_full_day_eval():
    assert "flowtrace.eval.fullday" in M.TOO_LARGE
