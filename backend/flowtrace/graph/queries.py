"""Every Cypher query FlowTrace depends on, parameterised. Agent and MCP tools run only the read-only
ones (GRAPH.RO_QUERY). Writes: the loader, case memory, and actions (proposals, decisions, audit).

FalkorDB notes that shape these queries:
  * Always bind and use the TRANSFER variable (e.g. count(t)). If the edge alias is unused,
    FalkorDB only checks that one edge exists and undercounts parallel transfers.
  * LIMIT cannot take a parameter, so limits are literals.
  * Timestamps are integer epoch milliseconds (range-indexed).
  * Time filters on TRANSFER are written `t.ts + 0`. On a bare `t.ts` the planner picks an
    Edge By Index Scan over every transfer in the window (hundreds of thousands) instead of
    walking the anchor account's own edges: 205 ms -> 1 ms per account on HI-Small.
  * Look up lists of accounts with `WHERE a.id IN $ids`. `UNWIND $ids AS x MATCH (a {id: x})`
    skips the Account.id index and scans the whole graph per row (timeouts at 200 accounts).
  * Leave the label off the far end when it is filtered with `NOT x.id IN $ids`, or the planner
    starts from a label scan of every account there (2.2 s -> 1.5 ms for SHARED_DESTINATIONS).
  * A variable-length relationship variable is bound to a path, not a list: read the edges
    with relationships(p). shortestPath() is only allowed in WITH or RETURN.
  * Conditional writes: put the condition in WHERE (`WHERE a.status = 'PROPOSED'`) so a second request
    matches nothing and changes nothing. Writes on one graph are serialised, so this is race-free.
  * MERGE on a key property is how a double click or two callers converge on one node; `ON CREATE SET a += $map`
    sets the rest. A side write only on creation: FOREACH (_ IN CASE WHEN created THEN [1] ELSE [] END | ...).
  * Audit entries are only ever CREATEd, in the same query as the change they record.
"""

# --- Account context ----------------------------------------------------------------------

ACCOUNT_CARD = """
MATCH (a:Account {id: $id})
OPTIONAL MATCH (a)-[:OWNED_BY]->(e:Entity)
RETURN a.id AS id, a.bank AS bank, a.entity_type AS entity_type, a.self_cnt AS self_transfers,
       e.id AS entity_id, e.name AS entity_name
"""

ACCOUNT_ACTIVITY = """
MATCH (a:Account {id: $id})
OPTIONAL MATCH (a)-[o:TRANSFER]->(d:Account) WHERE o.ts + 0 >= $t0 AND o.ts + 0 < $t1
WITH a, count(o) AS out_cnt, count(DISTINCT d) AS out_cp, sum(o.amount) AS out_sum,
     collect(DISTINCT o.fmt) AS out_fmts
OPTIONAL MATCH (a)<-[i:TRANSFER]-(s) WHERE i.ts + 0 >= $t0 AND i.ts + 0 < $t1
RETURN out_cnt, out_cp, coalesce(out_sum, 0.0) AS out_sum, out_fmts,
       count(i) AS in_cnt, count(DISTINCT s) AS in_cp, coalesce(sum(i.amount), 0.0) AS in_sum,
       collect(DISTINCT i.fmt) AS in_fmts
"""

# Daily outgoing activity before the alert window: the account's own baseline.
ACCOUNT_DAILY_HISTORY = """
MATCH (a:Account {id: $id})-[t:TRANSFER]->(:Account)
WHERE t.ts + 0 >= $h0 AND t.ts + 0 < $t0
RETURN t.ts / 86400000 AS day, count(t) AS n, sum(t.amount) AS total
ORDER BY day
"""

# Who the counterparties are: owner type and how busy they are (hub = merchant/lender/payroll).
CP_INFO = """
MATCH (a:Account) WHERE a.id IN $ids
OPTIONAL MATCH (a)-[o:TRANSFER]->()
WITH a, count(o) AS out_deg
OPTIONAL MATCH (a)<-[i:TRANSFER]-()
RETURN a.id AS id, a.entity_type AS entity_type, a.entity_id AS entity_id, out_deg, count(i) AS in_deg
"""

# Same-owner accounts (the analyst's "who else does this person/company control").
SIBLING_ACCOUNTS = """
MATCH (a:Account {id: $id})-[:OWNED_BY]->(e:Entity)<-[:OWNED_BY]-(o:Account)
WHERE o.id <> a.id
RETURN o.id AS id, o.bank AS bank
LIMIT 50
"""

