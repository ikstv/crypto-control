# Crypto Control · Codex working rules

These rules apply to every task in this repository. GitHub `main` and the
committed project log are the source of truth. A task is not fully complete
until its safe evidence and outcome are committed and pushed, or a GitHub
access blocker is reported explicitly.

## Start every task

1. Read the current GitHub `main` state first. Fetch/pull when local Git access
   permits; reconcile local commits and uncommitted changes without discarding
   user work. If fetch/pull fails, use the connected GitHub view to inspect
   current files and disclose that local synchronization was unavailable.
2. Review the [project index](docs/project-index.md), linked log, the
   [experiment registry](docs/strategy-experiments.md), relevant protocols,
   existing results, tests, and recent commits. Search for prior work before
   repeating checks or experiments.
3. Do not repeat an existing check or hypothesis without a specific new
   question, changed input, defect, or reproducibility reason. Record why a
   justified rerun was needed and link its prior evidence.

## Before any experiment or test

1. Give the work a unique ID in the appropriate registry before running it.
   Freeze the protocol and commit it to GitHub first. Record the exact protocol
   commit that authorizes the run; never change parameters after observing
   outcomes. A change requires a new ID and a new protocol commit.
2. For ordinary software tests, first inspect existing test coverage and results;
   state the narrow reason and scope for any run. Do not call a routine
   verification a strategy experiment.

## Record and close work

- Add results to the canonical registry or project log: ID, question and
  duplicate-check, protocol commit when applicable, code/version commit,
  data source and time window, provenance or safe reproducibility details,
  commands/tests, metrics, limitations, decision, including negative or
  rejected findings, and next action.
- Keep a short [project index](docs/project-index.md) current: what is done,
  what must not be repeated, and what comes next. Link canonical registries,
  protocols, and logs rather than copying them.
- Never publish API keys, passwords, tokens, private IPs or server addresses,
  personal data, raw SQLite/database files, raw logs, or secret-bearing config.
  Publish only reviewed, sanitized summaries and safe reproducibility artifacts.
- Before committing, review the exact staged file list and diff for unrelated
  work or sensitive data. Preserve unrelated local modifications and backups.
- After the work is reviewed, commit and push it to GitHub, then report the
  commit hash and link. If GitHub is unavailable, do not claim completion:
  report the exact blocker and retain the result locally.
- Keep Crypto Control in research-only state: Shadow remains `ABSTAIN`;
  Paper Trading stays off. Do not start trading or signal processes or alter
  CC-EXP-003's frozen protocol unless the user explicitly authorizes that work.

## Current research guardrails

- CC-EXP-003 is frozen and collecting prospective data; do not alter, evaluate
  early, or restart it.
- CC-EXP-004 is locally prepared but **not registered on GitHub** as of the
  project state currently under reconciliation. Do not run it, change its
  parameters, or represent it as registered until its frozen protocol and
  registry entry are committed to GitHub.
