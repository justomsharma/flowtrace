"""FlowTrace API.

Run:  uvicorn flowtrace.api.main:app --port 8000   (from backend/)
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import multiprocessing
import os
import threading
import time
from contextlib import asynccontextmanager
from functools import partial
from typing import Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from dotenv import dotenv_values
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from flowtrace import store
from flowtrace.actions import planner, service
from flowtrace.agent import registry
from flowtrace.agent.investigate import save_case_memory
from flowtrace.api import case_view, jobs
from flowtrace.api.overview import build_overview
from flowtrace.api.runner import CaseRunner
from flowtrace.pipeline.cases import batch_of
from flowtrace.graph import queries as Q
from flowtrace.graph.client import rw
from flowtrace.settings import ROOT, cfg, settings

@asynccontextmanager
async def lifespan(_app: FastAPI):
    if should_requeue():
        threading.Thread(target=requeue_unfinished, daemon=True).start()
    else:
        log.info("not re-queuing unfinished cases in this process (set FLOWTRACE_REQUEUE=1 to force)")
    stop = threading.Event()
    threading.Thread(target=release_due_holds, args=(stop,), daemon=True).start()
    yield
    stop.set()
    RUNNER.shutdown()  # cancel queued work instead of finishing it (and paying for it) on the way out


app = FastAPI(title="FlowTrace", version="0.2.0", lifespan=lifespan)
log = logging.getLogger("uvicorn.error")  # shown in the server console
_UNSET = object()


def should_requeue(parent=_UNSET) -> bool:
    """Re-queue unfinished top cases on startup only in a server started directly. Under `uvicorn --reload`
    (or --workers) the app runs in a child process that restarts on every code change; re-queuing there
    would pay for the same investigations again and again. FLOWTRACE_REQUEUE=1/0 overrides."""
    env = os.environ.get("FLOWTRACE_REQUEUE")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "")
    if parent is _UNSET:
        parent = multiprocessing.parent_process()
    return parent is None
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLES = settings().raw_dir.parent / "samples"
RUNS = settings().raw_dir.parent / "runs"
BATCHES: dict[str, dict] = {}   # batch_id -> summary
CASES: dict[str, dict] = {}     # case_id -> case input (from the batch pipeline)
RESULTS: dict[str, dict] = {}   # case_id -> investigation result
RUNNER = CaseRunner(parallel=cfg()["agent"]["prefetch_parallel"])


def _persist(kind: str, key: str, obj: dict) -> None:
    d = RUNS / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps(obj, default=str))


def _restore() -> None:
    for kind, store in (("batches", BATCHES), ("cases", CASES), ("results", RESULTS)):
        for f in (RUNS / kind).glob("*.json") if (RUNS / kind).exists() else []:
            store[f.stem] = json.loads(f.read_text())
            if kind == "batches":
                store[f.stem].setdefault("uploaded_at", f.stat().st_mtime)  # stored before uploads were timed


_restore()


MAX_UPLOAD_MB = 50
TOO_LARGE = (f"File too large ({MAX_UPLOAD_MB} MB max). A full HI-Small day is about 61 MB: evaluate full days offline "
             f"with `python -m flowtrace.eval.fullday --day YYYY-MM-DD` (from backend/).")


def is_curated_sample(name: str) -> bool:
    """Demo files from data/samples are hand-picked around known laundering (~29x a real day's rate)."""
    p = (SAMPLES / name).resolve()
    return p.parent == SAMPLES.resolve() and p.exists()


def release_due_holds(stop: threading.Event) -> None:
    """Approved holds end on their own when their time is up, even if nobody opens the app."""
    while not stop.wait(cfg()["actions"]["expiry_check_seconds"]):
        try:
            if n := service.expire_due():
                log.info("released %d hold(s) whose time was up", n)
        except Exception as e:  # the graph may be restarting; try again next time
            log.warning("hold expiry check failed: %s", e)


def forget_batch(batch_id: str) -> None:
    """Drop a batch's earlier cases, investigation results, hold plans and case memory before it is stored
    again, and withdraw its undecided proposals. Case ids are rank-based (B..-C001), so after a retune an old
    result would otherwise attach to a different case. Decided actions are retired from the renumbered cases;
    approved holds keep running (that money is really held), and cases with a person's verdict stay in case
    memory under a retired id."""
    prefix = f"{batch_id}-C"
    for kind, mem in (("cases", CASES), ("results", RESULTS)):
        for cid in [c for c in mem if c.startswith(prefix)]:
            del mem[cid]
            (RUNS / kind / f"{cid}.json").unlink(missing_ok=True)
    for f in (RUNS / "plans").glob(f"{prefix}*.json") if (RUNS / "plans").exists() else []:
        f.unlink(missing_ok=True)
    rw(Q.DELETE_BATCH_CASES, {"prefix": prefix, "now": int(time.time() * 1000)})
    service.retire_batch(prefix, "The file was uploaded again, so its cases were renumbered")


@app.get("/api/health")
def health() -> dict:
    graph_ok = False
    try:
        from flowtrace.graph.client import ro
        ro("RETURN 1 AS ok", timeout=1000)
        graph_ok = True
    except Exception:
        pass
    from flowtrace.agent.llm import available, model_name
    return {"api": True, "graph": graph_ok, "llm": available(), "model": model_name()}


@app.get("/api/overview")
def overview() -> dict:
    return build_overview()


@app.get("/api/model")
def model() -> dict:
    d = settings().models_dir
    if not (d / "triage_metrics.json").exists():
        raise HTTPException(404, "Model not trained yet: run python -m flowtrace.ml.train")
    out = {"metrics": json.loads((d / "triage_metrics.json").read_text())}
    for key, name in (("curves", "triage_curves.json"), ("typology", "typology_metrics.json"),
                      ("agent_eval", "agent_eval.json")):
        if (d / name).exists():
            out[key] = json.loads((d / name).read_text())
    return out


# --- samples ---------------------------------------------------------------------------------

@app.get("/api/samples")
def samples() -> list[dict]:
    return [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(SAMPLES.glob("*.csv"))]


@app.get("/api/samples/{name}")
def sample_file(name: str):
    p = (SAMPLES / name).resolve()
    if p.parent != SAMPLES.resolve() or not p.exists():
        raise HTTPException(404, "no such sample")
    return FileResponse(p, media_type="text/csv", filename=name)


# --- batches ---------------------------------------------------------------------------------

@app.post("/api/batches")
async def create_batch(file: UploadFile | None = File(None), sample: str | None = Form(None)) -> dict:
    if file is not None:
        data, name = await file.read(), file.filename or "upload.csv"
    elif sample:
        p = (SAMPLES / sample).resolve()
        if p.parent != SAMPLES.resolve() or not p.exists():
            raise HTTPException(404, "no such sample")
        data, name = p.read_bytes(), sample
    else:
        raise HTTPException(400, "upload a CSV file or choose a sample")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, TOO_LARGE)

    from flowtrace.pipeline.batch import process_batch

    job = jobs.start("batch", lambda emit: process_batch(data, name, emit), lambda result: accept_batch(result, name))
    return {"job_id": job.id}


