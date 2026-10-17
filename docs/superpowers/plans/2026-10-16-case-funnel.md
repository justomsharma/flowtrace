# Case Funnel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One day of payments becomes about 10–20 ranked, explainable cases, and the top 15 are "Today's work".

**Architecture:**
- The model score is calibrated to a probability, and each detector carries its measured precision.
  Both are fitted on the validation days.
- One bar, `cases.flag_probability`, decides which signals open a case. Weaker signals only add
  evidence to a case that is already open.
- Cases grow from the riskiest accounts outward. Hubs never link two cases, and case size is capped.
  A case is scored by noisy-OR over its signals and ranked by investigator capacity.
- Uploads get content-hash ids and use `MERGE`, so they are idempotent.

**Tech Stack:** Python 3.11, polars, numpy, scikit-learn (isotonic), FalkorDB 6.0.2 Cypher,
FastAPI, React 19 + Tailwind 4.

**Spec:** `docs/superpowers/specs/2026-10-15-case-funnel-design.md`

## Global Constraints

- Every threshold lives in `config.yaml`; no numbers in code.
- Cypher follows the rules in `backend/flowtrace/graph/queries.py`:
  - account lists use `WHERE a.id IN $ids`;
  - time filters are written `t.ts + 0`;
  - incoming edges are written `(a)<-[t]-(b)`;
  - every edge variable is used.
- Tune on Sep 7–8 only, and report Sep 9 once.
- Never commit `data/models/*` or `data/processed/fx_rates.json`. No `Co-Authored-By` lines.
- After any live upload, delete that batch with `DELETE_BATCH`, or reload with `python -m flowtrace.graph.load`.
- Commands run from `backend/` with `PYTHONIOENCODING=utf-8`, using `../venv/Scripts/python`.

## Review Focus

- **Hub-to-hub flagged payment:** it must still land in a case, not vanish.
- **A payment whose two accounts sit in different capped groups:** it goes to the sender's case, and
  the receiver is listed as a counterparty.
- **Identical payments in one file:** both are kept, with different ids.
- **The same file uploaded twice:** the edge count is unchanged after the second upload.
- **A file with no flagged payments:** zero cases, and the UI shows "0 cases". No crash.

---

### Task 1: One honest sample day

**Files:**
- Modify: `backend/flowtrace/data/samples.py`
- Test: `backend/tests/test_samples.py`

**Interfaces:**
- Produces: `build(tx: pl.DataFrame, pat: pl.DataFrame, day: date) -> dict[str, pl.DataFrame]`.
  The keys are the file names. CLI: `python -m flowtrace.data.samples [--day YYYY-MM-DD] [--out DIR]`.

- [ ] **Step 1: Write the failing test.** Use a synthetic attempt that runs past midnight, and assert
  every row of every file is inside the day.

```python
from datetime import date, datetime, timezone
import polars as pl
from flowtrace.data.samples import build

D = date(2022, 9, 9)
D0 = int(datetime(2022, 9, 9, tzinfo=timezone.utc).timestamp() * 1000)
H = 3_600_000

def frames():
    rows = []
    def add(i, ts, s, d, lab):
        rows.append({"tx_id": f"T{i}", "ts": ts, "src": s, "dst": d, "amount_usd": 100.0, "amount_paid": 100.0,
                     "payment_currency": "US Dollar", "amount_received": 100.0, "receiving_currency": "US Dollar",
                     "payment_format": "ACH", "is_laundering": lab})
    add(1, D0 + 20 * H, "1:A", "1:B", 1)       # attempt starts on the day
    add(2, D0 + 30 * H, "1:B", "1:C", 1)       # ...and continues the next day
    for i in range(3, 40):
        add(i, D0 + (i % 23) * H, f"1:X{i}", f"1:Y{i}", 0)
    tx = pl.DataFrame(rows).with_columns(pl.col("is_laundering").cast(pl.Int8))
    pat = pl.DataFrame({"tx_id": ["T1", "T2"], "attempt_id": [7, 7], "typology": ["FAN-OUT"] * 2,
                        "typology_detail": [""] * 2}).with_columns(pl.col("attempt_id").cast(pl.Int32))
    return tx, pat

def test_every_sample_row_is_on_the_day():
    tx, pat = frames()
    out = build(tx, pat, D)
    assert out, "no sample files built"
    for name, df in out.items():
        assert df["ts"].min() >= D0 and df["ts"].max() < D0 + 24 * H, name
    assert "T1" in out[f"demo_mixed_{D}.csv"]["tx_id"].to_list()
```

- [ ] **Step 2: Run it.** `python -m pytest -q tests/test_samples.py` should FAIL with `ImportError: cannot import name 'build'`.

- [ ] **Step 3: Implement.** Move the body of `main()` into `build(tx, pat, day)`.
  - Clip attempt rows to the day: right after building `att_tx` and `small_tx`, add
    `.filter(pl.col("ts").is_between(d0, d1, closed="left"))`.
  - Replace `.sample(1500, seed=7)` with `.sample(min(1500, n), seed=7)`, where `n` is that frame's
    height, and do the same for `.sample(60, seed=11)`.
  - Return `{name: df}` instead of writing files.
  - `write(df, name, out_dir)` takes the output directory.
  - `main()` parses `--day` (default `2022-09-09`) and `--out` (default `data/samples`), reads the
    parquet files lazily (`pl.scan_parquet(...).filter(pl.col("ts") < d1).collect()`), calls
    `build`, and writes each file.
  - Update the module docstring: rows are clipped to the day, and validation files go to
    `data/samples/val/` with `--out`.

