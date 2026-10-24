"""Tests for the one-call pipeline, report export and command line."""

import json

import pandas as pd
import pytest

from recon.__main__ import main
from recon.datagen import write_outputs
from recon.pipeline import Tolerances, run_reconciliation
from recon.report import export_report, formatted_headline_table, headline_table


@pytest.fixture(scope="module")
def result(small_run):
    return run_reconciliation(small_run.source_a, small_run.source_b)


def test_tolerances_cover_the_generator_bounds(small_run):
    tolerances = Tolerances.from_metadata(small_run.metadata)
    bounds = small_run.metadata["tolerances"]
    assert tolerances.max_fee_bps == bounds["max_fee_pct_bps"]
    assert tolerances.date_window_days == bounds["max_date_shift_days"]


def test_run_reconciliation_returns_every_record(result, small_run):
    assert len(result.results) == len(small_run.source_a) + len(small_run.source_b)
    assert 0 < result.summary["match_rate"] <= 1
    assert isinstance(result.summary["matched_records"], int)


def test_headline_table_lists_every_metric(result):
    table = headline_table(result)
    assert list(table.columns) == ["metric", "value"]
    assert "Match rate" in set(table["metric"])


def test_excel_report_has_one_sheet_per_table(result, tmp_path):
    path = export_report(result, tmp_path / "report.xlsx")
    sheets = pd.read_excel(path, sheet_name=None)
    assert set(sheets) == {"Summary", "By category", "Records", "Matched pairs"}
    assert len(sheets["Records"]) == len(result.results)


def test_csv_report_and_bad_suffix(result, tmp_path):
    path = export_report(result, tmp_path / "records.csv")
    assert len(pd.read_csv(path)) == len(result.results)
    with pytest.raises(ValueError, match="unsupported"):
        export_report(result, tmp_path / "report.pdf")


def test_command_line_scores_a_generated_run(small_run, tmp_path, capsys):
    paths = write_outputs(small_run, tmp_path)
    json.loads(paths["metadata"].read_text())  # valid metadata for --truth
    main(
        [
            str(paths["source_a"]),
            str(paths["source_b"]),
            "--truth",
            str(tmp_path / "truth"),
            "--out",
            str(tmp_path / "report.xlsx"),
        ]
    )
    printed = capsys.readouterr().out
    assert "Match rate" in printed and "F1 1.0000" in printed
    assert "np.float64" not in printed
    assert (tmp_path / "report.xlsx").exists()


def test_headline_numbers_are_formatted_for_people(result):
    shown = dict(formatted_headline_table(result).itertuples(index=False))
    assert shown["Match rate"].endswith("%")
    assert shown["Value reconciled"].startswith("£")
    assert len(shown["Value reconciled"].split(".")[1]) == 2  # always 2 decimals
    assert "." not in shown["Ledger records"]  # counts are whole numbers
