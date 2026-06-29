"""DuckDB connection helpers and a runner for the versioned SQL passes.

Python's job here is deliberately small: open a connection, load the two sources
into properly typed tables, and execute the ``.sql`` files in ``sql/``. All the
reconciliation logic lives in those files, where it can be read (and reviewed) as
plain SQL rather than as strings buried in Python.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import duckdb
import pandas as pd

# The repository's sql/ folder (src/recon/db.py -> repo root -> sql/).
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"

# The columns every source must provide, in order.
REQUIRED_COLUMNS = (
    "record_id",
    "reference",
    "amount",
    "txn_date",
    "currency",
    "description",
)

# Each source is cast to these types on load. DECIMAL(18, 2) keeps money exact:
# a DOUBLE would turn 0.10 + 0.20 into 0.30000000000000004.
_LOAD_SQL = """
CREATE OR REPLACE TABLE {table} AS
SELECT
    CAST(record_id AS VARCHAR) AS record_id,
    CAST(reference AS VARCHAR) AS reference,
    CAST(amount AS DECIMAL(18, 2)) AS amount,
    CAST(txn_date AS DATE) AS txn_date,
    CAST(currency AS VARCHAR) AS currency,
    CAST(description AS VARCHAR) AS description,
FROM {view}
"""


def connect(database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection.

    Args:
        database: ``":memory:"`` (the default) for a throwaway in-process database,
            or a file path to persist tables between runs.

    Returns:
        An open DuckDB connection.
    """
    return duckdb.connect(database)


def read_source(source: Path | str | pd.DataFrame) -> pd.DataFrame:
    """Read a source CSV (or accept a DataFrame) as text, ready for typed loading.

    Everything is read as text so nothing is guessed: amounts never pass through
    floating point, and DuckDB applies the exact types in ``load_sources``.

    Args:
        source: A CSV path or an existing DataFrame.

    Returns:
        A DataFrame whose columns are all strings.

    Raises:
        ValueError: If a required column is missing.
    """
    if isinstance(source, pd.DataFrame):
        frame = source.copy()
    else:
        frame = pd.read_csv(source, dtype=str, keep_default_na=False)
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"source is missing required columns: {', '.join(missing)}")
    return frame[list(REQUIRED_COLUMNS)].astype(str)


def load_sources(
    con: duckdb.DuckDBPyConnection,
    source_a: Path | str | pd.DataFrame,
    source_b: Path | str | pd.DataFrame,
) -> None:
    """Load both sources into the typed tables ``source_a`` and ``source_b``.

    Args:
        con: An open DuckDB connection.
        source_a: The ledger (CSV path or DataFrame).
        source_b: The bank statement (CSV path or DataFrame).
    """
    for table, source in (("source_a", source_a), ("source_b", source_b)):
        view = f"raw_{table}"
        con.register(view, read_source(source))
        con.execute(_LOAD_SQL.format(table=table, view=view))
        con.unregister(view)


def set_variables(
    con: duckdb.DuckDBPyConnection, variables: Mapping[str, float | int | str]
) -> None:
    """Expose parameters to the SQL files as DuckDB variables.

    A ``.sql`` file reads them with ``getvariable('name')``, so tolerances live in
    one place (Python) without ever pasting values into SQL text.

    Args:
        con: An open DuckDB connection.
        variables: Variable names mapped to values.
    """
    for name, value in variables.items():
        if not name.isidentifier():
            raise ValueError(f"invalid variable name: {name!r}")
        con.execute(f"SET VARIABLE {name} = ?", [value])


def run_sql_file(con: duckdb.DuckDBPyConnection, name: str) -> None:
    """Execute one versioned SQL file from ``sql/``.

    Args:
        con: An open DuckDB connection.
        name: The file name, e.g. ``"01_normalise.sql"``.
    """
    con.execute((SQL_DIR / name).read_text(encoding="utf-8"))


def fetch(con: duckdb.DuckDBPyConnection, query: str) -> pd.DataFrame:
    """Run a query and return the result as a pandas DataFrame.

    Args:
        con: An open DuckDB connection.
        query: The SQL to run (typically ``SELECT * FROM some_table``).

    Returns:
        The result set.
    """
    return con.execute(query).df()
