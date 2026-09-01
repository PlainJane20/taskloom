# Changelog

All notable Taskloom changes are documented here. This project follows
[Semantic Versioning](https://semver.org/).

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
[0.3.0]: https://github.com/PlainJane20/taskloom/releases/tag/v0.3.0
[0.2.0]: https://github.com/PlainJane20/taskloom/releases/tag/v0.2.0
[0.1.0]: https://github.com/PlainJane20/taskloom/releases/tag/v0.1.0
