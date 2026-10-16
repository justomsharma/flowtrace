# Case UI Design

**Why.** The case was a 380 px side panel over the 3D map. Analysts and judges need one page that answers, at a glance: how much money, which accounts, why it is suspicious, what might explain it, what the agent proposes to do, and where the investigation is. Two reference images set the layout (image 1 = main, image 2 = extra right-hand panels and the stepper).

## Goals
- A light, sidebar-based app shell: Today's work, Case overview, Live Network (the existing 3D screen, unchanged), Holds & audit (the existing Results page), Reports (STR draft).
- A Case overview page backed by one read-only endpoint, `GET /api/cases/{id}/overview`.
- The agent's proposed holds are the most prominent action on the page (Track 1: agents that act).
- The agent's steps stream live on the case page.

## Non-goals
- No new detection, scoring, or planner logic. No writes from the overview.
- No search, date picker, notifications, user menu, Share, More, tour (the images have them; we have no data behind them).

## Anti-goals
- Any number on screen not backed by data. Any ₹ that is not the data's currency. Any dead button.
- Breaking upload, sample day, background investigations, live streaming, approve/reject, audit, MCP.

## Decisions (my calls; the user delegated them)
- **Stack stays:** React 19 + Vite + Tailwind 4 + recharts + lucide. The 2D flow graph is hand-rolled SVG with a longest-path layered layout (cases have 2–6 accounts, ≤ ~16 with traced money), no dagre.
- **Theme:** the app becomes light (`data-theme="light"` on `<html>`). Live Network keeps its dark 3D console inside a `.theme-dark` scope, so it looks and works exactly as today.
- **Case flow** = the case's own payments (`alert`), the money the investigation followed (`trace`, `cycle`), and the hold plan's trail payments into proposed/approved hold accounts. Investigation `context` payments (history) are excluded. Capped at `ui.flow_max_accounts` accounts.
- **Roles** from that flow: no inflow = Sender, no outflow = Destination, else Intermediary. Node amount = sent for a Sender, received otherwise.
- **Payment status:** Flagged = a case payment or one cited by a red-flag evidence item; In trail = followed money or plan trail; Normal = anything else shown.
- **Confidence badge** from evidence weight, thresholds in `config.yaml` `ui.evidence_confidence` (High ≥ 0.5, Medium ≥ 0.35, else Low). **Priority** from case score, `ui.priority` (High ≥ 0.95, Medium ≥ 0.8, else Low).
- **Top risk indicators** = the disposition's raw components (model, red flags, shape, legitimacy) as %.
- **Summary badge:** "AI" when the report author is not `template`, "Rules" otherwise.
- **Generate report** downloads Markdown from `GET /api/cases/{id}/report.md` (no PDF).
- **Currency:** case payments now carry `cur`; the overview uses the single currency of the case payments, else the plan's, else US dollars (same rule as the planner).
- **Timeline** buckets by UTC hour when the flow spans ≤ 48 h, else by UTC day.
- **Stepper:** Alert received (case created) → Initial analysis (scored) → Graph investigation (running / done) → Report (written) → Analyst review (proposals open = in progress; all decided = done).
- The current batch comes from `?batch=`, else the newest upload.
