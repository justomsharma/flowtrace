# Evaluation: every number, and how it was measured

[← Back to the README](../README.md)

## Results (measured; reproduce with the commands below)

| What | Result | How it was measured |
|---|---|---|
| Triage model, Precision@100 | **97%** | Of the 100 highest-scored transfers on held-out test days (Sep 9–10), 97 are labelled laundering |
| Triage model, F1 (laundering class) | **0.515** (P 0.65, R 0.43, PR-AUC 0.51) | Same held-out days. Accuracy is 99.91%, but flagging nothing already scores 99.89%, so accuracy is not used to judge the model |
| Laundering-shape recognition | **96.5%** on 282 schemes with 3+ accounts; 80.8% on all 370 | Against IBM's ground-truth pattern file. Schemes with 1–2 transfers are inherently ambiguous |
| Strongest legitimacy signal | First-time account pairs launder at **1.69%**; pairs with >10 prior transfers at **0.018%** (92× lower) | Whole HI-Small dataset |
| Case funnel, **full test day** (Sep 9, 654,467 payments, 0.08% laundering) | **280 flagged → 186 cases**, largest 9 accounts; **15 of the top 15 cases are laundering**; of 121 schemes active that day, 18 are in the top 15, 49 in the top 50, 93 in some case | `python -m flowtrace.eval.fullday --day 2022-09-09`: offline, every payment, no graph upload, detectors not run (see [How flagging works](architecture.md#how-flagging-works)). Validation days: 168 and 176 cases, 22 of 117 and 22 of 116 schemes in the top 15 |
| Case funnel, **full test day** (Sep 10, 208,325 payments, 0.21% laundering) | **262 flagged → 180 cases**, largest 7 accounts; **15 of the top 15 cases are laundering**; of 124 schemes active that day, 18 are in the top 15, 51 in the top 50, 86 in some case | `python -m flowtrace.eval.fullday --day 2022-09-10`: same offline method as Sep 9 |
| Case funnel, **curated demo sample** (Sep 9, 2,271 payments, 2.3% laundering, ~29× a real day) | 40 flagged → 17 cases, largest 6 accounts; 14 of the top 15 cases are laundering; 18 of 26 schemes in the top 15 | `python -m flowtrace.eval.funnel ../data/samples/demo_mixed_2022-09-09.csv`. The sample is hand-picked around known schemes for the demo, so it reads far better than a real day |
| Adaptive agent vs fixed workflow, **Sep 9 eval slice** (40 cases: 21 laundering, 19 legitimate) | Escalate recall **1.00 / 1.00 / 0.67** and precision **0.58 / 0.57 / 0.74** (fixed / heuristic / llm); legitimate cases escalated 15 / 16 / 5 of 19; no laundering case closed; 0.5% of the LLM report's claims unsupported | Same alerts, same query budget, final test after the scoring was chosen on Sep 7–8; see [Agent evaluation](#agent-evaluation-adaptive-agent-vs-fixed-workflow) |
| Hold planner, **Sep 9 eval slice** (top 40 ranked cases), laundering cases (21) | With rule-based dispositions, holds **freeze $5.59M** of the $5.67M of case money still in the trail; 61% of the 41 holds are on accounts that really launder, against a **64% base rate**; on legitimate cases the proposals would wrongly freeze **$98.3M** if approved. With the LLM's dispositions: **$5.51M** frozen, 57% vs 64%, and **$47,796** wrongly frozen | `python -m flowtrace.eval.holds --sample eval/day_slice_2022-09-09.csv --cases 40 [--policy llm]`. Full table and caveats in [Hold planner measurement](#hold-planner-measurement-honest) |

For comparison, published results on HI-Small are 62.9 F1 for GFP+LightGBM and 68.2 for Multi-PNA, on a different split that includes the Sep 11–18 tail. We exclude that tail (see [Data](#data)).

## Hold planner measurement (honest)

How the planner works is in [architecture.md](architecture.md#acting-on-a-case-holds-a-person-approves).

How the measurement works:
- **Which cases.** The **top 40 ranked cases** of the Sep 9 eval slice, and every case of the curated sample.
- **Dispositions** come from the rule-based investigation (deterministic, no LLM), or with `--policy llm` from the LLM agent (one run; it is not deterministic). Holds are planned only after ESCALATE, as in the app.
- **Correct hold.** A hold counts as correct when its account sent or received a labelled laundering payment **before the decision time**.
- **Base rate.** The same share among *every* account a hold could have gone on (the planner's candidates), shown next to the precision.
- **Fixed in advance.** The config values (cap, minimum, expiry) were set before the first run. The two later method changes were decided after seeing Sep 9:
  - holds only after ESCALATE;
  - a lien on the traced amount instead of the account's balance.
- **Currency.** Amounts are the US dollar equivalent.

| | Sep 9 slice: 21 laundering cases | Curated sample: 17 cases (16 laundering) |
|---|---|---|
| Case money still in the trail | $5,669,216 (median case $31,763) | $813,787 |
| **Frozen now by the planned holds** | **$5,588,222 (98.6%)** | **$704,345 (86.6%)** |
| On the case's routes (assumption, not held) | $7,097 | $1,580 |
| Already gone (cannot be held) | $144,885 | $116,663 |
| Holds | 41, in 17 cases | 16, in 9 cases |
| Holds on accounts that really launder | 25 of 41 (61%) | 10 of 16 (62.5%) |
| Base rate among accounts a hold could go on | 37 of 58 (64%) | 15 of 21 (71%) |

**Cost: money wrongly frozen on legitimate cases** (Sep 9 slice, 19 legitimate cases), rule-based dispositions:
- The rule-based investigation escalates 16 of them.
- The planner proposes 15 holds that would freeze **$98,331,467** if a person approved them.
- $98.2M of that comes from two cases (C024 and C025). In each, the *flagged payment itself* is a single ACH of $77.3M or $20.9M into the case account. So a lien limited to the flagged amount is still that large, and only a person rejecting it prevents the cost.
- The other 13 holds come to $104,501.

**Rules vs LLM: whose escalation gates the holds** (Sep 9 slice, top 40 cases; the planner and the full graph are the same, only the disposition differs; measured from a clean graph on Oct 17; the rules row rerun after the forwarded-share fix, the llm row from before it):

| Dispositions from | Laundering cases escalated | **Frozen now** (laundering; $5,669,216 still in the trail) | Holds on accounts that really launder vs **base rate** | Legitimate cases escalated | **Wrongly frozen** if approved (legitimate) |
|---|---|---|---|---|---|
| rules (heuristic) | 21 of 21 | **$5,588,222** (98.6%), 41 holds in 17 cases | 25 of 41 (61%) vs 64% | 16 of 19 | **$98,331,467** (15 holds) |
| llm (gpt-6-luna) | 16 of 21 (5 sent to REVIEW) | **$5,505,124** (97.1%), 35 holds in 14 cases | 20 of 35 (57%) vs 64% | 7 of 19 | **$47,796** (7 holds) |

- **The LLM cuts the cost by more than 2,000×** ($98.3M to $47.8K) because it does not escalate C024 and C025, the two
  legitimate cases with one huge ACH payment each. It still freezes 97% of the laundering money still in the trail.
- **The 5 laundering cases it sends to REVIEW lose their holds** (holds follow ESCALATE only). This costs $83,098 of
  money that could have been frozen, and a person still sees each case.
- **Picking which account to hold is no better than the base rate under either policy** (61% and 57% vs 64%). The
  disposition decides *whether* to hold; the planner decides *where*, by amount.
- **Read with care:** one LLM run on 40 cases. The earlier agent eval of the same slice escalated 14 of 21 laundering
  and 5 of 19 legitimate cases, so expect a few cases to move between runs.

To reproduce, run `python -m flowtrace.eval.holds --sample eval/day_slice_2022-09-09.csv --cases 40` (add `--policy llm` for the LLM row) and `--sample demo_mixed_2022-09-09.csv`.
- Each run writes `data/models/holds_eval_*.json`.
- The planner only reads.
- The slice batch is uploaded and then deleted. The demo batch is left alone if the UI already uploaded it.

**Auto-hold, measured** (`flowtrace.eval.variants`, heuristic dispositions, the rule written before measuring: default on only with precision ≥ 90% on Sep 7–8 and no legitimate case with more than $1M frozen):

| | Validation, Sep 7–8 (80 cases) | Final test, Sep 9 (40 cases) |
|---|---|---|
| Cases it would auto-hold | 11, all laundering (100%) | 6, all laundering (100%) |
| Of their holds, on accounts that really launder | 60% | 69% |
| Money it would freeze | $1,078,269 | $4,958,624 |
| **Money wrongly frozen** (legitimate cases) | **$0** | **$0** |

So auto-hold is on by default. Read it with care: 11 of 11 still leaves a 95% lower bound of about 72% precision, and the scoring first pushed (variant B below) would have auto-frozen **$77.3M** on one legitimate Sep 9 case (C024).

What the numbers say:
- **The holds freeze most of the case money still in the trail** (98.6% and 86.6%), with at most 3 holds per case.
- **Picking by amount is no better than picking at random** among the holdable accounts: 61% vs a 64% base rate, and 62.5% vs 71%. The planner finds where the money is. It doesn't tell a mule from an innocent receiver.
- **The decision before the holds sets their cost.** The rule-based investigation escalated 16 of 19 legitimate cases on this slice, the LLM 7. This is why every hold waits for a person.
- **The hub rule costs some laundering.** In 2 slice cases and 3 curated cases, every case account is a hub (70+ payments in 6 days before the case), so those cases get no hold.
- **The samples are small and inflated.** About 50% of the slice's cases are laundering, and 94% of the curated sample's, against well under 1% of payments on a real day. With n = 40 and n = 17, one case moves a share by several points.

## Agent evaluation: adaptive agent vs fixed workflow

[`flowtrace.eval.compare`](../backend/flowtrace/eval/compare.py) runs three policies on the same alerts with the same budget:
- **fixed:** the same tools in the same order for every case;
- **heuristic:** adaptive rules, no LLM;
- **llm:** the adaptive agent (the LLM chooses each step and writes the report).

It reports:
- the share of legitimate alerts auto-closed;
- the share of laundering kept open;
- escalation precision;
- evidence coverage (the share of ground-truth scheme transfers the investigation retrieved);
- the unsupported-claim rate;
- queries and seconds per case.

The curated demo file has almost no legitimate cases. For a fairer comparison, build a slice of the real day:
pick the top 20 laundering and top 20 legitimate cases of the full-day ranking, and keep every payment of their
accounts. Uploading that slice rebuilds its own cases (for 2022-09-09: **21 laundering + 19 legitimate**, because
cases group differently when only these accounts are present); `compare` scores the top 40 of that ranking.

Read the results with care:
- The slice is about **50% laundering**, far above a real day (well under 1%). Escalation precision measured on it is
  **not real-world precision**; use it only to compare policies with each other.
- **n = 40** is small: one case moves recall by about 5 points. Treat differences of a few cases as noise.

```bash
python -m flowtrace.eval.slice --day 2022-09-09 --pos 20 --neg 20      # writes data/samples/eval/day_slice_2022-09-09.csv
python -m flowtrace.eval.compare --sample eval/day_slice_2022-09-09.csv --cases 40
```

**Sep 9 eval slice, top 40 cases (21 laundering, 19 legitimate), dev scoring (variant A below), gpt-6-luna.** The fixed and heuristic columns were rerun on Oct 17 after the forwarded-share fix (each receiver counts only up to what it received); the llm column is from the run before it.

| | fixed | heuristic | llm |
|---|---|---|---|
| Escalate recall (laundering escalated) | 1.00 (21/21) | 1.00 (21/21) | 0.67 (14/21) |
| Laundering kept open (escalate or review) | 1.00 | 1.00 | 1.00 |
| Escalate precision | 0.58 | 0.57 | 0.74 |
| Legitimate cases escalated | 15 of 19 | 16 of 19 | 5 of 19 |
| Legitimate cases auto-closed | 0 | 0 | 0 |
| Evidence coverage | 0.21 | 0.30 | 0.30 |
| Unsupported-claim rate | 0 | 0 | 0.005 |
| Cypher queries / seconds per case | 14.7 / 0.09 | 15.2 / 0.08 | 17.2 / 30.2 |

The LLM sends the 7 laundering cases it does not escalate to REVIEW, so a person still sees them; it escalates far fewer legitimate cases. No LLM step fell back to the rules and no report fell back to the template.

**Scoring variants.** A teammate proposed weighting each indicator by its measured laundering lift. The variants were compared by a rule written down before measuring ([decision rules](superpowers/specs/2026-10-16-simple-report-review-design.md)): keep escalate recall within 0.02 of A for both free policies, then escalate the fewest legitimate cases, then freeze the least money on them. Validation is Sep 7 and Sep 8 slices built the same way (80 cases), each replayed on a scratch graph holding history up to that day only; the same investigations are re-scored under each variant (`flowtrace.eval.variants`).
- **A**: dev scoring, every indicator at its tool weight.
- **B**: as first pushed: payment-level lift from Sep 1–8 (overlapping the validation days), CYCLE and CASH_OR_CRYPTO_EXIT borrowing other codes' lifts, a "≥ 500 payments" rule, and an escalator switched off when its lift was weak.
- **C**: B fixed: each code measured on its own definition on Sep 1–6 (`flowtrace.ml.signal_rates`), used only with 30+ laundering examples, shrunk toward 1 (10 pseudo-examples), never switching off an escalator.

| Variant | Sep 7–8 recall, fixed / heuristic | Sep 7–8 legitimate escalated (of 38 + 38) | Sep 9 recall, fixed / heuristic / llm | Sep 9 legitimate escalated (of 19), fixed / heuristic / llm |
|---|---|---|---|---|
| **A (winner)** | 0.83 / 0.88 | **62** | 1.00 / 1.00 / 0.67 | 15 / 16 / 5 |
| B | 1.00 / 1.00 | 76 | 1.00 / 0.95 / 0.81 | 19 / 19 / 9 |
| C | 0.98 / 1.00 | 65 | 1.00 / 0.95 / 0.67 | 18 / 18 / 6 |

All three keep recall, and A escalates the fewest legitimate cases, so `disposition.measured_weights.enabled` stays off. The fixed measurement still drives the case page's *Strong sign* / *Common in normal accounts* tags. Sep 9 was run once, after the choice, and nothing was changed after seeing it; B and C on Sep 9 are for comparison only. On every variant the planned holds on the validation days' legitimate cases come to about $1.31B if a person approved them all, mostly a few very large flagged payments; that is why every hold except the provisional auto-hold waits for a person.

```bash
# validation days: a scratch graph with history up to the day, so the main graph is never touched
GRAPH_NAME=flowtrace_val python -m flowtrace.graph.load --until 2022-09-07 --replace
GRAPH_NAME=flowtrace_val python -m flowtrace.eval.variants --sample eval/day_slice_2022-09-07.csv --cases 40
GRAPH_NAME=flowtrace_val python -m flowtrace.graph.load --until 2022-09-08 --replace
GRAPH_NAME=flowtrace_val python -m flowtrace.eval.variants --sample eval/day_slice_2022-09-08.csv --cases 40
python -m flowtrace.eval.variants --decide variants_eval_day_slice_2022-09-07.json variants_eval_day_slice_2022-09-08.json
# final test, once, on the main graph (uploads the slice and deletes it afterwards)
python -m flowtrace.eval.variants --sample eval/day_slice_2022-09-09.csv --cases 40 --policies fixed,heuristic,llm
```
Then delete the scratch graph (`GRAPH.DELETE flowtrace_val` in redis-cli).
`compare` uploads the slice into the graph and deletes its batch afterwards (unless the UI had already uploaded it).

## Data

- **Source:** IBM *Transactions for Anti Money Laundering* (HI-Small) by Altman et al. (NeurIPS 2023), [Kaggle](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml), licence CDLA-Sharing-1.0. The data in this repository (`data/samples`, `data/demo`) is derived from it and shared under the same licence: [`data/LICENSE`](../data/LICENSE), [`data/NOTICE`](../data/NOTICE). It is **synthetic**: no real customer data is used, as the hackathon rules require.
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
