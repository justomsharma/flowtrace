"""Evidence ledger. Every fact in a report must point at an entry here, and every entry points at
the transfers (tx_ids) FalkorDB returned. Facts are computed by tool code, never by the LLM."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Kind = Literal["support", "counter", "context"]


@dataclass
class Evidence:
    id: str
    kind: Kind               # support = points to laundering, counter = points to legitimate activity
    code: str                # stable reason code, e.g. NEW_COUNTERPARTIES, RECURRING_OBLIGATION
    claim: str               # one sentence with the numbers, written by tool code
    weight: float            # 0..1 strength used by the disposition score
    tool: str
    step: int
    tx_ids: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    escalator: bool = False  # support evidence that overrides legitimacy explanations

    def as_dict(self) -> dict:
        return asdict(self)


class EvidenceBook:
    def __init__(self) -> None:
        self.items: list[Evidence] = []
        self.seen_tx: set[str] = set()

    def add(self, **kw) -> Evidence:
        e = Evidence(id=f"E{len(self.items) + 1}", **kw)
        self.items.append(e)
        self.seen_tx.update(e.tx_ids)
        return e

    def get(self, eid: str) -> Evidence | None:
        return next((e for e in self.items if e.id == eid), None)

    def by_kind(self, kind: Kind) -> list[Evidence]:
        return [e for e in self.items if e.kind == kind]

    def codes(self) -> set[str]:
        return {e.code for e in self.items}
