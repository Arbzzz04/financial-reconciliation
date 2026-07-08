-- =============================================================================
-- 07_classify_report.sql -- label every record and summarise the outcome
-- -----------------------------------------------------------------------------
-- Reads:   norm_a, norm_b, matches, duplicates
-- Creates: pair_results            -- one row per matched pair, with its deltas
--          reconciliation_results  -- one row per record in EITHER source
--          summary_by_category     -- record count and value per category
--          summary                 -- headline numbers for the report
--
-- Matching says WHICH records belong together; this pass says WHY they differ.
-- CASE expressions turn deltas into labels (several can apply at once, e.g.
-- "FEE;TIMING"), and unmatched records are classified from their narrative.
-- The labels use the same vocabulary as the generator's ground truth, so
-- accuracy can be scored record by record.
-- =============================================================================

CREATE OR REPLACE TABLE pair_results AS
SELECT
    matches.a_record_id,
    matches.b_record_id,
    matches.match_pass,
    matches.batch_id,
    CASE WHEN matches.match_pass = 'BATCH' THEN NULL
        ELSE bank.amount - ledger.amount END AS amount_delta,
    date_diff('day', ledger.txn_date, bank.txn_date) AS day_gap,
    CASE
        WHEN matches.match_pass = 'BATCH' THEN 'BATCH_MEMBER'
        ELSE coalesce(
            nullif(
                concat_ws(
                    ';',
                    -- amount: a single penny is rounding; anything else is a fee
                    CASE
                        WHEN abs(bank.amount - ledger.amount) = 0.01 THEN 'ROUNDING'
                        WHEN bank.amount <> ledger.amount THEN 'FEE'
                    END,
                    CASE WHEN bank.txn_date <> ledger.txn_date THEN 'TIMING' END,
                    -- same normalised key, but written differently
                    CASE WHEN bank.reference <> ledger.reference THEN 'REF_FORMAT' END
                ),
                ''
            ),
            'CLEAN'
        )
    END AS category,
FROM matches
INNER JOIN norm_a AS ledger ON ledger.record_id = matches.a_record_id
INNER JOIN norm_b AS bank ON bank.record_id = matches.b_record_id;

