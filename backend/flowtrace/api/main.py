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

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import dotenv_values
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse
from starlette.routing import Route

from flowtrace import store
from flowtrace.actions import planner, service
from flowtrace.agent import registry
from flowtrace.agent.investigate import save_case_memory
from flowtrace.api import case_view, guard, jobs, pdf_report
from flowtrace.api.overview import build_overview
from flowtrace.api.runner import CaseRunner
from flowtrace.pipeline.cases import batch_of
from flowtrace.graph import queries as Q
from flowtrace.graph.client import rw
from flowtrace.ml import signal_rates
from flowtrace.settings import ROOT, cfg, settings

@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop = threading.Event()
    if signal_rates.enabled(cfg()):
        signal_rates.rates()  # measured weights on without their file: refuse to start, never score silently without
    if guard.demo_mode():  # in the background: an unreachable graph must never stop (or crash-loop) the server
        threading.Thread(target=watch_demo_history, args=(stop,), daemon=True).start()
    if should_requeue():
        threading.Thread(target=requeue_unfinished, daemon=True).start()
    else:
        log.info("not re-queuing unfinished cases in this process (set FLOWTRACE_REQUEUE=1 to force)")
    threading.Thread(target=release_due_holds, args=(stop,), daemon=True).start()
    mcp_app = _mcp_app()  # its session manager runs once per app instance: a new one for each start
    MCP_GUARD.app = mcp_app.routes[0].endpoint
    async with mcp_app.router.lifespan_context(mcp_app):
        yield
    MCP_GUARD.app = None
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
app.add_middleware(guard.BodyLimit)  # before anything reads a body
app.add_middleware(
    CORSMiddleware,
    # The deployed app serves its own frontend (same origin); CORS_ORIGINS adds others, comma-separated.
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"]
                  + [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()],
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


# --- public demo: a clean state, and the demo history graph ------------------------------------------

def clear_demo_state() -> int:
    """Forget every upload, case and investigation (memory and data/runs). Running investigations are cancelled;
    a result that still arrives is dropped because its upload run moved on."""
    from flowtrace.demo.reset import reset_runs
    for bid in list(BATCHES):
        with RUNNER.batch_lock(bid):
            RUNNER.new_run(bid)
    BATCHES.clear()
    CASES.clear()
    RESULTS.clear()
    return reset_runs()


def reset_demo() -> dict:
    """Back to a clean demo: no uploads, no investigations, nothing decided (flowtrace.demo.reset)."""
    from flowtrace.demo.reset import reset_graph
    return {"graph": reset_graph(), "files_removed": clear_demo_state()}


HISTORY_WATCH = None   # flowtrace.demo.history.HistoryWatch, made on first use
_history_lock = threading.Lock()


def ensure_demo_history() -> str:
    """One check of the demo history graph. It is loaded (and the stored state cleared, since it no longer matches
    the graph) only when FalkorDB answers that it is missing, three checks in a row. An error is only logged: it
    never clears anything. A failed load backs off. Returns the watch's verdict."""
    global HISTORY_WATCH
    from flowtrace.demo import history
    with _history_lock:
        if HISTORY_WATCH is None:
            HISTORY_WATCH = history.HistoryWatch()
        verdict = HISTORY_WATCH.check()
        if verdict == "unreachable":
            log.error("graph database unreachable: demo history not checked (nothing is cleared)")
        if verdict != "reload":
            return verdict
        log.warning("demo history graph missing (3 checks in a row): loading data/demo/history and starting clean")
        try:
            clear_demo_state()
            log.info("demo history loaded: %s", history.load())
            HISTORY_WATCH.loaded()
            return "loaded"
        except Exception as e:  # the graph went away mid-load: try again later, never in a tight loop
            HISTORY_WATCH.failed()
            log.error("loading the demo history failed (next try in %.0f s): %s", HISTORY_WATCH.wait_s, e)
            return "failed"


def watch_demo_history(stop: threading.Event, fast_s: float = 5, slow_s: float = 60) -> None:
    """Check every few seconds while the history looks missing (a new graph loads within ~15 s of the first
    start), otherwise every minute. Never raises."""
    every = fast_s
    while not stop.is_set():
        try:
            every = fast_s if ensure_demo_history() == "missing" else slow_s
        except Exception as e:
            log.error("demo history check failed: %s", e)
        stop.wait(every)


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
    from flowtrace.agent.llm import available, model_name, spend
    return {"api": True, "graph": graph_ok, "llm": available(), "model": model_name(), "auto_hold": auto_hold_policy(),
            "demo": guard.demo_mode(), "llm_budget": {k: spend()[k] for k in ("usd", "budget_usd", "over")}}


@app.get("/healthz")
def healthz() -> dict:
    """Liveness for the host's health check: the process answers. /api/health also checks the graph and LLM."""
    return {"ok": True}


def auto_hold_policy() -> dict:
    """Whether the agent may hold money provisionally on this server (actions.auto_hold on AND an APPROVER_TOKEN,
    so a person can confirm or release it), and when. The UI states it next to every "nothing is frozen" line."""
    r = cfg()["actions"].get("auto_hold") or {}
    return {"active": bool(r.get("enabled")) and approver_token() is not None,
            "min_confidence": r.get("min_confidence"), "confirm_business_days": r.get("confirm_business_days")}


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

DEMO_ONLY = ("This public demo only accepts the bundled sample files (try the sample day). "
             "Run FlowTrace locally to upload your own payments.")


def bundled_sample(data: bytes) -> str | None:
    """The name of the bundled sample with exactly these bytes, if any."""
    return next((p.name for p in sorted(SAMPLES.glob("*.csv"))
                 if p.stat().st_size == len(data) and p.read_bytes() == data), None)


@app.post("/api/batches")
async def create_batch(request: Request, file: UploadFile | None = File(None), sample: str | None = Form(None)) -> dict:
    if file is not None:
        data, name = await file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1), file.filename or "upload.csv"
    elif sample:
        p = (SAMPLES / sample).resolve()
        if p.parent != SAMPLES.resolve() or not p.exists():
            raise HTTPException(404, "no such sample")
        data, name = p.read_bytes(), sample
    else:
        raise HTTPException(400, "upload a CSV file or choose a sample")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, TOO_LARGE)

    from flowtrace.pipeline.batch import batch_id_for, process_batch

    if guard.demo_mode():
        name = bundled_sample(data)
        if name is None:
            raise HTTPException(403, DEMO_ONLY)
        if batch_id_for(data) in BATCHES:  # already uploaded: open it as it is, never renumber someone's demo
            return {"job_id": jobs.start("batch", partial(reopen_batch, batch_id_for(data))).id}
    guard.limit(request, "uploads")
    job = jobs.start("batch", lambda emit: process_batch(data, name, emit), lambda result: accept_batch(result, name))
    return {"job_id": job.id}


