"""Smoke tests for the Streamlit demo, using Streamlit's headless AppTest."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


def test_app_runs_on_sample_data():
    app = AppTest.from_file(APP, default_timeout=120).run()
    assert not app.exception
    labels = [metric.label for metric in app.metric]
    assert "Match rate" in labels


def test_app_prompts_for_uploads():
    app = AppTest.from_file(APP, default_timeout=120).run()
    app.sidebar.radio[0].set_value("Upload two CSVs").run()
    assert not app.exception
    assert any("Upload a ledger" in info.value for info in app.info)
