-- =============================================================================
-- 04_tolerant_match.sql -- tier 2: the same transaction, slightly different
-- -----------------------------------------------------------------------------
-- Reads:   remaining_a, remaining_b (via 03), variables max_fee_bps, fee_fixed,
--          date_window_days
-- Appends: matches (match_pass = 'TOLERANT')
--
-- Exact matching misses records that a processor fee, a penny of rounding, or a
-- settlement delay has nudged apart. This pass matches with RANGES instead of
-- equality -- a "range join" -- using the bounds the data generator publishes
-- (docs/contracts.md, contract 2):
--
--   amount: |bank - ledger| <= |ledger| * max_fee_bps / 10,000 + fee_fixed + 0.01
--           (a RELATIVE tolerance: a 40p gap is a fee on £100 but suspicious on £5)
--   date:   the bank posts 0 .. date_window_days business days later. Calendar
--           days allow +2 extra, because a 3-business-day delay from a Friday
--           lands 5 calendar days later.
--   reference: equal after normalisation, or a near-identical typo
--           (Jaro-Winkler similarity >= 0.92 with the same prefix).
--
-- One ledger record can sit within range of several bank records, so candidates
-- are ranked from BOTH sides and only "mutual best" pairs are kept.
-- =============================================================================

INSERT INTO matches
WITH
candidates AS (
    SELECT
        ledger.record_id AS a_record_id,
        bank.record_id AS b_record_id,
        ledger.norm_ref = bank.norm_ref AS same_reference,
        abs(bank.amount - ledger.amount) AS amount_gap,
        date_diff('day', ledger.txn_date, bank.txn_date) AS day_gap,
    FROM remaining_a AS ledger
    INNER JOIN remaining_b AS bank
        ON ledger.direction = bank.direction
        -- range condition on amount (relative fee tolerance)
        AND abs(bank.amount - ledger.amount)
            <= abs(ledger.amount) * getvariable('max_fee_bps') / 10000
                + getvariable('fee_fixed') + 0.01
        -- range condition on date (settlement window, never before the ledger)
        AND date_diff('day', ledger.txn_date, bank.txn_date)
            BETWEEN 0 AND getvariable('date_window_days') + 2
        -- the reference must agree, allowing for a small typo
        AND (
            ledger.norm_ref = bank.norm_ref
            OR (
                left(ledger.norm_ref, 3) = left(bank.norm_ref, 3)
                AND jaro_winkler_similarity(ledger.norm_ref, bank.norm_ref) >= 0.92
            )
        )
),
ranked AS (
    SELECT
        *,
        -- best candidate for each ledger record: same reference first, then the
        -- smallest amount gap, then the shortest delay
        ROW_NUMBER() OVER (
            PARTITION BY a_record_id
            ORDER BY same_reference DESC, amount_gap, day_gap, b_record_id
        ) AS rank_for_ledger,
        -- and, symmetrically, the best candidate for each bank record
        ROW_NUMBER() OVER (
            PARTITION BY b_record_id
            ORDER BY same_reference DESC, amount_gap, day_gap, a_record_id
        ) AS rank_for_bank,
    FROM candidates
)
SELECT
    a_record_id,
    b_record_id,
    'TOLERANT' AS match_pass,
    NULL AS batch_id,
FROM ranked
WHERE rank_for_ledger = 1
    AND rank_for_bank = 1;
