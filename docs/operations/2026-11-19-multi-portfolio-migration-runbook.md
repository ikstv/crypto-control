# Multi-portfolio SQLite migration runbook (P1)

**Status:** preparation only. No production database was opened, copied, or modified.
**Migration execution:** requires a separate explicit user approval after the fresh-copy rehearsal.
**Repository baseline reviewed:** `main` at `391dd38721fe1a753dae6b815396bf5dda2b1750`; `origin/main` at `3287bdbf3f09ceb19b42f6fbc74421bb82ae7c11` is an ancestor. Two pre-existing untracked plan files were left untouched.

## Scope and known runtime contract

- Engine entry point: `engine.py`, launched by `engine-task.cmd` from the repository root. The default listener is `127.0.0.1:8000` (or `CRYPTO_CONTROL_ENGINE_PORT` when explicitly configured).
- Scheduled task name: `CryptoControlEngine`. `engine.ps1 -Action stop` creates `data\engine-stopped`; the launcher/Engine watches this marker and exits. `engine.ps1 -Action start` removes it and starts the scheduled task. The stop action does **not** disable or unregister the scheduled task. Task settings include up to three restarts after a failure, so the future migration window must prevent task-triggered relaunch and verify the process has exited.
- Database path inferred from `backup.py`: `data\crypto_control.sqlite3`. This path was inferred from source; the database file itself was not opened or inspected.
- Backup implementation: `backup.py` uses SQLite Backup API, writes a SHA-256 sidecar, and verifies integrity/foreign keys and a restored snapshot. The default `create` command reads the default database; do not run it until separately authorized.
- Legacy schema is identified by absence of `portfolio_id` in `settings`; migrated schema by its presence. No explicit application migration version is set in `PRAGMA user_version` by the inspected migration. Therefore the rehearsal must validate actual columns, constraints, indexes, row ownership, and application behavior rather than rely on a numeric schema version.
- Current local environment observed: Python 3.12.10, Python SQLite 3.49.1; .NET SDK 9.0.200 and .NET 9 Desktop Runtime 9.0.2; WebView2 Runtime 157.0.4341.60. Desktop targets `net9.0-windows` and uses WebView2 package 1.0.2903.40. Recheck availability before any future cutover; do not install components as part of this plan.
- Code baseline must be treated as a matched set: application code at the approved commit, its existing `.venv` dependencies, Desktop publish output built from that same source, and either a verified legacy database or a verified migrated copy. Do not combine an older Engine with the migrated schema unless the compatibility tests explicitly establish support.

## Approval gates

| Gate | Required user approval | Permitted action |
|---|---|---|
| P1 (this document) | Already granted | Read source/Git metadata and prepare this runbook only |
| P2 fresh snapshot | Separate explicit approval | Temporarily stop writes as below; create and verify one fresh backup; do not migrate it yet |
| P3 rehearsal | Separate explicit approval after P2 results | Restore/copy the fresh backup to a new isolated file and migrate/test only that copy |
| P4 production cutover | Separate explicit approval after a GO rehearsal report | Stop/disable scheduled launch, make a final verified backup, migrate the working file atomically, and run smoke tests |

Approval for one gate does not grant approval for the next. Any failed check means NO-GO and no progression.

## P2 — fresh backup procedure (not authorized yet)

