"""Synthetic reconciliation data generator -- core logic.

This module is the functional core behind the ``data/generate_data.py`` CLI. In
this first slice it provides the configuration object, the single seeded RNG, the
money-quantisation helper, and the **base "truth" set** of transactions. Later
slices add the discrepancy injectors, batch settlements, ground-truth emission,
and the output writers.

Design rules honoured here (see ``docs/contracts.md``):
    * Money is always ``Decimal`` quantised through ``_money`` -- never float.
    * All structural randomness flows from one seeded ``numpy.Generator`` so a
      given seed reproduces the output exactly.
    * References are generated to stay unique after normalisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

# --- Schema constants ------------------------------------------------------

# Columns of the hidden base "truth" set (never handed to the engine).
BASE_COLUMNS = (
    "base_txn_id",
    "reference",
    "amount",
    "direction",
    "txn_date",
    "counterparty",
    "txn_type",
    "currency",
    "description",
)

# Columns the engine actually sees in source_a.csv / source_b.csv.
SOURCE_COLUMNS = (
    "record_id",
    "reference",
    "amount",
    "txn_date",
    "currency",
    "description",
)

# Transaction types, their sampling weights, sign convention, and reference prefix.
TXN_TYPES = ("invoice_payment", "refund", "payout", "transfer")
TXN_TYPE_WEIGHTS = (0.55, 0.15, 0.20, 0.10)
CREDIT_TYPES = frozenset({"invoice_payment"})  # money in (+); others are money out (-)
PREFIX_BY_TYPE = {
    "invoice_payment": "INV",
    "refund": "REF",
    "payout": "PAY",
    "transfer": "TRF",
}

_CENTS = Decimal("0.01")


def _money(value: Decimal | int | float | str) -> Decimal:
    """Quantise a value to 2-decimal-place money using round-half-up.

    Every monetary amount passes through here so rounding is consistent and never
    depends on Python's process-global decimal context (which defaults to
    round-half-*even*). Floats are converted via ``str`` first to avoid binary
    floating-point artefacts (e.g. ``2.675`` quantises to ``2.68``, not ``2.67``).

    Args:
        value: The amount to quantise.

    Returns:
        The value as a ``Decimal`` with exactly two decimal places.
    """
    if isinstance(value, float):
        value = str(value)
    return Decimal(value).quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class GeneratorConfig:
    """Every knob controlling synthetic reconciliation-data generation.

    Rates are probabilities in ``[0, 1]``. The structural rates (drops + batch)
    must leave headroom (sum ``< 1``) so there are clean matches to anchor scoring.
    """

    # Core
    n_transactions: int = 2000
    seed: int = 42
    currency: str = "GBP"
    date_start: str = "2024-01-01"
    date_end: str = "2024-03-31"
    amount_min: Decimal = Decimal("5.00")
    amount_max: Decimal = Decimal("10000.00")
    settlement_window_days: int = 3
    # Per-discrepancy rates
    drop_a_rate: float = 0.04
    drop_b_rate: float = 0.04
    side_only_a_rate: float = 0.02
    side_only_b_rate: float = 0.05
    amount_perturb_rate: float = 0.12
    rounding_only_fraction: float = 0.25
    date_shift_rate: float = 0.15
    duplicate_rate: float = 0.03
    reference_format_rate: float = 0.20
    batch_rate: float = 0.06
    batch_min_size: int = 2
    batch_max_size: int = 5
    # Processor-fee model
    fee_pct_bps: int = 30
    fee_pct_jitter_bps: int = 10
    fee_fixed: Decimal = Decimal("0.20")
    fee_fixed_prob: float = 0.5
    # Pluggable real-CSV seed (wired up in a later slice)
    seed_csv: Path | None = None
    seed_csv_columns: dict[str, str] = field(default_factory=dict)
    # Documented later-phase flags (off in Phase 2)
    flip_source_b_sign: bool = False
    multi_currency: bool = False
    asymmetric_schema: bool = False

    def __post_init__(self) -> None:
        """Validate that rates are probabilities and leave room for clean matches."""
        rate_fields = (
            "drop_a_rate",
            "drop_b_rate",
            "side_only_a_rate",
            "side_only_b_rate",
            "amount_perturb_rate",
            "rounding_only_fraction",
            "date_shift_rate",
            "duplicate_rate",
            "reference_format_rate",
            "batch_rate",
            "fee_fixed_prob",
        )
        for name in rate_fields:
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value!r}")
        if self.n_transactions <= 0:
            raise ValueError("n_transactions must be positive")
        if self.drop_a_rate + self.drop_b_rate + self.batch_rate >= 1.0:
            raise ValueError("drops + batch_rate must sum to < 1 (need clean matches)")
        if not 2 <= self.batch_min_size <= self.batch_max_size:
            raise ValueError("require 2 <= batch_min_size <= batch_max_size")


def build_rng(seed: int) -> np.random.Generator:
    """Create the single seeded RNG that drives all structural randomness.

    Args:
        seed: The integer seed; a given seed reproduces the structural output.

    Returns:
        A NumPy ``Generator``.
    """
    return np.random.default_rng(seed)


def build_faker(rng: np.random.Generator) -> Faker:
    """Create a Faker instance seeded *from* the shared RNG.

    Faker produces only free-text columns (descriptions, counterparties), which
    are deliberately excluded from determinism guarantees; seeding it from the same
    RNG still keeps a run reproducible for a fixed Faker version.

    Args:
        rng: The shared seeded RNG.

    Returns:
        A locale-pinned (``en_GB``) seeded Faker instance.
    """
    fake = Faker("en_GB")
    fake.seed_instance(int(rng.integers(0, 2**32 - 1)))
    return fake


def make_base_transactions(
    config: GeneratorConfig,
    rng: np.random.Generator,
    fake: Faker,
) -> pd.DataFrame:
    """Build the canonical "truth" set of transactions before any divergence.

    Amounts follow a clipped log-normal distribution (many small, a few large),
    are signed by their transaction type's direction, and are stored as ``Decimal``
    so no floating-point error ever touches the money. References are unique and
    stay unique after normalisation.

    Args:
        config: The generator configuration (size, date window, amount bounds...).
        rng: The single seeded RNG driving all draws.
        fake: A Faker instance, already seeded from ``rng``.

    Returns:
        A DataFrame with the columns in ``BASE_COLUMNS``; ``amount`` holds
        ``Decimal`` values and ``txn_date`` holds ``datetime.date`` values.
    """
    n = config.n_transactions
    type_index = rng.choice(len(TXN_TYPES), size=n, p=TXN_TYPE_WEIGHTS)
    magnitudes = np.clip(
        rng.lognormal(mean=5.0, sigma=1.1, size=n),
        float(config.amount_min),
        float(config.amount_max),
    )
    start = date.fromisoformat(config.date_start)
    end = date.fromisoformat(config.date_end)
    day_offsets = rng.integers(0, (end - start).days + 1, size=n)

    records: list[dict[str, object]] = []
    for i in range(n):
        txn_type = TXN_TYPES[type_index[i]]
        if txn_type == "transfer":
            sign = 1 if rng.random() < 0.5 else -1
        else:
            sign = 1 if txn_type in CREDIT_TYPES else -1
        seq = i + 1
        records.append(
            {
                "base_txn_id": f"TXN-{seq:06d}",
                "reference": f"{PREFIX_BY_TYPE[txn_type]}-{seq}",
                "amount": _money(float(magnitudes[i])) * sign,
                "direction": "credit" if sign > 0 else "debit",
                "txn_date": start + timedelta(days=int(day_offsets[i])),
                "counterparty": fake.company(),
                "txn_type": txn_type,
                "currency": config.currency,
                "description": fake.sentence(nb_words=6),
            }
        )
    return pd.DataFrame.from_records(records, columns=list(BASE_COLUMNS))
