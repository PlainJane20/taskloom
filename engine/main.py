#!/usr/bin/env python3
"""Taskloom's asynchronous JSON-lines agent engine.

Protocol: one JSON object per stdin line, one JSON object per stdout line.
Logs must go to stderr so stdout remains machine-readable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProtocolError(ValueError):
    """An invalid or unsupported IPC message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    status: str = "backlog"
    file_path: str | None = None
    provider: str = "ollama"
    error: str | None = None


@dataclass
class PendingChange:
    request_id: str
    task_id: str
    file_path: str
    before: str
    after: str
    summary: str
    workflow_run_id: str | None = None
    step_run_id: str | None = None


@dataclass
class AgentProfile:
    id: str
    name: str
    role: str
    instructions: str
    provider: str = "ollama"
    model: str | None = None
    capabilities: tuple[str, ...] = ("analysis",)


@dataclass
class WorkflowStep:
    id: str
    name: str
    agent_id: str
    kind: str
    instruction: str
    depends_on: tuple[str, ...] = ()


@dataclass
class Workflow:
    id: str
    name: str
    description: str
    approval_mode: str
    steps: tuple[WorkflowStep, ...]
    enabled: bool = True


@dataclass
class WorkflowRun:
    id: str
    workflow_id: str
    goal: str
    target_file: str
    status: str = "queued"
    current_step: str | None = None
    error: str | None = None
    plan_approved: bool = False
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class StepRun:
    id: str
    workflow_run_id: str
    step_id: str
    agent_id: str
    name: str
    kind: str
    status: str = "queued"
    output: str = ""
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class PlanApproval:
    request_id: str
    workflow_run_id: str
    workflow_id: str
    summary: str


