"""Measure the hold planner on an uploaded day: money still in motion, money the holds block, and whether
the held accounts really are laundering accounts.

  disposition  the rule-based investigation (heuristic policy: deterministic, no LLM). Holds are planned only
               for actions.propose_for (ESCALATE, REVIEW), exactly as in the app.
  precision    a hold is right when its account sent or received a labelled laundering payment in the dataset.
               Labels are used only here, never by the planner.
  hubs         every held account is checked again against the hub rules; this must be 0.

No settings are tuned on this run: config.yaml `actions` was fixed before measuring. The batch is deleted
from the graph afterwards unless it was already there (uploaded from the UI). Nothing is written by the
planner itself (read-only queries).

Run (graph loaded up to the sample's day):
  python -m flowtrace.eval.holds --sample eval/day_slice_2022-09-09.csv --cases 40
  python -m flowtrace.eval.holds --sample demo_mixed_2022-09-09.csv
"""
from __future__ import annotations

import argparse
import json
import statistics as st

import polars as pl

from flowtrace.actions.planner import _hubs, plan_holds
from flowtrace.agent.investigate import investigate, new_context
from flowtrace.eval.funnel import cleanup
from flowtrace.graph.client import ro
from flowtrace.pipeline.batch import batch_id_for, process_batch
from flowtrace.settings import cfg, settings


def laundering_accounts() -> set[str]:
    tx = pl.read_parquet(settings().processed_dir / "transactions.parquet", columns=["src", "dst", "is_laundering"])
    bad = tx.filter(pl.col("is_laundering") == 1)
    return set(bad["src"].to_list()) | set(bad["dst"].to_list())


def case_row(case: dict, disposition: str, plan: dict, laundering: set[str], hubs: set[str]) -> dict:
    held = [h["account"] for h in plan["holds"]]
    return {
        "case_id": case["case_id"], "truth": case.get("ground_truth"), "disposition": disposition,
        "in_motion": plan["in_motion"], "already_out": plan["already_out"], "blocked": plan["blocked"],
        "blocked_share": plan["blocked"] / plan["in_motion"] if plan["in_motion"] else None,
        "holds": len(held), "holds_on_laundering": sum(a in laundering for a in held), "hubs_held": sum(a in hubs for a in held),
        "method": plan["method"], "min_cut_size": plan["min_cut_size"], "currency": plan["currency"],
        "all_case_accounts_are_hubs": bool(case.get("accounts")) and set(case["accounts"]) <= set(plan["skipped_hubs"]),
    }


def summarise(rows: list[dict], split: bool = True) -> dict:
    holds = sum(r["holds"] for r in rows)
    by_truth = {"by_truth": {
        "laundering": summarise([r for r in rows if r["truth"]], split=False),
        "legitimate": summarise([r for r in rows if not r["truth"]], split=False),
    }} if split else {}
    motion = [r["in_motion"] for r in rows]
    return {
        "cases": len(rows), "cases_with_holds": sum(r["holds"] > 0 for r in rows),
        "cases_with_money_in_motion": sum(m > 0 for m in motion),
        "in_motion_total": sum(motion), "in_motion_median": st.median(motion) if motion else 0.0,
        "already_out_total": sum(r["already_out"] for r in rows),
        "blocked_total": sum(r["blocked"] for r in rows), "holds": holds,
        "holds_per_case_with_holds": holds / max(sum(r["holds"] > 0 for r in rows), 1),
        "hold_precision": sum(r["holds_on_laundering"] for r in rows) / holds if holds else None,
        "hubs_held": sum(r["hubs_held"] for r in rows),
        "laundering_cases_all_hub_accounts": sum(bool(r["truth"]) and r["all_case_accounts_are_hubs"] for r in rows),
        "methods": {m: sum(r["method"] == m for r in rows) for m in sorted({r["method"] for r in rows})},
        **by_truth,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="eval/day_slice_2022-09-09.csv")
    ap.add_argument("--cases", type=int, default=0, help="top N cases by rank (0 = all)")
    a = ap.parse_args()
    s, rules = settings(), cfg()
    data = (s.raw_dir.parent / "samples" / a.sample).read_bytes()
    batch_id = batch_id_for(data)
    existed = ro("MATCH ()-[t:TRANSFER {batch: $b}]->() RETURN count(t) AS n", {"b": batch_id}).rows[0]["n"] > 0
    batch = process_batch(data, a.sample, emit=lambda e: None)
    try:
        cases = list(batch["cases"].values())
        cases = cases[: a.cases] if a.cases else cases
        bad = laundering_accounts()
        rows = []
        for c in cases:
            r = investigate(new_context(c, rules, lambda e: None), policy_name="heuristic", use_llm_report=False,
                            save_memory=False)
            p = plan_holds(c, rules, r)
            held = [h["account"] for h in p["holds"]]
            rows.append(case_row(c, r["disposition"]["action"], p, bad, _hubs(held, c["t0"], rules)))
            x = rows[-1]
            print(f"  {x['case_id']} truth={x['truth']} {x['disposition']:8s} motion={x['in_motion']:>12,.0f} "
                  f"blocked={x['blocked']:>12,.0f} holds={x['holds']} on_laundering={x['holds_on_laundering']} {x['method']}")
        out = {"sample": a.sample, "batch": batch_id, "summary": summarise(rows), "rows": rows}
        name = a.sample.replace("/", "_").removesuffix(".csv")
        (s.models_dir / f"holds_eval_{name}.json").write_text(json.dumps(out, indent=2))
        print(json.dumps(out["summary"], indent=2))
    finally:
        cleanup(batch_id, existed)


if __name__ == "__main__":
    main()
