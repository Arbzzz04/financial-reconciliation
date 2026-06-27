"""Synthetic reconciliation data generator -- core logic.

This module is the functional core behind the ``data/generate_data.py`` CLI. It
builds a hidden **base "truth" set** of transactions, then derives two sources that
*should* agree but don't -- ``source_a`` (the internal ledger) and ``source_b`` (the
bank statement) -- by injecting controlled discrepancies:

    * drops (a record missing from one side),
    * side-only records (bank fees and interest; ledger adjustments),
    * processor fees and rounding on amounts,
    * settlement-delay date shifts (in business days),
    * duplicates (a record posted twice),
    * reference-format variation (``INV-12`` / ``Inv 12`` / ``INV-000012``),
    * one-to-many batch settlements.

Alongside the two sources it emits the **ground truth**: which records truly pair
up, how each record should be classified, and the bounds the tolerant SQL pass must
cover. Because the answer key is known, the engine's accuracy becomes measurable.

Design rules honoured here (see ``docs/contracts.md``):
    * Money is always ``Decimal`` quantised through ``_money`` -- never float.
    * All structural randomness flows from one seeded ``numpy.Generator`` so a
      given seed reproduces the output exactly.
    * References are generated to stay unique after normalisation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

from recon.normalise import normalise_reference

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
# Ledger-side narrative per transaction type. Naming the counterparty gives the
# later fuzzy-text pass something real to compare with the bank's terse narrative.
LEDGER_NARRATIVE = {
    "invoice_payment": "Payment received from {counterparty}",
    "refund": "Refund issued to {counterparty}",
    "payout": "Supplier payout to {counterparty}",
    "transfer": "Transfer with {counterparty}",
}
LEDGER_ADJUSTMENT_REASONS = (
    "Accrual correction",
    "FX revaluation",
    "Write-off of small balance",
    "Reclass between accounts",
    "Petty cash top-up",
)
PREFIX_BY_TYPE = {
    "invoice_payment": "INV",
    "refund": "REF",
    "payout": "PAY",
    "transfer": "TRF",
}

_CENTS = Decimal("0.01")

# Discrepancy / status labels written to the ground truth. A record (or a 1:1 pair)
# can carry several discrepancy labels at once, e.g. "FEE;TIMING".
CLEAN = "CLEAN"
FEE = "FEE"
ROUNDING = "ROUNDING"
TIMING = "TIMING"
REF_FORMAT = "REF_FORMAT"
MISSING_FROM_A = "MISSING_FROM_A"  # on a B record whose ledger entry was dropped
MISSING_FROM_B = "MISSING_FROM_B"  # on an A record the bank never shows
BANK_FEE = "BANK_FEE"
BANK_INTEREST = "BANK_INTEREST"
LEDGER_ADJUSTMENT = "LEDGER_ADJUSTMENT"
DUPLICATE = "DUPLICATE"
BATCH_MEMBER = "BATCH_MEMBER"
BATCH_SETTLEMENT = "BATCH_SETTLEMENT"

# Match statuses: how a record should end up once reconciled.
STATUS_ONE_TO_ONE = "ONE_TO_ONE"
STATUS_BATCH = "BATCH"
STATUS_UNMATCHED = "UNMATCHED"
STATUS_DUPLICATE = "DUPLICATE"

# Reference prefixes used only by side-only and settlement records. They differ
# from PREFIX_BY_TYPE so these references can never collide with a base reference.
BANK_FEE_PREFIX = "BKF"
BANK_INTEREST_PREFIX = "INT"
LEDGER_ADJUSTMENT_PREFIX = "ADJ"
SETTLEMENT_PREFIX = "STL"

# Columns of the ground-truth outputs.
PAIR_COLUMNS = (
    "a_record_id",
    "b_record_id",
    "base_txn_id",
    "match_type",
    "batch_id",
    "discrepancies",
    "amount_delta",
    "date_shift_days",
)
LABEL_COLUMNS = (
    "record_id",
    "source",
    "base_txn_id",
    "status",
    "category",
    "duplicate_of",
)


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
    # Amount magnitudes: synthetic log-normal by default, or sampled (with
    # replacement) from a real transactions CSV when one is configured.
    seed_descriptions: list[str] | None = None
    if config.seed_csv is None:
        raw_magnitudes = rng.lognormal(mean=5.0, sigma=1.1, size=n)
    else:
        seed_amounts, seed_descriptions = load_seed_csv(
            config.seed_csv, config.seed_csv_columns
        )
        picks = rng.integers(0, len(seed_amounts), size=n)
        raw_magnitudes = seed_amounts[picks]
        if seed_descriptions is not None:
            seed_descriptions = [seed_descriptions[p] for p in picks]
    magnitudes = np.clip(
        raw_magnitudes, float(config.amount_min), float(config.amount_max)
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
        counterparty = fake.company()
        records.append(
            {
                "base_txn_id": f"TXN-{seq:06d}",
                "reference": f"{PREFIX_BY_TYPE[txn_type]}-{seq}",
                "amount": _money(float(magnitudes[i])) * sign,
                "direction": "credit" if sign > 0 else "debit",
                "txn_date": start + timedelta(days=int(day_offsets[i])),
                "counterparty": counterparty,
                "txn_type": txn_type,
                "currency": config.currency,
                "description": (
                    seed_descriptions[i]
                    if seed_descriptions is not None
                    else LEDGER_NARRATIVE[txn_type].format(counterparty=counterparty)
                ),
            }
        )
    return pd.DataFrame.from_records(records, columns=list(BASE_COLUMNS))


def load_seed_csv(
    path: Path, columns: dict[str, str]
) -> tuple[np.ndarray, list[str] | None]:
    """Load amount magnitudes (and optionally descriptions) from a real CSV.

    The seed source is pluggable: any transactions CSV works, as long as
    ``columns`` says which of its columns holds the amount (and, optionally, the
    description). Amounts may contain currency symbols or thousands separators;
    only their magnitude is used, because the sign comes from the transaction type.

    Args:
        path: Path to the CSV file.
        columns: Maps our field names to the CSV's column names. ``"amount"`` is
            required; ``"description"`` is optional. Missing keys default to the
            field name itself (e.g. a CSV that already has an ``amount`` column).

    Returns:
        A tuple of (non-zero amount magnitudes as a float array, descriptions
        aligned with them, or ``None`` if no description column was mapped).

    Raises:
        ValueError: If the amount column is missing or has no usable values.
    """
    frame = pd.read_csv(path)
    amount_col = columns.get("amount", "amount")
    if amount_col not in frame.columns:
        raise ValueError(f"seed CSV has no amount column {amount_col!r}")
    cleaned = frame[amount_col].astype(str).str.replace(r"[^0-9.\-]", "", regex=True)
    amounts = pd.to_numeric(cleaned, errors="coerce").abs()
    usable = amounts.notna() & (amounts > 0)
    if not usable.any():
        raise ValueError("seed CSV has no non-zero numeric amounts")

    description_col = columns.get("description")
    descriptions: list[str] | None = None
    if description_col is not None:
        if description_col not in frame.columns:
            raise ValueError(f"seed CSV has no description column {description_col!r}")
        descriptions = frame.loc[usable, description_col].astype(str).tolist()
    return amounts[usable].to_numpy(dtype=float), descriptions


# --- Discrepancy injection --------------------------------------------------


def vary_reference(reference: str, rng: np.random.Generator) -> str:
    """Rewrite a reference in a different but *equivalent* format.

    Only invertible edits are used -- separators, spacing, case and zero-padding --
    so the variant always normalises back to the same canonical key (see
    ``docs/contracts.md``). This is what the exact-match pass must see through.

    Args:
        reference: A base reference such as ``"INV-12"``.
        rng: The shared seeded RNG.

    Returns:
        An equivalent variant, e.g. ``"INV12"``, ``"Inv 12"`` or ``"INV-000012"``.
    """
    prefix, number = reference.split("-", 1)
    styles = (
        f"{prefix}{number}",
        f"{prefix.title()} {number}",
        f"{prefix.lower()}-{number}",
        f"{prefix}/{number}",
        f"{prefix}-{number.zfill(6)}",
        f"{prefix} {number}",
    )
    return styles[int(rng.integers(len(styles)))]


def processor_fee(
    amount: Decimal, config: GeneratorConfig, rng: np.random.Generator
) -> Decimal:
    """Draw the fee a payment processor deducts from one transaction.

    The fee is a percentage of the amount (``fee_pct_bps`` basis points, jittered by
    up to ``fee_pct_jitter_bps``) plus, sometimes, a fixed charge. One basis point is
    0.01%, so 30 bps is 0.30%.

    Args:
        amount: The transaction amount (sign is ignored).
        config: The generator configuration holding the fee model.
        rng: The shared seeded RNG.

    Returns:
        A positive fee of at least 1p, quantised to money.
    """
    jitter = int(
        rng.integers(-config.fee_pct_jitter_bps, config.fee_pct_jitter_bps + 1)
    )
    bps = config.fee_pct_bps + jitter
    fee = _money(abs(amount) * Decimal(bps) / Decimal(10_000))
    if rng.random() < config.fee_fixed_prob:
        fee += config.fee_fixed
    return max(fee, _CENTS)


def shift_business_days(day: date, shift: int) -> date:
    """Move a date forward by a number of business days (Mon-Fri).

    Banks settle on business days, so a 2-day settlement delay from a Friday lands
    on Tuesday. A weekend start date is first rolled forward to Monday.

    Args:
        day: The starting date.
        shift: Business days to move forward (``0`` only rolls off a weekend).

    Returns:
        The shifted date.
    """
    shifted = np.busday_offset(np.datetime64(day, "D"), shift, roll="forward")
    return shifted.astype(object)


def business_days_between(start: date, end: date) -> int:
    """Count the business-day shift from ``start`` to ``end``.

    This is the inverse of ``shift_business_days`` for ``shift >= 1``, and is how
    ``date_shift_days`` in the ground truth is measured.

    Args:
        start: The earlier (ledger) date.
        end: The later (bank) date.

    Returns:
        The number of business days in ``[start, end)``.
    """
    return int(np.busday_count(np.datetime64(start, "D"), np.datetime64(end, "D")))


def _bank_description(counterparty: str, reference: str) -> str:
    """Format a bank-statement narrative: terse, upper-case, reference appended."""
    return f"{counterparty.upper()[:18].strip()} {reference}"


@dataclass
class GeneratedData:
    """Everything one generator run produces.

    Attributes:
        source_a: The internal-ledger records the engine sees (``SOURCE_COLUMNS``).
        source_b: The bank-statement records the engine sees (``SOURCE_COLUMNS``).
        base: The hidden base truth set (``BASE_COLUMNS``).
        ground_truth_pairs: One row per true ``(a, b)`` pair (``PAIR_COLUMNS``);
            batch settlements are pre-exploded to one row per member.
        record_labels: One row per record in either source (``LABEL_COLUMNS``).
        metadata: Config, counts by category, and the tolerance bounds.
    """

    source_a: pd.DataFrame
    source_b: pd.DataFrame
    base: pd.DataFrame
    ground_truth_pairs: pd.DataFrame
    record_labels: pd.DataFrame
    metadata: dict[str, object]


def generate_datasets(config: GeneratorConfig) -> GeneratedData:
    """Run the full generator: base truth set -> two divergent sources + truth.

    Steps, in order (all randomness flows from one RNG seeded by ``config.seed``):

    1. Build the base truth set.
    2. Give every base transaction one *fate*: dropped from A, dropped from B, part
       of a batch settlement, or present on both sides as a 1:1 pair. Fates are
       drawn once, so the categories never overlap.
    3. For each 1:1 pair, perturb the bank side: processor fee or rounding on the
       amount, a settlement delay on the date, and reference reformatting.
    4. Group batch candidates (same direction) into settlements of 2-5 members.
    5. Add side-only records and duplicates.
    6. Shuffle each source and only *then* assign record IDs, so row order and IDs
       leak nothing about which records match.

    Args:
        config: The generator configuration.

    Returns:
        The generated sources, base set, ground truth and run metadata.
    """
    rng = build_rng(config.seed)
    fake = build_faker(rng)
    base = make_base_transactions(config, rng, fake)

    # Records are built with internal keys ("a3", "b7"); public record IDs are
    # assigned after shuffling in _finalise().
    a_rows: list[dict[str, object]] = []
    b_rows: list[dict[str, object]] = []
    pairs: list[dict[str, object]] = []
    labels: dict[str, dict[str, object]] = {}

    def add(side: str, row: dict[str, object], **label: object) -> str:
        rows = a_rows if side == "A" else b_rows
        key = f"{side.lower()}{len(rows)}"
        rows.append({"_key": key, **row})
        labels[key] = {"source": side, "duplicate_of": None, **label}
        return key

    def source_row(base_row: pd.Series, **overrides: object) -> dict[str, object]:
        row = {
            "reference": base_row["reference"],
            "amount": base_row["amount"],
            "txn_date": base_row["txn_date"],
            "currency": base_row["currency"],
            "description": base_row["description"],
        }
        row.update(overrides)
        return row

    # Step 2: one fate per base transaction, from a single uniform draw.
    fate_draw = rng.random(len(base))
    drop_a_cut = config.drop_a_rate
    drop_b_cut = drop_a_cut + config.drop_b_rate
    batch_cut = drop_b_cut + config.batch_rate

    batch_candidates: list[int] = []
    one_to_one: list[int] = []
    for i, base_row in base.iterrows():
        txn_id = base_row["base_txn_id"]
        if fate_draw[i] < drop_a_cut:
            b_ref = base_row["reference"]
            add(
                "B",
                source_row(
                    base_row,
                    description=_bank_description(base_row["counterparty"], b_ref),
                ),
                base_txn_id=txn_id,
                status=STATUS_UNMATCHED,
                category=MISSING_FROM_A,
            )
        elif fate_draw[i] < drop_b_cut:
            add(
                "A",
                source_row(base_row),
                base_txn_id=txn_id,
                status=STATUS_UNMATCHED,
                category=MISSING_FROM_B,
            )
        elif fate_draw[i] < batch_cut:
            batch_candidates.append(i)
        else:
            one_to_one.append(i)

    # Step 4 (done before step 3 so leftovers from batching become 1:1 pairs):
    # a processor settles once per period, so batch candidates are grouped by
    # direction *and* ISO week, then cut into batches of 2-5 in date order.
    # Members of a batch are therefore never more than a few days apart.
    batches: list[list[int]] = []
    periods: dict[tuple[str, int, int], list[int]] = {}
    for i in sorted(batch_candidates, key=lambda i: (base.at[i, "txn_date"], i)):
        year, week, _ = base.at[i, "txn_date"].isocalendar()
        periods.setdefault((base.at[i, "direction"], year, week), []).append(i)
    for group in periods.values():
        while group:
            size = int(rng.integers(config.batch_min_size, config.batch_max_size + 1))
            chunk, group = group[:size], group[size:]
            if len(chunk) < config.batch_min_size:
                one_to_one.extend(chunk)  # too few left to form a batch
            else:
                batches.append(chunk)
    one_to_one.sort()

    # Step 3: 1:1 pairs, with the bank side perturbed.
    pair_keys: list[tuple[str, str]] = []
    for i in one_to_one:
        base_row = base.loc[i]
        a_amount = base_row["amount"]
        a_date = base_row["txn_date"]
        a_ref = base_row["reference"]
        b_ref = base_row["reference"]
        discrepancies: list[str] = []

        b_amount = a_amount
        if rng.random() < config.amount_perturb_rate:
            if rng.random() < config.rounding_only_fraction:
                b_amount = a_amount + (_CENTS if rng.random() < 0.5 else -_CENTS)
                discrepancies.append(ROUNDING)
            else:
                # The processor deducts its fee: less money in, or more money out.
                b_amount = a_amount - processor_fee(a_amount, config, rng)
                discrepancies.append(FEE)

        b_date = a_date
        if rng.random() < config.date_shift_rate:
            shift = int(rng.integers(1, config.settlement_window_days + 1))
            b_date = shift_business_days(a_date, shift)
            discrepancies.append(TIMING)

        if rng.random() < config.reference_format_rate:
            # Vary whichever side the formatting difference shows up on.
            if rng.random() < 0.5:
                a_ref = vary_reference(a_ref, rng)
            else:
                b_ref = vary_reference(b_ref, rng)
            discrepancies.append(REF_FORMAT)

        category = ";".join(discrepancies) if discrepancies else CLEAN
        label = {
            "base_txn_id": base_row["base_txn_id"],
            "status": STATUS_ONE_TO_ONE,
            "category": category,
        }
        a_key = add("A", source_row(base_row, reference=a_ref), **label)
        b_key = add(
            "B",
            source_row(
                base_row,
                reference=b_ref,
                amount=b_amount,
                txn_date=b_date,
                description=_bank_description(base_row["counterparty"], b_ref),
            ),
            **label,
        )
        pair_keys.append((a_key, b_key))
        pairs.append(
            {
                "_a": a_key,
                "_b": b_key,
                "base_txn_id": base_row["base_txn_id"],
                "match_type": STATUS_ONE_TO_ONE,
                "batch_id": None,
                "discrepancies": category,
                "amount_delta": b_amount - a_amount,
                "date_shift_days": business_days_between(a_date, b_date),
            }
        )

    # Batch settlements: members stay separate in A; B shows one summed record.
    for batch_number, members in enumerate(batches, start=1):
        batch_id = f"BATCH-{batch_number:04d}"
        settle_ref = f"{SETTLEMENT_PREFIX}-{batch_number}"
        member_rows = base.loc[members]
        latest = max(member_rows["txn_date"])
        settle_date = shift_business_days(
            latest, int(rng.integers(0, config.settlement_window_days + 1))
        )
        settle_amount = sum(member_rows["amount"], Decimal("0.00"))
        b_key = add(
            "B",
            {
                "reference": settle_ref,
                "amount": settle_amount,
                "txn_date": settle_date,
                "currency": config.currency,
                "description": f"BATCH SETTLEMENT {len(members)} ITEMS {settle_ref}",
            },
            base_txn_id=None,
            status=STATUS_BATCH,
            category=BATCH_SETTLEMENT,
        )
        for i in members:
            base_row = base.loc[i]
            a_key = add(
                "A",
                source_row(base_row),
                base_txn_id=base_row["base_txn_id"],
                status=STATUS_BATCH,
                category=BATCH_MEMBER,
            )
            pairs.append(
                {
                    "_a": a_key,
                    "_b": b_key,
                    "base_txn_id": base_row["base_txn_id"],
                    "match_type": STATUS_BATCH,
                    "batch_id": batch_id,
                    "discrepancies": BATCH_MEMBER,
                    "amount_delta": None,
                    "date_shift_days": business_days_between(
                        base_row["txn_date"], settle_date
                    ),
                }
            )

    # Step 5a: side-only records -- things only one system knows about.
    start = date.fromisoformat(config.date_start)
    span = (date.fromisoformat(config.date_end) - start).days + 1

    def random_day() -> date:
        return start + timedelta(days=int(rng.integers(0, span)))

    n = config.n_transactions
    for k in range(1, round(n * config.side_only_b_rate) + 1):
        if rng.random() < 0.6:
            ref, amount = f"{BANK_FEE_PREFIX}-{k}", -_money(rng.uniform(1, 30))
            category, text = BANK_FEE, "ACCOUNT FEE"
        else:
            ref, amount = f"{BANK_INTEREST_PREFIX}-{k}", _money(rng.uniform(0.01, 50))
            category, text = BANK_INTEREST, "INTEREST PAID"
        add(
            "B",
            {
                "reference": ref,
                "amount": amount,
                "txn_date": random_day(),
                "currency": config.currency,
                "description": f"{text} {ref}",
            },
            base_txn_id=None,
            status=STATUS_UNMATCHED,
            category=category,
        )
    for k in range(1, round(n * config.side_only_a_rate) + 1):
        sign = 1 if rng.random() < 0.5 else -1
        ref = f"{LEDGER_ADJUSTMENT_PREFIX}-{k}"
        add(
            "A",
            {
                "reference": ref,
                "amount": _money(rng.uniform(1, 500)) * sign,
                "txn_date": random_day(),
                "currency": config.currency,
                "description": "Manual adjustment: "
                + LEDGER_ADJUSTMENT_REASONS[
                    int(rng.integers(len(LEDGER_ADJUSTMENT_REASONS)))
                ],
            },
            base_txn_id=None,
            status=STATUS_UNMATCHED,
            category=LEDGER_ADJUSTMENT,
        )

    # Step 5b: duplicates -- an exact repeat of one side of a clean-or-not 1:1
    # pair. The original keeps its true match; the copy should end up flagged.
    for a_key, b_key in pair_keys:
        if rng.random() < config.duplicate_rate:
            side, key = ("A", a_key) if rng.random() < 0.5 else ("B", b_key)
            rows = a_rows if side == "A" else b_rows
            original = rows[int(key[1:])]
            copy = {k: v for k, v in original.items() if k != "_key"}
            new_key = add(
                side,
                copy,
                base_txn_id=labels[key]["base_txn_id"],
                status=STATUS_DUPLICATE,
                category=DUPLICATE,
            )
            labels[new_key]["duplicate_of"] = key

    return _finalise(config, rng, base, a_rows, b_rows, pairs, labels)


def _finalise(
    config: GeneratorConfig,
    rng: np.random.Generator,
    base: pd.DataFrame,
    a_rows: list[dict[str, object]],
    b_rows: list[dict[str, object]],
    pairs: list[dict[str, object]],
    labels: dict[str, dict[str, object]],
) -> GeneratedData:
    """Shuffle each source, assign public record IDs, and assemble the outputs."""
    id_of: dict[str, str] = {}

    def to_frame(rows: list[dict[str, object]], prefix: str) -> pd.DataFrame:
        order = rng.permutation(len(rows))
        shuffled = [rows[j] for j in order]
        for position, row in enumerate(shuffled, start=1):
            id_of[row["_key"]] = f"{prefix}-{position:06d}"
        frame = pd.DataFrame.from_records(shuffled)
        frame.insert(0, "record_id", [id_of[r["_key"]] for r in shuffled])
        return frame[list(SOURCE_COLUMNS)].reset_index(drop=True)

    source_a = to_frame(a_rows, "A")
    source_b = to_frame(b_rows, "B")

    truth_pairs = pd.DataFrame.from_records(
        [
            {"a_record_id": id_of[p["_a"]], "b_record_id": id_of[p["_b"]], **p}
            for p in pairs
        ]
    )[list(PAIR_COLUMNS)]
    truth_pairs = truth_pairs.sort_values(["a_record_id", "b_record_id"])
    truth_pairs = truth_pairs.reset_index(drop=True)

    record_labels = pd.DataFrame.from_records(
        [
            {
                "record_id": id_of[key],
                **label,
                "duplicate_of": (
                    id_of[label["duplicate_of"]] if label["duplicate_of"] else None
                ),
            }
            for key, label in labels.items()
        ]
    )[list(LABEL_COLUMNS)]
    record_labels = record_labels.sort_values("record_id").reset_index(drop=True)

    one_to_one = truth_pairs[truth_pairs["match_type"] == STATUS_ONE_TO_ONE]
    max_delta = max(
        (abs(d) for d in one_to_one["amount_delta"]), default=Decimal("0.00")
    )
    config_dict = {
        k: (str(v) if isinstance(v, Decimal | Path) else v)
        for k, v in asdict(config).items()
    }
    metadata: dict[str, object] = {
        "config": config_dict,
        "counts": {
            "base_transactions": len(base),
            "source_a_records": len(source_a),
            "source_b_records": len(source_b),
            "ground_truth_pairs": len(truth_pairs),
            "one_to_one_pairs": len(one_to_one),
            "batch_settlements": int(
                (record_labels["category"] == BATCH_SETTLEMENT).sum()
            ),
            "by_category": _count_categories(record_labels),
        },
        # Contract 2 (docs/contracts.md): the tolerant pass must cover these.
        "tolerances": {
            "max_amount_delta_abs": str(max_delta),
            "max_date_shift_days": config.settlement_window_days,
            "max_fee_pct_bps": config.fee_pct_bps + config.fee_pct_jitter_bps,
            "fee_pct_bps": config.fee_pct_bps,
            "fee_fixed": str(config.fee_fixed),
        },
    }
    return GeneratedData(
        source_a=source_a,
        source_b=source_b,
        base=base,
        ground_truth_pairs=truth_pairs,
        record_labels=record_labels,
        metadata=metadata,
    )


def _count_categories(record_labels: pd.DataFrame) -> dict[str, int]:
    """Count records per discrepancy label (a record may carry several)."""
    counts: dict[str, int] = {}
    for category in record_labels["category"]:
        for part in category.split(";"):
            counts[part] = counts.get(part, 0) + 1
    return dict(sorted(counts.items()))


def write_outputs(data: GeneratedData, out_dir: Path) -> dict[str, Path]:
    """Write a generator run to disk.

    Layout (``out_dir`` defaults to ``data/samples/``, which is git-ignored)::

        source_a.csv                 # what the engine sees: the ledger
        source_b.csv                 # what the engine sees: the bank statement
        truth/base_transactions.csv  # hidden base set
        truth/ground_truth_pairs.csv # the answer key, one row per true pair
        truth/record_labels.csv      # expected status + category per record
        truth/run_metadata.json      # config, counts and tolerance bounds

    Money is written from ``Decimal`` as plain text (e.g. ``-123.45``), so nothing
    passes through floating point on the way to disk.

    Args:
        data: The output of ``generate_datasets``.
        out_dir: The directory to write into (created if needed).

    Returns:
        A mapping of output name to the path written.
    """
    truth_dir = out_dir / "truth"
    truth_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "source_a": out_dir / "source_a.csv",
        "source_b": out_dir / "source_b.csv",
        "base": truth_dir / "base_transactions.csv",
        "ground_truth_pairs": truth_dir / "ground_truth_pairs.csv",
        "record_labels": truth_dir / "record_labels.csv",
        "metadata": truth_dir / "run_metadata.json",
    }
    data.source_a.to_csv(paths["source_a"], index=False)
    data.source_b.to_csv(paths["source_b"], index=False)
    data.base.to_csv(paths["base"], index=False)
    data.ground_truth_pairs.to_csv(paths["ground_truth_pairs"], index=False)
    data.record_labels.to_csv(paths["record_labels"], index=False)
    paths["metadata"].write_text(json.dumps(data.metadata, indent=2) + "\n")
    return paths


def check_reference_contract(data: GeneratedData) -> None:
    """Assert every true 1:1 pair shares one normalised reference key.

    A cheap self-check the CLI runs after generating: if a reference variant ever
    stopped normalising back to its original, exact matching would silently fail.

    Args:
        data: The output of ``generate_datasets``.

    Raises:
        AssertionError: If any 1:1 pair's references normalise differently.
    """
    a_refs = data.source_a.set_index("record_id")["reference"]
    b_refs = data.source_b.set_index("record_id")["reference"]
    one_to_one = data.ground_truth_pairs[
        data.ground_truth_pairs["match_type"] == STATUS_ONE_TO_ONE
    ]
    for a_id, b_id in zip(
        one_to_one["a_record_id"], one_to_one["b_record_id"], strict=True
    ):
        a_key = normalise_reference(a_refs[a_id])
        b_key = normalise_reference(b_refs[b_id])
        if a_key != b_key:
            raise AssertionError(f"{a_id}/{b_id}: {a_key!r} != {b_key!r}")