def accept_batch(result: dict, name: str) -> None:
    """Store a processed upload, then investigate its top cases in the background."""
    s = result["summary"]
    # A result from the previous run of this file is either stored before this, or dropped. The queued
    # jobs exist before the upload's "done" event, so the UI can join them as soon as it opens a case.
    with RUNNER.batch_lock(s["batch_id"]):
        RUNNER.new_run(s["batch_id"])
        forget_batch(s["batch_id"])
        s["curated"] = is_curated_sample(name)
        s["uploaded_at"] = time.time()
        BATCHES[s["batch_id"]] = s
        CASES.update(result["cases"])
        _persist("batches", s["batch_id"], s)
        _persist("graphs", s["batch_id"], {"links": result["graph"]})
        for cid, c in result["cases"].items():
            _persist("cases", cid, c)
        prefetch(s["batch_id"])


# --- background investigations -----------------------------------------------------------------

def top_cases(batch_id: str) -> list[str]:
    return [c["case_id"] for c in BATCHES[batch_id]["cases"][: cfg()["agent"]["prefetch_top"]]]


def _investigation(case_id: str, policy: str):
    """The work for one case. policy "auto" is decided when the job starts: the LLM if its key works,
    the rules otherwise (so creating the job never waits for the key check)."""
    case = CASES[case_id]

    def run(emit):
        from flowtrace.agent.investigate import investigate, new_context
        from flowtrace.agent.llm import available
        p = ("llm" if available() else "heuristic") if policy == "auto" else policy
        # Case memory is written only once the result is accepted for the current upload (_store_result).
        return investigate(new_context(case, cfg(), emit), policy_name=p, save_memory=False)
    return run


def _store_result(case_id: str, result: dict) -> None:
    save_case_memory(result, cfg())
    RESULTS[case_id] = result
    _persist("results", case_id, result)
    try:  # the agent acts: plan the holds and propose them for a person to decide
        registry.act(case_id)
    except Exception as e:
        log.warning("could not plan holds for %s: %s", case_id, e)


