"""Tests for the classification and summary pass (07_classify_report.sql)."""

import pytest

from recon.db import fetch
from recon.pipeline import run_passes

VARIABLES = {
    "max_fee_bps": 40,
    "fee_fixed": 0.20,
    "date_window_days": 3,
    "batch_max_size": 5,
    "batch_window_days": 10,
    "batch_span_days": 6,
}


@pytest.fixture
def results(loaded):
    run_passes(loaded, VARIABLES)
    return loaded


def test_one_result_row_per_source_record(results, small_run):
    rows = fetch(results, "SELECT record_id FROM reconciliation_results")
    expected = len(small_run.source_a) + len(small_run.source_b)
    assert len(rows) == expected
    assert rows["record_id"].is_unique


def test_statuses_partition_the_records(results):
    (summary,) = fetch(results, "SELECT * FROM summary").to_dict("records")
    total = summary["ledger_records"] + summary["bank_records"]
    parts = (
        summary["matched_records"]
        + summary["unmatched_records"]
        + summary["duplicate_records"]
    )
    assert parts == total
    assert 0 < summary["match_rate"] <= 1


def test_pair_labels_follow_the_deltas(results):
    pairs = fetch(results, "SELECT * FROM pair_results WHERE match_pass <> 'BATCH'")
    for category, delta, gap in zip(
        pairs["category"], pairs["amount_delta"], pairs["day_gap"], strict=True
    ):
        assert ("ROUNDING" in category) == (abs(delta) == 0.01)
        assert ("TIMING" in category) == (gap != 0)
        if category == "CLEAN":
            assert delta == 0 and gap == 0


def test_every_matched_record_has_a_partner_or_batch(results):
    orphans = fetch(
        results,
        "SELECT * FROM reconciliation_results "
        "WHERE status = 'ONE_TO_ONE' AND partner_id IS NULL "
        "   OR status = 'BATCH' AND batch_id IS NULL",
    )
    assert orphans.empty
