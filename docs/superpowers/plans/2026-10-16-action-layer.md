# Action Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FlowTrace proposes holds that block a case's money with the fewest accounts. A human approves or rejects
each one. Every change is audited. The same tools are served over MCP.

**Architecture:**
- A deterministic planner reads the case's time-ordered money trail from FalkorDB (read-only) and runs a vertex
  min-cut in networkx.
- A service writes Action and Audit nodes with conditional, merge-based Cypher.
- One tool registry feeds the in-app agent, the API's "act" step and the MCP server.

**Tech Stack:** Python 3.11, FalkorDB 6.0.2, networkx, FastAPI, mcp 2.3.0 (`MCPServer`), React + Vite + Tailwind.

**Spec:** `docs/superpowers/specs/2026-10-16-action-layer-design.md`

## Global Constraints

- Every threshold lives in `config.yaml` under `actions:`:
  - `hold_expiry_business_days: 4`
  - `max_holds_per_case: 3`
  - `min_hold_usd: 1000`
  - `propose_for: [ESCALATE, REVIEW]`
  - `min_reason_chars: 5`
- Cypher follows the `queries.py` docstring rules:
  - `t.ts + 0`;
  - `IN $ids`;
  - literal LIMIT;
  - bind the TRANSFER variable.
- Agent and MCP reads go through `ro()`. Only `actions/service.py` (and the existing loaders and case memory) call
  `rw()`.
- `proposed_by` is always `'agent'`. No code path lets the agent approve.
- Audit nodes are only CREATEd.
- Tests that write use the scratch graph `zz_test_actions`, deleted afterwards. Main graph counts must match
  `scratchpad/baseline_counts.txt` at the end.
- Do not commit any of these: `data/models/*`, `data/processed/fx_rates.json`, `.gitignore`, `data/samples/eval/`.
- Commit messages must not mention Claude.
- UI copy is plain, in sentence case. Amounts are shown in the trail's own currency.

## Review Focus

1. **Double-clicked approve.** The same idempotency key returns the same action. A different key gets a 409. Only
   one audit entry is written. (Task 2.)
2. **Hub on the trail with money behind it.** It is never in the plan, and `propose_action` refuses it. (Tasks 1
   and 3.)
3. **Case with no result yet.** POST plan returns 409 and makes no writes. (Task 4.)
4. **Expired hold read after its time.** It shows as EXPIRED with one AUTO_RELEASED entry, even if read twice.
   (Task 2.)
5. **Mixed-currency trail.** The plan says US Dollar with rate 1. (Task 1.)

---

### Task 1: Hold planner

**Files:**
- Create: `backend/flowtrace/actions/__init__.py`, `backend/flowtrace/actions/planner.py`
- Modify: `backend/flowtrace/graph/queries.py`:
  - add `t.cur AS cur` to the two trace hop queries;
  - add a `HOLD_FLOWS` query.
- Modify: `config.yaml`, to add the `actions:` block.
- Modify: `backend/tests/fakegraph.py`, so it answers `HOLD_FLOWS`.
- Test: `backend/tests/test_planner.py`

**Interfaces:**
- Produces:
  - `plan_holds(case: dict, rules: dict, result: dict | None = None) -> dict`
    - Keys: `case_id, decided_at, in_motion, already_out, blocked, frozen_now, method, min_cut_size, holds[], case_action, skipped_hubs[], currency, rate, network{nodes, edges}, note`.
    - Each hold: `{account, amount_blocked, frozen_now, why, evidence_ids, tx_ids, expiry_days}`.
    - `case_action`: `{type, why} | None`.
  - `choose_holds(supply: dict[str, float], routes: dict[str, set[str]], exits: set[str], cap: int, min_usd: float) -> tuple[list[tuple[str, float]], str, int]`
    - Pure function. Returns `(account, marginal)` in order, the method, and the min-cut size.
- `HOLD_FLOWS(ids, since:[{id, ts}], t1, exit_fmts)` returns one row per account:
  `id, out_sum, exit_sum, in_sum`.

