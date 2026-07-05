-- =============================================================================
-- 06_batch_match.sql -- tier 3: one bank settlement = several ledger payments
-- -----------------------------------------------------------------------------
-- Reads:   remaining_a, remaining_b, duplicates, variables batch_max_size,
--          batch_window_days, batch_span_days
-- Appends: matches (match_pass = 'BATCH', one row per member -- contract 3)
--
-- A payment processor often pays out several customer payments as ONE bank
-- credit for their total. Matching it means finding the set of ledger records
-- whose SUM equals the settlement exactly: a subset-sum search.
--
-- A RECURSIVE CTE does this in SQL. It starts from single members, then each
-- step adds one more record to every partial combination:
--   * members are added in record_id order, so each set is built only once;
--   * a combination stops growing at batch_max_size members, or as soon as its
--     running total overshoots the settlement (all members share a sign, so the
--     total can only move further away).
--
-- Subset-sum is exponential, so the search is kept small with three business
-- rules, each of which also guards against false matches (some random handful
-- of payments will eventually sum to an unrelated amount):
--   * only bank lines that describe themselves as a settlement are searched;
--   * if the line states its item count ("... 3 ITEMS"), only sets of exactly
--     that size are kept, and none larger are ever built;
--   * members fall within batch_window_days before the settlement, and within
--     batch_span_days of each other (one payment run).
-- =============================================================================

INSERT INTO matches
WITH RECURSIVE
settlements AS (
    SELECT
        record_id,
        amount,
        txn_date,
        direction,
        -- the stated item count, or the configured maximum if none is given
        coalesce(
            TRY_CAST(regexp_extract(norm_description, '(\d+) ITEMS', 1) AS INTEGER),
            getvariable('batch_max_size')
        ) AS max_members,
        regexp_matches(norm_description, '\d+ ITEMS') AS count_is_stated,
    FROM remaining_b
    WHERE regexp_matches(norm_description, 'SETTLEMENT|BATCH')
        AND record_id NOT IN (SELECT record_id FROM duplicates WHERE source = 'B')
),
members AS (
    SELECT record_id, amount, txn_date, direction
    FROM remaining_a
    WHERE record_id NOT IN (SELECT record_id FROM duplicates WHERE source = 'A')
),
-- every eligible (settlement, member) pairing: same sign, member settled before
-- the bank line and within the window, and smaller than the settlement itself
eligible AS (
    SELECT
        settlements.record_id AS b_record_id,
        settlements.amount AS b_amount,
        settlements.max_members,
        settlements.count_is_stated,
        members.record_id AS a_record_id,
        members.amount AS a_amount,
        members.txn_date AS a_date,
    FROM settlements
    INNER JOIN members
        ON members.direction = settlements.direction
        AND members.txn_date
            BETWEEN settlements.txn_date - getvariable('batch_window_days')::INTEGER
            AND settlements.txn_date
        AND abs(members.amount) < abs(settlements.amount)
),
combos AS (
    -- anchor: every combination starts as one member
    SELECT
        b_record_id,
        b_amount,
        max_members,
        count_is_stated,
        a_record_id AS last_member,
        [a_record_id] AS member_ids,
        1 AS n_members,
        a_amount AS running_total,
        a_date AS first_date,
        a_date AS last_date,
    FROM eligible
    UNION ALL
    -- recursive step: extend a combination by one later-numbered member
    SELECT
        combos.b_record_id,
        combos.b_amount,
        combos.max_members,
        combos.count_is_stated,
        eligible.a_record_id,
        list_append(combos.member_ids, eligible.a_record_id),
        combos.n_members + 1,
        combos.running_total + eligible.a_amount,
        least(combos.first_date, eligible.a_date),
        greatest(combos.last_date, eligible.a_date),
    FROM combos
    INNER JOIN eligible
        ON eligible.b_record_id = combos.b_record_id
        AND eligible.a_record_id > combos.last_member
    WHERE combos.n_members < least(combos.max_members, getvariable('batch_max_size'))
        AND abs(combos.running_total + eligible.a_amount) <= abs(combos.b_amount)
        -- one payment run: stop if the members would span too many days
        AND date_diff(
            'day',
            least(combos.first_date, eligible.a_date),
            greatest(combos.last_date, eligible.a_date)
        ) <= getvariable('batch_span_days')
),
-- combinations whose members sum EXACTLY to the settlement
exact_sets AS (
    SELECT
        b_record_id,
        member_ids,
        n_members,
        date_diff('day', first_date, last_date) AS date_span,
    FROM combos
    WHERE n_members >= 2
        AND running_total = b_amount
        AND (NOT count_is_stated OR n_members = max_members)
),
-- if several sets fit one settlement, prefer the tightest date range (a
-- processor settles a run of consecutive payments); skip it if still tied
best_sets AS (
    SELECT
        b_record_id,
        member_ids,
        COUNT(*) OVER (PARTITION BY b_record_id, date_span) AS tied_sets,
        ROW_NUMBER() OVER (
            PARTITION BY b_record_id
            ORDER BY date_span, n_members
        ) AS preference,
        date_span,
    FROM exact_sets
),
chosen AS (
    SELECT b_record_id, member_ids
    FROM best_sets
    WHERE preference = 1
        AND tied_sets = 1
),
-- explode each chosen set into one row per member ...
exploded AS (
    SELECT b_record_id, unnest(member_ids) AS a_record_id
    FROM chosen
),
-- ... and drop any member claimed by two different settlements
claimed_once AS (
    SELECT a_record_id
    FROM exploded
    GROUP BY a_record_id
    HAVING COUNT(*) = 1
)
SELECT
    exploded.a_record_id,
    exploded.b_record_id,
    'BATCH' AS match_pass,
    'BATCH-' || exploded.b_record_id AS batch_id,
FROM exploded
WHERE exploded.b_record_id NOT IN (
    -- a settlement is only accepted if every one of its members survived
    SELECT b_record_id
    FROM exploded
    WHERE a_record_id NOT IN (SELECT a_record_id FROM claimed_once)
);
