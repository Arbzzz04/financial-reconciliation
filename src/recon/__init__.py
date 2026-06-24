"""financial-reconciliation: a SQL-first transaction reconciliation engine.

This package orchestrates a sequence of SQL passes (executed in DuckDB) that
match two transaction datasets and classify every discrepancy. Python here is a
thin layer: it loads CSVs into DuckDB, runs the versioned ``.sql`` files in
``sql/``, and pulls results back out for reporting and the Streamlit demo.

Modules:
    db: DuckDB connection management and a helper to run ``.sql`` files.
    pipeline: Orchestrates the ordered reconciliation passes end to end.
    report: Summary statistics and CSV/Excel export.
    metrics: Accuracy scoring (precision / recall / F1) against ground truth.
"""

__version__ = "0.1.0"