- [ ] **Step 4: Run.** `python -m pytest -q tests/test_samples.py` should PASS.

- [ ] **Step 5: Regenerate the samples.** Run each command from `backend/`:
  - `python -m flowtrace.data.samples`
  - `python -m flowtrace.data.samples --day 2022-09-07 --out ../data/samples/val`
  - `python -m flowtrace.data.samples --day 2022-09-08 --out ../data/samples/val`

  Check that every file only has dates on its own day.

- [ ] **Step 6: Commit.** Commit `samples.py`, the test and `data/samples/**`, with the message
  "Clip sample days to one day; add --day/--out for validation samples".

### Task 2: Idempotent uploads

**Files:**
- Modify: `backend/flowtrace/pipeline/batch.py` (`process_batch`, add `payment_ids`, `batch_id_for`)
- Modify: `backend/flowtrace/graph/queries.py` (`INSERT_BATCH`)
- Modify: `backend/flowtrace/graph/smoke.py` (add a re-insert check)
- Test: `backend/tests/test_upload.py`

**Interfaces:**
- Produces:
  - `payment_ids(batch: pl.DataFrame) -> list[str]` returns ids like `"P" + 16 hex`.
  - `batch_id_for(data: bytes) -> str` returns `"B" + 6 hex`, upper case.

- [ ] **Step 1: Write the failing tests.**

```python
import polars as pl
from flowtrace.graph import queries as Q
from flowtrace.pipeline.batch import batch_id_for, payment_ids

def rows(n_dupes=2):
    r = {"ts": 1, "src": "1:A", "dst": "1:B", "amount_paid": 10.0, "payment_currency": "US Dollar",
         "amount_received": 10.0, "receiving_currency": "US Dollar", "payment_format": "ACH"}
    return pl.DataFrame([r] * n_dupes + [{**r, "dst": "1:C"}])

def test_ids_are_deterministic_and_repeats_differ():
    a, b = payment_ids(rows()), payment_ids(rows())
    assert a == b
    assert len(set(a)) == 3 and all(x.startswith("P") and len(x) == 17 for x in a)

def test_batch_id_depends_only_on_bytes():
    assert batch_id_for(b"x,y\n1,2\n") == batch_id_for(b"x,y\n1,2\n") != batch_id_for(b"x,y\n1,3\n")

def test_insert_merges_on_tx_id():
    assert "MERGE (s)-[t:TRANSFER {tx_id: r.tx_id}]->(d)" in Q.INSERT_BATCH
    assert "CREATE (s)-[" not in Q.INSERT_BATCH
```

- [ ] **Step 2: Run.** It should FAIL on the import.

- [ ] **Step 3: Implement.** In `batch.py`:

```python
ID_COLS = ["ts", "src", "dst", "amount_paid", "payment_currency", "amount_received", "receiving_currency",
           "payment_format"]


def payment_ids(batch: pl.DataFrame) -> list[str]:
    """Stable id from the payment's own fields; a counter keeps genuine repeats in one file apart."""
    seen: dict[str, int] = {}
    out = []
    for row in batch.select(ID_COLS).iter_rows():
        key = "|".join(map(str, row))
        n = seen.get(key, 0)
        seen[key] = n + 1
        out.append("P" + hashlib.sha1(f"{key}|{n}".encode()).hexdigest()[:16])
    return out


def batch_id_for(data: bytes) -> str:
    return "B" + hashlib.sha1(data).hexdigest()[:6].upper()
```

  In `process_batch`, use `batch_id = batch_id_for(data)`, and replace the `tx_id` prefixing with
  `batch = batch.with_columns(pl.Series("tx_id", payment_ids(batch)))`. In `queries.py`:

```python
# Payments are merged on their content-derived tx_id, so re-uploading a file (or an overlapping one)
# adds nothing. The edge keeps the batch id of the upload that first wrote it.
INSERT_BATCH = """
UNWIND $rows AS r
MERGE (s:Account {id: r.src})
MERGE (d:Account {id: r.dst})
MERGE (s)-[t:TRANSFER {tx_id: r.tx_id}]->(d)
ON CREATE SET t.ts = r.ts, t.amount = r.amount, t.fmt = r.fmt, t.cur = r.cur, t.xcur = r.xcur, t.batch = $batch
"""
```

- [ ] **Step 4: Run.** `python -m pytest -q tests/test_upload.py` should PASS.

- [ ] **Step 5: Add a smoke check.** In `smoke.py` after the queries loop, insert two fake rows
  between the probe accounts with batch `"SMOKE"`, insert them again, and count with
  `MATCH ()-[t:TRANSFER {batch: 'SMOKE'}]->() RETURN count(t) AS n` (expect 2). Then `DELETE_BATCH`
  for `"SMOKE"` and fail if the count is not 2. Use `rw` from `flowtrace.graph.client`.

- [ ] **Step 6: Run the smoke test.** `python -m flowtrace.graph.smoke` should print "all queries passed".

- [ ] **Step 7: Commit.** Message: "Make uploads idempotent: content-hash payment and batch ids, MERGE on tx_id".

### Task 3: Time-paired PASS-THROUGH

