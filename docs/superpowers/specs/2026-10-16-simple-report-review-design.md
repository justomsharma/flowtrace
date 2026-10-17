# Review of feat/simple-report: decided by measurement

Written on 2026-10-16, **before** any of the measurements below were run. Nothing here changes after the
results are seen. Branch `fix/simple-report-review`, cut from `origin/feat/simple-report` (Genga's branch is not
changed).

## 1. Scoring: which variant wins

Variants (only the disposition score changes; the same investigations are re-scored under each one):

- **A** — dev scoring: `disposition.measured_weights` off. Every indicator counts at its tool weight.
- **B** — Genga's scoring as pushed: payment-level lift from `signal_rates.json` (measured on Sep 1–8),
  aliases (CYCLE = ROUND_TRIP, CASH_OR_CRYPTO_EXIT = HIGH_RISK_RAIL), "≥ 500 payments" rule, and an escalator
  with strength < 0.5 no longer escalates.
- **C** — Genga's idea, fixed:
  - a lift never switches off an escalator;
  - a lift is used only when the sign has **≥ 30 labelled laundering payments**, otherwise strength 1.0;
  - the lift is shrunk toward 1 with additive smoothing: 10 pseudo laundering payments at the base rate
    (rate = (laundering + 10) / (payments + 10 / base_rate));
  - no aliases: every code is measured on its own definition;
  - PASS_THROUGH / FAST_ONWARD_TRANSFER (and the others) measured the way the evidence defines them
    (e.g. PASS_THROUGH = a payment out of an account within 24 h of a payment in of 0.80–1.05× its size, ≥ $1,000;
    FAST_ONWARD = the receiver sends on ≥ 50% of the payment within 24 h);
  - measured on the **training days only (Sep 1–6)**, so the validation days (Sep 7–8) stay unseen.
    (B measured on Sep 1–8, which includes the validation days; it is kept as pushed.)

Data: eval slices of Sep 7 and Sep 8 built with `flowtrace.eval.slice --pos 20 --neg 20`, the same way as the
Sep 9 slice, top 40 cases of each upload. Each day is replayed on a scratch graph that holds history up to that
day only (deleted afterwards); the main graph is not touched. Policies: **fixed** and **heuristic** (no LLM).

**Decision rule** (Sep 7 and Sep 8 combined, 80 cases):

1. Escalate recall on laundering cases must be within **0.02 of A**, for each of the two policies.
2. Among the variants that pass (1), the winner escalates the **fewest legitimate cases** (summed over both
   policies).
3. Ties go to **less money wrongly frozen**: what the planned holds would freeze on legitimate cases, with the
   heuristic policy's dispositions (as `eval.holds` measures it).
4. A remaining tie goes to A (no change).

Then, and only then, the winner runs **once** on the Sep 9 slice (fixed, heuristic and llm) as the final test,
next to A on the same investigations. No changes after seeing Sep 9.

## 2. Auto-hold: default on or off

Measured on Sep 7–8 with the winning scoring and the heuristic policy (the dispositions `eval.holds` uses). An
auto-hold fires on a case when the disposition is ESCALATE at confidence ≥ `actions.auto_hold.min_confidence`
(0.9) and the planner has a hold for it.

- **Precision** = laundering cases among the cases it would auto-hold (the account-level share is reported too).
- **Money wrongly frozen** = what it would freeze on legitimate cases (total and the largest case).

**Decision rule:** default **ON** only if precision ≥ **90%** on Sep 7–8 combined **and** no legitimate case
would have more than **$1M** frozen. Otherwise (including when it fires on no case at all) default **OFF**, and
it stays available as a server setting.

## 3. Everything else (not measured)

Fixed as reviewed: auto-hold mechanics (PROVISIONAL status, needs APPROVER_TOKEN, never re-holds after a
release or an analyst's CLOSE, released unless a person confirms within 1 business day, audited as
"agent (auto-hold)"); "USD equivalent" labels with each payment's own currency; crime hints only on ESCALATE;
holds start unticked and the STR decision is separate; hold expiry shown before approval; High/Medium/Low
instead of an uncalibrated percentage; evidence IDs kept in the STR draft; clean-ups.
