"""Synthetic reconciliation data generator.

Planned for Phase 2. Produces a base ground-truth transaction set, then derives
two divergent sources (``source_a``, ``source_b``) by injecting configurable
discrepancies: dropped records, side-only rows, amount perturbations (fees,
rounding), date shifts within a settlement window, duplicates, reference-format
variation, and one-to-many batch settlements.

The true source-to-source mapping is persisted separately as ground truth, so
the engine's accuracy can be measured. The generator can optionally seed from a
real transactions CSV to make the base look realistic; the seed source is
pluggable, not hardcoded.
"""
