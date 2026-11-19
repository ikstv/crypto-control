# Background Portfolio History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist trustworthy paper-portfolio valuations from the FastAPI lifespan worker independently of browser traffic.

**Architecture:** Extend the existing Ledger/SQLite path with idempotent observation records and a read-only history API. A single asyncio worker owned by FastAPI lifespan records at startup and every five minutes, skipping stale/unavailable data without backfilling gaps.

**Tech Stack:** Python 3.12, FastAPI lifespan, asyncio, sqlite3, Decimal, unittest.

**Spec:** `docs/superpowers/specs/2026-11-19-background-portfolio-history-design.md`

## Global Constraints

- Only virtual trading; no real orders or credentials.
- Do not change strategies, initial capital, Task Scheduler, or dependencies.
- Do not invent valuations for stale data, outages, sleep, or missed intervals.
- Preserve Android-compatible backend API and SQLite history.

## Review Focus

- Concurrent reads must not create duplicate observations — test idempotent slot insertion.
- Stale/unavailable quotes must produce explicit status and no equity value — test skip reasons.
- Restart must read persisted observations without backfill — test restart and gap behavior.
- Sale and valuation writes must remain consistent — test operation history and equity integrity.
- Worker cancellation must finish cleanly — test lifespan shutdown.

### Task 1: Persisted observation model

**Files:** Modify `ledger.py`; test `test_ledger.py`.

- [ ] Write failing tests for fresh recording, idempotent slot, stale/unavailable skip, and persisted history.
- [ ] Run focused tests and confirm failure.
- [ ] Add additive SQLite columns/table and `record_valuation(now, slot)` plus read-only history helpers.
- [ ] Run focused tests and confirm pass.

### Task 2: Lifespan worker and API

**Files:** Modify `app.py`, `ledger.py`; test `test_engine.py`/new focused tests.

- [ ] Write failing tests proving startup recording, interval scheduling with injected clock/sleep, cancellation, and side-effect-free `GET /api/v1/paper`.
- [ ] Run focused tests and confirm failure.
- [ ] Add one lifespan-managed worker, no backfill, and history response with gaps/statuses/drawdown.
- [ ] Run focused and full suites.

### Task 3: Verification and commit

- [ ] Run full `python -m unittest -v` with the project `.venv`.
- [ ] Verify temporary SQLite integrity and unchanged real portfolio metadata.
- [ ] Review `git diff --check`, commit only source/tests/spec/plan changes, and do not push GitHub.
