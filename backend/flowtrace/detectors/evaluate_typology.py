"""Measure shape-classification accuracy against the ground-truth patterns file.

For every laundering attempt, classify the subgraph formed by its transactions and compare with
the labelled typology. This measures the classifier given the right transactions; the end-to-end
pipeline (which must also *find* those transactions) is measured separately.

Run:  python -m flowtrace.detectors.evaluate_typology
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

import polars as pl

from flowtrace.detectors.typology import TYPOLOGIES, classify
from flowtrace.settings import settings


def main() -> None:
    s = settings()
    pat = pl.read_parquet(s.processed_dir / "patterns.parquet")
    tx = pl.read_parquet(s.processed_dir / "transactions.parquet", columns=["tx_id", "src", "dst", "ts", "amount_usd"])
    rows = pat.join(tx, on="tx_id")

    confusion: dict[str, Counter] = defaultdict(Counter)
    correct = 0
    big, big_correct = 0, 0
    attempts = rows.partition_by("attempt_id", as_dict=False)
    for a in attempts:
        truth = a["typology"][0]
        pred = classify(a.select("src", "dst", "ts", "amount_usd").to_dicts()).typology
        confusion[truth][pred] += 1
        correct += pred == truth
        if pl.concat([a["src"], a["dst"]]).n_unique() >= 3:
            big += 1
            big_correct += pred == truth

    per_class = {
        t: {"attempts": sum(confusion[t].values()),
            "recall": confusion[t][t] / max(sum(confusion[t].values()), 1)}
        for t in TYPOLOGIES
    }
    out = {
        "attempts": len(attempts),
        "accuracy": correct / len(attempts),
        "attempts_3plus_accounts": big,
        "accuracy_3plus_accounts": big_correct / big,
        "per_class": per_class,
        "confusion": {t: dict(confusion[t]) for t in TYPOLOGIES},
    }
    (s.models_dir / "typology_metrics.json").write_text(json.dumps(out, indent=2))
    print(f"shape accuracy {out['accuracy']:.3f} on {len(attempts)} attempts; "
          f"{out['accuracy_3plus_accounts']:.3f} on the {big} attempts with 3+ accounts")
    for t, m in per_class.items():
        print(f"  {t:15s} {m['recall']:.2f}  ({m['attempts']})  -> {dict(confusion[t])}")


if __name__ == "__main__":
    main()
