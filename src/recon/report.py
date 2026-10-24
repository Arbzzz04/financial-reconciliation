"""Summary statistics and report export for reconciliation results.

The SQL passes already compute the numbers; this module arranges them for
people: a headline table, and an export an analyst can open in Excel.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from recon.pipeline import ReconciliationResult

# Friendly names for the headline numbers, in display order.
HEADLINE_LABELS = {
    "ledger_records": "Ledger records",
    "bank_records": "Bank records",
    "matched_records": "Matched records",
    "match_rate": "Match rate",
    "value_reconciled": "Value reconciled",
    "value_in_discrepancy": "Value in discrepancy",
    "unmatched_records": "Unmatched records",
    "duplicate_records": "Duplicate records",
}


def headline_table(result: ReconciliationResult) -> pd.DataFrame:
    """Arrange the summary as a two-column table for display or export.

    Args:
        result: A reconciliation run.

    Returns:
        A DataFrame with ``metric`` and ``value`` columns.
    """
    return pd.DataFrame(
        {
            "metric": list(HEADLINE_LABELS.values()),
            "value": [result.summary[key] for key in HEADLINE_LABELS],
        }
    )


def format_metric(key: str, value: float | int) -> str:
    """Format one headline number for people to read.

    Counts get thousands separators, the match rate becomes a percentage, and
    money gets a currency sign and exactly two decimal places.

    Args:
        key: The summary key, e.g. ``"match_rate"``.
        value: Its raw value.

    Returns:
        The display string, e.g. ``"91.6%"`` or ``"£114,956.52"``.
    """
    if key == "match_rate":
        return f"{value:.1%}"
    if key.startswith("value_"):
        return f"£{value:,.2f}"
    return f"{value:,.0f}"


def formatted_headline_table(result: ReconciliationResult) -> pd.DataFrame:
    """The headline table with every value formatted for display.

    ``headline_table`` keeps raw numbers (so the Excel report stays sortable and
    summable); this version is for printing.

    Args:
        result: A reconciliation run.

    Returns:
        A DataFrame with ``metric`` and ``value`` columns, both strings.
    """
    return pd.DataFrame(
        {
            "metric": list(HEADLINE_LABELS.values()),
            "value": [format_metric(k, result.summary[k]) for k in HEADLINE_LABELS],
        }
    )


def report_sheets(result: ReconciliationResult) -> dict[str, pd.DataFrame]:
    """The report's tables, keyed by sheet name.

    Args:
        result: A reconciliation run.

    Returns:
        Summary, per-category breakdown, every record, and the matched pairs.
    """
    return {
        "Summary": headline_table(result),
        "By category": result.by_category,
        "Records": result.results,
        "Matched pairs": result.pairs,
    }


def report_excel_bytes(result: ReconciliationResult) -> bytes:
    """Render the full report as an Excel workbook, in memory.

    Args:
        result: A reconciliation run.

    Returns:
        The ``.xlsx`` file contents (used by the Streamlit download button).
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet, frame in report_sheets(result).items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
    return buffer.getvalue()


def export_report(result: ReconciliationResult, path: Path | str) -> Path:
    """Write the report to disk as Excel (``.xlsx``) or CSV (``.csv``).

    Excel gets one sheet per table. CSV holds the per-record results, which is
    the table an analyst filters and works through.

    Args:
        result: A reconciliation run.
        path: Where to write; the suffix chooses the format.

    Returns:
        The path written.

    Raises:
        ValueError: If the suffix is neither ``.xlsx`` nor ``.csv``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".xlsx":
        path.write_bytes(report_excel_bytes(result))
    elif path.suffix == ".csv":
        result.results.to_csv(path, index=False)
    else:
        raise ValueError(f"unsupported report format: {path.suffix!r}")
    return path
