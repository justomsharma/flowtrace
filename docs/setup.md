# Full setup, model files, persistence upgrade and tests

[← Back to the README](../README.md)

## Setup

Prerequisites: Python 3.10+, Docker Desktop, Node 20+. Put the IBM HI-Small files (`HI-Small_Trans.csv`, `HI-Small_accounts.csv`, `HI-Small_Patterns.txt`) in `data/raw/`.

```bash
# 1. Python environment
python -m venv venv
venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env             # add OPENAI_API_KEY; LLM_PROVIDER and AGENT_MODEL are optional

cd backend
# 2. Data and model (no database needed)
python -m flowtrace.data.prepare             # raw CSV -> parquet, FX rates, patterns
python -m flowtrace.ml.train                 # trains + saves data/models/triage_lgbm.pkl (+ .txt, features.json)
python -m flowtrace.ml.evaluate              # test-day curves for the dashboard
python -m flowtrace.detectors.evaluate_typology
python -m flowtrace.data.samples             # demo upload files in data/samples/ (one day, Sep 9)
python -m flowtrace.data.samples --day 2022-09-08 --out ../data/samples/val   # validation-day samples
python -m pytest -q tests

# 3. Graph
cd ..
docker compose up -d                         # FalkorDB on :6379, Browser on http://localhost:3000
cd backend
python -m flowtrace.graph.load --replace     # loads history (before Sep 9) with the bulk loader
python -m flowtrace.graph.smoke              # runs every Cypher query (read-only) and checks counts against the source
python -m flowtrace.graph.smoke --write      # also checks upload idempotency and case memory (cleans up after itself)
python -m flowtrace.eval.tune                # calibration, detector precision, hub degree from Sep 7-8 (paste into config.yaml)
python -m flowtrace.eval.fullday --day 2022-09-09   # honest full-day funnel, offline (a full day is ~61 MB, over the 50 MB upload limit)

# 4. Agent evaluation (optional, uses the LLM). It uploads the sample batch and deletes it afterwards
python -m flowtrace.eval.compare --sample eval/day_slice_2022-09-09.csv --cases 40

# 5. Hold planner measurement (no LLM; uploads the slice and deletes it afterwards)
python -m flowtrace.eval.holds --sample eval/day_slice_2022-09-09.csv --cases 40
python -m flowtrace.eval.holds --sample demo_mixed_2022-09-09.csv
python -m flowtrace.eval.holds --sample eval/day_slice_2022-09-09.csv --cases 40 --policy llm   # LLM dispositions

# 6. Run the app (and, optionally, the MCP server for Claude Desktop or any MCP client).
#    Approve/reject needs APPROVER_TOKEN in .env: sign in as the approver in the app with it (sidebar), or put the
#    same value in frontend/.env.development.local as VITE_APPROVER_TOKEN to be signed in automatically in dev.
#    Never give it to the MCP server. DEMO_MODE=1 turns on the public-demo rules (docs/deploy.md).
python -m uvicorn flowtrace.api.main:app --port 8000
python -m flowtrace.mcp.server
cd ../frontend && npm install && npm run dev  # http://localhost:5173
npm test                                      # frontend unit tests (vitest)
```

### Upgrading an existing FalkorDB volume to append-only persistence
`docker-compose.yml` starts Redis with `--appendonly yes`, so writes survive restarts and crashes. **If your volume was created before this change, do the switch-over once, or the graph will start empty:** Redis then finds no append-only file and ignores `dump.rdb`.

```bash
# 0. Back up the current snapshot
docker exec flowtrace-falkordb-1 redis-cli SAVE
docker cp flowtrace-falkordb-1:/var/lib/falkordb/data/dump.rdb ./dump-backup.rdb
# 1. Recreate the container with the new compose file, but append-only OFF for this one start
#    (temporarily set REDIS_ARGS: "--appendonly no" in docker-compose.yml)
docker compose up -d
# 2. Turn append-only on live; Redis writes the append-only file from the loaded graph
docker exec flowtrace-falkordb-1 redis-cli CONFIG SET appendonly yes
docker exec flowtrace-falkordb-1 redis-cli INFO persistence   # wait for aof_rewrite_in_progress:0
# 3. Restore REDIS_ARGS to "--appendonly yes --appendfsync everysec" and recreate
docker compose up -d
python -m flowtrace.graph.smoke                               # from backend/: checks counts and the history marker
```

