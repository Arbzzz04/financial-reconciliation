"""The SQL normalisation pass must agree with the Python contract exactly."""

import pytest

from recon.db import fetch
from recon.normalise import normalise_reference
from recon.pipeline import run_passes

CONTRACT_VECTORS = [
    ("INV-123", "INV123"),
    ("Inv 000123", "INV123"),
    ("INV-0001", "INV1"),
    ("PAY/000", "PAY0"),
    ("TRF-10-20", "TRF1020"),
    ("  INV / 123  ", "INV123"),
    ("X0Y00Z007", "X0Y0Z7"),
    ("00A00", "0A0"),
]


@pytest.mark.parametrize(("raw", "expected"), CONTRACT_VECTORS)
def test_sql_macro_matches_contract_vectors(loaded, raw, expected):
    run_passes(loaded)  # defines the macro (and builds norm_a / norm_b)
    sql = loaded.execute("SELECT normalise_reference(?)", [raw]).fetchone()[0]
    assert sql == expected == normalise_reference(raw)


def test_sql_and_python_agree_on_every_generated_reference(loaded):
    run_passes(loaded)
    for table in ("norm_a", "norm_b"):
        rows = fetch(loaded, f"SELECT reference, norm_ref FROM {table}")
        python = rows["reference"].map(normalise_reference)
        assert (rows["norm_ref"] == python).all(), table


def test_normalised_tables_keep_every_record(loaded, small_run):
    run_passes(loaded)
    assert fetch(loaded, "SELECT COUNT(*) AS n FROM norm_a")["n"][0] == len(
        small_run.source_a
    )
    directions = set(
        fetch(loaded, "SELECT DISTINCT direction FROM norm_b")["direction"]
    )
    assert directions == {"credit", "debit"}
