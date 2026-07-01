-- =============================================================================
-- 02_exact_match.sql -- tier 1: records that agree on every key
-- -----------------------------------------------------------------------------
-- Reads:   norm_a, norm_b
-- Creates: matches  -- one row per matched (a, b) pair; later passes append to it
--
-- An exact match is the strictest, highest-confidence tier: the normalised
-- reference, the amount and the date are all identical. Running it first means
-- the looser passes only ever see records that genuinely need them.
-- =============================================================================

CREATE OR REPLACE TABLE matches (
    a_record_id VARCHAR NOT NULL,
    b_record_id VARCHAR NOT NULL,
    match_pass VARCHAR NOT NULL,  -- EXACT | TOLERANT | BATCH
    batch_id VARCHAR,             -- set only by the batch pass
);

INSERT INTO matches
WITH
-- If a record is duplicated, a plain join would pair the other side with *both*
-- copies. ROW_NUMBER() numbers identical records 1, 2, 3... within each key, and
-- joining on that number too pairs first-with-first, so a surplus copy is left
-- unmatched for the duplicate pass to flag.
ledger AS (
    SELECT
        record_id,
        norm_ref,
        amount,
        txn_date,
        ROW_NUMBER() OVER (
            PARTITION BY norm_ref, amount, txn_date
            ORDER BY record_id
        ) AS occurrence,
    FROM norm_a
),
bank AS (
    SELECT
        record_id,
        norm_ref,
        amount,
        txn_date,
        ROW_NUMBER() OVER (
            PARTITION BY norm_ref, amount, txn_date
            ORDER BY record_id
        ) AS occurrence,
    FROM norm_b
)
SELECT
    ledger.record_id AS a_record_id,
    bank.record_id AS b_record_id,
    'EXACT' AS match_pass,
    NULL AS batch_id,
FROM ledger
INNER JOIN bank
    ON ledger.norm_ref = bank.norm_ref
    AND ledger.amount = bank.amount
    AND ledger.txn_date = bank.txn_date
    AND ledger.occurrence = bank.occurrence;
