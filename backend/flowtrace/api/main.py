"""FlowTrace API.

Run:  uvicorn flowtrace.api.main:app --reload --port 8000   (from backend/)
"""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from flowtrace.api.overview import build_overview
from flowtrace.settings import settings

app = FastAPI(title="FlowTrace", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    graph_ok = False
    try:
        from flowtrace.graph.client import ro
        ro("RETURN 1 AS ok", timeout=1000)
        graph_ok = True
    except Exception:
        pass
    return {"api": True, "graph": graph_ok, "llm": bool(settings().anthropic_api_key)}


@app.get("/api/overview")
def overview() -> dict:
    return build_overview()


@app.get("/api/model")
def model() -> dict:
    d = settings().models_dir
    metrics, curves = d / "triage_metrics.json", d / "triage_curves.json"
    if not metrics.exists():
        raise HTTPException(404, "Model not trained yet: run python -m flowtrace.ml.train")
    out = {"metrics": json.loads(metrics.read_text())}
    if curves.exists():
        out["curves"] = json.loads(curves.read_text())
    return out
