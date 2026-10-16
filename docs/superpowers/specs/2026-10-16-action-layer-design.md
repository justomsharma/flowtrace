# Action layer: hold planner, human-approved actions, MCP server

Status: design (decided by the engineer; the user asked for decisions, not options).

## Why

FlowTrace explains cases but does not act. Track 1 asks for an agent that acts on connected data. Banks act on
laundering by holding money while a human decides:

- UK, FCA FG24/6 (PSRs reg 86(2A), in force 30 Oct 2024): an outbound payment may be delayed until the end of the
  4th business day after it was received, only while the PSP investigates, and the payer is told why.
- India, RBI draft KYC Amendment Directions 2026 (11 Sep 2026): a debit hold on the suspected mule **amount**
  (an account-wide freeze is the last resort), customer told at once, 60 days maximum.
- Vendors (Unit21, Lucinity, Feedzai): the AI recommends and a human approves, with every decision in an audit trail.

So the agent proposes holds. Only a person approves or rejects them, and the person must give a reason.

## Goals

1. A deterministic hold planner (no LLM) that picks the fewest accounts to hold to block the most case money
   that is still in the trail.
2. Actions stored in the graph, proposed by the agent and decided by a person, with an append-only audit trail.
   Holds expire on their own.
3. Analyst verdicts written back to the Case node. Case memory prefers human verdicts.
4. An API, an MCP server, and UI panels on top of the same tool registry the in-app agent uses.
5. An honest measurement on the Sep 9 eval slice and on the curated sample. No tuning on Sep 9.

## Non-goals and anti-goals

- No real payment-system integration. A "hold" is a recorded decision.
- The agent never approves. MCP has no approve tool. Approval exists only in the HTTP API that the UI calls.
- Never hold a hub or merchant, whatever any caller asks.
- No auth system. The analyst name is a free-text field, defaulting to "analyst".

## Hold planner (backend/flowtrace/actions/planner.py)

**When the decision is made.** The decision is made at the end of the case window (t1). That is when a batch
run can first act.

**Trail.** The trail is a time-respecting forward trace from the case accounts, starting at t0. It reuses
`graph.trace.expand` with the `trace` rules and adds the case's alert payments. Only payments before t1 are kept.

**Money at rest.** For each trail account:
- `trail_in(v)` is the trail money paid into v. For a case account, it is every payment received in the window.
- `r(v) = clip(trail_in(v) − out(v), 0, trail_in(v))`, where `out(v)` is everything v sent after the trail money
  first reached it and before t1.
- This is conservative: any payment out is assumed to carry the case money first.

**Already out.** Money that left by a hard-to-trace rail or reached a hub is not blockable. It is reported as
"already out":
- hard-to-trace rails are `escalators.high_risk_formats` (Cash, Bitcoin), plus cross-currency payments as a proxy
  for cross-border;
- money that reached a hub counts as already out.

**Money still in motion.** R = Σ r(v) over holdable trail accounts.

**Hubs are never holdable.** An account is a hub if any of the existing rules says so:
- `cases.hub_degree` over the `cases.hub_lookback_days` window;
- `trace.hub_degree` on total degree;
- `legitimacy.hub_receiver_in_degree` on in-degree.

**Flow network.**
- Every holdable account is split into `v_in → v_out`.
- The super-source S connects to `v_in`, carrying `r(v)`.
- Each trail payment `u → v` becomes `u_out → v_in`.
- `v_out → T` (an exit) when any of these is true:
  - v already sent money by a hard-to-trace or cross-currency payment;
  - v paid a hub;
  - v has no onward trail payment (end of trace: the money can leave by any route).
- Money at rest is assumed to keep using the routes the case has used so far. This is the network interdiction
  assumption.

**Choosing the holds.**
- Run a minimum vertex cut S→T, where each holdable account costs `1 − 0.1·r(v)/R`.
- This gives the fewest accounts that cut every route from the money at rest to an exit. Among cuts of the same
  size, it prefers accounts where more money already sits.
- If the cut is larger than `actions.max_holds_per_case`, holds are picked greedily instead, one at a time, by how
  much more money each one blocks.
- Holds that block less than `actions.min_hold_usd` are dropped.
- Money blocked by a set H is the money at rest at accounts that are in H, or that can no longer reach T without
  passing H. All inner capacities are infinite, so this is exactly R minus the max flow of the network without H.

