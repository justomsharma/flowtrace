"""Measure the hold planner on an uploaded day: money frozen now, money on the case's routes (an assumption),
and whether the held accounts really are laundering accounts.

  disposition  the rule-based investigation by default (heuristic policy: deterministic, no LLM); --policy llm
               uses the LLM agent's dispositions (not deterministic; costs LLM calls; the report is not written).
               Holds are planned only for actions.propose_for (ESCALATE), exactly as in the app.
  frozen       the headline: what the holds freeze today (a lien on the traced case money in each account).
  on routes    reported apart: money upstream that would have to pass a held account if the mules keep the
               routes seen so far. An assumption, not money held.
  precision    a hold is right when its account sent or received a labelled laundering payment before the
               decision time (the end of the case window). Shown next to the base rate: the laundering share
               among every account a hold could have gone on (the planner's candidates). Labels are used only here.

The batch is deleted from the graph afterwards unless it was already there (uploaded from the UI). The planner
only reads.

Run (graph loaded up to the sample's day); --cases N keeps the top N cases of the upload's ranking:
  python -m flowtrace.eval.holds --sample eval/day_slice_2022-09-09.csv --cases 40
  python -m flowtrace.eval.holds --sample demo_mixed_2022-09-09.csv
  python -m flowtrace.eval.holds --sample eval/day_slice_2022-09-09.csv --cases 40 --policy llm
"""
from __future__ import annotations

import argparse
import json
import statistics as st

import polars as pl

from flowtrace.actions.planner import plan_holds
from flowtrace.agent.investigate import investigate, new_context
from flowtrace.eval.funnel import cleanup
from flowtrace.graph.client import ro
from flowtrace.pipeline.batch import batch_id_for, process_batch
from flowtrace.settings import cfg, settings


def laundering_transfers() -> pl.DataFrame:
    tx = pl.read_parquet(settings().processed_dir / "transactions.parquet", columns=["src", "dst", "ts", "is_laundering"])
    return tx.filter(pl.col("is_laundering") == 1)


def laundering_before(bad: pl.DataFrame, t1: int) -> set[str]:
    """Accounts that sent or received a labelled laundering payment before t1 (epoch ms)."""
    b = bad.filter((pl.col("is_laundering") == 1) & (pl.col("ts") < t1))
    return set(b["src"].to_list()) | set(b["dst"].to_list())


def case_row(case: dict, disposition: str, plan: dict, laundering: set[str]) -> dict:
    held = [h["account"] for h in plan["holds"]]
    return {
        "case_id": case["case_id"], "truth": case.get("ground_truth"), "disposition": disposition,
        "in_motion": plan["in_motion"], "frozen_now": plan["frozen_now"], "on_routes": plan["on_routes"],
        "already_out": plan["already_out"],
        "holds": len(held), "holds_on_laundering": sum(a in laundering for a in held),
        "candidates": len(plan["candidates"]), "candidates_laundering": sum(a in laundering for a in plan["candidates"]),
        "route_cut_size": plan["route_cut_size"], "currency": plan["currency"],
        "all_case_accounts_are_hubs": bool(case.get("accounts")) and set(case["accounts"]) <= set(plan.get("skipped_hubs", [])),
    }


def summarise(rows: list[dict], split: bool = True) -> dict:
    holds = sum(r["holds"] for r in rows)
    cands = sum(r["candidates"] for r in rows)
    motion = [r["in_motion"] for r in rows]
    by_truth = {"by_truth": {
        "laundering": summarise([r for r in rows if r["truth"]], split=False),
        "legitimate": summarise([r for r in rows if not r["truth"]], split=False),
    }} if split else {}
    return {
        "cases": len(rows), "cases_with_holds": sum(r["holds"] > 0 for r in rows),
        "in_motion_total": sum(motion), "in_motion_median": st.median(motion) if motion else 0.0,
        "frozen_total": sum(r["frozen_now"] for r in rows), "on_routes_total": sum(r["on_routes"] for r in rows),
        "already_out_total": sum(r["already_out"] for r in rows), "holds": holds,
        "hold_precision": sum(r["holds_on_laundering"] for r in rows) / holds if holds else None,
        "base_rate": sum(r["candidates_laundering"] for r in rows) / cands if cands else None,
        "laundering_cases_all_hub_accounts": sum(bool(r["truth"]) and r["all_case_accounts_are_hubs"] for r in rows),
        "dispositions": {d: sum(r["disposition"] == d for r in rows) for d in sorted({r["disposition"] for r in rows})},
        **by_truth,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="eval/day_slice_2022-09-09.csv")
    ap.add_argument("--cases", type=int, default=0, help="top N cases of the upload's ranking (0 = all)")
    ap.add_argument("--policy", choices=["heuristic", "llm"], default="heuristic", help="whose dispositions gate the holds")
    a = ap.parse_args()
    s, rules = settings(), cfg()
    data = (s.raw_dir.parent / "samples" / a.sample).read_bytes()
    batch_id = batch_id_for(data)
    existed = ro("MATCH ()-[t:TRANSFER {batch: $b}]->() RETURN count(t) AS n", {"b": batch_id}).rows[0]["n"] > 0
    batch = process_batch(data, a.sample, emit=lambda e: None)
    try:
        cases = list(batch["cases"].values())
        cases = cases[: a.cases] if a.cases else cases
        bad = laundering_transfers()
        rows = []
        for c in cases:
            r = investigate(new_context(c, rules, lambda e: None), policy_name=a.policy, use_llm_report=False,
                            save_memory=False)
            p = plan_holds(c, rules, r)
            rows.append(case_row(c, r["disposition"]["action"], p, laundering_before(bad, c["t1"])))
            x = rows[-1]
            x["policy_used"] = r["stats"]["policy"]  # "llm", or a rule-based fallback for some steps
            print(f"  {x['case_id']} truth={x['truth']} {x['disposition']:8s} motion={x['in_motion']:>12,.0f} "
                  f"frozen={x['frozen_now']:>12,.0f} routes={x['on_routes']:>10,.0f} holds={x['holds']} "
                  f"on_laundering={x['holds_on_laundering']}")
        out = {"sample": a.sample, "batch": batch_id, "policy": a.policy, "summary": summarise(rows), "rows": rows}
        name = a.sample.replace("/", "_").removesuffix(".csv") + ("" if a.policy == "heuristic" else f"_{a.policy}")
        (s.models_dir / f"holds_eval_{name}.json").write_text(json.dumps(out, indent=2))
        print(json.dumps(out["summary"], indent=2))
    finally:
        cleanup(batch_id, existed)


if __name__ == "__main__":
    main()