If the graph does come up empty, stop the container, copy `dump-backup.rdb` back to `/var/lib/falkordb/data/dump.rdb`, delete `appendonlydir/` in the volume, and repeat from step 1. Or reload from scratch with `python -m flowtrace.graph.load --replace`.

### Model files
`data/models/triage_lgbm.pkl` is the pickled bundle `{model, features, threshold}`. `triage_lgbm.txt` is the native LightGBM booster, and `triage_features.json` holds the feature order and threshold.

## Tests

`backend/tests` holds unit tests for recurring-payment detection, the agent loop, the LLM client (mocked OpenAI: success, refusal, truncation, invalid JSON, no key, API errors), pass-through pairing, case building (hubs, size cap, score, tiers), calibration, idempotent uploads, sample date range and the loader's index wait. The agent tests use a small in-memory stand-in for FalkorDB, so the tool, policy and disposition logic can be tested without a database. The real Cypher is checked against FalkorDB by `flowtrace.graph.smoke`.

Action-layer tests:
- **Hold planner** (in-memory graph):
  - a lien only on the traced case money (a busy case account holds only its flagged money);
  - the largest amounts within the cap, nothing below the minimum, and round-trip loops;
  - hubs never held, and hub degrees counted only before the decision;
  - holds only after ESCALATE;
  - currency.
- **Actions** (a scratch FalkorDB graph that is deleted afterwards, never the main graph):
  - double proposals, and double approvals with idempotency keys;
  - the hold cap checked in the same write, and MCP proposals marked `mcp`;
  - reasons required, and rejected holds not proposed again;
  - expiry and release, and approving CLOSE withdrawing and releasing the case's holds;
  - withdrawal on re-upload;
  - verdict write-back, and case memory preferring human verdicts.

  A separate check confirms the audit queries only ever create entries.
- **Registry**: the read tools never write, and `propose_action` refuses hubs, accounts off the trail, amounts below the minimum, and REVIEW or CLOSE cases.
- **MCP**:
  - the tools are listed and called through an in-process MCP client;
  - its proposals are recorded as `mcp`;
  - it has no approve tool, and it drops the approver token.
- **API**:
  - plan, approve/reject, error codes and audit;
  - a decision without the right approver token, or with no approver name, is refused.
- **Case page** (`tests/test_case_view.py`): account roles from the flow (a pure loop is all intermediaries), history payments left out, flagged vs followed payments, a hold account downstream of the case drawn amber, the account cap, confidence and priority thresholds from config, risk and confidence as High/Medium/Low from config (never a percentage), possible typologies only on escalated cases, each payment's own currency next to the USD equivalent, hourly vs daily timeline, an uninvestigated case, the STR draft (evidence IDs on every point, provisional holds) and its endpoints.
- **PDF report** (`tests/test_pdf_report.py`): it builds for an escalated case with a hold, a case closed by the rules
  and a case with no holds; a 30-account case with 15 holds still fits two pages with "+N more"; the case page's
  numbers, short account IDs, the footer and data credit appear in the text; the endpoint is read-only and works with
  the graph down.
- **Auto-hold** (`test_actions_service.py`, `test_registry.py`, `test_api_actions.py`): a provisional hold is audited as the agent and counts toward the cap; a person confirms it (normal term) or rejects it (released); unconfirmed, it is released after 1 business day; CLOSE and RELEASE end it; it never fires when switched off, without an approver token, on a manual re-plan, below the confidence bar, after a decided hold or after an analyst verdict; the UI is told exactly when it can act.
- **Signal measurement and scoring variants** (`test_signal_rates.py`, `test_variants.py`): each evidence code measured by its own definition (pass-through pairs, fast onward, loops with their own time rules, equal splits, convergence, cash exit), the 30-example rule and shrinkage, an escalator never switched off, a missing rates file failing loudly, variant B reproduced as first pushed, and the pre-written decision rule.
- **Frontend** (`frontend/src/lib/*.test.ts`, vitest): the layered flow layout (chains, loops, fan-out, crossings), the case stepper from real states, the STR Markdown reader, the hold promise text, business-day hold ends and payments in their own currency.