# --- Transfers in and out ------------------------------------------------------------------

TRANSFERS_OUT = """
MATCH (a:Account {id: $id})-[t:TRANSFER]->(b:Account)
WHERE t.ts + 0 >= $t0 AND t.ts + 0 < $t1
RETURN t.tx_id AS tx_id, a.id AS src, b.id AS dst, t.ts AS ts, t.amount AS amount, t.fmt AS fmt,
       t.cur AS cur, t.xcur AS xcur
ORDER BY t.ts
LIMIT 300
"""

TRANSFERS_IN = """
MATCH (a:Account {id: $id})<-[t:TRANSFER]-(s)
WHERE t.ts + 0 >= $t0 AND t.ts + 0 < $t1
RETURN t.tx_id AS tx_id, s.id AS src, a.id AS dst, t.ts AS ts, t.amount AS amount, t.fmt AS fmt,
       t.cur AS cur, t.xcur AS xcur
ORDER BY t.ts
LIMIT 300
"""

# One hop of a time-respecting money trace. Called repeatedly by the tracer so the UI can
# animate each wave. Money can only move on after it arrived: t.ts >= f.ts.
# $ids holds the frontier account ids so the index finds them; $frontier carries each one's time.
TRACE_FORWARD_HOP = """
MATCH (a:Account) WHERE a.id IN $ids
UNWIND $frontier AS f
WITH a, f WHERE f.id = a.id
MATCH (a)-[t:TRANSFER]->(b:Account)
WHERE t.ts + 0 >= f.ts AND t.ts + 0 <= f.ts + $max_gap_ms AND NOT b.id IN $visited
RETURN a.id AS src, b.id AS dst, t.tx_id AS tx_id, t.ts AS ts, t.amount AS amount, t.fmt AS fmt, t.cur AS cur,
       t.xcur AS xcur
ORDER BY t.amount DESC
LIMIT 200
"""

TRACE_BACKWARD_HOP = """
MATCH (a:Account) WHERE a.id IN $ids
UNWIND $frontier AS f
WITH a, f WHERE f.id = a.id
MATCH (a)<-[t:TRANSFER]-(b)
WHERE t.ts + 0 <= f.ts AND t.ts + 0 >= f.ts - $max_gap_ms AND NOT b.id IN $visited
RETURN b.id AS src, a.id AS dst, t.tx_id AS tx_id, t.ts AS ts, t.amount AS amount, t.fmt AS fmt, t.cur AS cur,
       t.xcur AS xcur
ORDER BY t.amount DESC
LIMIT 200
"""

# Degree check so the tracer can stop at hubs (merchants, payroll processors, exchanges).
DEGREES = """
MATCH (a:Account) WHERE a.id IN $ids
OPTIONAL MATCH (a)-[o:TRANSFER]->()
WITH a, count(o) AS out_deg
OPTIONAL MATCH (a)<-[i:TRANSFER]-()
RETURN a.id AS id, out_deg, count(i) AS in_deg
"""

# Hub check for case grouping: degree over a fixed history window before the batch, so the
# threshold (cases.hub_degree, fitted on a window of the same length) means the same thing every day.
HUB_DEGREES = """
MATCH (a:Account) WHERE a.id IN $ids
OPTIONAL MATCH (a)-[o:TRANSFER]->() WHERE o.ts + 0 >= $h0 AND o.ts + 0 < $t0
WITH a, count(o) AS out_deg
OPTIONAL MATCH (a)<-[i:TRANSFER]-() WHERE i.ts + 0 >= $h0 AND i.ts + 0 < $t0
RETURN a.id AS id, out_deg, count(i) AS in_deg
"""

# --- Hold planner -------------------------------------------------------------------------

# Degrees from payments before the decision time only, so a later busy spell never decides a past hold.
DEGREES_UNTIL = """
MATCH (a:Account) WHERE a.id IN $ids
OPTIONAL MATCH (a)-[o:TRANSFER]->() WHERE o.ts + 0 < $t1
WITH a, count(o) AS out_deg
OPTIONAL MATCH (a)<-[i:TRANSFER]-() WHERE i.ts + 0 < $t1
RETURN a.id AS id, out_deg, count(i) AS in_deg
"""

