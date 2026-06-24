"""Reference normalisation -- the canonical contract for matching keys.

Reconciliation can only match two records if their *keys* agree, but real-world
references are written inconsistently: ``"INV-001"``, ``"INV001"``, ``"Inv 001"``
all mean the same invoice. Before any join we therefore collapse a reference to a
single canonical form.

This module is the **single source of truth** for that transformation. The
synthetic data generator tests its reference-format variations against it, and
the Phase 3 SQL pass ``sql/01_normalise.sql`` must mirror these rules exactly. If
the Python and the SQL ever drift, matches silently disappear -- so the rules
live here, in one place, with tests and a written spec in ``docs/contracts.md``.
"""

from __future__ import annotations

import re

# A "run" of one or more characters that are not letters or digits.
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]+")
# A maximal run of digits (so we can strip leading zeros run-by-run).
_DIGIT_RUN = re.compile(r"\d+")


def _strip_run_leading_zeros(match: re.Match[str]) -> str:
    """Strip leading zeros from one digit run, keeping at least a single ``0``."""
    digits = match.group(0)
    stripped = digits.lstrip("0")
    return stripped if stripped else "0"


def normalise_reference(reference: str) -> str:
    """Normalise a transaction reference to its canonical matching form.

    The transformation is applied in this order:

    1. Remove every character that is not a letter or digit (spaces, hyphens,
       slashes, punctuation).
    2. Upper-case the result.
    3. Strip leading zeros from each maximal run of digits, keeping a single
       ``0`` if the run is all zeros.

    Args:
        reference: The raw reference as it appears in a source record.

    Returns:
        The canonical form. For example ``"Inv 000123"`` becomes ``"INV123"``,
        ``"INV-0001"`` becomes ``"INV1"``, and ``"PAY/000"`` becomes ``"PAY0"``.
    """
    alnum = _NON_ALNUM.sub("", reference)
    upper = alnum.upper()
    return _DIGIT_RUN.sub(_strip_run_leading_zeros, upper)
