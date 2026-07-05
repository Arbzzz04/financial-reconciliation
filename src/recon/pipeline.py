"""End-to-end orchestration of the reconciliation passes.

The passes run in tier order, strictest first, so high-confidence matches are
made before the looser passes see what is left. Each pass is one versioned
``.sql`` file in ``sql/``; this module only decides the order and feeds in the
tolerances.
"""

from __future__ import annotations

from collections.abc import Mapping

import duckdb

from recon.db import run_sql_file, set_variables

# The SQL passes, in the order they run.
PASSES = (
    "01_normalise.sql",
    "02_exact_match.sql",
    "03_unmatched.sql",
    "04_tolerant_match.sql",
    "05_duplicates.sql",
    "06_batch_match.sql",
)


def run_passes(
    con: duckdb.DuckDBPyConnection,
    variables: Mapping[str, float | int | str] | None = None,
) -> None:
    """Run every SQL pass, in order, against already-loaded sources.

    Args:
        con: A connection where ``source_a`` and ``source_b`` are loaded.
        variables: Tolerances and other parameters the passes read with
            ``getvariable()``.
    """
    if variables:
        set_variables(con, variables)
    for name in PASSES:
        run_sql_file(con, name)
