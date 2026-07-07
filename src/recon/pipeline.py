"""End-to-end orchestration of the reconciliation passes.

The passes run in tier order, strictest first, so high-confidence matches are
made before the looser passes see what is left. Each pass is one versioned
``.sql`` file in ``sql/``; this module only decides the order, feeds in the
tolerances, and collects the results.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import pandas as pd

from recon.db import connect, fetch, load_sources, run_sql_file, set_variables

# The SQL passes, in the order they run.
PASSES = (
    "01_normalise.sql",
    "02_exact_match.sql",
    "03_unmatched.sql",
    "04_tolerant_match.sql",
    "05_duplicates.sql",
    "06_batch_match.sql",
    "07_classify_report.sql",
)


@dataclass(frozen=True)
class Tolerances:
    """How far apart two records may be and still be the same transaction.

    Attributes:
        max_fee_bps: Largest fee as basis points of the amount (40 = 0.40%).
        fee_fixed: Largest fixed fee on top of the percentage, in currency units.
        date_window_days: Largest settlement delay, in business days.
        batch_max_size: Most payments one batch settlement may cover.
        batch_window_days: How many days before a settlement its members may fall.
        batch_span_days: Largest gap, in days, between members of one batch.
    """

    max_fee_bps: float = 40
    fee_fixed: float = 0.20
    date_window_days: int = 3
    batch_max_size: int = 5
    batch_window_days: int = 10
    batch_span_days: int = 6

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, object] | Path | str) -> Tolerances:
        """Derive tolerances from a generator run (docs/contracts.md, contract 2).

        Args:
            metadata: A parsed ``run_metadata.json`` or the path to one.

        Returns:
            Tolerances that cover the discrepancies that run injected.
        """
        if isinstance(metadata, Path | str):
            metadata = json.loads(Path(metadata).read_text(encoding="utf-8"))
        bounds = metadata["tolerances"]
        return cls(
            max_fee_bps=float(bounds["max_fee_pct_bps"]),
            fee_fixed=float(bounds["fee_fixed"]),
            date_window_days=int(bounds["max_date_shift_days"]),
        )

    def as_variables(self) -> dict[str, float | int]:
        """Return the tolerances as the variables the SQL passes read."""
        return asdict(self)


@dataclass
class ReconciliationResult:
    """Everything one reconciliation run produces.

    Attributes:
        results: One row per record in either source, with status and category.
        matches: One row per matched pair (batch settlements: one row per member).
        duplicates: One row per surplus copy, pointing at its original.
        pairs: Matched pairs with their amount and date deltas.
        summary: Headline numbers (match rate, value reconciled, ...).
        by_category: Record count and value per discrepancy category.
        tolerances: The tolerances the run used.
    """

    results: pd.DataFrame
    matches: pd.DataFrame
    duplicates: pd.DataFrame
    pairs: pd.DataFrame
    summary: dict[str, float | int]
    by_category: pd.DataFrame
    tolerances: Tolerances


def run_passes(
    con: duckdb.DuckDBPyConnection,
    variables: Mapping[str, float | int | str] | None = None,
) -> None:
    """Run every SQL pass, in order, against already-loaded sources.

    Args:
        con: A connection where ``source_a`` and ``source_b`` are loaded.
        variables: Tolerances and other parameters the passes read with
            ``getvariable()``; defaults to ``Tolerances()``.
    """
    set_variables(con, variables or Tolerances().as_variables())
    for name in PASSES:
        run_sql_file(con, name)


def run_reconciliation(
    source_a: Path | str | pd.DataFrame,
    source_b: Path | str | pd.DataFrame,
    tolerances: Tolerances | None = None,
) -> ReconciliationResult:
    """Reconcile two sources end to end in a fresh in-memory database.

    Args:
        source_a: The ledger (CSV path or DataFrame).
        source_b: The bank statement (CSV path or DataFrame).
        tolerances: Matching tolerances; defaults to ``Tolerances()``.

    Returns:
        The per-record results, matches, summaries and the tolerances used.
    """
    tolerances = tolerances or Tolerances()
    con = connect()
    try:
        load_sources(con, source_a, source_b)
        run_passes(con, tolerances.as_variables())
        (summary,) = fetch(con, "SELECT * FROM summary").to_dict("records")
        return ReconciliationResult(
            results=fetch(con, "SELECT * FROM reconciliation_results"),
            matches=fetch(con, "SELECT * FROM matches ORDER BY a_record_id"),
            duplicates=fetch(con, "SELECT * FROM duplicates ORDER BY record_id"),
            pairs=fetch(con, "SELECT * FROM pair_results ORDER BY a_record_id"),
            summary={key: _plain(value) for key, value in summary.items()},
            by_category=fetch(con, "SELECT * FROM summary_by_category"),
            tolerances=tolerances,
        )
    finally:
        con.close()


def _plain(value: object) -> float | int:
    """Convert a NumPy or Decimal scalar from DuckDB into a plain Python number."""
    number = float(value)
    if number.is_integer() and "int" in type(value).__name__:
        return int(number)
    return number
