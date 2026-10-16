"""Short reasons for the case page, in standard AML terms: one line (about 12 words) per finding.

Built from the evidence's own numbers (metrics, or the figures in its claim), so results stored before
this module existed get the same lines. "{A}" stands for the subject account; the case view replaces it
with the case's readable name ("Account A"). No IDs, no scores.
"""
from __future__ import annotations

import re

from flowtrace.ml import signal_rates
from flowtrace.settings import cfg

MONEY = re.compile(r"\$[\d,]+")
NUMS = re.compile(r"\d+(?:\.\d+)?")
PCT = re.compile(r"\d+%")
# Account references inside claims ("account …6A6A0") carry digits that are not figures.
ACCOUNT_REF = re.compile(r"accounts?\s+…\w+|…\w+", re.IGNORECASE)


def _money(claim: str) -> str:
    m = MONEY.search(claim)
    return m.group(0) if m else ""


def _nums(claim: str) -> list[str]:
    return NUMS.findall(ACCOUNT_REF.sub("", MONEY.sub("", claim)))


def _n(x: str) -> str:
    """"5.0" -> "5", "0.0" -> "none", "2.5" stays."""
    v = float(x)
    return "none" if v == 0 else str(int(v)) if v == int(v) else x


def _first_words(text: str, n: int = 12) -> str:
    words = re.sub(r"\s*\(.*?\)", "", ACCOUNT_REF.sub("the account", text)).split()
    out = " ".join(words[:n]).rstrip(",;:")
    return out if out.endswith(".") or len(words) <= n else out + "…"


def brief(e: dict) -> str:
    """One short line for an evidence item."""
    code, m, claim = e.get("code", ""), e.get("metrics") or {}, e.get("claim", "")
    nums, money = _nums(claim), _money(claim)
    found = PCT.search(claim)
    pct = found.group(0) if found else None
    lines = {
        # indicators of laundering
        "NEW_COUNTERPARTIES": lambda: f"{m.get('new')} of {m.get('counterparties')} counterparties are new to {{A}}",
        "EQUAL_SPLITS": lambda: "Funds were split into near-identical amounts",
        "PASS_THROUGH": lambda: f"{{A}} forwarded {money} within hours of receipt" if money else "{A} forwarded funds within hours of receipt",
        "FAST_ONWARD_TRANSFER": lambda: f"Receiving accounts forwarded {pct} of the funds within 24 hours" if pct
                                        else "Receiving accounts forwarded the funds within 24 hours",
        "CASH_OR_CRYPTO_EXIT": lambda: f"{money} left the trail as cash or crypto" if money else "Funds left the trail as cash or crypto",
        "HIGH_RISK_RAIL": lambda: f"{money} was paid in cash or crypto" if money else "Payments made in cash or crypto",
        "ROUND_TRIP": lambda: "Funds returned to {A} through other accounts",
        "CYCLE": lambda: "Funds returned to {A} through a loop of accounts",
        "DORMANT_THEN_ACTIVE": lambda: f"{{A}} was inactive {m.get('prior_days')} days, then made {m.get('window_out')} payments",
        "ACTIVITY_SPIKE": lambda: f"{{A}} made {_n(nums[0])} payments a day; usually {_n(nums[1])}" if len(nums) >= 2
                                  else "{A} was far more active than usual",
        "CONVERGENCE": lambda: f"{nums[0]} case accounts paid the same beneficiary" if nums else "Several accounts paid one beneficiary",
        "MANY_SOURCES": lambda: f"{{A}} received funds from {nums[-1]} different accounts" if nums else "{A} received funds from many accounts",
        "NEAR_ESCALATED": lambda: "{A} is linked to a previously escalated account",
        "PRIOR_ESCALATION": lambda: "{A} was escalated in an earlier case",
        # legitimate explanations
        "ESTABLISHED_COUNTERPARTIES": lambda: f"{m.get('established')} of {m.get('counterparties')} counterparties are established relationships",
        "RECURRING_FLOWS": lambda: f"{nums[0]} payments follow a regular schedule" if nums else "Payments follow a regular schedule",
        "SALARY_DISTRIBUTION": lambda: "Regular income followed by regular bill payments",
        "CONSISTENT_WITH_BASELINE": lambda: "{A}'s activity is in line with its history",
        "PAYS_HIGH_VOLUME_RECEIVERS": lambda: "Payments go to high-volume merchants, lenders or utilities",
        "INTERNAL_TRANSFER": lambda: "Transfers are between accounts of the same owner",
        "FUNDS_STOP": lambda: "Receiving accounts retained the funds",
        "BEHAVIOUR_MEMORY": lambda: "{A} has an established payment history",
        "PRIOR_CLOSURE": lambda: "A similar earlier alert was reviewed and closed",
    }
    try:
        line = lines[code]() if code in lines else _first_words(claim)
    except (IndexError, KeyError, TypeError, ValueError):
        line = _first_words(claim)
    return line if "None" not in line else _first_words(claim)


