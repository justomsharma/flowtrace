"""process_batch wires calibration, case building and capacity tiers into the batch summary."""
import numpy as np

from flowtrace.graph.client import QueryResult
from flowtrace.pipeline import batch as B

CSV = ("Timestamp,From Bank,Account,To Bank,Account,Amount Received,Receiving Currency,Amount Paid,"
       "Payment Currency,Payment Format,Is Laundering\n"
       "2022/09/09 10:00,001,AAA,002,BBB,5000.00,US Dollar,5000.00,US Dollar,ACH,1\n"
       "2022/09/09 11:00,002,BBB,003,CCC,4900.00,US Dollar,4900.00,US Dollar,ACH,1\n"
       "2022/09/09 12:00,004,DDD,005,EEE,20.00,US Dollar,20.00,US Dollar,Credit Card,0\n").encode()


def test_summary_has_ranked_tiered_cases(monkeypatch):
    monkeypatch.setattr(B, "score", lambda batch: np.array([0.99, 0.98, 0.0]))
    monkeypatch.setattr(B, "rw", lambda *a, **k: None)
    monkeypatch.setattr(B, "run_detectors", lambda *a, **k: [])
    monkeypatch.setattr(B, "ro", lambda c, p, **k: QueryResult(
        rows=[{"id": i, "out_deg": 1, "in_deg": 1} for i in p["ids"]], cypher=c, params=p, ms=0.1))
    monkeypatch.setattr(B, "calibrate", lambda p, rules: np.asarray(p))

    out = B.process_batch(CSV, "t.csv", lambda e: None)
    s = out["summary"]
    assert s["batch_id"] == B.batch_id_for(CSV)
    assert s["capacity"] == B.cfg()["cases"]["daily_capacity"]
    assert len(s["cases"]) == 1 and s["flagged_tx"] == 2
    c = s["cases"][0]
    assert c["tier"] == "today" and c["case_score"] >= 0.98 and c["signals"][0]["signal"] == "model"
    assert "priority" not in c
