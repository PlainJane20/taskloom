#!/usr/bin/env python3
"""Taskloom's asynchronous JSON-lines agent engine.

Protocol: one JSON object per stdin line, one JSON object per stdout line.
Logs must go to stderr so stdout remains machine-readable.
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from .providers import GitHubCLIAdapter, IssueProvider, ProviderError, validate_repository
except ImportError:  # direct `python engine/main.py` execution
    from providers import GitHubCLIAdapter, IssueProvider, ProviderError, validate_repository


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
    source: str = "manual"
    governance_state: str = "accepted"
    confidence_score: float | None = None
    agent_id: str | None = None
    session_id: str | None = None
    branch_name: str | None = None
    parent_task_id: str | None = None
    cluster_key: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    version: int = 1
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class AgentSession:
    id: str
    agent_id: str
    status: str = "active"
    branch_name: str | None = None
    control_capabilities: tuple[str, ...] = ()
    started_at: str | None = None
    last_heartbeat_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


@dataclass
class TaskWorklog:
    id: str
    task_id: str
    message: str
    kind: str = "progress"
    agent_id: str | None = None
    session_id: str | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    trace_id: str | None = None
    created_at: str | None = None


@dataclass
class ExecutionTrace:
    id: str
    task_id: str
    worklog_id: str | None = None
    command_executed: str | None = None
    stdout_preview: str = ""
    stderr_preview: str = ""
    exit_code: int | None = None
    truncated: bool = False
    started_at: str | None = None
    completed_at: str | None = None
    content_sha256: str | None = None


@dataclass
class IngestionEvent:
    id: str
    idempotency_key: str
    source: str
    operation: str
    disposition: str
    raw_payload: str
    agent_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    cluster_id: str | None = None
    received_at: str | None = None
    processed_at: str | None = None
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
    command: tuple[str, ...] = ()
    timeout_seconds: int = 120


@dataclass
class Workflow:
    id: str
    name: str
    description: str
    approval_mode: str
    steps: tuple[WorkflowStep, ...]
    enabled: bool = True
    archived: bool = False


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


@dataclass
class AutomationTrigger:
    id: str
    workflow_id: str
    name: str
    interval_minutes: int
    goal: str
    target_file: str
    enabled: bool = True
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_run_id: str | None = None
    error: str | None = None


@dataclass
class FileTrigger:
    id: str
    workflow_id: str
    name: str
    watch_path: str
    pattern: str
    cooldown_seconds: int
    goal: str
    enabled: bool = True
    baseline: dict[str, tuple[int, int]] | None = None
    last_run_at: str | None = None
    last_run_id: str | None = None
    error: str | None = None


@dataclass
class ExecutionEvent:
    id: str
    workflow_run_id: str
    event_type: str
    message: str
    created_at: str
    step_run_id: str | None = None


@dataclass
class CommandResult:
    return_code: int | None
    output: str
    timed_out: bool = False


@dataclass
class ProviderConnection:
    id: str
    provider: str
    repository: str
    sync_direction: str = "bidirectional"
    auto_close: bool = True
    enabled: bool = True
    status: str = "not_tested"
    last_sync_at: str | None = None
    background_sync_enabled: bool = True
    sync_interval_minutes: int = 15
    next_sync_at: str | None = None
    last_success_at: str | None = None
    consecutive_failures: int = 0
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class SyncEvent:
    id: str
    connection_id: str
    direction: str
    action: str
    status: str
    message: str
    task_id: str | None = None
    external_id: str | None = None
    attempt_count: int = 0
    next_retry_at: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


@dataclass
class ExternalIssueLink:
    id: str
    task_id: str
    connection_id: str
    external_id: str
    issue_number: int
    url: str
    external_state: str
    external_updated_at: str
    last_synced_at: str


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
                source TEXT NOT NULL DEFAULT 'manual',
                governance_state TEXT NOT NULL DEFAULT 'accepted',
                confidence_score REAL,
                agent_id TEXT,
                session_id TEXT,
                branch_name TEXT,
                parent_task_id TEXT,
                cluster_key TEXT,
                progress_current INTEGER NOT NULL DEFAULT 0,
                progress_total INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_sessions (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                branch_name TEXT,
                status TEXT NOT NULL,
                control_capabilities TEXT NOT NULL DEFAULT '[]',
                started_at TEXT NOT NULL,
                last_heartbeat_at TEXT NOT NULL,
                completed_at TEXT,
                error TEXT,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS task_worklogs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                agent_id TEXT,
                session_id TEXT,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                progress_current INTEGER,
                progress_total INTEGER,
                trace_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS execution_traces (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                worklog_id TEXT,
                command_executed TEXT,
                stdout_preview TEXT NOT NULL,
                stderr_preview TEXT NOT NULL,
                exit_code INTEGER,
                truncated INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                completed_at TEXT,
                content_sha256 TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY(worklog_id) REFERENCES task_worklogs(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS ingestion_events (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                operation TEXT NOT NULL,
                agent_id TEXT,
                session_id TEXT,
                raw_payload TEXT NOT NULL,
                disposition TEXT NOT NULL,
                task_id TEXT,
                cluster_id TEXT,
                received_at TEXT NOT NULL,
                processed_at TEXT,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS task_links (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                provider TEXT,
                label TEXT,
                url TEXT,
                git_sha TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                UNIQUE(task_id, kind, url, git_sha)
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
                archived INTEGER NOT NULL DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS automation_triggers (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                name TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL,
                goal TEXT NOT NULL,
                target_file TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                next_run_at TEXT,
                last_run_at TEXT,
                last_run_id TEXT,
                error TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflows(id)
            );

            CREATE TABLE IF NOT EXISTS file_triggers (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                name TEXT NOT NULL,
                watch_path TEXT NOT NULL,
                pattern TEXT NOT NULL,
                cooldown_seconds INTEGER NOT NULL,
                goal TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                baseline TEXT NOT NULL,
                last_run_at TEXT,
                last_run_id TEXT,
                error TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflows(id)
            );

            CREATE TABLE IF NOT EXISTS execution_events (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                step_run_id TEXT,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(workflow_run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS provider_connections (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                repository TEXT NOT NULL,
                sync_direction TEXT NOT NULL,
                auto_close INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                status TEXT NOT NULL,
                last_sync_at TEXT,
                background_sync_enabled INTEGER NOT NULL DEFAULT 1,
                sync_interval_minutes INTEGER NOT NULL DEFAULT 15,
                next_sync_at TEXT,
                last_success_at TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(provider, repository)
            );

            CREATE TABLE IF NOT EXISTS sync_events (
                id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                task_id TEXT,
                external_id TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(connection_id) REFERENCES provider_connections(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS external_issue_links (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                url TEXT NOT NULL,
                external_state TEXT NOT NULL,
                external_updated_at TEXT NOT NULL,
                last_synced_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY(connection_id) REFERENCES provider_connections(id) ON DELETE CASCADE,
                UNIQUE(connection_id, external_id)
            );
            """
        )
        self._ensure_column("pending_changes", "workflow_run_id", "TEXT")
        self._ensure_column("pending_changes", "step_run_id", "TEXT")
        self._ensure_column("workflows", "archived", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("tasks", "source", "TEXT NOT NULL DEFAULT 'manual'")
        self._ensure_column("tasks", "governance_state", "TEXT NOT NULL DEFAULT 'accepted'")
        self._ensure_column("tasks", "confidence_score", "REAL")
        self._ensure_column("tasks", "agent_id", "TEXT")
        self._ensure_column("tasks", "session_id", "TEXT")
        self._ensure_column("tasks", "branch_name", "TEXT")
        self._ensure_column("tasks", "parent_task_id", "TEXT")
        self._ensure_column("tasks", "cluster_key", "TEXT")
        self._ensure_column("tasks", "progress_current", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("tasks", "progress_total", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("tasks", "version", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("tasks", "created_at", "TEXT")
        self._ensure_column(
            "provider_connections", "background_sync_enabled", "INTEGER NOT NULL DEFAULT 1",
        )
        self._ensure_column(
            "provider_connections", "sync_interval_minutes", "INTEGER NOT NULL DEFAULT 15",
        )
        self._ensure_column("provider_connections", "next_sync_at", "TEXT")
        self._ensure_column("provider_connections", "last_success_at", "TEXT")
        self._ensure_column(
            "provider_connections", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0",
        )
        with self.connection:
            self.connection.execute(
                "UPDATE tasks SET created_at = updated_at WHERE created_at IS NULL"
            )
            self.connection.execute(
                """INSERT OR IGNORE INTO schema_migrations
                   (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)""",
                (6, "governance-foundation", "taskloom-v0.6-governance",
                 datetime.now(timezone.utc).isoformat()),
            )
            self.connection.execute(
                """INSERT OR IGNORE INTO schema_migrations
                   (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)""",
                (7, "provider-sync-foundation", "taskloom-v0.7-provider-sync",
                 datetime.now(timezone.utc).isoformat()),
            )
            self.connection.execute(
                """INSERT OR IGNORE INTO schema_migrations
                   (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)""",
                (8, "provider-background-sync", "taskloom-v0.8-background-sync",
                 datetime.now(timezone.utc).isoformat()),
            )

    def _ensure_column(self, table: str, name: str, declaration: str) -> None:
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            with self.connection:
                self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def load_tasks(self) -> list[Task]:
        rows = self.connection.execute(
            """SELECT id, title, prompt, status, file_path, provider, error, source,
                      governance_state, confidence_score, agent_id, session_id, branch_name,
                      parent_task_id, cluster_key, progress_current, progress_total, version,
                      created_at, updated_at
                 FROM tasks ORDER BY updated_at, rowid"""
        ).fetchall()
        return [
            Task(
                id=row["id"], title=row["title"], prompt=row["prompt"], status=row["status"],
                file_path=row["file_path"], provider=row["provider"], error=row["error"],
                source=row["source"], governance_state=row["governance_state"],
                confidence_score=row["confidence_score"], agent_id=row["agent_id"],
                session_id=row["session_id"], branch_name=row["branch_name"],
                parent_task_id=row["parent_task_id"], cluster_key=row["cluster_key"],
                progress_current=row["progress_current"], progress_total=row["progress_total"],
                version=row["version"], created_at=row["created_at"], updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def save_task(self, task: Task) -> None:
        now = datetime.now(timezone.utc).isoformat()
        task.created_at = task.created_at or now
        task.updated_at = now
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO tasks
                    (id, title, prompt, status, file_path, provider, error, source,
                     governance_state, confidence_score, agent_id, session_id, branch_name,
                     parent_task_id, cluster_key, progress_current, progress_total, version,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    prompt = excluded.prompt,
                    status = excluded.status,
                    file_path = excluded.file_path,
                    provider = excluded.provider,
                    error = excluded.error,
                    source = excluded.source,
                    governance_state = excluded.governance_state,
                    confidence_score = excluded.confidence_score,
                    agent_id = excluded.agent_id,
                    session_id = excluded.session_id,
                    branch_name = excluded.branch_name,
                    parent_task_id = excluded.parent_task_id,
                    cluster_key = excluded.cluster_key,
                    progress_current = excluded.progress_current,
                    progress_total = excluded.progress_total,
                    version = excluded.version,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    task.id, task.title, task.prompt, task.status, task.file_path, task.provider,
                    task.error, task.source, task.governance_state, task.confidence_score,
                    task.agent_id, task.session_id, task.branch_name, task.parent_task_id,
                    task.cluster_key, task.progress_current, task.progress_total, task.version,
                    task.created_at, task.updated_at,
                ),
            )

    def load_sessions(self) -> list[AgentSession]:
        rows = self.connection.execute(
            """SELECT id, agent_id, branch_name, status, control_capabilities, started_at,
                      last_heartbeat_at, completed_at, error
                 FROM agent_sessions ORDER BY started_at, rowid"""
        ).fetchall()
        return [AgentSession(
            id=row["id"], agent_id=row["agent_id"], branch_name=row["branch_name"],
            status=row["status"],
            control_capabilities=tuple(json.loads(row["control_capabilities"])),
            started_at=row["started_at"], last_heartbeat_at=row["last_heartbeat_at"],
            completed_at=row["completed_at"], error=row["error"],
        ) for row in rows]

    def save_session(self, session: AgentSession) -> None:
        now = datetime.now(timezone.utc).isoformat()
        session.started_at = session.started_at or now
        session.last_heartbeat_at = session.last_heartbeat_at or now
        with self.connection:
            self.connection.execute(
                """INSERT INTO agent_sessions
                   (id, agent_id, branch_name, status, control_capabilities, started_at,
                    last_heartbeat_at, completed_at, error, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
                   ON CONFLICT(id) DO UPDATE SET agent_id=excluded.agent_id,
                    branch_name=excluded.branch_name, status=excluded.status,
                    control_capabilities=excluded.control_capabilities,
                    last_heartbeat_at=excluded.last_heartbeat_at,
                    completed_at=excluded.completed_at, error=excluded.error""",
                (session.id, session.agent_id, session.branch_name, session.status,
                 json.dumps(session.control_capabilities), session.started_at,
                 session.last_heartbeat_at, session.completed_at, session.error),
            )

    def load_worklogs(self) -> list[TaskWorklog]:
        rows = self.connection.execute(
            """SELECT id, task_id, agent_id, session_id, kind, message, progress_current,
                      progress_total, trace_id, created_at
                 FROM task_worklogs ORDER BY created_at, rowid"""
        ).fetchall()
        return [TaskWorklog(
            id=row["id"], task_id=row["task_id"], agent_id=row["agent_id"],
            session_id=row["session_id"], kind=row["kind"], message=row["message"],
            progress_current=row["progress_current"], progress_total=row["progress_total"],
            trace_id=row["trace_id"], created_at=row["created_at"],
        ) for row in rows]

    def save_worklog(self, worklog: TaskWorklog) -> None:
        worklog.created_at = worklog.created_at or datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                """INSERT INTO task_worklogs
                   (id, task_id, agent_id, session_id, kind, message, progress_current,
                    progress_total, trace_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id,
                    agent_id=excluded.agent_id, session_id=excluded.session_id,
                    kind=excluded.kind, message=excluded.message,
                    progress_current=excluded.progress_current,
                    progress_total=excluded.progress_total, trace_id=excluded.trace_id,
                    created_at=excluded.created_at""",
                (worklog.id, worklog.task_id, worklog.agent_id, worklog.session_id,
                 worklog.kind, worklog.message, worklog.progress_current,
                 worklog.progress_total, worklog.trace_id, worklog.created_at),
            )

    def load_traces(self) -> list[ExecutionTrace]:
        rows = self.connection.execute(
            """SELECT id, task_id, worklog_id, command_executed, stdout_preview,
                      stderr_preview, exit_code, truncated, started_at, completed_at,
                      content_sha256 FROM execution_traces ORDER BY rowid"""
        ).fetchall()
        return [ExecutionTrace(
            id=row["id"], task_id=row["task_id"], worklog_id=row["worklog_id"],
            command_executed=row["command_executed"], stdout_preview=row["stdout_preview"],
            stderr_preview=row["stderr_preview"], exit_code=row["exit_code"],
            truncated=bool(row["truncated"]), started_at=row["started_at"],
            completed_at=row["completed_at"], content_sha256=row["content_sha256"],
        ) for row in rows]

    def save_trace(self, trace: ExecutionTrace) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO execution_traces
                   (id, task_id, worklog_id, command_executed, stdout_preview, stderr_preview,
                    exit_code, truncated, started_at, completed_at, content_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id,
                    worklog_id=excluded.worklog_id, command_executed=excluded.command_executed,
                    stdout_preview=excluded.stdout_preview, stderr_preview=excluded.stderr_preview,
                    exit_code=excluded.exit_code, truncated=excluded.truncated,
                    started_at=excluded.started_at, completed_at=excluded.completed_at,
                    content_sha256=excluded.content_sha256""",
                (trace.id, trace.task_id, trace.worklog_id, trace.command_executed,
                 trace.stdout_preview, trace.stderr_preview, trace.exit_code,
                 int(trace.truncated), trace.started_at, trace.completed_at,
                 trace.content_sha256),
            )

    def load_ingestion_event(self, idempotency_key: str) -> IngestionEvent | None:
        row = self.connection.execute(
            """SELECT id, idempotency_key, source, operation, agent_id, session_id,
                      raw_payload, disposition, task_id, cluster_id, received_at,
                      processed_at, error FROM ingestion_events WHERE idempotency_key = ?""",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return IngestionEvent(
            id=row["id"], idempotency_key=row["idempotency_key"], source=row["source"],
            operation=row["operation"], agent_id=row["agent_id"], session_id=row["session_id"],
            raw_payload=row["raw_payload"], disposition=row["disposition"],
            task_id=row["task_id"], cluster_id=row["cluster_id"],
            received_at=row["received_at"], processed_at=row["processed_at"], error=row["error"],
        )

    def save_ingestion_event(self, event: IngestionEvent) -> None:
        now = datetime.now(timezone.utc).isoformat()
        event.received_at = event.received_at or now
        event.processed_at = event.processed_at or now
        with self.connection:
            self.connection.execute(
                """INSERT INTO ingestion_events
                   (id, idempotency_key, source, operation, agent_id, session_id, raw_payload,
                    disposition, task_id, cluster_id, received_at, processed_at, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event.id, event.idempotency_key, event.source, event.operation,
                 event.agent_id, event.session_id, event.raw_payload, event.disposition,
                 event.task_id, event.cluster_id, event.received_at, event.processed_at,
                 event.error),
            )

    def save_task_link(
        self, task_id: str, kind: str, *, url: str | None = None,
        git_sha: str | None = None, provider: str | None = None, label: str | None = None,
    ) -> None:
        with self.connection:
            existing = self.connection.execute(
                """SELECT id FROM task_links
                   WHERE task_id = ? AND kind = ? AND COALESCE(url, '') = ?
                         AND COALESCE(git_sha, '') = ?""",
                (task_id, kind, url or "", git_sha or ""),
            ).fetchone()
            if existing:
                return
            self.connection.execute(
                """INSERT OR IGNORE INTO task_links
                   (id, task_id, kind, provider, label, url, git_sha, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), task_id, kind, provider, label, url, git_sha,
                 datetime.now(timezone.utc).isoformat()),
            )

    def load_task_links(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT id, kind, provider, label, url, git_sha, created_at
                 FROM task_links WHERE task_id = ? ORDER BY created_at, rowid""",
            (task_id,),
        ).fetchall()
        return [{
            "id": row["id"], "kind": row["kind"], "provider": row["provider"],
            "label": row["label"], "url": row["url"], "gitSha": row["git_sha"],
            "createdAt": row["created_at"],
        } for row in rows]

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
            command=tuple(str(value) for value in item.get("command", [])),
            timeout_seconds=int(item.get("timeoutSeconds", 120)),
        ) for item in json.loads(raw))

    @staticmethod
    def _encode_steps(steps: tuple[WorkflowStep, ...]) -> str:
        return json.dumps([{
            "id": step.id, "name": step.name, "agentId": step.agent_id, "kind": step.kind,
            "instruction": step.instruction, "dependsOn": list(step.depends_on),
            "command": list(step.command), "timeoutSeconds": step.timeout_seconds,
        } for step in steps])

    def load_workflows(self) -> list[Workflow]:
        rows = self.connection.execute(
            "SELECT id, name, description, approval_mode, steps, enabled, archived FROM workflows ORDER BY rowid"
        ).fetchall()
        return [Workflow(
            id=row["id"], name=row["name"], description=row["description"],
            approval_mode=row["approval_mode"], steps=self._decode_steps(row["steps"]),
            enabled=bool(row["enabled"]), archived=bool(row["archived"]),
        ) for row in rows]

    def save_workflow(self, workflow: Workflow) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO workflows
                    (id, name, description, approval_mode, steps, enabled, archived, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
                    approval_mode=excluded.approval_mode, steps=excluded.steps,
                    enabled=excluded.enabled, archived=excluded.archived,
                    updated_at=excluded.updated_at
                """,
                (workflow.id, workflow.name, workflow.description, workflow.approval_mode,
                 self._encode_steps(workflow.steps), int(workflow.enabled),
                 int(workflow.archived),
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

    def load_triggers(self) -> list[AutomationTrigger]:
        rows = self.connection.execute(
            """SELECT id, workflow_id, name, interval_minutes, goal, target_file, enabled,
                      next_run_at, last_run_at, last_run_id, error
                 FROM automation_triggers ORDER BY rowid"""
        ).fetchall()
        return [AutomationTrigger(
            id=row["id"], workflow_id=row["workflow_id"], name=row["name"],
            interval_minutes=row["interval_minutes"], goal=row["goal"],
            target_file=row["target_file"], enabled=bool(row["enabled"]),
            next_run_at=row["next_run_at"], last_run_at=row["last_run_at"],
            last_run_id=row["last_run_id"], error=row["error"],
        ) for row in rows]

    def save_trigger(self, trigger: AutomationTrigger) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO automation_triggers
                    (id, workflow_id, name, interval_minutes, goal, target_file, enabled,
                     next_run_at, last_run_at, last_run_id, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET workflow_id=excluded.workflow_id,
                    name=excluded.name, interval_minutes=excluded.interval_minutes,
                    goal=excluded.goal, target_file=excluded.target_file,
                    enabled=excluded.enabled, next_run_at=excluded.next_run_at,
                    last_run_at=excluded.last_run_at, last_run_id=excluded.last_run_id,
                    error=excluded.error, updated_at=excluded.updated_at
                """,
                (trigger.id, trigger.workflow_id, trigger.name, trigger.interval_minutes,
                 trigger.goal, trigger.target_file, int(trigger.enabled), trigger.next_run_at,
                 trigger.last_run_at, trigger.last_run_id, trigger.error,
                 datetime.now(timezone.utc).isoformat()),
            )

    def delete_trigger(self, trigger_id: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM automation_triggers WHERE id = ?", (trigger_id,))

    def load_file_triggers(self) -> list[FileTrigger]:
        rows = self.connection.execute(
            """SELECT id, workflow_id, name, watch_path, pattern, cooldown_seconds, goal,
                      enabled, baseline, last_run_at, last_run_id, error
                 FROM file_triggers ORDER BY rowid"""
        ).fetchall()
        return [FileTrigger(
            id=row["id"], workflow_id=row["workflow_id"], name=row["name"],
            watch_path=row["watch_path"], pattern=row["pattern"],
            cooldown_seconds=row["cooldown_seconds"], goal=row["goal"],
            enabled=bool(row["enabled"]),
            baseline={
                path: (int(fingerprint[0]), int(fingerprint[1]))
                for path, fingerprint in json.loads(row["baseline"]).items()
            },
            last_run_at=row["last_run_at"], last_run_id=row["last_run_id"],
            error=row["error"],
        ) for row in rows]

    def save_file_trigger(self, trigger: FileTrigger) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO file_triggers
                    (id, workflow_id, name, watch_path, pattern, cooldown_seconds, goal,
                     enabled, baseline, last_run_at, last_run_id, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET workflow_id=excluded.workflow_id,
                    name=excluded.name, watch_path=excluded.watch_path,
                    pattern=excluded.pattern, cooldown_seconds=excluded.cooldown_seconds,
                    goal=excluded.goal, enabled=excluded.enabled, baseline=excluded.baseline,
                    last_run_at=excluded.last_run_at, last_run_id=excluded.last_run_id,
                    error=excluded.error, updated_at=excluded.updated_at
                """,
                (trigger.id, trigger.workflow_id, trigger.name, trigger.watch_path,
                 trigger.pattern, trigger.cooldown_seconds, trigger.goal, int(trigger.enabled),
                 json.dumps(trigger.baseline or {}, sort_keys=True), trigger.last_run_at,
                 trigger.last_run_id, trigger.error, datetime.now(timezone.utc).isoformat()),
            )

    def delete_file_trigger(self, trigger_id: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM file_triggers WHERE id = ?", (trigger_id,))

    def load_events(self) -> list[ExecutionEvent]:
        rows = self.connection.execute(
            """SELECT id, workflow_run_id, step_run_id, event_type, message, created_at
                 FROM execution_events ORDER BY created_at, rowid"""
        ).fetchall()
        return [ExecutionEvent(
            id=row["id"], workflow_run_id=row["workflow_run_id"],
            step_run_id=row["step_run_id"], event_type=row["event_type"],
            message=row["message"], created_at=row["created_at"],
        ) for row in rows]

    def save_event(self, event: ExecutionEvent) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO execution_events
                   (id, workflow_run_id, step_run_id, event_type, message, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event.id, event.workflow_run_id, event.step_run_id,
                 event.event_type, event.message, event.created_at),
            )

    def load_provider_connections(self) -> list[ProviderConnection]:
        rows = self.connection.execute(
            """SELECT id, provider, repository, sync_direction, auto_close, enabled,
                      status, last_sync_at, background_sync_enabled, sync_interval_minutes,
                      next_sync_at, last_success_at, consecutive_failures, error,
                      created_at, updated_at
                 FROM provider_connections ORDER BY created_at, rowid"""
        ).fetchall()
        return [ProviderConnection(
            id=row["id"], provider=row["provider"], repository=row["repository"],
            sync_direction=row["sync_direction"], auto_close=bool(row["auto_close"]),
            enabled=bool(row["enabled"]), status=row["status"],
            last_sync_at=row["last_sync_at"],
            background_sync_enabled=bool(row["background_sync_enabled"]),
            sync_interval_minutes=row["sync_interval_minutes"],
            next_sync_at=row["next_sync_at"], last_success_at=row["last_success_at"],
            consecutive_failures=row["consecutive_failures"], error=row["error"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        ) for row in rows]

    def save_provider_connection(self, connection: ProviderConnection) -> None:
        now = datetime.now(timezone.utc).isoformat()
        connection.created_at = connection.created_at or now
        connection.updated_at = now
        with self.connection:
            self.connection.execute(
                """INSERT INTO provider_connections
                   (id, provider, repository, sync_direction, auto_close, enabled,
                    status, last_sync_at, background_sync_enabled, sync_interval_minutes,
                    next_sync_at, last_success_at, consecutive_failures, error,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    provider=excluded.provider, repository=excluded.repository,
                    sync_direction=excluded.sync_direction, auto_close=excluded.auto_close,
                    enabled=excluded.enabled, status=excluded.status,
                    last_sync_at=excluded.last_sync_at,
                    background_sync_enabled=excluded.background_sync_enabled,
                    sync_interval_minutes=excluded.sync_interval_minutes,
                    next_sync_at=excluded.next_sync_at,
                    last_success_at=excluded.last_success_at,
                    consecutive_failures=excluded.consecutive_failures,
                    error=excluded.error,
                    updated_at=excluded.updated_at""",
                (connection.id, connection.provider, connection.repository,
                 connection.sync_direction, int(connection.auto_close), int(connection.enabled),
                 connection.status, connection.last_sync_at,
                 int(connection.background_sync_enabled), connection.sync_interval_minutes,
                 connection.next_sync_at, connection.last_success_at,
                 connection.consecutive_failures, connection.error,
                 connection.created_at, connection.updated_at),
            )

    def load_sync_events(self, limit: int = 100) -> list[SyncEvent]:
        rows = self.connection.execute(
            """SELECT id, connection_id, direction, action, status, message, task_id,
                      external_id, attempt_count, next_retry_at, created_at, completed_at
                 FROM sync_events ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [SyncEvent(
            id=row["id"], connection_id=row["connection_id"],
            direction=row["direction"], action=row["action"], status=row["status"],
            message=row["message"], task_id=row["task_id"], external_id=row["external_id"],
            attempt_count=row["attempt_count"], next_retry_at=row["next_retry_at"],
            created_at=row["created_at"], completed_at=row["completed_at"],
        ) for row in rows]

    def save_sync_event(self, event: SyncEvent) -> None:
        event.created_at = event.created_at or datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                """INSERT INTO sync_events
                   (id, connection_id, direction, action, status, message, task_id,
                    external_id, attempt_count, next_retry_at, created_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                    message=excluded.message, attempt_count=excluded.attempt_count,
                    next_retry_at=excluded.next_retry_at, completed_at=excluded.completed_at""",
                (event.id, event.connection_id, event.direction, event.action, event.status,
                 event.message, event.task_id, event.external_id, event.attempt_count,
                 event.next_retry_at, event.created_at, event.completed_at),
            )

    def load_external_issue_links(self) -> list[ExternalIssueLink]:
        rows = self.connection.execute(
            """SELECT id, task_id, connection_id, external_id, issue_number, url,
                      external_state, external_updated_at, last_synced_at
                 FROM external_issue_links ORDER BY last_synced_at, rowid"""
        ).fetchall()
        return [ExternalIssueLink(
            id=row["id"], task_id=row["task_id"], connection_id=row["connection_id"],
            external_id=row["external_id"], issue_number=row["issue_number"], url=row["url"],
            external_state=row["external_state"],
            external_updated_at=row["external_updated_at"],
            last_synced_at=row["last_synced_at"],
        ) for row in rows]

    def save_external_issue_link(self, link: ExternalIssueLink) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO external_issue_links
                   (id, task_id, connection_id, external_id, issue_number, url,
                    external_state, external_updated_at, last_synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(connection_id, external_id) DO UPDATE SET
                    task_id=excluded.task_id, issue_number=excluded.issue_number,
                    url=excluded.url, external_state=excluded.external_state,
                    external_updated_at=excluded.external_updated_at,
                    last_synced_at=excluded.last_synced_at""",
                (link.id, link.task_id, link.connection_id, link.external_id,
                 link.issue_number, link.url, link.external_state,
                 link.external_updated_at, link.last_synced_at),
            )


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
    VALIDATION_EXECUTABLES = {
        "cargo", "eslint", "git", "mypy", "node", "npm", "pytest", "python", "python3",
        "ruff", "tsc", "vitest",
    }
    MAX_COMMAND_OUTPUT_BYTES = 64 * 1024
    CONFIDENCE_THRESHOLD = 0.7
    AGGREGATION_WINDOW_SECONDS = 30
    SESSION_STATUSES = {"active", "waiting_for_human", "error_stuck", "idle", "completed"}
    MAX_WATCHED_FILES = 2_000
    WATCH_EXCLUDED_DIRECTORIES = {
        ".git", ".taskloom", ".venv", "__pycache__", "dist", "node_modules", "target",
    }

    def __init__(
        self,
        workspace: Path,
        llm: LLMClient | None = None,
        providers: dict[str, IssueProvider] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.snapshots = SnapshotStore(self.workspace)
        self.state = StateStore(self.workspace / ".taskloom" / "taskloom.db")
        self.llm = llm or LLMClient()
        self.tasks = {task.id: task for task in self.state.load_tasks()}
        self.sessions = {session.id: session for session in self.state.load_sessions()}
        self.worklogs = {worklog.id: worklog for worklog in self.state.load_worklogs()}
        self.traces = {trace.id: trace for trace in self.state.load_traces()}
        self.pending = {change.request_id: change for change in self.state.load_pending()}
        self.agents = {agent.id: agent for agent in self.state.load_agents()}
        self.workflows = {workflow.id: workflow for workflow in self.state.load_workflows()}
        self.workflow_runs = {run.id: run for run in self.state.load_workflow_runs()}
        self.step_runs = {step.id: step for step in self.state.load_step_runs()}
        self.plan_approvals = {
            approval.request_id: approval for approval in self.state.load_plan_approvals()
        }
        self.triggers = {trigger.id: trigger for trigger in self.state.load_triggers()}
        self.file_triggers = {
            trigger.id: trigger for trigger in self.state.load_file_triggers()
        }
        self.events = self.state.load_events()
        self.provider_connections = {
            connection.id: connection for connection in self.state.load_provider_connections()
        }
        self.sync_events = self.state.load_sync_events()
        self.external_issue_links = {
            (link.connection_id, link.external_id): link
            for link in self.state.load_external_issue_links()
        }
        self.providers: dict[str, IssueProvider] = providers or {"github": GitHubCLIAdapter()}
        self._run_lock = asyncio.Lock()
        self._provider_syncing: set[str] = set()
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

    def update_task(
        self, task_id: str, status: str, *, expected_version: int | None = None,
        **changes: Any,
    ) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise ProtocolError("task_not_found", f"Task '{task_id}' does not exist")
        if expected_version is not None and expected_version != task.version:
            raise ProtocolError(
                "version_conflict",
                f"Task '{task_id}' changed (expected version {expected_version}, found {task.version})",
            )
        allowed = {
            "draft", "backlog", "active", "blocked", "needs_approval", "completed",
            "failed", "cancelled",
        }
        if status not in allowed:
            raise ProtocolError("invalid_status", f"Unsupported task status: {status}")
        task.status = status
        for key, value in changes.items():
            if hasattr(task, key):
                setattr(task, key, value)
        task.version += 1
        self.state.save_task(task)
        return task

    def serialize_task(self, task: Task) -> dict[str, Any]:
        return {
            "id": task.id, "title": task.title, "prompt": task.prompt, "status": task.status,
            "filePath": task.file_path, "provider": task.provider, "error": task.error,
            "source": task.source, "governanceState": task.governance_state,
            "confidenceScore": task.confidence_score, "agentId": task.agent_id,
            "sessionId": task.session_id, "branchName": task.branch_name,
            "parentTaskId": task.parent_task_id, "clusterKey": task.cluster_key,
            "progressCurrent": task.progress_current, "progressTotal": task.progress_total,
            "version": task.version, "createdAt": task.created_at, "updatedAt": task.updated_at,
            "links": self.state.load_task_links(task.id),
            "worklogs": [
                self.serialize_worklog(worklog) for worklog in self.worklogs.values()
                if worklog.task_id == task.id
            ],
        }

    @staticmethod
    def serialize_session(session: AgentSession) -> dict[str, Any]:
        return {
            "id": session.id, "agentId": session.agent_id, "status": session.status,
            "branchName": session.branch_name,
            "controlCapabilities": list(session.control_capabilities),
            "startedAt": session.started_at, "lastHeartbeatAt": session.last_heartbeat_at,
            "completedAt": session.completed_at, "error": session.error,
        }

    def serialize_worklog(self, worklog: TaskWorklog) -> dict[str, Any]:
        trace = self.traces.get(worklog.trace_id or "")
        return {
            "id": worklog.id, "taskId": worklog.task_id, "message": worklog.message,
            "kind": worklog.kind, "agentId": worklog.agent_id,
            "sessionId": worklog.session_id, "progressCurrent": worklog.progress_current,
            "progressTotal": worklog.progress_total, "traceId": worklog.trace_id,
            "createdAt": worklog.created_at,
            "trace": self.serialize_trace(trace) if trace else None,
        }

    @staticmethod
    def serialize_trace(trace: ExecutionTrace) -> dict[str, Any]:
        return {
            "id": trace.id, "taskId": trace.task_id, "worklogId": trace.worklog_id,
            "commandExecuted": trace.command_executed, "stdout": trace.stdout_preview,
            "stderr": trace.stderr_preview, "exitCode": trace.exit_code,
            "truncated": trace.truncated, "startedAt": trace.started_at,
            "completedAt": trace.completed_at, "contentSha256": trace.content_sha256,
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
            "archived": workflow.archived,
            "steps": [{
                "id": step.id, "name": step.name, "agentId": step.agent_id, "kind": step.kind,
                "instruction": step.instruction, "dependsOn": list(step.depends_on),
                "command": list(step.command), "timeoutSeconds": step.timeout_seconds,
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
            "events": [{
                "id": event.id, "workflowRunId": event.workflow_run_id,
                "stepRunId": event.step_run_id, "type": event.event_type,
                "message": event.message, "createdAt": event.created_at,
            } for event in self.events if event.workflow_run_id == run.id],
        }

    @staticmethod
    def serialize_trigger(trigger: AutomationTrigger) -> dict[str, Any]:
        return {
            "id": trigger.id, "workflowId": trigger.workflow_id, "name": trigger.name,
            "intervalMinutes": trigger.interval_minutes, "goal": trigger.goal,
            "targetFile": trigger.target_file, "enabled": trigger.enabled,
            "nextRunAt": trigger.next_run_at, "lastRunAt": trigger.last_run_at,
            "lastRunId": trigger.last_run_id, "error": trigger.error,
        }

    @staticmethod
    def serialize_file_trigger(trigger: FileTrigger) -> dict[str, Any]:
        return {
            "id": trigger.id, "workflowId": trigger.workflow_id, "name": trigger.name,
            "watchPath": trigger.watch_path, "pattern": trigger.pattern,
            "cooldownSeconds": trigger.cooldown_seconds, "goal": trigger.goal,
            "enabled": trigger.enabled, "lastRunAt": trigger.last_run_at,
            "lastRunId": trigger.last_run_id, "error": trigger.error,
            "trackedFiles": len(trigger.baseline or {}),
        }

    @staticmethod
    def serialize_provider_connection(connection: ProviderConnection) -> dict[str, Any]:
        return {
            "id": connection.id, "provider": connection.provider,
            "repository": connection.repository, "syncDirection": connection.sync_direction,
            "autoClose": connection.auto_close, "enabled": connection.enabled,
            "status": connection.status, "lastSyncAt": connection.last_sync_at,
            "backgroundSyncEnabled": connection.background_sync_enabled,
            "syncIntervalMinutes": connection.sync_interval_minutes,
            "nextSyncAt": connection.next_sync_at,
            "lastSuccessAt": connection.last_success_at,
            "consecutiveFailures": connection.consecutive_failures,
            "error": connection.error, "createdAt": connection.created_at,
            "updatedAt": connection.updated_at,
        }

    @staticmethod
    def serialize_sync_event(event: SyncEvent) -> dict[str, Any]:
        return {
            "id": event.id, "connectionId": event.connection_id,
            "direction": event.direction, "action": event.action, "status": event.status,
            "message": event.message, "taskId": event.task_id,
            "externalId": event.external_id, "attemptCount": event.attempt_count,
            "nextRetryAt": event.next_retry_at, "createdAt": event.created_at,
            "completedAt": event.completed_at,
        }

    @staticmethod
    def serialize_external_issue_link(link: ExternalIssueLink) -> dict[str, Any]:
        return {
            "id": link.id, "taskId": link.task_id,
            "connectionId": link.connection_id, "externalId": link.external_id,
            "issueNumber": link.issue_number, "url": link.url,
            "externalState": link.external_state,
            "externalUpdatedAt": link.external_updated_at,
            "lastSyncedAt": link.last_synced_at,
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
            "sessions": [self.serialize_session(session) for session in self.sessions.values()],
            "approvals": [self.serialize_change(change) for change in self.pending.values()],
            "agents": [self.serialize_agent(agent) for agent in self.agents.values()],
            "workflows": [
                self.serialize_workflow(workflow) for workflow in self.workflows.values()
                if not workflow.archived
            ],
            "workflowRuns": [
                self.serialize_workflow_run(run) for run in reversed(tuple(self.workflow_runs.values()))
            ],
            "planApprovals": [
                self.serialize_plan_approval(approval) for approval in self.plan_approvals.values()
            ],
            "triggers": [self.serialize_trigger(trigger) for trigger in self.triggers.values()],
            "fileTriggers": [
                self.serialize_file_trigger(trigger) for trigger in self.file_triggers.values()
            ],
            "providerConnections": [
                self.serialize_provider_connection(connection)
                for connection in self.provider_connections.values()
            ],
            "syncEvents": [self.serialize_sync_event(event) for event in self.sync_events],
            "externalIssueLinks": [
                self.serialize_external_issue_link(link)
                for link in self.external_issue_links.values()
            ],
        }

    @staticmethod
    def _health_check(
        check_id: str, label: str, status: str, detail: str, required: bool,
    ) -> dict[str, Any]:
        return {
            "id": check_id, "label": label, "status": status,
            "detail": detail, "required": required,
        }

    @staticmethod
    def _ollama_tags_url(generate_url: str) -> str:
        parsed = urllib.parse.urlsplit(generate_url)
        path = parsed.path
        if path.endswith("/api/generate"):
            path = f"{path[:-len('/api/generate')]}/api/tags"
        else:
            path = "/api/tags"
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    def _check_ollama(self) -> list[str]:
        url = os.environ.get("TASKLOOM_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
        request = urllib.request.Request(self._ollama_tags_url(url), method="GET")
        with urllib.request.urlopen(request, timeout=3) as response:
            data = json.loads(response.read())
        return [str(item.get("name", "")) for item in data.get("models", [])]

    async def _check_github(self) -> tuple[str, str]:
        if shutil.which("gh") is None:
            return "warning", "GitHub CLI is not installed; issue synchronization is optional."
        try:
            process = await asyncio.create_subprocess_exec(
                "gh", "auth", "status", "--hostname", "github.com",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.communicate(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return "warning", "GitHub CLI authentication check timed out."
        except OSError as cause:
            return "warning", f"GitHub CLI could not start: {cause}"
        if process.returncode == 0:
            return "ready", "GitHub CLI is authenticated."
        return "warning", "Run `gh auth login` to enable GitHub Issues synchronization."

    async def health_report(self) -> dict[str, Any]:
        """Return readiness diagnostics without exposing credentials or file contents."""
        checks: list[dict[str, Any]] = []
        workspace_ready = self.workspace.is_dir() and os.access(self.workspace, os.R_OK | os.W_OK)
        checks.append(self._health_check(
            "workspace", "Workspace", "ready" if workspace_ready else "error",
            f"Readable and writable: {self.workspace}" if workspace_ready
            else f"Taskloom cannot read and write: {self.workspace}", True,
        ))
        checks.append(self._health_check(
            "python", "Python engine", "ready",
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", True,
        ))

        provider = os.environ.get("TASKLOOM_DEFAULT_PROVIDER", "ollama")
        ollama_required = provider == "ollama"
        ollama_model = os.environ.get("TASKLOOM_OLLAMA_MODEL", "llama3.2")
        try:
            models = await asyncio.to_thread(self._check_ollama)
            checks.append(self._health_check(
                "ollama", "Ollama service", "ready", "Local Ollama service is reachable.",
                ollama_required,
            ))
            model_found = any(name == ollama_model or name.startswith(f"{ollama_model}:") for name in models)
            checks.append(self._health_check(
                "model", "Default model", "ready" if model_found else ("error" if ollama_required else "warning"),
                f"{ollama_model} is installed." if model_found
                else f"Install it with `ollama pull {ollama_model}`.", ollama_required,
            ))
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            detail = "Ollama is not reachable. Start Ollama before running local-agent tasks."
            checks.append(self._health_check(
                "ollama", "Ollama service", "error" if ollama_required else "warning",
                detail, ollama_required,
            ))
            checks.append(self._health_check(
                "model", "Default model", "error" if ollama_required else "warning",
                f"Could not verify {ollama_model}: {type(exc).__name__}.", ollama_required,
            ))

        if provider == "openai":
            configured = bool(os.environ.get("OPENAI_API_KEY"))
            checks.append(self._health_check(
                "openai", "OpenAI access", "ready" if configured else "error",
                "OPENAI_API_KEY is configured." if configured
                else "Set OPENAI_API_KEY before starting Taskloom.", True,
            ))
        github_status, github_detail = await self._check_github()
        checks.append(self._health_check(
            "github", "GitHub CLI", github_status, github_detail, False,
        ))
        return {
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "ready": all(item["status"] != "error" for item in checks if item["required"]),
            "workspace": str(self.workspace),
            "checks": checks,
        }

    def _record_event(
        self, run_id: str, event_type: str, message: str, step_run_id: str | None = None,
    ) -> ExecutionEvent:
        event = ExecutionEvent(
            id=str(uuid.uuid4()), workflow_run_id=run_id, step_run_id=step_run_id,
            event_type=event_type, message=message,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.events.append(event)
        self.state.save_event(event)
        return event

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
            self._validate_command(step)
            unknown = set(step.depends_on) - seen
            if unknown:
                raise ProtocolError(
                    "invalid_dependency",
                    f"Step '{step.name}' depends on unavailable steps: {', '.join(sorted(unknown))}",
                )
            seen.add(step.id)

    def _validate_command(self, step: WorkflowStep) -> None:
        if not step.command:
            if step.timeout_seconds < 1 or step.timeout_seconds > 900:
                raise ProtocolError("invalid_timeout", "Validation timeout must be 1–900 seconds")
            return
        if step.kind != "validate":
            raise ProtocolError("invalid_command_step", "Only validation steps may run commands")
        if len(step.command) > 32 or any(len(argument) > 512 for argument in step.command):
            raise ProtocolError("invalid_command", "Validation command is too large")
        executable = step.command[0]
        if executable != Path(executable).name or executable not in self.VALIDATION_EXECUTABLES:
            raise ProtocolError(
                "unsafe_executable",
                f"Validation executable '{executable}' is not in Taskloom's allowlist",
            )
        for argument in step.command[1:]:
            argument_path = Path(argument)
            if argument_path.is_absolute() or ".." in argument_path.parts or "\x00" in argument:
                raise ProtocolError("unsafe_argument", "Validation arguments must remain workspace-relative")
        if step.timeout_seconds < 1 or step.timeout_seconds > 900:
            raise ProtocolError("invalid_timeout", "Validation timeout must be 1–900 seconds")

    async def _run_validation_command(self, step: WorkflowStep) -> CommandResult:
        environment = {
            key: value for key, value in os.environ.items()
            if key in {
                "PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "COMSPEC", "PATHEXT",
                "RUSTUP_HOME", "CARGO_HOME",
            }
        }
        environment.update({"NO_COLOR": "1", "TASKLOOM_VALIDATION": "1"})
        with tempfile.TemporaryFile() as output_file:
            process = await asyncio.create_subprocess_exec(
                *step.command, cwd=self.workspace, env=environment,
                stdout=output_file, stderr=asyncio.subprocess.STDOUT,
            )
            timed_out = False
            try:
                await asyncio.wait_for(process.wait(), timeout=step.timeout_seconds)
            except asyncio.TimeoutError:
                timed_out = True
                process.kill()
                await process.wait()
            output_file.seek(0)
            raw = output_file.read(self.MAX_COMMAND_OUTPUT_BYTES + 1)
        truncated = len(raw) > self.MAX_COMMAND_OUTPUT_BYTES
        output = raw[:self.MAX_COMMAND_OUTPUT_BYTES].decode("utf-8", errors="replace")
        if truncated:
            output += "\n[Taskloom truncated validation output at 64 KiB]\n"
        return CommandResult(
            return_code=None if timed_out else process.returncode,
            output=output or "(command produced no output)", timed_out=timed_out,
        )

    def _create_workflow_run(
        self, workflow: Workflow, goal: str, target_file: str,
    ) -> tuple[WorkflowRun, PlanApproval | None]:
        if workflow.archived or not workflow.enabled:
            raise ProtocolError("workflow_not_found", "Workflow is missing or disabled")
        self.snapshots.resolve_path(target_file)
        run = WorkflowRun(
            id=str(uuid.uuid4()), workflow_id=workflow.id, goal=goal, target_file=target_file,
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
        self._record_event(run.id, "run_created", f"Created workflow run for {workflow.name}")
        if workflow.approval_mode != "approve_plan":
            return run, None
        approval = PlanApproval(
            request_id=str(uuid.uuid4()), workflow_run_id=run.id,
            workflow_id=workflow.id,
            summary=f"Approve {len(workflow.steps)} steps before Taskloom begins",
        )
        self.plan_approvals[approval.request_id] = approval
        self.state.save_plan_approval(approval)
        run.status = "needs_approval"
        self.state.save_workflow_run(run)
        self._record_event(run.id, "approval_required", "Workflow plan requires approval")
        return run, approval

    @staticmethod
    def _matches_watch_pattern(relative_path: str, pattern: str) -> bool:
        normalized = relative_path.replace(os.sep, "/")
        return fnmatch.fnmatchcase(normalized, pattern) or (
            pattern.startswith("**/") and fnmatch.fnmatchcase(normalized, pattern[3:])
        )

    def _scan_file_trigger(self, trigger: FileTrigger) -> dict[str, tuple[int, int]]:
        """Return lightweight metadata only; never read watched file contents."""
        root = self.snapshots.resolve_path(trigger.watch_path)
        candidates: list[Path] = []
        if root.is_file() and not root.is_symlink():
            candidates = [root]
        elif root.is_dir():
            for directory, names, files in os.walk(root, followlinks=False):
                names[:] = sorted(
                    name for name in names
                    if name not in self.WATCH_EXCLUDED_DIRECTORIES
                    and not (Path(directory) / name).is_symlink()
                )
                for name in sorted(files):
                    candidate = Path(directory) / name
                    if candidate.is_symlink():
                        continue
                    relative_to_watch = candidate.relative_to(root).as_posix()
                    if self._matches_watch_pattern(relative_to_watch, trigger.pattern):
                        candidates.append(candidate)
                        if len(candidates) > self.MAX_WATCHED_FILES:
                            raise ProtocolError(
                                "watch_too_large",
                                f"File watch exceeds the {self.MAX_WATCHED_FILES:,}-file safety limit",
                            )
        snapshot: dict[str, tuple[int, int]] = {}
        for candidate in candidates:
            stat = candidate.stat()
            relative = candidate.relative_to(self.workspace).as_posix()
            snapshot[relative] = (stat.st_mtime_ns, stat.st_size)
        return snapshot

    def _refresh_file_trigger_after_run(self, run: WorkflowRun) -> None:
        """Accept the run's own write as the new baseline to prevent feedback loops."""
        for trigger in self.file_triggers.values():
            if trigger.last_run_id != run.id:
                continue
            try:
                current = self._scan_file_trigger(trigger)
                baseline = dict(trigger.baseline or {})
                if run.target_file in current:
                    baseline[run.target_file] = current[run.target_file]
                else:
                    baseline.pop(run.target_file, None)
                trigger.baseline = baseline
                trigger.error = None
            except Exception as exc:
                trigger.error = str(exc)
            self.state.save_file_trigger(trigger)

    async def run_due_triggers(self, now: datetime | None = None) -> list[WorkflowRun]:
        """Run each due interval trigger at most once and advance its next deadline."""
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        started: list[WorkflowRun] = []
        for trigger in tuple(self.triggers.values()):
            if not trigger.enabled or not trigger.next_run_at:
                continue
            try:
                due_at = datetime.fromisoformat(trigger.next_run_at).astimezone(timezone.utc)
            except ValueError:
                due_at = current
            if due_at > current:
                continue
            trigger.next_run_at = (
                current + timedelta(minutes=trigger.interval_minutes)
            ).isoformat()
            trigger.last_run_at = current.isoformat()
            trigger.error = None
            try:
                workflow = self.workflows.get(trigger.workflow_id)
                if workflow is None:
                    raise ProtocolError("workflow_not_found", "Scheduled workflow no longer exists")
                run, _ = self._create_workflow_run(
                    workflow, trigger.goal, trigger.target_file,
                )
                trigger.last_run_id = run.id
                self.state.save_trigger(trigger)
                self._record_event(run.id, "triggered", f"Started by schedule: {trigger.name}")
                if run.status != "needs_approval":
                    await self._execute_workflow(run)
                started.append(run)
            except Exception as exc:
                trigger.error = str(exc)
                self.state.save_trigger(trigger)
        return started

    async def run_due_file_triggers(self, now: datetime | None = None) -> list[WorkflowRun]:
        """Run at most one changed file per watch while respecting its cooldown."""
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        started: list[WorkflowRun] = []
        for trigger in tuple(self.file_triggers.values()):
            if not trigger.enabled:
                continue
            if trigger.last_run_at:
                try:
                    last_run = datetime.fromisoformat(trigger.last_run_at).astimezone(timezone.utc)
                except ValueError:
                    last_run = current_time - timedelta(seconds=trigger.cooldown_seconds)
                if current_time < last_run + timedelta(seconds=trigger.cooldown_seconds):
                    continue
            try:
                current = self._scan_file_trigger(trigger)
                baseline = trigger.baseline or {}
                changed = sorted(
                    path for path, fingerprint in current.items()
                    if baseline.get(path) != fingerprint
                )
                if not changed:
                    if set(baseline) != set(current):
                        trigger.baseline = current
                        self.state.save_file_trigger(trigger)
                    continue
                changed_path = changed[0]
                updated_baseline = dict(baseline)
                updated_baseline[changed_path] = current[changed_path]
                for deleted in set(updated_baseline) - set(current):
                    updated_baseline.pop(deleted, None)
                trigger.baseline = updated_baseline
                trigger.last_run_at = current_time.isoformat()
                trigger.error = None
                workflow = self.workflows.get(trigger.workflow_id)
                if workflow is None:
                    raise ProtocolError("workflow_not_found", "Watched workflow no longer exists")
                goal = trigger.goal.replace("{file}", changed_path)
                run, _ = self._create_workflow_run(workflow, goal, changed_path)
                trigger.last_run_id = run.id
                self.state.save_file_trigger(trigger)
                self._record_event(
                    run.id, "triggered", f"Started by file watch: {trigger.name} ({changed_path})",
                )
                if run.status != "needs_approval":
                    await self._execute_workflow(run)
                started.append(run)
            except Exception as exc:
                trigger.error = str(exc)
                self.state.save_file_trigger(trigger)
        return started

    async def run_scheduler(self, emit: Any) -> None:
        """Keep interval and filesystem triggers active while the engine is running."""
        poll_seconds = max(5, int(os.environ.get("TASKLOOM_SCHEDULER_POLL_SECONDS", "15")))
        while True:
            scheduled, watched, provider_retries, provider_syncs = await asyncio.gather(
                self.run_due_triggers(), self.run_due_file_triggers(),
                self.run_due_provider_retries(),
                self.run_due_provider_syncs(),
            )
            started = [*scheduled, *watched]
            if started or provider_retries or provider_syncs:
                await emit({"type": "state_snapshot", "payload": self.state_payload()})
            await asyncio.sleep(poll_seconds)

    async def _execute_workflow(self, run: WorkflowRun) -> None:
        async with self._run_lock:
            if run.status == "cancelled":
                return
            workflow = self.workflows[run.workflow_id]
            run.status = "running"
            run.error = None
            run.completed_at = None
            run.started_at = run.started_at or datetime.now(timezone.utc).isoformat()
            self.state.save_workflow_run(run)
            self._record_event(run.id, "run_started", "Workflow execution started")
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
                self._record_event(run.id, "step_started", f"Started {definition.name}", step.id)
                should_continue = await self._execute_step(workflow, run, definition, step)
                if not should_continue:
                    return
                completed.add(definition.id)
            run.status = "completed"
            run.current_step = None
            run.completed_at = datetime.now(timezone.utc).isoformat()
            self.state.save_workflow_run(run)
            self._record_event(run.id, "run_completed", "Workflow completed successfully")
            self._refresh_file_trigger_after_run(run)

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
                if definition.command:
                    result = await self._run_validation_command(definition)
                    step.output = result.output
                    if result.timed_out:
                        raise ProtocolError(
                            "validation_timeout",
                            f"Validation command exceeded {definition.timeout_seconds} seconds",
                        )
                    if result.return_code != 0:
                        raise ProtocolError(
                            "validation_command_failed",
                            f"Validation command exited with code {result.return_code}",
                        )
                else:
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
                    self._record_event(
                        run.id, "approval_required",
                        f"{definition.name} is waiting for file approval", step.id,
                    )
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
            self._record_event(run.id, "step_completed", f"Completed {definition.name}", step.id)
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
            self._record_event(run.id, "step_failed", run.error, step.id)
            return False

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError) as exc:
            raise ProtocolError("invalid_confidence", "confidenceScore must be a number") from exc
        if not 0.0 <= score <= 1.0:
            raise ProtocolError("invalid_confidence", "confidenceScore must be between 0.0 and 1.0")
        return score

    @staticmethod
    def _cluster_key(payload: dict[str, Any]) -> str:
        explicit = str(payload.get("correlationKey") or "").strip()
        if explicit:
            return explicit
        title = " ".join(str(payload.get("title") or "untitled").lower().split())
        target = str(payload.get("filePath") or "")
        directory = str(Path(target).parent) if target else "."
        return "|".join((
            str(payload.get("agentId") or "unknown"),
            str(payload.get("sessionId") or "unknown"),
            str(payload.get("branchName") or ""), directory, title,
        ))

    def _touch_session(self, payload: dict[str, Any]) -> AgentSession:
        self._require(payload, "agentId", "sessionId")
        session_id = str(payload["sessionId"])
        now = datetime.now(timezone.utc).isoformat()
        session = self.sessions.get(session_id) or AgentSession(
            id=session_id, agent_id=str(payload["agentId"]), started_at=now,
        )
        status = str(payload.get("agentStatus") or session.status or "active").lower()
        if status not in self.SESSION_STATUSES:
            raise ProtocolError("invalid_agent_status", f"Unsupported agent status: {status}")
        if session.agent_id != str(payload["agentId"]):
            raise ProtocolError("session_agent_mismatch", "sessionId is already owned by another agent")
        session.status = status
        session.branch_name = str(payload["branchName"]) if payload.get("branchName") else session.branch_name
        session.last_heartbeat_at = now
        session.error = str(payload["agentError"]) if payload.get("agentError") else None
        capabilities = payload.get("controlCapabilities")
        if capabilities is not None:
            if not isinstance(capabilities, list):
                raise ProtocolError("invalid_capabilities", "controlCapabilities must be a list")
            session.control_capabilities = tuple(str(item) for item in capabilities)
        self.sessions[session.id] = session
        self.state.save_session(session)
        return session

    def control_agent_session(self, session_id: str, action: str) -> AgentSession:
        session = self.sessions.get(session_id)
        if session is None:
            # Refresh for a second engine process sharing the same WAL database.
            self.sessions = {item.id: item for item in self.state.load_sessions()}
            session = self.sessions.get(session_id)
        if session is None:
            raise ProtocolError("session_not_found", f"Session '{session_id}' does not exist")
        if action not in {"pause", "resume", "kill"}:
            raise ProtocolError("invalid_control_action", f"Unsupported control action: {action}")
        if action not in session.control_capabilities:
            raise ProtocolError(
                "control_not_supported", f"Session '{session_id}' did not advertise {action} support",
            )
        # Repeated requests are safe, but terminal sessions cannot be restarted and
        # resume is meaningful only after a cooperative pause has been observed.
        target_status = {"pause": "idle", "resume": "active", "kill": "completed"}[action]
        if session.status == target_status:
            return session
        if session.status == "completed":
            raise ProtocolError(
                "invalid_control_transition", f"Completed session '{session_id}' cannot {action}",
            )
        if action == "resume" and session.status != "idle":
            raise ProtocolError(
                "invalid_control_transition", f"Session '{session_id}' must be idle before resume",
            )
        session.status = target_status
        session.last_heartbeat_at = datetime.now(timezone.utc).isoformat()
        session.completed_at = session.last_heartbeat_at if action == "kill" else None
        session.error = "Stopped by user" if action == "kill" else None
        self.state.save_session(session)
        return session

    def _duplicate_ingestion(self, payload: dict[str, Any]) -> IngestionEvent | None:
        self._require(payload, "idempotencyKey")
        return self.state.load_ingestion_event(str(payload["idempotencyKey"]))

    def _save_ingestion(
        self, payload: dict[str, Any], operation: str, disposition: str, task_id: str,
        cluster_id: str | None = None,
    ) -> None:
        self.state.save_ingestion_event(IngestionEvent(
            id=str(uuid.uuid4()), idempotency_key=str(payload["idempotencyKey"]),
            source=str(payload.get("source") or "mcp"), operation=operation,
            disposition=disposition,
            raw_payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            agent_id=str(payload.get("agentId") or "") or None,
            session_id=str(payload.get("sessionId") or "") or None,
            task_id=task_id, cluster_id=cluster_id,
        ))

    def _duplicate_response(self, event: IngestionEvent) -> dict[str, Any]:
        task = self.tasks.get(event.task_id or "")
        if task is None:
            raise ProtocolError("duplicate_target_missing", "The original idempotent result is unavailable")
        return {"task": self.serialize_task(task), "disposition": "duplicate",
                "originalDisposition": event.disposition}

    def _add_links(self, task_id: str, payload: dict[str, Any]) -> None:
        if payload.get("gitSha"):
            self.state.save_task_link(
                task_id, "commit", git_sha=str(payload["gitSha"]), provider="git",
                label=str(payload["gitSha"])[:8],
            )
        if payload.get("prUrl"):
            self.state.save_task_link(
                task_id, "pull_request", url=str(payload["prUrl"]), provider="github",
                label="Pull request",
            )

    def ingest_create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require(
            payload, "title", "prompt", "agentId", "sessionId", "confidenceScore",
            "idempotencyKey",
        )
        duplicate = self._duplicate_ingestion(payload)
        if duplicate:
            return self._duplicate_response(duplicate)
        requested_task_id = str(payload["taskId"]) if payload.get("taskId") else None
        if requested_task_id and requested_task_id in self.tasks:
            raise ProtocolError("duplicate_task_id", f"Task '{requested_task_id}' already exists")
        session = self._touch_session(payload)
        score = self._confidence(payload["confidenceScore"])
        file_path = str(payload["filePath"]) if payload.get("filePath") else None
        if file_path:
            self.snapshots.resolve_path(file_path)
        cluster_key = self._cluster_key(payload)
        now = datetime.now(timezone.utc)
        if score >= self.CONFIDENCE_THRESHOLD:
            for task in reversed(tuple(self.tasks.values())):
                if (task.cluster_key == cluster_key and task.governance_state == "accepted"
                        and task.status not in {"completed", "cancelled", "failed"}
                        and task.created_at):
                    created = datetime.fromisoformat(task.created_at)
                    if now - created <= timedelta(seconds=self.AGGREGATION_WINDOW_SECONDS):
                        task.progress_total = max(task.progress_total, 1) + 1
                        if str(payload.get("status") or "") == "completed":
                            task.progress_current += 1
                        task.version += 1
                        self.state.save_task(task)
                        worklog = TaskWorklog(
                            id=str(uuid.uuid4()), task_id=task.id,
                            message=str(payload.get("summary") or payload["prompt"]),
                            kind="clustered_update", agent_id=session.agent_id,
                            session_id=session.id, progress_current=task.progress_current,
                            progress_total=task.progress_total,
                        )
                        self.worklogs[worklog.id] = worklog
                        self.state.save_worklog(worklog)
                        self._add_links(task.id, payload)
                        self._save_ingestion(payload, "create_task", "clustered", task.id, task.id)
                        return {"task": self.serialize_task(task), "disposition": "clustered",
                                "governanceReason": "Aggregated into recent related work"}
        accepted = score >= self.CONFIDENCE_THRESHOLD
        task = Task(
            id=requested_task_id or str(uuid.uuid4()), title=str(payload["title"]),
            prompt=str(payload["prompt"]), status=(str(payload.get("status") or "backlog") if accepted else "draft"),
            file_path=file_path, provider=str(payload.get("provider") or "ollama"),
            source=str(payload.get("source") or "mcp"),
            governance_state="accepted" if accepted else "pending_review",
            confidence_score=score, agent_id=session.agent_id, session_id=session.id,
            branch_name=session.branch_name, parent_task_id=str(payload["parentTaskId"]) if payload.get("parentTaskId") else None,
            cluster_key=cluster_key, progress_current=1 if payload.get("status") == "completed" else 0,
            progress_total=1,
        )
        if task.status not in {
            "draft", "backlog", "active", "blocked", "needs_approval", "completed",
            "failed", "cancelled",
        }:
            raise ProtocolError("invalid_status", f"Unsupported task status: {task.status}")
        self.tasks[task.id] = task
        self.state.save_task(task)
        self._add_links(task.id, payload)
        disposition = "created" if accepted else "drafted"
        self._save_ingestion(payload, "create_task", disposition, task.id)
        reason = "Confidence threshold met" if accepted else "Confidence below 0.70; human review required"
        return {"task": self.serialize_task(task), "disposition": disposition,
                "governanceReason": reason}

    def ingest_update_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require(payload, "taskId", "agentId", "sessionId", "idempotencyKey")
        duplicate = self._duplicate_ingestion(payload)
        if duplicate:
            return self._duplicate_response(duplicate)
        self._touch_session(payload)
        task_id = str(payload["taskId"])
        task = self.tasks.get(task_id)
        if task is None:
            raise ProtocolError("task_not_found", f"Task '{task_id}' does not exist")
        progress_current = int(payload.get("progressCurrent", task.progress_current))
        progress_total = int(payload.get("progressTotal", task.progress_total))
        if progress_current < 0 or progress_total < 0 or progress_current > progress_total:
            raise ProtocolError("invalid_progress", "Progress must satisfy 0 <= current <= total")
        status = str(payload.get("status") or task.status)
        expected = int(payload["expectedVersion"]) if payload.get("expectedVersion") is not None else None
        task = self.update_task(
            task_id, status, expected_version=expected, progress_current=progress_current,
            progress_total=progress_total, error=str(payload["error"]) if payload.get("error") else None,
        )
        summary = str(payload.get("summary") or f"Task moved to {status}")
        worklog = TaskWorklog(
            id=str(uuid.uuid4()), task_id=task.id, message=summary, kind="state_change",
            agent_id=str(payload["agentId"]), session_id=str(payload["sessionId"]),
            progress_current=progress_current, progress_total=progress_total,
        )
        self.worklogs[worklog.id] = worklog
        self.state.save_worklog(worklog)
        self._add_links(task.id, payload)
        self._save_ingestion(payload, "update_task", "updated", task.id)
        return {"task": self.serialize_task(task), "disposition": "updated"}

    @staticmethod
    def _redact_trace(value: str) -> str:
        patterns = (
            r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
            r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s]+",
        )
        for pattern in patterns:
            value = re.sub(pattern, r"\1[REDACTED]", value)
        return value

    def _bounded_trace(self, value: Any) -> tuple[str, bool]:
        redacted = self._redact_trace(str(value or ""))
        encoded = redacted.encode("utf-8")
        if len(encoded) <= self.MAX_COMMAND_OUTPUT_BYTES:
            return redacted, False
        return encoded[:self.MAX_COMMAND_OUTPUT_BYTES].decode("utf-8", errors="ignore"), True

    def ingest_add_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require(payload, "taskId", "agentId", "sessionId", "message", "idempotencyKey")
        duplicate = self._duplicate_ingestion(payload)
        if duplicate:
            result = self._duplicate_response(duplicate)
            result["worklog"] = None
            return result
        self._touch_session(payload)
        task_id = str(payload["taskId"])
        task = self.tasks.get(task_id)
        if task is None:
            raise ProtocolError("task_not_found", f"Task '{task_id}' does not exist")
        proposed_current = (
            int(payload["progressCurrent"]) if payload.get("progressCurrent") is not None
            else task.progress_current
        )
        proposed_total = (
            int(payload["progressTotal"]) if payload.get("progressTotal") is not None
            else task.progress_total
        )
        if proposed_current < 0 or proposed_total < 0 or proposed_current > proposed_total:
            raise ProtocolError("invalid_progress", "Progress must satisfy 0 <= current <= total")
        worklog = TaskWorklog(
            id=str(uuid.uuid4()), task_id=task_id, message=str(payload["message"]),
            kind=str(payload.get("kind") or "progress"), agent_id=str(payload["agentId"]),
            session_id=str(payload["sessionId"]),
            progress_current=int(payload["progressCurrent"]) if payload.get("progressCurrent") is not None else None,
            progress_total=int(payload["progressTotal"]) if payload.get("progressTotal") is not None else None,
        )
        self.worklogs[worklog.id] = worklog
        self.state.save_worklog(worklog)
        trace = None
        if any(key in payload for key in ("commandExecuted", "stdout", "stderr", "exitCode")):
            command, command_truncated = self._bounded_trace(payload.get("commandExecuted"))
            stdout, stdout_truncated = self._bounded_trace(payload.get("stdout"))
            stderr, stderr_truncated = self._bounded_trace(payload.get("stderr"))
            digest = hashlib.sha256(
                f"{command}\0{stdout}\0{stderr}".encode("utf-8")
            ).hexdigest()
            trace = ExecutionTrace(
                id=str(uuid.uuid4()), task_id=task_id, worklog_id=worklog.id,
                command_executed=command,
                stdout_preview=stdout, stderr_preview=stderr,
                exit_code=int(payload["exitCode"]) if payload.get("exitCode") is not None else None,
                truncated=command_truncated or stdout_truncated or stderr_truncated,
                started_at=str(payload["startedAt"]) if payload.get("startedAt") else None,
                completed_at=str(payload["completedAt"]) if payload.get("completedAt") else None,
                content_sha256=digest,
            )
            self.traces[trace.id] = trace
            self.state.save_trace(trace)
            worklog.trace_id = trace.id
            self.state.save_worklog(worklog)
        if worklog.progress_current is not None or worklog.progress_total is not None:
            self.update_task(
                task_id, task.status, progress_current=proposed_current,
                progress_total=proposed_total,
            )
        self._save_ingestion(payload, "add_log", "logged", task_id)
        return {"task": self.serialize_task(task), "worklog": self.serialize_worklog(worklog),
                "disposition": "logged"}

    @staticmethod
    def _schedule_next_provider_sync(
        connection: ProviderConnection, now: datetime, *, failed: bool = False,
    ) -> None:
        if failed:
            connection.consecutive_failures += 1
            multiplier = 2 ** min(connection.consecutive_failures, 5)
            delay = min(connection.sync_interval_minutes * multiplier, 360)
        else:
            connection.consecutive_failures = 0
            connection.last_success_at = now.isoformat()
            delay = connection.sync_interval_minutes
        connection.next_sync_at = (now + timedelta(minutes=delay)).isoformat()

    async def sync_provider_inbound(
        self, connection: ProviderConnection, *, automatic: bool = False,
    ) -> dict[str, int]:
        if not connection.enabled:
            raise ProtocolError("connection_disabled", "Provider connection is disabled.")
        if connection.status != "connected" and not (automatic and connection.status == "error"):
            raise ProtocolError(
                "connection_not_ready", "Test the provider connection before importing issues.",
            )
        if connection.sync_direction == "outbound":
            raise ProtocolError(
                "sync_direction_blocked", "This connection is configured for outbound sync only.",
            )
        adapter = self.providers.get(connection.provider)
        if adapter is None:
            raise ProtocolError(
                "unsupported_provider", f"Unsupported issue provider: {connection.provider}",
            )
        event = SyncEvent(
            id=str(uuid.uuid4()), connection_id=connection.id, direction="inbound",
            action="import_issues", status="running",
            message=f"Importing open issues from {connection.repository}", attempt_count=1,
        )
        self.state.save_sync_event(event)
        try:
            issues = await adapter.list_open_issues(connection.repository)
        except ProviderError as exc:
            failed_at = datetime.now(timezone.utc)
            connection.status = "error"
            connection.error = str(exc)
            self._schedule_next_provider_sync(connection, failed_at, failed=True)
            event.status = "failed"
            event.message = str(exc)
            event.completed_at = failed_at.isoformat()
            self.state.save_provider_connection(connection)
            self.state.save_sync_event(event)
            self.sync_events = self.state.load_sync_events()
            raise ProtocolError(exc.code, str(exc)) from exc

        imported = 0
        updated = 0
        unchanged = 0
        completed = 0
        reopened = 0
        now = datetime.now(timezone.utc).isoformat()
        open_external_ids = {issue.external_id for issue in issues}
        for issue in issues:
            key = (connection.id, issue.external_id)
            link = self.external_issue_links.get(key)
            if link is None:
                task_id = str(uuid.uuid5(
                    uuid.NAMESPACE_URL, f"taskloom:{connection.id}:{issue.external_id}",
                ))
                task = Task(
                    id=task_id, title=issue.title,
                    prompt=issue.body or f"Imported from GitHub Issue #{issue.number}.",
                    status="backlog", source="provider", governance_state="accepted",
                )
                self.tasks[task.id] = task
                self.state.save_task(task)
                self.state.save_task_link(
                    task.id, "issue", url=issue.url, provider=connection.provider,
                    label=f"{connection.repository}#{issue.number}",
                )
                link = ExternalIssueLink(
                    id=str(uuid.uuid5(
                        uuid.NAMESPACE_URL, f"taskloom-link:{connection.id}:{issue.external_id}",
                    )),
                    task_id=task.id, connection_id=connection.id,
                    external_id=issue.external_id, issue_number=issue.number,
                    url=issue.url, external_state=issue.state,
                    external_updated_at=issue.updated_at, last_synced_at=now,
                )
                imported += 1
            else:
                task = self.tasks.get(link.task_id)
                changed = False
                if task is not None:
                    prompt = issue.body or f"Imported from GitHub Issue #{issue.number}."
                    if task.title != issue.title or task.prompt != prompt:
                        task.title = issue.title
                        task.prompt = prompt
                        task.version += 1
                        self.state.save_task(task)
                        changed = True
                    if link.external_state == "closed" and task.status == "completed":
                        task.status = "backlog"
                        task.version += 1
                        self.state.save_task(task)
                        reopened += 1
                updated += int(changed)
                unchanged += int(not changed)
                link.issue_number = issue.number
                link.url = issue.url
                link.external_state = issue.state
                link.external_updated_at = issue.updated_at
                link.last_synced_at = now
            self.external_issue_links[key] = link
            self.state.save_external_issue_link(link)

        # Open-issue listing is efficient but omits issues closed outside Taskloom.
        # Confirm missing linked issues individually before reconciling their cards.
        linked_to_connection = [
            link for link in self.external_issue_links.values()
            if link.connection_id == connection.id
            and link.external_id not in open_external_ids
            and link.external_state != "closed"
        ]
        try:
            for link in linked_to_connection:
                issue = await adapter.get_issue(connection.repository, link.issue_number)
                if issue.state != "closed":
                    continue
                task = self.tasks.get(link.task_id)
                if task is not None and task.status != "completed":
                    task.status = "completed"
                    task.version += 1
                    self.state.save_task(task)
                    completed += 1
                link.external_state = issue.state
                link.external_updated_at = issue.updated_at
                link.last_synced_at = now
                self.state.save_external_issue_link(link)
        except ProviderError as exc:
            failed_at = datetime.now(timezone.utc)
            connection.status = "error"
            connection.error = str(exc)
            self._schedule_next_provider_sync(connection, failed_at, failed=True)
            event.status = "failed"
            event.message = str(exc)
            event.completed_at = failed_at.isoformat()
            self.state.save_provider_connection(connection)
            self.state.save_sync_event(event)
            self.sync_events = self.state.load_sync_events()
            raise ProtocolError(exc.code, str(exc)) from exc

        connection.last_sync_at = now
        connection.status = "connected"
        connection.error = None
        self._schedule_next_provider_sync(connection, datetime.fromisoformat(now))
        self.state.save_provider_connection(connection)
        event.status = "completed"
        event.message = (
            f"Imported {imported}, updated {updated}, unchanged {unchanged}, "
            f"completed {completed}, reopened {reopened} "
            f"from {connection.repository}"
        )
        event.completed_at = now
        self.state.save_sync_event(event)
        self.sync_events = self.state.load_sync_events()
        return {
            "imported": imported, "updated": updated, "unchanged": unchanged,
            "completed": completed, "reopened": reopened,
        }

    async def run_due_provider_syncs(
        self, now: datetime | None = None,
    ) -> list[dict[str, int]]:
        """Reconcile due inbound connections without terminating the scheduler on failure."""
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        summaries: list[dict[str, int]] = []
        for connection in tuple(self.provider_connections.values()):
            if (
                not connection.enabled
                or not connection.background_sync_enabled
                or connection.sync_direction == "outbound"
                or connection.status not in {"connected", "error"}
                or connection.id in self._provider_syncing
            ):
                continue
            if connection.next_sync_at:
                try:
                    if datetime.fromisoformat(connection.next_sync_at).astimezone(timezone.utc) > current:
                        continue
                except ValueError:
                    connection.next_sync_at = current.isoformat()
            self._provider_syncing.add(connection.id)
            try:
                summaries.append(await self.sync_provider_inbound(connection, automatic=True))
            except ProtocolError:
                # The sync operation already persisted the health state and retry schedule.
                summaries.append({})
            finally:
                self._provider_syncing.discard(connection.id)
        return summaries

    async def _close_external_issue(
        self,
        task: Task,
        link: ExternalIssueLink,
        connection: ProviderConnection,
        *,
        event: SyncEvent | None = None,
        force: bool = False,
    ) -> SyncEvent:
        adapter = self.providers.get(connection.provider)
        if adapter is None:
            raise ProtocolError(
                "unsupported_provider", f"Unsupported issue provider: {connection.provider}",
            )
        event = event or SyncEvent(
            id=str(uuid.uuid4()), connection_id=connection.id, direction="outbound",
            action="close_issue", status="running",
            message=f"Closing {connection.repository}#{link.issue_number}",
            task_id=task.id, external_id=link.external_id,
        )
        event.status = "running"
        event.attempt_count += 1
        event.next_retry_at = None
        self.state.save_sync_event(event)
        try:
            remote = await adapter.get_issue(connection.repository, link.issue_number)
            if remote.state == "closed":
                closed = remote
                message = f"{connection.repository}#{link.issue_number} was already closed"
            elif (not force and link.external_updated_at and remote.updated_at
                  and remote.updated_at != link.external_updated_at):
                event.status = "conflict"
                event.message = (
                    f"{connection.repository}#{link.issue_number} changed on GitHub; "
                    "import the latest issue before closing it."
                )
                event.completed_at = datetime.now(timezone.utc).isoformat()
                self.state.save_sync_event(event)
                return event
            else:
                closed = await adapter.close_issue(connection.repository, link.issue_number)
                message = f"Closed {connection.repository}#{link.issue_number}"
            now = datetime.now(timezone.utc).isoformat()
            link.external_state = closed.state
            link.external_updated_at = closed.updated_at
            link.last_synced_at = now
            self.state.save_external_issue_link(link)
            connection.status = "connected"
            connection.error = None
            connection.last_sync_at = now
            self.state.save_provider_connection(connection)
            event.status = "completed"
            event.message = message
            event.completed_at = now
        except ProviderError as exc:
            event.message = str(exc)
            if exc.retryable and event.attempt_count < 5:
                delay_minutes = min(2 ** event.attempt_count, 60)
                event.status = "queued"
                event.next_retry_at = (
                    datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                ).isoformat()
            else:
                event.status = "failed"
                event.completed_at = datetime.now(timezone.utc).isoformat()
            connection.error = str(exc)
            self.state.save_provider_connection(connection)
        self.state.save_sync_event(event)
        return event

    async def sync_completed_task_outbound(
        self, task: Task, *, force: bool = False,
    ) -> list[SyncEvent]:
        if task.status != "completed":
            return []
        completed_events: list[SyncEvent] = []
        links = [link for link in self.external_issue_links.values() if link.task_id == task.id]
        for link in links:
            if link.external_state == "closed" and not force:
                continue
            connection = self.provider_connections.get(link.connection_id)
            if (connection is None or not connection.enabled or not connection.auto_close
                    or connection.sync_direction == "inbound"):
                continue
            completed_events.append(await self._close_external_issue(
                task, link, connection, force=force,
            ))
        self.sync_events = self.state.load_sync_events()
        return completed_events

    async def run_due_provider_retries(self) -> list[SyncEvent]:
        now = datetime.now(timezone.utc)
        retried: list[SyncEvent] = []
        for event in self.state.load_sync_events(limit=500):
            if event.status != "queued" or not event.next_retry_at:
                continue
            if datetime.fromisoformat(event.next_retry_at) > now:
                continue
            task = self.tasks.get(event.task_id or "")
            link = self.external_issue_links.get((event.connection_id, event.external_id or ""))
            connection = self.provider_connections.get(event.connection_id)
            if task is None or link is None or connection is None:
                event.status = "failed"
                event.message = "Retry target no longer exists."
                event.completed_at = now.isoformat()
                self.state.save_sync_event(event)
                retried.append(event)
                continue
            retried.append(await self._close_external_issue(task, link, connection, event=event))
        if retried:
            self.sync_events = self.state.load_sync_events()
        return retried

    async def handle(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        request_id = message.get("id")
        kind = message["type"]
        payload = message.get("payload", {})
        if kind == "ping":
            return [self._response(request_id, "pong", {"workspace": str(self.workspace)})]
        if kind == "health_check":
            return [self._response(
                request_id, "health_report", {"health": await self.health_report()},
            )]
        if kind in {"list_tasks", "list_state"}:
            response_type = "task_list" if kind == "list_tasks" else "state_snapshot"
            return [self._response(request_id, response_type, self.state_payload())]
        if kind == "create_provider_connection":
            self._require(payload, "provider", "repository")
            provider = str(payload["provider"]).lower()
            adapter = self.providers.get(provider)
            if adapter is None:
                raise ProtocolError("unsupported_provider", f"Unsupported issue provider: {provider}")
            try:
                repository = validate_repository(str(payload["repository"]))
            except ProviderError as exc:
                raise ProtocolError(exc.code, str(exc)) from exc
            sync_direction = str(payload.get("syncDirection", "bidirectional"))
            if sync_direction not in {"inbound", "outbound", "bidirectional"}:
                raise ProtocolError(
                    "invalid_sync_direction",
                    "Sync direction must be inbound, outbound, or bidirectional.",
                )
            duplicate = next((
                item for item in self.provider_connections.values()
                if item.provider == provider and item.repository.lower() == repository.lower()
            ), None)
            if duplicate:
                raise ProtocolError(
                    "connection_exists", f"{repository} is already connected to Taskloom.",
                )
            connection = ProviderConnection(
                id=str(payload.get("connectionId") or uuid.uuid4()),
                provider=provider, repository=repository,
                sync_direction=sync_direction,
                auto_close=bool(payload.get("autoClose", True)),
                background_sync_enabled=bool(payload.get("backgroundSyncEnabled", True)),
                sync_interval_minutes=max(
                    5, min(int(payload.get("syncIntervalMinutes", 15)), 1_440),
                ),
            )
            self.provider_connections[connection.id] = connection
            self.state.save_provider_connection(connection)
            return [self._response(
                request_id, "provider_connection_created",
                {"connection": self.serialize_provider_connection(connection)},
            )]
        if kind == "test_provider_connection":
            self._require(payload, "connectionId")
            connection = self.provider_connections.get(str(payload["connectionId"]))
            if connection is None:
                raise ProtocolError("connection_not_found", "Provider connection does not exist.")
            adapter = self.providers.get(connection.provider)
            if adapter is None:
                raise ProtocolError(
                    "unsupported_provider", f"Unsupported issue provider: {connection.provider}",
                )
            event = SyncEvent(
                id=str(uuid.uuid4()), connection_id=connection.id, direction="system",
                action="test_connection", status="running",
                message=f"Testing access to {connection.repository}",
            )
            self.state.save_sync_event(event)
            connection.status = "testing"
            connection.error = None
            self.state.save_provider_connection(connection)
            try:
                result = await adapter.test_connection(connection.repository)
            except ProviderError as exc:
                connection.status = "error"
                connection.error = str(exc)
                event.status = "failed"
                event.message = str(exc)
                event.completed_at = datetime.now(timezone.utc).isoformat()
                self.state.save_provider_connection(connection)
                self.state.save_sync_event(event)
                self.sync_events = self.state.load_sync_events()
                raise ProtocolError(exc.code, str(exc)) from exc
            connection.repository = result.get("repository", connection.repository)
            connection.status = "connected"
            connection.error = None
            connection.consecutive_failures = 0
            if connection.background_sync_enabled and connection.sync_direction != "outbound":
                connection.next_sync_at = datetime.now(timezone.utc).isoformat()
            event.status = "completed"
            event.message = f"Connected to {connection.repository}"
            event.completed_at = datetime.now(timezone.utc).isoformat()
            self.state.save_provider_connection(connection)
            self.state.save_sync_event(event)
            self.sync_events = self.state.load_sync_events()
            return [self._response(
                request_id, "provider_connection_tested",
                {"connection": self.serialize_provider_connection(connection)},
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
        if kind == "update_provider_connection_sync":
            self._require(payload, "connectionId")
            connection = self.provider_connections.get(str(payload["connectionId"]))
            if connection is None:
                raise ProtocolError("connection_not_found", "Provider connection does not exist.")
            interval = int(payload.get("syncIntervalMinutes", connection.sync_interval_minutes))
            if interval < 5 or interval > 1_440:
                raise ProtocolError(
                    "invalid_sync_interval", "Automatic sync interval must be 5 to 1,440 minutes.",
                )
            connection.background_sync_enabled = bool(
                payload.get("backgroundSyncEnabled", connection.background_sync_enabled),
            )
            connection.sync_interval_minutes = interval
            if connection.background_sync_enabled and connection.sync_direction != "outbound":
                connection.next_sync_at = datetime.now(timezone.utc).isoformat()
            else:
                connection.next_sync_at = None
            self.state.save_provider_connection(connection)
            return [self._response(
                request_id, "provider_connection_updated",
                {"connection": self.serialize_provider_connection(connection)},
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
        if kind == "sync_provider_inbound":
            self._require(payload, "connectionId")
            connection = self.provider_connections.get(str(payload["connectionId"]))
            if connection is None:
                raise ProtocolError("connection_not_found", "Provider connection does not exist.")
            summary = await self.sync_provider_inbound(connection)
            return [self._response(
                request_id, "provider_sync_completed",
                {"connection": self.serialize_provider_connection(connection), "summary": summary},
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
        if kind == "sync_task_outbound":
            self._require(payload, "taskId")
            task = self.tasks.get(str(payload["taskId"]))
            if task is None:
                raise ProtocolError("task_not_found", "Task does not exist.")
            events = await self.sync_completed_task_outbound(
                task, force=bool(payload.get("force", False)),
            )
            return [self._response(
                request_id, "provider_sync_completed",
                {"events": [self.serialize_sync_event(event) for event in events]},
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
        if kind == "ingest_create_task":
            return [self._response(
                request_id, "governed_task_ingested", self.ingest_create_task(payload),
            )]
        if kind == "ingest_update_task":
            result = self.ingest_update_task(payload)
            task = self.tasks[result["task"]["id"]]
            await self.sync_completed_task_outbound(task)
            return [self._response(
                request_id, "governed_task_updated", result,
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
        if kind == "ingest_add_log":
            return [self._response(
                request_id, "governed_log_ingested", self.ingest_add_log(payload),
            )]
        if kind == "control_agent_session":
            self._require(payload, "sessionId", "action")
            session = self.control_agent_session(str(payload["sessionId"]), str(payload["action"]))
            return [self._response(
                request_id, "agent_session_controlled", {"session": self.serialize_session(session)},
            )]
        if kind == "get_agent_control_state":
            self._require(payload, "sessionId")
            self.sessions = {item.id: item for item in self.state.load_sessions()}
            session = self.sessions.get(str(payload["sessionId"]))
            if session is None:
                raise ProtocolError("session_not_found", "Agent session does not exist")
            return [self._response(
                request_id, "agent_control_state", {"session": self.serialize_session(session)},
            )]
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
            events = await self.sync_completed_task_outbound(task)
            return [self._response(
                request_id, "task_updated", {
                    "task": self.serialize_task(task),
                    "events": [self.serialize_sync_event(event) for event in events],
                },
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
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
                command=tuple(str(value) for value in item.get("command", [])),
                timeout_seconds=int(item.get("timeoutSeconds", 120)),
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
        if kind == "update_workflow":
            self._require(payload, "workflowId", "name", "description", "approvalMode", "steps")
            workflow = self.workflows.get(str(payload["workflowId"]))
            if workflow is None or workflow.archived:
                raise ProtocolError("workflow_not_found", "Workflow does not exist")
            raw_steps = payload["steps"]
            if not isinstance(raw_steps, list):
                raise ProtocolError("invalid_steps", "Workflow steps must be a list")
            updated = Workflow(
                id=workflow.id, name=str(payload["name"]), description=str(payload["description"]),
                approval_mode=str(payload["approvalMode"]),
                steps=tuple(WorkflowStep(
                    id=str(item.get("id") or uuid.uuid4()), name=str(item["name"]),
                    agent_id=str(item["agentId"]), kind=str(item["kind"]),
                    instruction=str(item["instruction"]),
                    depends_on=tuple(str(value) for value in item.get("dependsOn", [])),
                    command=tuple(str(value) for value in item.get("command", [])),
                    timeout_seconds=int(item.get("timeoutSeconds", 120)),
                ) for item in raw_steps),
                enabled=bool(payload.get("enabled", workflow.enabled)),
            )
            self._validate_workflow(updated)
            self.workflows[updated.id] = updated
            self.state.save_workflow(updated)
            return [self._response(
                request_id, "workflow_updated", {"workflow": self.serialize_workflow(updated)},
            )]
        if kind == "duplicate_workflow":
            self._require(payload, "workflowId")
            source = self.workflows.get(str(payload["workflowId"]))
            if source is None or source.archived:
                raise ProtocolError("workflow_not_found", "Workflow does not exist")
            duplicate = Workflow(
                id=str(uuid.uuid4()), name=str(payload.get("name") or f"{source.name} copy"),
                description=source.description, approval_mode=source.approval_mode,
                steps=source.steps, enabled=False,
            )
            self.workflows[duplicate.id] = duplicate
            self.state.save_workflow(duplicate)
            return [self._response(
                request_id, "workflow_created", {"workflow": self.serialize_workflow(duplicate)},
            )]
        if kind == "set_workflow_enabled":
            self._require(payload, "workflowId", "enabled")
            workflow = self.workflows.get(str(payload["workflowId"]))
            if workflow is None or workflow.archived:
                raise ProtocolError("workflow_not_found", "Workflow does not exist")
            workflow.enabled = bool(payload["enabled"])
            self.state.save_workflow(workflow)
            return [self._response(
                request_id, "workflow_updated", {"workflow": self.serialize_workflow(workflow)},
            )]
        if kind == "archive_workflow":
            self._require(payload, "workflowId")
            workflow = self.workflows.get(str(payload["workflowId"]))
            if workflow is None or workflow.archived:
                raise ProtocolError("workflow_not_found", "Workflow does not exist")
            if any(run.workflow_id == workflow.id and run.status in {
                "queued", "running", "needs_approval",
            } for run in self.workflow_runs.values()):
                raise ProtocolError("workflow_in_use", "Cancel active runs before deleting this workflow")
            workflow.enabled = False
            workflow.archived = True
            self.state.save_workflow(workflow)
            for trigger in self.triggers.values():
                if trigger.workflow_id == workflow.id:
                    trigger.enabled = False
                    self.state.save_trigger(trigger)
            for trigger in self.file_triggers.values():
                if trigger.workflow_id == workflow.id:
                    trigger.enabled = False
                    self.state.save_file_trigger(trigger)
            return [self._response(request_id, "workflow_archived", {"workflowId": workflow.id})]
        if kind == "run_workflow":
            self._require(payload, "workflowId", "goal", "targetFile")
            workflow = self.workflows.get(str(payload["workflowId"]))
            if workflow is None:
                raise ProtocolError("workflow_not_found", "Workflow is missing or disabled")
            run, approval = self._create_workflow_run(
                workflow, str(payload["goal"]), str(payload["targetFile"]),
            )
            created = self._response(
                request_id, "workflow_run_created", {"workflowRun": self.serialize_workflow_run(run)},
            )
            if approval:
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
        if kind == "create_trigger":
            self._require(payload, "workflowId", "name", "intervalMinutes", "goal", "targetFile")
            workflow = self.workflows.get(str(payload["workflowId"]))
            if workflow is None or workflow.archived:
                raise ProtocolError("workflow_not_found", "Workflow does not exist")
            try:
                interval = int(payload["intervalMinutes"])
            except (TypeError, ValueError) as exc:
                raise ProtocolError("invalid_interval", "Schedule interval must be a whole number") from exc
            if interval < 15:
                raise ProtocolError("unsafe_interval", "Schedule interval must be at least 15 minutes")
            self.snapshots.resolve_path(str(payload["targetFile"]))
            trigger = AutomationTrigger(
                id=str(payload.get("triggerId") or uuid.uuid4()), workflow_id=workflow.id,
                name=str(payload["name"]), interval_minutes=interval, goal=str(payload["goal"]),
                target_file=str(payload["targetFile"]), enabled=bool(payload.get("enabled", True)),
                next_run_at=str(payload.get("nextRunAt") or (
                    datetime.now(timezone.utc) + timedelta(minutes=interval)
                ).isoformat()),
            )
            self.triggers[trigger.id] = trigger
            self.state.save_trigger(trigger)
            return [self._response(
                request_id, "trigger_created", {"trigger": self.serialize_trigger(trigger)},
            )]
        if kind == "update_trigger":
            self._require(
                payload, "triggerId", "name", "intervalMinutes", "goal", "targetFile", "enabled",
            )
            trigger = self.triggers.get(str(payload["triggerId"]))
            if trigger is None:
                raise ProtocolError("trigger_not_found", "Schedule does not exist")
            try:
                interval = int(payload["intervalMinutes"])
            except (TypeError, ValueError) as exc:
                raise ProtocolError("invalid_interval", "Schedule interval must be a whole number") from exc
            if interval < 15:
                raise ProtocolError("unsafe_interval", "Schedule interval must be at least 15 minutes")
            self.snapshots.resolve_path(str(payload["targetFile"]))
            trigger.name = str(payload["name"])
            trigger.interval_minutes = interval
            trigger.goal = str(payload["goal"])
            trigger.target_file = str(payload["targetFile"])
            trigger.enabled = bool(payload["enabled"])
            trigger.next_run_at = str(payload.get("nextRunAt") or (
                datetime.now(timezone.utc) + timedelta(minutes=interval)
            ).isoformat())
            trigger.error = None
            self.state.save_trigger(trigger)
            return [self._response(
                request_id, "trigger_updated", {"trigger": self.serialize_trigger(trigger)},
            )]
        if kind == "set_trigger_enabled":
            self._require(payload, "triggerId", "enabled")
            trigger = self.triggers.get(str(payload["triggerId"]))
            if trigger is None:
                raise ProtocolError("trigger_not_found", "Schedule does not exist")
            trigger.enabled = bool(payload["enabled"])
            if trigger.enabled:
                trigger.next_run_at = (
                    datetime.now(timezone.utc) + timedelta(minutes=trigger.interval_minutes)
                ).isoformat()
            self.state.save_trigger(trigger)
            return [self._response(
                request_id, "trigger_updated", {"trigger": self.serialize_trigger(trigger)},
            )]
        if kind == "delete_trigger":
            self._require(payload, "triggerId")
            trigger = self.triggers.pop(str(payload["triggerId"]), None)
            if trigger is None:
                raise ProtocolError("trigger_not_found", "Schedule does not exist")
            self.state.delete_trigger(trigger.id)
            return [self._response(request_id, "trigger_deleted", {"triggerId": trigger.id})]
        if kind == "run_trigger_now":
            self._require(payload, "triggerId")
            trigger = self.triggers.get(str(payload["triggerId"]))
            if trigger is None:
                raise ProtocolError("trigger_not_found", "Schedule does not exist")
            workflow = self.workflows.get(trigger.workflow_id)
            if workflow is None:
                raise ProtocolError("workflow_not_found", "Scheduled workflow no longer exists")
            current = datetime.now(timezone.utc)
            trigger.last_run_at = current.isoformat()
            trigger.next_run_at = (
                current + timedelta(minutes=trigger.interval_minutes)
            ).isoformat()
            trigger.error = None
            run, _ = self._create_workflow_run(workflow, trigger.goal, trigger.target_file)
            trigger.last_run_id = run.id
            self.state.save_trigger(trigger)
            self._record_event(run.id, "triggered", f"Started manually from schedule: {trigger.name}")
            if run.status != "needs_approval":
                await self._execute_workflow(run)
            return [self._response(
                request_id, "trigger_ran",
                {"trigger": self.serialize_trigger(trigger),
                 "workflowRun": self.serialize_workflow_run(run)},
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
        if kind == "create_file_trigger":
            self._require(
                payload, "workflowId", "name", "watchPath", "pattern", "cooldownSeconds", "goal",
            )
            workflow = self.workflows.get(str(payload["workflowId"]))
            if workflow is None or workflow.archived:
                raise ProtocolError("workflow_not_found", "Workflow does not exist")
            try:
                cooldown = int(payload["cooldownSeconds"])
            except (TypeError, ValueError) as exc:
                raise ProtocolError("invalid_cooldown", "Cooldown must be a whole number") from exc
            if cooldown < 15 or cooldown > 86_400:
                raise ProtocolError("unsafe_cooldown", "Cooldown must be 15–86,400 seconds")
            watch_path = str(payload["watchPath"])
            pattern = str(payload["pattern"])
            if len(pattern) > 128 or "\x00" in pattern:
                raise ProtocolError("invalid_pattern", "File pattern is invalid or too long")
            self.snapshots.resolve_path(watch_path)
            trigger = FileTrigger(
                id=str(payload.get("triggerId") or uuid.uuid4()), workflow_id=workflow.id,
                name=str(payload["name"]), watch_path=watch_path, pattern=pattern,
                cooldown_seconds=cooldown, goal=str(payload["goal"]),
                enabled=bool(payload.get("enabled", True)),
            )
            trigger.baseline = self._scan_file_trigger(trigger)
            self.file_triggers[trigger.id] = trigger
            self.state.save_file_trigger(trigger)
            return [self._response(
                request_id, "file_trigger_created",
                {"fileTrigger": self.serialize_file_trigger(trigger)},
            )]
        if kind == "update_file_trigger":
            self._require(
                payload, "triggerId", "name", "watchPath", "pattern", "cooldownSeconds", "goal",
                "enabled",
            )
            trigger = self.file_triggers.get(str(payload["triggerId"]))
            if trigger is None:
                raise ProtocolError("trigger_not_found", "File watch does not exist")
            try:
                cooldown = int(payload["cooldownSeconds"])
            except (TypeError, ValueError) as exc:
                raise ProtocolError("invalid_cooldown", "Cooldown must be a whole number") from exc
            if cooldown < 15 or cooldown > 86_400:
                raise ProtocolError("unsafe_cooldown", "Cooldown must be 15–86,400 seconds")
            watch_path = str(payload["watchPath"])
            pattern = str(payload["pattern"])
            if len(pattern) > 128 or "\x00" in pattern:
                raise ProtocolError("invalid_pattern", "File pattern is invalid or too long")
            self.snapshots.resolve_path(watch_path)
            reset_baseline = watch_path != trigger.watch_path or pattern != trigger.pattern
            trigger.name = str(payload["name"])
            trigger.watch_path = watch_path
            trigger.pattern = pattern
            trigger.cooldown_seconds = cooldown
            trigger.goal = str(payload["goal"])
            trigger.enabled = bool(payload["enabled"])
            trigger.error = None
            if reset_baseline:
                trigger.baseline = self._scan_file_trigger(trigger)
            self.state.save_file_trigger(trigger)
            return [self._response(
                request_id, "file_trigger_updated",
                {"fileTrigger": self.serialize_file_trigger(trigger)},
            )]
        if kind == "set_file_trigger_enabled":
            self._require(payload, "triggerId", "enabled")
            trigger = self.file_triggers.get(str(payload["triggerId"]))
            if trigger is None:
                raise ProtocolError("trigger_not_found", "File watch does not exist")
            trigger.enabled = bool(payload["enabled"])
            trigger.error = None
            if trigger.enabled:
                trigger.baseline = self._scan_file_trigger(trigger)
            self.state.save_file_trigger(trigger)
            return [self._response(
                request_id, "file_trigger_updated",
                {"fileTrigger": self.serialize_file_trigger(trigger)},
            )]
        if kind == "delete_file_trigger":
            self._require(payload, "triggerId")
            trigger = self.file_triggers.pop(str(payload["triggerId"]), None)
            if trigger is None:
                raise ProtocolError("trigger_not_found", "File watch does not exist")
            self.state.delete_file_trigger(trigger.id)
            return [self._response(
                request_id, "file_trigger_deleted", {"triggerId": trigger.id},
            )]
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
                self._record_event(run.id, "plan_rejected", "Workflow plan rejected by user")
            elif payload["decision"] == "approve":
                run.plan_approved = True
                self.state.save_workflow_run(run)
                self._record_event(run.id, "plan_approved", "Workflow plan approved by user")
                await self._execute_workflow(run)
            self.state.save_workflow_run(run)
            return [self._response(
                request_id, "workflow_run_updated", {"workflowRun": self.serialize_workflow_run(run)},
            ), {"type": "state_snapshot", "payload": self.state_payload()}]
        if kind in {"resume_workflow", "retry_workflow"}:
            self._require(payload, "workflowRunId")
            run = self.workflow_runs.get(str(payload["workflowRunId"]))
            if run is None:
                raise ProtocolError("workflow_run_not_found", "Workflow run does not exist")
            if run.status not in {"queued", "failed"}:
                raise ProtocolError("invalid_run_state", f"Cannot resume a {run.status} workflow")
            self._record_event(run.id, "run_retried", "Workflow execution retried by user")
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
            self._record_event(run.id, "run_cancelled", "Workflow cancelled by user")
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
                    self._record_event(run.id, "change_approved", f"Approved {step.name}", step.id)
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
                    self._record_event(run.id, "change_rejected", f"Rejected {step.name}", step.id)
            if decision == "approve":
                await self.sync_completed_task_outbound(task)
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

    scheduler = asyncio.create_task(engine.run_scheduler(emit))
    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if line == "":
                break
            job = asyncio.create_task(process(line))
            jobs.add(job)
            job.add_done_callback(jobs.discard)
        if jobs:
            await asyncio.gather(*jobs)
    finally:
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)


def cli() -> None:
    parser = argparse.ArgumentParser(description="Taskloom local engine")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Root directory agents may access")
    args = parser.parse_args()
    asyncio.run(run_stdio(TaskloomEngine(args.workspace)))


if __name__ == "__main__":
    cli()
