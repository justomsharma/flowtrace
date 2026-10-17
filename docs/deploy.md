# Deploying the public demo

**Live:** https://flowtrace.omsharma.dev (also https://flowtrace-demo.fly.dev)

- **App:** one container on **Fly.io** in Singapore (`sin`), the nearest Fly region to the graph in Mumbai (Fly has
  no Mumbai region). The API also serves the built frontend, so there is one URL.
- **Graph:** **FalkorDB Cloud's free tier.**
- **Secrets:** only in Fly's secret store.
- **Image:** built from a clean git checkout.

## Why this setup

**Graph: FalkorDB Cloud, free tier.**
- It holds the demo history graph. That is 54 MB in FalkorDB; the full history needs about 2.7 GB, and the free
  tier has 100 MB.
- **The free tier has no TLS** (`FALKORDB_TLS=false`). The connection needs a password but is not encrypted. The
  data is the public synthetic IBM dataset, and the password is a secret on Fly only.
- With a paid tier, set `FALKORDB_TLS=true`. Or run FalkorDB on Fly's encrypted private network instead
  (`deploy/falkordb/fly.toml`, about $3 a month).
- The free tier keeps no data across a restart, and stops an instance unused for a day. The API queries the graph
  every minute, and reloads the demo history by itself (below).

**App: Fly.io, shared-cpu-1x, 1 GB, always on.**
- Costs about $6 a month, so about ₹170 for 10 days, plus a 1 GB volume (a few rupees).
- 1 GB is the smallest machine that runs it reliably: scoring an upload peaks at about 450 MB.
- Render's free tier (512 MB) sleeps after 15 minutes. Hugging Face Docker Spaces need a paid plan, and block the
  database port.
- Fly's proxy keeps the live investigation stream open: the server sends a ping every 15 s, under Fly's 60 s idle
  limit.

**Domain:** `flowtrace.omsharma.dev` is a CNAME to the Fly app, and Fly issues the certificate. The root domain
`omsharma.dev` and its records are not touched.

## Steps

### 1. Install flyctl and sign up (once)

```powershell
iwr https://fly.io/install.ps1 -useb | iex     # PowerShell; then open a new terminal
fly auth signup                                # or `fly auth login` if you already have an account; add a card
```

### 2. Create the app and its volume

Do this from a **clean checkout of the branch**, never the working copy. Then untracked or locally changed files
(`data/models/*.json`, a local `fx_rates.json`, `.env`) never go into the image.

```bash
git worktree add --detach ../flowtrace-deploy feat/deploy     # a fresh copy of the committed branch
cd ../flowtrace-deploy
fly apps create flowtrace-demo                 # another name: change app and PUBLIC_HOSTS in fly.toml
fly volumes create flowtrace_runs --size 1 --region sin -a flowtrace-demo --yes
```

### 3. Set the secrets, read from `.env`, never printed

```bash
v() { grep "^$1=" ../flowtrace/.env | cut -d= -f2-; }
fly secrets set -a flowtrace-demo --stage \
  FALKORDB_HOST="<cloud host>" FALKORDB_PORT="<cloud port>" FALKORDB_USERNAME="$(v FALKORDB_USERNAME)" \
  FALKORDB_PASSWORD="$(v FALKORDB_CLOUD_PASSWORD)" OPENAI_API_KEY="$(v OPENAI_API_KEY)" \
  APPROVER_TOKEN="$(v APPROVER_TOKEN)" > /dev/null
```

### 4. Deploy

```bash
fly deploy -a flowtrace-demo
```

On its first start, the API loads the demo history if the graph does not have it:
- It loads only when FalkorDB answers that the history is missing, three checks in a row.
- A connection error is logged and never clears anything.
- A failed load backs off, from 1 minute up to 30.

### 5. Domain

```bash
fly certs add flowtrace.omsharma.dev -a flowtrace-demo     # prints the DNS records
```

