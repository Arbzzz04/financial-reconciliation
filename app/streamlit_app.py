"""Streamlit demo for the reconciliation engine.

Generate a sample dataset (with its hidden ground truth) or upload two CSVs,
tune the matching tolerances, then explore the per-record results and download
a report. Run it with::

    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    import recon  # noqa: F401  (installed with `pip install -e .`)
except ModuleNotFoundError:  # e.g. on Streamlit Community Cloud
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from recon.datagen import GeneratedData, GeneratorConfig, generate_datasets
from recon.db import REQUIRED_COLUMNS, read_source
from recon.pipeline import ReconciliationResult, Tolerances, run_reconciliation
from recon.report import report_excel_bytes

st.set_page_config(page_title="Reconciliation Engine", page_icon="🧾", layout="wide")


@st.cache_data(show_spinner="Generating sample data…")
def sample_data(n_transactions: int, seed: int) -> GeneratedData:
    """Generate (and cache) a sample dataset with its ground truth."""
    return generate_datasets(GeneratorConfig(n_transactions=n_transactions, seed=seed))


@st.cache_data(show_spinner="Reconciling…")
def reconcile(
    source_a: pd.DataFrame, source_b: pd.DataFrame, tolerances: Tolerances
) -> ReconciliationResult:
    """Run (and cache) the reconciliation for one set of inputs."""
    return run_reconciliation(source_a, source_b, tolerances)


def sidebar() -> tuple[pd.DataFrame | None, pd.DataFrame | None, GeneratedData | None]:
    """Draw the data-source controls and return the two sources (and truth)."""
    st.sidebar.header("Data")
    mode = st.sidebar.radio(
        "Source",
        ["Generate sample data", "Upload two CSVs"],
        label_visibility="collapsed",
    )
    if mode == "Generate sample data":
        n = st.sidebar.slider("Transactions", 500, 4000, 2000, step=500)
        seed = st.sidebar.number_input("Seed", value=42, step=1)
        data = sample_data(n, int(seed))
        return data.source_a, data.source_b, data

    st.sidebar.caption("Columns required: " + ", ".join(REQUIRED_COLUMNS))
    upload_a = st.sidebar.file_uploader("Ledger (source A)", type="csv")
    upload_b = st.sidebar.file_uploader("Bank statement (source B)", type="csv")
    if upload_a is None or upload_b is None:
        return None, None, None
    try:
        return read_source(upload_a), read_source(upload_b), None
    except ValueError as error:
        st.sidebar.error(str(error))
        return None, None, None


def tolerance_controls() -> Tolerances:
    """Draw the tolerance sliders and return the chosen tolerances."""
    defaults = Tolerances()
    with st.sidebar.expander("Matching tolerances", expanded=False):
        return Tolerances(
            max_fee_bps=st.slider(
                "Max fee (basis points)",
                0,
                100,
                int(defaults.max_fee_bps),
                help="40 bps = 0.40% of the amount",
            ),
            fee_fixed=st.slider("Max fixed fee", 0.0, 1.0, defaults.fee_fixed, 0.05),
            date_window_days=st.slider(
                "Settlement window (business days)", 0, 7, defaults.date_window_days
            ),
            batch_max_size=st.slider(
                "Max payments per batch", 2, 6, defaults.batch_max_size
            ),
            batch_window_days=defaults.batch_window_days,
            batch_span_days=defaults.batch_span_days,
        )


def headline(result: ReconciliationResult) -> None:
    """Show the headline numbers as metric tiles."""
    s = result.summary
    cols = st.columns(5)
    cols[0].metric("Match rate", f"{s['match_rate']:.1%}")
    cols[1].metric("Value reconciled", f"£{s['value_reconciled']:,.0f}")
    cols[2].metric("Value in discrepancy", f"£{s['value_in_discrepancy']:,.0f}")
    cols[3].metric("Unmatched records", f"{s['unmatched_records']:,}")
    cols[4].metric("Duplicates", f"{s['duplicate_records']:,}")


def records_tab(result: ReconciliationResult) -> None:
    """A filterable table of every record and its outcome."""
    records = result.results
    cols = st.columns(3)
    sources = cols[0].multiselect("Source", ["A", "B"], default=["A", "B"])
    statuses = cols[1].multiselect(
        "Status", sorted(records["status"].unique()), placeholder="All statuses"
    )
    categories = cols[2].multiselect(
        "Category", sorted(result.by_category["category"]), placeholder="All categories"
    )
    shown = records[records["source"].isin(sources)]
    if statuses:
        shown = shown[shown["status"].isin(statuses)]
    if categories:
        pattern = "|".join(categories)
        shown = shown[shown["category"].str.contains(pattern, regex=True)]
    st.caption(f"{len(shown):,} of {len(records):,} records")
    st.dataframe(shown, hide_index=True)


def main() -> None:
    """Assemble the page."""
    st.title("🧾 Financial Transaction Reconciliation")
    st.write(
        "Matches a **ledger** against a **bank statement** with a tiered SQL "
        "pipeline on DuckDB (exact → tolerant → duplicates → one-to-many batches), "
        "then explains every difference."
    )
    source_a, source_b, truth = sidebar()
    tolerances = tolerance_controls()
    if source_a is None:
        st.info("Upload a ledger CSV and a bank-statement CSV to begin.")
        return

    result = reconcile(source_a, source_b, tolerances)
    headline(result)

    records, pairs, about = st.tabs(["Records", "Matched pairs", "How it works"])
    with records:
        records_tab(result)
    with pairs:
        st.dataframe(result.pairs, hide_index=True)
    with about:
        st.markdown(
            "1. **Normalise** references, dates and amounts\n"
            "2. **Exact match**: `INNER JOIN` on reference + amount + date\n"
            "3. **Unmatched**: anti-join views of what is left\n"
            "4. **Tolerant match**: range join for fees, rounding and delays\n"
            "5. **Duplicates**: `GROUP BY … HAVING COUNT(*) > 1`\n"
            "6. **Batches**: recursive CTE searching for exact sums\n"
            "7. **Classify**: `CASE` labels and summary aggregates"
        )

    st.sidebar.header("Report")
    st.sidebar.download_button(
        "Download Excel report",
        report_excel_bytes(result),
        file_name="reconciliation_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.sidebar.download_button(
        "Download records (CSV)",
        result.results.to_csv(index=False),
        file_name="reconciliation_records.csv",
        mime="text/csv",
    )


main()
