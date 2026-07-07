"""Tests for accuracy scoring (recon.metrics)."""

import pandas as pd
import pytest
from tests.conftest import VARIABLES

from recon.db import fetch
from recon.metrics import (
    Score,
    canonicaliser,
    classification_accuracy,
    score_duplicates,
    score_matches,
    score_sets,
)
from recon.pipeline import run_passes


def test_score_arithmetic():
    score = score_sets({1, 2, 3, 4}, {3, 4, 5})
    assert (score.true_positives, score.false_positives, score.false_negatives) == (
        2,
        2,
        1,
    )
    assert score.precision == 0.5
    assert score.recall == pytest.approx(2 / 3)
    assert score.f1 == pytest.approx(4 / 7)


def test_empty_cases_do_not_divide_by_zero():
    assert Score(0, 0, 0).precision == Score(0, 0, 0).recall == 1.0
    assert Score(0, 3, 0).f1 == 0.0


def test_canonicaliser_maps_copies_to_originals():
    labels = pd.DataFrame(
        {"record_id": ["A-1", "A-2", "B-1"], "duplicate_of": [None, "A-1", None]}
    )
    canon = canonicaliser(labels)
    assert [canon(r) for r in ("A-1", "A-2", "B-1")] == ["A-1", "A-1", "B-1"]


@pytest.fixture
def run(loaded):
    run_passes(loaded, VARIABLES)
    return loaded


def test_engine_is_perfect_on_the_fixture_run(run, small_run):
    scores = score_matches(
        fetch(run, "SELECT * FROM matches"),
        small_run.ground_truth_pairs,
        small_run.record_labels,
    )
    for name, score in scores.items():
        assert score.f1 == 1.0, name


def test_duplicate_detection_is_scored_by_original(run, small_run):
    score = score_duplicates(
        fetch(run, "SELECT * FROM duplicates"), small_run.record_labels
    )
    assert score.f1 == 1.0


def test_classification_is_scored_per_duplicate_group(run, small_run):
    accuracy = classification_accuracy(
        fetch(run, "SELECT * FROM reconciliation_results"), small_run.record_labels
    )
    assert accuracy == {"status_accuracy": 1.0, "category_accuracy": 1.0}
