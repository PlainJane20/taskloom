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
            """
        )

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
            SELECT request_id, task_id, file_path, before_content, after_content, summary
            FROM pending_changes ORDER BY created_at, rowid
            """
        ).fetchall()
        return [
            PendingChange(
                request_id=row["request_id"], task_id=row["task_id"], file_path=row["file_path"],
                before=row["before_content"], after=row["after_content"], summary=row["summary"],
            )
            for row in rows
        ]

    def save_pending(self, change: PendingChange) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO pending_changes
                    (request_id, task_id, file_path, before_content, after_content, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change.request_id, change.task_id, change.file_path, change.before, change.after,
                    change.summary, datetime.now(timezone.utc).isoformat(),
                ),
            )

    def delete_pending(self, request_id: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM pending_changes WHERE request_id = ?", (request_id,))


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

    async def generate(self, prompt: str, current: str, provider: str) -> str:
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
                "model": os.environ.get("TASKLOOM_OPENAI_MODEL", "gpt-4o-mini"),
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.2,
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            data = await asyncio.to_thread(self._post_json, "https://api.openai.com/v1/chat/completions", body, headers)
            return data["choices"][0]["message"]["content"]
        if provider == "ollama":
            body = {
                "model": os.environ.get("TASKLOOM_OLLAMA_MODEL", "llama3.2"),
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
    def __init__(self, workspace: Path, llm: LLMClient | None = None) -> None:
        self.workspace = workspace.resolve()
        self.snapshots = SnapshotStore(self.workspace)
        self.state = StateStore(self.workspace / ".taskloom" / "taskloom.db")
        self.llm = llm or LLMClient()
        self.tasks = {task.id: task for task in self.state.load_tasks()}
        self.pending = {change.request_id: change for change in self.state.load_pending()}
        self._recover_interrupted_state()

    def _recover_interrupted_state(self) -> None:
        """Make task state coherent after the process was closed mid-operation."""
        pending_task_ids = {change.task_id for change in self.pending.values()}
        for task in self.tasks.values():
            if task.id in pending_task_ids and task.status != "needs_approval":
                task.status = "needs_approval"
                self.state.save_task(task)
            elif task.status == "active" or (task.status == "needs_approval" and task.id not in pending_task_ids):
                task.status = "backlog"
                task.error = None
                self.state.save_task(task)

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
        """Keep the language-neutral IPC contract in frontend-friendly camelCase."""
        return {
            "id": task.id,
            "title": task.title,
            "prompt": task.prompt,
            "status": task.status,
            "filePath": task.file_path,
            "provider": task.provider,
            "error": task.error,
        }

    @staticmethod
    def serialize_change(change: PendingChange) -> dict[str, Any]:
        return {
            "taskId": change.task_id,
            "requestId": change.request_id,
            "filePath": change.file_path,
            "before": change.before,
            "after": change.after,
            "summary": change.summary,
        }

    async def handle(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        request_id = message.get("id")
        kind = message["type"]
        payload = message.get("payload", {})
        if kind == "ping":
            return [self._response(request_id, "pong", {"workspace": str(self.workspace)})]
        if kind == "list_tasks":
            return [self._response(request_id, "task_list", {
                "tasks": [self.serialize_task(t) for t in self.tasks.values()],
                "approvals": [self.serialize_change(change) for change in self.pending.values()],
            })]
        if kind == "create_task":
            self._require(payload, "title", "prompt", "filePath")
            task = Task(
                id=str(payload.get("taskId") or uuid.uuid4()),
                title=str(payload["title"]),
                prompt=str(payload["prompt"]),
                file_path=str(payload["filePath"]),
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
        if kind == "approval_decision":
            self._require(payload, "requestId", "decision")
            decision = payload["decision"]
            if decision not in {"approve", "reject"}:
                raise ProtocolError("invalid_decision", "Decision must be 'approve' or 'reject'")
            change = self.pending.get(str(payload["requestId"]))
            if change is None:
                raise ProtocolError("request_not_found", "Approval request is missing or already resolved")
            if decision == "approve":
                snapshot_id = self.snapshots.create(change.file_path)
                destination = self.snapshots.resolve_path(change.file_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(change.after, encoding="utf-8")
                task = self.update_task(change.task_id, "completed")
                self.pending.pop(change.request_id, None)
                self.state.delete_pending(change.request_id)
                return [self._response(request_id, "task_updated", {"task": self.serialize_task(task), "snapshotId": snapshot_id})]
            if decision == "reject":
                task = self.update_task(change.task_id, "backlog")
                self.pending.pop(change.request_id, None)
                self.state.delete_pending(change.request_id)
                return [self._response(request_id, "task_updated", {"task": self.serialize_task(task)})]
        if kind == "restore_snapshot":
            self._require(payload, "snapshotId")
            file_path = self.snapshots.restore(str(payload["snapshotId"]))
            return [self._response(request_id, "snapshot_restored", {"filePath": file_path})]
        if kind == "read_file":
            self._require(payload, "filePath")
            path = self.snapshots.resolve_path(str(payload["filePath"]))
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
            return [self._response(request_id, "file_content", {"filePath": payload["filePath"], "content": content})]
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
