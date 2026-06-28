# Reconciliation contracts

This file pins three small **contracts** that the data generator (Phase 2) and the
SQL passes (Phases 3–6) must agree on. They are written down here, separately from
any one piece of code, because the generator is built *before* the SQL exists — and
if the two sides quietly disagree, matches vanish and the accuracy metrics blame the
engine for what is really a specification mismatch. Each contract names the code that
implements it and the code that must mirror it.

---

## 1. Reference normalisation

**Implemented by:** [`src/recon/normalise.py`](../src/recon/normalise.py) →
`normalise_reference()`.
**Must be mirrored by:** `sql/01_normalise.sql` (Phase 3), rule-for-rule.

A reference is normalised by applying, **in order**:

1. **Strip non-alphanumerics** — remove every character that is not a letter or a
   digit (spaces, hyphens, slashes, punctuation).
2. **Upper-case** the result.
3. **Strip leading zeros of each maximal digit run** — `0012` → `12`; an all-zero run
   collapses to a single `0`.

Worked vectors (these are asserted in `tests/test_normalise.py`):

| Raw | Canonical |
|---|---|
| `INV-123` | `INV123` |
| `INV123` | `INV123` |
| `Inv 123` | `INV123` |
| `Inv 000123` | `INV123` |
| `INV-0001` | `INV1` |
| `PAY/000` | `PAY0` |
| `TRF-10-20` | `TRF1020` |

**Generator obligation:** base references are generated so they remain **unique after
normalisation** (no zero-padding in the canonical form). The reference-format injector
may only apply *invertible* edits — separator/spacing/case changes and zero-padding —
so the canonical key is preserved. A rare deliberate typo family (later) is the only
non-invertible variant, and exists to exercise *fuzzy* matching, not exact match.

---

## 2. Match tolerances

**Emitted by:** the generator, into `data/samples/truth/run_metadata.json`.
**Consumed by:** `sql/04_tolerant_match.sql` (Phase 5), whose ranges must **cover** the
injected spread, and validated by a generator invariant test.

The tolerant pass can only recover a fee/rounding/timing pair if its ranges are at least
as wide as the perturbations the generator injected. So the generator records the bounds
it actually used, and Phase 5 derives its tolerances from them rather than guessing:

| Bound | Source (default) | Meaning |
|---|---|---|
| `max_amount_delta_abs` | fee model: `pct (≈0.40% max) + fixed (£0.20)` | Largest \|`b.amount − a.amount`\| for a true 1:1 pair |
| `max_date_shift_days` | `settlement_window_days` (3) | Largest \|business-day shift\| for a true 1:1 pair |
| `max_fee_pct_bps` | `fee_pct_bps + fee_pct_jitter_bps` (40) | Largest fee as a share of the amount, for a *relative* tolerance |
| `fee_pct_bps`, `fee_fixed` | fee model | Lets Phase 5 model fees explicitly if it wants |

`max_amount_delta_abs` is the largest delta *observed* in the run, so it grows with
the largest amounts drawn. A relative bound (`max_fee_pct_bps` of the amount, plus
`fee_fixed`) is usually the better tolerance for Phase 5, because a £10 difference
means very different things on £50 and on £5,000.

**Invariant (tested):** every true `ONE_TO_ONE` non-clean pair satisfies
`|amount_delta| ≤ max_amount_delta_abs` and `|date_shift_days| ≤ max_date_shift_days`.

---

## 3. Predicted-pair grain

**Produced by:** the SQL passes (Phases 3–6) as a `matched_pairs` view.
**Consumed by:** `src/recon/metrics.py` (Phase 6).

Scoring compares two sets of `(a_record_id, b_record_id)` pairs: the engine's predictions
**P** and the ground truth **G** (`ground_truth_pairs.csv`). For the comparison to be
well-defined, both sides must share the same grain:

- Exact match (pass 02) and tolerant match (pass 04) each emit **one** pair per match.
- Batch settlement (pass 06) emits **one row per `(a_member, b_settlement)` pair** — never
  a single collapsed group row. A 3-member batch contributes 3 pairs.
- The unmatched set `U_pred` is taken from pass 03's **explicit** anti-join output, and is
  validated to equal the complement of P (every record is matched or unmatched, never both).

This is why `ground_truth_pairs.csv` is stored pre-exploded: Phase 6 becomes a plain set
comparison (`TP = |P ∩ G|`, `FP = |P \ G|`, `FN = |G \ P|`) with no special-casing.
