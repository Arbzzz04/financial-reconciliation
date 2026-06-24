"""Smoke tests: the package imports and is wired up correctly.

These run green from Phase 1 so the test command works from day one; real
reconciliation tests arrive alongside the engine in later phases.
"""

import recon


def test_package_exposes_version() -> None:
    """``recon.__version__`` should be a non-empty string."""
    assert isinstance(recon.__version__, str)
    assert recon.__version__