def reopen_batch(batch_id: str, emit) -> None:
    """The events of an upload for a batch that is already stored, so the upload screen and the Live Network
    finish the same way without processing the file again."""
    emit({"type": "batch", "batch_id": batch_id})
    f = RUNS / "graphs" / f"{batch_id}.json"
    if f.exists():
        emit({"type": "graph_rows", "links": json.loads(f.read_text())["links"]})
    for stage in ("parse", "score", "graph", "detect", "cases"):
        emit({"type": "stage", "stage": stage, "status": "done", "reused": True})


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


def _store_result(case_id: str, result: dict, started_by_person: bool = False) -> None:
    """Accept a finished investigation, then act. Only an investigation a person started (POST .../investigate)
    may auto-hold; background runs (prefetch after an upload, re-queue after a restart) only propose."""
    save_case_memory(result, cfg())
    RESULTS[case_id] = result
    _persist("results", case_id, result)
    try:  # the agent acts: plan the holds and propose them for a person to decide
        registry.act(case_id, auto=started_by_person, approvals_on=approver_token() is not None)
    except Exception as e:
        log.warning("could not plan holds for %s: %s", case_id, e)


def prefetch_on() -> bool:
    """Background investigations after an upload; off on the public demo (config.yaml demo.prefetch)."""
    return not guard.demo_mode() or bool((cfg().get("demo") or {}).get("prefetch", True))


