"""Senior-analyst report.

The LLM turns the validated evidence ledger into a narrative. It cannot change the disposition,
the risk score or the typology (computed in code). Every statement must cite evidence IDs; the
validator drops statements whose IDs do not exist or whose numbers do not appear in the cited
evidence, and reports the unsupported-claim rate. Without the LLM, a template report is built.
"""
from __future__ import annotations

import json
import re

from flowtrace.agent.context import CaseContext
from flowtrace.agent.disposition import Disposition
from flowtrace.agent.llm import structured

SYSTEM = """You are a senior AML investigator with 10+ years of experience writing case reports for
a bank's financial intelligence unit (FIU-IND / RBI context). Write precise, neutral, evidence-based
prose. Rules:
- Use ONLY facts in the evidence list. Every statement cites evidence IDs (e.g. E3).
- Copy amounts, counts and percentages exactly as they appear in the evidence; do not compute new numbers.
- The disposition, risk score and typology are already decided; explain them, never change them.
- Separate observed facts from inference. A suspicious pattern is not proof of a crime.
- Weigh the legitimate explanation fairly; say what evidence is missing.
- Write so a junior analyst or a non-specialist manager can follow it: short sentences, plain words,
  and explain any AML term the first time you use it (e.g. "pass-through (money leaving almost as soon as it arrives)").
- Style, for every field: plain words, no paragraphs. summary_points: 3 to 5 points, each one short
  sentence of at most 12 words, most important first (what happened, why it is suspicious, why it may
  be normal). Do not state the decision: it is added after your points. headline: at most 10 words. executive_summary: one sentence, at most 20 words.
  next_steps: at most 3, each at most 12 words. All amounts in US dollars ($).
- Never write evidence IDs (E1, E2…) in any text; put them only in evidence_ids. The one exception is
  str_narrative: end each of its sentences with the evidence IDs it rests on, in brackets, e.g. "[E1, E3]".
- str_narrative: only when the disposition is ESCALATE, a draft STR narrative (who, what, when,
  where, why suspicious, how), otherwise an empty string."""

ITEM = {"type": "object", "properties": {"text": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["text", "evidence_ids"], "additionalProperties": False}
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary_points": {"type": "array", "items": ITEM},
        "executive_summary": ITEM,
        "subject_profile": ITEM,
        "money_trail": {"type": "array", "items": ITEM},
        "typology_assessment": ITEM,
        "red_flags": {"type": "array", "items": ITEM},
        "mitigating_factors": {"type": "array", "items": ITEM},
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "str_narrative": {"type": "string"},
    },
    "required": ["headline", "summary_points", "executive_summary", "subject_profile", "money_trail", "typology_assessment",
                 "red_flags", "mitigating_factors", "unknowns", "next_steps", "str_narrative"],
    "additionalProperties": False,
}

NUM = re.compile(r"\d[\d,]*\.?\d*")
# Evidence IDs written into text, e.g. "(E1, E3)", "[E2]", "E4": the page never shows them.
IDS = re.compile(r"\s*[\(\[]\s*E\d+(?:\s*,\s*E\d+)*\s*[\)\]]|\bE\d+\b")


CITE = re.compile(r"\[\s*(E\d+(?:\s*,\s*E\d+)*)\s*\]")


def known_citations(text: str, known: set[str]) -> tuple[str, list[str]]:
    """Keep only bracketed evidence IDs that exist ("[E1, E9]" -> "[E1]" when E9 is unknown); returns the dropped IDs."""
    bad: list[str] = []

    def keep(m: re.Match) -> str:
        ids = [i.strip() for i in m.group(1).split(",")]
        bad.extend(i for i in ids if i not in known)
        ok = [i for i in ids if i in known]
        return f"[{', '.join(ok)}]" if ok else ""
    return re.sub(r"\s+([.,;])", r"\1", CITE.sub(keep, text or "")).strip(), bad


def clean(text: str) -> str:
    return re.sub(r"\s{2,}", " ", IDS.sub("", text or "")).replace(" .", ".").replace(" ,", ",").strip()


def _nums(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(text)}


def _check(item: dict, ev: dict[str, dict], computed: set[str] = frozenset()) -> tuple[bool, str]:
    ids = item.get("evidence_ids") or []
    if not ids:
        return False, "no evidence cited"
    missing = [i for i in ids if i not in ev]
    if missing:
        return False, f"unknown evidence {missing}"
    pool = " ".join(ev[i]["claim"] + " " + json.dumps(ev[i]["metrics"], default=str) for i in ids)
    allowed = _nums(pool) | {i[1:] for i in ids} | computed
    bad = [n for n in _nums(item["text"]) if n not in allowed and len(n) > 1]
    if bad:
        return False, f"numbers not in cited evidence: {bad[:3]}"
    return True, ""