- [ ] Step 1: Write the pure tests in `test_planner.py`:
  - `test_collector_chokepoint_beats_holding_each_mule`: 5 mules each with 2000 at rest, every one routes to C,
    C routes to an exit. Expect 1 hold `C`, method `min-cut`, 10000 blocked.
  - `test_cap_falls_back_to_greedy_by_money`: 5 separate leaves with 5000, 4000, 3000, 2000, 1000 and cap 3.
    Expect the holds to be the 3 largest, method `greedy`, min_cut_size 5.
  - `test_small_holds_dropped`: a leaf with 300 is under `min_usd`, so it is never proposed.
  - `test_tie_prefers_money_at_rest`: A (1000 at rest) → B (5000 at rest) → exit. Expect `[B]`, which blocks 6000.
- [ ] Step 2: Write the fake-graph tests in `test_planner.py`:
  - `test_plan_on_fake_trail_never_holds_hub`: a suspect fans out to 3 mules, one of which pays a hub with degree
    600. The hub is not in the holds and is in `skipped_hubs`.
  - `test_plan_reports_motion_and_already_out`: cash out counts in `already_out`, and money at rest counts in
    `in_motion`.
  - `test_closed_case_gets_no_holds`: with `result.disposition.action == 'CLOSE'`, `holds == []` and
    `case_action.type == 'CLOSE'`.
  - `test_single_currency_plan_carries_rate`: every trail payment is in Euro, so `currency == 'Euro'`.
  - `test_mixed_currency_plan_is_usd`: a mixed-currency trail gives `currency == 'US Dollar'`.
- [ ] Step 3: Run `pytest tests/test_planner.py`. It fails because there is no module.
- [ ] Step 4: Implement:
  - `choose_holds`: an nx DiGraph with node split, cost `1 − 0.1·r/R`, and `nx.minimum_cut`.
    - If the cut is no bigger than `cap`, order it by greedy marginal.
    - Otherwise use greedy reachability blocking.
    - Drop holds whose marginal is below `min_usd`.
  - `plan_holds`:
    - seeds are the case accounts at t0;
    - run `expand` forward with the trace rules, add the alert edges, and keep only ts < t1;
    - check hubs with `HUB_DEGREES` and `DEGREES`;
    - run `HOLD_FLOWS`;
    - compute residuals, exits and currency.
- [ ] Step 5: Run `pytest tests/test_planner.py tests/test_agent.py`. Everything passes.
- [ ] Step 6: Run a smoke test against the real graph: `python -c` runs `plan_holds` on one stored case from
  `data/runs/cases`. Check the queries are under 100 ms and nothing errors.
- [ ] Step 7: Commit with the message "Hold planner: min vertex cut over the time-ordered money trail".

### Task 2: Actions service, audit and verdict write-back

**Files:**
- Create: `backend/flowtrace/actions/service.py`
- Modify: `backend/flowtrace/graph/queries.py`:
  - add the action queries;
  - make `PRIOR_CASES` and `NEAREST_ESCALATED` use `coalesce(c.verdict, c.disposition)` and put human verdicts
    first.
- Modify: `backend/flowtrace/agent/tools.py`: when a prior case has a human verdict, the `case_memory` claims say
  "an analyst confirmed".
- Modify: `backend/tests/fakegraph.py`: add the field `decided_by` to rows for the changed queries; they are still
  empty.
- Test: `backend/tests/test_actions_service.py` (scratch FalkorDB; skipped if FalkorDB is down).

