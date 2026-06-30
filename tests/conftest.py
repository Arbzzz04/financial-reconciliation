"""Shared fixtures: a small generated dataset, loaded into DuckDB."""

import pytest

from recon.datagen import GeneratorConfig, generate_datasets
from recon.db import connect, load_sources


@pytest.fixture(scope="session")
def small_run():
    """A 600-transaction generator run with its ground truth."""
    return generate_datasets(GeneratorConfig(n_transactions=600, seed=11))


@pytest.fixture
def loaded(small_run):
    """A fresh DuckDB connection with the small run's sources loaded."""
    con = connect()
    load_sources(con, small_run.source_a, small_run.source_b)
    return con