# Verification steps: what an analyst obtains or checks to confirm or rule out each indicator.
VERIFY = {
    "NEW_COUNTERPARTIES": "Obtain invoices or contracts for the new counterparties.",
    "PASS_THROUGH": "Check KYC: payment processors and agents forward funds legitimately.",
    "FAST_ONWARD_TRANSFER": "Confirm whether the receiving accounts are merchants settling with suppliers.",
    "EQUAL_SPLITS": "Request payroll or loan records that explain the equal amounts.",
    "CASH_OR_CRYPTO_EXIT": "Check KYC for a cash-intensive business, such as retail or hospitality.",
    "HIGH_RISK_RAIL": "Check KYC for a cash-intensive business, such as retail or hospitality.",
    "CYCLE": "Request evidence of a refund or loan repayment for the returned funds.",
    "ROUND_TRIP": "Request evidence of a refund or loan repayment for the returned funds.",
    "DORMANT_THEN_ACTIVE": "Confirm the account opening date and expected activity in KYC.",
    "ACTIVITY_SPIKE": "Check for a seasonal or month-end business reason for the volume.",
    "CONVERGENCE": "Confirm whether the common beneficiary is a known supplier or tax authority.",
    "MANY_SOURCES": "Check whether the account collects customer payments, such as retail or rent.",
    "NEAR_ESCALATED": "Review the relationship with the previously escalated account.",
    "PRIOR_ESCALATION": "Review the outcome of the earlier escalation.",
}
UNMEASURED_WHY = {
    "NEAR_ESCALATED": "Close links to an escalated account raise the risk.",
    "PRIOR_ESCALATION": "The account was already referred to a senior analyst.",
    "ACTIVITY_SPIKE": "A sharp rise against the account's own history.",
    "MANY_SOURCES": "Pooling funds from many unrelated accounts is a placement pattern.",
    "SALARY_DISTRIBUTION": "This is how salary accounts typically behave.",
    "RECURRING_FLOWS": "Scheduled payments are typical of bills and salaries.",
    "PAYS_HIGH_VOLUME_RECEIVERS": "High-volume receivers are usually merchants, lenders or utilities.",
    "INTERNAL_TRANSFER": "Moving funds between one owner's accounts is routine.",
    "FUNDS_STOP": "Laundered funds usually keep moving; these did not.",
    "BEHAVIOUR_MEMORY": "Consistent history lowers the risk.",
    "PRIOR_CLOSURE": "A similar alert was already reviewed and closed.",
    "CONSISTENT_WITH_BASELINE": "Activity matches the account's own history.",
}


def assess(e: dict) -> dict:
    """Strength of an indicator measured on the dataset's training days, and how to verify it.

    tag    Strong sign / Moderate sign / Common in normal accounts (indicators); Strong sign of normal (explanations)
    why    one line with the measured rate, or the analyst's rationale when the indicator was not measured
    check  the verification step (indicators only)
    """
    code, kind = e.get("code", ""), e.get("kind")
    tag, why = "", UNMEASURED_WHY.get(code, "")
    try:  # display only: without the measurement file the page shows no tags (the disposition fails loudly)
        lift = signal_rates.lift(code, cfg()["disposition"]["measured_weights"])
        share = signal_rates.rates()["codes"][code]["share"] if lift is not None else 0.0
    except FileNotFoundError:
        lift = None
    if lift is not None:
        if kind == "support":
            if lift >= 3:
                tag, why = "Strong sign", f"{lift:.0f}x the average laundering rate in historical payments."
            elif lift >= 1.5:
                tag, why = "Moderate sign", f"{lift:.1f}x the average laundering rate in historical payments."
            else:
                tag, why = "Common in normal accounts", f"Seen in {share:.0%} of payments; weak on its own."
        elif lift <= 0.5:
            tag, why = "Strong sign of normal", f"{round(1 / lift):g}x below the average laundering rate."
    return {"tag": tag, "why": why, "check": VERIFY.get(code, "") if kind == "support" else ""}


DETECTOR_LINES = {
    "FAN-OUT": lambda d: f"Paid {d[0]} different accounts in a short period" if d else "Paid many accounts in a short period",
    "FAN-IN": lambda d: f"Received from {d[0]} different accounts in a short period" if d else "Received from many accounts",
    "PASS-THROUGH": lambda d: "Funds were forwarded within hours of receipt",
    "SCATTER-GATHER": lambda d: f"{d[0]} intermediary accounts routed funds to one beneficiary" if d else "Intermediaries routed funds to one beneficiary",
    "GATHER-SCATTER": lambda d: "Funds pooled from many accounts, then paid out to many",
    "CYCLE": lambda d: "Funds moved in a loop back to the sender",
}


def alert_reasons(case: dict, limit: int = 4) -> list[str]:
    """Why the case was flagged before any investigation: the pattern detectors that fired, then the model."""
    out: list[str] = []
    seen: set[str] = set()
    for h in sorted(case.get("detector_hits", []), key=lambda h: -h.get("strength", 0)):
        det = h.get("detector")
        if det in seen or det not in DETECTOR_LINES:
            continue
        seen.add(det)
        out.append(DETECTOR_LINES[det](_nums(h.get("detail", ""))))
    model = next((s["p"] for s in case.get("signals", []) if s.get("signal") == "model"), case.get("ml_score"))
    if model is not None:
        level = "high" if model >= 0.7 else "elevated" if model >= 0.3 else "slightly elevated"
        out.append(f"The risk model scores these payments as {level} risk")
    return out[:limit]
