# Case funnel: from a day of payments to ~15 ranked cases

Date: 2026-10-15 · Branch: `fix/case-funnel` · Target: `dev`

## Problem

Uploading the sample day (2,721 payments) gives 1,912 flagged payments and 695 cases. The largest
case has 406 accounts. Measured causes:

- On the day itself the model flags only 57 payments at score ≥ 0.3 (52 are laundering). The flood
  comes from the detectors, mostly PASS-THROUGH (outflow/inflow ratio only, no timing, no amount).
- The "daily" sample spans 8 days (69 of 124 laundering rows fall after Sep 9, into the excluded tail).
- Cases are unbounded connected components, so busy hubs glue unrelated alerts together.
- Ranking is a hand-picked `0.6·ml + 0.4·detector`.
- Re-uploading a file duplicates every edge (`CREATE`, new random batch id, `DELETE_BATCH` unused).

## Goal

- A sample day gives about 10–20 ranked cases. The top `daily_capacity` (15) are "Today's work",
  the rest "Lower priority". Nothing is hidden.
- Success is recall at capacity (as in Google Cloud AML AI): laundering attempts from
  `HI-Small_Patterns.txt` with a payment in a top-15 case, reported on validation and test.
- Thresholds are tuned on validation days (Sep 7, Sep 8) only. The test day (Sep 9) is reported once.

## Design

### 1. Calibrated model score

The LightGBM score is mapped to a probability with isotonic regression fitted on the validation
days (all payments, from `features.parquet`). The fitted curve is stored as breakpoints in
`config.yaml` (`calibration.x`, `calibration.y`) and applied with linear interpolation.

A payment is flagged by the model when its calibrated score ≥ `cases.flag_probability`.

### 2. Detectors become evidence

Every detector keeps running in Cypher over the batch window. Each detector gets a precision
measured on the validation days: the share of its hits whose transfers include labelled laundering.
These are stored in `config.yaml` (`detectors.precision.<NAME>`).

Detector hits do two things:

- They attach to the case of any account they fire on, as evidence the analyst sees.
- They can open a case on their own (to catch what the model misses). That case is scored by the
  detector's precision only, so it ranks low unless the evidence is strong.

Precision is measured with the same Cypher detectors against the full graph. Detectors only read
`ts < t1`, so the current graph (history up to Sep 9) can be used for Sep 7 and Sep 8 without leaking.
If the full day is too slow, a uniform random sample of that day's active accounts is used.
Precision is a ratio, so a random sample estimates it without bias.

### 3. Time-paired PASS-THROUGH

An account is a pass-through when an incoming payment is followed by an outgoing one:

- within `detectors.passthrough.max_gap_hours` (24) of the incoming payment;
- to a different counterparty than the sender;
- with the outgoing amount within `[ratio_low, ratio_high]` (0.8–1.05) of the incoming amount;
- where the incoming amount is at least `min_amount_usd` (1,000).

These numbers are our own choices. FATF (2023), FinCEN FIN-2014-A005 and the FCA mule reviews
describe "rapid" in/out movement but give no figures. The query follows the queries.py rules
(`WHERE a.id IN $ids`, `t.ts + 0`, incoming edges as `(a)<-[i]-(s)`).

### 4. Case grouping (suspect-centred, hub-aware, capped)

Done in Python over the flagged payments.

1. **Account risk.** Each account's risk is the highest calibrated score among its flagged payments
   combined with its detector precisions, using the formula in section 5.
2. **Hubs.** An account is a hub when its total degree in history is at or above
   `cases.hub_degree`. That value is the 99th percentile of account degree on the training days,
   computed by the tuning script, and read from FalkorDB with `DEGREES`. A hub never links two
   accounts into one case. Its flagged payments still appear in the case of the other account.
3. **Growing cases.** Take accounts in descending risk. Each account not yet assigned seeds a case
   and grows breadth-first over flagged payments between non-hub accounts that are not yet assigned.
   Growth stops at `cases.max_accounts` (30). Small groups therefore come out as whole connected
   groups, and big groups split around their riskiest accounts.

### 5. Case score and ranking

```
case_score = 1 − (1 − p_model) · Π over distinct detectors d in the case (1 − precision_d)
```

- `p_model` is the highest calibrated score of the case's payments.
- `case_score` is the chance that at least one signal is right. It assumes the signals are
  independent; that assumption is stated in the UI tooltip and the README.
- No hand-picked weights.

Cases are sorted by `case_score`. The top `cases.daily_capacity` (15) get `tier = "today"`, the
rest get `tier = "lower"`. Shape classification and ground truth stay as they are.

