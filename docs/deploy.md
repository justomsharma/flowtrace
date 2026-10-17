# Deploying the public demo

The public demo runs as two apps on **Fly.io**:
- `flowtrace-demo` runs the API and serves the built frontend, so there is one URL.
- `flowtrace-db` runs FalkorDB on Fly's private network. It has no public port.

Secrets are kept only in Fly's secret store, and the image is built from a clean git checkout.

## Why this setup

**Graph: FalkorDB on Fly's private network (`deploy/falkordb/fly.toml`, 512 MB, a 1 GB volume).**
- It holds the demo history graph: 54 MB in FalkorDB. The full history needs about 2.7 GB.
- The app reaches it at `flowtrace-db.internal` over Fly's WireGuard-encrypted 6PN network, and it has a password.
- FalkorDB Cloud's free tier fits the size (100 MB), but it has **no TLS**, so the graph and its password would cross
  the internet in plain text. It also has no persistence, and stops an instance unused for a day.
- With TLS (`FALKORDB_TLS=true`, Cloud's paid tiers) the app can use FalkorDB Cloud instead; see the end of this
  page.

**App: Fly.io, shared-cpu-1x, 1 GB, always on (about $6–7 a month, plus about $3 for the graph).**
- The API needs about 1 GB (LightGBM, polars). Render's free tier is 512 MB and sleeps.
- Hugging Face Docker Spaces need a paid plan and only allow outbound ports 80/443/8080.
- Fly's proxy keeps the live investigation stream open: the server sends a ping every 15 s, under Fly's 60 s idle
  limit.

**Frontend: served by the API from the same image.** One URL, no CORS, one deploy.

## Steps

Deploy from a **clean checkout of the branch**, never the working copy. Then untracked or locally changed files
(`data/models/*.json`, a local `fx_rates.json`, `.env`) are never baked into the image.

```bash
git worktree add --detach ../flowtrace-deploy feat/deploy   # a fresh copy of the committed branch
cd ../flowtrace-deploy

fly auth login
# 1. The graph, private
fly apps create flowtrace-db
fly volumes create falkordb_data --size 1 --region iad -a flowtrace-db
fly secrets set -a flowtrace-db REDIS_ARGS="--appendonly yes --appendfsync everysec --requirepass <password>"
fly deploy -c deploy/falkordb/fly.toml

# 2. The app
fly apps create flowtrace-demo            # another name: change app and PUBLIC_HOSTS in fly.toml
fly volumes create flowtrace_runs --size 1 --region iad -a flowtrace-demo
fly secrets set -a flowtrace-demo FALKORDB_PASSWORD=<password> OPENAI_API_KEY=<key> APPROVER_TOKEN=<long random>
fly deploy
```

On its first start the API finds no graph and loads the demo history: about 57K transfers, in seconds. It loads
only when FalkorDB answers that the history is missing, three checks in a row. A connection error is logged and
never clears anything. A failed load backs off, from 1 minute up to 30.

**Check:** `https://flowtrace-demo.fly.dev/api/health` should say `"graph": true, "llm": true, "demo": true`.

## Public-demo safety (DEMO_MODE=1, set in fly.toml)

- **Uploads:** only the bundled sample files.
  - A sample that is already uploaded opens as it is, so a visitor never renumbers someone else's demo.
  - A request body is capped before anything reads it: 1 MB for an upload, 64 KB for anything else.
- **Who a visitor is:** `Fly-Client-IP`, which Fly's proxy sets itself.
  - `X-Forwarded-For` is never read, and uvicorn trusts no forwarded headers.
  - An IPv6 visitor counts by its /64.
- **Rate limits** (`config.yaml`, `demo`):
  - investigations: 6 per visitor per hour, 60 in total per hour. The signed-in approver counts only toward
    the total, so a visitor cannot lock the presenter out;
  - uploads: 4 per 10 minutes;
  - hold plans: 20 per 10 minutes;
  - MCP requests: 120 per 10 minutes, read tools too.
- **Investigations:**
  - An anonymous visitor gets the rule-based agent, and their run never auto-holds.
  - The signed-in approver gets the LLM agent and the auto-hold.
  - Nothing is investigated in the background.
- **LLM spend:** a daily cap (`llm.daily_budget_usd`, $2). Over it, the rule-based policy runs until midnight UTC.
- **Approvals:** approve and reject need the approver token. Visitors can view everything.
  - Whoever has the token signs in as the **demo approver**. The token stays in that browser tab only.
  - A built frontend never carries a token.
  - Five wrong tokens from one visitor lock it out for 15 minutes, even with the right one.
- **MCP** (`/mcp`):
  - The read tools are open, within the rate limit.
  - `propose_action` needs the approver token (`X-Approver-Token`); its reason is at most 500 characters.
  - Sessions are capped (64 cases, the least recently used dropped) and expire after 30 minutes.
- **Health:** `/healthz` is Fly's liveness check. `/api/health` also reports the graph, the LLM and today's spend.

## Reset to a clean demo

```bash
cd backend
python -m flowtrace.demo.reset --url https://flowtrace-demo.fly.dev              # asks for the approver token
python -m flowtrace.demo.reset --url https://flowtrace-demo.fly.dev --prerun 5   # also prepares the backup case
```

The reset deletes every upload, investigation, hold plan, case memory, action and audit entry. That includes old
test approvals and provisional auto-holds. The history graph stays. Locally, with the API stopped, run
`python -m flowtrace.demo.reset`.

## FalkorDB Cloud instead (paid tier, TLS)

Set these secrets:
- `FALKORDB_HOST` and `FALKORDB_PORT`;
- `FALKORDB_USERNAME=falkordb`, `FALKORDB_PASSWORD` and `FALKORDB_TLS=true`.

Then remove `FALKORDB_HOST` and `FALKORDB_PORT` from `[env]` in `fly.toml`, and skip step 1. The password is read
from the environment only.

## The demo history graph

`data/demo/` holds everything the demo needs instead of the full IBM dataset:
- `history/` is the reduced graph, loaded with plain Cypher;
- `processed/` is the scoring history of the samples' accounts;
- `model/` is the triage model.

The data is CDLA-Sharing-1.0 (`data/LICENSE`, `data/NOTICE`). `python -m flowtrace.demo.history build` rebuilds it
from the full graph, and `python -m flowtrace.demo.verify` checks it. See the README, *Demo history graph*.