def validate(report: dict, ctx: CaseContext, disp: Disposition) -> dict:
    ev = {e.id: e.as_dict() for e in ctx.book.items}
    # Numbers computed by code (not by the LLM) may also be quoted.
    computed = _nums(" ".join([str(disp.risk), str(disp.confidence), json.dumps(disp.components),
                               str(len(ctx.book.by_kind("support"))), str(len(ctx.book.by_kind("counter"))),
                               str(round(ctx.ml_score, 3)), json.dumps(ctx.shape or {}, default=str)]))
    checked, dropped = 0, []
    for key in ("executive_summary", "subject_profile", "typology_assessment"):
        ok, why = _check(report[key], ev, computed)
        checked += 1
        if not ok:
            dropped.append({"section": key, "text": report[key]["text"], "why": why})
            report[key] = {"text": "(statement removed: could not be verified against evidence)", "evidence_ids": []}
    for key in ("summary_points", "money_trail", "red_flags", "mitigating_factors"):
        kept = []
        for item in report[key]:
            ok, why = _check(item, ev, computed)
            checked += 1
            if ok:
                kept.append(item)
            else:
                dropped.append({"section": key, "text": item["text"], "why": why})
        report[key] = kept
    if not report["summary_points"]:
        report["summary_points"] = _points(ctx, disp)
    # The decision is computed in code, not claimed by the writer: it closes the list unvalidated.
    report["summary_points"] = report["summary_points"][:5] + [{"text": DECISION_POINT[disp.action], "evidence_ids": []}]
    for key in ("executive_summary", "subject_profile", "typology_assessment"):
        report[key]["text"] = clean(report[key]["text"])
    for key in ("summary_points", "money_trail", "red_flags", "mitigating_factors"):
        for item in report[key]:
            item["text"] = clean(item["text"])
    report["headline"] = clean(report["headline"])
    report["next_steps"] = [clean(x) for x in report["next_steps"]][:3]
    # str_narrative keeps its [E1] citations, so the STR draft traces every sentence to the evidence; a cited ID
    # that is not in the ledger is dropped, so no claim points at evidence that does not exist.
    report["str_narrative"], bad = known_citations(report["str_narrative"], {e.id for e in ctx.book.items})
    if bad:
        dropped.append({"section": "str_narrative", "text": ", ".join(bad), "why": "cited evidence IDs not in the ledger"})
    report["validation"] = {"statements_checked": checked, "unsupported": len(dropped),
                            "unsupported_rate": round(len(dropped) / checked, 3) if checked else 0.0,
                            "dropped": dropped}
    return report


DECISION_POINT = {"ESCALATE": "Recommend escalating to a senior analyst.",
                  "REVIEW": "Mixed signals: a person should review it.",
                  "CLOSE": "Looks like normal activity: recommend closing."}


def _points(ctx: CaseContext, disp: Disposition) -> list[dict]:
    """Fallback summary: the strongest reasons each way, in short lines, then the decision."""
    from flowtrace.agent.brief import brief
    strongest = lambda kind, n: sorted(ctx.book.by_kind(kind), key=lambda e: -e.weight)[:n]  # noqa: E731
    return [{"text": brief(e.as_dict()).rstrip(".…") + ".", "evidence_ids": [e.id]} for e in strongest("support", 3) + strongest("counter", 2)]


def template_report(ctx: CaseContext, disp: Disposition) -> dict:
    sup, cnt = ctx.book.by_kind("support"), ctx.book.by_kind("counter")
    prof = next((e for e in ctx.book.items if e.code == "PROFILE"), None)
    shape = ctx.shape or {}
    item = lambda e: {"text": e.claim, "evidence_ids": [e.id]}  # noqa: E731
    trail = [item(e) for e in ctx.book.items if e.tool == "trace_funds"]
    return {
        "headline": f"{disp.action.capitalize()}: {shape.get('typology', 'unclassified').lower()} pattern",
        "summary_points": [],
        "executive_summary": {"text": f"{len(sup)} red flags and {len(cnt)} legitimate explanations were found; "
                                      f"risk {disp.risk} leads to {disp.action}.",
                              "evidence_ids": [e.id for e in (sup + cnt)[:4]]},
        "subject_profile": item(prof) if prof else {"text": "No profile retrieved.", "evidence_ids": []},
        "money_trail": trail[:6],
        "typology_assessment": {"text": shape.get("why", "No dominant shape."),
                                "evidence_ids": [e.id for e in sup[:2]]},
        "red_flags": [item(e) for e in sup],
        "mitigating_factors": [item(e) for e in cnt],
        "unknowns": ["Customer KYC profile and declared purpose of funds are not in the dataset.",
                     "Counterparty identities beyond entity type are not available."],
        "next_steps": ["Request source-of-funds documentation." if disp.action != "CLOSE"
                       else "No action; closure expires after 90 days or on any new red flag."],
        "str_narrative": "",
        "author": "template",
    }


def write_report(ctx: CaseContext, disp: Disposition, use_llm: bool = True) -> dict:
    evidence = [{"id": e.id, "kind": e.kind, "code": e.code, "claim": e.claim, "metrics": e.metrics}
                for e in ctx.book.items]
    if use_llm:
        user = json.dumps({
            "case_id": ctx.case_id, "primary_account": ctx.primary, "case_accounts": ctx.accounts[:20],
            "window": {"start_ms": ctx.t0, "end_ms": ctx.t1},
            "disposition": disp.action, "risk": disp.risk, "confidence": disp.confidence_label,
            "decision_reasons": disp.reasons, "typology": ctx.shape,
            "model_score": round(ctx.ml_score, 3), "evidence": evidence,
        }, default=str)
        out, meta = structured(SYSTEM, user, SCHEMA, effort="low", max_tokens=16000)
        if out is not None:
            out["author"] = meta.get("served_by", "llm")
            out["llm"] = meta
            return validate(out, ctx, disp)
        rep = template_report(ctx, disp)
        rep["llm"] = meta
        return validate(rep, ctx, disp)
    return validate(template_report(ctx, disp), ctx, disp)