def prefetch(batch_id: str) -> list[str]:
    """Queue the top cases that have no result and no job yet."""
    started = []
    with RUNNER.batch_lock(batch_id):  # same lock as accept_batch: never reads a case being renumbered
        for cid in top_cases(batch_id):
            if cid in RESULTS or cid not in CASES or RUNNER.active(cid):
                continue
            RUNNER.start(cid, _investigation(cid, "auto"), partial(_store_result, cid), queued=True)
            started.append(cid)
    return started


def requeue_unfinished() -> list[str]:
    """After a restart: the top cases of every stored batch that never got a finished result, newest upload first."""
    newest = sorted(list(BATCHES.items()), key=lambda kv: -kv[1].get("uploaded_at", 0))
    return [cid for bid, _ in newest for cid in prefetch(bid)]


def _failed(case_id: str) -> bool:
    return case_id not in RESULTS and RUNNER.failed(case_id)


def _job_brief(case_id: str) -> dict | None:
    j = RUNNER.active(case_id)
    return {"id": j.id, "status": j.status} if j else None


@app.get("/api/batches")
def list_batches() -> list[dict]:
    return sorted(({k: v for k, v in b.items() if k != "cases"} | {"n_cases": len(b["cases"])} for b in BATCHES.values()),
                  key=lambda b: -b["window"][0])


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict:
    if batch_id not in BATCHES:
        raise HTTPException(404, "no such batch")
    b = dict(BATCHES[batch_id])
    b["cases"] = [c | {"result": _result_brief(c["case_id"]), "job": _job_brief(c["case_id"]),
                       "failed": _failed(c["case_id"])} | _currency(c["case_id"]) for c in b["cases"]]
    top = top_cases(batch_id)
    b["prefetch"] = {"total": len(top), "done": sum(cid in RESULTS for cid in top), "failed": sum(map(_failed, top))}
    return b


@app.get("/api/batches/{batch_id}/graph")
def batch_graph(batch_id: str) -> dict:
    f = RUNS / "graphs" / f"{batch_id}.json"
    if not f.exists():
        raise HTTPException(404, "no graph for that batch")
    return json.loads(f.read_text())


def _currency(case_id: str) -> dict:
    """The currency of the case's own payments (US dollars when mixed or unknown), so the queue shows the case
    amount the way the case page does."""
    plan = store.load_plan(case_id)
    cur = case_view.case_currency(CASES.get(case_id, {}), plan)
    return {"currency": cur, "rate": case_view.case_rate(cur, plan)}


def _result_brief(case_id: str) -> dict | None:
    r = RESULTS.get(case_id)
    if not r:
        return None
    return {"action": r["disposition"]["action"], "risk": r["disposition"]["risk"],
            "confidence": r["disposition"]["confidence_label"]}


# --- cases -----------------------------------------------------------------------------------

@app.post("/api/cases/{case_id}/investigate")
def investigate_case(case_id: str, policy: Literal["llm", "heuristic", "fixed"] = "llm") -> dict:
    # Under the batch lock: a re-upload cannot renumber the case between reading it and starting its job.
    with RUNNER.batch_lock(batch_of(case_id) or case_id):
        if case_id not in CASES:
            raise HTTPException(404, "no such case")
        # Joins the case's queued or running investigation if there is one; otherwise starts now.
        job = RUNNER.start(case_id, _investigation(case_id, policy), partial(_store_result, case_id))
    return {"job_id": job.id}


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    if case_id not in CASES:
        raise HTTPException(404, "no such case")
    c = CASES[case_id]
    return {"case": c, "result": RESULTS.get(case_id), "job": _job_brief(case_id)}


def _case_actions(case_id: str) -> list[dict]:
    """Read-only: the expiry timer releases due holds, so a page poll never writes."""
    try:
        return service.list_actions(case_id=case_id, expire=False)
    except Exception as e:  # the graph may be restarting: the page still shows the case, without its actions
        log.warning("could not read actions for %s: %s", case_id, e)
        return []


@app.get("/api/cases/{case_id}/overview")
def case_overview(case_id: str) -> dict:
    """Everything the case page shows (read-only): KPIs, the flow with account roles, timeline, distribution,
    payments, findings, the summary and the state of its investigation. Built from the stored case, result and
    plan, plus a read-only listing of the case's actions."""
    if case_id not in CASES:
        raise HTTPException(404, "no such case")
    c = CASES[case_id]
    return case_view.build_overview(c, RESULTS.get(case_id), store.load_plan(case_id), _case_actions(case_id),
                                    _job_brief(case_id), _failed(case_id), cfg(),
                                    uploaded_at=BATCHES.get(c.get("batch_id") or "", {}).get("uploaded_at"))


