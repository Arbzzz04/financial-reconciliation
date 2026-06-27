"""Synthetic reconciliation data generator -- command-line entry point.

Produces a base ground-truth transaction set, then derives two divergent sources
(``source_a`` = ledger, ``source_b`` = bank statement) by injecting configurable
discrepancies: dropped records, side-only rows, amount perturbations (fees,
rounding), date shifts within a settlement window, duplicates, reference-format
variation, and one-to-many batch settlements.

The true source-to-source mapping is persisted separately as ground truth, so the
engine's accuracy can be measured. The generator can optionally seed from a real
transactions CSV to make the base look realistic; the seed source is pluggable,
not hardcoded. All logic lives in ``recon.datagen``; this script only parses
arguments, runs it, and prints a summary.

Usage::

    python data/generate_data.py                 # defaults: 2,000 txns, seed 42
    python data/generate_data.py --n 5000 --seed 7
    python data/generate_data.py --seed-csv kaggle.csv --seed-col amount=Amount
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

from recon.datagen import (
    GeneratorConfig,
    check_reference_contract,
    generate_datasets,
    write_outputs,
)

DEFAULT_OUT = Path(__file__).resolve().parent / "samples"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options.

    Args:
        argv: Arguments to parse; ``None`` means ``sys.argv[1:]``.

    Returns:
        The parsed options.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, help="number of base transactions")
    parser.add_argument("--seed", type=int, help="random seed (same seed, same data)")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="output directory"
    )
    parser.add_argument(
        "--seed-csv", type=Path, help="optional real transactions CSV to seed from"
    )
    parser.add_argument(
        "--seed-col",
        action="append",
        default=[],
        metavar="FIELD=COLUMN",
        help="map a field (amount, description) to a seed-CSV column; repeatable",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> GeneratorConfig:
    """Turn parsed options into a ``GeneratorConfig``, keeping defaults otherwise.

    Args:
        args: The parsed command-line options.

    Returns:
        The generator configuration.
    """
    overrides: dict[str, object] = {}
    if args.n is not None:
        overrides["n_transactions"] = args.n
    if args.seed is not None:
        overrides["seed"] = args.seed
    if args.seed_csv is not None:
        overrides["seed_csv"] = args.seed_csv
        overrides["seed_csv_columns"] = dict(
            pair.split("=", 1) for pair in args.seed_col
        )
    return dataclasses.replace(GeneratorConfig(), **overrides)


def main(argv: list[str] | None = None) -> None:
    """Generate the datasets, self-check them, write them, and print a summary.

    Args:
        argv: Arguments to parse; ``None`` means ``sys.argv[1:]``.
    """
    args = parse_args(argv)
    config = build_config(args)
    data = generate_datasets(config)
    check_reference_contract(data)
    paths = write_outputs(data, args.out)

    counts = data.metadata["counts"]
    print(
        f"Generated {counts['base_transactions']:,} base transactions "
        f"(seed {config.seed}) into {args.out}"
    )
    print(f"  source_a: {counts['source_a_records']:,} records")
    print(f"  source_b: {counts['source_b_records']:,} records")
    print(
        f"  true pairs: {counts['ground_truth_pairs']:,} "
        f"({counts['one_to_one_pairs']:,} one-to-one, "
        f"{counts['batch_settlements']:,} batch settlements)"
    )
    print("  records by category:")
    for category, count in counts["by_category"].items():
        print(f"    {category:<18} {count:>6,}")
    print(f"  ground truth: {paths['ground_truth_pairs'].parent}")


if __name__ == "__main__":
    main()