# What each trail account did with money after the case money first reached it ($since: [{id, ts}]),
# up to the decision time $t1. exit_sum = money sent in hard-to-trace form or across currencies.
HOLD_FLOWS = """
MATCH (a:Account) WHERE a.id IN $ids
UNWIND $since AS f
WITH a, f WHERE f.id = a.id
OPTIONAL MATCH (a)-[o:TRANSFER]->() WHERE o.ts + 0 >= f.ts AND o.ts + 0 < $t1
WITH a, f, count(o) AS out_cnt, coalesce(sum(o.amount), 0.0) AS out_sum,
     coalesce(sum(CASE WHEN o.fmt IN $exit_fmts OR o.xcur THEN o.amount ELSE 0.0 END), 0.0) AS exit_sum
OPTIONAL MATCH (a)<-[i:TRANSFER]-() WHERE i.ts + 0 >= f.ts AND i.ts + 0 < $t1
RETURN a.id AS id, out_cnt, out_sum, exit_sum, count(i) AS in_cnt, coalesce(sum(i.amount), 0.0) AS in_sum
"""


# --- Relationship history (legitimacy evidence) -------------------------------------------

# For each counterparty: how long and how often these two accounts have transacted before.
PAIR_HISTORY_OUT = """
MATCH (a:Account {id: $id})-[t:TRANSFER]->(b:Account)
WHERE b.id IN $cps AND t.ts + 0 < $before
RETURN b.id AS cp, count(t) AS prior_cnt, min(t.ts) AS first_ts, collect(t.ts) AS ts_list,
       collect(t.amount) AS amounts
"""

PAIR_HISTORY_IN = """
MATCH (a:Account {id: $id})<-[t:TRANSFER]-(b)
WHERE b.id IN $cps AND t.ts + 0 < $before
RETURN b.id AS cp, count(t) AS prior_cnt, min(t.ts) AS first_ts, collect(t.ts) AS ts_list,
       collect(t.amount) AS amounts
"""

# Series per counterparty, used to detect recurring flows (salary, EMI, rent, subscriptions).
FLOW_SERIES = """
MATCH (a:Account {id: $id})-[t:TRANSFER]-(b:Account)
WHERE t.ts + 0 < $before
WITH b, t, CASE WHEN startNode(t) = a THEN 'out' ELSE 'in' END AS dir
RETURN b.id AS cp, dir, t.fmt AS fmt, collect(t.ts) AS ts_list, collect(t.amount) AS amounts,
       count(t) AS n
ORDER BY n DESC
LIMIT 100
"""

# --- Network shape ------------------------------------------------------------------------

# Does money from several accounts end up in the same place? (scatter-gather / funnel)
SHARED_DESTINATIONS = """
MATCH (s:Account) WHERE s.id IN $ids
MATCH (s)-[t:TRANSFER]->(d)
WHERE t.ts + 0 >= $t0 AND t.ts + 0 < $t1 AND NOT d.id IN $ids
WITH d, count(DISTINCT s) AS senders, count(t) AS n, sum(t.amount) AS total, collect(t.tx_id) AS tx_ids
WHERE senders >= 2
RETURN d.id AS dst, senders, n, total, tx_ids[0..20] AS tx_ids
ORDER BY senders DESC, total DESC
LIMIT 20
"""

SHARED_SOURCES = """
MATCH (d:Account) WHERE d.id IN $ids
MATCH (d)<-[t:TRANSFER]-(s)
WHERE t.ts + 0 >= $t0 AND t.ts + 0 < $t1 AND NOT s.id IN $ids
WITH s, count(DISTINCT d) AS receivers, count(t) AS n, sum(t.amount) AS total, collect(t.tx_id) AS tx_ids
WHERE receivers >= 2
RETURN s.id AS src, receivers, n, total, tx_ids[0..20] AS tx_ids
ORDER BY receivers DESC, total DESC
LIMIT 20
"""

# Short time-ordered cycles back to the origin (round-tripping).
CYCLES = """
MATCH p = (a:Account {id: $id})-[:TRANSFER*2..4]->(a)
WITH p, relationships(p) AS r
WHERE ALL(x IN r WHERE x.ts >= $t0 AND x.ts < $t1)
  AND ALL(i IN range(0, size(r) - 2) WHERE r[i].ts <= r[i + 1].ts)
RETURN [x IN r | x.tx_id] AS tx_ids, [n IN nodes(p) | n.id] AS accounts,
       [x IN r | x.amount] AS amounts, [x IN r | x.ts] AS ts
LIMIT 10
"""

