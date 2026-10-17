"""eval.compare: each row says which policy really ran, so llm results that fell back to rules are visible."""
from flowtrace.eval.compare import case_row, summarise


def result(action, policy, errors=(), author="gpt-6-luna", calls=7):
    return {"disposition": {"action": action, "risk": 0.5}, "graph": {"edges": []},
            "stats": {"steps": 6, "queries": 15, "seconds": 3.0, "policy": policy, "llm_calls": calls,
                      "llm_errors": list(errors)},
            "report": {"author": author, "validation": {"unsupported_rate": 0.0}}}


CASE = {"case_id": "B-C001", "ground_truth": 1, "edges": []}


def test_row_records_the_policy_that_ran_and_llm_errors():
    row = case_row(CASE, result("ESCALATE", "llm + heuristic fallback", ["rate limited: slow down"]), {})
    assert row["policy"] == "llm + heuristic fallback"
    assert row["llm_calls"] == 7 and row["llm_errors"] == ["rate limited: slow down"]
    assert row["report_author"] == "gpt-6-luna"


def test_summary_counts_llm_rows_that_fell_back():
    rows = [case_row(CASE, result("ESCALATE", "llm"), {}),
            case_row(CASE, result("ESCALATE", "llm + heuristic fallback", ["x"]), {}),
            case_row(CASE, result("REVIEW", "heuristic fallback", ["x"] * 7, author="template"), {})]
    s = summarise("llm", rows)
    assert s["fallback"] == {"rows_all_steps": 1, "rows_some_steps": 1, "template_reports": 1, "rows_with_llm_errors": 2}
    assert summarise("heuristic", [case_row(CASE, result("ESCALATE", "heuristic", calls=0), {})])["fallback"] is None
