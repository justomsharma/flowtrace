# Graph data model and the Cypher FlowTrace depends on

[← Back to the README](../README.md)

## Graph data model (FalkorDB)

```
(:Entity {id, name, type})                      owner: Corporation, Partnership, Sole Proprietorship, Individual
(:Account {id, bank, entity_id, entity_type, self_cnt})-[:OWNED_BY]->(:Entity)
(:Account)-[:TRANSFER {tx_id, ts, amount, fmt, cur, xcur, batch?}]->(:Account)
(:Case {id, disposition, typology, crime_hypothesis, score, confidence, created_ts, summary, tx_ids, expires_ts,
        verdict?, verdict_by?, verdict_at?, verdict_reason?})
      -[:ABOUT]->(:Account)                     case memory written after each investigation; verdict = a person's decision
(:Action {id, type, status, case_id, account?, hold_amount, on_routes, currency, rate, why, evidence_ids, tx_ids,
          expiry_days, expires_ts, proposed_by: 'agent'|'mcp', proposed_at, decided_by, decided_at, reason, dedup_key})
      -[:FOR]->(:Case), -[:ON]->(:Account)      type HOLD|RELEASE|ESCALATE|FILE_STR|CLOSE; status PROPOSED|APPROVED|REJECTED|EXPIRED
(:Audit {id, ts, actor, auth?, event, action_id, case_id, account, from_status, to_status, reason})   only ever created
```

- `ts` is stored as an integer in epoch milliseconds, with a range index on it. `amount` is in USD, converted at fixed rates derived from the data.
- Indexes: `Account.id`, `Entity.id`, and `TRANSFER(ts, tx_id)`.
- Labels (`Is Laundering`) are **never written to the graph**, so nothing the agent reads can leak ground truth.
- The graph holds history before the test days; the loader records where it ends (`(:Meta {id: 'history', until})`), and uploads with payments before that are refused, since those payments are already in the graph. Uploaded batches are merged in as `TRANSFER` edges tagged with a batch id. Payment ids are a hash of the payment's own fields and the batch id is a hash of the file, so uploading the same (or an overlapping) file again adds nothing.

## Cypher and graph algorithms the product depends on

Every query lives in two files: [`backend/flowtrace/graph/queries.py`](../backend/flowtrace/graph/queries.py) and [`backend/flowtrace/detectors/alerts.py`](../backend/flowtrace/detectors/alerts.py).

| Purpose | Query |
|---|---|
| Alert detectors | `FAN_OUT`, `FAN_IN`, `PASS_THROUGH`, `SCATTER_GATHER` (2-hop, time-ordered), `CYCLE` (`[:TRANSFER*2..4]` with `ALL()` time-order predicate) |
| Money tracing | `TRACE_FORWARD_HOP` / `TRACE_BACKWARD_HOP`. One query per hop, so each wave streams to the UI. Money can only move on after it arrived (`t.ts >= f.ts`), and tracing stops at hub accounts (`DEGREES`) |
| Legitimacy evidence | `PAIR_HISTORY_OUT/IN` (relationship age and count), `FLOW_SERIES` (payment series per counterparty → recurring-payment detection), `CP_INFO` (owner type and degree), `SIBLING_ACCOUNTS` (same owner) |
| Baseline | `ACCOUNT_DAILY_HISTORY` (robust z-score against the account's own history) |
| Network shape | `SHARED_DESTINATIONS` / `SHARED_SOURCES`, `CYCLES` |
| Case memory | `SAVE_CASE`, `PRIOR_CASES`, `NEAREST_ESCALATED` (`shortestPath` to escalated accounts). Both read `coalesce(c.verdict, c.disposition)`, so a person's verdict replaces the agent's, and human verdicts come first |
| Hold planner | `TRACE_FORWARD_HOP` from the case accounts, `HUB_DEGREES` + `DEGREES_UNTIL` (hub rules, payments before the decision only), `HOLD_FLOWS` (what each trail account received and sent after the case money arrived) |
| Actions | `PROPOSE_ACTION` (dedup and the per-case hold cap checked in the same write; audit entry only when created), `DECIDE_ACTION` (`WHERE a.status = 'PROPOSED'`, so a second click changes nothing), `EXPIRE_DUE`, `EXPIRE_PROPOSALS` + `RETIRE_DECIDED` (re-upload), `AUDIT_LOG`. The decision, its verdict, an approved release and withdrawing the case's other open decisions happen in one query |

FalkorDB specifics we rely on:
- **`GRAPH.RO_QUERY`:** every agent tool is read-only, enforced by the database.
- **Per-query timeouts.**
- **Edge counting:** every query references the `TRANSFER` variable (e.g. `count(t)`), because FalkorDB otherwise counts parallel edges between the same two accounts only once.
- **Literal `LIMIT`s**, because `LIMIT` cannot take a parameter.

**`algo.maxFlow`.** FalkorDB 6.0.2 has it (checked with `CALL dbms.procedures()`), along with BFS, WCC, PageRank, betweenness, harmonic centrality, label propagation, MSF and shortest paths. The hold planner does not use it for its context figure (the minimum vertex cut of the trail):
- it returns a flow, not a cut;
- it has no node capacities;
- it runs over stored edges, so time order, node splitting and a super-source would mean writing a temporary network into the graph.

The trail is small (a few hundred accounts at most), so the planner reads it with read-only Cypher and computes the cut in networkx. Holds are liens on amounts, so the cut doesn't choose them (see [Acting on a case](architecture.md#acting-on-a-case-holds-a-person-approves)).

**Without FalkorDB the product does not work:** detection, investigation, tracing, legitimacy checks and case memory are all graph queries.
