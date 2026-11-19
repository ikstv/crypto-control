# Multi-Portfolio Design — Crypto Control 7A

## Intent

Move Crypto Control from one implicit demonstration portfolio to explicitly identified, independent virtual portfolios without rewriting the existing 10,000 UAH portfolio or enabling automated trading.

## Goals

- Add a `portfolios` registry with immutable identity, name, accounting currency (`UAH` or `USDT`), initial capital, and creation time.
- Map all existing legacy data to one deterministic portfolio and make the migration idempotent.
- Isolate cash, positions, operations, sales, valuations, observations, drawdown, risk settings, and balance-change history by `portfolio_id`.
- Expose explicit list/create/read/select APIs while preserving legacy read endpoints during a compatibility transition.
- Keep Shadow Trading market-global unless a signal is explicitly portfolio-scoped; do not execute trades.

## Non-goals and safety constraints

- No UAH/USDT conversion without a fresh, verified FX quote; no invented conversion.
- No automatic or real orders, strategy changes, Task Scheduler changes, or GitHub publication.
- No silent mutation of the existing portfolio when creating or selecting another portfolio.
- Desktop changes are limited to displaying/selecting a portfolio and a confirmed create form after backend verification.

## Current schema facts

The legacy database uses singleton/global tables: `settings`, `positions`, `history`, `paper_start`, `paper_operations`, `paper_sales`, `paper_risk`, `paper_equity`, and `paper_observations`. `Ledger` and `read_portfolio()` currently assume these tables represent one UAH demonstration portfolio.

## Chosen approach

Use one shared SQLite database. Add `portfolios` first, create one legacy row with a stable ID, then add nullable `portfolio_id` columns to legacy data tables and backfill them in one transaction. New rows require a valid portfolio ID. Existing legacy API calls resolve only to the mapped legacy portfolio for compatibility; new portfolio APIs require an explicit ID and never change the active portfolio globally.

Where SQLite cannot add a required column or constraint in place, use a transactional table rebuild: create the replacement table with the new schema, copy rows with the legacy portfolio ID, validate counts and foreign keys, then replace the old table. No production migration runs until the same migration succeeds on a copied database and a verified backup exists.

## Identity and invariants

- Portfolio IDs are generated SQLite integers and never reused.
- Names are non-empty and bounded; duplicate-create requests require an idempotency key and return the original row.
- Currency is an enum-like check constraint (`UAH`, `USDT`). Initial capital is a positive integer in the currency's minor unit.
- Money is represented as integer minor units at rest and `Decimal` in Python: UAH uses scale 2 (kopecks), USDT uses scale 6. Inputs are parsed from decimal strings, quantized to the currency scale with `ROUND_DOWN` for user-entered capital, and never pass through `float`.
- Initial capital is immutable once a portfolio is created; no edit endpoint is provided in 7A.
- Every portfolio-owned table has `portfolio_id INTEGER NOT NULL` after migration, a foreign key to `portfolios(id)`, and an index beginning with `portfolio_id`. SQLite foreign-key enforcement is enabled on every connection. Missing or unknown IDs fail explicitly rather than defaulting to another portfolio.
- A portfolio's valuations, drawdown, and balance-change snapshots use only that portfolio's observations.

## Migration and rollback

1. Back up the SQLite file using the existing backup mechanism and verify the backup.
2. Copy the database to a temporary path; run the migration in `BEGIN IMMEDIATE`.
3. Create the legacy portfolio once, add/backfill IDs, install indexes and constraints, and validate row counts and foreign keys.
4. Run the full test suite against the migrated copy, including a second migration run.
5. Only an explicit later operation may migrate the working database; on any validation failure, leave it untouched.

## API shape

- `GET /api/v1/portfolios` — list metadata and current per-portfolio status.
- `POST /api/v1/portfolios` with confirmed local header and `{name, currency, initial_capital}` — create exactly one portfolio; supports an idempotency key.
- `GET /api/v1/portfolios/{portfolio_id}` — read isolated portfolio state/history.
- Existing `/api/v1/paper*` endpoints remain compatibility aliases for the legacy portfolio until Desktop is migrated; new portfolio operations use explicit path IDs.
- The five-minute worker iterates every portfolio independently, regardless of Desktop selection. Observation uniqueness is `(portfolio_id, slot)`; gaps are represented as missing intervals, never fabricated rows.
- Portfolio creation accepts an idempotency key. Reusing a key with identical normalized parameters returns the original portfolio; reusing it with different parameters returns a conflict.

## Verification

Tests must cover migration idempotency, legacy preservation of balances/positions/operations/PnL/history, UAH and USDT creation and rounding, duplicate requests, cross-portfolio isolation of operations/PnL/observations/drawdown, missing FX, restart persistence, SQLite foreign-key rejection, all-portfolio worker iteration, and explicit invalid-ID failures. Desktop smoke tests must show the selected portfolio and currency without changing balances. Shadow signals remain global market research and are not forced into portfolio tables.
