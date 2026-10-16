"""Compare investigation policies on the same alerts with the same budget.

  fixed      baseline: same tools, same order, every case
  heuristic  adaptive rules (no LLM)
  llm        adaptive agent (the LLM chooses each step)

For each case we know (but never show the agent) whether it contains labelled laundering, and
which ground-truth laundering transfers exist. Reported per policy:
  * escalate precision / recall, and the share of legitimate alerts auto-closed (false positives removed)
  * laundering kept open (ESCALATE or REVIEW) = recall that triage must not lose
  * evidence coverage: share of ground-truth pattern transfers the investigation retrieved
  * report validity (llm only): unsupported-claim rate
  * cost: tool steps, Cypher queries, seconds

Run (needs FalkorDB loaded):  python -m flowtrace.eval.compare --sample demo_mixed_2022-09-09.csv --cases 25
"""
from __future__ import annotations

import argparse
import json
import statistics as st

import polars as pl

from flowtrace.agent.investigate import investigate, new_context
from flowtrace.pipeline.batch import process_batch
from flowtrace.settings import cfg, settings


def ground_truth_paths() -> dict[tuple, int]:
    s = settings()
    pat = pl.read_parquet(s.processed_dir / "patterns.parquet")
    tx = pl.read_parquet(s.processed_dir / "transactions.parquet", columns=["tx_id", "src", "dst", "ts"])
    rows = pat.join(tx, on="tx_id")
    return {(r["src"], r["dst"], r["ts"]): r["attempt_id"] for r in rows.iter_rows(named=True)}


def case_row(c: dict, r: dict, gt: dict[tuple, int]) -> dict:
    """One case under one policy. policy is what actually picked the steps (an llm run can fall back to rules)."""
    seen = {(e["src"], e["dst"], e["ts"]) for e in r["graph"]["edges"]}
    alert = {(e["src"], e["dst"], e["ts"]) for e in c["edges"]}
    attempts = {gt[k] for k in alert if k in gt}
    truth_edges = {k for k, att in gt.items() if att in attempts}
    st_ = r["stats"]
    return {
        "case_id": c["case_id"], "truth": c["ground_truth"], "action": r["disposition"]["action"],
        "risk": r["disposition"]["risk"], "steps": st_["steps"], "queries": st_["queries"], "seconds": st_["seconds"],
        "coverage": (len(truth_edges & seen) / len(truth_edges)) if truth_edges else None,
        "unsupported_rate": r["report"]["validation"]["unsupported_rate"],
        "policy": st_.get("policy"), "llm_calls": st_.get("llm_calls", 0), "llm_errors": st_.get("llm_errors", []),
        "report_author": r["report"].get("author"),
    }


def summarise(pol: str, rows: list[dict]) -> dict:
    pos = [r for r in rows if r["truth"] == 1]
    neg = [r for r in rows if r["truth"] == 0]
    esc = [r for r in rows if r["action"] == "ESCALATE"]
    cov = [r["coverage"] for r in rows if r["coverage"] is not None]
    return {
        "escalate_precision": (sum(r["truth"] == 1 for r in esc) / len(esc)) if esc else None,
        "escalate_recall": (sum(r["action"] == "ESCALATE" for r in pos) / len(pos)) if pos else None,
        "laundering_kept_open": (sum(r["action"] != "CLOSE" for r in pos) / len(pos)) if pos else None,
        "legit_auto_closed": (sum(r["action"] == "CLOSE" for r in neg) / len(neg)) if neg else None,
        "evidence_coverage": st.mean(cov) if cov else None,
        "unsupported_claim_rate": st.mean(r["unsupported_rate"] for r in rows),
        "avg_steps": st.mean(r["steps"] for r in rows), "avg_queries": st.mean(r["queries"] for r in rows),
        "avg_seconds": st.mean(r["seconds"] for r in rows),
        # How often the llm policy was not really the LLM: every step by rules, some steps, or the template report.
        "fallback": None if pol != "llm" else {
            "rows_all_steps": sum(r["policy"] == "heuristic fallback" for r in rows),
            "rows_some_steps": sum("heuristic" in (r["policy"] or "") and r["policy"] != "heuristic fallback" for r in rows),
            "template_reports": sum(r["report_author"] == "template" for r in rows),
            "rows_with_llm_errors": sum(bool(r["llm_errors"]) for r in rows),
        },
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="demo_mixed_2022-09-09.csv")
    ap.add_argument("--cases", type=int, default=25)
    ap.add_argument("--policies", default="fixed,heuristic,llm")
    a = ap.parse_args()
    s, rules = settings(), cfg()

    data = (s.raw_dir.parent / "samples" / a.sample).read_bytes()
    batch = process_batch(data, a.sample, emit=lambda e: None)
    cases = list(batch["cases"].values())[: a.cases]
    gt = ground_truth_paths()
    print(f"{len(cases)} cases from {a.sample}; {sum(1 for c in cases if c['ground_truth'])} contain labelled laundering")

    out = {"sample": a.sample, "cases": len(cases), "policies": {}}
    for pol in a.policies.split(","):
        rows = []
        for c in cases:
            r = investigate(new_context(c, rules, lambda e: None), policy_name=pol, save_memory=False,
                            use_llm_report=(pol == "llm"))
            rows.append(case_row(c, r, gt))
            print(f"  [{pol}] {c['case_id']} truth={c['ground_truth']} -> {rows[-1]['action']} ({rows[-1]['risk']})"
                  f" policy={rows[-1]['policy']}{' errors=' + str(len(rows[-1]['llm_errors'])) if rows[-1]['llm_errors'] else ''}")
        out["policies"][pol] = summarise(pol, rows)
    (s.models_dir / "agent_eval.json").write_text(json.dumps(out, indent=2))
    for pol, m in out["policies"].items():
        print(f"{pol:10s} " + "  ".join(f"{k}={v:.2f}" for k, v in m.items() if isinstance(v, float))
              + (f"  fallback={m['fallback']}" if m["fallback"] else ""))


if __name__ == "__main__":
    main()