**Files:**
- Modify: `backend/flowtrace/detectors/alerts.py` (add `passthrough_pairs`, replace the `PASS_THROUGH` query and its call)
- Modify: `backend/flowtrace/agent/tools.py:127-131` (use `passthrough_pairs`)
- Modify: `config.yaml` (replace `passthrough_ratio` with a `passthrough:` block)
- Modify: `backend/flowtrace/graph/smoke.py` (new params)
- Modify: `backend/tests/fakegraph.py` (answer `A.PASS_THROUGH`)
- Test: `backend/tests/test_passthrough.py`

**Interfaces:**
- Produces: `passthrough_pairs(inflows: list[dict], outflows: list[dict], p: dict) -> list[tuple[dict, dict]]`.
  - Each dict has `tx_id`, `cp`, `ts` and `amount`.
  - `p` is `rules["detectors"]["passthrough"]`, with keys `max_gap_hours`, `ratio_low`,
    `ratio_high` and `min_amount_usd`.

- [ ] **Step 1: Write the failing tests.**

```python
from flowtrace.detectors.alerts import passthrough_pairs

H = 3_600_000
P = {"max_gap_hours": 24, "ratio_low": 0.8, "ratio_high": 1.05, "min_amount_usd": 1000}

def tx(i, cp, ts, amt):
    return {"tx_id": f"T{i}", "cp": cp, "ts": ts, "amount": amt}

def test_pairs_money_that_leaves_soon_in_similar_amount():
    pairs = passthrough_pairs([tx(1, "S", 0, 5000)], [tx(2, "D", 3 * H, 4900)], P)
    assert [(i["tx_id"], o["tx_id"]) for i, o in pairs] == [("T1", "T2")]

def test_rejects_late_small_dissimilar_or_back_to_sender():
    ins = [tx(1, "S", 0, 5000), tx(9, "S2", 0, 500)]
    outs = [tx(2, "D", 30 * H, 5000),    # too late
            tx(3, "D", H, 2000),          # amount not similar
            tx(4, "S", H, 5000),          # back to the sender
            tx(5, "D", -H, 5000),         # before the money arrived
            tx(6, "D", H, 480)]           # pairs only with the 500 inflow, which is below the minimum
    assert passthrough_pairs(ins, outs, P) == []
```

- [ ] **Step 2: Run.** It should FAIL on the import.

- [ ] **Step 3: Implement** in `alerts.py`:

```python
def passthrough_pairs(inflows: list[dict], outflows: list[dict], p: dict) -> list[tuple[dict, dict]]:
    """Money in, then a similar amount out to someone else within max_gap_hours (FATF 2023: 'rapid or
    immediate ... transfers ... following the receipt of a funds transfer'). Mirrors the PASS_THROUGH query."""
    gap = p["max_gap_hours"] * HOUR_MS
    return [(i, o) for i in inflows if i["amount"] >= p["min_amount_usd"] for o in outflows
            if i["ts"] <= o["ts"] <= i["ts"] + gap and o["cp"] != i["cp"]
            and p["ratio_low"] * i["amount"] <= o["amount"] <= p["ratio_high"] * i["amount"]]
```

```python
# Time-paired pass-through: each incoming payment is matched with outgoing ones that leave within
# $gap in a similar amount, to someone other than the sender. Outflows must fall in the batch day.
PASS_THROUGH = """
MATCH (a:Account) WHERE a.id IN $ids
MATCH (a)<-[i:TRANSFER]-(s)
WHERE i.ts + 0 >= $t0 AND i.ts + 0 < $t1 AND s.id <> a.id AND i.amount >= $min_amount
MATCH (a)-[o:TRANSFER]->(d:Account)
WHERE o.ts + 0 >= i.ts AND o.ts + 0 <= i.ts + $gap AND o.ts + 0 >= $day0 AND o.ts + 0 < $t1
  AND d.id <> a.id AND d.id <> s.id AND o.amount >= i.amount * $lo AND o.amount <= i.amount * $hi
WITH a, collect(DISTINCT i.tx_id) AS in_tx, collect(DISTINCT o.tx_id) AS out_tx, count(o) AS pairs
RETURN a.id AS account, pairs, size(in_tx) AS n_in, size(out_tx) AS n_out, (in_tx + out_tx)[0..60] AS tx_ids
"""
```

  The call in `run_detectors`:

```python
    pt = d["passthrough"]
    for r in q("PASS-THROUGH", PASS_THROUGH, {"ids": ids, "t0": window_t0, "t1": t1, "day0": t0,
                                              "gap": pt["max_gap_hours"] * HOUR_MS, "lo": pt["ratio_low"],
                                              "hi": pt["ratio_high"], "min_amount": pt["min_amount_usd"]}):
        hits.append({"detector": "PASS-THROUGH", "account": r["account"], "strength": 1.0,
                     "detail": f"sent on {r['n_in']} incoming payments within {pt['max_gap_hours']}h "
                               f"in similar amounts ({r['n_out']} outgoing)", "tx_ids": list(r["tx_ids"])})
```

  In `config.yaml`, replace `passthrough_ratio: 0.8` with:

```yaml
  passthrough:               # money in, similar amount out within the gap (FATF 2023, FinCEN FIN-2014-A005)
    max_gap_hours: 24
    ratio_low: 0.80          # outgoing amount / incoming amount
    ratio_high: 1.05
    min_amount_usd: 1000
```

  In `tools.py`, replace the ratio block:

