"""Tests for the reference-normalisation contract (recon.normalise)."""

import pytest

from recon.normalise import normalise_reference


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("INV-123", "INV123"),
        ("INV123", "INV123"),
        ("Inv 123", "INV123"),
        ("inv-123", "INV123"),
        ("Inv 000123", "INV123"),  # leading zeros stripped
        ("INV-0001", "INV1"),
        ("  INV / 123  ", "INV123"),  # surrounding + internal punctuation
        ("PAY/000", "PAY0"),  # all-zero run keeps a single 0
        ("TRF-10-20", "TRF1020"),  # multiple digit runs, zeros only stripped per run
    ],
)
def test_normalise_reference_examples(raw: str, expected: str) -> None:
    assert normalise_reference(raw) == expected


def test_format_variants_collapse_to_one_key() -> None:
    """Every common way of writing the same reference must yield one key."""
    variants = ["INV-123", "INV123", "Inv 123", "INV-0123", "inv/123"]
    assert {normalise_reference(v) for v in variants} == {"INV123"}


def test_distinct_references_stay_distinct() -> None:
    """Genuinely different references must not collide after normalisation."""
    refs = ["INV-1", "INV-2", "INV-10", "REF-1", "PAY-1"]
    assert len({normalise_reference(r) for r in refs}) == len(refs)
