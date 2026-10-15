from flowtrace.behaviour.recurring import DAY_MS, detect_recurrence, infer_purpose

RULES = {
    "min_occurrences": 3,
    "max_gap_cv": 0.20,
    "max_amount_cv": 0.05,
    "max_amount_cv_bill": 0.25,
    "cadences_days": {"daily": [0.75, 1.25], "weekly": [6, 8], "biweekly": [12, 16], "monthly": [26, 35]},
}


def series(start_day, period, n, amount=1000.0, jitter=None):
    jitter = jitter or [0] * n
    return [int((start_day + i * period + jitter[i]) * DAY_MS) for i in range(n)], [amount] * n


def test_monthly_fixed_amount_is_recurring():
    ts, amts = series(0, 30, 4, jitter=[0, 1, -1, 0.5])
    rec = detect_recurrence(ts, amts, RULES)
    assert rec and rec.cadence == "monthly" and rec.fixed_amount


def test_two_payments_are_not_enough():
    ts, amts = series(0, 7, 2)
    assert detect_recurrence(ts, amts, RULES) is None


def test_irregular_gaps_are_rejected():
    ts = [0, 1 * DAY_MS, 9 * DAY_MS, 10 * DAY_MS]
    assert detect_recurrence(ts, [100.0] * 4, RULES) is None


def test_variable_amount_on_schedule_is_bill_not_fixed():
    ts, _ = series(0, 7, 4)
    rec = detect_recurrence(ts, [100.0, 120.0, 90.0, 110.0], RULES)
    assert rec and rec.cadence == "weekly" and not rec.fixed_amount


def test_payroll_inference():
    ts, amts = series(0, 14, 3)
    rec = detect_recurrence(ts, amts, RULES)
    g = infer_purpose("in", "ACH", rec, "Corporation", cp_in_degree=5, cp_out_degree=40,
                      same_owner=False, self_transfer=False, hub_in_degree=200)
    assert g.purpose == "PAYROLL"


def test_emi_like_obligation():
    ts, amts = series(0, 30, 3, amount=550.0)
    rec = detect_recurrence(ts, amts, RULES)
    g = infer_purpose("out", "ACH", rec, "Corporation", cp_in_degree=900, cp_out_degree=3,
                      same_owner=False, self_transfer=False, hub_in_degree=200)
    assert g.purpose == "FIXED_OBLIGATION"


def test_no_schedule_is_unexplained():
    g = infer_purpose("out", "ACH", None, None, 1, 1, False, False, 200)
    assert g.purpose == "UNEXPLAINED" and g.confidence == 0.0
