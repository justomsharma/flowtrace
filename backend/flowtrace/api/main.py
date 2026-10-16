"""FlowTrace API.

Run:  uvicorn flowtrace.api.main:app --port 8000   (from backend/)
"""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from flowtrace.api import jobs
from flowtrace.api.overview import build_overview
from flowtrace.graph import queries as Q
from flowtrace.graph.client import rw
from flowtrace.settings import cfg, settings

app = FastAPI(title="FlowTrace", version="0.2.0")
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


def _persist(kind: str, key: str, obj: dict) -> None:
    d = RUNS / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps(obj, default=str))


def _restore() -> None:
    for kind, store in (("batches", BATCHES), ("cases", CASES), ("results", RESULTS)):
        for f in (RUNS / kind).glob("*.json") if (RUNS / kind).exists() else []:
            store[f.stem] = json.loads(f.read_text())


_restore()


MAX_UPLOAD_MB = 50
TOO_LARGE = (f"File too large ({MAX_UPLOAD_MB} MB max). A full HI-Small day is about 61 MB: evaluate full days offline "
             f"with `python -m flowtrace.eval.fullday --day YYYY-MM-DD` (from backend/).")


def is_curated_sample(name: str) -> bool:
    """Demo files from data/samples are hand-picked around known laundering (~29x a real day's rate)."""
    p = (SAMPLES / name).resolve()
    return p.parent == SAMPLES.resolve() and p.exists()


def forget_batch(batch_id: str) -> None:
    """Drop a batch's earlier cases, investigation results and case memory before it is stored again.
    Case ids are rank-based (B..-C001), so after a retune an old result would otherwise attach to a
    different case."""
    prefix = f"{batch_id}-C"
    for kind, store in (("cases", CASES), ("results", RESULTS)):
        for cid in [c for c in store if c.startswith(prefix)]:
            del store[cid]
            (RUNS / kind / f"{cid}.json").unlink(missing_ok=True)
    rw(Q.DELETE_BATCH_CASES, {"prefix": prefix})


@app.get("/api/health")
def health() -> dict:
    graph_ok = False
    try:
        from flowtrace.graph.client import ro
        ro("RETURN 1 AS ok", timeout=1000)
        graph_ok = True
    except Exception:
        pass
    from flowtrace.agent.llm import available
    return {"api": True, "graph": graph_ok, "llm": available(), "model": settings().agent_model}


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

    def done(result: dict) -> None:
        s = result["summary"]
        forget_batch(s["batch_id"])
        s["curated"] = is_curated_sample(name)
        BATCHES[s["batch_id"]] = s
        CASES.update(result["cases"])
        _persist("batches", s["batch_id"], s)
        _persist("graphs", s["batch_id"], {"links": result["graph"]})
        for cid, c in result["cases"].items():
            _persist("cases", cid, c)

    job = jobs.start("batch", lambda emit: process_batch(data, name, emit), done)
    return {"job_id": job.id}


@app.get("/api/batches")
def list_batches() -> list[dict]:
    return sorted(({k: v for k, v in b.items() if k != "cases"} | {"n_cases": len(b["cases"])} for b in BATCHES.values()),
                  key=lambda b: -b["window"][0])


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict:
    if batch_id not in BATCHES:
        raise HTTPException(404, "no such batch")
    b = dict(BATCHES[batch_id])
    b["cases"] = [c | {"result": _result_brief(c["case_id"])} for c in b["cases"]]
    return b


@app.get("/api/batches/{batch_id}/graph")
def batch_graph(batch_id: str) -> dict:
    f = RUNS / "graphs" / f"{batch_id}.json"
    if not f.exists():
        raise HTTPException(404, "no graph for that batch")
    return json.loads(f.read_text())


def _result_brief(case_id: str) -> dict | None:
    r = RESULTS.get(case_id)
    if not r:
        return None
    return {"action": r["disposition"]["action"], "risk": r["disposition"]["risk"],
            "confidence": r["disposition"]["confidence_label"]}


# --- cases -----------------------------------------------------------------------------------

@app.post("/api/cases/{case_id}/investigate")
def investigate_case(case_id: str, policy: Literal["claude", "heuristic", "fixed"] = "claude") -> dict:
    if case_id not in CASES:
        raise HTTPException(404, "no such case")
    from flowtrace.agent.investigate import investigate, new_context

    case = CASES[case_id]

    def run(emit):
        return investigate(new_context(case, cfg(), emit), policy_name=policy)

    def done(result: dict) -> None:
        RESULTS[case_id] = result
        _persist("results", case_id, result)

    job = jobs.start("case", run, done)
    return {"job_id": job.id}


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    if case_id not in CASES:
        raise HTTPException(404, "no such case")
    c = CASES[case_id]
    return {"case": c, "result": RESULTS.get(case_id)}


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
            if j.status != "running" and i >= len(j.events):
                break
            await asyncio.sleep(0.08)

    return EventSourceResponse(gen())


@app.get("/api/config")
def config() -> dict:
    return cfg()