# How close is this account to accounts already escalated by earlier cases? (never the case itself)
# An analyst's verdict replaces the agent's disposition: a human CLOSE removes an agent ESCALATE.
NEAREST_ESCALATED = """
MATCH (a:Account {id: $id}), (c:Case)-[:ABOUT]->(x:Account)
WHERE coalesce(c.verdict, c.disposition) = 'ESCALATE' AND x.id <> a.id
  AND NOT c.id STARTS WITH $batch_prefix AND c.created_ts < $t0
WITH a, c, x, shortestPath((a)-[:TRANSFER*1..3]-(x)) AS p
WHERE p IS NOT NULL
RETURN x.id AS account, c.id AS case_id, length(p) AS hops,
       CASE WHEN c.verdict IS NULL THEN 'agent' ELSE 'human' END AS decided_by
ORDER BY hops, c.verdict IS NULL
LIMIT 5
"""

# --- Case memory --------------------------------------------------------------------------
# Only earlier cases count: $batch_prefix ("<batch>-C") excludes the case itself and every case of the
# same upload (investigated in parallel), and c.created_ts < $t0 excludes any case whose window ends after
# this one starts (a same-day case from another upload is not history). An analyst's verdict replaces the
# agent's disposition, and human verdicts are listed first.

PRIOR_CASES = """
MATCH (a:Account {id: $id})<-[:ABOUT]-(c:Case)
WHERE NOT c.id STARTS WITH $batch_prefix AND c.created_ts < $t0
RETURN c.id AS case_id, coalesce(c.verdict, c.disposition) AS disposition,
       CASE WHEN c.verdict IS NULL THEN 'agent' ELSE 'human' END AS decided_by, c.typology AS typology,
       c.score AS score, c.created_ts AS created_ts, coalesce(c.verdict_reason, c.summary) AS summary
ORDER BY c.verdict IS NULL, c.created_ts DESC
LIMIT 10
"""

# Re-investigating a case replaces its account links instead of adding to them.
SAVE_CASE = """
MERGE (c:Case {id: $case_id})
SET c.disposition = $disposition, c.typology = $typology, c.crime_hypothesis = $crime,
    c.score = $score, c.confidence = $confidence, c.created_ts = $created_ts,
    c.summary = $summary, c.tx_ids = $tx_ids, c.expires_ts = $expires_ts
WITH c
OPTIONAL MATCH (c)-[old:ABOUT]->()
DELETE old
WITH DISTINCT c
MATCH (a:Account) WHERE a.id IN $accounts
MERGE (c)-[:ABOUT]->(a)
"""

# Re-uploading a batch renumbers its cases, so the old case memory for that batch is dropped. A case with a
# person's verdict is kept under a retired id ("<id>~<now>"), so later uploads still learn from that decision.
DELETE_BATCH_CASES = """
MATCH (c:Case) WHERE c.id STARTS WITH $prefix AND c.retired_id IS NULL
FOREACH (_ IN CASE WHEN c.verdict IS NULL THEN [] ELSE [1] END |
  SET c.retired_id = c.id, c.id = c.id + '~' + toString($now))
WITH c WHERE c.verdict IS NULL
DETACH DELETE c
"""

# --- Uploaded batches ---------------------------------------------------------------------

# The loader records where history ends. Payments before it are already in the graph under their
# original ids, so an upload that overlaps it would add them a second time.
HISTORY_UNTIL = """
MATCH (m:Meta {id: 'history'})
RETURN m.until AS until
"""

SET_HISTORY_UNTIL = """
MERGE (m:Meta {id: 'history'})
SET m.until = $until
"""

# Payments are merged on their content-derived tx_id, so re-uploading a file (or an overlapping one)
# adds nothing. The edge keeps the batch id of the upload that first wrote it.
INSERT_BATCH = """
UNWIND $rows AS r
MERGE (s:Account {id: r.src})
MERGE (d:Account {id: r.dst})
MERGE (s)-[t:TRANSFER {tx_id: r.tx_id}]->(d)
ON CREATE SET t.ts = r.ts, t.amount = r.amount, t.fmt = r.fmt, t.cur = r.cur, t.xcur = r.xcur, t.batch = $batch
"""

