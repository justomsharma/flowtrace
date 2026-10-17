"""Uploads are idempotent: payment and batch ids come from content, and the graph write merges on tx_id."""
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