1. Confirm the user approved P2 and that no other maintenance or process is changing the repository/database.
2. Capture read-only pre-stop service state: current commit, scheduled-task state, Engine PID/command line, health endpoint and quote freshness. Do not infer process shutdown from a marker file alone.
3. Prevent automatic relaunch for the maintenance window. The operator must disable the existing `CryptoControlEngine` task (not delete it), record its prior enabled state, then request orderly Engine stop using the agreed local procedure. Preserve the stop marker state for diagnosis. Do not terminate the user session or reboot.
4. Confirm the Engine PID has exited and `127.0.0.1:8000/api/v1/status` no longer responds. If the PID remains or a new Engine appears, abort; do not copy a live database as the approved cutover backup.
5. Only now create the fresh snapshot using the repository virtualenv and explicit paths, for example:

   ```powershell
   $db = Join-Path (Get-Location) 'data\crypto_control.sqlite3'
   $backupDir = Join-Path (Get-Location) 'backups'
   $copy = & (Join-Path (Get-Location) '.venv\Scripts\python.exe') (Join-Path (Get-Location) 'backup.py') create --database $db --directory $backupDir
   if ($LASTEXITCODE -ne 0) { throw 'Backup creation failed; NO-GO' }
   & (Join-Path (Get-Location) '.venv\Scripts\python.exe') (Join-Path (Get-Location) 'backup.py') verify $copy
   if ($LASTEXITCODE -ne 0) { throw 'Backup verification failed; NO-GO' }
   Get-FileHash -Algorithm SHA256 -LiteralPath $copy
   Get-Content -LiteralPath ($copy + '.sha256')
   ```

   Run this only after P2 is separately approved. Keep the database and adjacent `.sha256` together in a protected location, preferably on a separate drive. Record their paths, sizes, timestamps and hashes in the change record. Never overwrite an existing backup.
6. Re-enable the scheduled task only if P2 ends without proceeding to P3, clear a deliberate stop marker only through the normal `engine.ps1 -Action start` path, then check task and Engine status. If a cutover is being planned, keep service stopped and task disabled until the approved next action is clear.

## Financial snapshot and rehearsal on the fresh copy (P3)

Never run migration against the original working database during rehearsal. Restore the verified backup into a new, empty, uniquely named rehearsal directory using `backup.py restore --to <new-file>`; verify the restored file before opening it with migration code. Preserve the original backup and checksum read-only.

Record a pre-migration snapshot from the restored rehearsal copy, then run the migration only on that copy. Compare pre/post values exactly, including:

- initial capital: `1,000,000` minor units = **10,000.00 UAH**;
- the nine configured symbols and each allocation, plus total allocated and unallocated cash;
- cash, positions/quantities, operation and sale counts and IDs, `operation_id` uniqueness, fees, cost basis and realized/unrealized PnL;
- risk settings, observations and their slots/status/reasons, equity history/currency, high-water mark and maximum drawdown;
- history events and timestamps.

Use exact decimal strings/minor units, not floats or display-rounded values. For data that may legitimately change during the rehearsal, use the backup's frozen snapshot as the comparison baseline. Require identical financial values and identifiers after migration; explain any intentionally transformed representation before treating it as acceptable.

Re-run migration on the rehearsal copy and require an explicit `already_migrated` result with no SQL dump/data change. Inject migration failures only on disposable copies and prove transaction rollback leaves the rehearsal source intact. Run `PRAGMA integrity_check` (must be `ok`) and `PRAGMA foreign_key_check` (must return no rows). Verify indexes and non-null foreign-key ownership for each portfolio-owned table.

Run API/Engine tests against the rehearsal copy and a dedicated loopback port, never port 8000: legacy portfolio read/risk/preview/sale compatibility; multi-portfolio creation, allocation configure/start, scoped history/observations and operations; idempotency; unknown IDs; restart; isolated UAH/USDT values; and no unexpected trades. Run backup → verify → restore into another empty file and compare the full logical SQL dump and financial snapshot. Desktop testing must use the explicit isolated test-port mode and its isolated WebView2 profile; the normal Desktop/8000 connection remains unverified.

## GO / NO-GO for P4

**GO to request a separate production-cutover approval only if all are true:** fresh backup checksum and verify succeed; source is unchanged; before/after migration financial snapshots match exactly; second migration is idempotent; integrity and foreign-key checks pass; failure injection rolls back; legacy and multi-portfolio API tests pass; Desktop isolated smoke test passes; backup/restore is exact; full Windows Python suite and `dotnet publish` pass on the same commit; no unexplained warnings or unresolved defects; and a tested rollback copy is available.

**NO-GO:** any mismatch in cash, holdings, allocations, fees, PnL, IDs, history, observations/equity/drawdown; missing or stale required market data during a read/valuation check; task/Engine not confirmed stopped; backup/restore or checksum failure; failed tests/build; schema not recognized; or any unplanned write to the working DB. Stop, preserve evidence and backups, and do not improvise a repair on the production file.

