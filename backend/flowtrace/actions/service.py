"""Actions on cases: the agent proposes, a person approves or rejects with a reason.

  propose   keyed on dedup_key = "case|type|account": a double click, a re-plan, or the app and the MCP
            server racing each other all get the one existing action. A HOLD is refused once the case has
            actions.max_holds_per_case open holds, checked in the same write. proposed_by is 'agent' (the
            app) or 'mcp' (an MCP client), never a person.
  decide    conditional write (only a PROPOSED action changes). The same idempotency key replays the
            first answer; any other second decision is a conflict (409).
  expire    an approved HOLD ends at the end of its Nth business day (UTC, Mon-Fri) and is released
            automatically, with its own audit entry.
  auto-hold the agent may place a proposed HOLD PROVISIONALLY (actions.auto_hold, only from the API's act step).
            A person confirms it (approve: it then runs its normal term) or rejects it (released). Unconfirmed, it
            is released at the end of actions.auto_hold.confirm_business_days. Audited as "agent (auto-hold)".
  verdict   approving FILE_STR / ESCALATE / CLOSE writes the analyst's verdict on the Case node; case
            memory then prefers it over the agent's disposition.

Every change writes an (:Audit) node in the same query. Audit nodes are never changed or deleted.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

from flowtrace.graph import queries as Q
from flowtrace.graph.client import ro, rw
from flowtrace.settings import cfg

DAY_MS = 86_400_000
PROPOSERS = ("agent", "mcp")
ACTION_TYPES = ("HOLD", "RELEASE", "ESCALATE", "FILE_STR", "CLOSE")
NEEDS_ACCOUNT = ("HOLD", "RELEASE")
VERDICT = {"FILE_STR": "ESCALATE", "ESCALATE": "ESCALATE", "CLOSE": "CLOSE"}
DECIDABLE = ("PROPOSED", "PROVISIONAL")  # PROVISIONAL: an auto-hold waiting for a person to confirm it
AUTO_HOLD_ACTOR = "agent (auto-hold)"


class ActionError(Exception):
    """status follows HTTP: 404 unknown action, 409 already decided, 422 bad input."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def now_ms() -> int:
    return int(time.time() * 1000)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def add_business_days(ts_ms: int, n: int) -> int:
    """End (last millisecond, UTC) of the nth business day after the day of ts_ms. Bank holidays are not counted."""
    d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
    left = n
    while left > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000) + DAY_MS - 1


def get(action_id: str) -> dict | None:
    rows = ro(Q.GET_ACTION, {"id": action_id}).rows
    return dict(rows[0]["action"]) if rows else None


def propose(case_id: str, type: str, *, account: str | None = None, hold_amount: float = 0.0,
            on_routes: float = 0.0, currency: str = "US Dollar", rate: float = 1.0, why: str = "",
            evidence_ids: list[str] | tuple = (), tx_ids: list[str] | tuple = (), proposed_by: str = "agent",
            now: int | None = None) -> tuple[dict, bool]:
    """Create a PROPOSED action, or return the current one for the same case, type and account.
    hold_amount is the money the hold freezes (a lien on the amount); on_routes is the assumption figure."""
    if proposed_by not in PROPOSERS:
        raise ActionError(422, f"proposals come from {' or '.join(PROPOSERS)}, not {proposed_by!r}")
    if type not in ACTION_TYPES:
        raise ActionError(422, f"unknown action type {type!r}; use one of {', '.join(ACTION_TYPES)}")
    if type in NEEDS_ACCOUNT and not account:
        raise ActionError(422, f"a {type} needs an account")
    now = now or now_ms()
    props = {
        "id": _id("A"), "type": type, "status": "PROPOSED", "case_id": case_id, "account": account,
        "hold_amount": float(hold_amount), "on_routes": float(on_routes), "currency": currency,
        "rate": float(rate), "why": why, "evidence_ids": list(evidence_ids), "tx_ids": list(tx_ids)[:20],
        "expiry_days": cfg()["actions"]["hold_expiry_business_days"] if type == "HOLD" else None,
        "proposed_by": proposed_by, "proposed_at": now, "dedup_key": f"{case_id}|{type}|{account or ''}",
    }
    props = {k: v for k, v in props.items() if v is not None}
    cap = cfg()["actions"]["max_holds_per_case"] if type == "HOLD" else None
    row = rw(Q.PROPOSE_ACTION, {"case_id": case_id, "dedup_key": props["dedup_key"], "props": props,
                                "account": account, "audit_id": _id("AU"), "now": now, "cap": cap}).rows[0]
    if row["action"] is None:
        raise ActionError(409, f"this case already has {row['open_holds']} open holds (the most is {cap})")
    return dict(row["action"]), bool(row["created"])


