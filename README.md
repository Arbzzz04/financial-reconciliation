# Financial Transaction Reconciliation Engine

> A **SQL-first** engine that matches two transaction datasets — for example an
> internal accounting ledger vs. a bank or payment-processor statement — then
> surfaces and classifies every discrepancy: missing records, amount mismatches,
> timing differences, duplicates, fees, and one-to-many batch settlements.

**Status:** 🚧 In active development — see the [Roadmap](#roadmap). This README
grows with the project, one phase at a time.

---

## Why this project

Reconciliation is, at its core, a **SQL problem**: take two sources that *should*
agree, match everything you can, and explain everything you can't. This repo
implements that as a sequence of readable, versioned SQL passes on **DuckDB**,
with a thin Python layer for orchestration, a synthetic-data generator that hides
a **ground-truth mapping** (so accuracy is genuinely *measurable*), and an
interactive Streamlit demo.

## The problem

Two systems record the same money and disagree. The ledger says an invoice was
paid; the bank statement shows a slightly different amount two days later, minus a
processor fee. A refund appears in one source and not the other. Five small
payments arrive at the bank as a single batch settlement. **Reconciliation** is
the discipline of matching these records across sources and categorising every
difference by its likely cause — so an analyst knows what to investigate.

## Approach: reconciliation as a tiered SQL pipeline

We match in **tiers**, from strictest to loosest, so that high-confidence matches
are made first and only the genuinely ambiguous records fall through to the
fuzzier passes. Each pass is one versioned `.sql` file.

| # | File | What it does | Core SQL concept |
| --- | ---- | ------------ | ---------------- |
| 1 | `sql/01_normalise.sql` | Standardise references, dates, amounts | Cleaning, `CASE`, string functions |
| 2 | `sql/02_exact_match.sql` | Match on normalised reference + amount + date | `INNER JOIN` |
| 3 | `sql/03_unmatched.sql` | Find what's in one source but not the other | Anti-join (`LEFT JOIN … IS NULL`), `EXCEPT` |
| 4 | `sql/04_tolerant_match.sql` | Catch fees, rounding, timing differences | Range-join + fuzzy text |
| 5 | `sql/05_duplicates.sql` | Flag repeated records | `GROUP BY … HAVING`, `ROW_NUMBER()` |
| 6 | `sql/06_batch_match.sql` | Match one record to the sum of several | `GROUP BY` + `SUM` |
| 7 | `sql/07_classify_report.sql` | Label every record, produce summary stats | `CASE` + aggregation |

> The SQL passes are introduced across **Phases 3–6**; the table above is the
> roadmap for them. CTEs (`WITH …`) keep each query readable.

## Tech stack

- **[DuckDB](https://duckdb.org/)** — the SQL engine. Embedded (zero setup), reads
  CSVs directly, and supports full analytical SQL. See [Why DuckDB](#why-duckdb).
- **SQL** — the reconciliation logic, as versioned `.sql` files in [`sql/`](sql/).
- **Python 3.11+ / pandas** — a thin orchestration layer (load data, run the
  passes, pull results out for reporting and the demo).
- **Streamlit + Plotly** — the interactive demo and charts.
- **Faker** — realistic synthetic transaction descriptions for the generator.
- **pytest** + **ruff** — tests, linting, and formatting.

## Why DuckDB

DuckDB is embedded like SQLite (no server to install — it just runs), but unlike
SQLite it has proper analytical SQL and a real `DECIMAL` type. That last point
matters a lot here: money stored as floating point accumulates rounding errors,
which is unacceptable in the one domain — reconciliation — where amounts must be
exact. The SQL we write (CTEs, window functions, set operations) transfers almost
unchanged to Postgres or BigQuery, so it's good to learn on. Where we use a
DuckDB-specific convenience that *isn't* portable (e.g. the `QUALIFY` clause), the
relevant `.sql` file will say so.

## Project structure

```text
financial-reconciliation/
├── README.md
├── requirements.txt          # pinned dependency versions (reproducible install)
├── pyproject.toml            # package metadata + ruff / pytest configuration
├── .gitignore
├── data/
│   ├── generate_data.py      # synthetic generator (Phase 2)
│   └── samples/              # generated source_a / source_b / ground_truth (git-ignored)
├── sql/                      # the reconciliation passes (Phases 3–6)
├── src/recon/
│   ├── __init__.py
│   ├── db.py                 # DuckDB connection + .sql runner (Phase 3)
│   ├── pipeline.py           # orchestrate the passes (Phase 3+)
│   ├── report.py             # summary stats & export (Phase 6)
│   └── metrics.py            # precision / recall vs. ground truth (Phase 6)
├── app/
│   └── streamlit_app.py      # interactive demo (Phase 8)
├── notebooks/                # runnable SQL walkthrough (later)
└── tests/                    # pytest suite, grown alongside each module
```

## Getting started

**Prerequisites:** Python 3.11 or newer and `git`.

```bash
# 1. Clone
git clone https://github.com/Arbzzz04/financial-reconciliation.git
cd financial-reconciliation

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

# 3. Install pinned dependencies, then the project itself (editable)
pip install -r requirements.txt
pip install -e .

# 4. Verify the install
pytest
```

> Most of the engine arrives in later phases; for now, `pytest` runs a smoke test
> confirming the package imports correctly. Each phase adds runnable commands here.

## Conventions

A consistent style is decided up front and applied everywhere.

### Python

- **Google-style docstrings** on every public function and class.
- **Type hints** on all public function signatures.
- **ruff** for both linting and formatting (line length **88**, the ecosystem
  default). The config even enforces the docstring convention automatically.
- `snake_case` for functions/variables, `PascalCase` for classes; small,
  single-responsibility functions.

### SQL

- **UPPERCASE** keywords (`SELECT`, `FROM`, `WHERE`, `JOIN`); `snake_case`
  lowercase identifiers.
- **One major clause per line**; trailing commas in `SELECT` lists.
- **CTEs (`WITH …`) over nested subqueries** — one CTE per logical step, named
  meaningfully.
- **Explicit `JOIN … ON`** — never comma-joins.
- **Meaningful aliases** (`ledger`, `bank` — not `a`, `b`).
- A **comment block at the top of every `.sql` file** describing what it does,
  plus inline comments on any non-obvious logic.

### Commits

[**Conventional Commits**](https://www.conventionalcommits.org/) — e.g.
`feat: add exact-match SQL pass`, `test: cover anti-join edge cases`,
`docs: explain tolerant matching`. Readable history for reviewers (and recruiters).

## Roadmap

- [x] **Phase 1** — Repo scaffold, `.gitignore`, README skeleton, tooling
- [ ] **Phase 2** — Synthetic data generator (+ hidden ground truth)
- [ ] **Phase 3** — DuckDB setup + normalisation SQL
- [ ] **Phase 4** — Exact match + anti-joins
- [ ] **Phase 5** — Tolerant + duplicate + batch matching
- [ ] **Phase 6** — Classification, reporting & accuracy metrics
- [ ] **Phase 7** — Tests throughout
- [ ] **Phase 8** — Streamlit demo

## Results

*Planned — populated once the engine and demo exist: headline accuracy
(precision / recall / F1) against the synthetic ground truth, plus a demo
screenshot.*

## What this demonstrates

- Applied **SQL**: joins and anti-joins, CTEs, window functions, aggregations,
  and set operations, used to solve a real analytical problem.
- **Reconciliation domain knowledge** — the discrepancy categories that matter in
  finance and how to detect them.
- **Measurable accuracy** — testing the engine against a known ground truth
  (precision / recall / F1), not just eyeballing output.
- **Reproducible analytics tooling** — pinned dependencies, linting, tests, and a
  one-command setup.
- An **interactive demo** that turns the pipeline into something clickable.

## License

*To be decided (MIT planned).*