```python
    pt = rules["detectors"]["passthrough"]
    pairs = passthrough_pairs([{**r, "cp": r["src"]} for r in in_rows], [{**r, "cp": r["dst"]} for r in out_rows], pt)
    ratio = out_sum / in_sum if in_sum else 0.0
    if pairs:
        fwd = {o["tx_id"]: o for _, o in pairs}
        ctx.book.add(kind="support", code="PASS_THROUGH", tool="account_profile", step=ctx.step,
                     claim=(f"Money did not stay: {_a(acc)} sent on {_usd(sum(o['amount'] for o in fwd.values()))} "
                            f"within {pt['max_gap_hours']} hours of receiving it, in similar amounts."),
                     weight=0.45, tx_ids=[t["tx_id"] for pr in pairs for t in pr][:30], accounts=[acc],
                     metrics={"pairs": len(pairs), "ratio": round(ratio, 3)})
```

  In `fakegraph.answer`, before the final `raise`:

```python
        if c == A.PASS_THROUGH:
            rule = {"max_gap_hours": p["gap"] / 3_600_000, "ratio_low": p["lo"], "ratio_high": p["hi"],
                    "min_amount_usd": p["min_amount"]}
            rows = []
            for a in p["ids"]:
                ins = [{**e, "cp": e["src"]} for e in self.inc(a, p["t0"], p["t1"]) if e["src"] != a]
                outs = [{**e, "cp": e["dst"]} for e in self.out(a, p["day0"], p["t1"]) if e["dst"] != a]
                pairs = passthrough_pairs(ins, outs, rule)
                if pairs:
                    i_tx = list(dict.fromkeys(i["tx_id"] for i, _ in pairs))
                    o_tx = list(dict.fromkeys(o["tx_id"] for _, o in pairs))
                    rows.append({"account": a, "pairs": len(pairs), "n_in": len(i_tx), "n_out": len(o_tx),
                                 "tx_ids": i_tx + o_tx})
            return rows
```

  In `smoke.py`, the detector params become
  `"PASS_THROUGH": {"ids": [acc, cp], "t0": t0, "t1": t1, "day0": t0, "gap": DAY, "lo": 0.0, "hi": 10.0, "min_amount": 0}`.

- [ ] **Step 4: Run the tests.** `python -m pytest -q tests` should pass. That includes `test_agent.py`
  (the mule fan-out world still escalates). If it no longer escalates, check its amounts against the
  ratio band before changing anything.

- [ ] **Step 5: Run the smoke test.** `python -m flowtrace.graph.smoke` should pass, and PASS_THROUGH should take under 50 ms.

- [ ] **Step 6: Commit.** Message: "Time-paired PASS-THROUGH detector (in -> similar out within 24h, min amount)".

### Task 4: Calibrated model score

**Files:**
- Create: `backend/flowtrace/ml/calibration.py`
- Test: `backend/tests/test_calibration.py`

**Interfaces:**
- Produces:
  - `calibrate(p: np.ndarray, rules: dict) -> np.ndarray` maps raw scores to probabilities.
  - `fit(p: np.ndarray, y: np.ndarray) -> dict` returns `{"x": [...], "y": [...]}` for `config.yaml`.

- [ ] **Step 1: Write the failing tests.**

```python
import numpy as np
from flowtrace.ml.calibration import calibrate, fit

def test_fit_is_monotone_and_maps_into_unit_interval():
    rng = np.random.default_rng(0)
    p = rng.random(5000)
    y = (rng.random(5000) < p ** 3).astype(int)
    c = fit(p, y)
    assert c["x"] == sorted(c["x"]) and c["y"] == sorted(c["y"])
    out = calibrate(np.array([-1.0, 0.0, 0.5, 1.0, 2.0]), {"calibration": c})
    assert ((out >= 0) & (out <= 1)).all() and (np.diff(out) >= 0).all()
```

- [ ] **Step 2: Run.** It should FAIL on the import.

- [ ] **Step 3: Implement.**

```python
"""Map the triage model's raw score to a probability of laundering (isotonic, fitted on validation days).

The curve is stored as breakpoints in config.yaml (`calibration.x`, `calibration.y`) by
`python -m flowtrace.eval.tune`, so the app needs nothing beyond the config to apply it.
"""
from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression

GRID = np.r_[np.linspace(0, 0.1, 11), np.linspace(0.15, 0.9, 16), np.linspace(0.92, 1.0, 5)]


def fit(p: np.ndarray, y: np.ndarray) -> dict:
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(p, y)
    ys = np.maximum.accumulate(iso.predict(GRID))
    return {"x": [round(float(v), 4) for v in GRID], "y": [round(float(v), 5) for v in ys]}


def calibrate(p: np.ndarray, rules: dict) -> np.ndarray:
    c = rules["calibration"]
    return np.clip(np.interp(np.asarray(p, dtype=float), c["x"], c["y"]), 0.0, 1.0)
```

- [ ] **Step 4: Run.** It should PASS.
- [ ] **Step 5: Commit.** Message: "Add isotonic score calibration stored in config".

### Task 5: Case building (flag, group, score, tier)

**Files:**
- Create: `backend/flowtrace/pipeline/cases.py`
- Test: `backend/tests/test_cases.py`

**Interfaces:**
- Consumes:
  - from Task 4: calibrated scores `p_cal` per batch payment;
  - from Task 3: hits `{detector, account, strength, detail, tx_ids}`.
