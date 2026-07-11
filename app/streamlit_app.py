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
import plotly.express as px
import streamlit as st

try:
    import recon  # noqa: F401  (installed with `pip install -e .`)
except ModuleNotFoundError:  # e.g. on Streamlit Community Cloud
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from recon.datagen import GeneratedData, GeneratorConfig, generate_datasets
from recon.db import REQUIRED_COLUMNS, read_source
from recon.metrics import classification_accuracy, score_duplicates, score_matches
from recon.pipeline import ReconciliationResult, Tolerances, run_reconciliation
from recon.report import report_excel_bytes

st.set_page_config(page_title="Reconciliation Engine", page_icon="🧾", layout="wide")

# Chart colours: one series -> slot 1; two series -> slots 1 and 2 (a pair that
# stays distinguishable under colour-vision deficiency).
BLUE = "#2a78d6"
ORANGE = "#eb6834"
STATUS_LABELS = {
    "ONE_TO_ONE": "Matched 1:1",
    "BATCH": "Matched in a batch",
    "DUPLICATE": "Duplicate",
    "UNMATCHED": "Unmatched",
}


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


def _bar(frame: pd.DataFrame, x: str, y: str, text: str, title: str):
    """A thin horizontal bar chart in one colour, values labelled on the bars."""
    fig = px.bar(frame, x=x, y=y, orientation="h", text=text, title=title)
    fig.update_traces(marker_color=BLUE, textposition="outside", cliponaxis=False)
    fig.update_layout(
        yaxis={"categoryorder": "total ascending", "title": None},
        xaxis={"title": None, "showgrid": True},
        margin={"l": 10, "r": 40, "t": 50, "b": 10},
        bargap=0.35,
        height=max(260, 34 * len(frame) + 90),
    )
    return fig


def overview_tab(result: ReconciliationResult) -> None:
    """Charts: status breakdown, categories by count and value, value over time."""
    records = result.results
    status = (
        records["status"].map(STATUS_LABELS).value_counts().rename_axis("status")
    ).reset_index(name="records")
    status["label"] = [f"{n:,} ({n / len(records):.0%})" for n in status["records"]]
    st.plotly_chart(
        _bar(status, "records", "status", "label", "Where every record ended up"),
        theme="streamlit",
    )

    categories = result.by_category.copy()
    categories["value_label"] = [f"£{v:,.0f}" for v in categories["total_value"]]
    left, right = st.columns(2)
    left.plotly_chart(
        _bar(categories, "records", "category", "records", "Categories by count"),
        theme="streamlit",
    )
    right.plotly_chart(
        _bar(
            categories, "total_value", "category", "value_label", "Categories by value"
        ),
        theme="streamlit",
    )

    weekly = records.assign(
        week=pd.to_datetime(records["txn_date"]).dt.to_period("W").dt.start_time,
        outcome=records["status"]
        .isin(["ONE_TO_ONE", "BATCH"])
        .map({True: "Reconciled", False: "Unreconciled"}),
        value=records["amount"].astype(float).abs(),
    )
    weekly = weekly.groupby(["week", "outcome"], as_index=False)["value"].sum()
    fig = px.bar(
        weekly,
        x="week",
        y="value",
        color="outcome",
        title="Reconciled vs unreconciled value, by week",
        color_discrete_map={"Reconciled": BLUE, "Unreconciled": ORANGE},
        category_orders={"outcome": ["Reconciled", "Unreconciled"]},
    )
    fig.update_layout(
        xaxis_title=None,
        yaxis_title="Value (£)",
        legend_title=None,
        bargap=0.25,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    st.plotly_chart(fig, theme="streamlit")


def accuracy_panel(result: ReconciliationResult, truth: GeneratedData) -> None:
    """Precision, recall and F1 against the sample data's hidden ground truth."""
    matches = score_matches(
        result.matches, truth.ground_truth_pairs, truth.record_labels
    )
    duplicates = score_duplicates(result.duplicates, truth.record_labels)
    classification = classification_accuracy(result.results, truth.record_labels)
    st.subheader("Accuracy against the hidden ground truth")
    cols = st.columns(4)
    cols[0].metric("Precision", f"{matches['overall'].precision:.2%}")
    cols[1].metric("Recall", f"{matches['overall'].recall:.2%}")
    cols[2].metric("F1", f"{matches['overall'].f1:.4f}")
    cols[3].metric(
        "Records classified correctly", f"{classification['category_accuracy']:.2%}"
    )
    table = pd.DataFrame(
        {
            name: score.as_dict()
            for name, score in {
                "All matches": matches["overall"],
                "One-to-one": matches["ONE_TO_ONE"],
                "Batch settlements": matches["BATCH"],
                "Duplicate detection": duplicates,
            }.items()
        }
    ).T
    table = table.rename(
        columns={
            "true_positives": "Correct",
            "false_positives": "Wrong",
            "false_negatives": "Missed",
            "precision": "Precision",
            "recall": "Recall",
            "f1": "F1",
        }
    )
    percent = st.column_config.NumberColumn(format="percent")
    st.dataframe(
        table,
        column_config={
            "_index": "Pass",
            "Precision": percent,
            "Recall": percent,
            "F1": st.column_config.NumberColumn(format="%.4f"),
        },
    )


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
    if truth is not None:
        accuracy_panel(result, truth)

    overview, records, pairs, about = st.tabs(
        ["Overview", "Records", "Matched pairs", "How it works"]
    )
    with overview:
        overview_tab(result)
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
