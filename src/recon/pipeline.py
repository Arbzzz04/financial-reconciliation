"""End-to-end orchestration of the reconciliation passes.

Planned for Phase 3 onward. This module will load two source datasets, run the
SQL passes in tier order (normalise -> exact -> unmatched -> tolerant ->
duplicates -> batch -> classify), and return a single per-record reconciliation
result table.
"""
