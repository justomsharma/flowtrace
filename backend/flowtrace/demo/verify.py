"""Check the demo history graph against the full graph: same cases, same investigations, same hold plans.

Each run starts from a clean graph (flowtrace.demo.reset), uploads the samples in order the way the app does (each
upload stays in the graph, and case memory is saved, so later uploads see earlier cases), investigates every case
with the rule-based and fixed policies, plans its holds, and writes everything to a JSON file. Timings and query
logs are dropped; everything else must match.

The LLM agent is not deterministic, so it is checked by replay: its tool calls on the demo graph are run again,
in the same order with the same arguments, on the full graph, and the evidence and decision must match.

  python -m flowtrace.demo.verify run out.json [--llm]             on GRAPH_NAME
  python -m flowtrace.demo.verify replay demo.json out.json        LLM steps from demo.json, on GRAPH_NAME
  python -m flowtrace.demo.verify compare a.json b.json

Run it with GRAPH_NAME=<full graph> and GRAPH_NAME=<demo graph>, then compare. Both graphs are reset first.
"""
from __future__ import annotations

import argparse
import json
import sys

from flowtrace.demo.history import demo_samples
from flowtrace.demo.reset import reset_graph
from flowtrace.settings import ROOT, cfg

VOLATILE = {"queries", "llm", "meta", "policy_meta", "uploaded_at", "timings", "internals", "graph_rows"}


def clean(x):
    """Drop timings and query logs (they differ run to run on any graph)."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()
                if k not in VOLATILE and not k.endswith("_ms") and k not in ("ms", "seconds", "stats")}
    if isinstance(x, list):
        return [clean(v) for v in x]
    if isinstance(x, float):
        return round(x, 6)
    return x


class Replay:
    """Runs a recorded list of steps, in order."""
    name = "llm"

    def __init__(self, steps: list[dict]) -> None:
        self.steps = list(steps)
        self.meta: list = []

    def next(self, ctx):
        if not self.steps:
            return None
        s = self.steps.pop(0)
        return {"action": s["tool"], "args": s["args"], "reason": s["reason"], "policy": "llm"}


def run(policies: list[str], replay: dict | None = None) -> dict:
    from flowtrace.actions.planner import plan_holds
    from flowtrace.agent import investigate as inv
    from flowtrace.pipeline.batch import process_batch

    rules, noop = cfg(), (lambda e: None)
    reset_graph()
    out: dict = {"samples": {}}
    try:
        for name in demo_samples():
            res = process_batch((ROOT / "data" / "samples" / name).read_bytes(), name, noop)
            cases = res["cases"]
            per: dict = {"summary": clean({k: v for k, v in res["summary"].items() if k != "cases"}),
                         "cases": clean(cases), "investigations": {}}
            for cid, c in cases.items():
                for p in policies:
                    if p == "replay":
                        steps = (replay or {}).get(name, {}).get(cid)
                        if steps is None:
                            continue
                        inv.POLICIES["replay"] = lambda steps=steps: Replay(steps)
                    r = inv.investigate(inv.new_context(c, rules, noop), policy_name=p, use_llm_report=False,
                                        save_memory=(p == policies[0]))
                    r["graph"] = {"nodes": sorted(r["graph"]["nodes"], key=lambda n: n["id"]),  # drawing order
                                  "edges": sorted(r["graph"]["edges"], key=lambda e: e["tx_id"])}
                    per["investigations"][f"{cid}|{p}"] = {
                        "result": clean(r), "plan": clean(plan_holds(c, rules, r)),
                        "steps": [{"tool": s["tool"], "args": s["args"], "reason": s["reason"]} for s in r["steps"]]}
            out["samples"][name] = per
            print(f"  {name}: {len(cases)} cases", file=sys.stderr)
    finally:
        reset_graph()
    return out


def compare(a: dict, b: dict) -> list[str]:
    diffs: list[str] = []

    def walk(x, y, path):
        if len(diffs) > 200:
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                if k not in x or k not in y:
                    diffs.append(f"{path}.{k}: only in {'first' if k in x else 'second'}")
                else:
                    walk(x[k], y[k], f"{path}.{k}")
        elif isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y):
                diffs.append(f"{path}: {len(x)} vs {len(y)} items")
            for i, (u, v) in enumerate(zip(x, y)):
                walk(u, v, f"{path}[{i}]")
        elif x != y:
            diffs.append(f"{path}: {str(x)[:120]!r} vs {str(y)[:120]!r}")

    walk(a, b, "")
    return diffs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("out")
    r.add_argument("--llm", action="store_true")
    p = sub.add_parser("replay")
    p.add_argument("source")
    p.add_argument("out")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--only", choices=["rules", "llm"], default=None)
    a = ap.parse_args()
    if a.cmd == "run":
        res = run(["heuristic", "fixed"] + (["llm"] if a.llm else []))
        open(a.out, "w").write(json.dumps(res, indent=1, default=str))
    elif a.cmd == "replay":
        src = json.load(open(a.source))
        steps = {n: {k.split("|")[0]: v["steps"] for k, v in s["investigations"].items() if k.endswith("|llm")}
                 for n, s in src["samples"].items()}
        res = run(["heuristic", "replay"], replay=steps)
        open(a.out, "w").write(json.dumps(res, indent=1, default=str))
    else:
        x, y = json.load(open(a.a)), json.load(open(a.b))
        if a.only:  # compare the LLM runs of one file with the replays of the other, or the rule runs only
            keep = ("|llm", "|replay") if a.only == "llm" else ("|heuristic", "|fixed")
            for d in (x, y):
                for s in d["samples"].values():
                    s["investigations"] = {k.replace("|replay", "|llm"): v for k, v in s["investigations"].items()
                                           if k.endswith(keep)}
                    if a.only == "llm":
                        for v in s["investigations"].values():
                            v["result"].pop("policy", None)
        d = compare(x, y)
        n_cases = sum(len(s["cases"]) for s in x["samples"].values())
        n_inv = sum(len(s["investigations"]) for s in x["samples"].values())
        print(f"{n_cases} cases, {n_inv} investigations compared: {'IDENTICAL' if not d else f'{len(d)} differences'}")
        for line in d[:60]:
            print("  " + line)
        sys.exit(1 if d else 0)


if __name__ == "__main__":
    main()
