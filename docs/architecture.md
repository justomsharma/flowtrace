# Architecture: how FlowTrace finds, investigates and acts

[← Back to the README](../README.md)

## About

The main idea: **rules catch patterns; FlowTrace explains them, and closes the ones a human analyst would close.** A salary that is split into EMIs, rent and card bills looks like "fan-out + pass-through" to a rule. FlowTrace recognises it from the graph.

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB, Oct 15–18, 2026).
**Track:** Track 01, Agents That Act on Connected Data.

## Architecture

```
CSV batch ─► parse ─► triage model (LightGBM) ─► load into FalkorDB ─► Cypher detectors ─► cases
                                                                                        │
                       ┌────────────────────── investigation loop (≤ 6 steps) ◄─────────┘
                       │  policy picks an approved tool ─► read-only Cypher ─► evidence ledger
                       │  (the LLM, or rules, or the fixed baseline)      risk re-scored each step
                       └─► deterministic disposition ─► senior-analyst report ─► validator ─► case memory in graph
                                                                                        │
                       act: hold planner (lien on the traced case money) ─► PROPOSED actions in the graph
                            ─► a person approves or rejects with a reason ─► audit trail, verdict back into case memory
```

The same tools are served over **MCP** (`python -m flowtrace.mcp.server`), so Claude Desktop or any MCP client can investigate cases and propose actions.

- **Backend:** Python, FastAPI, Server-Sent Events, FalkorDB (Python client), polars, LightGBM, networkx, OpenAI SDK (Anthropic optional)
- **Frontend:** React, Vite, TypeScript, Tailwind, Recharts, a hand-drawn SVG flow graph (layered layout), 3d-force-graph (three.js), vitest
- **LLM:** OpenAI `gpt-6-luna` by default (Responses API, strict JSON-schema output), chosen for low latency and cost. It chooses the next tool and writes the report. It never computes facts, never runs its own Cypher, and never sets the disposition.

## How flagging works

1. **Triage model.** LightGBM on 37 point-in-time features: amount, format, relationship history, same-day fan-in/out and pass-through, 2-hop scatter-gather, reciprocal flows, entity type. Features only use data up to the end of the transfer's day, which matches end-of-day batch monitoring.
   The raw score is **calibrated** to a probability of laundering (isotonic regression on the validation days, stored in `config.yaml` as `calibration`).
2. **Cypher detectors** fire on typology shapes in the batch window. **PASS-THROUGH** is time-paired: money comes in and a similar amount (0.8–1.05×) leaves to someone else within 24 hours, for incoming payments of at least $1,000 (FATF 2023 cyber-fraud indicators; FinCEN FIN-2014-A005 funnel accounts). Each detector carries its **measured precision** on the validation days (`detectors.precision`), shrunk toward the pooled rate of all detectors so a detector with few hits cannot look perfect. On full days these are low (FAN-OUT 1%, FAN-IN 5%, PASS-THROUGH 1%, SCATTER-GATHER 11%, CYCLE 30%), so detectors mainly explain cases rather than create them.
3. **Cases** ([`cases.py`](../backend/flowtrace/pipeline/cases.py)):
   - **One bar for every signal.** A payment opens a case when its calibrated probability, or the precision of a detector that fired on it, reaches `cases.flag_probability` (0.5). Weaker detector hits are attached to open cases as evidence.
   - **Suspect-centred grouping.** Cases grow outward from the riskiest accounts over flagged payments. **Hubs** (degree in the `cases.hub_lookback_days` = 6 days before the batch ≥ `cases.hub_degree`, the 99th percentile of a 6-day window = 70) never join two cases or lead one; their payments stay in the case of the other account. A case stops growing at `cases.max_accounts` (30).
   - **Score.** `case_score = 1 − (1 − model probability) · Π(1 − detector precision)` over the distinct detectors in the case: the chance that at least one signal is right (assumes independence). No hand-picked weights.
   - **Capacity.** Cases are ranked by `case_score`. The top `cases.daily_capacity` (15) are *Today's work*; the rest are listed as *Lower priority*. Nothing is hidden. This is recall at fixed investigator capacity, the way Google Cloud AML AI backtests. On a full day about 180 cases open; the 15 worked first are all laundering, and the rest stay listed below.
   - Each case's shape is classified (FAN-OUT, FAN-IN, SCATTER-GATHER, GATHER-SCATTER, CYCLE, BIPARTITE, STACK, RANDOM).
   - **Tuning.** `flag_probability` was chosen on the validation samples with a rule fixed before looking: 10–20 cases on both days, the most schemes in the top 15, the higher bar on ties.

     | Bar | Sep 7: cases / schemes in top 15 | Sep 8: cases / schemes in top 15 |
     |---|---|---|
     | 0.1 | 20 / 20 of 23 | 28 / 17 of 23 |
     | 0.3 | 17 / 20 | 23 / 17 |
     | **0.5** | **15 / 20** | **20 / 17** |
     | 0.6 | 13 / 18 | 20 / 17 |
