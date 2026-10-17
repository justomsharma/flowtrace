"""The PDF case report: it builds for escalated, closed and hold-free cases, is never more than two pages, and shows
the case page's own numbers."""
import io

import pypdf
import pytest

from flowtrace.api import case_view as V
from flowtrace.api import pdf_report as R
from tests.test_case_view import RULES, ev, full_result, pay, result

H = 3_600_000
T0 = 1_662_717_600_000  # 9 Sep 2022 10:00 UTC
A, B, C = "011:800A1B2C0", "0212:80F739E30", "3402:80BCFF0D0"
CHAIN = [pay("t1", A, B, 17_387.25, ts=T0, cur="Euro"), pay("t2", B, C, 16_468.0, ts=T0 + 2 * H, fmt="Wire")]


def case(edges, **kw):
    accts = sorted({a for e in edges for a in (e["src"], e["dst"])})
    return {"case_id": "BX-C005", "batch_id": "BX", "primary": edges[0]["src"], "accounts": accts, "edges": edges,
            "t0": min(e["ts"] for e in edges), "t1": max(e["ts"] for e in edges) + 1, "case_score": 0.97,
            "amount": sum(e["amount"] for e in edges), "shape": {"typology": "STACK", "confidence": 0.6},
            "tier": "today", **kw}


def build(c, r, actions=(), audit=()):
    o = V.build_overview(c, r, None, list(actions), None, False, RULES)
    pdf = R.build_pdf(o, r, list(actions), list(audit), now_ms=T0 + 400 * 24 * H)
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    return o, len(reader.pages), "\n".join(p.extract_text() for p in reader.pages)


HOLD = {"id": "A1", "type": "HOLD", "status": "APPROVED", "account": C, "hold_amount": 16_468.0, "decided_by": "Asha",
        "decided_at": T0 + 5 * H, "reason": "Funds still in the account", "expires_ts": T0 + 100 * H}
AUDIT = [{"ts": T0 + 5 * H, "actor": "Asha", "event": "APPROVED", "action_id": "A1", "account": C,
          "reason": "Funds still in the account"},
         {"ts": T0 + 4 * H, "actor": "agent", "event": "PROPOSED", "action_id": "A1", "account": C,
          "reason": "Case money reached this account"}]


def escalated():
    r = full_result()
    r["evidence"] = [ev("E1", "support", 0.6, accounts=[B], code="PASS_THROUGH", claim="Passed on within 2 hours."),
                     ev("E2", "counter", 0.3, accounts=[A], code="ESTABLISHED_COUNTERPARTIES", claim="Known for 6 days.")]
    return r


def test_escalated_case_with_a_hold():
    o, pages, text = build(case(CHAIN), escalated(), [HOLD], AUDIT)
    assert pages == 2
    for part in ("BX-C005", "Case 5", "Layered relay", "Escalate", "Investigated by:", "AI (gpt-6-luna)",
                 "Reviewed by: Asha", "$33,855", "Amount (USD equivalent)", "2 h 0 min", "Account A", "Account C",
                 "0D0", "Money passed straight through", "Possible legitimate explanation", "Known counterparties",
                 "Holds", "$16,468", "Approved", "Asha", "Key payments", "$17,387", "EUR", "STR draft narrative",
                 "Who", "When", "Why", "How", "STR text.", "Audit trail", "Approved hold on Account C",
                 "decisions are made by a person", "IBM AMLworld HI-Small (CDLA-Sharing-1.0)", "Page 2 of 2"):
        assert part in text, part
    assert o["kpis"]["amount"] == 33_855.25  # the same number the case page shows, from the same overview


def test_closed_case_by_the_rules():
    r = result(action="CLOSE", author="template",
               evidence=[ev("E1", "counter", 0.6, accounts=[A], code="RECURRING_FLOWS", claim="Monthly payments.")])
    r["disposition"]["risk"] = 0.1
    _, pages, text = build(case(CHAIN), r)
    assert pages <= 2
    for part in ("Close", "Rules (no AI model)", "awaiting review", "No hold was proposed for this case.",
                 "No indicator was found", "Regular, scheduled payments", "No sign of laundering was found",
                 "No action has been recorded"):
        assert part in text, part


