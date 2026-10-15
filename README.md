# FlowTrace

FlowTrace is an AML (anti-money-laundering) investigation agent built on a FalkorDB transaction graph. It scores a batch of payments, finds laundering shapes with graph queries, and then investigates each alert the way an analyst would. It follows the money, checks relationship history and recurring payments, and looks for both suspicious *and* legitimate explanations. Each case ends in an evidence-backed report with a recommended action for a human analyst.

The main idea: **rules catch patterns; FlowTrace explains them, and closes the ones a human analyst would close.** A salary that is split into EMIs, rent and card bills looks like "fan-out + pass-through" to a rule. FlowTrace recognises it from the graph.

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB, Oct 15–18, 2026).
**Track:** Track 01, Agents That Act on Connected Data.

---

## Results (measured; reproduce with the commands below)

| What | Result | How it was measured |
|---|---|---|
| Triage model, Precision@100 | **97%** | Of the 100 highest-scored transfers on held-out test days (Sep 9–10), 97 are labelled laundering |
| Triage model, F1 (laundering class) | **0.515** (P 0.65, R 0.43, PR-AUC 0.51) | Same held-out days. Accuracy is 99.91%, but flagging nothing already scores 99.89%, so accuracy is not used to judge the model |
| Laundering-shape recognition | **96.5%** on 282 schemes with 3+ accounts; 80.8% on all 370 | Against IBM's ground-truth pattern file. Schemes with 1–2 transfers are inherently ambiguous |
| Strongest legitimacy signal | First-time account pairs launder at **1.69%**; pairs with >10 prior transfers at **0.018%** (92× lower) | Whole HI-Small dataset |
| Adaptive agent vs fixed workflow | `python -m flowtrace.eval.compare` writes `data/models/agent_eval.json` | Same alerts, same query budget; see *Evaluation* |

For comparison, published results on HI-Small are 62.9 F1 for GFP+LightGBM and 68.2 for Multi-PNA, on a different split that includes the Sep 11–18 tail. We exclude that tail (see *Data*).

---

## Architecture

```
CSV batch ─► parse ─► triage model (LightGBM) ─► load into FalkorDB ─► Cypher detectors ─► cases
                                                                                        │
                       ┌────────────────────── investigation loop (≤ 6 steps) ◄─────────┘
                       │  policy picks an approved tool ─► read-only Cypher ─► evidence ledger
                       │  (Claude, or rules, or the fixed baseline)       risk re-scored each step
                       └─► deterministic disposition ─► senior-analyst report ─► validator ─► case memory in graph
```

- **Backend:** Python, FastAPI, Server-Sent Events, FalkorDB (Python client), polars, LightGBM, networkx, Anthropic SDK
- **Frontend:** React, Vite, TypeScript, Tailwind, Recharts, Cytoscape.js
- **LLM:** Claude chooses the next tool and writes the report. It never computes facts, never runs its own Cypher, and never sets the disposition.

---

## Graph data model (FalkorDB)

```
(:Entity {id, name, type})                      owner: Corporation, Partnership, Sole Proprietorship, Individual
(:Account {id, bank, entity_id, entity_type, self_cnt})-[:OWNED_BY]->(:Entity)
(:Account)-[:TRANSFER {tx_id, ts, amount, fmt, cur, xcur, batch?}]->(:Account)
(:Case {id, disposition, typology, crime_hypothesis, score, confidence, created_ts, summary, tx_ids, expires_ts})
      -[:ABOUT]->(:Account)                     case memory written after each investigation
```

- `ts` is stored as an integer in epoch milliseconds, with a range index on it. `amount` is in USD, converted at fixed rates derived from the data.
- Indexes: `Account.id`, `Entity.id`, and `TRANSFER(ts, tx_id)`.
- Labels (`Is Laundering`) are **never written to the graph**, so nothing the agent reads can leak ground truth.
- The graph holds history before the test days. Uploaded batches are inserted as new `TRANSFER` edges tagged with a batch id.

## Cypher and graph algorithms the product depends on