- Produces:
  - `combine(probs) -> float`: noisy-OR.
  - `effective_hits(hits) -> list[dict]`: drops FAN-IN/FAN-OUT where GATHER-SCATTER fired on the same account.
  - `flag(tx: list[dict], hits, rules) -> list[dict]`: each tx dict has `tx_id, src, dst, ts, amount_usd,
    payment_format, p, p_cal, is_laundering`.
  - `group(flagged, risk: dict[str, float], hubs: set[str], max_accounts: int) -> list[dict]`.
    Each group is `{"members": set, "tx": list}`.
  - `build_cases(tx, hits, batch_id, rules, degrees: dict[str, int]) -> list[dict]`. Each case has
    `case_id, batch_id, primary, accounts, tx_ids, edges, t0, t1, ml_score, case_score, tier, signals,
    detector_hits, shape, amount, ground_truth`.

Rules used (Task 7 fills in the real values):
- `cases.flag_probability`, `cases.max_accounts`, `cases.daily_capacity`, `cases.hub_degree`;
- `detectors.precision.<NAME>`.

- [ ] **Step 1: Write the failing tests.**

```python
from flowtrace.pipeline.cases import build_cases, combine, effective_hits, group

RULES = {"cases": {"flag_probability": 0.3, "max_accounts": 4, "daily_capacity": 2, "hub_degree": 100},
         "detectors": {"precision": {"FAN-OUT": 0.1, "FAN-IN": 0.1, "GATHER-SCATTER": 0.5, "PASS-THROUGH": 0.05,
                                      "SCATTER-GATHER": 0.4, "CYCLE": 0.6}}}

def t(i, s, d, p_cal, lab=0):
    return {"tx_id": f"P{i}", "src": s, "dst": d, "ts": i, "amount_usd": 100.0, "payment_format": "ACH",
            "p": p_cal, "p_cal": p_cal, "is_laundering": lab}

def test_noisy_or():
    assert combine([]) == 0.0
    assert abs(combine([0.5, 0.5]) - 0.75) < 1e-9

def test_gather_scatter_replaces_its_parts():
    hits = [{"detector": d, "account": "A", "tx_ids": []} for d in ("FAN-IN", "FAN-OUT", "GATHER-SCATTER")]
    assert [h["detector"] for h in effective_hits(hits)] == ["GATHER-SCATTER"]

def test_hub_does_not_merge_cases_but_keeps_its_payments():
    tx = [t(1, "A", "HUB", 0.9), t(2, "B", "HUB", 0.8)]
    cases = build_cases(tx, [], "B1", RULES, degrees={"HUB": 500, "A": 3, "B": 3})
    assert len(cases) == 2
    assert all("HUB" in c["accounts"] and len(c["tx_ids"]) == 1 for c in cases)

def test_hub_to_hub_payment_still_lands_in_a_case():
    cases = build_cases([t(1, "H1", "H2", 0.95)], [], "B1", RULES, degrees={"H1": 500, "H2": 500})
    assert len(cases) == 1 and cases[0]["tx_ids"] == ["P1"]

def test_case_size_is_capped_and_split_around_riskiest_accounts():
    chain = [t(i, f"A{i}", f"A{i+1}", 0.9 - i * 0.01) for i in range(10)]
    cases = build_cases(chain, [], "B1", RULES, degrees={})
    assert all(len(c["accounts"]) <= RULES["cases"]["max_accounts"] + 1 for c in cases)  # +1: counterparty across the cut
    assert sorted(x for c in cases for x in c["tx_ids"]) == sorted(x["tx_id"] for x in chain)

def test_weak_detector_adds_evidence_but_does_not_open_a_case():
    tx = [t(1, "A", "B", 0.05), t(2, "C", "D", 0.9)]
    hits = [{"detector": "PASS-THROUGH", "account": "A", "strength": 1.0, "detail": "", "tx_ids": ["P1"]},
            {"detector": "PASS-THROUGH", "account": "C", "strength": 1.0, "detail": "", "tx_ids": ["P2"]}]
    cases = build_cases(tx, hits, "B1", RULES, degrees={})
    assert [c["tx_ids"] for c in cases] == [["P2"]]
    assert abs(cases[0]["case_score"] - combine([0.9, 0.05])) < 1e-6

def test_strong_detector_opens_a_case_alone():
    tx = [t(1, "A", "B", 0.01), t(2, "B", "A", 0.01)]
    hits = [{"detector": "CYCLE", "account": "A", "strength": 0.9, "detail": "", "tx_ids": ["P1", "P2"]}]
    cases = build_cases(tx, hits, "B1", RULES, degrees={})
    assert len(cases) == 1 and abs(cases[0]["case_score"] - combine([0.01, 0.6])) < 1e-6

def test_tiers_follow_capacity_and_order():
    tx = [t(1, "A", "B", 0.9), t(2, "C", "D", 0.7), t(3, "E", "F", 0.5)]
    cases = build_cases(tx, [], "B1", RULES, degrees={})
    assert [c["tier"] for c in cases] == ["today", "today", "lower"]
    assert [c["case_id"] for c in cases] == ["B1-C001", "B1-C002", "B1-C003"]

def test_no_flags_no_cases():
    assert build_cases([t(1, "A", "B", 0.01)], [], "B1", RULES, degrees={}) == []
```

- [ ] **Step 2: Run.** It should FAIL on the import.

- [ ] **Step 3: Implement `cases.py`.**

