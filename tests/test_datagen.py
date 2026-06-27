"""Tests for the synthetic data generator (recon.datagen).

The first group covers the building blocks (config, money, base set). The second
checks the properties later phases rely on: every record is accounted for, the
answer key is consistent, and the published tolerances really cover the injected
discrepancies.
"""

import decimal
import json
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from recon.datagen import (
    GeneratorConfig,
    _money,
    build_faker,
    build_rng,
    business_days_between,
    check_reference_contract,
    generate_datasets,
    load_seed_csv,
    make_base_transactions,
    shift_business_days,
    vary_reference,
    write_outputs,
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


# --- Full generator: divergent sources + ground truth -----------------------


@pytest.fixture(scope="module")
def generated():
    return generate_datasets(GeneratorConfig(n_transactions=400, seed=7))


def test_every_base_transaction_has_exactly_one_fate(generated):
    # Each base txn is either a 1:1 pair, in a batch, or present on one side only.
    labels = generated.record_labels
    originals = labels[labels["status"] != "DUPLICATE"].dropna(subset=["base_txn_id"])
    per_txn = originals.groupby("base_txn_id")["status"].agg(["count", "first"])
    assert set(per_txn.index) == set(generated.base["base_txn_id"])
    one_to_one = per_txn[per_txn["first"] == "ONE_TO_ONE"]
    assert (one_to_one["count"] == 2).all()  # once in A, once in B
    assert (per_txn[per_txn["first"] != "ONE_TO_ONE"]["count"] == 1).all()


def test_every_record_is_labelled_exactly_once(generated):
    ids = list(generated.source_a["record_id"]) + list(generated.source_b["record_id"])
    assert sorted(ids) == sorted(generated.record_labels["record_id"])
    assert generated.record_labels["record_id"].is_unique


def test_ground_truth_refers_to_real_records(generated):
    pairs = generated.ground_truth_pairs
    assert set(pairs["a_record_id"]) <= set(generated.source_a["record_id"])
    assert set(pairs["b_record_id"]) <= set(generated.source_b["record_id"])
    assert not pairs.duplicated(["a_record_id", "b_record_id"]).any()


def test_one_to_one_pairs_stay_within_published_tolerances(generated):
    # Contract 2: the tolerant SQL pass can only recover what these bounds cover.
    tol = generated.metadata["tolerances"]
    pairs = generated.ground_truth_pairs
    one_to_one = pairs[pairs["match_type"] == "ONE_TO_ONE"]
    max_delta = Decimal(tol["max_amount_delta_abs"])
    assert all(abs(d) <= max_delta for d in one_to_one["amount_delta"])
    assert one_to_one["date_shift_days"].between(0, tol["max_date_shift_days"]).all()


def test_fee_and_rounding_deltas_match_their_labels(generated):
    pairs = generated.ground_truth_pairs
    one_to_one = pairs[pairs["match_type"] == "ONE_TO_ONE"]
    for tags, delta in zip(
        one_to_one["discrepancies"], one_to_one["amount_delta"], strict=True
    ):
        if "ROUNDING" in tags:
            assert abs(delta) == Decimal("0.01")
        elif "FEE" in tags:
            assert delta < 0  # a processor fee always reduces what the bank shows
        else:
            assert delta == 0


def test_one_to_one_references_share_a_normalised_key(generated):
    # Contract 1: reference-format variants must survive normalisation.
    check_reference_contract(generated)  # raises AssertionError on a mismatch


def test_batch_settlement_equals_sum_of_its_members(generated):
    a = generated.source_a.set_index("record_id")["amount"]
    b = generated.source_b.set_index("record_id")["amount"]
    batch = generated.ground_truth_pairs.query("match_type == 'BATCH'")
    assert not batch.empty
    for b_id, members in batch.groupby("b_record_id"):
        assert 2 <= len(members) <= 5
        assert b[b_id] == sum(a[members["a_record_id"]], Decimal("0.00"))
        # Contract 3: one ground-truth row per member, never a collapsed group row.
        assert members["a_record_id"].is_unique


def test_batch_members_are_settled_close_together(generated):
    batch = generated.ground_truth_pairs.query("match_type == 'BATCH'")
    window = generated.metadata["tolerances"]["max_date_shift_days"]
    # Members share an ISO week (at most 5 business days apart), then the batch
    # settles within the settlement window.
    assert batch["date_shift_days"].max() <= 5 + window


def test_duplicates_are_exact_copies_of_their_original(generated):
    labels = generated.record_labels.set_index("record_id")
    both = pd.concat([generated.source_a, generated.source_b]).set_index("record_id")
    dupes = labels[labels["status"] == "DUPLICATE"]
    assert not dupes.empty
    for dup_id, original_id in dupes["duplicate_of"].items():
        assert dup_id[0] == original_id[0]  # same source
        assert both.loc[dup_id].equals(both.loc[original_id])


def test_record_ids_do_not_leak_matches(generated):
    # IDs are assigned after shuffling, so A-n should rarely pair with B-n.
    pairs = generated.ground_truth_pairs
    same_number = (pairs["a_record_id"].str[2:] == pairs["b_record_id"].str[2:]).mean()
    assert same_number < 0.05


def test_generator_is_deterministic_for_fixed_seed():
    config = GeneratorConfig(n_transactions=200, seed=3)
    first, second = generate_datasets(config), generate_datasets(config)
    structural = ["record_id", "reference", "amount", "txn_date"]
    assert_frame_equal(first.source_a[structural], second.source_a[structural])
    assert_frame_equal(first.source_b[structural], second.source_b[structural])
    assert_frame_equal(first.ground_truth_pairs, second.ground_truth_pairs)


@pytest.mark.parametrize("reference", ["INV-1", "REF-42", "PAY-1999", "TRF-7"])
def test_vary_reference_is_always_invertible(reference):
    rng = build_rng(0)
    for _ in range(50):
        variant = vary_reference(reference, rng)
        assert normalise_reference(variant) == normalise_reference(reference)


def test_shift_business_days_skips_weekends():
    friday = date(2024, 1, 5)
    assert shift_business_days(friday, 1) == date(2024, 1, 8)  # Monday
    assert shift_business_days(friday, 2) == date(2024, 1, 9)
    assert business_days_between(friday, date(2024, 1, 9)) == 2


def test_write_outputs_round_trips_money_exactly(generated, tmp_path):
    paths = write_outputs(generated, tmp_path)
    assert all(p.exists() for p in paths.values())
    reread = pd.read_csv(paths["source_a"], dtype={"amount": str})
    assert [Decimal(x) for x in reread["amount"]] == list(generated.source_a["amount"])
    metadata = json.loads(paths["metadata"].read_text())
    assert metadata["tolerances"]["max_date_shift_days"] == 3


def test_seed_csv_supplies_amounts_and_descriptions(tmp_path):
    seed = tmp_path / "kaggle.csv"
    pd.DataFrame(
        {"Amount": ["£12.50", "1,234.00", "-99.99", "0"], "Narrative": list("wxyz")}
    ).to_csv(seed, index=False)
    config = GeneratorConfig(
        n_transactions=60,
        seed_csv=seed,
        seed_csv_columns={"amount": "Amount", "description": "Narrative"},
    )
    base = make_base_transactions(config, build_rng(1), build_faker(build_rng(1)))
    allowed = {Decimal("12.50"), Decimal("1234.00"), Decimal("99.99")}
    assert {abs(a) for a in base["amount"]} <= allowed  # the zero row is dropped
    assert set(base["description"]) <= {"w", "x", "y"}


def test_seed_csv_without_amount_column_is_rejected(tmp_path):
    seed = tmp_path / "bad.csv"
    pd.DataFrame({"value": [1, 2]}).to_csv(seed, index=False)
    with pytest.raises(ValueError, match="amount column"):
        load_seed_csv(seed, {"amount": "Amount"})
