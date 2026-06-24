"""DuckDB connection helpers and a runner for the versioned SQL passes.

Planned for Phase 3. This module will open a DuckDB connection (in-memory or
file-backed), register the source CSVs as tables/views, and execute the ``.sql``
files in ``sql/`` in order, returning results as pandas DataFrames.
"""