def test_case_with_no_holds_and_no_audit():
    r = result(action="REVIEW")
    r["disposition"]["risk"] = 0.4
    _, pages, text = build(case(CHAIN), r)
    assert pages <= 2 and "Review" in text and "No hold was proposed" in text


def test_a_large_case_still_fits_two_pages():
    accts = [f"{i:03d}:8000000{i:02d}0" for i in range(30)]
    edges = [pay(f"t{i}", accts[i % 6], accts[6 + i % 24], 1000.0 + i, ts=T0 + i * H, cur="US Dollar") for i in range(40)]
    r = escalated()
    r["evidence"] = [ev(f"E{i}", "support", 0.3 + i / 100, accounts=[accts[0]], code="NEW_COUNTERPARTIES",
                        claim=f"{i} of 9 counterparties are new.") for i in range(12)] + \
                    [ev(f"E{i}", "counter", 0.4, accounts=[accts[1]], code="FUNDS_STOP") for i in range(12, 18)]
    r["report"]["str_narrative"] = "Long narrative → with ₹ and ≈ symbols. " * 200
    holds = [{**HOLD, "id": f"A{i}", "account": accts[6 + i], "status": "PROPOSED", "decided_by": None}
             for i in range(15)]
    audit = [{**AUDIT[1], "action_id": f"A{i}", "account": accts[6 + i % 24], "ts": T0 + i} for i in range(30)]
    _, pages, text = build(case(edges), r, holds, audit)
    assert pages == 2
    for part in ("more findings", "more explanations", "more holds", "more payments", "more earlier entries"):
        assert part in text, part


def test_text_outside_the_pdf_fonts_never_breaks_it():
    assert R.plain("A → B ≈ ₹5") == "A -> B ~ INR 5"
    assert R.short("0212:80F739E30") == "…9E30"  # the UI's shortAcct
    assert R.money(12_345_678) == "$12.3M" and R.money(9_999_999) == "$9,999,999"  # the UI's fmtMoney
    assert R.case_no("BC0C047-C005") == "5"


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient
    from flowtrace.api import main as M
    monkeypatch.setattr(M, "CASES", {"BX-C005": case(CHAIN)})
    monkeypatch.setattr(M, "RESULTS", {})
    monkeypatch.setattr(M, "BATCHES", {})
    monkeypatch.setattr(M.store, "load_plan", lambda cid: None)
    calls = []
    monkeypatch.setattr(M.service, "list_actions", lambda **kw: calls.append(kw) or [HOLD])
    monkeypatch.setattr(M.service, "audit", lambda **kw: calls.append(kw) or AUDIT)
    return TestClient(M.app), M, calls


def test_pdf_endpoint(api):
    client, M, calls = api
    assert client.get("/api/cases/BX-C999/report.pdf").status_code == 404
    assert client.get("/api/cases/BX-C005/report.pdf").status_code == 409  # not investigated yet
    M.RESULTS["BX-C005"] = escalated()
    r = client.get("/api/cases/BX-C005/report.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert 'filename="BX-C005-report.pdf"' in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF") and len(pypdf.PdfReader(io.BytesIO(r.content)).pages) == 2
    assert calls and all(kw.get("expire") is False for kw in calls)  # read-only: no expiry write


def test_pdf_endpoint_survives_the_graph_being_down(api, monkeypatch):
    client, M, _ = api
    M.RESULTS["BX-C005"] = escalated()

    def down(**kw):
        raise ConnectionError("graph down")
    monkeypatch.setattr(M.service, "list_actions", down)
    monkeypatch.setattr(M.service, "audit", down)
    assert client.get("/api/cases/BX-C005/report.pdf").status_code == 200
