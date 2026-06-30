-- =============================================================================
-- 01_normalise.sql -- standardise both sources into a comparable form
-- -----------------------------------------------------------------------------
-- Reads:   source_a (ledger), source_b (bank statement)
-- Creates: norm_a, norm_b  -- one row per source record, plus matching keys
--
-- Every later pass joins on the columns built here, so this file decides what
-- "the same" means. The reference rule mirrors recon.normalise exactly (see
-- docs/contracts.md, contract 1); tests/test_sql_normalise.py proves the SQL and
-- the Python agree on every reference.
-- =============================================================================

-- A macro is a reusable SQL expression: write the rule once, apply it to both
-- tables. Steps, in order:
--   1. strip every character that is not a letter or digit
--   2. upper-case
--   3. strip leading zeros from each run of digits, keeping a single 0
--      ('(^|[^0-9])0+([0-9])' finds zeros that start a digit run and are followed
--       by another digit; '\1\2' keeps the character before the run and that digit)
CREATE OR REPLACE MACRO normalise_reference(reference) AS
    regexp_replace(
        upper(regexp_replace(reference, '[^0-9A-Za-z]+', '', 'g')),
        '(^|[^0-9])0+([0-9])',
        '\1\2',
        'g'
    );

-- Free text is compared case-insensitively with whitespace collapsed.
CREATE OR REPLACE MACRO normalise_text(text) AS
    upper(trim(regexp_replace(text, '\s+', ' ', 'g')));

CREATE OR REPLACE TABLE norm_a AS
SELECT
    record_id,
    reference,
    normalise_reference(reference) AS norm_ref,
    amount,
    txn_date,
    currency,
    description,
    normalise_text(description) AS norm_description,
    CASE WHEN amount >= 0 THEN 'credit' ELSE 'debit' END AS direction,
FROM source_a;

CREATE OR REPLACE TABLE norm_b AS
SELECT
    record_id,
    reference,
    normalise_reference(reference) AS norm_ref,
    amount,
    txn_date,
    currency,
    description,
    normalise_text(description) AS norm_description,
    CASE WHEN amount >= 0 THEN 'credit' ELSE 'debit' END AS direction,
FROM source_b;