In Porkbun, add the record it prints for `flowtrace.omsharma.dev` (a CNAME to `flowtrace-demo.fly.dev`), plus the
`_acme-challenge` CNAME if it asks for one. Leave the root domain alone. Then:

```bash
fly certs show flowtrace.omsharma.dev -a flowtrace-demo    # wait for "Issued"
```

### 6. Check

- `https://flowtrace.omsharma.dev/api/health` says `"graph": true, "llm": true, "demo": true`.
- `http://` redirects to `https://`.

## Public-demo safety (DEMO_MODE=1, set in fly.toml)

**Uploads**
- Only the bundled sample files can be uploaded.
- A sample that is already uploaded opens as it is, so a visitor never renumbers someone else's demo.
- A request body is capped before anything reads it: 1 MB for an upload, 64 KB for anything else.

**Who a visitor is**
- On Fly, a visitor is identified by `Fly-Client-IP`, which Fly's proxy sets itself.
- `X-Forwarded-For` is never read, and uvicorn trusts no forwarded headers.
- An IPv6 visitor counts by its /64.

**Rate limits** (`config.yaml`, `demo`)
- Investigations: 6 per visitor per hour, and 60 in total per hour. The signed-in approver counts only toward the
  total.
- Uploads: 4 per visitor per 10 minutes.
- Hold plans: 20 per visitor per 10 minutes.
- MCP requests, read tools included: 120 per visitor per 10 minutes.

**Investigations**
- An anonymous visitor gets the rule-based agent, and their run never auto-holds.
- The signed-in approver gets the LLM agent and the auto-hold.
- Nothing is investigated in the background.

**LLM spend**
- There is a daily cap (`llm.daily_budget_usd`, $2).
- Over it, the rule-based policy runs until midnight UTC.

**Approvals**
- Approve and reject need the approver token. Visitors can view everything.
- Whoever has the token signs in as the **demo approver**, and the token stays in that browser tab only.
- A built frontend never carries a token.
- After 5 wrong tokens, a visitor is locked out for 15 minutes, even with the right one.

**MCP** (`/mcp`)
- The read tools are open, within the rate limit.
- `propose_action` needs the approver token (`X-Approver-Token`), and its reason is at most 500 characters.
- Sessions are capped at 64 cases (the least recently used is dropped) and expire after 30 minutes.

**Health**
- `/healthz` is Fly's liveness check.
- `/api/health` also reports the graph, the LLM and today's spend.

## Reset to a clean demo

```bash
cd backend
python -m flowtrace.demo.reset --url https://flowtrace.omsharma.dev              # asks for the approver token
python -m flowtrace.demo.reset --url https://flowtrace.omsharma.dev --prerun 5   # also prepares the backup case
```

The reset deletes every upload, investigation, hold plan, case memory, action and audit entry, including old test
approvals and provisional auto-holds. The history graph stays. Locally, with the API stopped, run
`python -m flowtrace.demo.reset`.

## FalkorDB versions

FalkorDB Cloud runs **v4.22**; `docker-compose.yml` runs v6. Three query differences between them were found by
comparing the cloud with the full graph, and fixed so both give the same answers ([demo-history.md](demo-history.md)).
After loading the demo graph into a new instance, check it:

```bash
python -m flowtrace.graph.smoke --demo        # every query, read-only
```

## The demo history graph

`data/demo/` holds everything the demo needs instead of the full IBM dataset:
- `history/` is the reduced graph, loaded with plain Cypher;
- `processed/` is the scoring history of the samples' accounts;
- `model/` is the triage model.

The data is licensed CDLA-Sharing-1.0 (`data/LICENSE`, `data/NOTICE`). To work with it:
- `python -m flowtrace.demo.history load` loads it into `GRAPH_NAME`.
- `python -m flowtrace.graph.smoke --demo` runs every query on it.
- `python -m flowtrace.demo.verify` compares it with the full graph.

See [demo-history.md](demo-history.md).
