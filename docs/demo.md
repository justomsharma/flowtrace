# FlowTrace: the 3-minute demo

**One mule chain from the curated sample:** payments come in, leave again within hours, and the agent freezes what
is still there.

**Live app:** https://flowtrace-demo.fly.dev

**The case:** `C001`, the top case of `demo_mixed_2022-09-09.csv`. Three accounts and $33,855 of payments.
- The money comes in from `…2DF30`, passes through `…6A6A0` within 24 hours, and lands in `…BCFF0`.
- `…BCFF0` still holds $16,468 (paid in euros; amounts are shown as US dollars).
- Every policy escalates it with High confidence (LLM about 0.9, rules 0.97), so the auto-hold happens even if the LLM falls
  back to the rules.

## Before you start (5 minutes before)

1. **Reset to a clean demo, and prepare the backup case `C005`:**
   ```bash
   cd backend
   python -m flowtrace.demo.reset --url https://flowtrace-demo.fly.dev --prerun 5
   ```
   It asks for the approver token. `--prerun 5` uploads the sample and has the agent investigate C005 (a 6-account
   fan-out ring, 3 provisional holds, $31,401). No person decides anything. Leave out `--prerun` for a fully empty
   start; then the first click on *Try the sample day* uploads it.
2. **Check health.** https://flowtrace-demo.fly.dev/api/health should show `graph`, `llm` and `demo` all true. In the
   sidebar, *System online* should show the AI investigator (gpt-6-luna).
3. **Browser tab 1:** the app at *Today's work*.
4. **Browser tab 2 (backup):** `https://flowtrace-demo.fly.dev/case?batch=BC0C047&case=BC0C047-C005`, already
   investigated.
5. **Terminal or tab 3:** the MCP Inspector, connected (see step 7). Run `npx @modelcontextprotocol/inspector`, set
   *Transport Type* to **Streamable HTTP**, *URL* to `https://flowtrace-demo.fly.dev/mcp`, then **Connect**.
6. **Sign in as the demo approver in tab 1 now** (sidebar: *Sign in as approver for the AI agent and decisions*,
   then paste the token from a password manager, off screen). Signed out, *Investigate* runs the rule-based agent
   and never auto-holds; signed in, it runs the LLM agent and the auto-hold can fire.

## The script

| Time | Click | Say |
|---|---|---|
| **0:00–0:20** | Tab 1, *Today's work*: click **Try the sample day** (or it is already there after the reset). The steps tick by: parse, score, graph, detect, cases. | "This is one day of bank payments, 2,271 of them. FlowTrace scores every payment, loads them into a FalkorDB graph on top of eight days of history, and runs Cypher detectors for laundering shapes." |
| **0:20–0:35** | Point at the list: **15 cases** in *Today's work*, 2 more under *Lower priority*. | "2,271 payments became 40 flagged payments and 17 cases. These 15 are today's work for one analyst, ranked by the chance of laundering." |
| **0:35–0:45** | Click the top case, **C001**. On the verdict card, click **Investigate**. | "Let's ask the agent to investigate the top case." |
| **0:45–1:25** | Watch the steps stream in. They are usually: account profile, *trace funds* (forward, 3 hops), recurring flows, owner network, counterparty history, then a profile of the account the money went to. | "The agent picks each step itself. Each step is a graph query: it follows the money hop by hop, checks whether these accounts ever paid each other before, and looks for salary or rent patterns that would explain it. It looks for legitimate reasons too, not just bad ones." When the verdict lands: "**Escalate, High confidence.** Money came in and left within 24 hours, to an account it had never paid before." |
| **1:25–1:50** | Scroll to **Next step**. Show the hold on `…BCFF0`: **Held provisionally by agent (auto-hold)**, the headline **$16,468 frozen now**, the bar (frozen now, still in the trail, already gone), and the proposed **File an STR with FIU-IND**. | "The agent doesn't just write a report; it acts. It froze the $16,468 that is still sitting in the last mule account. That's a lien on the case money only, not the whole account. The hold is provisional: if no person confirms it by the end of the next business day, it is released automatically. Filing the STR is never automatic." |
| **1:50–2:20** | You are already signed in as **demo approver** (sidebar). Tick the hold, type your name (asked once) and a reason, e.g. *"Funds forwarded within 24h to a new counterparty; confirming hold pending STR"*. Click **Confirm hold**. Then tick **File an STR with FIU-IND**, give a reason, and click **Approve filing**. | "Visitors can only look: they get the rule-based agent and cannot decide. Decisions need the approver's sign-in, and every decision needs a written reason. I confirm the hold and approve the STR draft." |
| **2:20–2:40** | Sidebar: **Holds & audit**. Show *Frozen today*, the hold with its countdown, and the audit log: PROPOSED by agent, PROVISIONAL by agent (auto-hold), APPROVED by you, with the reason. | "Every action is in an append-only audit log: what the agent proposed, what it did on its own, and what a person decided, and why." |
| **2:40–3:00** | Tab 3, the MCP Inspector: **Tools**, then **List Tools** (12 tools). Run **plan_holds** with `case_id` = `BC0C047-C001`: the same hold, the same $16,468. Point out **propose_action**, and that there is no approve tool. | "The same tools are an MCP server. Any agent, such as Claude, can investigate these cases. It can propose actions, but only a person approves them." |

The STR draft is on **Reports** if there is time (*Generate report* on the case page downloads it).

## Backup plans

- **The LLM is slow.** An investigation takes about 30 s, and a step can stall for 10–20 s.
  - Keep talking over the steps as they arrive.
  - If nothing has moved for 45 s, switch to **tab 2 (C005)**: the finished investigation, 3 provisional auto-holds,
    $31,401 frozen now. Do the confirm step there.
- **The LLM is down or over budget.** The agent falls back to its rule-based policy step by step (the sidebar says
  so). C001 still escalates with High confidence (0.97), and the auto-hold still happens.
- **Rate limited (429).** Each visitor gets 6 investigations an hour. Use tab 2, or wait.
- **The verdict says the rule-based agent ran.** You were signed out: sign in (sidebar) and click *Investigate*
  on another case, or use tab 2.
- **The site is down.** Run it locally: `docker compose --profile app up -d --build`, then open
  http://localhost:8000. Same app, same demo graph. Otherwise play the recorded video.
- **The graph lost its data** (it keeps it on a volume, so this should not happen). Once FalkorDB answers that the
  history is missing three times in a row, the app reloads the demo history and starts clean. Click **Try the
  sample day** again.

## After the demo

Reset again so the next visitor starts clean: `python -m flowtrace.demo.reset --url https://flowtrace-demo.fly.dev`.
