"""Recurring-flow detection and purpose inference (behaviour memory).

The dataset has no memo or purpose field, so purpose is *inferred* from metadata the way an
analyst would: cadence, amount stability, payment format, who the counterparty is and how many
other accounts it deals with. Every inference carries the numbers it was based on.

Cadence method follows BBVA's recurring-payment approach: median gap between payments mapped to
a cadence band, with gap and amount coefficients of variation as stability tests.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean, median, pstdev

DAY_MS = 86_400_000

PAYROLL_SOURCE_TYPES = {"Corporation", "Partnership"}


@dataclass
class Recurrence:
    cadence: str           # daily | weekly | biweekly | monthly
    period_days: float     # median gap
    occurrences: int
    gap_cv: float
    amount_mean: float
    amount_cv: float
    first_ts: int
    last_ts: int
    fixed_amount: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _cv(xs: list[float]) -> float:
    m = mean(xs)
    return pstdev(xs) / m if m else float("inf")


def detect_recurrence(ts_list: list[int], amounts: list[float], rules: dict) -> Recurrence | None:
    """Return the cadence if payments in one series repeat on a stable schedule, else None.

    rules = config.yaml legitimacy.recurring
    """
    if len(ts_list) < rules["min_occurrences"]:
        return None
    pairs = sorted(zip(ts_list, amounts))
    ts = [p[0] for p in pairs]
    amts = [float(p[1]) for p in pairs]
    gaps = [(b - a) / DAY_MS for a, b in zip(ts, ts[1:])]
    gaps = [g for g in gaps if g > 0.01]  # same-minute duplicates are one payment event
    if len(gaps) < rules["min_occurrences"] - 1:
        return None
    period = median(gaps)
    cadence = next((name for name, (lo, hi) in rules["cadences_days"].items() if lo <= period <= hi), None)
    if cadence is None:
        return None
    gap_cv = _cv(gaps)
    amount_cv = _cv(amts)
    if gap_cv > rules["max_gap_cv"] or amount_cv > rules["max_amount_cv_bill"]:
        return None
    return Recurrence(
        cadence=cadence, period_days=round(period, 2), occurrences=len(ts),
        gap_cv=round(gap_cv, 3), amount_mean=round(mean(amts), 2), amount_cv=round(amount_cv, 3),
        first_ts=ts[0], last_ts=ts[-1], fixed_amount=amount_cv <= rules["max_amount_cv"],
    )


@dataclass
class PurposeGuess:
    purpose: str
    confidence: float
    because: list[str]


def infer_purpose(
    direction: str,
    fmt: str,
    rec: Recurrence | None,
    cp_entity_type: str | None,
    cp_in_degree: int,
    cp_out_degree: int,
    same_owner: bool,
    self_transfer: bool,
    hub_in_degree: int,
) -> PurposeGuess:
    """Best-guess purpose for a flow between an account and one counterparty.

    direction: 'in' (counterparty pays the account) or 'out' (account pays counterparty).
    """
    if self_transfer or fmt == "Reinvestment":
        return PurposeGuess("REINVESTMENT", 0.95, ["transfer to the same account"])
    if same_owner:
        return PurposeGuess("INTERNAL_TREASURY", 0.85, ["both accounts belong to the same entity"])
    if rec is None:
        return PurposeGuess("UNEXPLAINED", 0.0, ["no stable schedule between these accounts"])

    sched = f"{rec.cadence} schedule ({rec.occurrences} payments, gap CV {rec.gap_cv})"
    if direction == "in":
        if (cp_entity_type in PAYROLL_SOURCE_TYPES and cp_out_degree >= 10
                and fmt in ("ACH", "Cheque", "Wire") and rec.fixed_amount):
            return PurposeGuess("PAYROLL", 0.8, [
                sched, f"payer is a {cp_entity_type} paying {cp_out_degree} accounts",
                f"fixed amount (CV {rec.amount_cv}) by {fmt}",
            ])
        return PurposeGuess("RECURRING_INCOME", 0.6, [sched, f"by {fmt}"])

    busy = cp_in_degree >= hub_in_degree
    if rec.fixed_amount and busy:
        return PurposeGuess("FIXED_OBLIGATION", 0.75, [
            sched, f"fixed amount (CV {rec.amount_cv})",
            f"receiver collects from {cp_in_degree} accounts (lender/landlord/subscription-like)",
        ])
    if busy and fmt in ("Credit Card", "Cheque"):
        return PurposeGuess("BILL_PAYMENT", 0.65, [
            sched, f"variable amount (CV {rec.amount_cv}) by {fmt}",
            f"receiver collects from {cp_in_degree} accounts",
        ])
    if rec.fixed_amount:
        return PurposeGuess("STANDING_ORDER", 0.55, [sched, f"fixed amount (CV {rec.amount_cv})"])
    return PurposeGuess("RECURRING_PAYMENT", 0.5, [sched])
