# Taskloom

**Visual agent orchestration, woven around human control.**

[![CI](https://github.com/PlainJane20/taskloom/actions/workflows/ci.yml/badge.svg)](https://github.com/PlainJane20/taskloom/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-22c55e.svg)](LICENSE)
[![Tauri](https://img.shields.io/badge/Tauri-2-24c8db.svg)](https://tauri.app/)
[![React](https://img.shields.io/badge/React-TypeScript-61dafb.svg)](https://react.dev/)

Taskloom is an open-source, local-first desktop application that turns AI agent work into a visual Kanban workflow. It is designed for people who want the privacy and control of local agents without operating a command line, editing orchestration files, or managing Git worktrees.

Every proposed file mutation stops at a human approval boundary. Taskloom shows the original and proposed content side by side, writes only after explicit approval, and creates a recoverable snapshot before applying the change.

## Features

- **Visual agent workflow** — move work through Backlog, Active, Needs Approval, and Completed states.
- **Human-in-the-loop safety** — inspect a before/after preview before any generated file is written.
- **Local-first inference** — run models through Ollama on the local machine; OpenAI is available as an opt-in provider.
- **Durable state** — SQLite preserves tasks and pending approvals across restarts.
- **Recoverable writes** — automatic file snapshots support restoration after an approved mutation.
- **Workspace containment** — canonical path validation prevents agents from escaping the configured workspace.
- **Asynchronous IPC** — a typed JSON Lines protocol keeps the React interface responsive while the Python engine performs long-running work.
- **Desktop distribution** — Tauri provides a small native shell and platform-specific installers.

## Architecture

```text
┌──────────────────────────────────────────────────────────────────────┐
│                         Taskloom Desktop                             │
│                                                                      │
│  ┌──────────────────────────┐       JSON Lines       ┌────────────┐  │
│  │ React + TypeScript       │ ◄────────────────────► │ Python     │  │
│  │                          │       stdin/stdout      │ engine     │  │
│  │ KanbanBoard              │                        │            │  │
│  │ ApprovalModal            │                        │ Task state │  │
│  │ useAgentBridge           │                        │ File guard │  │
│  └─────────────┬────────────┘                        │ Snapshots  │  │
│                │                                     └─────┬──────┘  │
│          Tauri native shell                                │         │
└────────────────────────────────────────────────────────────┼─────────┘
                                                             │
                                      ┌──────────────────────┴────────┐
                                      │ LLM providers                 │
                                      │ Ollama (local) | OpenAI       │
                                      └───────────────────────────────┘

Approval path:
Backlog → Active → Proposed diff → Human approval → Snapshot → File write
                                      │
                                      └── Reject → Backlog (no write)
```

## Technology

| Layer | Technology | Responsibility |
|---|---|---|
| Desktop shell | Tauri 2 + Rust | Native lifecycle, packaging, and restricted process spawning |
| Interface | React 18 + TypeScript + Tailwind CSS | Kanban board, task creation, and approval experience |
| Engine | Python 3.11+ + `asyncio` | IPC handling, provider calls, task transitions, and safe file operations |
| Persistence | SQLite | Durable tasks and unresolved approval requests |
| Local AI | Ollama | Private inference using locally installed models |
| Testing | pytest + Vitest + React Testing Library | Engine, protocol, persistence, and approval interaction coverage |

## Quick start

### Prerequisites

- Node.js 20 or newer
- Python 3.11 or newer
- Rust stable and the platform prerequisites required by Tauri
- Ollama for local inference

### 1. Install dependencies

```bash
npm ci
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

### 2. Prepare a local coding model

```bash
ollama pull qwen2.5-coder:7b
ollama ls
```

For lower-memory machines, `llama3.2` is a smaller alternative. Taskloom defaults Ollama's `keep_alive` to `0`, releasing model memory when a response finishes.

### 3. Run Taskloom

```bash
TASKLOOM_OLLAMA_MODEL=qwen2.5-coder:7b npm run tauri dev
```

Create a task, provide a workspace-relative output path, press Play, review the generated file, and choose **Approve & apply** or **Reject**.

## Configuration

Taskloom reads configuration from environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `TASKLOOM_OLLAMA_MODEL` | `llama3.2` | Ollama model used for generation |
| `TASKLOOM_OLLAMA_URL` | `http://127.0.0.1:11434/api/generate` | Ollama generation endpoint |
| `TASKLOOM_OLLAMA_KEEP_ALIVE` | `0` | How long Ollama retains a model after generation |
| `OPENAI_API_KEY` | unset | Enables the opt-in OpenAI provider |
| `TASKLOOM_OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model used by the MVP adapter |

Runtime state and snapshots are stored under `.taskloom/` in the active workspace and are intentionally excluded from Git. Development mode uses the repository as the workspace. Packaged builds use `~/Documents/TaskloomWorkspace`.

## Safety model

Taskloom treats generated output as untrusted until a person approves it:

1. The engine resolves every target against the workspace root and rejects path traversal.
2. The model response is held as a pending change; the destination file remains untouched.
3. The interface displays before and after content for review.
4. Rejection deletes the pending change and returns the task to Backlog.
5. Approval snapshots the previous state before performing the write.

The MVP does not execute generated code or shell commands. Users should still review every proposal before approval.

## Testing

```bash
# Python engine, IPC, persistence, snapshots, and approval behavior
python -m pytest -q

# React approval interaction tests
npm test

# TypeScript and production frontend build
npm run build

# Native Tauri validation
cargo check --locked --manifest-path src-tauri/Cargo.toml
```

GitHub Actions runs all four checks for every push to `main` and every pull request targeting `main`.

## Build the desktop application

```bash
npm run tauri build
```

On macOS, Tauri writes the application bundle under `src-tauri/target/release/bundle/macos/` and, when the host supports disk-image creation, a DMG under `src-tauri/target/release/bundle/dmg/`. A distributable ZIP can be created with:

```bash
ditto -c -k --sequesterRsrc --keepParent \
  src-tauri/target/release/bundle/macos/Taskloom.app \
  src-tauri/target/release/bundle/Taskloom_0.1.0_aarch64.zip
```

Local ad-hoc builds may require approval in **System Settings → Privacy & Security** when opened on another machine. Public releases should use an Apple Developer ID and notarization.

The packaged MVP requires Python 3 and Ollama to be installed on the destination machine. A future release can replace that requirement with a compiled Python sidecar.

## Repository layout

```text
engine/main.py                         Async JSONL engine and safe file operations
src/components/KanbanBoard.tsx        Visual workflow and task creation
src/components/ApprovalModal.tsx      Before/after human approval boundary
src/hooks/useAgentBridge.ts            Tauri-to-Python process and IPC lifecycle
tests/test_engine.py                   Python unit and integration coverage
src/components/__tests__/              React interaction coverage
src-tauri/                              Native application and bundle configuration
.github/workflows/ci.yml               Cross-layer continuous integration
```

## Resume / key takeaways

- Designed a language-neutral, asynchronous JSON Lines IPC protocol that synchronizes a React/Tauri desktop interface with a long-running Python agent engine while preserving responsive optimistic UI state.
- Implemented a human-in-the-loop mutation interceptor with path containment, before/after review, SQLite-backed pending approvals, and automatic file snapshots so model output cannot modify files without explicit authorization.
- Built a local-first, provider-agnostic agent workflow supporting Ollama and OpenAI, durable Kanban state recovery, desktop packaging, and automated Python/React/TypeScript/Rust validation in CI.

## Roadmap

- Task editing, deletion, filtering, and run history
- Configurable workspaces and model settings in the interface
- Semantic syntax-aware diffs and automated validation gates
- Compiled Python sidecar for zero-dependency desktop installation
- Snapshot browser and one-click restore
- Additional agent adapters and multi-step workflows

## Contributing

Issues and pull requests are welcome. Keep changes focused, add tests for behavior changes, and ensure the full validation suite passes before opening a pull request.

## License

Taskloom is available under the [MIT License](LICENSE).
