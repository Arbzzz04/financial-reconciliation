"""Tests for the matching passes, checked against the generator's ground truth."""

import pytest

from recon.db import fetch
from recon.pipeline import run_passes


def _canonical(small_run):
    """Map each duplicate copy to its original.

    A duplicate is an exact copy, so pairing the copy instead of the original is
    equally correct; scoring compares canonical IDs.
    """
    labels = small_run.record_labels.dropna(subset=["duplicate_of"])
    mapping = dict(zip(labels["record_id"], labels["duplicate_of"], strict=True))
    return lambda record_id: mapping.get(record_id, record_id)


def _truth_pairs(small_run, match_type):
    truth = small_run.ground_truth_pairs
    truth = truth[truth["match_type"] == match_type]
    return set(zip(truth["a_record_id"], truth["b_record_id"], strict=True)), truth


@pytest.fixture
def reconciled(loaded):
    run_passes(loaded, {"max_fee_bps": 40, "fee_fixed": 0.20, "date_window_days": 3})
    return loaded


def _predicted(con, small_run, match_pass):
    canon = _canonical(small_run)
    rows = fetch(con, f"SELECT * FROM matches WHERE match_pass = '{match_pass}'")
    return {
        (canon(a), canon(b))
        for a, b in zip(rows["a_record_id"], rows["b_record_id"], strict=True)
    }


def test_every_record_is_matched_at_most_once(reconciled):
    for column in ("a_record_id", "b_record_id"):
        dupes = fetch(
            reconciled,
            f"SELECT {column}, COUNT(*) AS n FROM matches "
            f"WHERE match_pass <> 'BATCH' GROUP BY {column} HAVING COUNT(*) > 1",
        )
        assert dupes.empty, column


def test_exact_matches_are_all_true_pairs(reconciled, small_run):
    truth, _ = _truth_pairs(small_run, "ONE_TO_ONE")
    predicted = _predicted(reconciled, small_run, "EXACT")
    assert predicted and predicted <= truth


def test_exact_pass_finds_every_clean_or_reformatted_pair(reconciled, small_run):
    truth, frame = _truth_pairs(small_run, "ONE_TO_ONE")
    exact_kinds = frame["discrepancies"].isin(["CLEAN", "REF_FORMAT"])
    expected = set(
        zip(
            frame.loc[exact_kinds, "a_record_id"],
            frame.loc[exact_kinds, "b_record_id"],
            strict=True,
        )
    )
    assert _predicted(reconciled, small_run, "EXACT") == expected


def test_anti_join_and_except_agree(reconciled):
    anti = set(fetch(reconciled, "SELECT record_id FROM remaining_a")["record_id"])
    via_except = set(
        fetch(reconciled, "SELECT record_id FROM remaining_a_ids_via_except")[
            "record_id"
        ]
    )
    assert anti == via_except


def test_every_record_is_either_matched_or_remaining(reconciled):
    for side, column in (("a", "a_record_id"), ("b", "b_record_id")):
        everyone = set(
            fetch(reconciled, f"SELECT record_id FROM norm_{side}")["record_id"]
        )
        matched = set(fetch(reconciled, f"SELECT {column} AS id FROM matches")["id"])
        remaining = set(
            fetch(reconciled, f"SELECT record_id FROM remaining_{side}")["record_id"]
        )
        assert matched | remaining == everyone
        assert not matched & remaining


def test_tolerant_matches_are_all_true_pairs(reconciled, small_run):
    truth, _ = _truth_pairs(small_run, "ONE_TO_ONE")
    predicted = _predicted(reconciled, small_run, "TOLERANT")
    assert predicted and predicted <= truth


def test_exact_plus_tolerant_recover_every_one_to_one_pair(reconciled, small_run):
    truth, _ = _truth_pairs(small_run, "ONE_TO_ONE")
    found = _predicted(reconciled, small_run, "EXACT") | _predicted(
        reconciled, small_run, "TOLERANT"
    )
    assert found == truth