### 6. Idempotent uploads

- **Payment ID.** Each payment's `tx_id` is `"P" + sha1(ts, src, dst, amount_paid, payment_currency,
  amount_received, receiving_currency, payment_format, n)[:16]`. Here `n` counts identical rows
  within the file, so genuine repeats are kept.
- **Batch ID.** The batch id is `"B" + sha1(file bytes)[:6]`.
- **Writes.** `INSERT_BATCH` uses `MERGE (s)-[t:TRANSFER {tx_id: r.tx_id}]->(d) ON CREATE SET ...`.
  Re-uploading the same or an overlapping file adds no edges. `DELETE_BATCH` stays available for cleanup.

### 7. One honest sample day

- `samples.py` keeps only rows inside the sample day. An attempt that continues past midnight keeps
  only its in-day part.
- A `--day YYYY-MM-DD` option builds the same files for any day.
- Validation-day files go to `data/samples/val/`, which the API does not list.

### 8. UI

- **Headline.** The top bar shows a funnel: `2,652 payments → 57 flagged → 18 cases → 15 to review today`.
- **Case list.** It has two sections, "Today's work (15)" and "Lower priority (n)", the second in a
  quieter style.
- **Case rows.** Each row shows the case score as a percent and a short reason, e.g. "fan-out + model 92%".

### 9. Config (all new numbers live in `config.yaml`)

```yaml
calibration: {x: [...], y: [...]}          # fitted by the tuning script
detectors:
  passthrough: {max_gap_hours: 24, ratio_low: 0.8, ratio_high: 1.05, min_amount_usd: 1000}
  precision: {FAN-OUT: ..., FAN-IN: ..., PASS-THROUGH: ..., SCATTER-GATHER: ..., CYCLE: ..., GATHER-SCATTER: ...}
cases:
  flag_probability: ...                     # tuned on validation
  hub_degree: ...                           # p99 of history degree
  max_accounts: 30
  daily_capacity: 15
```

`disposition.candidate_score` and `detectors.passthrough_ratio` are removed.

### 10. Tuning script

`python -m flowtrace.eval.tune` does the following:

- fits the calibration on Sep 7–8;
- measures detector precision on Sep 7–8;
- computes the hub degree;
- prints the YAML block for `config.yaml`;
- writes a report to `data/runs/tune.json`.

`flag_probability` and the pass-through numbers are chosen by running the validation samples through
the full pipeline. Each run uses a graph loaded `--until` that day. The values chosen are those that
give 10–20 cases with the most attempts in the top 15.

## Measurement

The same script runs before and after on the Sep 9 sample. It reports:

- payments, flagged payments and cases;
- the size of the largest case (accounts and payments);
- attempts in the batch, and how many land in the top 15 and the top 50;
- laundering cases in the top 15.

Validation numbers (Sep 7, Sep 8) are reported next to the test number.

Before, on the sample: 2,721 payments → 1,912 flagged → 695 cases. The largest case has 406
accounts. 28 of 29 attempts are in the top 15, but only because case #1 swallows them.

## Tests (pytest, no database; fakegraph extended)

- Pass-through pairs only within the gap, ratio band and minimum amount, and never back to the sender.
- A hub does not merge two cases. Its payments still show in each case.
- No case exceeds `max_accounts`. An oversized group splits around its riskiest accounts.
- `case_score` follows the noisy-OR formula. Tiers follow `daily_capacity`.
- Payment ids are deterministic. Identical rows get distinct ids. `INSERT_BATCH` merges on `tx_id`.
- Sample rows all fall inside the chosen day.
- Calibration interpolation is monotone and clipped to [0, 1].

`python -m flowtrace.graph.smoke` checks the new PASS-THROUGH and INSERT queries on the live graph.

## Out of scope

- Retraining the triage model.
- The agent investigation flow, except that it reads the new case fields.
- FalkorDB `algo.WCC` and `algo.labelPropagation`. They cannot filter by edge property, so they
  would need extra writes per batch for a graph of about 100 edges.

## Risks

- Calibration and precision are measured on full days, where laundering is about 0.1%. The demo
  sample is enriched, about 2%. Case scores will read low on the sample, but the ranking is unaffected.
- Noisy-OR assumes independence. Correlated detectors (FAN-IN + GATHER-SCATTER) can inflate a score.
  GATHER-SCATTER is derived from the other two, so only its own precision counts when it fires and
  FAN-IN/FAN-OUT on the same account are not counted again.
