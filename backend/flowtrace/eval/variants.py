"""Compare disposition scoring variants on the same investigations, then pick one by a rule written in advance
(docs/superpowers/specs/2026-10-16-simple-report-review-design.md).

  A  dev scoring: measured_weights off, every indicator at its tool weight
  B  measured weights as first pushed: payment-level lift from Sep 1-8 (variant_b_rates.json), aliases, a
     ">= 500 payments" rule, and an escalator whose strength is under 0.5 no longer escalates
  C  measured weights, fixed (flowtrace.ml.signal_rates): each code measured on its own definition on Sep 1-6,
     >= 30 laundering examples, shrunk toward 1, never switching off an escalator

Each case is investigated once per policy; only the score is recomputed per variant, so the variants see exactly
the same evidence. Per variant it reports the eval.compare metrics, the eval.holds metrics (heuristic policy, as
eval.holds), and what auto-hold would do (ESCALATE at confidence >= actions.auto_hold.min_confidence).

Run one day (the graph must hold history up to that day only; for a validation day use a scratch graph):
  GRAPH_NAME=flowtrace_val python -m flowtrace.eval.variants --sample eval/day_slice_2022-09-07.csv --cases 40
Then the decision over the validation days:
  python -m flowtrace.eval.variants --decide variants_eval_day_slice_2022-09-07.json variants_eval_day_slice_2022-09-08.json
"""
from __future__ import annotations

import argparse
import copy
import json
from functools import lru_cache
from pathlib import Path

from flowtrace.actions.planner import plan_holds
from flowtrace.agent.disposition import decide
from flowtrace.agent.evidence import Evidence, EvidenceBook
from flowtrace.agent.investigate import investigate, new_context
from flowtrace.eval import compare, holds
from flowtrace.eval.funnel import cleanup
from flowtrace.graph.client import ro
from flowtrace.pipeline.batch import batch_id_for, process_batch
from flowtrace.settings import cfg, settings

VARIANTS = ("A", "B", "C")
HOLD_POLICY = "heuristic"  # as eval.holds
RECALL_SLACK = 0.02


@lru_cache
def _rates_b() -> dict:
    return json.loads((Path(__file__).parent / "variant_b_rates.json").read_text())


def strength_b(e: Evidence, rules: dict) -> float:
    """The measured strength exactly as first pushed (kept here only to measure it)."""
    r = _rates_b().get(e.code)
    if not r or r.get("lift") is None or r.get("payments", 0) < 500:
        return 1.0
    lift = max(r["lift"], 0.01)
    if e.kind == "support":
        return min(1.5, max(0.15, lift / 3))
    return min(1.5, max(0.5, 1 / (3 * lift)))


def variant_rules(rules: dict, v: str) -> dict:
    r = copy.deepcopy(rules)
    mw = r["disposition"]["measured_weights"]
    r["disposition"]["measured_weights"] = {**mw, "enabled": v == "C"}
    return r


def score(v: str, book: EvidenceBook, ml: float, shape_conf: float, rules: dict):
    if v != "B":
        return decide(book, ml, shape_conf, variant_rules(rules, v))
    weak = EvidenceBook()  # B: an escalator with strength under 0.5 stopped escalating
    weak.items = [Evidence(**{**e.as_dict(), "escalator": e.escalator and strength_b(e, rules) >= 0.5}) for e in book.items]
    return decide(weak, ml, shape_conf, variant_rules(rules, "A"), strength=strength_b)


def auto_hold_summary(rows: list[dict]) -> dict:
    fired = [r for r in rows if r["auto_hold"]]
    legit = [r for r in fired if not r["truth"]]
    n_holds = sum(r["holds"] for r in fired)
    return {
        "cases": len(fired), "laundering_cases": sum(bool(r["truth"]) for r in fired),
        "precision": sum(bool(r["truth"]) for r in fired) / len(fired) if fired else None,
        "hold_precision": sum(r["holds_on_laundering"] for r in fired) / n_holds if n_holds else None,
        "frozen_total": sum(r["frozen_now"] for r in fired),
        "wrongly_frozen_total": sum(r["frozen_now"] for r in legit),
        "wrongly_frozen_max_case": max((r["frozen_now"] for r in legit), default=0.0),
        "legit_cases": [{"case_id": r["case_id"], "frozen_now": r["frozen_now"]} for r in legit],
    }