class StateStore:
    """Durable SQLite storage for board state and unresolved approvals."""

    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                file_path TEXT,
                provider TEXT NOT NULL,
                error TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_changes (
                request_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                before_content TEXT NOT NULL,
                after_content TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                instructions TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT,
                capabilities TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                steps TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                target_file TEXT NOT NULL,
                status TEXT NOT NULL,
                current_step TEXT,
                error TEXT,
                plan_approved INTEGER NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflows(id)
            );

            CREATE TABLE IF NOT EXISTS step_runs (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                output TEXT NOT NULL,
                error TEXT,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(workflow_run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS plan_approvals (
                request_id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(workflow_run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            );
            """
        )
        self._ensure_column("pending_changes", "workflow_run_id", "TEXT")
        self._ensure_column("pending_changes", "step_run_id", "TEXT")

    def _ensure_column(self, table: str, name: str, declaration: str) -> None:
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            with self.connection:
                self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def load_tasks(self) -> list[Task]:
        rows = self.connection.execute(
            "SELECT id, title, prompt, status, file_path, provider, error FROM tasks ORDER BY updated_at, rowid"
        ).fetchall()
        return [
            Task(
                id=row["id"], title=row["title"], prompt=row["prompt"], status=row["status"],
                file_path=row["file_path"], provider=row["provider"], error=row["error"],
            )
            for row in rows
        ]

    def save_task(self, task: Task) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO tasks (id, title, prompt, status, file_path, provider, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    prompt = excluded.prompt,
                    status = excluded.status,
                    file_path = excluded.file_path,
                    provider = excluded.provider,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    task.id, task.title, task.prompt, task.status, task.file_path, task.provider,
                    task.error, datetime.now(timezone.utc).isoformat(),
                ),
            )

    def load_pending(self) -> list[PendingChange]:
        rows = self.connection.execute(
            """
            SELECT request_id, task_id, file_path, before_content, after_content, summary,
                   workflow_run_id, step_run_id
            FROM pending_changes ORDER BY created_at, rowid
            """
        ).fetchall()
        return [
            PendingChange(
                request_id=row["request_id"], task_id=row["task_id"], file_path=row["file_path"],
                before=row["before_content"], after=row["after_content"], summary=row["summary"],
                workflow_run_id=row["workflow_run_id"], step_run_id=row["step_run_id"],
            )
            for row in rows
        ]

    def save_pending(self, change: PendingChange) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO pending_changes
                    (request_id, task_id, file_path, before_content, after_content, summary,
                     created_at, workflow_run_id, step_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change.request_id, change.task_id, change.file_path, change.before, change.after,
                    change.summary, datetime.now(timezone.utc).isoformat(), change.workflow_run_id,
                    change.step_run_id,
                ),
            )

    def delete_pending(self, request_id: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM pending_changes WHERE request_id = ?", (request_id,))

    def load_agents(self) -> list[AgentProfile]:
        rows = self.connection.execute(
            "SELECT id, name, role, instructions, provider, model, capabilities FROM agents ORDER BY rowid"
        ).fetchall()
        return [AgentProfile(
            id=row["id"], name=row["name"], role=row["role"], instructions=row["instructions"],
            provider=row["provider"], model=row["model"],
            capabilities=tuple(json.loads(row["capabilities"])),
        ) for row in rows]

    def save_agent(self, agent: AgentProfile) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO agents (id, name, role, instructions, provider, model, capabilities, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, role=excluded.role,
                    instructions=excluded.instructions, provider=excluded.provider, model=excluded.model,
                    capabilities=excluded.capabilities, updated_at=excluded.updated_at
                """,
                (agent.id, agent.name, agent.role, agent.instructions, agent.provider, agent.model,
                 json.dumps(agent.capabilities), datetime.now(timezone.utc).isoformat()),
            )

    @staticmethod
    def _decode_steps(raw: str) -> tuple[WorkflowStep, ...]:
        return tuple(WorkflowStep(
            id=item["id"], name=item["name"], agent_id=item["agentId"], kind=item["kind"],
            instruction=item["instruction"], depends_on=tuple(item.get("dependsOn", [])),
        ) for item in json.loads(raw))

    @staticmethod
    def _encode_steps(steps: tuple[WorkflowStep, ...]) -> str:
        return json.dumps([{
            "id": step.id, "name": step.name, "agentId": step.agent_id, "kind": step.kind,
            "instruction": step.instruction, "dependsOn": list(step.depends_on),
        } for step in steps])

    def load_workflows(self) -> list[Workflow]:
        rows = self.connection.execute(
            "SELECT id, name, description, approval_mode, steps, enabled FROM workflows ORDER BY rowid"
        ).fetchall()
        return [Workflow(
            id=row["id"], name=row["name"], description=row["description"],
            approval_mode=row["approval_mode"], steps=self._decode_steps(row["steps"]),
            enabled=bool(row["enabled"]),
        ) for row in rows]

    def save_workflow(self, workflow: Workflow) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO workflows (id, name, description, approval_mode, steps, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
                    approval_mode=excluded.approval_mode, steps=excluded.steps,
                    enabled=excluded.enabled, updated_at=excluded.updated_at
                """,
                (workflow.id, workflow.name, workflow.description, workflow.approval_mode,
                 self._encode_steps(workflow.steps), int(workflow.enabled),
                 datetime.now(timezone.utc).isoformat()),
            )

    def load_workflow_runs(self) -> list[WorkflowRun]:
        rows = self.connection.execute(
            """SELECT id, workflow_id, goal, target_file, status, current_step, error,
                      plan_approved, started_at, completed_at FROM workflow_runs ORDER BY rowid"""
        ).fetchall()
        return [WorkflowRun(
            id=row["id"], workflow_id=row["workflow_id"], goal=row["goal"],
            target_file=row["target_file"], status=row["status"], current_step=row["current_step"],
            error=row["error"], plan_approved=bool(row["plan_approved"]),
            started_at=row["started_at"], completed_at=row["completed_at"],
        ) for row in rows]

    def save_workflow_run(self, run: WorkflowRun) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO workflow_runs
                    (id, workflow_id, goal, target_file, status, current_step, error,
                     plan_approved, started_at, completed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                    current_step=excluded.current_step, error=excluded.error,
                    plan_approved=excluded.plan_approved, started_at=excluded.started_at,
                    completed_at=excluded.completed_at, updated_at=excluded.updated_at
                """,
                (run.id, run.workflow_id, run.goal, run.target_file, run.status, run.current_step,
                 run.error, int(run.plan_approved), run.started_at, run.completed_at,
                 datetime.now(timezone.utc).isoformat()),
            )

    def load_step_runs(self) -> list[StepRun]:
        rows = self.connection.execute(
            """SELECT id, workflow_run_id, step_id, agent_id, name, kind, status, output,
                      error, started_at, completed_at FROM step_runs ORDER BY rowid"""
        ).fetchall()
        return [StepRun(
            id=row["id"], workflow_run_id=row["workflow_run_id"], step_id=row["step_id"],
            agent_id=row["agent_id"], name=row["name"], kind=row["kind"], status=row["status"],
            output=row["output"], error=row["error"], started_at=row["started_at"],
            completed_at=row["completed_at"],
        ) for row in rows]

    def save_step_run(self, step: StepRun) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO step_runs
                    (id, workflow_run_id, step_id, agent_id, name, kind, status, output,
                     error, started_at, completed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status, output=excluded.output,
                    error=excluded.error, started_at=excluded.started_at,
                    completed_at=excluded.completed_at, updated_at=excluded.updated_at
                """,
                (step.id, step.workflow_run_id, step.step_id, step.agent_id, step.name, step.kind,
                 step.status, step.output, step.error, step.started_at, step.completed_at,
                 datetime.now(timezone.utc).isoformat()),
            )

    def load_plan_approvals(self) -> list[PlanApproval]:
        rows = self.connection.execute(
            "SELECT request_id, workflow_run_id, workflow_id, summary FROM plan_approvals ORDER BY rowid"
        ).fetchall()
        return [PlanApproval(
            request_id=row["request_id"], workflow_run_id=row["workflow_run_id"],
            workflow_id=row["workflow_id"], summary=row["summary"],
        ) for row in rows]

    def save_plan_approval(self, approval: PlanApproval) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT OR REPLACE INTO plan_approvals
                   (request_id, workflow_run_id, workflow_id, summary, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (approval.request_id, approval.workflow_run_id, approval.workflow_id,
                 approval.summary, datetime.now(timezone.utc).isoformat()),
            )

    def delete_plan_approval(self, request_id: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM plan_approvals WHERE request_id = ?", (request_id,))


def parse_message(line: str) -> dict[str, Any]:
    """Parse and minimally validate a single IPC request."""
    if not line.strip():
        raise ProtocolError("empty_message", "IPC message cannot be empty")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError("invalid_json", f"Invalid JSON: {exc.msg}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("invalid_message", "IPC message must be a JSON object")
    if not isinstance(message.get("type"), str) or not message["type"]:
        raise ProtocolError("missing_type", "IPC message requires a string 'type'")
    if "payload" in message and not isinstance(message["payload"], dict):
        raise ProtocolError("invalid_payload", "'payload' must be a JSON object")
    return message


def normalize_generated_file(content: str) -> str:
    """Remove one Markdown fence that wraps an otherwise complete file.

    Models commonly add a fenced code block despite being asked for raw file
    contents. Only a matching outer wrapper is removed; embedded fences and
    ordinary file whitespace are left untouched.
    """
    stripped = content.strip()
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).rstrip() + "\n"
    return content


def atomic_write_text(destination: Path, content: str) -> None:
    """Replace a file atomically so an interrupted write cannot leave partial content."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


class SnapshotStore:
    """Creates recoverable copies while preventing paths outside the workspace."""

    def __init__(self, workspace: Path, snapshot_dir: Path | None = None) -> None:
        self.workspace = workspace.resolve()
        self.snapshot_dir = (snapshot_dir or self.workspace / ".taskloom" / "snapshots").resolve()

    def resolve_path(self, value: str) -> Path:
        candidate = (self.workspace / value).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise ProtocolError("unsafe_path", "File path must stay inside the workspace") from exc
        return candidate

    def create(self, file_path: str) -> str:
        source = self.resolve_path(file_path)
        snapshot_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
        target = self.snapshot_dir / snapshot_id / Path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            "snapshot_id": snapshot_id,
            "file_path": file_path,
            "existed": source.is_file(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if source.is_file():
            shutil.copy2(source, target)
        metadata_path = self.snapshot_dir / snapshot_id / "snapshot.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return snapshot_id

    def restore(self, snapshot_id: str) -> str:
        snapshot_root = (self.snapshot_dir / snapshot_id).resolve()
        try:
            snapshot_root.relative_to(self.snapshot_dir)
        except ValueError as exc:
            raise ProtocolError("unsafe_snapshot", "Invalid snapshot ID") from exc
        metadata_path = snapshot_root / "snapshot.json"
        if not metadata_path.is_file():
            raise ProtocolError("snapshot_not_found", f"Snapshot '{snapshot_id}' does not exist")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        destination = self.resolve_path(metadata["file_path"])
        if metadata["existed"]:
            source = snapshot_root / metadata["file_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        elif destination.exists():
            destination.unlink()
        return metadata["file_path"]


class LLMClient:
    """Dependency-free OpenAI/Ollama HTTP client suitable for a sidecar."""

    async def generate(
        self, prompt: str, current: str, provider: str, model: str | None = None,
    ) -> str:
        system = (
            "You edit one text file for a local user. Return only the complete new file "
            "contents: no Markdown fence and no commentary."
        )
        user = f"Requested change:\n{prompt}\n\nCurrent file:\n{current}"
        if provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ProtocolError("missing_api_key", "OPENAI_API_KEY is not configured")
            body = {
                "model": model or os.environ.get("TASKLOOM_OPENAI_MODEL", "gpt-4o-mini"),
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.2,
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            data = await asyncio.to_thread(self._post_json, "https://api.openai.com/v1/chat/completions", body, headers)
            return data["choices"][0]["message"]["content"]
        if provider == "ollama":
            body = {
                "model": model or os.environ.get("TASKLOOM_OLLAMA_MODEL", "llama3.2"),
                "prompt": f"{system}\n\n{user}",
                "stream": False,
                # Eco mode: release CPU/GPU memory as soon as generation ends.
                "keep_alive": os.environ.get("TASKLOOM_OLLAMA_KEEP_ALIVE", "0"),
            }
            url = os.environ.get("TASKLOOM_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
            data = await asyncio.to_thread(self._post_json, url, body, {})
            return data["response"]
        raise ProtocolError("unknown_provider", f"Unsupported provider: {provider}")

    @staticmethod
    def _post_json(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read())
        except urllib.error.URLError as exc:
            raise ProtocolError("provider_unavailable", f"LLM provider request failed: {exc.reason}") from exc


class TaskloomEngine:
    APPROVAL_MODES = {"observe", "approve_changes", "approve_plan", "trusted"}
    STEP_KINDS = {"analysis", "file_edit", "validate"}

    def __init__(self, workspace: Path, llm: LLMClient | None = None) -> None:
        self.workspace = workspace.resolve()
        self.snapshots = SnapshotStore(self.workspace)
        self.state = StateStore(self.workspace / ".taskloom" / "taskloom.db")
        self.llm = llm or LLMClient()
        self.tasks = {task.id: task for task in self.state.load_tasks()}
        self.pending = {change.request_id: change for change in self.state.load_pending()}
        self.agents = {agent.id: agent for agent in self.state.load_agents()}
        self.workflows = {workflow.id: workflow for workflow in self.state.load_workflows()}
        self.workflow_runs = {run.id: run for run in self.state.load_workflow_runs()}
        self.step_runs = {step.id: step for step in self.state.load_step_runs()}
        self.plan_approvals = {
            approval.request_id: approval for approval in self.state.load_plan_approvals()
        }
        self._run_lock = asyncio.Lock()
        self._seed_defaults()
        self._recover_interrupted_state()

    def _seed_defaults(self) -> None:
        if not self.agents:
            defaults = (
                AgentProfile(
                    "planner", "Planner", "Breaks goals into safe, concrete steps",
                    "Analyze the goal, identify constraints, and produce a concise implementation plan.",
                    capabilities=("analysis",),
                ),
                AgentProfile(
                    "builder", "Builder", "Creates focused file changes",
                    "Implement the approved goal precisely. Preserve unrelated behavior and return a complete file.",
                    capabilities=("file_edit",),
                ),
                AgentProfile(
                    "reviewer", "Reviewer", "Checks correctness and risk",
                    "Review the result for correctness, safety, missing requirements, and maintainability.",
                    capabilities=("analysis", "validate"),
                ),
            )
            for agent in defaults:
                self.agents[agent.id] = agent
                self.state.save_agent(agent)
        if not self.workflows:
            workflow = Workflow(
                id="safe-delivery",
                name="Safe delivery pipeline",
                description="Planner, Builder, and Reviewer collaborate on one workspace file.",
                approval_mode="approve_plan",
                steps=(
                    WorkflowStep("plan", "Plan", "planner", "analysis", "Create a safe implementation plan."),
                    WorkflowStep(
                        "implement", "Implement", "builder", "file_edit",
                        "Apply the goal to the target file using the plan.", ("plan",),
                    ),
                    WorkflowStep(
                        "validate", "Validate", "reviewer", "validate",
                        "Verify that the target exists and is not empty.", ("implement",),
                    ),
                    WorkflowStep(
                        "review", "Review", "reviewer", "analysis",
                        "Review the completed work and report remaining risks.", ("validate",),
                    ),
                ),
            )
            self.workflows[workflow.id] = workflow
            self.state.save_workflow(workflow)

    def _recover_interrupted_state(self) -> None:
        """Make task and workflow state coherent after an interrupted process."""
        pending_task_ids = {change.task_id for change in self.pending.values()}
        pending_workflow_ids = {
            change.workflow_run_id for change in self.pending.values() if change.workflow_run_id
        }
        plan_workflow_ids = {approval.workflow_run_id for approval in self.plan_approvals.values()}
        for task in self.tasks.values():
            if task.id in pending_task_ids and task.status != "needs_approval":
                task.status = "needs_approval"
                self.state.save_task(task)
            elif task.status == "active" or (
                task.status == "needs_approval" and task.id not in pending_task_ids
            ):
                task.status = "backlog"
                task.error = None
                self.state.save_task(task)
        for step in self.step_runs.values():
            if step.status == "running":
                step.status = "queued"
                step.started_at = None
                self.state.save_step_run(step)
        for run in self.workflow_runs.values():
            if run.id in pending_workflow_ids or run.id in plan_workflow_ids:
                run.status = "needs_approval"
            elif run.status == "running":
                run.status = "queued"
                run.error = "Recovered after Taskloom restarted; resume when ready."
            self.state.save_workflow_run(run)

    def update_task(self, task_id: str, status: str, **changes: Any) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise ProtocolError("task_not_found", f"Task '{task_id}' does not exist")
        allowed = {"backlog", "active", "needs_approval", "completed", "failed"}
        if status not in allowed:
            raise ProtocolError("invalid_status", f"Unsupported task status: {status}")
        task.status = status
        for key, value in changes.items():
            if hasattr(task, key):
                setattr(task, key, value)
        self.state.save_task(task)
        return task

    @staticmethod
    def serialize_task(task: Task) -> dict[str, Any]:
        return {
            "id": task.id, "title": task.title, "prompt": task.prompt, "status": task.status,
            "filePath": task.file_path, "provider": task.provider, "error": task.error,
        }

    @staticmethod
    def serialize_change(change: PendingChange) -> dict[str, Any]:
        return {
            "taskId": change.task_id, "requestId": change.request_id,
            "filePath": change.file_path, "before": change.before, "after": change.after,
            "summary": change.summary, "workflowRunId": change.workflow_run_id,
            "stepRunId": change.step_run_id,
        }

    @staticmethod
    def serialize_agent(agent: AgentProfile) -> dict[str, Any]:
        return {
            "id": agent.id, "name": agent.name, "role": agent.role,
            "instructions": agent.instructions, "provider": agent.provider, "model": agent.model,
            "capabilities": list(agent.capabilities),
        }

    @staticmethod
    def serialize_workflow(workflow: Workflow) -> dict[str, Any]:
        return {
            "id": workflow.id, "name": workflow.name, "description": workflow.description,
            "approvalMode": workflow.approval_mode, "enabled": workflow.enabled,
            "steps": [{
                "id": step.id, "name": step.name, "agentId": step.agent_id, "kind": step.kind,
                "instruction": step.instruction, "dependsOn": list(step.depends_on),
            } for step in workflow.steps],
        }

    @staticmethod
    def serialize_step_run(step: StepRun) -> dict[str, Any]:
        return {
            "id": step.id, "workflowRunId": step.workflow_run_id, "stepId": step.step_id,
            "agentId": step.agent_id, "name": step.name, "kind": step.kind,
            "status": step.status, "output": step.output, "error": step.error,
            "startedAt": step.started_at, "completedAt": step.completed_at,
        }

    def serialize_workflow_run(self, run: WorkflowRun) -> dict[str, Any]:
        steps = [step for step in self.step_runs.values() if step.workflow_run_id == run.id]
        return {
            "id": run.id, "workflowId": run.workflow_id, "goal": run.goal,
            "targetFile": run.target_file, "status": run.status,
            "currentStep": run.current_step, "error": run.error,
            "planApproved": run.plan_approved, "startedAt": run.started_at,
            "completedAt": run.completed_at,
            "steps": [self.serialize_step_run(step) for step in steps],
        }

    def serialize_plan_approval(self, approval: PlanApproval) -> dict[str, Any]:
        run = self.workflow_runs[approval.workflow_run_id]
        workflow = self.workflows[approval.workflow_id]
        return {
            "requestId": approval.request_id, "workflowRunId": run.id,
            "workflowName": workflow.name, "goal": run.goal, "targetFile": run.target_file,
            "summary": approval.summary,
            "steps": [{
                "name": step.name,
                "agentName": self.agents[step.agent_id].name,
                "kind": step.kind,
            } for step in workflow.steps],
        }

    def state_payload(self) -> dict[str, Any]:
        return {
            "tasks": [self.serialize_task(task) for task in self.tasks.values()],
            "approvals": [self.serialize_change(change) for change in self.pending.values()],
            "agents": [self.serialize_agent(agent) for agent in self.agents.values()],
            "workflows": [self.serialize_workflow(workflow) for workflow in self.workflows.values()],
            "workflowRuns": [
                self.serialize_workflow_run(run) for run in reversed(tuple(self.workflow_runs.values()))
            ],
            "planApprovals": [
                self.serialize_plan_approval(approval) for approval in self.plan_approvals.values()
            ],
        }

    def _validate_workflow(self, workflow: Workflow) -> None:
        if workflow.approval_mode not in self.APPROVAL_MODES:
            raise ProtocolError("invalid_approval_mode", "Unsupported workflow approval mode")
        if not workflow.steps:
            raise ProtocolError("empty_workflow", "A workflow requires at least one step")
        seen: set[str] = set()
        for step in workflow.steps:
            if step.id in seen:
                raise ProtocolError("duplicate_step", f"Duplicate workflow step '{step.id}'")
            if step.agent_id not in self.agents:
                raise ProtocolError("agent_not_found", f"Agent '{step.agent_id}' does not exist")
            if step.kind not in self.STEP_KINDS:
                raise ProtocolError("invalid_step_kind", f"Unsupported step kind '{step.kind}'")
            unknown = set(step.depends_on) - seen
            if unknown:
                raise ProtocolError(
                    "invalid_dependency",
                    f"Step '{step.name}' depends on unavailable steps: {', '.join(sorted(unknown))}",
                )
            seen.add(step.id)

    async def _execute_workflow(self, run: WorkflowRun) -> None:
        async with self._run_lock:
            if run.status == "cancelled":
                return
            workflow = self.workflows[run.workflow_id]
            run.status = "running"
            run.error = None
            run.started_at = run.started_at or datetime.now(timezone.utc).isoformat()
            self.state.save_workflow_run(run)
            completed = {
                step.step_id for step in self.step_runs.values()
                if step.workflow_run_id == run.id and step.status == "completed"
            }
            for definition in workflow.steps:
                step = self.step_runs[f"{run.id}:{definition.id}"]
                if step.status == "completed":
                    continue
                if run.status == "cancelled":
                    return
                if not set(definition.depends_on).issubset(completed):
                    run.status = "failed"
                    run.error = f"Dependencies for '{definition.name}' did not complete"
                    self.state.save_workflow_run(run)
                    return
                run.current_step = definition.id
                self.state.save_workflow_run(run)
                should_continue = await self._execute_step(workflow, run, definition, step)
                if not should_continue:
                    return
                completed.add(definition.id)
            run.status = "completed"
            run.current_step = None
            run.completed_at = datetime.now(timezone.utc).isoformat()
            self.state.save_workflow_run(run)

    async def _execute_step(
        self, workflow: Workflow, run: WorkflowRun, definition: WorkflowStep, step: StepRun,
    ) -> bool:
        agent = self.agents[definition.agent_id]
        step.status = "running"
        step.error = None
        step.started_at = datetime.now(timezone.utc).isoformat()
        self.state.save_step_run(step)
        dependency_outputs = [
            candidate.output for candidate in self.step_runs.values()
            if candidate.workflow_run_id == run.id and candidate.step_id in definition.depends_on
        ]
        context = "\n\n".join(output for output in dependency_outputs if output)
        prompt = (
            f"Agent role: {agent.role}\nAgent instructions: {agent.instructions}\n\n"
            f"Workflow goal: {run.goal}\nStep: {definition.instruction}"
        )
        if context:
            prompt += f"\n\nOutputs from completed dependencies:\n{context}"
        try:
            if definition.kind == "validate":
                target = self.snapshots.resolve_path(run.target_file)
                if not target.is_file() or not target.read_text(encoding="utf-8").strip():
                    raise ProtocolError("validation_failed", "Target file is missing or empty")
                step.output = f"Validated {run.target_file}: file exists and is not empty."
                step.status = "completed"
            elif definition.kind == "analysis":
                step.output = normalize_generated_file(
                    await self.llm.generate(prompt, context, agent.provider, agent.model)
                )
                step.status = "completed"
            else:
                target = self.snapshots.resolve_path(run.target_file)
                before = target.read_text(encoding="utf-8") if target.is_file() else ""
                proposed = normalize_generated_file(
                    await self.llm.generate(prompt, before, agent.provider, agent.model)
                )
                step.output = proposed
                task_id = f"workflow:{run.id}:{definition.id}"
                task = self.tasks.get(task_id) or Task(
                    id=task_id, title=f"{workflow.name}: {definition.name}", prompt=prompt,
                    status="active", file_path=run.target_file, provider=agent.provider,
                )
                self.tasks[task_id] = task
                self.state.save_task(task)
                if workflow.approval_mode == "observe":
                    step.status = "completed"
                    self.update_task(task_id, "completed")
                elif workflow.approval_mode == "approve_changes":
                    change = PendingChange(
                        request_id=str(uuid.uuid4()), task_id=task_id,
                        file_path=run.target_file, before=before, after=proposed,
                        summary=f"{agent.name} proposes updating {run.target_file}",
                        workflow_run_id=run.id, step_run_id=step.id,
                    )
                    self.pending[change.request_id] = change
                    self.state.save_pending(change)
                    self.update_task(task_id, "needs_approval")
                    step.status = "needs_approval"
                    run.status = "needs_approval"
                    self.state.save_step_run(step)
                    self.state.save_workflow_run(run)
                    return False
                elif workflow.approval_mode == "trusted" or (
                    workflow.approval_mode == "approve_plan" and run.plan_approved
                ):
                    self.snapshots.create(run.target_file)
                    atomic_write_text(target, proposed)
                    step.status = "completed"
                    self.update_task(task_id, "completed")
                else:
                    raise ProtocolError("plan_not_approved", "The workflow plan has not been approved")
            step.completed_at = datetime.now(timezone.utc).isoformat()
            self.state.save_step_run(step)
            return True
        except Exception as exc:
            step.status = "failed"
            step.error = str(exc)
            step.completed_at = datetime.now(timezone.utc).isoformat()
            run.status = "failed"
            run.error = f"{definition.name}: {exc}"
            run.completed_at = datetime.now(timezone.utc).isoformat()
            self.state.save_step_run(step)
            self.state.save_workflow_run(run)
            return False

    async def handle(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        request_id = message.get("id")
        kind = message["type"]
        payload = message.get("payload", {})
        if kind == "ping":
            return [self._response(request_id, "pong", {"workspace": str(self.workspace)})]
        if kind in {"list_tasks", "list_state"}:
            response_type = "task_list" if kind == "list_tasks" else "state_snapshot"
            return [self._response(request_id, response_type, self.state_payload())]
        if kind == "create_task":
            self._require(payload, "title", "prompt", "filePath")
            task = Task(
                id=str(payload.get("taskId") or uuid.uuid4()), title=str(payload["title"]),
                prompt=str(payload["prompt"]), file_path=str(payload["filePath"]),
                provider=str(payload.get("provider", "ollama")),
            )
            self.snapshots.resolve_path(task.file_path)
            self.tasks[task.id] = task
            self.state.save_task(task)
            return [self._response(request_id, "task_created", {"task": self.serialize_task(task)})]
        if kind == "update_task":
            self._require(payload, "taskId", "status")
            task = self.update_task(str(payload["taskId"]), str(payload["status"]))
            return [self._response(request_id, "task_updated", {"task": self.serialize_task(task)})]
        if kind == "run_task":
            self._require(payload, "taskId")
            task = self.update_task(str(payload["taskId"]), "active", error=None)
            async with self._run_lock:
                path = self.snapshots.resolve_path(task.file_path or "")
                current = path.read_text(encoding="utf-8") if path.is_file() else ""
                try:
                    proposed = normalize_generated_file(
                        await self.llm.generate(task.prompt, current, task.provider)
                    )
                except Exception as exc:
                    self.update_task(task.id, "failed", error=str(exc))
                    raise
            change = PendingChange(
                request_id=str(uuid.uuid4()), task_id=task.id, file_path=task.file_path or "",
                before=current, after=proposed, summary=f"Agent proposes updating {task.file_path}",
            )
            self.pending[change.request_id] = change
            self.state.save_pending(change)
            self.update_task(task.id, "needs_approval")
            return [
                self._response(request_id, "task_updated", {"task": self.serialize_task(task)}),
                {"type": "approval_required", "payload": self.serialize_change(change)},
            ]
        if kind == "create_agent":
            self._require(payload, "name", "role", "instructions")
            provider = str(payload.get("provider", "ollama"))
            if provider not in {"ollama", "openai"}:
                raise ProtocolError("unknown_provider", f"Unsupported provider: {provider}")
            capabilities = tuple(str(item) for item in payload.get("capabilities", ["analysis"]))
            if not capabilities or not set(capabilities).issubset(self.STEP_KINDS):
                raise ProtocolError("invalid_capability", "Agent capabilities are invalid")
            agent = AgentProfile(
                id=str(payload.get("agentId") or uuid.uuid4()), name=str(payload["name"]),
                role=str(payload["role"]), instructions=str(payload["instructions"]),
                provider=provider, model=str(payload["model"]) if payload.get("model") else None,
                capabilities=capabilities,
            )
            self.agents[agent.id] = agent
            self.state.save_agent(agent)
            return [self._response(request_id, "agent_created", {"agent": self.serialize_agent(agent)})]
        if kind == "create_workflow":
            self._require(payload, "name", "description", "approvalMode", "steps")
            raw_steps = payload["steps"]
            if not isinstance(raw_steps, list):
                raise ProtocolError("invalid_steps", "Workflow steps must be a list")
            steps = tuple(WorkflowStep(
                id=str(item.get("id") or uuid.uuid4()), name=str(item["name"]),
                agent_id=str(item["agentId"]), kind=str(item["kind"]),
                instruction=str(item["instruction"]),
                depends_on=tuple(str(value) for value in item.get("dependsOn", [])),
            ) for item in raw_steps)
            workflow = Workflow(
                id=str(payload.get("workflowId") or uuid.uuid4()), name=str(payload["name"]),
                description=str(payload["description"]), approval_mode=str(payload["approvalMode"]),
                steps=steps,
            )
            self._validate_workflow(workflow)
            self.workflows[workflow.id] = workflow
            self.state.save_workflow(workflow)
            return [self._response(
                request_id, "workflow_created", {"workflow": self.serialize_workflow(workflow)},
            )]
        if kind == "run_workflow":
            self._require(payload, "workflowId", "goal", "targetFile")
            workflow = self.workflows.get(str(payload["workflowId"]))
            if workflow is None or not workflow.enabled:
                raise ProtocolError("workflow_not_found", "Workflow is missing or disabled")
            self.snapshots.resolve_path(str(payload["targetFile"]))
            run = WorkflowRun(
                id=str(uuid.uuid4()), workflow_id=workflow.id, goal=str(payload["goal"]),
                target_file=str(payload["targetFile"]),
            )
            self.workflow_runs[run.id] = run
            self.state.save_workflow_run(run)
            for definition in workflow.steps:
                step = StepRun(
                    id=f"{run.id}:{definition.id}", workflow_run_id=run.id,
                    step_id=definition.id, agent_id=definition.agent_id,
                    name=definition.name, kind=definition.kind,
                )
                self.step_runs[step.id] = step
                self.state.save_step_run(step)
            created = self._response(
                request_id, "workflow_run_created", {"workflowRun": self.serialize_workflow_run(run)},
            )
            if workflow.approval_mode == "approve_plan":
                approval = PlanApproval(
                    request_id=str(uuid.uuid4()), workflow_run_id=run.id,
                    workflow_id=workflow.id,
                    summary=f"Approve {len(workflow.steps)} steps before Taskloom begins",
                )
                self.plan_approvals[approval.request_id] = approval
                self.state.save_plan_approval(approval)
                run.status = "needs_approval"
                self.state.save_workflow_run(run)
                return [created, {
                    "type": "plan_approval_required",
                    "payload": self.serialize_plan_approval(approval),
                }]
            await self._execute_workflow(run)
            events = [created, {"type": "state_snapshot", "payload": self.state_payload()}]
            events.extend(
                {"type": "approval_required", "payload": self.serialize_change(change)}
                for change in self.pending.values() if change.workflow_run_id == run.id
            )
            return events
        if kind == "plan_approval_decision":
            self._require(payload, "requestId", "decision")
            if payload["decision"] not in {"approve", "reject"}:
                raise ProtocolError("invalid_decision", "Decision must be 'approve' or 'reject'")
            approval = self.plan_approvals.get(str(payload["requestId"]))
            if approval is None:
                raise ProtocolError("request_not_found", "Plan approval is missing or already resolved")
            run = self.workflow_runs[approval.workflow_run_id]
            self.plan_approvals.pop(approval.request_id, None)
            self.state.delete_plan_approval(approval.request_id)
            if payload["decision"] == "reject":
                run.status = "cancelled"
                run.error = "Plan rejected by user"
                run.completed_at = datetime.now(timezone.utc).isoformat()
            elif payload["decision"] == "approve":
                run.plan_approved = True
                self.state.save_workflow_run(run)
                await self._execute_workflow(run)
            self.state.save_workflow_run(run)
            return [self._response(
                request_id, "workflow_run_updated", {"workflowRun": self.serialize_workflow_run(run)},
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
        if kind == "resume_workflow":
            self._require(payload, "workflowRunId")
            run = self.workflow_runs.get(str(payload["workflowRunId"]))
            if run is None:
                raise ProtocolError("workflow_run_not_found", "Workflow run does not exist")
            if run.status not in {"queued", "failed"}:
                raise ProtocolError("invalid_run_state", f"Cannot resume a {run.status} workflow")
            await self._execute_workflow(run)
            return [self._response(
                request_id, "workflow_run_updated", {"workflowRun": self.serialize_workflow_run(run)},
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
        if kind == "cancel_workflow":
            self._require(payload, "workflowRunId")
            run = self.workflow_runs.get(str(payload["workflowRunId"]))
            if run is None:
                raise ProtocolError("workflow_run_not_found", "Workflow run does not exist")
            run.status = "cancelled"
            run.error = "Cancelled by user"
            run.completed_at = datetime.now(timezone.utc).isoformat()
            self.state.save_workflow_run(run)
            return [self._response(
                request_id, "workflow_run_updated", {"workflowRun": self.serialize_workflow_run(run)},
            )]
        if kind == "approval_decision":
            self._require(payload, "requestId", "decision")
            decision = payload["decision"]
            if decision not in {"approve", "reject"}:
                raise ProtocolError("invalid_decision", "Decision must be 'approve' or 'reject'")
            change = self.pending.get(str(payload["requestId"]))
            if change is None:
                raise ProtocolError("request_not_found", "Approval request is missing or already resolved")
            task = self.tasks[change.task_id]
            snapshot_id: str | None = None
            if decision == "approve":
                snapshot_id = self.snapshots.create(change.file_path)
                atomic_write_text(self.snapshots.resolve_path(change.file_path), change.after)
                self.update_task(change.task_id, "completed")
            else:
                self.update_task(change.task_id, "backlog")
            self.pending.pop(change.request_id, None)
            self.state.delete_pending(change.request_id)
            if change.workflow_run_id and change.step_run_id:
                run = self.workflow_runs[change.workflow_run_id]
                step = self.step_runs[change.step_run_id]
                if decision == "approve":
                    step.status = "completed"
                    step.output = change.after
                    step.completed_at = datetime.now(timezone.utc).isoformat()
                    self.state.save_step_run(step)
                    await self._execute_workflow(run)
                else:
                    step.status = "rejected"
                    step.error = "Change rejected by user"
                    step.completed_at = datetime.now(timezone.utc).isoformat()
                    run.status = "cancelled"
                    run.error = f"{step.name} was rejected"
                    run.completed_at = datetime.now(timezone.utc).isoformat()
                    self.state.save_step_run(step)
                    self.state.save_workflow_run(run)
            return [self._response(
                request_id, "task_updated",
                {"task": self.serialize_task(task), "snapshotId": snapshot_id},
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
        if kind == "restore_snapshot":
            self._require(payload, "snapshotId")
            file_path = self.snapshots.restore(str(payload["snapshotId"]))
            return [self._response(request_id, "snapshot_restored", {"filePath": file_path})]
        if kind == "read_file":
            self._require(payload, "filePath")
            path = self.snapshots.resolve_path(str(payload["filePath"]))
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
            return [self._response(
                request_id, "file_content", {"filePath": payload["filePath"], "content": content},
            )]
        raise ProtocolError("unknown_message", f"Unsupported message type: {kind}")

    @staticmethod
    def _require(payload: dict[str, Any], *keys: str) -> None:
        missing = [key for key in keys if key not in payload or payload[key] == ""]
        if missing:
            raise ProtocolError("missing_field", f"Missing required field(s): {', '.join(missing)}")

    @staticmethod
    def _response(request_id: Any, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": request_id, "type": kind, "ok": True, "payload": payload}


async def run_stdio(engine: TaskloomEngine) -> None:
    """Read lines without blocking the event loop and emit serialized responses."""
    loop = asyncio.get_running_loop()
    write_lock = asyncio.Lock()
    jobs: set[asyncio.Task[None]] = set()

    async def emit(message: dict[str, Any]) -> None:
        async with write_lock:
            print(json.dumps(message, separators=(",", ":")), flush=True)

    async def process(raw: str) -> None:
        request_id: Any = None
        try:
            message = parse_message(raw)
            request_id = message.get("id")
            for response in await engine.handle(message):
                await emit(response)
        except ProtocolError as exc:
            await emit({"id": request_id, "type": "error", "ok": False, "error": {"code": exc.code, "message": str(exc)}})
        except Exception as exc:  # keep the sidecar alive after an isolated task failure
            print(f"Unexpected engine error: {exc}", file=sys.stderr)
            await emit({"id": request_id, "type": "error", "ok": False, "error": {"code": "internal_error", "message": str(exc)}})

    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if line == "":
            break
        job = asyncio.create_task(process(line))
        jobs.add(job)
        job.add_done_callback(jobs.discard)
    if jobs:
        await asyncio.gather(*jobs)


def cli() -> None:
    parser = argparse.ArgumentParser(description="Taskloom local engine")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Root directory agents may access")
    args = parser.parse_args()
    asyncio.run(run_stdio(TaskloomEngine(args.workspace)))


if __name__ == "__main__":
    cli()