```python
"""Turn flagged payments into ranked cases an analyst can work through.

  flag     a payment opens a case if its calibrated model probability, or the measured precision of a
           detector that fired on it, reaches cases.flag_probability. Weaker detector hits only add
           evidence to cases that are already open.
  group    cases grow outward from the riskiest accounts over flagged payments. Hubs (history degree
           >= cases.hub_degree: merchants, payroll, exchanges) never link two cases; their payments stay
           in the case of the other account. Growth stops at cases.max_accounts.
  score    case_score = 1 - (1 - best model probability) * prod(1 - detector precision), over distinct
           detectors in the case: the chance at least one signal is right (assumes independence).
  tier     the top cases.daily_capacity cases are "today", the rest "lower" (nothing is hidden).
"""
from __future__ import annotations

from collections import deque

from flowtrace.detectors.typology import classify


def combine(probs) -> float:
    q = 1.0
    for p in probs:
        q *= 1.0 - p
    return 1.0 - q


def effective_hits(hits: list[dict]) -> list[dict]:
    gs = {h["account"] for h in hits if h["detector"] == "GATHER-SCATTER"}
    return [h for h in hits if not (h["detector"] in ("FAN-IN", "FAN-OUT") and h["account"] in gs)]


def flag(tx: list[dict], hits: list[dict], rules: dict) -> list[dict]:
    bar = rules["cases"]["flag_probability"]
    prec = rules["detectors"]["precision"]
    by_id = {t["tx_id"]: t for t in tx}
    strong = {tid for h in hits if prec.get(h["detector"], 0.0) >= bar for tid in h["tx_ids"] if tid in by_id}
    return [t for t in tx if t["p_cal"] >= bar or t["tx_id"] in strong]


def group(flagged: list[dict], risk: dict[str, float], hubs: set[str], max_accounts: int) -> list[dict]:
    rank = lambda a: (-risk.get(a, 0.0), a)  # noqa: E731
    adj: dict[str, set[str]] = {}
    for t in flagged:
        a, b = t["src"], t["dst"]
        for x in (a, b):
            if x not in hubs:
                adj.setdefault(x, set())
        if a not in hubs and b not in hubs and a != b:
            adj[a].add(b)
            adj[b].add(a)
    owner: dict[str, int] = {}
    groups: list[dict] = []
    for seed in sorted(adj, key=rank):
        if seed in owner:
            continue
        g = {"members": {seed}, "tx": []}
        owner[seed] = len(groups)
        queue = deque([seed])
        while queue and len(g["members"]) < max_accounts:
            for n in sorted(adj[queue.popleft()], key=rank):
                if n not in owner and len(g["members"]) < max_accounts:
                    g["members"].add(n)
                    owner[n] = len(groups)
                    queue.append(n)
        groups.append(g)
    for t in flagged:
        i = owner.get(t["src"], owner.get(t["dst"]))
        if i is None:  # hub to hub: its own case, keyed by the sender
            owner[t["src"]] = i = len(groups)
            groups.append({"members": {t["src"]}, "tx": []})
        groups[i]["tx"].append(t)
    return [g for g in groups if g["tx"]]


def build_cases(tx: list[dict], hits: list[dict], batch_id: str, rules: dict, degrees: dict[str, int]) -> list[dict]:
    c = rules["cases"]
    prec = rules["detectors"]["precision"]
    hits = effective_hits(hits)
    flagged = flag(tx, hits, rules)
    hubs = {a for a, d in degrees.items() if d >= c["hub_degree"]}
    hits_by_acc: dict[str, list[dict]] = {}
    for h in hits:
        hits_by_acc.setdefault(h["account"], []).append(h)
    best: dict[str, float] = {}
    for t in flagged:
        for a in (t["src"], t["dst"]):
            best[a] = max(best.get(a, 0.0), t["p_cal"])
    risk = {a: combine([p] + [prec.get(h["detector"], 0.0) for h in hits_by_acc.get(a, [])]) for a, p in best.items()}

    cases = []
    for g in group(flagged, risk, hubs, c["max_accounts"]):
        ctx = g["tx"]
        accounts = sorted({a for t in ctx for a in (t["src"], t["dst"])} | g["members"], key=lambda a: -risk.get(a, 0.0))
        dhits = [{k: h[k] for k in ("detector", "account", "strength", "detail")}
                 for a in accounts if a not in hubs for h in hits_by_acc.get(a, [])]
        p_model = max(t["p_cal"] for t in ctx)
        det = {h["detector"]: prec.get(h["detector"], 0.0) for h in dhits}
        signals = sorted([{"signal": "model", "p": round(p_model, 4)}] +
                         [{"signal": d, "p": round(p, 4)} for d, p in det.items()], key=lambda s: -s["p"])
        edges = [{"tx_id": t["tx_id"], "src": t["src"], "dst": t["dst"], "ts": t["ts"], "amount": t["amount_usd"],
                  "fmt": t["payment_format"], "p": round(float(t["p"]), 4)} for t in ctx]
        shape = classify(edges)
        labels = [t["is_laundering"] for t in ctx if t["is_laundering"] is not None]
        cases.append({
            "case_id": "", "batch_id": batch_id, "primary": accounts[0], "accounts": accounts,
            "tx_ids": [t["tx_id"] for t in ctx], "edges": edges,
            "t0": min(t["ts"] for t in ctx), "t1": max(t["ts"] for t in ctx) + 1,
            "ml_score": round(float(max(t["p"] for t in ctx)), 4),
            "case_score": round(combine([p_model] + list(det.values())), 4), "signals": signals,
            "detector_hits": dhits,
            "shape": {"typology": shape.typology, "confidence": shape.confidence, "why": shape.why, "stats": shape.stats},
            "amount": round(sum(t["amount_usd"] for t in ctx), 2),
            # Ground truth is kept for evaluation only and never passed to the agent.
            "ground_truth": (int(max(labels)) if labels else None),
        })
    cases.sort(key=lambda x: (-x["case_score"], -x["amount"]))
    for i, x in enumerate(cases, 1):
        x["case_id"] = f"{batch_id}-C{i:03d}"
        x["tier"] = "today" if i <= c["daily_capacity"] else "lower"
    return cases
```

