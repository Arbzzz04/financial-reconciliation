"""End-to-end tests: the whole engine against fresh generated data.

The module tests check each pass in isolation; these check the engine as a user
runs it, on datasets it has never seen, and guard its accuracy with floors so a
change that quietly makes matching worse fails the build.
"""

import pandas as pd
import pytest

from recon.datagen import GeneratorConfig, generate_datasets
from recon.db import REQUIRED_COLUMNS
from recon.metrics import classification_accuracy, score_duplicates, score_matches
from recon.pipeline import Tolerances, run_reconciliation


def _score(data, tolerances=None):
    result = run_reconciliation(data.source_a, data.source_b, tolerances)
    matches = score_matches(result.matches, data.ground_truth_pairs, data.record_labels)
    return result, matches


@pytest.mark.parametrize("seed", [101, 202, 303])
def test_accuracy_floors_on_unseen_data(seed):
    data = generate_datasets(GeneratorConfig(n_transactions=1500, seed=seed))
    result, matches = _score(data, Tolerances.from_metadata(data.metadata))
    assert matches["overall"].precision >= 0.999
    assert matches["overall"].recall >= 0.995
    assert matches["ONE_TO_ONE"].f1 == 1.0
    assert score_duplicates(result.duplicates, data.record_labels).f1 == 1.0
    accuracy = classification_accuracy(result.results, data.record_labels)
    assert accuracy["category_accuracy"] >= 0.99


def test_tolerances_are_what_recover_perturbed_pairs():
    data = generate_datasets(GeneratorConfig(n_transactions=800, seed=5))
    strict = Tolerances(max_fee_bps=0, fee_fixed=0, date_window_days=-2)
    _, strict_scores = _score(data, strict)
    _, default_scores = _score(data)
    # Without tolerances only the exact pass can match, so fees, rounding and
    # settlement delays go unmatched; the default tolerances recover them.
    assert strict_scores["ONE_TO_ONE"].recall < 0.8
    assert default_scores["ONE_TO_ONE"].recall == 1.0


def test_empty_sources_produce_an_empty_but_valid_report():
    empty = pd.DataFrame(columns=list(REQUIRED_COLUMNS))
    result = run_reconciliation(empty, empty)
    assert result.results.empty
    assert result.summary["match_rate"] == 0
    assert result.summary["value_reconciled"] == 0


def test_column_order_and_extra_columns_do_not_matter(small_run):
    shuffled_a = small_run.source_a[list(reversed(REQUIRED_COLUMNS))].assign(note="x")
    reference = run_reconciliation(small_run.source_a, small_run.source_b)
    reordered = run_reconciliation(shuffled_a, small_run.source_b)
    assert reordered.summary == reference.summary


def test_reconciliation_is_deterministic(small_run):
    first = run_reconciliation(small_run.source_a, small_run.source_b)
    second = run_reconciliation(small_run.source_a, small_run.source_b)
    pd.testing.assert_frame_equal(first.results, second.results)
    pd.testing.assert_frame_equal(first.matches, second.matches)