## P4 — future production cutover order (not authorized yet)

1. Obtain explicit P4 approval naming the verified fresh backup, target code commit and maintenance window.
2. Notify the user that the local API/Desktop will be unavailable; do not close their Windows session or reboot.
3. Disable (do not delete) the scheduled task, request graceful stop, verify the exact Engine process exited and the loopback health endpoint is down. Ensure no second Engine/worker is writing.
4. Create a final pre-cutover backup with the P2 procedure, verify its sidecar, integrity, foreign keys and financial snapshot. If any check fails, leave original DB untouched and go NO-GO.
5. Apply migration to a staged copy of this final backup, not in-place. Run all P3 integrity and financial comparisons. Keep the original database byte-for-byte untouched while validating the staged migrated file.
6. Publish the migrated file only by a same-volume atomic replacement procedure that refuses to overwrite unexpectedly; retain the original and verified backup. Before publication, verify there are no SQLite `-wal`, `-shm`, or journal files that could be paired with the wrong database. If atomic replacement cannot be established on this Windows filesystem, stop and request a revised plan.
7. Start the approved Engine build against the migrated database while the scheduled task remains disabled. Verify health, schema mode, portfolio 1 values, legacy API, portfolio list, no unexpected operations, and local Desktop connection only if separately approved.
8. If all smoke tests pass, record the exact database hash, commit, task state and financial snapshot. Re-enable the existing task only after explicit cutover approval includes resuming autostart; verify the task is enabled and Engine has only one instance.

## Rollback

1. On any post-publication failure, immediately disable the scheduled task and request orderly Engine stop. Confirm process exit and health endpoint down before touching SQLite.
2. Preserve the failed migrated database, its WAL/SHM/journal files and logs in a timestamped diagnostic directory; do not delete or overwrite evidence.
3. Restore the verified pre-cutover backup to a new file using `backup.py restore --to <fresh-target>`. Verify checksum, `integrity_check`, `foreign_key_check`, and the original financial snapshot.
4. Switch back to the exact pre-cutover legacy database/code pair using a same-volume atomic file operation while Engine is stopped. If any sidecar remains or file identity is uncertain, stop and do not launch.
5. Start only the known-compatible legacy Engine with Task Scheduler still disabled. Verify health, portfolio 1 balance/positions/history and legacy API; confirm no new operations were added by the failed attempt.
6. Re-enable scheduled startup only after the user approves rollback completion. Keep both the failed migrated file and the verified backup until the user confirms the incident is resolved.

## User checklist for a separately approved cutover

- [ ] P2 snapshot authorization received; Engine process confirmed stopped; task disabled and original state recorded.
- [ ] Fresh backup and `.sha256` verified; copy stored outside the only disk containing the working DB.
- [ ] P3 rehearsal on this fresh backup passed all financial, migration, API, UI, integrity and restore checks.
- [ ] GO report reviewed; P4 production cutover explicitly approved.
- [ ] Final backup verified; rollback target and compatible code commit identified.
- [ ] No user-session end, reboot, real orders, strategy changes, or GitHub publication included.
- [ ] Post-cutover smoke tests pass before task autostart is re-enabled.

## Residual risks / currently unverified

- Normal Desktop connection to the working Engine at `127.0.0.1:8000` has not been interactively tested and is deliberately outside this plan's execution.
- The earlier rehearsal used an older backup; it is not evidence about the current working database.
- Working DB path and runtime requirements were inferred from tracked source/configuration; the file itself was not accessed.
- Windows atomic file replacement and SQLite sidecar behavior must be validated on the actual volume during rehearsal, without the live Engine.
- This repository does not currently expose an explicit migration version number; schema verification relies on structure and application checks.
- Backup checksum proves file identity/integrity, not correctness of application-level financial semantics; the financial snapshot and end-to-end rehearsal remain mandatory.
- No snapshot, migration, Engine stop/start, Task Scheduler change, production Desktop connection, or trade was performed for P1.