def run(sample: str, n_cases: int, policies: list[str], variants: list[str]) -> dict:
    s, rules = settings(), cfg()
    data = (s.raw_dir.parent / "samples" / sample).read_bytes()
    batch_id = batch_id_for(data)
    existed = ro("MATCH ()-[t:TRANSFER {batch: $b}]->() RETURN count(t) AS n", {"b": batch_id}).rows[0]["n"] > 0
    batch = process_batch(data, sample, emit=lambda e: None)
    try:
        cases = list(batch["cases"].values())[:n_cases]
        gt, bad = compare.ground_truth_paths(), holds.laundering_transfers()
        min_conf = rules["actions"]["auto_hold"]["min_confidence"]
        rows = {v: {p: [] for p in policies} for v in variants}
        hold_rows = {v: [] for v in variants}
        print(f"{len(cases)} cases from {sample}; {sum(1 for c in cases if c['ground_truth'])} contain labelled laundering")
        for pol in policies:
            for c in cases:
                ctx = new_context(c, rules, lambda e: None)
                r = investigate(ctx, policy_name=pol, save_memory=False, use_llm_report=(pol == "llm"))
                base = compare.case_row(c, r, gt)
                shape_conf = (ctx.shape or {}).get("confidence", 0.0)
                line = []
                for v in variants:
                    d = score(v, ctx.book, ctx.ml_score, shape_conf, rules)
                    rows[v][pol].append({**base, "action": d.action, "risk": d.risk, "confidence": d.confidence})
                    line.append(f"{v}={d.action[:3]}({d.risk:.2f})")
                    if pol == HOLD_POLICY:
                        plan = plan_holds(c, rules, {**r, "disposition": d.as_dict()})
                        h = holds.case_row(c, d.action, plan, holds.laundering_before(bad, c["t1"]))
                        h["auto_hold"] = d.action == "ESCALATE" and d.confidence >= min_conf and h["holds"] > 0
                        h["confidence"] = d.confidence
                        hold_rows[v].append(h)
                print(f"  [{pol}] {c['case_id']} truth={c['ground_truth']} " + " ".join(line))
        out = {"sample": sample, "batch": batch_id, "cases": len(cases), "variants": {}}
        for v in variants:
            out["variants"][v] = {
                "policies": {p: compare.summarise(p, rows[v][p]) for p in policies},
                **({"holds": holds.summarise(hold_rows[v]), "hold_rows": hold_rows[v],
                    "auto_hold": auto_hold_summary(hold_rows[v])} if HOLD_POLICY in policies else {}),
            }
        name = sample.replace("/", "_").removesuffix(".csv")
        (s.models_dir / f"variants_{name}.json").write_text(json.dumps(out, indent=2))
        return out
    finally:
        cleanup(batch_id, existed)


def combined(runs: list[dict], v: str) -> dict:
    """Decision figures for one variant over several days: recall and legit escalations per policy, money
    wrongly frozen (holds the heuristic dispositions would plan on legitimate cases), and auto-hold."""
    pols = sorted(runs[0]["variants"][v]["policies"])
    rows = {p: [r for run in runs for r in run["variants"][v]["policies"][p]["rows"]] for p in pols}
    hrows = [r for run in runs for r in run["variants"][v].get("hold_rows", [])]
    out = {}
    for p in pols:
        pos = [r for r in rows[p] if r["truth"] == 1]
        neg = [r for r in rows[p] if r["truth"] == 0]
        out[p] = {"laundering": len(pos), "legitimate": len(neg),
                  "escalate_recall": sum(r["action"] == "ESCALATE" for r in pos) / len(pos) if pos else None,
                  "legit_escalated": sum(r["action"] == "ESCALATE" for r in neg)}
    legit_holds = [r for r in hrows if not r["truth"]]
    return {"policies": out,
            "legit_escalated_total": sum(out[p]["legit_escalated"] for p in pols),
            "money_wrongly_frozen": sum(r["frozen_now"] for r in legit_holds),
            "money_frozen_laundering": sum(r["frozen_now"] for r in hrows if r["truth"]),
            "auto_hold": auto_hold_summary(hrows)}


def pick(table: dict[str, dict]) -> tuple[str, list[str]]:
    """The rule written before measuring: recall within RECALL_SLACK of A for every policy, then fewest legitimate
    cases escalated, then least money wrongly frozen, then A."""
    a = table["A"]["policies"]
    ok = [v for v in table if all(table[v]["policies"][p]["escalate_recall"] >= a[p]["escalate_recall"] - RECALL_SLACK
                                  for p in a)]
    why = [f"{v}: recall {'within' if v in ok else 'NOT within'} {RECALL_SLACK} of A" for v in table]
    best = min(ok, key=lambda v: (table[v]["legit_escalated_total"], round(table[v]["money_wrongly_frozen"], 2), v != "A"))
    return best, why


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample")
    ap.add_argument("--cases", type=int, default=40)
    ap.add_argument("--policies", default="fixed,heuristic")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--decide", nargs="+", help="variants_*.json files of the validation days")
    a = ap.parse_args()
    s = settings()
    if a.decide:
        runs = [json.loads((s.models_dir / f).read_text()) for f in a.decide]
        table = {v: combined(runs, v) for v in runs[0]["variants"]}
        winner, why = pick(table)
        auto = table[winner]["auto_hold"]
        auto_on = (auto["precision"] is not None and auto["precision"] >= 0.9 and auto["wrongly_frozen_max_case"] <= 1_000_000)
        out = {"files": a.decide, "table": table, "winner": winner, "why": why, "auto_hold_default_on": auto_on}
        (s.models_dir / "variants_decision.json").write_text(json.dumps(out, indent=2))
        print(json.dumps({k: v for k, v in out.items() if k != "table"}, indent=2))
        for v, t in table.items():
            print(v, json.dumps({k: t[k] for k in ("policies", "legit_escalated_total", "money_wrongly_frozen")}))
            print("   auto-hold", json.dumps({k: auto for k, auto in t["auto_hold"].items() if k != "legit_cases"}))
        return
    out = run(a.sample, a.cases, a.policies.split(","), a.variants.split(","))
    for v, m in out["variants"].items():
        for p, x in m["policies"].items():
            print(f"{v} {p:10s} recall={x['escalate_recall']} precision={x['escalate_precision']} "
                  f"legit_closed={x['legit_auto_closed']}")
        if "auto_hold" in m:
            print(f"{v} holds frozen={m['holds']['frozen_total']:,.0f} auto-hold={json.dumps(m['auto_hold'])}")


if __name__ == "__main__":
    main()
