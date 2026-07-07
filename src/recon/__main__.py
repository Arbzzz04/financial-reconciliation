"""Command line: reconcile two CSVs and, optionally, score against ground truth.

Usage::

    python -m recon data/samples/source_a.csv data/samples/source_b.csv
    python -m recon A.csv B.csv --out reports/report.xlsx --truth data/samples/truth
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from recon.metrics import classification_accuracy, score_duplicates, score_matches
from recon.pipeline import Tolerances, run_reconciliation
from recon.report import export_report, headline_table


def main(argv: list[str] | None = None) -> None:
    """Parse arguments, run the reconciliation, and print the results.

    Args:
        argv: Arguments to parse; ``None`` means ``sys.argv[1:]``.
    """
    parser = argparse.ArgumentParser(prog="python -m recon", description=__doc__)
    parser.add_argument("source_a", type=Path, help="ledger CSV")
    parser.add_argument("source_b", type=Path, help="bank statement CSV")
    parser.add_argument("--out", type=Path, help="write a report (.xlsx or .csv)")
    parser.add_argument(
        "--truth",
        type=Path,
        help="a generator truth/ folder: score accuracy and use its tolerances",
    )
    args = parser.parse_args(argv)

    tolerances = (
        Tolerances.from_metadata(args.truth / "run_metadata.json")
        if args.truth
        else Tolerances()
    )
    result = run_reconciliation(args.source_a, args.source_b, tolerances)
    print(headline_table(result).to_string(index=False))

    if args.truth:
        pairs = pd.read_csv(args.truth / "ground_truth_pairs.csv")
        labels = pd.read_csv(args.truth / "record_labels.csv")
        print("\nAccuracy against ground truth:")
        for name, score in score_matches(result.matches, pairs, labels).items():
            print(f"  matches ({name}): {score.as_dict()}")
        print(f"  duplicates: {score_duplicates(result.duplicates, labels).as_dict()}")
        print(f"  classification: {classification_accuracy(result.results, labels)}")

    if args.out:
        print(f"\nReport written to {export_report(result, args.out)}")


if __name__ == "__main__":
    main()