4. **Investigation.** The agent gathers two kinds of evidence:
   - **Red flags:** new counterparties, equal splits, fast onward transfer, cash/crypto exit, round trips, activity spike, a dormant account becoming active, nearness to escalated cases.
   - **Legitimate explanations:** established counterparties, recurring flows with an inferred purpose (payroll, fixed obligation/EMI, bill, standing order), payments to high-volume receivers, activity consistent with the account's baseline, internal transfers between the same owner's accounts, money that stops moving.
   - Some red flags are **escalators**: they halve the weight of legitimate explanations.
5. **Disposition** is computed in code from the evidence ([`disposition.py`](../backend/flowtrace/agent/disposition.py)):
   `risk = 0.45·model + 0.35·red_flags + 0.20·shape − 0.40·legitimacy`
   - **ESCALATE** at risk ≥ 0.55.
   - **CLOSE** at risk < 0.25, but only if legitimate explanations are strong and no escalator fired. A closure expires after 90 days.
   - **REVIEW** for everything else.
6. **Report.** The LLM writes the senior-analyst report from the evidence ledger. A validator drops any statement that cites unknown evidence, or uses numbers that are not in the cited evidence. The unsupported-claim rate is reported for every case.

All thresholds and weights live in [`config.yaml`](../config.yaml).

**Possible typology.** The dataset labels laundering yes/no only. The "possible typology" maps the observed shape to the money-laundering typology it resembles (money mule distribution, funnel account, layering, round-tripping, money mule pass-through), plus the few indicators with a well-supported link (`crime_by_indicator`: money returning to its sender -> round-tripping, money forwarded straight on -> money mule pass-through). It never guesses the underlying crime, which the payments alone cannot show. Splits, cash or crypto, many sources and convergence are left out: they are common in normal accounts. It is a **hypothesis**, labelled *indicative*, shown for escalated cases only, and is not a measured prediction.

## Acting on a case: holds a person approves

An investigation ends with a recommendation. Then the agent acts: it plans which accounts to hold and proposes those holds, along with the case decision. **The agent proposes. Only a person can approve or reject, and they must give a reason. Nothing is frozen until a person approves, with one exception, the auto-hold below.**

