"""Every Cypher query FlowTrace depends on. All are read-only and parameterised.

FalkorDB notes that shape these queries:
  * Always bind and use the TRANSFER variable (e.g. count(t)). If the edge alias is unused,
    FalkorDB only checks that one edge exists and undercounts parallel transfers.
  * LIMIT cannot take a parameter, so limits are literals.
  * Timestamps are integer epoch milliseconds (range-indexed).
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
OPTIONAL MATCH (a)-[o:TRANSFER]->(d:Account) WHERE o.ts >= $t0 AND o.ts < $t1
WITH a, count(o) AS out_cnt, count(DISTINCT d) AS out_cp, sum(o.amount) AS out_sum,
     collect(DISTINCT o.fmt) AS out_fmts
OPTIONAL MATCH (s:Account)-[i:TRANSFER]->(a) WHERE i.ts >= $t0 AND i.ts < $t1
RETURN out_cnt, out_cp, coalesce(out_sum, 0.0) AS out_sum, out_fmts,
       count(i) AS in_cnt, count(DISTINCT s) AS in_cp, coalesce(sum(i.amount), 0.0) AS in_sum,
       collect(DISTINCT i.fmt) AS in_fmts
"""

# Daily outgoing activity before the alert window: the account's own baseline.
ACCOUNT_DAILY_HISTORY = """
MATCH (a:Account {id: $id})-[t:TRANSFER]->(:Account)
WHERE t.ts >= $h0 AND t.ts < $t0
RETURN t.ts / 86400000 AS day, count(t) AS n, sum(t.amount) AS total
ORDER BY day
"""

# Who the counterparties are: owner type and how busy they are (hub = merchant/lender/payroll).
CP_INFO = """
UNWIND $ids AS id
MATCH (a:Account {id: id})
OPTIONAL MATCH (a)-[o:TRANSFER]->()
WITH a, count(o) AS out_deg
OPTIONAL MATCH ()-[i:TRANSFER]->(a)
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
WHERE t.ts >= $t0 AND t.ts < $t1
RETURN t.tx_id AS tx_id, a.id AS src, b.id AS dst, t.ts AS ts, t.amount AS amount, t.fmt AS fmt,
       t.cur AS cur, t.xcur AS xcur
ORDER BY t.ts
LIMIT 300
"""

TRANSFERS_IN = """
MATCH (s:Account)-[t:TRANSFER]->(a:Account {id: $id})
WHERE t.ts >= $t0 AND t.ts < $t1
RETURN t.tx_id AS tx_id, s.id AS src, a.id AS dst, t.ts AS ts, t.amount AS amount, t.fmt AS fmt,
       t.cur AS cur, t.xcur AS xcur
ORDER BY t.ts
LIMIT 300
"""

# One hop of a time-respecting money trace. Called repeatedly by the tracer so the UI can
# animate each wave. Money can only move on after it arrived: t.ts >= f.ts.
TRACE_FORWARD_HOP = """
UNWIND $frontier AS f
MATCH (a:Account {id: f.id})-[t:TRANSFER]->(b:Account)
WHERE t.ts >= f.ts AND t.ts <= f.ts + $max_gap_ms AND NOT b.id IN $visited
RETURN a.id AS src, b.id AS dst, t.tx_id AS tx_id, t.ts AS ts, t.amount AS amount, t.fmt AS fmt
ORDER BY t.amount DESC
LIMIT 200
"""

TRACE_BACKWARD_HOP = """
UNWIND $frontier AS f
MATCH (b:Account)-[t:TRANSFER]->(a:Account {id: f.id})
WHERE t.ts <= f.ts AND t.ts >= f.ts - $max_gap_ms AND NOT b.id IN $visited
RETURN b.id AS src, a.id AS dst, t.tx_id AS tx_id, t.ts AS ts, t.amount AS amount, t.fmt AS fmt
ORDER BY t.amount DESC
LIMIT 200
"""

# Degree check so the tracer can stop at hubs (merchants, payroll processors, exchanges).
DEGREES = """
UNWIND $ids AS id
MATCH (a:Account {id: id})
OPTIONAL MATCH (a)-[o:TRANSFER]->()
WITH a, count(o) AS out_deg
OPTIONAL MATCH ()-[i:TRANSFER]->(a)
RETURN a.id AS id, out_deg, count(i) AS in_deg
"""

# --- Relationship history (legitimacy evidence) -------------------------------------------

# For each counterparty: how long and how often these two accounts have transacted before.
PAIR_HISTORY_OUT = """
UNWIND $cps AS cp
MATCH (a:Account {id: $id})-[t:TRANSFER]->(b:Account {id: cp})
WHERE t.ts < $before
RETURN b.id AS cp, count(t) AS prior_cnt, min(t.ts) AS first_ts, collect(t.ts) AS ts_list,
       collect(t.amount) AS amounts
"""

