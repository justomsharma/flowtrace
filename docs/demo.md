# FlowTrace: the 3-minute demo

**One mule chain from the curated sample:** money comes in and leaves again within hours, and the agent freezes what
is still there.

**Live app:** https://flowtrace.omsharma.dev

**The case:** `C001`, the top case of `demo_mixed_2022-09-09.csv`. Three accounts and $33,855 of payments.
- The money comes in from `…2DF30`, passes through `…6A6A0` within 24 hours, and lands in `…BCFF0`.
- `…BCFF0` still held $16,468 at the last flagged payment (9 Sep 13:01 UTC; paid in euros, shown as US dollars).
- Every policy escalates it with High confidence (LLM 0.91–0.97, rules 0.97), so the auto-hold happens even if the
  LLM falls back to the rules.

## Before you record (5 minutes before)

- [ ] **Reset, and prepare the backup case `C005`:**
  ```bash
  cd backend
  python -m flowtrace.demo.reset --url https://flowtrace.omsharma.dev --prerun 5
  ```
  It asks for the approver token. It uploads the sample (so *Today's work* is already loaded) and has the agent
  investigate C005 (a 6-account fan-out, 3 provisional holds, $31,401). C001 stays uninvestigated for the demo.
- [ ] **Check health:** https://flowtrace.omsharma.dev/api/health shows `graph`, `llm` and `demo` all true.
- [ ] **Sign in as the approver** in tab 1 (sidebar: *Sign in as approver for the AI agent and decisions*; paste the
  token off screen). Signed out, *Investigate* runs the rule-based agent and never auto-holds.
- [ ] **Tab 1:** the app at *Today's work*.
- [ ] **Tab 2 (backup):** `https://flowtrace.omsharma.dev/case?batch=BC0C047&case=BC0C047-C005`.
- [ ] **Tab 3:** the MCP Inspector, connected. Run `npx @modelcontextprotocol/inspector`, set *Transport Type* to
  **Streamable HTTP** and *URL* to `https://flowtrace.omsharma.dev/mcp`, then **Connect**.
- [ ] **Close every other tab and app**, and turn off notifications.

## The script

| Time | Click | Say |
|---|---|---|
| **0:00–0:15** | Tab 1, *Today's work*. | "Mule money leaves within hours. By the time an analyst opens the alert, it has been cashed out. FlowTrace finds it on a FalkorDB payment graph and freezes what is still there." |
| **0:15–0:25** | Point at the list: **15 cases** in *Today's work*, already loaded. | "This is one day of payments, 2,271 of them, scored and loaded into the graph. 40 flagged payments became 15 cases for one analyst, ranked by the chance of laundering." |
| **0:25–1:15** | Click **C001**. On the verdict card, click **Investigate**. Watch the steps stream in and the trace fill the money flow (*3D view* shows it on the Live Network). | "The agent picks each step itself, and each step is a graph query. It follows the money hop by hop, checks whether these accounts ever paid each other before, and looks for salary or rent that would explain it." When the verdict lands: "**Escalate, High confidence.** The money came in and left within 24 hours, to an account it had never paid before." |
| **1:15–1:45** | The banner: **$16,468 held provisionally by the agent (auto-hold)**. Scroll to **Next step**: the hold on `…BCFF0`, **Held provisionally by agent (auto-hold)**, and its basis. | "The agent doesn't just write a report; it acts. It froze the $16,468 that was still in the last account at the last flagged payment. That is a lien on the case money only, not the whole account. If no person confirms it by the end of the next business day, it is released." |
| **1:45–2:15** | Tick the hold only. Type your name (asked once) and a reason, e.g. *"Funds forwarded within 24h to a new counterparty; confirming hold pending STR"*. Click **Confirm hold**. | "A person decides, with a written reason. Visitors can only look; decisions need the approver's sign-in. Filing the STR is a separate decision, and it is never automatic." |
| **2:15–2:35** | Sidebar: **Holds & audit**. Show the hold with its countdown and the audit log: PROPOSED by agent, PROVISIONAL by agent (auto-hold), APPROVED by you, with the reason. | "Every step is in an append-only audit log: what the agent proposed, what it did on its own, and what a person decided, and why." |
| **2:35–2:59** | Tab 3, the MCP Inspector: **Tools**, **List Tools**. Run **plan_holds** with `case_id` = `BC0C047-C001`: the same hold, the same **$16,468**. | "The same tools are an MCP server. Any agent, such as Claude, can investigate these cases and propose actions. There is no approve tool: only a person approves." |
| **2:59** | End. | |

## Backup plans

- **The LLM is slow.** An investigation takes about 30 s, and a step can stall for 10–20 s. Keep talking over the
  steps. If nothing has moved for 45 s, switch to **tab 2 (C005)**: finished, 3 provisional auto-holds, $31,401
  frozen. Confirm a hold there.
- **C001 comes back Medium, with no auto-hold.** Say it plainly: "it escalated, but not confidently enough to freeze
  money on its own; a person decides." Then switch to **tab 2 (C005)**.
- **The LLM is down or over budget.** The agent falls back to its rule-based policy (the sidebar says so). C001 still
  escalates at 0.97 and the auto-hold still happens.
- **The verdict says the rule-based agent ran.** You were signed out: sign in and use tab 2.
- **Rate limited (429).** Each visitor gets 6 investigations an hour. Use tab 2, or wait.
- **The site is down.** Run it locally: `docker compose --profile app up -d --build`, then open
  http://localhost:8000. Otherwise play the recorded video.

## After the demo

Reset again so the next visitor starts clean: `python -m flowtrace.demo.reset --url https://flowtrace.omsharma.dev`.
