-- =============================================================================
-- 05_duplicates.sql -- flag records posted more than once
-- -----------------------------------------------------------------------------
-- Reads:   norm_a, norm_b, matches
-- Creates: duplicates  -- one row per surplus copy, pointing at its original
--
-- Two steps, two classic tools:
--   1. GROUP BY ... HAVING COUNT(*) > 1 finds every key (reference, amount, date)
--      that appears more than once in the same source. HAVING is WHERE for groups:
--      it filters after aggregation.
--   2. A window function then picks one "original" per group -- preferring the
--      copy that a matching pass already paired -- and every other copy that is
--      still unmatched is flagged as a duplicate of it.
-- =============================================================================

CREATE OR REPLACE TABLE duplicates AS
WITH
ledger_keys AS (
    SELECT norm_ref, amount, txn_date
    FROM norm_a
    GROUP BY norm_ref, amount, txn_date
    HAVING COUNT(*) > 1
),
bank_keys AS (
    SELECT norm_ref, amount, txn_date
    FROM norm_b
    GROUP BY norm_ref, amount, txn_date
    HAVING COUNT(*) > 1
),
copies AS (
    SELECT
        'A' AS source,
        ledger.record_id,
        ledger.norm_ref,
        ledger.amount,
        ledger.txn_date,
        ledger.record_id IN (SELECT a_record_id FROM matches) AS is_matched,
    FROM norm_a AS ledger
    INNER JOIN ledger_keys USING (norm_ref, amount, txn_date)
    UNION ALL
    SELECT
        'B' AS source,
        bank.record_id,
        bank.norm_ref,
        bank.amount,
        bank.txn_date,
        bank.record_id IN (SELECT b_record_id FROM matches) AS is_matched,
    FROM norm_b AS bank
    INNER JOIN bank_keys USING (norm_ref, amount, txn_date)
),
ranked AS (
    SELECT
        *,
        FIRST_VALUE(record_id) OVER (
            PARTITION BY source, norm_ref, amount, txn_date
            ORDER BY is_matched DESC, record_id
        ) AS original_id,
    FROM copies
)
SELECT
    record_id,
    source,
    original_id AS duplicate_of,
FROM ranked
WHERE record_id <> original_id
    AND NOT is_matched;