DELETE_BATCH = """
MATCH ()-[t:TRANSFER {batch: $batch}]->()
DELETE t
"""

# --- Actions (the agent proposes, a person decides) -----------------------------------------
# (:Action)-[:FOR]->(:Case), (:Action)-[:ON]->(:Account). dedup_key = "case|type|account" while the action is
# current (proposed, approved or rejected); it is cleared when a hold ends (so the account can be held again) and
# when the batch is uploaded again (its cases are renumbered).

# A new action is created only when no current action has the same dedup_key and, for a HOLD ($cap set), the
# case has fewer than $cap open holds. Both checks and the write are one query, so two callers racing each
# other can neither duplicate an action nor go over the cap. action is null when the cap stopped it.
PROPOSE_ACTION = """
MERGE (c:Case {id: $case_id})
WITH c
OPTIONAL MATCH (o:Action {case_id: $case_id, type: 'HOLD'})
WHERE $cap IS NOT NULL AND o.status IN ['PROPOSED', 'APPROVED'] AND o.retired_at IS NULL
WITH c, count(o) AS open_holds
OPTIONAL MATCH (x:Action {dedup_key: $dedup_key})
WITH c, open_holds, x IS NULL AND ($cap IS NULL OR open_holds < $cap) AS created
FOREACH (_ IN CASE WHEN created THEN [1] ELSE [] END |
  CREATE (n:Action)
  SET n += $props
  MERGE (n)-[:FOR]->(c)
  CREATE (:Audit {id: $audit_id, ts: $now, actor: $props.proposed_by, event: 'PROPOSED', action_id: $props.id,
                  case_id: $case_id, account: $account, from_status: '', to_status: 'PROPOSED', reason: $props.why}))
WITH created, open_holds
OPTIONAL MATCH (a:Action {dedup_key: $dedup_key})
OPTIONAL MATCH (acc:Account {id: $account})
FOREACH (_ IN CASE WHEN created AND a IS NOT NULL AND acc IS NOT NULL THEN [1] ELSE [] END | MERGE (a)-[:ON]->(acc))
RETURN properties(a) AS action, created, open_holds
"""

GET_ACTION = """
MATCH (a:Action {id: $id})
RETURN properties(a) AS action
"""

# Approve or reject: only a PROPOSED action changes, so a second click finds nothing to change. Everything the
# decision causes happens in the same query, so it all happens or none of it does:
#   $verdict   (approved FILE_STR / ESCALATE / CLOSE) the person's verdict on the Case, and the case's other
#              open case decisions are withdrawn (SUPERSEDED)
#   CLOSE      (approved: the case is legitimate) its proposed holds and releases are withdrawn (WITHDRAWN)
#              and its approved holds end now (RELEASED)
#   RELEASE    (approved) the case's approved hold on that account ends now, and the release's own
#              dedup_key is cleared so the account can be released again after a new hold
DECIDE_ACTION = """
MATCH (a:Action {id: $id}) WHERE a.status = 'PROPOSED'
SET a.status = $to, a.decided_by = $by, a.decided_at = $now, a.reason = $reason, a.decision_key = $key,
    a.expires_ts = $expires_ts,
    a.dedup_key = CASE WHEN a.type = 'RELEASE' AND $to = 'APPROVED' THEN NULL ELSE a.dedup_key END
CREATE (:Audit {id: $audit_id, ts: $now, actor: $by, auth: $auth, event: $to, action_id: a.id, case_id: a.case_id,
                account: a.account, from_status: 'PROPOSED', to_status: $to, reason: $reason})
WITH a
OPTIONAL MATCH (c:Case {id: a.case_id})
FOREACH (_ IN CASE WHEN $verdict IS NOT NULL AND c IS NOT NULL THEN [1] ELSE [] END |
  SET c.verdict = $verdict, c.verdict_by = $by, c.verdict_at = $now, c.verdict_reason = $reason,
      c.verdict_action = a.id
  CREATE (:Audit {id: $audit_id + '-V', ts: $now, actor: $by, auth: $auth, event: 'VERDICT', action_id: a.id,
                  case_id: c.id, from_status: coalesce(c.disposition, ''), to_status: $verdict, reason: $reason}))
WITH a
OPTIONAL MATCH (o:Action {case_id: a.case_id})
WHERE o.id <> a.id AND o.retired_at IS NULL AND (
      ($to = 'APPROVED' AND a.type = 'RELEASE' AND o.type = 'HOLD' AND o.status = 'APPROVED' AND o.account = a.account)
   OR ($verdict IS NOT NULL AND o.status = 'PROPOSED' AND o.type IN ['ESCALATE', 'FILE_STR', 'CLOSE'])
   OR ($verdict = 'CLOSE' AND o.type IN ['HOLD', 'RELEASE'] AND o.status = 'PROPOSED')
   OR ($verdict = 'CLOSE' AND o.type = 'HOLD' AND o.status = 'APPROVED'))
WITH a, collect(o) AS others
FOREACH (o IN others |
  CREATE (:Audit {id: $audit_id + '-' + o.id, ts: $now, actor: $by, auth: $auth,
                  event: CASE WHEN o.status = 'APPROVED' THEN 'RELEASED'
                              WHEN o.type IN ['HOLD', 'RELEASE'] THEN 'WITHDRAWN' ELSE 'SUPERSEDED' END,
                  action_id: o.id, case_id: o.case_id, account: o.account, from_status: o.status,
                  to_status: 'EXPIRED', reason: $reason})
  SET o.released_at = CASE WHEN o.status = 'APPROVED' THEN $now ELSE o.released_at END
  SET o.status = 'EXPIRED', o.dedup_key = NULL)
RETURN properties(a) AS action
"""