Every query lives in two files: [`backend/flowtrace/graph/queries.py`](backend/flowtrace/graph/queries.py) and [`backend/flowtrace/detectors/alerts.py`](backend/flowtrace/detectors/alerts.py).

| Purpose | Query |
|---|---|
| Alert detectors | `FAN_OUT`, `FAN_IN`, `PASS_THROUGH`, `SCATTER_GATHER` (2-hop, time-ordered), `CYCLE` (`[:TRANSFER*2..4]` with `ALL()` time-order predicate) |
| Money tracing | `TRACE_FORWARD_HOP` / `TRACE_BACKWARD_HOP`. One query per hop, so each wave streams to the UI. Money can only move on after it arrived (`t.ts >= f.ts`), and tracing stops at hub accounts (`DEGREES`) |
| Legitimacy evidence | `PAIR_HISTORY_OUT/IN` (relationship age and count), `FLOW_SERIES` (payment series per counterparty → recurring-payment detection), `CP_INFO` (owner type and degree), `SIBLING_ACCOUNTS` (same owner) |
| Baseline | `ACCOUNT_DAILY_HISTORY` (robust z-score against the account's own history) |
| Network shape | `SHARED_DESTINATIONS` / `SHARED_SOURCES`, `CYCLES` |
| Case memory | `SAVE_CASE`, `PRIOR_CASES`, `NEAREST_ESCALATED` (`shortestPath` to escalated accounts) |

FalkorDB specifics we rely on:
- **`GRAPH.RO_QUERY`:** every agent tool is read-only, enforced by the database.
- **Per-query timeouts.**
- **Edge counting:** every query references the `TRANSFER` variable (e.g. `count(t)`), because FalkorDB otherwise counts parallel edges between the same two accounts only once.
- **Literal `LIMIT`s**, because `LIMIT` cannot take a parameter.

**Without FalkorDB the product does not work:** detection, investigation, tracing, legitimacy checks and case memory are all graph queries.

---

## How flagging works

1. **Triage model.** LightGBM on 37 point-in-time features: amount, format, relationship history, same-day fan-in/out and pass-through, 2-hop scatter-gather, reciprocal flows, entity type. Features only use data up to the end of the transfer's day, which matches end-of-day batch monitoring.
2. **Cypher detectors** fire on typology shapes in the batch window.
3. **Cases.** Transfers scoring ≥ `candidate_score`, or caught by a detector, are grouped into connected subgraphs. Each case's shape is classified (FAN-OUT, FAN-IN, SCATTER-GATHER, GATHER-SCATTER, CYCLE, BIPARTITE, STACK, RANDOM).
4. **Investigation.** The agent gathers two kinds of evidence:
   - **Red flags:** new counterparties, equal splits, fast onward transfer, cash/crypto exit, round trips, activity spike, a dormant account becoming active, nearness to escalated cases.
   - **Legitimate explanations:** established counterparties, recurring flows with an inferred purpose (payroll, fixed obligation/EMI, bill, standing order), payments to high-volume receivers, activity consistent with the account's baseline, internal transfers between the same owner's accounts, money that stops moving.
   - Some red flags are **escalators**: they halve the weight of legitimate explanations.
5. **Disposition** is computed in code from the evidence ([`disposition.py`](backend/flowtrace/agent/disposition.py)):
   `risk = 0.45·model + 0.35·red_flags + 0.20·shape − 0.40·legitimacy`
   - **ESCALATE** at risk ≥ 0.55.
   - **CLOSE** at risk < 0.25, but only if legitimate explanations are strong and no escalator fired. A closure expires after 90 days.
   - **REVIEW** for everything else.
6. **Report.** Claude writes the senior-analyst report from the evidence ledger. A validator drops any statement that cites unknown evidence, or uses numbers that are not in the cited evidence. The unsupported-claim rate is reported for every case.

All thresholds and weights live in [`config.yaml`](config.yaml).

**Crime type.** The dataset labels laundering yes/no only. The "possible predicate crime" shown in reports maps the observed shape to the crimes the data generator models (extortion, loan sharking, gambling, prostitution, kidnapping, robbery, embezzlement, drugs, smuggling). It is a **hypothesis**, labelled as such, and is not a measured prediction.

---

## Data

- **Source:** IBM *Transactions for Anti Money Laundering* (HI-Small), licence CDLA-Sharing-1.0. It is **synthetic**: no real customer data is used, as the hackathon rules require.
- **Size:** 5,078,345 transfers; 515,088 accounts (key = bank + account); 5,177 laundering transfers (0.10%). The patterns file links 3,209 transfers to 370 known schemes.
- **Split** (by day, the way a bank's nightly monitoring run works):
  - train: Sep 1–6
  - validation: Sep 7–8
  - test: Sep 9–10
  - **Sep 11–18 is excluded.** Normal activity stops on Sep 10 while laundering rows continue (1,108 rows, 59% laundering), so keeping it would inflate every metric.
- **Limitations:**
  - HI-Small covers 10 days, so monthly salary/EMI cycles cannot complete. Recurring detection in this data finds daily and weekly cadences.
  - Account owners are mostly companies.
  - Purpose is *inferred* from metadata; the dataset has no memo field.

---

## Setup

Prerequisites: Python 3.10+, Docker Desktop, Node 20+. Put the IBM HI-Small files (`HI-Small_Trans.csv`, `HI-Small_accounts.csv`, `HI-Small_Patterns.txt`) in `data/raw/`.

```bash
# 1. Python environment
python -m venv venv
venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env             # add ANTHROPIC_API_KEY; AGENT_MODEL is optional

cd backend
# 2. Data and model (no database needed)
python -m flowtrace.data.prepare             # raw CSV -> parquet, FX rates, patterns
python -m flowtrace.ml.train                 # trains + saves data/models/triage_lgbm.pkl (+ .txt, features.json)
python -m flowtrace.ml.evaluate              # test-day curves for the dashboard
python -m flowtrace.detectors.evaluate_typology
python -m flowtrace.data.samples             # demo upload files in data/samples/
python -m pytest -q tests

# 3. Graph
cd ..
docker compose up -d                         # FalkorDB on :6379, Browser on http://localhost:3000
cd backend
python -m flowtrace.graph.load --replace     # loads history (before Sep 9) with the bulk loader
python -m flowtrace.graph.smoke              # runs every Cypher query and checks counts against the source

# 4. Agent evaluation (optional, uses Claude)
python -m flowtrace.eval.compare --cases 25

# 5. Run the app
python -m uvicorn flowtrace.api.main:app --port 8000
cd ../frontend && npm install && npm run dev  # http://localhost:5173
```

### Model files
`data/models/triage_lgbm.pkl` is the pickled bundle `{model, features, threshold}`. `triage_lgbm.txt` is the native LightGBM booster, and `triage_features.json` holds the feature order and threshold.

---

## Evaluation

[`flowtrace.eval.compare`](backend/flowtrace/eval/compare.py) runs three policies on the same alerts with the same budget:
- **fixed:** the same tools in the same order for every case;
- **heuristic:** adaptive rules, no LLM;
- **claude:** the adaptive agent.

It reports:
- the share of legitimate alerts auto-closed;
- the share of laundering kept open;
- escalation precision;
- evidence coverage (the share of ground-truth scheme transfers the investigation retrieved);
- the unsupported-claim rate;
- queries and seconds per case.

## Tests

`backend/tests` holds unit tests for recurring-payment detection and for the agent loop. The agent tests use a small in-memory stand-in for FalkorDB, so the tool, policy and disposition logic can be tested without a database. The real Cypher is checked against FalkorDB by `flowtrace.graph.smoke`.

## AI assistance

This project was built with help from an AI coding assistant (Claude Code). At runtime, FlowTrace calls Claude (Anthropic API) to choose investigation steps and write case reports. Facts, scores and dispositions are computed in code. Every design decision and every line of code was reviewed by the team.

## License

MIT
