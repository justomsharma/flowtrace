"""Reset FlowTrace to a clean demo state: nothing uploaded, nothing investigated, nothing decided.

  graph   deletes every uploaded payment (TRANSFER edges with a batch id), accounts that only uploads created,
          and all case memory, actions and audit entries (old test approvals, provisional auto-holds). The
          history graph and its cutoff marker stay.
  disk    deletes data/runs/{batches,cases,results,plans,graphs} (uploads, investigations and hold plans).

The audit log is append-only while the app runs; a demo reset is the one thing that clears it.

Run (from backend/):
  python -m flowtrace.demo.reset                         this machine's graph and data/runs (API stopped)
  python -m flowtrace.demo.reset --url https://<host>    a running server, through POST /api/admin/reset
                                                         (asks for the approver token, or reads APPROVER_TOKEN)
  ... --url https://<host> --prerun 5                    then upload the curated sample and investigate case
                                                         C005 as the signed-in approver: the demo's backup case
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import time
import urllib.parse
import urllib.request

from flowtrace.graph.client import rw
from flowtrace.settings import settings

RUN_KINDS = ("batches", "cases", "results", "plans", "graphs")

CLEAN_GRAPH = [
    ("uploaded payments", "MATCH ()-[t:TRANSFER]->() WHERE t.batch IS NOT NULL DELETE t RETURN count(t) AS n"),
    ("accounts only uploads created", "MATCH (a:Account) WHERE a.bank IS NULL DETACH DELETE a RETURN count(a) AS n"),
    ("cases, actions and audit entries",
     "MATCH (n) WHERE n:Case OR n:Action OR n:Audit DETACH DELETE n RETURN count(n) AS n"),
]


def reset_graph() -> dict[str, int]:
    return {name: rw(q, timeout=60_000).rows[0]["n"] for name, q in CLEAN_GRAPH}


def reset_runs() -> int:
    runs = settings().raw_dir.parent / "runs"
    n = 0
    for kind in RUN_KINDS:
        d = runs / kind
        if d.exists():
            n += sum(1 for _ in d.glob("*.json"))
            shutil.rmtree(d)
    return n


def reset() -> dict:
    return {"graph": reset_graph(), "files_removed": reset_runs()}


CURATED = "demo_mixed_2022-09-09.csv"


def _call(url: str, path: str, method: str = "GET", token: str | None = None, form: dict | None = None) -> dict:
    data = urllib.parse.urlencode(form).encode() if form else None
    headers = {"X-Approver-Token": token} if token else {}
    if form:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def _wait(url: str, job_id: str, timeout_s: float = 600) -> str:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        status = _call(url, f"/api/jobs/{job_id}")["status"]
        if status in ("done", "error", "cancelled"):
            return status
        time.sleep(2)
    return "timeout"


def remote(url: str, token: str, prerun: int | None = None) -> dict:
    out = {"reset": _call(url, "/api/admin/reset", "POST", token)}
    if prerun:
        job = _call(url, "/api/batches", "POST", form={"sample": CURATED})["job_id"]
        out["upload"] = _wait(url, job)
        batch = _call(url, "/api/batches")[0]["batch_id"]
        case = f"{batch}-C{prerun:03d}"
        # As the signed-in approver: the LLM agent, and the auto-hold, as in the demo.
        job = _call(url, f"/api/cases/{case}/investigate", "POST", token)["job_id"]
        out["prerun"] = {"case": case, "status": _wait(url, job)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="reset a running server instead of this machine's graph and files")
    ap.add_argument("--prerun", type=int, help="with --url: upload the curated sample and investigate case N")
    a = ap.parse_args()
    if a.url:
        token = os.environ.get("APPROVER_TOKEN") or getpass.getpass("Approver token: ")
        print(json.dumps(remote(a.url, token, a.prerun), indent=2))
    else:
        print(json.dumps(reset(), indent=2))


if __name__ == "__main__":
    main()
