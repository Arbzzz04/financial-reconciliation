"""Tests for DuckDB loading helpers (recon.db)."""

from decimal import Decimal

import pandas as pd
import pytest

from recon.db import connect, load_sources, read_source, set_variables

SAMPLE = pd.DataFrame(
    {
        "record_id": ["A-1", "A-2"],
        "reference": ["INV-1", "INV-2"],
        "amount": ["0.10", "-1234.56"],
        "txn_date": ["2024-01-05", "2024-02-29"],
        "currency": ["GBP", "GBP"],
        "description": ["one", "two"],
    }
)


def test_sources_load_with_exact_types():
    con = connect()
    load_sources(con, SAMPLE, SAMPLE)
    types = dict(
        con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'source_a'"
        ).fetchall()
    )
    assert types["amount"] == "DECIMAL(18,2)"
    assert types["txn_date"] == "DATE"


def test_decimal_arithmetic_is_exact():
    con = connect()
    load_sources(con, SAMPLE, SAMPLE)
    (total,) = con.execute("SELECT SUM(amount) FROM source_a").fetchone()
    assert total == Decimal("-1234.46")


def test_missing_column_is_reported():
    with pytest.raises(ValueError, match="description"):
        read_source(SAMPLE.drop(columns="description"))


def test_csv_round_trip(tmp_path):
    path = tmp_path / "a.csv"
    SAMPLE.to_csv(path, index=False)
    con = connect()
    load_sources(con, path, path)
    assert con.execute("SELECT COUNT(*) FROM source_b").fetchone() == (2,)


def test_variables_are_readable_from_sql():
    con = connect()
    set_variables(con, {"max_fee_bps": 40})
    assert con.execute("SELECT getvariable('max_fee_bps')").fetchone() == (40,)
    with pytest.raises(ValueError):
        set_variables(con, {"bad name": 1})