**Auto-hold** (`actions.auto_hold`, on by default; decided by measurement, see [the auto-hold measurement](evaluation.md#hold-planner-measurement-honest)). It acts only when **all** of these hold:
- `actions.auto_hold.enabled` is on **and** the API server has an `APPROVER_TOKEN` (so a person can confirm or release);
- an investigation **a person started** has just finished (never a background prefetch or re-queue, never a manual re-plan, never MCP) and recommends **ESCALATE with a confidence score of 0.9 or more** (`min_confidence`; a score, not a probability);
- the planner found a hold, no hold of the case was ever decided (approved, rejected, released or expired), and no analyst gave the case a verdict, so a release or a CLOSE is never undone.

The hold is **PROVISIONAL**, audited as `agent (auto-hold)`. A person confirms it (it then runs its normal term) or rejects it (released now). If nobody confirms it within **1 business day** (`confirm_business_days`) it is released automatically. It never files an STR or escalates. The UI states this exception wherever it says "Nothing is frozen until you approve", and only when it is active on the server.

How FlowTrace holds money, and what it is based on:
- **India framing.** FlowTrace is set up for an Indian bank: STRs go to FIU-IND, and holds follow the RBI draft below. The FIU name is one setting (`actions.str_fiu` in `config.yaml`).
- **A hold freezes an amount, never a whole account.** It is a lien on the case money traced into an account. The RBI [draft KYC Amendment Directions, Sep 2026](https://rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63587) propose "temporary debit holds on amounts or accounts linked to money-mule activity"; FlowTrace holds amounts only. Indian High Courts have also limited liens to the disputed amount.
- **How long a hold lasts is a policy setting:** `actions.hold_expiry_business_days`, 4 business days by default. After that the hold is released automatically.
- **For comparison only:** the UK FCA's [FG24/6](https://www.fca.org.uk/publication/finalised-guidance/fg24-6.pdf) lets the payer's bank delay **one outbound payment** for up to 4 business days. That rule is about single payments. It is not an account hold, and FlowTrace does not cite it as one.
- **AML vendors** (Unit21, Lucinity, Feedzai) show the same split: the AI recommends, a person decides, and an audit trail records every decision.

**Hold planner** ([`actions/planner.py`](../backend/flowtrace/actions/planner.py)). It is deterministic and uses no LLM.
1. **Trail.** It traces the money forward in time from the case accounts, and adds the case's own flagged payments. It stops at the end of the case window: that is the decision time, and nothing after it is used.
   - Some payments are **exits**: cash or crypto, a payment in another currency (the data's stand-in for cross-border), or a payment to a hub. Money that took an exit is **already gone**.
2. **Hold amount.** For each account on the trail, the hold amount is the smaller of two figures:
   - the money at rest in the account since the case money arrived (money in minus money out);
   - the case money traced into it.

   So a busy company that is a case account holds only its flagged money, never its day's receipts. The sum over the trail is the **case money still in the trail**.
3. **Which holds.** The planner picks the accounts that freeze the most money, with these limits:
   - at most `actions.max_holds_per_case` (3) holds per case;
   - none below `actions.min_hold_usd` ($1,000);
   - never a hub, by any of the existing hub rules. Hub status counts only payments before the decision time.

   When two accounts hold the same amount, it picks the one that also sits on more of the case's routes.
4. **The headline is "frozen now":** what the holds freeze today.
5. **"On the case's routes" is an assumption, reported separately.** It is money resting upstream whose every route seen so far passes a held account. It counts only if the mules don't pay someone new. The planner still computes a minimum vertex cut of the trail network, but only as context: how many *account-wide* freezes would cut every route. A lien on an account with nothing in it freezes nothing, so the cut doesn't choose the holds.
6. **Holds only after ESCALATE.** REVIEW means mixed evidence, which is below "reasonable grounds to suspect", so REVIEW only proposes a senior look. *This rule was decided after seeing the Sep 9 results* (before that, REVIEW cases got holds too).

**Actions and audit** ([`actions/service.py`](../backend/flowtrace/actions/service.py)):
- **Proposals.** Proposals are keyed on case, type and account, so a double click or a re-plan never creates a second action. The per-case cap of 3 holds is checked in the same write, so two callers racing each other can't go over it. Each proposal records where it came from: `agent` (the app) or `mcp` (an outside AI client).
- **Decisions.** Approve and reject need a written reason, the approver's name, and the server's `APPROVER_TOKEN` in the `X-Approver-Token` header. The audit log records the name and how the approver was checked. The same `Idempotency-Key` returns the first answer; any other second decision gets a 409.
- **What a decision changes, all in one query:**
  - **Approving FILE_STR, ESCALATE or CLOSE** writes the person's verdict on the Case node, and withdraws the case's other open case decisions.
  - **Approving CLOSE** (the case is legitimate) also withdraws the case's proposed holds and releases its approved holds.
  - **Approving a RELEASE** ends that hold.
- **Append-only audit.** Audit entries are only ever created. No query changes or deletes one.
- **Case memory.** `PRIOR_CASES` and `NEAREST_ESCALATED` prefer a person's verdict over the agent's, and keep the `created_ts < $t0` rule.

**API:**
- `POST /api/cases/{id}/plan` plans holds and proposes them. It needs a finished investigation, and it also runs automatically after each investigation.
- `GET /api/cases/{id}/plan` returns the plan, the actions, and what the open holds freeze now.
- `GET /api/actions?status=&case_id=` lists actions.
- `POST /api/actions/{id}/approve` and `/reject` take `{reason, by}`, the `X-Approver-Token` header, and an optional `Idempotency-Key`.
- `GET /api/audit?case_id=&limit=` returns the audit trail.
- `GET /api/cases/{id}/overview` returns everything the case page shows, built from the stored case, result and hold plan plus a read-only listing of the case's actions (it never writes, so polling it is safe): figures, the flow with account roles, timeline, amount distribution, payments, findings, summary and investigation status.
- `GET /api/cases/{id}/report.md` downloads the STR draft as Markdown (409 until the case is investigated).
- `GET /api/cases/{id}/report.pdf` downloads the case report as a PDF of at most two pages, built from the same
  overview (read-only, 409 until the case is investigated).

**One tool registry** ([`agent/registry.py`](../backend/flowtrace/agent/registry.py)):
- **Read-only tools** (GRAPH.RO_QUERY): the investigation tools, `get_case`, `list_cases` and `plan_holds`.
- **One write tool**, `propose_action`, with the same checks for every caller:
  - a hold only after ESCALATE, only on an account on the trail, and never on a hub;
  - only for at least the minimum amount, and only within the cap;
  - a RELEASE only of an approved hold.
- The in-app agent, the app's act step and the MCP server all use this one registry.

## MCP server

[`backend/flowtrace/mcp/server.py`](../backend/flowtrace/mcp/server.py) uses the official Python SDK (`mcp` 2.x, `MCPServer`).
- **Read-only tools:** `account_profile`, `counterparty_history`, `recurring_flows`, `trace_funds`, `shared_counterparties`, `find_cycles`, `owner_network`, `case_memory`, `get_case`, `list_cases`, `plan_holds`. They are annotated `read_only_hint`, and their Cypher runs through GRAPH.RO_QUERY, so FalkorDB itself rejects a write.
- **One write tool:** `propose_action`. Its proposals are recorded as `proposed_by: "mcp"`, and the confirm panel shows "Proposed by an outside AI client (MCP)".
- **It can't approve.** There is no approve tool, and the standalone process drops `APPROVER_TOKEN` from its environment at startup, so it never holds the approver token.
- **Hosted.** The API also serves the same server at `/mcp` (streamable HTTP), so a client can use the live demo's tools: `https://flowtrace.omsharma.dev/mcp` (for example in the MCP Inspector: `npx @modelcontextprotocol/inspector`, transport *Streamable HTTP*). Mounted there, no tool approves either; only the API's approve endpoints, behind the approver token, do. On the public demo every MCP request counts toward a per-visitor limit, and `propose_action` also needs the approver token in an `X-Approver-Token` header.
- **Sessions.** Investigation tools keep one context per case for the session, so an account found by `trace_funds` can be profiled next.

```bash
cd backend
python -m flowtrace.mcp.server             # stdio (Claude Desktop, IDEs, most clients)
python -m flowtrace.mcp.server --http      # streamable HTTP on http://127.0.0.1:8765/mcp
```

The server needs FalkorDB and the cases the app saved (`data/runs`), but not the API.

**Claude Desktop.** Edit the config file, then restart Claude Desktop. Do **not** add `APPROVER_TOKEN` to it.
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "flowtrace": {
      "command": "C:\\path\\to\\flowtrace\\venv\\Scripts\\python.exe",
      "args": ["-m", "flowtrace.mcp.server"],
      "cwd": "C:\\path\\to\\flowtrace\\backend",
      "env": { "PYTHONPATH": "C:\\path\\to\\flowtrace\\backend" }
    }
  }
}
```

**Any other MCP client.** Run the same command over stdio, or point the client at an HTTP endpoint (local or the live demo's). Try: "list the cases, then plan holds for the top one".

## The screens, one by one

All screenshots are from the real app on the curated sample day (`demo_mixed_2022-09-09.csv`), at laptop width. Screens 2–4 and 6–9 were taken on the live demo; screens 1 and 5 come from an earlier local run.

**1. Upload a day of payments**, or try the sample day. The steps show while the file is processed.

![Upload in progress](screenshots/upload.png)

**2. Today's work.** 2,271 payments become 40 flagged payments and 17 cases. The 15 with the highest chance of laundering are today's work; the other 2 are folded under *Lower priority*. On a local run the agent investigates the top 15 in the background; on the public demo nothing runs in the background, and here cases 1–8 were investigated by the AI agent. The sidebar lists the top cases with a dot for their disposition, and "System online" comes from `/api/health` (graph database and AI investigator). This is the curated demo sample; full-day numbers are in [Results](evaluation.md#results-measured-reproduce-with-the-commands-below).

![Today's work: ranked cases](screenshots/todays-work.png)

**3. Case overview.** One page per case:
- **Header:** the case, its laundering shape and a priority from the case score. *Generate report* downloads the two-page PDF case report (the STR draft is a small *Markdown* link under it).
- **Four figures:** the amount involved, accounts connected, red flags found, and the investigation status (Queued, Investigating, Escalate, Review or Close).
- **Transaction flow**, left to right. Each account's role comes from the flow itself: no money in = sender, no money out = destination, otherwise intermediary. Red arrows are suspicious payments, amber arrows carried case money into a proposed hold, and the amber ring marks the hold (dashed = proposed, solid = approved).
- **Key findings:** one row per red flag, with a confidence badge from the evidence weight (thresholds in `config.yaml`, `ui.evidence_confidence`). Under them, the legitimate explanations the agent found.
- **Verdict card:** risk and confidence as High / Medium / Low, at the thresholds in `config.yaml` (`disposition.escalate_at`, `close_below`, `confidence_labels`). The confidence score measures distance from a decision line; it is not a probability, so no percentage is shown. Escalated cases also show a *possible typology (indicative, not a finding)*.

Every amount is the **USD equivalent** (the dataset's payments in other currencies are converted at its fixed rates), and each payment row also shows the payment in its own currency (EUR, SAR, BRL…).

The screenshot shows case 5's money flow, key findings and summary.

![Case overview](screenshots/case-overview.png)

**4. The agent acts: recommended next step.** After an escalation, the agent proposes which accounts to hold. A hold freezes only the case money traced into an account, never its whole balance. The headline is the money frozen now. The bar splits the case money into frozen now, still in the trail, and already gone (cash, crypto, another currency or a merchant). Every hold, and the case decision (here: file an STR), waits for a person. Nothing starts ticked, and holds and the case decision are approved or rejected separately. A proposed hold shows when it would end before anyone approves it. Here, case 5's three holds were placed provisionally by the agent (auto-hold) and wait for a person to confirm or release them.

![Provisional holds waiting for a person](screenshots/holds.png)

**5. A person decides.** Approve or Reject needs the person's name (asked once) and a written reason, and the server checks its approver token. An approved hold shows who approved it, why, and a live countdown to its automatic release.

![Approved hold and STR filing](screenshots/approve.png)

**6. Live investigation.** The agent's checks stream onto the case page as it works: what it checked and what each check found, then the report is written (here case 9, four checks in). The stepper at the top of the page maps the case to its real states: alert received, scored, graph investigation, report, analyst review.

![Investigation streaming live](screenshots/investigation.png)

**7. Holds & audit.** Money frozen today, holds in place with live countdowns, proposals waiting for a decision, and the append-only audit log, filtered by what needs you. Amounts are the USD equivalent.

![Holds and audit log](screenshots/results.png)

**8. Reports.** The STR draft of every investigated case, built from the report, the evidence, the proposed actions and what people decided. It downloads as Markdown. When the AI quotes an account's totals for the whole window, a note under the summary says which figure is which, as on the case page.

![STR draft](screenshots/reports.png)

**The PDF case report.** *Generate report* (case page or Reports page) downloads a PDF of at most two A4 pages, for an
analyst or a compliance officer:
- **Page 1, the case at a glance:** the decision (Escalate / Review / Close) with risk, priority and confidence; who
  investigated it (the AI model or the rules) and who reviewed it; a short summary; amount, accounts, payments and
  time span; the money flow (Sender to Intermediary to Destination, amounts on the arrows, holds dashed); the key
  findings with High / Medium / Low confidence, and the possible legitimate explanation.
- **Page 2, actions and evidence:** the holds (amount frozen, status, until when, who decided, why), the largest
  payments with their own currency, the STR narrative (who / what / when / where / why / how) with evidence IDs, and
  the latest audit entries.

It is built from the case page's own overview, so the numbers match. Long lists end with "+N more", and each page is
shrunk to fit, so it never runs to a third page. It prints well in black and white. It is made in Python with
reportlab (no browser, no system packages). Sample: [docs/sample-report.pdf](sample-report.pdf) (case 5 of the
curated demo day, with three holds placed provisionally by the agent and waiting for a person).

![PDF case report, pages 1 and 2](screenshots/report-pdf.png)

**9. Live Network.** The 3D map of every payment in the file, with the open case highlighted (here case 5 and its trail) and the agent's money trace drawn as it runs. *View full graph* on a case opens it here.

![Live Network](screenshots/live-network.png)
