"""Benchmark the engine's accuracy and speed across several generated datasets.

Each run generates fresh data with a different seed, reconciles it, and scores
the result against the hidden ground truth. This is where the numbers in the
README's Results section come from.

Usage::

    python scripts/benchmark.py                 # 2,000 transactions, 5 seeds
    python scripts/benchmark.py --n 6000 --seeds 1 2 3
"""

from __future__ import annotations

import argparse
import time

import pandas as pd

from recon.datagen import GeneratorConfig, generate_datasets
from recon.metrics import classification_accuracy, score_duplicates, score_matches
from recon.pipeline import Tolerances, run_reconciliation


def benchmark(n_transactions: int, seeds: list[int]) -> pd.DataFrame:
    """Reconcile one generated dataset per seed and score each run.

    Args:
        n_transactions: Base transactions per generated dataset.
        seeds: One run per seed.

    Returns:
        One row per run with precision, recall, F1, classification accuracy,
        duplicate-detection F1 and the reconciliation time in seconds.
    """
    rows = []
    for seed in seeds:
        data = generate_datasets(
            GeneratorConfig(n_transactions=n_transactions, seed=seed)
        )
        started = time.perf_counter()
        result = run_reconciliation(
            data.source_a, data.source_b, Tolerances.from_metadata(data.metadata)
        )
        seconds = time.perf_counter() - started
        matches = score_matches(
            result.matches, data.ground_truth_pairs, data.record_labels
        )["overall"]
        rows.append(
            {
                "seed": seed,
                "records": len(result.results),
                "precision": matches.precision,
                "recall": matches.recall,
                "f1": matches.f1,
                "classification": classification_accuracy(
                    result.results, data.record_labels
                )["category_accuracy"],
                "duplicates_f1": score_duplicates(
                    result.duplicates, data.record_labels
                ).f1,
                "seconds": round(seconds, 2),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    """Parse arguments, run the benchmark, and print per-run and mean results."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, default=2000, help="transactions per run")
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 42, 99])
    args = parser.parse_args()
    table = benchmark(args.n, args.seeds)
    print(table.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("\nmean:")
    print(
        table.drop(columns="seed").mean().to_string(float_format=lambda x: f"{x:.4f}")
    )


if __name__ == "__main__":
    main()