- [ ] **Step 4: Run.** `python -m pytest -q tests/test_cases.py` should PASS. Fix the code, not the tests,
  unless a test contradicts the spec.
- [ ] **Step 5: Commit.** Message: "Suspect-centred case building: hub-aware, size-capped, noisy-OR score, capacity tiers".

### Task 6: Wire the pipeline and API

**Files:**
- Modify: `backend/flowtrace/pipeline/batch.py` (score → calibrate, flag/cases from `cases.py`, degrees, summary)
- Modify: `config.yaml` (add `cases:`, `calibration:`, `detectors.precision:`; remove `disposition.candidate_score`)
- Modify: `frontend/src/lib/api.ts` (`CaseBrief`, `BatchSummary` types)

**Interfaces:**
- Consumes: `calibrate` (Task 4) and `build_cases` (Task 5).
- Produces the summary fields:
  - `flagged_tx`: payments in cases;
  - `capacity`;
  - `cases[]`, each with `case_score`, `tier` and `signals`. `priority` is removed.

- [ ] **Step 1: Add placeholder-free starter config.** These values are replaced by Task 7's tuning
  output before any measurement.

```yaml
# --- Cases (Stage B) -------------------------------------------------------------------------
cases:
  flag_probability: 0.30    # a signal opens a case at this probability (tuned on Sep 7-8)
  hub_degree: 500           # history degree at which an account is a hub (p99, computed by eval.tune)
  max_accounts: 30          # largest case one analyst reviews; bigger groups split around their riskiest accounts
  daily_capacity: 15        # cases the team works per day -> "Today's work"
calibration: {x: [0.0, 1.0], y: [0.0, 1.0]}   # replaced by eval.tune
```

  Under `detectors:`, add `precision: {FAN-OUT: 0.0, FAN-IN: 0.0, GATHER-SCATTER: 0.0, PASS-THROUGH: 0.0, SCATTER-GATHER: 0.0, CYCLE: 0.0}`.
  Delete `candidate_score` from `disposition:`.

- [ ] **Step 2: Wire `batch.py`.**
  - Delete the old `build_cases`.
  - After `p = score(batch)`, add `p_cal = calibrate(p, rules)`.
  - The score stage reports `above_bar = int((p_cal >= rules["cases"]["flag_probability"]).sum())`.
    The `scores` event lists payments with `p_cal >= bar` and sends `p_cal` as `p`.
  - After detection:

```python
    tx = batch.with_columns(pl.Series("p", p), pl.Series("p_cal", p_cal)).to_dicts()
    flagged_accts = list({a for t in flag(tx, effective_hits(hits), rules) for a in (t["src"], t["dst"])})
    degrees = {r["id"]: r["out_deg"] + r["in_deg"] for r in ro(Q.DEGREES, {"ids": flagged_accts}).rows} if flagged_accts else {}
    cases = build_cases(tx, hits, batch_id, rules, degrees)
```

  Imports: `from flowtrace.graph.client import ro, rw`, `from flowtrace.ml.calibration import calibrate`,
  `from flowtrace.pipeline.cases import build_cases, effective_hits, flag`. Drop `networkx` and `classify`
  if they are now unused.

  Summary: `"flagged_tx": len({t for c in cases for t in c["tx_ids"]})`, `"capacity": rules["cases"]["daily_capacity"]`,
  and the case keys `("case_id", "primary", "accounts", "ml_score", "case_score", "tier", "signals", "shape", "amount", "detector_hits", "ground_truth")`.

  Update the module docstring, step 5: "build cases: suspect-centred, hub-aware, capped, ranked by capacity".

- [ ] **Step 3: Update `api.ts`.** In `CaseBrief`, replace `priority: number` with `case_score: number`,
  `tier: 'today' | 'lower'` and `signals: { signal: string; p: number }[]`. In `BatchSummary`, add `capacity: number`.

- [ ] **Step 4: Run.** `python -m pytest -q tests` should pass, and `grep -rn "candidate_score\|priority" backend/flowtrace frontend/src` should find nothing.
  The agent's `disposition` still uses `ml_score`, which is unchanged.
- [ ] **Step 5: Commit.** Message: "Wire calibrated scores and new case builder into the batch pipeline".

### Task 7: Tune on validation days

**Files:**
- Create: `backend/flowtrace/eval/tune.py`
- Modify: `config.yaml` (paste the tuned values)
- Copy: the measurement script from the session scratchpad to `backend/flowtrace/eval/funnel.py`.
  It is the same before/after measure, run as `python -m flowtrace.eval.funnel <csv>`.