**Interfaces:**
- Produces:
  - `propose(case_id, type, *, account=None, amount_blocked=0.0, frozen_now=0.0, currency='US Dollar', rate=1.0, why='', evidence_ids=(), tx_ids=(), now=None) -> tuple[dict, bool]`
  - `decide(action_id, approve: bool, reason: str, by: str = 'analyst', key: str | None = None, now=None) -> dict`
    - Raises `ActionError(status, msg)` with status 404, 409 or 422.
  - `expire_due(now=None) -> int`
  - `list_actions(case_id=None, status=None) -> list[dict]`
  - `audit(case_id=None, limit=200) -> list[dict]`
  - `expire_proposals(case_prefix: str, reason: str) -> int`
  - `add_business_days(ts_ms: int, n: int) -> int`: end of the nth business day in UTC.
  - `ACTION_TYPES`, `ActionError`

- [ ] Step 1: Write the tests:
  - `test_add_business_days_skips_weekend`: Fri 2026-10-16 + 4 is the end of Thu 2026-10-22.
  - `test_propose_twice_returns_same_action_and_one_audit`.
  - `test_approve_needs_reason`: 422.
  - `test_double_approve_same_key_replays_other_key_409`: the audit count is 2 (PROPOSED and APPROVED).
  - `test_reject_then_replan_does_not_repropose`.
  - `test_expiry_auto_releases_once`: two `expire_due` calls produce one AUTO_RELEASED entry and status EXPIRED.
  - `test_approve_file_str_writes_verdict_and_prior_cases_prefer_it`:
    - Set up a Case with agent disposition REVIEW and `created_ts < t0`.
    - Approving FILE_STR makes PRIOR_CASES return `disposition == 'ESCALATE'` and `decided_by == 'human'`.
    - `NEAREST_ESCALATED` finds it.
  - `test_human_close_overrides_agent_escalate_in_nearest`.
  - `test_audit_is_create_only`: no query in `queries.py` that mentions `:Audit` contains `SET` on the audit
    variable, `DELETE` or `REMOVE`.
- [ ] Step 2: Run the tests. They fail.
- [ ] Step 3: Implement the queries and the service:
  - all writes go through `rw()`;
  - each change writes the action and its Audit in one query;
  - `decide` re-reads the action when its conditional write returns no row, then replays or raises 409.
- [ ] Step 4: Run the tests. They pass, and the scratch graph is deleted in the fixture teardown.
- [ ] Step 5: Run `pytest` (the full suite). Everything passes.
- [ ] Step 6: Commit with the message "Actions: proposals, human decisions, append-only audit, expiry, verdict write-back".

### Task 3: Tool registry and MCP server

**Files:**
- Create: `backend/flowtrace/store.py`, `backend/flowtrace/agent/registry.py`, `backend/flowtrace/mcp/__init__.py`,
  `backend/flowtrace/mcp/server.py`
- Modify: `backend/flowtrace/agent/policy.py`, to read tools from the registry.
- Modify: `requirements.txt`, to add `mcp>=2.3,<3`.
- Test: `backend/tests/test_registry.py`, `backend/tests/test_mcp.py`

**Interfaces:**
- Consumes: Task 1 `plan_holds`, Task 2 `propose` and `list_actions`.
- Produces:
  - `store.load_case(id) -> dict | None`
  - `store.load_result(id) -> dict | None`
  - `store.list_cases(batch_id=None) -> list[dict]`
  - `store.save_plan(id, plan)`
  - `store.load_plan(id)`
  - `registry.REGISTRY: dict[str, ToolSpec]`, where `ToolSpec(name, fn, args: type[BaseModel], doc, scope: 'case'|'global', writes: bool)`
  - `registry.investigation_tools() -> dict[str, ToolSpec]`
  - `registry.call(name: str, args: dict, ctx: CaseContext | None = None) -> dict`
  - `registry.act(case_id: str) -> dict`: plans and proposes, returning `{plan, actions}`.
  - `mcp.server.build_server() -> MCPServer`

