# Multi-Portfolio 7A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add independent UAH/USDT virtual portfolios through an idempotent SQLite migration while preserving the legacy portfolio and existing API behavior.

**Architecture:** Keep one SQLite database and add a `portfolios` registry. Migrate portfolio-owned tables to required `portfolio_id` foreign keys, route legacy endpoints to the legacy ID, and expose explicit portfolio APIs. Extend the background worker to record observations per portfolio; keep market quotes and Shadow Trading global.

**Tech Stack:** Python 3.12, sqlite3, Decimal, FastAPI/Pydantic, unittest/pytest, existing WinForms Desktop.

**Spec:** `docs/superpowers/specs/2026-11-19-multi-portfolio-design.md`

## Global Constraints

- UAH uses scale 2 and USDT scale 6; storage uses integer minor units and Python uses `Decimal`, never `float`.
- Every portfolio-owned row requires `portfolio_id`, foreign-key enforcement is enabled on every connection, and indexes begin with `portfolio_id`.
- Existing `/api/v1/paper*` endpoints resolve only to the legacy portfolio; new portfolio operations require explicit IDs.
- Worker observations cover every portfolio and are unique by `(portfolio_id, slot)`.
- No automatic trades, strategy changes, Task Scheduler changes, production DB migration, or GitHub publication.

## Review Focus

- Legacy migration must preserve balances, positions, operations, PnL, and history, not only row counts.
- Currency scaling/rounding must reject invalid precision and avoid float conversion.
- Idempotency-key reuse with changed parameters must conflict.
- Cross-portfolio IDs must never leak operations, observations, or drawdown.
- Worker restart and unavailable FX must not fabricate valuations.

### Task 1: Migration primitives

**Files:** modify `portfolio.py`, `ledger.py`; test `test_portfolio.py`, `test_ledger.py`; add focused migration tests if needed.

- [ ] Write failing tests for legacy snapshot preservation, repeat migration, required foreign keys, and per-currency money scaling.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement connection setup with `PRAGMA foreign_keys=ON`, portfolio registry, deterministic legacy mapping, additive/table-rebuild migration, indexes, and validation helpers.
- [ ] Run focused tests, then full Python tests.

### Task 2: Portfolio service and API

**Files:** modify `portfolio.py`, `ledger.py`, `app.py`; tests in new `test_portfolios.py` plus existing API-adjacent tests.

- [ ] Write failing tests for UAH/USDT creation, idempotency, explicit invalid IDs, immutable initial capital, and cross-portfolio isolation.
- [ ] Implement `list_portfolios`, `create_portfolio`, and `portfolio_state` with normalized Decimal-string inputs and explicit IDs.
- [ ] Route legacy paper APIs to the legacy ID without a global active portfolio.
- [ ] Run focused and full tests.

### Task 3: Background worker isolation

**Files:** modify `app.py`, `ledger.py`; tests in `test_engine.py`/`test_portfolios.py`.

- [ ] Write failing tests proving one worker cycle visits all portfolios, uniqueness is `(portfolio_id, slot)`, and unavailable FX creates no fabricated valuation.
- [ ] Implement portfolio iteration and portfolio-scoped observation/drawdown persistence while retaining global quotes and Shadow signals.
- [ ] Run full tests and verify restart persistence on temporary SQLite.

### Task 4: Desktop selection/create surface

**Files:** modify `desktop/CryptoControl.Desktop/Form1.cs`, designer as needed; update `desktop/README.md`.

- [ ] Add minimal portfolio list/selection and confirmed create form using the explicit API; show selected name and currency.
- [ ] Ensure closing/switching the window never changes balances or server-global state.
- [ ] Build with `dotnet publish` and run available tests.

### Task 5: Copy-only migration rehearsal and handoff

- [ ] Back up the working SQLite using the existing backup tool and verify the backup.
- [ ] Copy the database to a temporary path, run migration, compare balances/positions/operations/PnL/history, and run migration a second time.
- [ ] Do not migrate the working database; report the rehearsal evidence, tests, and rollback path.
- [ ] Create one local Git commit; do not push.
