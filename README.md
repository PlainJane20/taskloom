<div align="center">
  <img src="src-tauri/icons/taskloom-icon-v2.png" alt="Taskloom logo" width="112" />

  # Taskloom

  **Visual agent orchestration, woven around human control.**

  A local-first desktop workspace that turns AI file operations into a visual,
  reviewable Kanban workflow—without requiring users to manage terminals,
  orchestration files, or Git worktrees.

  [![CI](https://github.com/PlainJane20/taskloom/actions/workflows/ci.yml/badge.svg)](https://github.com/PlainJane20/taskloom/actions/workflows/ci.yml)
  [![Release](https://img.shields.io/badge/release-v0.1.0-24c8db.svg)](https://github.com/PlainJane20/taskloom)
  [![License: MIT](https://img.shields.io/badge/license-MIT-22c55e.svg)](LICENSE)
  [![Tauri 2](https://img.shields.io/badge/desktop-Tauri%202-ffc131.svg)](https://tauri.app/)
  [![React + TypeScript](https://img.shields.io/badge/UI-React%20%2B%20TypeScript-61dafb.svg)](https://react.dev/)
  [![Python](https://img.shields.io/badge/engine-Python-3776ab.svg)](https://www.python.org/)

  [Why Taskloom](#why-taskloom) · [How it works](#how-it-works) · [Quick start](#quick-start) · [Architecture](#architecture) · [Roadmap](#roadmap)
</div>

---

> [!NOTE]
> **Project status:** Taskloom is a working MVP. Task creation, local model generation, durable board state, human approval, safe file writes, and snapshot creation are implemented and tested. See the [roadmap](#roadmap) for the path to a zero-dependency public release.

## Why Taskloom

Local coding agents are powerful, but their safest workflows still assume that the user is comfortable with command-line tools, configuration files, repository layouts, and raw diffs. That excludes many people who would benefit most from private, local AI automation.

Taskloom moves those controls into a desktop interface:

- **See the work** — every task has a visible state on a Kanban board.
- **Stay in control** — proposed file changes pause for explicit human review.
- **Keep data local** — Ollama runs supported models on the user's machine.
- **Recover safely** — accepted writes snapshot the previous file state first.
- **Resume seamlessly** — SQLite restores tasks and unresolved approvals after restart.

Taskloom does not execute generated code or silently modify files. The human remains the final authority at the mutation boundary.

## How it works

```text
Create task       Generate locally       Review proposal       Apply safely
    │                    │                      │                    │
    ▼                    ▼                      ▼                    ▼
┌─────────┐       ┌─────────────┐       ┌──────────────┐      ┌───────────┐
│ Backlog │ ────► │   Active    │ ────► │    Needs     │ ───► │ Completed │
└─────────┘       │ Ollama / API│       │   Approval   │      └───────────┘
                  └─────────────┘       └──────┬───────┘            │
                                               │                     ▼
                                        Reject │              Snapshot + write
                                               ▼
                                            Backlog
                                         (no file write)
```

1. A user creates a task with a title, instruction, provider, and workspace-relative target path.
2. The Python engine validates the path and asks the configured model for complete file contents.
3. The destination remains untouched while Taskloom displays the current and proposed content side by side.
4. **Reject** discards the proposal and returns the task to Backlog.
5. **Approve & apply** snapshots the previous state, writes the reviewed content, and completes the task.

## Core capabilities

| Capability | What Taskloom provides |
|---|---|
| Visual orchestration | Backlog, Active, Needs Approval, and Completed workflow states |
| Human-in-the-loop control | Before/after file review with explicit Approve and Reject actions |
| Local inference | Ollama support with configurable local models and memory-conscious unloading |
| Optional cloud inference | OpenAI provider adapter enabled only when the user supplies credentials |
| Durable state | SQLite persistence for tasks, errors, and unresolved approvals |
| File safety | Canonical path containment prevents writes outside the selected workspace |
| Recovery | Timestamped snapshots preserve the pre-write file state |
| Resilient output handling | Removes accidental outer Markdown fences without altering embedded content |
| Desktop distribution | Native Tauri shell with macOS application packaging and branded assets |
| Automated quality gates | Python, React, TypeScript, and Rust checks on every push and pull request |

## Architecture

Taskloom deliberately separates presentation, orchestration, and file authority. The React application never writes task output directly.

```text
┌──────────────────────────────── Taskloom Desktop ────────────────────────────────┐
│                                                                                  │
│  ┌─────────────────────────────┐       JSON Lines       ┌──────────────────────┐ │
│  │ React + TypeScript          │ ◄────────────────────► │ Python engine        │ │
│  │                             │      stdin/stdout       │                      │ │
│  │ • KanbanBoard               │                        │ • Task state machine │ │
│  │ • ApprovalModal             │                        │ • Workspace guard    │ │
│  │ • useAgentBridge            │                        │ • Snapshot store     │ │
│  └──────────────┬──────────────┘                        │ • Provider adapters  │ │
│                 │                                       └──────────┬───────────┘ │
│                 ▼                                                  │             │
│        Tauri 2 native shell                                        │             │
│        restricted process spawn                                    │             │
└────────────────────────────────────────────────────────────────────┼─────────────┘
                                                                     │
                         ┌───────────────────────────────────────────┴──────┐
                         │ Ollama (local)        OpenAI (optional cloud)   │
                         └──────────────────────────────────────────────────┘
```

### Technology stack

| Layer | Technology | Responsibility |
|---|---|---|
| Desktop | Tauri 2 + Rust | Native lifecycle, security capabilities, resource bundling, packaging |
| Interface | React 18 + TypeScript + Tailwind CSS | Board state, task forms, diff review, status feedback |
| Bridge | Tauri shell plugin + JSONL | Asynchronous process lifecycle and language-neutral IPC |
| Engine | Python 3.11+ + `asyncio` | Task transitions, model calls, validation, snapshots, file writes |
| Persistence | SQLite with WAL | Restart-safe tasks and pending approval requests |
| Providers | Ollama + OpenAI | Local-first generation with an opt-in cloud adapter |
| Testing | pytest + Vitest + React Testing Library | Unit, integration, persistence, and user-interaction coverage |

## Quick start

### Prerequisites

- macOS, Linux, or Windows development environment supported by Tauri
- Node.js 20 or newer
- Python 3.11 or newer
- Rust stable and the [Tauri platform prerequisites](https://v2.tauri.app/start/prerequisites/)
- [Ollama](https://ollama.com/) for local inference

### 1. Clone and install

```bash
git clone https://github.com/PlainJane20/taskloom.git
cd taskloom

npm ci
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

On Windows PowerShell, activate Python with `.venv\Scripts\Activate.ps1`.

### 2. Prepare a local model

```bash
ollama pull qwen2.5-coder:7b
ollama ls
```

`qwen2.5-coder:7b` produces stronger code than the smaller default model but requires more memory. `llama3.2` is a lighter alternative.

### 3. Launch Taskloom

```bash
TASKLOOM_OLLAMA_MODEL=qwen2.5-coder:7b npm run tauri dev
```

### 4. Run a first task

Use this safe example in the task form:

```text
Title: Create a TypeScript slug utility

Instruction: Return only a valid TypeScript module. Export a function named
slugify(text: string): string that lowercases text, converts whitespace and
underscores to hyphens, removes unsupported characters, collapses repeated
hyphens, and trims leading or trailing hyphens.

Target file: scratch/slugify.ts
Provider: Ollama
```

Press Play, review the proposal, and select **Approve & apply** only if the output is correct.

## Configuration

Taskloom uses environment variables so local secrets never need to enter the repository.

| Variable | Default | Purpose |
|---|---|---|
| `TASKLOOM_OLLAMA_MODEL` | `llama3.2` | Ollama model used for generation |
| `TASKLOOM_OLLAMA_URL` | `http://127.0.0.1:11434/api/generate` | Local Ollama endpoint |
| `TASKLOOM_OLLAMA_KEEP_ALIVE` | `0` | Model retention after generation; `0` unloads immediately |
| `OPENAI_API_KEY` | unset | Enables the optional OpenAI provider |
| `TASKLOOM_OPENAI_MODEL` | `gpt-4o-mini` | Model used by the current OpenAI adapter |

Development mode treats the checked-out repository as its workspace. Packaged builds use `~/Documents/TaskloomWorkspace`. Runtime state lives under `.taskloom/` inside the active workspace and is excluded from Git.

## Safety guarantees

Taskloom treats every generated response as untrusted until approval.

| Boundary | Enforcement |
|---|---|
| Workspace escape | Target paths are resolved and verified to remain beneath the workspace root |
| Unreviewed mutation | Model output is persisted as a pending change; the target file stays untouched |
| Rejected proposal | Pending content is deleted and the original file remains unchanged |
| Approved mutation | A snapshot is created before the reviewed content is written |
| Interrupted run | Active tasks recover to Backlog after restart |
| Interrupted approval | Pending approvals survive process and application restarts |
| Secret handling | Cloud credentials are read from environment variables and never stored in board state |

> [!IMPORTANT]
> Human approval reduces risk; it does not prove generated code is correct. Review proposals and run the appropriate tests before using generated output in production.

## Testing and quality

```bash
# Python IPC, task state, persistence, snapshot, and path-safety tests
python -m pytest -q

# React approval interaction tests
npm test

# TypeScript validation and optimized frontend build
npm run build

# Native Tauri/Rust validation
cargo check --locked --manifest-path src-tauri/Cargo.toml
```

The repository currently contains **19 automated tests**: 16 Python tests and 3 React interaction tests. [GitHub Actions](.github/workflows/ci.yml) runs the engine, frontend, and native validation jobs for every push to `main` and every pull request targeting `main`.

## Project structure

```text
taskloom/
├── engine/
│   └── main.py                         # Async engine, IPC, persistence, file safety
├── src/
│   ├── components/
│   │   ├── KanbanBoard.tsx             # Visual task workflow
│   │   └── ApprovalModal.tsx            # Human review boundary
│   ├── hooks/
│   │   └── useAgentBridge.ts            # Python process and JSONL lifecycle
│   └── types.ts                         # Shared frontend protocol types
├── src-tauri/
│   ├── capabilities/default.json        # Restricted native permissions
│   ├── icons/                           # Taskloom platform assets
│   └── tauri.conf.json                  # Desktop and bundle configuration
├── tests/test_engine.py                 # Python unit and integration suite
├── .github/workflows/ci.yml             # Continuous integration
└── README.md
```

## Build the desktop application

```bash
npm run tauri build
```

On macOS, the application bundle is written to:

```text
src-tauri/target/release/bundle/macos/Taskloom.app
```

Local ad-hoc builds may require approval in **System Settings → Privacy & Security**. Public releases should use an Apple Developer ID and notarization. The MVP package requires Python 3 and Ollama on the destination machine; a compiled sidecar is planned.

## Roadmap

- [ ] Task editing, deletion, filtering, and run history
- [ ] In-app workspace and model settings
- [ ] Syntax-aware diffs with line-level navigation
- [ ] Configurable validation gates before approval
- [ ] Snapshot browser and one-click restoration
- [ ] Compiled Python sidecar for zero-dependency installation
- [ ] Signed and notarized macOS releases
- [ ] Additional agent and provider adapters
- [ ] Multi-file task plans and dependency-aware workflows

## Engineering highlights

Taskloom demonstrates several production-oriented software engineering challenges in a compact desktop application:

- Designed a language-neutral asynchronous JSONL protocol that keeps a React/Tauri interface responsive while a long-running Python process performs local model inference.
- Implemented a human-in-the-loop mutation interceptor with path containment, durable pending approvals, before/after review, and pre-write snapshots.
- Coordinated state across React, a Python task state machine, and SQLite while recovering coherently from interrupted generation and approval flows.
- Established 19 automated tests and cross-language CI gates covering Python, React, TypeScript, and Rust.

## Contributing

Issues and focused pull requests are welcome.

1. Fork the repository and create a feature branch.
2. Add or update tests for behavior changes.
3. Run the full [quality suite](#testing-and-quality).
4. Open a pull request describing the user-facing impact and safety considerations.

Please avoid committing model files, generated workspaces, `.taskloom/` state, or secrets.

## License

Taskloom is open source under the [MIT License](LICENSE).

<div align="center">
  <sub>Built to make powerful local agents understandable, reviewable, and safe.</sub>
</div>
