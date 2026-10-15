"""Case disposition: a transparent score computed from the evidence ledger, not by the LLM.

  red    = 1 - prod(1 - w) over supporting evidence          (noisy-OR of red flags)
  legit  = 1 - prod(1 - w) over counter-evidence              (noisy-OR of legitimate explanations)
  risk   = w_ml*ml + w_red*red + w_shape*shape - w_legit*legit*(0.5 if escalated else 1)

ESCALATE  risk >= escalate_at
CLOSE     risk <  close_below and legit >= close_min_legit and no escalator fired
REVIEW    everything else (a human decides)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from flowtrace.agent.evidence import EvidenceBook


@dataclass
class Disposition:
    action: str            # ESCALATE | REVIEW | CLOSE
    risk: float
    confidence: float
    confidence_label: str
    components: dict       # each term of the score, for the UI breakdown
    reasons: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


def _noisy_or(ws: list[float]) -> float:
    p = 1.0
    for w in ws:
        p *= 1.0 - max(0.0, min(w, 0.95))
    return 1.0 - p


def decide(book: EvidenceBook, ml_score: float, shape_conf: float, rules: dict) -> Disposition:
    r = rules["disposition"]
    support = book.by_kind("support")
    counter = book.by_kind("counter")
    red = _noisy_or([e.weight for e in support])
    legit = _noisy_or([e.weight for e in counter])
    escalated = any(e.escalator for e in support)
    legit_eff = legit * (0.5 if escalated else 1.0)

    comps = {
        "model": round(r["w_ml"] * ml_score, 3),
        "red_flags": round(r["w_red"] * red, 3),
        "shape": round(r["w_shape"] * shape_conf, 3),
        "legitimacy": round(-r["w_legit"] * legit_eff, 3),
    }
    risk = max(0.0, min(1.0, sum(comps.values())))

    reasons = []
    if risk >= r["escalate_at"]:
        action = "ESCALATE"
        reasons.append(f"risk {risk:.2f} >= escalation threshold {r['escalate_at']}")
    elif risk < r["close_below"] and legit >= r["close_min_legit"] and not escalated:
        action = "CLOSE"
        reasons.append(f"risk {risk:.2f} < {r['close_below']} and legitimate explanations are strong ({legit:.2f})")
    else:
        action = "REVIEW"
        reasons.append(f"risk {risk:.2f} sits between the close ({r['close_below']}) and escalate ({r['escalate_at']}) thresholds"
                       if risk >= r["close_below"] else "low risk, but the legitimate explanation is not strong enough to close")
    if escalated:
        reasons.append("an escalator fired: " + ", ".join(sorted({e.code for e in support if e.escalator})))

    # Confidence: distance from the nearest decision boundary, plus how much evidence was gathered.
    margin = min(abs(risk - r["escalate_at"]), abs(risk - r["close_below"]))
    n = len(support) + len(counter)
    conf = max(0.3, min(0.97, 0.5 + 1.2 * margin + 0.03 * min(n, 8)))
    label = "High" if conf >= 0.8 else "Medium" if conf >= 0.6 else "Low"
    return Disposition(action, round(risk, 3), round(conf, 2), label,
                       {**comps, "red_raw": round(red, 3), "legit_raw": round(legit, 3), "ml_raw": round(ml_score, 3),
                        "shape_raw": round(shape_conf, 3)}, reasons)
