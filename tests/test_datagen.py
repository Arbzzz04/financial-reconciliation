"""Tests for the generator config, money helper, and base transaction set."""

import decimal
from decimal import Decimal

import pytest
from pandas.testing import assert_frame_equal

from recon.datagen import (
    GeneratorConfig,
    _money,
    build_faker,
    build_rng,
    make_base_transactions,
)
from recon.normalise import normalise_reference

# Columns whose values are guaranteed deterministic for a fixed seed (i.e. not the
# Faker-generated free-text columns, which are excluded from determinism by design).
_STRUCTURAL = [
    "base_txn_id",
    "reference",
    "amount",
    "direction",
    "txn_date",
    "txn_type",
    "currency",
]


def _make_base(n: int = 50, seed: int = 42):
    config = GeneratorConfig(n_transactions=n, seed=seed)
    rng = build_rng(config.seed)
    fake = build_faker(rng)
    return make_base_transactions(config, rng, fake)


def test_money_quantises_half_up():
    assert _money("1.005") == Decimal("1.01")
    assert _money(2.675) == Decimal("2.68")  # via str() -> avoids the float artefact
    assert _money(10) == Decimal("10.00")


def test_base_amounts_are_decimal_with_two_places():
    base = _make_base()
    assert all(isinstance(a, Decimal) for a in base["amount"])
    assert all(a.as_tuple().exponent == -2 for a in base["amount"])


def test_base_references_unique_after_normalisation():
    base = _make_base()
    keys = [normalise_reference(r) for r in base["reference"]]
    assert len(set(keys)) == len(keys)


def test_base_is_deterministic_for_fixed_seed():
    assert_frame_equal(_make_base()[_STRUCTURAL], _make_base()[_STRUCTURAL])


def test_different_seeds_diverge():
    assert not _make_base(seed=1)["amount"].equals(_make_base(seed=2)["amount"])


def test_invalid_rate_is_rejected():
    with pytest.raises(ValueError, match="drop_a_rate"):
        GeneratorConfig(drop_a_rate=1.5)


def test_import_does_not_mutate_global_decimal_context():
    # _money quantises with an explicit rounding argument, so importing datagen
    # must not have touched the process-global decimal context.
    assert decimal.getcontext().rounding == decimal.ROUND_HALF_EVEN