def prefetch(batch_id: str) -> list[str]:
    """Queue the top cases that have no result and no job yet."""
    if not prefetch_on():
        return []
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
    top = top_cases(batch_id) if prefetch_on() else []
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
def investigate_case(case_id: str, request: Request, policy: Literal["llm", "heuristic", "fixed"] = "llm",
                     x_approver_token: str | None = Header(None)) -> dict:
    """Start (or join) a case's investigation. On the public demo an anonymous visitor gets the rule-based agent
    and its run never auto-holds; the signed-in approver (X-Approver-Token) gets the LLM agent and the auto-hold."""
    person, approver = True, False
    if guard.demo_mode():
        if x_approver_token:
            _check_approver(x_approver_token, request)
            approver = True
        else:
            policy, person = "heuristic", False
    if case_id in CASES and not RUNNER.active(case_id):  # joining a running investigation costs nothing
        guard.limit(request, "investigations", per_ip=not approver)  # the presenter is never locked out by a visitor
    # Under the batch lock: a re-upload cannot renumber the case between reading it and starting its job.
    with RUNNER.batch_lock(batch_of(case_id) or case_id):
        if case_id not in CASES:
            raise HTTPException(404, "no such case")
        # Joins the case's queued or running investigation if there is one; otherwise starts now.
        job = RUNNER.start(case_id, _investigation(case_id, policy),
                           partial(_store_result, case_id, started_by_person=person))
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
    return _overview(case_id, _case_actions(case_id))


def _overview(case_id: str, actions: list[dict]) -> dict:
    c = CASES[case_id]
    return {**case_view.build_overview(c, RESULTS.get(case_id), store.load_plan(case_id), actions,
                                       _job_brief(case_id), _failed(case_id), cfg(),
                                       uploaded_at=BATCHES.get(c.get("batch_id") or "", {}).get("uploaded_at")),
            "auto_hold": auto_hold_policy()}


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


@app.get("/api/cases/{case_id}/report.pdf")
def case_report_pdf(case_id: str) -> Response:
    """The case report as a PDF of at most two pages, built from the case page's own overview (read-only)."""
    if case_id not in CASES:
        raise HTTPException(404, "no such case")
    if case_id not in RESULTS:
        raise HTTPException(409, "investigate the case first: the report comes from the investigation")
    actions = _case_actions(case_id)
    try:
        audit = service.audit(case_id=case_id, limit=50, expire=False)
    except Exception as e:  # the graph may be restarting: the report still has the case, without its audit trail
        log.warning("could not read the audit trail for %s: %s", case_id, e)
        audit = []
    pdf = pdf_report.build_pdf(_overview(case_id, actions), RESULTS[case_id], actions, audit)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{case_id}-report.pdf"'})


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


def token_ok(token: str | None) -> bool:
    expected = approver_token()
    return bool(expected and token) and hmac.compare_digest(token.encode(), expected.encode())


def _check_approver(token: str | None, request: Request) -> None:
    """The approver token, or 401/403. Too many wrong tokens from one visitor lock it out for a while (429)."""
    if not approver_token():
        raise HTTPException(403, "approvals are switched off on this server: set APPROVER_TOKEN in .env")
    ok = token_ok(token)
    guard.token_attempt(request, ok or not token)  # a missing token is not a guess
    if not ok:
        raise HTTPException(401, "approver token missing or wrong")


@app.post("/api/cases/{case_id}/plan")
def plan_case(case_id: str, request: Request, idempotency_key: str | None = Header(None)) -> dict:
    """Plan holds for an investigated case and propose them. Safe to repeat: proposals are merged on case,
    type and account, so a double click adds nothing (the Idempotency-Key header is accepted but not needed)."""
    guard.limit(request, "plans")
    if case_id not in CASES:
        raise HTTPException(404, "no such case")
    if case_id not in RESULTS:
        raise HTTPException(409, "investigate the case first: holds follow from the investigation")
    return registry.act(case_id)


