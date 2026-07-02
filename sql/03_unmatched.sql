-- =============================================================================
-- 03_unmatched.sql -- what is in one source but not (yet) matched to the other
-- -----------------------------------------------------------------------------
-- Reads:   norm_a, norm_b, matches
-- Creates: remaining_a, remaining_b  -- views of every still-unmatched record
--
-- An ANTI-JOIN keeps the rows of one table that have NO partner in another. In
-- SQL it is written as a LEFT JOIN followed by "WHERE <right side> IS NULL": the
-- LEFT JOIN keeps every left row, filling the right side with NULLs where nothing
-- matched, and the WHERE keeps only those NULL-filled rows.
--
-- These are VIEWS, not tables: a view is a saved query that re-runs every time
-- it is read. Each later pass adds rows to `matches`, so "what is left" shrinks
-- automatically -- the tolerant pass sees what exact matching left behind, the
-- batch pass sees what tolerant matching left behind, and so on.
-- =============================================================================

CREATE OR REPLACE VIEW remaining_a AS
SELECT ledger.*
FROM norm_a AS ledger
LEFT JOIN matches
    ON matches.a_record_id = ledger.record_id
WHERE matches.a_record_id IS NULL;

CREATE OR REPLACE VIEW remaining_b AS
SELECT bank.*
FROM norm_b AS bank
LEFT JOIN matches
    ON matches.b_record_id = bank.record_id
WHERE matches.b_record_id IS NULL;

-- The same question with a set operation, kept as a self-check: EXCEPT returns
-- the IDs in the first query that are absent from the second. The test suite
-- asserts both formulations always agree.
CREATE OR REPLACE VIEW remaining_a_ids_via_except AS
SELECT record_id FROM norm_a
EXCEPT
SELECT a_record_id FROM matches;