@app.get("/api/cases/{case_id}/report.md")
def case_report(case_id: str) -> Response:
    """The STR draft as Markdown, for a person to review before filing."""
    if case_id not in CASES:
        raise HTTPException(404, "no such case")
    if case_id not in RESULTS:
        raise HTTPException(409, "investigate the case first: the STR draft comes from the investigation")
    md = case_view.str_markdown(CASES[case_id], RESULTS[case_id], store.load_plan(case_id), _case_actions(case_id))
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{case_id}-STR-draft.md"'})


# --- actions: the agent proposes, a person decides ------------------------------------------------

class Decision(BaseModel):
    reason: str
    by: str  # the approver's name, recorded in the audit log

    @field_validator("by")
    @classmethod
    def _named(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("say who is deciding")
        return v.strip()


def approver_token() -> str | None:
    """APPROVER_TOKEN from the environment or .env. Only this API process reads it; the MCP server drops it."""
    return os.environ.get("APPROVER_TOKEN") or dotenv_values(ROOT / ".env").get("APPROVER_TOKEN") or None


def _check_approver(token: str | None) -> None:
    expected = approver_token()
    if not expected:
        raise HTTPException(403, "approvals are switched off on this server: set APPROVER_TOKEN in .env")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "approver token missing or wrong")


@app.post("/api/cases/{case_id}/plan")
def plan_case(case_id: str, idempotency_key: str | None = Header(None)) -> dict:
    """Plan holds for an investigated case and propose them. Safe to repeat: proposals are merged on case,
    type and account, so a double click adds nothing (the Idempotency-Key header is accepted but not needed)."""
    if case_id not in CASES:
        raise HTTPException(404, "no such case")
    if case_id not in RESULTS:
        raise HTTPException(409, "investigate the case first: holds follow from the investigation")
    return registry.act(case_id)


@app.get("/api/cases/{case_id}/plan")
def get_plan(case_id: str) -> dict:
    """The saved plan, the case's current actions, and what the holds still open (proposed or approved) freeze,
    plus the money on the case's routes they would also stop (an assumption). Recounted after every decision."""
    plan, actions = store.load_plan(case_id), service.list_actions(case_id=case_id)
    held = [a["account"] for a in actions if a["type"] == "HOLD" and a["status"] in ("PROPOSED", "APPROVED")]
    return {"plan": plan, "actions": actions, "open_effect": planner.effect(plan, held) if plan else None}


@app.get("/api/actions")
def list_actions(status: str | None = None, case_id: str | None = None) -> list[dict]:
    return service.list_actions(case_id=case_id, status=status)


def _decide(action_id: str, approve: bool, d: Decision, key: str | None, token: str | None) -> dict:
    _check_approver(token)
    try:
        return service.decide(action_id, approve, d.reason, by=d.by, key=key, auth="approver-token")
    except service.ActionError as e:
        raise HTTPException(e.status, str(e)) from e


@app.post("/api/actions/{action_id}/approve")
def approve_action(action_id: str, d: Decision, idempotency_key: str | None = Header(None),
                   x_approver_token: str | None = Header(None)) -> dict:
    return _decide(action_id, True, d, idempotency_key, x_approver_token)


@app.post("/api/actions/{action_id}/reject")
def reject_action(action_id: str, d: Decision, idempotency_key: str | None = Header(None),
                  x_approver_token: str | None = Header(None)) -> dict:
    return _decide(action_id, False, d, idempotency_key, x_approver_token)


@app.get("/api/audit")
def audit_log(case_id: str | None = None, limit: int = 200) -> list[dict]:
    return service.audit(case_id=case_id, limit=min(max(limit, 1), 2000))


# --- job events (SSE) ------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    j = jobs.JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    return {"id": j.id, "kind": j.kind, "status": j.status, "error": j.error, "events": len(j.events)}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, since: int = 0):
    j = jobs.JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")

    async def gen():
        i = since
        while True:
            while i < len(j.events):
                yield {"event": "message", "data": json.dumps(j.events[i], default=str)}
                i += 1
            if j.status in jobs.TERMINAL and i >= len(j.events):
                break
            await asyncio.sleep(0.08)

    return EventSourceResponse(gen())


@app.get("/api/config")
def config() -> dict:
    return cfg()
