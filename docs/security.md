# Security and local data

Keep the API on loopback. The custom write header helps prevent accidental browser requests and is publicly known. Treat local processes as trusted and add authentication before any network exposure. WhiteBIT requests use public endpoints. Paper operations are local SQLite entries.

The emergency lock persists in SQLite and requires an explicit reset to return to stopped mode. It does not liquidate positions. Keep SQLite files, backups, credentials, local configuration, logs, caches, and build outputs outside public Git history.

## Back up and verify

From the repository root:

```powershell
$copy = .\.venv\Scripts\python.exe .\backup.py create
.\.venv\Scripts\python.exe .\backup.py verify "$copy"
.\.venv\Scripts\python.exe .\backup.py restore "$copy" --to .\recovered\crypto_control.sqlite3
```

Store the backup and its adjacent `.sha256` file together in a private location, ideally on a separate drive. The backup uses SQLite's backup API and verification checks the restored data without contacting WhiteBIT. Restore requires a fresh target. Stop the engine and preserve the existing data directory before switching to a restored database.

## Review before publishing

Review every commit in the proposed push for secrets, personal identifiers, and generated content. Ignore rules and later deletions only affect the current tree. Review commit metadata as well as file contents.