@app.get("/api/cases/{case_id}/plan")
def get_plan(case_id: str) -> dict:
    """The saved plan, the case's current actions, and the effect of its holds: held_effect for the holds in
    place (approved or provisional), open_effect for those plus the proposed ones (what approving them all would
    freeze), each with the money on the case's routes they would also stop (an assumption). Recounted after
    every decision."""
    plan, actions = store.load_plan(case_id), service.list_actions(case_id=case_id)
    holds = [a for a in actions if a["type"] == "HOLD"]
    held = [a["account"] for a in holds if a["status"] in ("APPROVED", "PROVISIONAL")]
    open_ = held + [a["account"] for a in holds if a["status"] == "PROPOSED"]
    return {"plan": plan, "actions": actions, "auto_hold": auto_hold_policy(),
            "held_effect": planner.effect(plan, held) if plan else None,
            "open_effect": planner.effect(plan, open_) if plan else None}


@app.get("/api/actions")
def list_actions(status: str | None = None, case_id: str | None = None) -> list[dict]:
    return service.list_actions(case_id=case_id, status=status)


def _decide(action_id: str, approve: bool, d: Decision, key: str | None, token: str | None,
            request: Request) -> dict:
    _check_approver(token, request)
    try:
        return service.decide(action_id, approve, d.reason, by=d.by, key=key, auth="approver-token")
    except service.ActionError as e:
        raise HTTPException(e.status, str(e)) from e


@app.post("/api/auth/approver")
def check_approver(request: Request, x_approver_token: str | None = Header(None)) -> dict:
    """The demo approver's sign-in: is this the server's approver token? Nothing is stored on the server."""
    _check_approver(x_approver_token, request)
    return {"ok": True}


@app.post("/api/admin/reset")
def admin_reset(request: Request, x_approver_token: str | None = Header(None)) -> dict:
    """Reset the running server to a clean demo state (flowtrace.demo.reset). Needs the approver token."""
    _check_approver(x_approver_token, request)
    return reset_demo()


@app.post("/api/actions/{action_id}/approve")
def approve_action(action_id: str, d: Decision, request: Request, idempotency_key: str | None = Header(None),
                   x_approver_token: str | None = Header(None)) -> dict:
    return _decide(action_id, True, d, idempotency_key, x_approver_token, request)


@app.post("/api/actions/{action_id}/reject")
def reject_action(action_id: str, d: Decision, request: Request, idempotency_key: str | None = Header(None),
                  x_approver_token: str | None = Header(None)) -> dict:
    return _decide(action_id, False, d, idempotency_key, x_approver_token, request)


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


# --- MCP: the agent's tools for any MCP client, at /mcp (flowtrace.mcp.server) ---------------------

def _mcp_app():
    from mcp.server.transport_security import TransportSecuritySettings
    from flowtrace.mcp.server import build_server
    hosts = [h.strip() for h in os.environ.get("PUBLIC_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*"] + hosts,
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"] + [f"https://{h}" for h in hosts])
    # The tools never approve; this process keeps APPROVER_TOKEN for its own approve endpoints.
    return build_server(drop_approver_token=False).streamable_http_app(
        streamable_http_path="/mcp", transport_security=security, max_sessions=100, session_idle_timeout=900)


# The MCP route sits behind guard.McpGuard: a per-visitor limit, a message size cap, and on the public demo the
# approver token for propose_action. The lifespan gives it the MCP app while the server runs.
MCP_GUARD = guard.McpGuard(None, token_ok)
app.router.routes.append(Route("/mcp", endpoint=MCP_GUARD, methods=None))


# --- the built frontend (frontend/dist), when it is there: one URL for the app and its API -------------

DIST = ROOT / "frontend" / "dist"
if (DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """A file in dist (favicon ...), else the app's index.html: the app's router handles /today, /cases ..."""
        if path.startswith(("api/", "mcp")):
            raise HTTPException(404, "not found")
        f = (DIST / path).resolve()
        if path and f.is_file() and f.parent == DIST.resolve():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