**Output per hold:**
- account;
- `amount_blocked`, as the marginal amount in the order chosen;
- `frozen_now`, the money already at that account (certain);
- why, in plain words;
- evidence ids (ledger items that name the account) and the trail payment ids into it;
- the expiry rule.

**Plan output:**
- `in_motion`, `already_out`, `blocked`, `frozen_now`;
- the method (`min-cut` or `greedy`) and the min-cut size;
- the network (for the map), the hubs that were skipped, and the currency.

**Gating.** Holds are proposed only for dispositions in `actions.propose_for` (ESCALATE, REVIEW). A plan also
carries one case-level proposal:
- ESCALATE becomes FILE_STR;
- REVIEW becomes ESCALATE;
- CLOSE becomes CLOSE.

**Currency.** Graph amounts are USD (converted at the dataset's fixed rates). If every trail payment uses one
currency, the plan carries that currency and its rate from `fx_rates.json`, and the UI shows that currency.
Otherwise it shows US dollars and says the currencies were mixed.

**Why not `algo.maxFlow`.** FalkorDB 6.0.2 has `algo.maxFlow` (checked with `dbms.procedures()`), but:
- it returns the flow, not the cut;
- it has no node capacities;
- it runs over stored edges, so it cannot respect time order, node splitting or the super-source without writing a
  temporary network into the graph.

The trail is small (≤ a few hundred accounts), so the cut runs in networkx on the trail read from FalkorDB.

## Actions and audit (backend/flowtrace/actions/service.py)

**Nodes and relationships:**
- `(:Action {id, type, status, case_id, account, amount_blocked, frozen_now, currency, rate, expiry_days, expires_ts, proposed_by, proposed_at, decided_by, decided_at, reason, why, evidence_ids, tx_ids, dedup_key, decision_key})`
- `-[:FOR]->(:Case)` and `-[:ON]->(:Account)`.
- `(:Audit {id, ts, actor, event, action_id, case_id, from_status, to_status, reason})` nodes are only ever
  CREATEd. No product query sets or deletes them.

**Types:** HOLD, RELEASE, ESCALATE, FILE_STR, CLOSE. **Statuses:** PROPOSED, APPROVED, REJECTED, EXPIRED.

**Rules:**
- **No duplicates.** Proposals MERGE on `dedup_key` (`case|type|account`). A double click, a re-plan, or the app
  and MCP racing each other all return the existing action. The key is cleared only when a hold expires or is
  released, so a rejected proposal is not proposed again for the same case.
- **Approve and reject** are conditional writes (`WHERE status = 'PROPOSED'`). They need a reason of at least
  `actions.min_reason_chars` and take an idempotency key. The same key returns the same result; any other second
  decision gets a 409.
- **Expiry.** Approving a HOLD sets `expires_ts` to the end of the Nth business day after approval (UTC, Mon–Fri,
  `actions.hold_expiry_business_days` = 4). Due holds turn EXPIRED with an `AUTO_RELEASED` audit entry. This runs
  lazily on every action/audit read and once a minute in the API process.
- **Release.** Approving a RELEASE expires that case's approved hold on that account.
- **Re-upload.** Re-uploading a batch expires its still-PROPOSED actions (audit: `CASE_REUPLOADED`). Approved
  holds stay.
- **Verdict write-back.** Approving FILE_STR or ESCALATE sets `c.verdict = 'ESCALATE'`; approving CLOSE sets
  `c.verdict = 'CLOSE'`. `verdict_by`, `verdict_at` and `verdict_reason` are set too, and a `VERDICT` audit entry
  is written.
- **Case memory.** PRIOR_CASES and NEAREST_ESCALATED use `coalesce(c.verdict, c.disposition)` and list human
  verdicts first. The `created_ts < $t0` rule and the batch-prefix rule stay.

## One tool registry (backend/flowtrace/agent/registry.py)

- `REGISTRY` lists every tool: the investigation tools (`tools.TOOLS`, scoped to a case context), `get_case`,
  `list_cases`, `plan_holds` (read), and `propose_action` (the only write).
- The in-app LLM policy reads its tool list from the registry.
- After an investigation is accepted, the app's "act" step calls `plan_holds` then `propose_action` through the
  registry.
- The MCP server registers the same entries.
- Case inputs and results are read from `data/runs` by `flowtrace/store.py`, so the MCP process needs no API
  process.
- Investigation tools in MCP keep one context per case for the session, so accounts found by a trace can be
  profiled next.
- `propose_action` checks every caller (app or MCP) the same way: the type is known, a HOLD account is on the trail
  and is not a hub, the per-case cap holds, and `proposed_by` is always `agent`.

## API (backend/flowtrace/api/main.py)

- `POST /api/cases/{id}/plan`: computes the plan, stores proposals, and returns `{plan, actions}`. Needs a finished
  investigation (409 otherwise).
- `GET /api/cases/{id}/plan`: the stored plan and the current actions.
- `GET /api/actions?status=&case_id=`.
- `POST /api/actions/{id}/approve` and `/reject`: body `{reason, by?}`, `Idempotency-Key` header.
- `GET /api/audit?case_id=&limit=`.

## MCP (backend/flowtrace/mcp/server.py)

- Uses mcp 2.3 `MCPServer` with stdio by default and `--http` for streamable HTTP.
- Read-only tools are annotated `read_only_hint=True` and run through `ro()` (GRAPH.RO_QUERY).
- `propose_action` is annotated non-read-only and idempotent.
- The README shows the Claude Desktop config.
- A test lists the tools and calls one in-process (`mcp.Client(server)`).

## UI

- **Case panel, "Confirm hold":**
  - the effect line: "Holding 2 accounts blocks $X of $Y still in motion";
  - each proposed hold: account, amount, frozen now vs via route, expiry, evidence claims;
  - Approve / Reject with a required reason;
  - the case-level proposal with the same controls;
  - held accounts highlighted on the money map.
- **"Results" page:** money blocked today, active holds with live countdowns, pending proposals, and the audit log.
- Same tokens, same panel style, sentence case.

## Measurement (backend/flowtrace/eval/holds.py)

- Runs on the eval slice (Sep 9) and the curated sample, with the heuristic investigation (no LLM, deterministic)
  for the disposition gate.
- Per case: money in motion, money blocked, holds.
- Overall: hold precision, where a hold counts as right when its account sent or received a labelled laundering
  payment.
- Hubs proposed, rechecked independently. This must be 0.
- Config values are set before the run and are not changed after seeing Sep 9.
- The uploaded batch is deleted afterwards unless it was already there.

## Builder's calls (marked as mine)

- The route assumption, and showing "frozen now" separately from "via route" so the certain part is visible.
- The min-cut tie-break weight of 0.1.
- 4 business days ignores bank holidays.
- A cap of 3 holds and a $1,000 minimum, chosen from the pass-through rule's minimum amount before measuring.
- The approval step has no login. `decided_by` is whatever name the analyst types (default "analyst").

## Update after review (round 2)

These decisions replace the matching parts above.

- **A hold is a lien on an amount.** `hold = min(money at rest in the account since the case money arrived, case money traced into it)`. This follows the RBI draft (Sep 2026) and the Indian courts, which limit a lien to the disputed amount. The stored action carries `hold_amount`; `amount_blocked` is gone.
- **The headline is "frozen now"** (the sum of the holds). "On the case's routes" is reported apart and labelled as an assumption.
- **The minimum vertex cut is context only.** A lien on an account with nothing in it freezes nothing, so holds are picked by the money they freeze. Ties go to the account that sits on more routes.
- **Holds only after ESCALATE** (decided after seeing Sep 9).
- **Approving CLOSE** withdraws the case's proposed holds and releases its approved ones, in the same query.
- **Approve/reject need the API's `APPROVER_TOKEN`** (`X-Approver-Token`) and an approver name. The audit records `auth`. The MCP process drops the token.
- **MCP proposals** are recorded as `proposed_by: "mcp"`. They get the same minimum, cap and gate as the agent's. The cap is checked inside the propose write.
- **Measurement:**
  - hold precision counts only laundering before the decision time, and is shown next to the base rate;
  - the hub metric is dropped (hubs are blocked by code and tests);
  - the legitimate-case column is reported as a cost.
- **Hub degrees use only payments before the decision time** (`DEGREES_UNTIL`).
