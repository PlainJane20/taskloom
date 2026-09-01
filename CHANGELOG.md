# Changelog

All notable Taskloom changes are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [0.7.0] - 2026-09-01

### Added

- Provider-neutral issue adapter contract with a GitHub implementation backed by the official `gh` client.
- Dedicated Integrations workspace for repository connection checks, issue imports, sync health, conflicts, and history.
- Idempotent inbound import and reconciliation of open GitHub Issues into linked Taskloom cards.
- Automatic outbound Issue closure when a linked Taskloom card reaches Completed.
- Durable provider connections, external issue links, sync audit events, and retry scheduling in SQLite.
- Actionable GitHub Issue badges on imported cards and an explicit conflict-override control.

### Safety

- Delegates authentication to GitHub CLI and never reads or persists its token in Taskloom state.
- Invokes `gh` as an argument array without a shell and validates repositories as `owner/name`.
- Re-reads an Issue before closure and blocks outbound mutation when GitHub contains a newer edit.
- Queues retryable provider/rate-limit failures with bounded exponential backoff and a five-attempt cap.
- Filters pull requests returned by GitHub's shared Issues endpoint.

### Verification

- Expands automated coverage to 84 tests: 66 Python/MCP and 18 React interactions.
- Adds provider persistence, idempotency, reconciliation, conflict, retry, connection, import, and override tests.

## [0.6.0] - 2026-09-01

### Added

- Official MCP v2 stdio server with structured `create_task`, `update_task`, `add_log`, and board-state tools.
- Migration-safe agent sessions, task worklogs, bounded execution traces, ingestion events, and Git/PR links.
- Confidence gating that routes autonomous work below `0.70` to Drafts / Pending Review.
- Idempotent ingestion and 30-second correlation clustering with `X of Y subtasks done` progress.
- Session, agent, and branch swimlanes with real-time status badges and cross-agent directory collision warnings.
- One-click terminal trace viewer, confidence badges, progress bars, and actionable commit/PR metadata.
- Capability-gated cooperative pause, resume, and stop controls with a pollable MCP control state.

### Safety

- Keeps every MCP operation behind the same workspace guard and durable governance service as desktop IPC.
- Redacts common token, password, secret, API-key, and bearer-authorization patterns before persistence.
- Truncates stdout and stderr previews to 64 KiB and hashes persisted trace content for integrity checks.
- Uses stable idempotency keys and optimistic task versions to prevent duplicate or stale agent mutations.
- Preserves existing v0.1-v0.5 SQLite data through additive schema migration 6.

### Verification

- Expands automated coverage to 65 tests, including real MCP subprocess/stdio discovery.
- Verifies Python/MCP, React interaction, optimized TypeScript, and native Rust builds.

## [0.5.0] - 2026-09-01

### Added

- Durable filesystem triggers for individual files or wildcard-filtered folders.
- Visual file-watch creation, status, activity, pause/resume, and deletion controls.
- `{file}` goal templates that route the changed workspace-relative path into a workflow run.
- Restart-safe watch baselines, last-run metadata, and trigger errors in SQLite.

### Safety

- Records a baseline before enabling a watch, so existing files do not cause surprise runs.
- Enforces a 15-second minimum cooldown and starts at most one changed file per watch poll.
- Ignores `.git`, `.taskloom`, virtual environments, dependency folders, caches, and build output.
- Caps each watch at 2,000 matching files and skips symbolic links.
- Refreshes the triggering file's baseline after a workflow write to prevent feedback loops.
- Preserves workspace path containment and every workflow's existing approval policy.

## [0.4.0] - 2026-09-01

### Added

- Configurable validation commands entered as one argument per line.
- Captured command output in durable workflow step history and the visual run interface.
- Per-step validation timeouts from 1 to 900 seconds.
- Failure, timeout, persistence, and UI integration coverage.

### Safety

- Executes argument arrays directly without a shell or shell expansion.
- Restricts commands to an explicit executable allowlist.
- Rejects absolute and parent-relative command arguments.
- Uses a sanitized subprocess environment and limits captured output to 64 KiB.
- Blocks dependent workflow steps when validation exits nonzero or times out.

## [0.3.0] - 2026-09-01

### Added

- Durable interval schedules with pause, resume, run-now, next-run, and error state.
- Visual workflow editing, duplication, enable/disable, and archival controls.
- Retry and resume support for failed workflow runs.
- Persistent execution-event timelines for workflow and step lifecycle auditing.
- Schedule and workflow-management integration tests.

### Changed

- Expanded the automation studio with schedule cards and richer run operations.
- Advanced overdue schedules by one interval instead of replaying missed runs.
- Increased automated coverage to 35 tests across Python and React.

### Safety

- Enforced a 15-minute minimum recurring interval to protect local resources.
- Kept scheduled writes behind the workflow's selected approval policy.
- Preserved workspace path guards, pre-write snapshots, and atomic replacement.

## [0.2.0] - 2026-08-31

### Added

- Reusable multi-agent profiles and dependency-aware workflows.
- Observe, Approve Changes, Approve Plan, and Trusted autonomy policies.
- Durable workflow runs, step state, plan approvals, and local resource serialization.

## [0.1.0] - 2026-08-31

### Added

- Tauri, React, TypeScript, Tailwind CSS, and Python JSONL desktop MVP.
- Visual Kanban tasks, before/after approvals, snapshots, Ollama, and OpenAI adapters.

[0.4.0]: https://github.com/PlainJane20/taskloom/releases/tag/v0.4.0
[0.7.0]: https://github.com/PlainJane20/taskloom/releases/tag/v0.7.0
[0.6.0]: https://github.com/PlainJane20/taskloom/releases/tag/v0.6.0
[0.5.0]: https://github.com/PlainJane20/taskloom/releases/tag/v0.5.0
[0.3.0]: https://github.com/PlainJane20/taskloom/releases/tag/v0.3.0
[0.2.0]: https://github.com/PlainJane20/taskloom/releases/tag/v0.2.0
[0.1.0]: https://github.com/PlainJane20/taskloom/releases/tag/v0.1.0
