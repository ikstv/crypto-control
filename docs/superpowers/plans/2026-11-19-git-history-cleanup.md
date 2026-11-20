# Git History Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rewrite only local commits after the published baseline so generated artifacts and user-specific settings are absent from every commit intended for publication.

**Architecture:** Preserve published baseline `2f824e5474b4b0cfabe42776b0e26ee5decdc71c` as the immutable parent. Create a temporary Git bundle before changes, then replay post-baseline snapshots while removing prohibited paths and personal generated content. Verify the resulting commit range, tests, and Desktop publish without pushing.

**Tech Stack:** Git, PowerShell, Python/pytest, .NET SDK.

**Spec:** User task “Етап 6B — безпечне очищення локальної Git-історії перед першою повною публікацією”.

## Global Constraints

- Do not push, force-push, or rewrite the published baseline.
- Preserve all source code, documentation, portfolio database, Task Scheduler, and `.venv` on disk.
- Remove generated `bin/`, `obj/`, `.csproj.user`, local IDE settings, and personal absolute paths from publishable history.
- Run the full Python test suite and `dotnet publish` before claiming completion.

## Review Focus

- Published baseline must remain unchanged; verify its tree and SHA.
- Every future commit must be free of generated artifacts and secrets; scan all commit trees, not only HEAD.
- Working SQLite, Task Scheduler, and `.venv` must not be modified by cleanup; compare before/after metadata.
- Rewritten history must retain the final application tree; compare tracked file manifests and content.
- Build/test failures must be reported as evidence, not inferred away.

### Task 1: Snapshot and backup

- [ ] Record status, branch, published baseline, and post-baseline commit list.
- [ ] Create a Git bundle outside the repository and verify it can list the current refs.
- [ ] Record hashes and metadata for SQLite, `.venv`, and relevant scripts without modifying them.

### Task 2: Rewrite local range

- [ ] Recreate a new local branch from the published baseline, replaying post-baseline snapshots while excluding prohibited generated/user files.
- [ ] Preserve the existing working tree and pre-existing untracked plan.
- [ ] Fast-forward `main` only after the rewritten chain is verified.

### Task 3: Audit and verification

- [ ] Scan every commit in the publishable range for forbidden paths, secrets, personal paths, and oversized generated files.
- [ ] Compare final tracked tree to the pre-rewrite tree except for intentional removals.
- [ ] Run `pytest`, `git diff --check`, and `dotnet publish`.
- [ ] Report exact rewritten commit list and stop without pushing.