PAIR_HISTORY_IN = """
UNWIND $cps AS cp
MATCH (b:Account {id: cp})-[t:TRANSFER]->(a:Account {id: $id})
WHERE t.ts < $before
RETURN b.id AS cp, count(t) AS prior_cnt, min(t.ts) AS first_ts, collect(t.ts) AS ts_list,
       collect(t.amount) AS amounts
"""

# Series per counterparty, used to detect recurring flows (salary, EMI, rent, subscriptions).
FLOW_SERIES = """
MATCH (a:Account {id: $id})-[t:TRANSFER]-(b:Account)
WHERE t.ts < $before
WITH b, t, CASE WHEN startNode(t) = a THEN 'out' ELSE 'in' END AS dir
RETURN b.id AS cp, dir, t.fmt AS fmt, collect(t.ts) AS ts_list, collect(t.amount) AS amounts,
       count(t) AS n
ORDER BY n DESC
LIMIT 100
"""

# --- Network shape ------------------------------------------------------------------------

# Does money from several accounts end up in the same place? (scatter-gather / funnel)
SHARED_DESTINATIONS = """
UNWIND $ids AS sid
MATCH (s:Account {id: sid})-[t:TRANSFER]->(d:Account)
WHERE t.ts >= $t0 AND t.ts < $t1 AND NOT d.id IN $ids
WITH d, count(DISTINCT s) AS senders, count(t) AS n, sum(t.amount) AS total, collect(t.tx_id) AS tx_ids
WHERE senders >= 2
RETURN d.id AS dst, senders, n, total, tx_ids[0..20] AS tx_ids
ORDER BY senders DESC, total DESC
LIMIT 20
"""

SHARED_SOURCES = """
UNWIND $ids AS did
MATCH (s:Account)-[t:TRANSFER]->(d:Account {id: did})
WHERE t.ts >= $t0 AND t.ts < $t1 AND NOT s.id IN $ids
WITH s, count(DISTINCT d) AS receivers, count(t) AS n, sum(t.amount) AS total, collect(t.tx_id) AS tx_ids
WHERE receivers >= 2
RETURN s.id AS src, receivers, n, total, tx_ids[0..20] AS tx_ids
ORDER BY receivers DESC, total DESC
LIMIT 20
"""

# Short time-ordered cycles back to the origin (round-tripping).
CYCLES = """
MATCH p = (a:Account {id: $id})-[r:TRANSFER*2..4]->(a)
WHERE ALL(x IN r WHERE x.ts >= $t0 AND x.ts < $t1)
  AND ALL(i IN range(0, size(r) - 2) WHERE r[i].ts <= r[i + 1].ts)
RETURN [x IN r | x.tx_id] AS tx_ids, [n IN nodes(p) | n.id] AS accounts,
       [x IN r | x.amount] AS amounts, [x IN r | x.ts] AS ts
LIMIT 10
"""

# How close is this account to accounts already escalated by earlier cases?
NEAREST_ESCALATED = """
MATCH (a:Account {id: $id}), (c:Case {disposition: 'ESCALATE'})-[:ABOUT]->(x:Account)
WHERE x.id <> a.id
MATCH p = shortestPath((a)-[:TRANSFER*1..3]-(x))
RETURN x.id AS account, c.id AS case_id, length(p) AS hops
ORDER BY hops
LIMIT 5
"""

# --- Case memory --------------------------------------------------------------------------

PRIOR_CASES = """
MATCH (c:Case)-[:ABOUT]->(a:Account {id: $id})
RETURN c.id AS case_id, c.disposition AS disposition, c.typology AS typology,
       c.score AS score, c.created_ts AS created_ts, c.summary AS summary
ORDER BY c.created_ts DESC
LIMIT 10
"""

SAVE_CASE = """
MERGE (c:Case {id: $case_id})
SET c.disposition = $disposition, c.typology = $typology, c.crime_hypothesis = $crime,
    c.score = $score, c.confidence = $confidence, c.created_ts = $created_ts,
    c.summary = $summary, c.tx_ids = $tx_ids, c.expires_ts = $expires_ts
WITH c
UNWIND $accounts AS aid
MATCH (a:Account {id: aid})
MERGE (c)-[:ABOUT]->(a)
"""

# --- Uploaded batches ---------------------------------------------------------------------

INSERT_BATCH = """
UNWIND $rows AS r
MERGE (s:Account {id: r.src})
MERGE (d:Account {id: r.dst})
CREATE (s)-[:TRANSFER {tx_id: r.tx_id, ts: r.ts, amount: r.amount, fmt: r.fmt, cur: r.cur,
                       xcur: r.xcur, batch: $batch}]->(d)
"""

DELETE_BATCH = """
MATCH ()-[t:TRANSFER {batch: $batch}]->()
DELETE t
"""