**Interfaces:**
- Consumes: `fit` (Task 4), `run_detectors` (Task 3) and `process_batch` (Task 6).

- [ ] **Step 1: Write `tune.py`.** It does four things:
  1. **Calibration:** read `features.parquet` for val days (`day` in Sep 7–8), predict with
     `load_model()` exactly as `train.to_xy` does, then call `fit(p, y)`.
  2. **Hub degree:** compute the 99th percentile of per-account total degree (out + in, no
     self-transfers) on the train days (Sep 1–6), using `pl.scan_parquet`.
  3. **Detector precision:** for each val day `d`, list the accounts active that day and take a random
     sample of `--accounts` (default 20,000, seed 0). Call
     `run_detectors(ids, d0, d1, rules, cycle_accounts=ids[:2000])` in chunks of 2,000 ids.
     - A hit counts as true if any of its `tx_ids` has `is_laundering == 1` in `transactions.parquet`.
     - Precision is true hits / all hits per detector, pooled over both days.
     - If a detector has fewer than 20 hits, report it and use 0.0.
     - The current graph (history < Sep 9) is correct here, because the detectors only read `ts < d1`.
  4. **Output:** print a ready-to-paste YAML block (`calibration`, `detectors.precision`,
     `cases.hub_degree`) and write `data/runs/tune.json` with the counts behind every number.

- [ ] **Step 2: Run it** and paste the values into `config.yaml`. Command:
  `PYTHONIOENCODING=utf-8 python -m flowtrace.eval.tune`. It is long-running, so run it in the background.

- [ ] **Step 3: Tune `flag_probability` on the validation samples.**
  - For each day `D` in Sep 7 and Sep 8:
    1. `python -m flowtrace.graph.load --until D` (history strictly before `D`);
    2. `python -m flowtrace.eval.funnel ../data/samples/val/demo_mixed_D.csv --bars 0.1,0.2,0.3,0.4,0.5`
       (the script re-runs only `build_cases` per bar on one pipeline run);
    3. delete the batch.
  - Pick the bar that gives 10–20 cases on both days with the most attempts in the top 15, and the
    higher bar on ties. Record the table in `data/runs/tune.json`.

- [ ] **Step 4: Reload for test.** `python -m flowtrace.graph.load --until 2022-09-09 --replace`, then
  run `python -m flowtrace.graph.smoke`.

- [ ] **Step 5: Commit.** Commit `tune.py`, `funnel.py` and `config.yaml`, with the message
  "Tune calibration, detector precision, hub degree and flag bar on Sep 7-8".

### Task 8: Analyst UI: funnel headline and capacity tiers

**Files:**
- Modify: `frontend/src/pages/Workspace.tsx` (top strip, case list)
- Modify: `frontend/src/lib/plain.ts` (`signalName`)

- [ ] **Step 1:** Invoke the `frontend-design:frontend-design` skill and follow it. The brief:
  - The analyst should see, at a glance, how much was narrowed down and what to work on first.
  - Keep the existing dark theme tokens (`--ink`, `--muted`, `--s2`, `--border`).
  - No new dependencies.

- [ ] **Step 2: Top strip.** Replace the four `Num` tiles with one funnel line, with each number labelled underneath:
  `2,652 payments → 57 flagged → 18 cases → 15 today`. "Today" uses `min(capacity, cases.length)`.
  Keep the "New file" link.

- [ ] **Step 3: Case list.** Show two sections:
  - **Header "Today's work".** The count, plus a one-line note: "Top {capacity} by chance of laundering".
  - **Header "Lower priority".** Collapsed by default, with a "Show n more" toggle; it is not hidden,
    just folded. Rows use the muted ink.

  Each row shows:
  - the rank;
  - `caseLine(c)`;
  - the reason, from the top two `signals`, e.g. `model 92% · fan-out 12%`, via `signalName`;
  - on the right, the case score as a percent. If an investigation result exists, show the action
    badge instead.

- [ ] **Step 4: Check the build.** `npm run build` should succeed, and `npm run lint` should be clean.
- [ ] **Step 5: Commit.** Message: "Analyst view: funnel headline and Today's work / Lower priority".

### Task 9: Measure after, end-to-end run, review

- [ ] **Step 1: Measure the test day.**
  `python -m flowtrace.eval.funnel ../data/samples/demo_mixed_2022-09-09.csv` on the graph loaded to
  Sep 9. Build the before/after table from:
  - payments, flagged and cases;
  - the largest case (accounts / payments);
  - attempts in the top 15 and the top 50;
  - laundering cases in the top 15.

  Add the validation-day numbers.
- [ ] **Step 2: Run the checks.** Run `python -m pytest -q tests` and `python -m flowtrace.graph.smoke`;
  both should pass.
- [ ] **Step 3: End-to-end run.**
  - Start the backend: `python -m uvicorn flowtrace.api.main:app --port 8000`.
  - Start the frontend: `npm run dev`.
  - Upload the sample twice. The second upload must give the same batch id and the same graph edge count.
  - Check the headline and the tiers in the browser.
  - Delete the batch afterwards.
- [ ] **Step 4:** Invoke `superpowers:requesting-code-review` on the branch, then fix any confirmed findings.
- [ ] **Step 5: Final report.** Write the README "Case funnel" section (the config keys and what each
  means). Ask the user before pushing or opening the PR, which goes into `dev` with Genga28 as reviewer.
