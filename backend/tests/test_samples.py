"""Sample files must be one honest day: no rows from before or after it."""
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
        if df.height:
            assert df["ts"].min() >= D0 and df["ts"].max() < D0 + 24 * H, name
    assert "T1" in out[f"demo_mixed_{D}.csv"]["tx_id"].to_list()
    assert "T2" not in out[f"demo_mixed_{D}.csv"]["tx_id"].to_list()
