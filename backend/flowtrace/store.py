"""Read-only access to what the API saved under data/runs (cases, investigation results, batches) plus
saved hold plans. The MCP server reads cases from here, so it needs no running API process."""
from __future__ import annotations

import json

from flowtrace.settings import settings

RUNS = settings().raw_dir.parent / "runs"


def _read(kind: str, key: str) -> dict | None:
    f = RUNS / kind / f"{key}.json"
    if f.parent.resolve() != (RUNS / kind).resolve() or not f.exists():  # never read outside data/runs
        return None
    return json.loads(f.read_text())


def load_case(case_id: str) -> dict | None:
    return _read("cases", case_id)


def load_result(case_id: str) -> dict | None:
    return _read("results", case_id)


def load_plan(case_id: str) -> dict | None:
    return _read("plans", case_id)


def save_plan(case_id: str, plan: dict) -> None:
    d = RUNS / "plans"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{case_id}.json").write_text(json.dumps(plan, default=str))


def list_cases(batch_id: str | None = None) -> list[dict]:
    """Every saved case, newest upload first and ranked within it, with the investigation's decision if any."""
    batches = [json.loads(f.read_text()) for f in (RUNS / "batches").glob("*.json")] if (RUNS / "batches").exists() else []
    out = []
    for b in sorted(batches, key=lambda b: -b.get("uploaded_at", 0)):
        if batch_id and b["batch_id"] != batch_id:
            continue
        for rank, c in enumerate(b["cases"], 1):
            r = load_result(c["case_id"])
            out.append({"case_id": c["case_id"], "batch_id": b["batch_id"], "rank": rank, "tier": c.get("tier"),
                        "case_score": c.get("case_score"), "typology": (c.get("shape") or {}).get("typology"),
                        "accounts": len(c.get("accounts", [])), "amount_usd": c.get("amount"),
                        "disposition": r["disposition"]["action"] if r else None})
    return out
