# Deploying the public demo

The live demo is one container on **Fly.io** (the API serves the built frontend, so there is one URL) and the graph on
**FalkorDB Cloud's free tier**. Secrets live only in Fly's secret store.

## Why this setup

| Piece | Choice | Why |
|---|---|---|
| Graph | FalkorDB Cloud, free tier (100 MB, AWS us-east-1) | The demo history graph needs 45 MB (the full history needs about 2.7 GB). The free tier has no persistence and stops an instance unused for a day, so the API keeps it busy (a query every minute) and reloads the demo history by itself if the graph is ever empty. |
| App | Fly.io, shared-cpu-1x, 1 GB, always on (about $6–7 a month) | The API needs about 1 GB (LightGBM, polars). Render's free tier is 512 MB and 0.1 CPU and sleeps after 15 minutes; Hugging Face Docker Spaces now need a paid plan and only allow outbound ports 80/443/8080, which blocks FalkorDB Cloud's Redis port. Fly runs the image as is, keeps a small volume for `data/runs`, and its proxy keeps the live investigation stream open (the server sends a ping every 15 s, under Fly's 60 s idle limit). |
| Frontend | Served by the API from the same image | One URL, no CORS, one deploy. |

## Steps (about 15 minutes)

1. **FalkorDB Cloud.** Sign up at https://app.falkordb.cloud, create a **Free** instance on **AWS us-east-1**, and set a
   password. Note the host and port. The user is `falkordb`.
2. **Fly.** Install flyctl (PowerShell: `iwr https://fly.io/install.ps1 -useb | iex`), then:

   ```bash
   fly auth login
   fly apps create flowtrace-demo            # or another name: change app and PUBLIC_HOSTS in fly.toml
   fly volumes create flowtrace_runs --size 1 --region iad
   fly secrets set FALKORDB_HOST=<host> FALKORDB_PORT=<port> FALKORDB_USERNAME=falkordb FALKORDB_PASSWORD=<password> \
       OPENAI_API_KEY=<key> APPROVER_TOKEN=<a long random string>
   fly deploy
   ```

   On its first start the API finds the graph empty and loads the demo history (about 50K transfers; under a minute).
3. **Check.** `https://flowtrace-demo.fly.dev/api/health` should say `"graph": true, "llm": true, "demo": true`.

## Public-demo safety (DEMO_MODE=1, set in fly.toml)

- **Uploads:** only the bundled sample files. A sample that is already uploaded opens as it is, so a visitor never
  renumbers someone else's demo. Other files get a 403 that says to run FlowTrace locally.
- **Rate limits** (`config.yaml`, `demo`): 6 investigations per visitor per 10 minutes, 60 in total per hour, and
  4 uploads per visitor per 10 minutes.
- **LLM spend:** a daily cap (`config.yaml`, `llm.daily_budget_usd`, $2). Over it, the agent and the reports use the
  rule-based policy until midnight UTC. The sidebar says so.
- **Approvals:** approve and reject need the approver token. Visitors can view everything. Whoever has the token signs
  in as the **demo approver** (sidebar, or the decision bar on a case); the token stays in that browser tab only.
- **No background investigations:** a person starts each one (which is also when the agent may auto-hold).
- **Health:** `/healthz` is Fly's liveness check; `/api/health` also reports the graph, the LLM and today's spend.

## Reset to a clean demo

```bash
cd backend
python -m flowtrace.demo.reset --url https://flowtrace-demo.fly.dev              # asks for the approver token
python -m flowtrace.demo.reset --url https://flowtrace-demo.fly.dev --prerun 2   # also prepares the backup case
```

It deletes every upload, investigation, hold plan, case memory, action and audit entry (including old test approvals
and provisional auto-holds). The history graph stays. Locally, with the API stopped: `python -m flowtrace.demo.reset`.

## The demo history graph

`data/demo/` holds everything the demo needs instead of the full IBM dataset:

- `history/` is the reduced history graph (CSV, loaded with plain Cypher, so it works on FalkorDB Cloud);
- `processed/` is the scoring history of the samples' accounts;
- `model/` is the triage model.

`python -m flowtrace.demo.history build` rebuilds it from the full graph, and `python -m flowtrace.demo.verify` checks
it. See the README, *Demo history graph*.