def decide(action_id: str, approve: bool, reason: str, by: str = "analyst", key: str | None = None,
           auth: str | None = None, now: int | None = None) -> dict:
    """A person approves or rejects a proposed action. Only the API calls this, after checking the approver
    token (auth says how the approver was checked, and goes in the audit log). MCP has no way to reach it."""
    reason = (reason or "").strip()
    by = (by or "").strip()[:80] or "analyst"
    if len(reason) < cfg()["actions"]["min_reason_chars"]:
        raise ActionError(422, "write a short reason for the decision")
    a = get(action_id)
    if a is None:
        raise ActionError(404, "no such action")
    if a["status"] not in DECIDABLE:
        return _already(a, key)
    now = now or now_ms()
    to = "APPROVED" if approve else "REJECTED"
    expires = add_business_days(now, a["expiry_days"]) if approve and a["type"] == "HOLD" else None
    # One query: the decision, its audit entry, the verdict, the release and withdrawn alternatives.
    rows = rw(Q.DECIDE_ACTION, {"id": action_id, "to": to, "by": by, "now": now, "reason": reason, "key": key,
                                "expires_ts": expires, "audit_id": _id("AU"), "auth": auth,
                                "verdict": VERDICT.get(a["type"]) if approve else None}).rows
    if not rows:  # someone else decided between our read and our write
        return _already(get(action_id), key)
    return dict(rows[0]["action"])


def _already(a: dict, key: str | None) -> dict:
    if key and a.get("decision_key") == key:
        return a
    raise ActionError(409, f"already {a['status'].lower()} by {a.get('decided_by') or 'the system'}")


def provisional_hold(action_id: str, reason: str, now: int | None = None) -> dict:
    """The auto-hold policy places a proposed HOLD provisionally. Only the API's act step calls this, after
    checking the policy (agent.registry.auto_hold); a person still confirms or rejects it."""
    now = now or now_ms()
    until = add_business_days(now, cfg()["actions"]["auto_hold"]["confirm_business_days"])
    rows = rw(Q.PROVISIONAL_HOLD, {"id": action_id, "now": now, "until": until, "reason": reason,
                                   "actor": AUTO_HOLD_ACTOR, "audit_id": _id("AU")}).rows
    if rows:
        return dict(rows[0]["action"])
    a = get(action_id)
    if a is None:
        raise ActionError(404, "no such action")
    raise ActionError(409, f"only a proposed hold can be auto-held; this one is {a['status'].lower()}")


def hold_history(case_id: str) -> dict:
    """verdict: a person's verdict on the case; decided_holds: holds of the case that left PROPOSED."""
    r = ro(Q.HOLD_HISTORY, {"case_id": case_id}).rows
    return {"verdict": r[0]["verdict"] if r else None, "decided_holds": r[0]["decided_holds"] if r else 0}


def expire_due(now: int | None = None) -> int:
    """Release approved holds whose time is up, and auto-holds nobody confirmed in time. Safe to call from
    anywhere, any number of times."""
    now = now or now_ms()
    return rw(Q.EXPIRE_DUE, {"now": now}).rows[0]["n"] + rw(Q.EXPIRE_PROVISIONAL, {"now": now}).rows[0]["n"]


def retire_batch(case_prefix: str, reason: str, now: int | None = None) -> dict:
    """A batch was uploaded again and its cases renumbered: withdraw its open proposals and retire its decided
    actions, so none of them attaches to a different case that now has the same id."""
    p = {"prefix": case_prefix, "reason": reason, "now": now or now_ms()}
    return {"withdrawn": rw(Q.EXPIRE_PROPOSALS, p).rows[0]["n"], "retired": rw(Q.RETIRE_DECIDED, p).rows[0]["n"]}


def list_actions(case_id: str | None = None, status: str | None = None, expire: bool = True) -> list[dict]:
    """expire=False keeps it read-only (MCP tools): a hold past its time then still reads APPROVED until
    the API's expiry timer or the next read here releases it."""
    if expire:
        expire_due()
    return [dict(r["action"]) for r in ro(Q.LIST_ACTIONS, {"case_id": case_id, "status": status}).rows]


def audit(case_id: str | None = None, limit: int = 200) -> list[dict]:
    """The audit trail, newest first."""
    expire_due()
    return [dict(r["entry"]) for r in ro(Q.AUDIT_LOG, {"case_id": case_id}).rows[:limit]]
