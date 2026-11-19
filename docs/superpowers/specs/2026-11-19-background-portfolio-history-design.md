# Background Portfolio History Design

## Intent

Record trustworthy portfolio valuations from the FastAPI engine every five minutes without depending on browser requests, while preserving existing paper-trading behavior and Android-compatible HTTP access.

## Goals

- One lifespan-managed worker per engine process and SQLite database.
- Persist only valuations made with fresh, complete-enough market data and a valid FX rate.
- Preserve gaps and skip reasons instead of inventing values.
- Keep reads side-effect free; expose persisted history through an API.
- Keep drawdown and balance-change calculations based on persisted observations.

## Non-goals

No strategy changes, automatic trades, capital changes, new dependencies, or Task Scheduler changes.

## Approach

Extend the existing `paper_equity` schema through a safe `CREATE TABLE IF NOT EXISTS` migration. Add a `record_valuation()` method that is idempotent for an observation slot and records `recorded`, `stale`, `unavailable`, or `missed` status with a reason. A lifespan worker invokes it on a five-minute cadence; it records once on startup and never backfills missed slots. `Ledger.state()` reads persisted data only.

## Data safety

The migration is additive and runs through the existing SQLite initialization path. Tests use temporary databases and verify integrity and unchanged operation history. No real orders or credentials are involved.

## API

`GET /api/v1/paper/history` returns persisted valuation observations, status/reason fields, and drawdown summary for future desktop/Android clients.