# Holds whose time is up are released. One audit entry each, because a released hold no longer matches.
EXPIRE_DUE = """
MATCH (a:Action {type: 'HOLD', status: 'APPROVED'}) WHERE a.expires_ts <= $now
SET a.status = 'EXPIRED', a.dedup_key = NULL, a.released_at = $now
CREATE (:Audit {id: 'AU-' + a.id + '-' + toString($now), ts: $now, actor: 'system', event: 'AUTO_RELEASED',
                action_id: a.id, case_id: a.case_id, account: a.account, from_status: 'APPROVED',
                to_status: 'EXPIRED', reason: 'The hold time is up'})
RETURN count(a) AS n
"""

# A re-uploaded batch renumbers its cases, so case B-C001 may now be a different case. Proposals nobody decided
# on are withdrawn. Decided actions are retired: their dedup_key is cleared and the case's panel no longer lists
# them, so nothing merges onto them; an approved hold keeps its status (that money is really held).
EXPIRE_PROPOSALS = """
MATCH (a:Action {status: 'PROPOSED'}) WHERE a.case_id STARTS WITH $prefix AND a.retired_at IS NULL
SET a.status = 'EXPIRED', a.dedup_key = NULL, a.retired_at = $now
CREATE (:Audit {id: 'AU-' + a.id + '-' + toString($now), ts: $now, actor: 'system', event: 'CASE_REUPLOADED',
                action_id: a.id, case_id: a.case_id, account: a.account, from_status: 'PROPOSED',
                to_status: 'EXPIRED', reason: $reason})
RETURN count(a) AS n
"""

RETIRE_DECIDED = """
MATCH (a:Action) WHERE a.case_id STARTS WITH $prefix AND a.retired_at IS NULL AND a.status <> 'PROPOSED'
SET a.dedup_key = NULL, a.retired_at = $now
CREATE (:Audit {id: 'AU-' + a.id + '-R' + toString($now), ts: $now, actor: 'system', event: 'CASE_REUPLOADED',
                action_id: a.id, case_id: a.case_id, account: a.account, from_status: a.status,
                to_status: a.status, reason: $reason})
RETURN count(a) AS n
"""

# A case's panel lists only its current actions; the global list (Results) keeps retired ones.
LIST_ACTIONS = """
MATCH (a:Action) WHERE ($case_id IS NULL OR (a.case_id = $case_id AND a.retired_at IS NULL))
  AND ($status IS NULL OR a.status = $status)
RETURN properties(a) AS action
ORDER BY a.proposed_at DESC, a.id
LIMIT 1000
"""

AUDIT_LOG = """
MATCH (x:Audit) WHERE $case_id IS NULL OR x.case_id = $case_id
RETURN properties(x) AS entry
ORDER BY x.ts DESC, x.id DESC
LIMIT 2000
"""