CREATE OR REPLACE TABLE reconciliation_results AS
WITH
ledger_rows AS (
    SELECT
        ledger.record_id,
        'A' AS source,
        ledger.reference,
        ledger.amount,
        ledger.txn_date,
        ledger.description,
        CASE
            WHEN pairs.match_pass = 'BATCH' THEN 'BATCH'
            WHEN pairs.match_pass IS NOT NULL THEN 'ONE_TO_ONE'
            WHEN dupes.record_id IS NOT NULL THEN 'DUPLICATE'
            ELSE 'UNMATCHED'
        END AS status,
        pairs.match_pass,
        pairs.b_record_id AS partner_id,
        pairs.batch_id,
        CASE
            WHEN pairs.category IS NOT NULL THEN pairs.category
            WHEN dupes.record_id IS NOT NULL THEN 'DUPLICATE'
            WHEN ledger.norm_description LIKE 'MANUAL ADJUSTMENT%'
                THEN 'LEDGER_ADJUSTMENT'
            ELSE 'MISSING_FROM_B'
        END AS category,
        pairs.amount_delta,
        pairs.day_gap,
        dupes.duplicate_of,
    FROM norm_a AS ledger
    LEFT JOIN pair_results AS pairs ON pairs.a_record_id = ledger.record_id
    LEFT JOIN duplicates AS dupes
        ON dupes.record_id = ledger.record_id AND dupes.source = 'A'
),
-- a settlement appears in pair_results once per member; collapse to one row
settlement_rows AS (
    SELECT b_record_id, any_value(batch_id) AS batch_id
    FROM pair_results
    WHERE match_pass = 'BATCH'
    GROUP BY b_record_id
),
bank_rows AS (
    SELECT
        bank.record_id,
        'B' AS source,
        bank.reference,
        bank.amount,
        bank.txn_date,
        bank.description,
        CASE
            WHEN settled.b_record_id IS NOT NULL THEN 'BATCH'
            WHEN pairs.match_pass IS NOT NULL THEN 'ONE_TO_ONE'
            WHEN dupes.record_id IS NOT NULL THEN 'DUPLICATE'
            ELSE 'UNMATCHED'
        END AS status,
        coalesce(pairs.match_pass, CASE WHEN settled.b_record_id IS NOT NULL
            THEN 'BATCH' END) AS match_pass,
        pairs.a_record_id AS partner_id,
        settled.batch_id,
        CASE
            WHEN settled.b_record_id IS NOT NULL THEN 'BATCH_SETTLEMENT'
            WHEN pairs.category IS NOT NULL THEN pairs.category
            WHEN dupes.record_id IS NOT NULL THEN 'DUPLICATE'
            WHEN bank.norm_description LIKE '%ACCOUNT FEE%' THEN 'BANK_FEE'
            WHEN bank.norm_description LIKE '%INTEREST%' THEN 'BANK_INTEREST'
            ELSE 'MISSING_FROM_A'
        END AS category,
        pairs.amount_delta,
        pairs.day_gap,
        dupes.duplicate_of,
    FROM norm_b AS bank
    LEFT JOIN pair_results AS pairs
        ON pairs.b_record_id = bank.record_id AND pairs.match_pass <> 'BATCH'
    LEFT JOIN settlement_rows AS settled ON settled.b_record_id = bank.record_id
    LEFT JOIN duplicates AS dupes
        ON dupes.record_id = bank.record_id AND dupes.source = 'B'
)
SELECT * FROM ledger_rows
UNION ALL
SELECT * FROM bank_rows
ORDER BY source, record_id;

-- Records and value per category. A record carrying several labels
-- ("FEE;TIMING") counts once under each, so this is a breakdown, not a partition.
CREATE OR REPLACE TABLE summary_by_category AS
WITH labels AS (
    -- split "FEE;TIMING" into one row per label before aggregating
    SELECT unnest(string_split(category, ';')) AS category, amount
    FROM reconciliation_results
)
SELECT
    category,
    COUNT(*) AS records,
    SUM(abs(amount)) AS total_value,
FROM labels
GROUP BY category
ORDER BY records DESC, category;

CREATE OR REPLACE TABLE summary AS
SELECT
    COUNT(*) FILTER (WHERE source = 'A') AS ledger_records,
    COUNT(*) FILTER (WHERE source = 'B') AS bank_records,
    COUNT(*) FILTER (WHERE status IN ('ONE_TO_ONE', 'BATCH')) AS matched_records,
    -- nullif() turns a zero denominator into NULL (no division by zero) and
    -- coalesce() reports an empty run as 0 rather than NULL / NaN
    coalesce(
        round(
            COUNT(*) FILTER (WHERE status IN ('ONE_TO_ONE', 'BATCH'))
                / nullif(COUNT(*), 0),
            4
        ),
        0
    ) AS match_rate,
    -- value reconciled: what the ledger agrees with the bank on
    coalesce(
        SUM(abs(amount))
            FILTER (WHERE source = 'A' AND status IN ('ONE_TO_ONE', 'BATCH')),
        0
    ) AS value_reconciled,
    -- value in discrepancy: everything unmatched or duplicated, plus the gaps
    -- (fees, rounding) inside matched pairs
    coalesce(SUM(abs(amount)) FILTER (WHERE status IN ('UNMATCHED', 'DUPLICATE')), 0)
        + coalesce(SUM(abs(amount_delta)) FILTER (WHERE source = 'A'), 0)
        AS value_in_discrepancy,
    COUNT(*) FILTER (WHERE status = 'UNMATCHED') AS unmatched_records,
    COUNT(*) FILTER (WHERE status = 'DUPLICATE') AS duplicate_records,
FROM reconciliation_results;