- [ ] Step 1: Write the tests:
  - `test_policy_tools_come_from_registry`: `policy._schema()` enum equals `list(investigation_tools()) + ['finish']`.
  - `test_propose_action_refuses_hub_and_non_trail_account`.
  - `test_propose_action_refuses_over_cap`.
  - `test_registry_read_tools_never_call_rw`: monkeypatch `rw` to raise, then call `get_case` and `plan_holds`
    on the fake graph.
  - `test_mcp_lists_tools_and_calls_get_case`:
    - Use the in-process `mcp.Client(build_server())`.
    - The tool names must include the 8 investigation tools, `get_case`, `list_cases`, `plan_holds` and
      `propose_action`.
    - Only `propose_action` has `read_only_hint` False.
    - `call_tool('get_case', ...)` on a temp `data/runs` returns the case.
  - `test_mcp_has_no_approve_tool`.
- [ ] Step 2: Run the tests. They fail.
- [ ] Step 3: Implement:
  - The MCP tools are thin wrappers built from the registry. Each one's arguments are `case_id` plus the tool's
    pydantic model.
  - Keep one `CaseContext` per case for the session.
  - Turn a `ToolError` into an MCP `ToolError`.
  - `python -m flowtrace.mcp.server [--http --port 8765]`.
- [ ] Step 4: Run `pytest`. Everything passes.
- [ ] Step 5: Commit with the message "One tool registry for the app agent and an MCP server (read-only tools + propose_action)".

### Task 4: API

**Files:**
- Modify: `backend/flowtrace/api/main.py`:
  - add the endpoints;
  - call `registry.act` inside `_store_result`;
  - make `forget_batch` call `expire_proposals`;
  - add a 60 s expiry timer in the lifespan.
- Test: `backend/tests/test_api_actions.py`, using `TestClient` with the service functions monkeypatched.

- [ ] Step 1: Write the tests:
  - `test_plan_needs_result_409`.
  - `test_plan_returns_actions_and_is_idempotent`.
  - `test_approve_passes_reason_key_and_maps_errors`: 422 and 409 pass through.
  - `test_audit_endpoint`.
  - `test_forget_batch_expires_proposals`.
- [ ] Step 2: Run the tests. They fail.
- [ ] Step 3: Implement. The `Idempotency-Key` header is optional.
- [ ] Step 4: Run `pytest`. Everything passes.
- [ ] Step 5: Commit with the message "API: plan, approve/reject with idempotency keys, actions and audit".

### Task 5: UI

**Files:**
- Create: `frontend/src/components/HoldPanel.tsx`, `frontend/src/pages/Results.tsx`, `frontend/src/lib/money.ts`
  (currency names to ISO codes, and `fmtMoney(usd, currency, rate)`).
- Modify:
  - `frontend/src/lib/api.ts`: types and `postJSONBody(path, body, key)`.
  - `frontend/src/App.tsx`: "Cases" and "Results" navigation, plus routes.
  - `frontend/src/components/LiveGraph3D.tsx`: a `marked` prop for held accounts.
  - `frontend/src/pages/Workspace.tsx`: mount HoldPanel under the report and pass `marked`.

- [ ] Step 1: Run `npm run build`, which runs tsc and vite. It passes before and after.
- [ ] Step 2: Drive the UI in the browser on the uploaded demo batch: open a case, see the holds, approve one with a
  reason, reject one, then open Results.
- [ ] Step 3: Take 3 screenshots into `docs/screenshots/`: `holds.png`, `approve.png`, `results.png`.
- [ ] Step 4: Commit with the message "UI: confirm-hold panel on the case and a results page with countdowns and audit log".

### Task 6: Measurement, README, cleanup

**Files:**
- Create: `backend/flowtrace/eval/holds.py`
- Modify: `README.md`, to cover the action layer, MCP setup and the measurement table.

- [ ] Step 1: Run `python -m flowtrace.eval.holds --sample eval/day_slice_2022-09-09.csv --cases 40`.
- [ ] Step 2: Run the same on `demo_mixed_2022-09-09.csv`.
- [ ] Step 3: Write the README tables using exactly the numbers printed.
- [ ] Step 4: Delete the test actions and audit from the main graph. Confirm the counts match the baseline.
- [ ] Step 5: Request code review, fix what it finds, run `pytest` and `npm run build`, and commit.
