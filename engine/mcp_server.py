#!/usr/bin/env python3
"""Official MCP v2 adapter for Taskloom's governed ingestion service."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from engine.main import TaskloomEngine


def create_mcp_server(workspace: Path) -> tuple[MCPServer, TaskloomEngine]:
    """Create a stdio-capable MCP server bound to one guarded workspace."""
    engine = TaskloomEngine(workspace)
    write_lock = asyncio.Lock()
    server = MCPServer(
        "Taskloom",
        version="0.6.0",
        description="Governed local task ingestion, progress logging, and execution traces.",
        instructions=(
            "Use create_task for autonomous work. Supply an honest confidence_score and stable "
            "idempotency_key. Low-confidence work is routed to Drafts; related events may be clustered."
        ),
    )

    @server.tool(
        name="create_task",
        title="Create governed Taskloom task",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False,
        ),
        structured_output=True,
    )
    async def create_task(
        title: Annotated[str, Field(min_length=1, max_length=200)],
        prompt: Annotated[str, Field(min_length=1, max_length=20_000)],
        agent_id: Annotated[str, Field(min_length=1, max_length=200)],
        session_id: Annotated[str, Field(min_length=1, max_length=200)],
        confidence_score: Annotated[float, Field(ge=0.0, le=1.0)],
        idempotency_key: Annotated[str, Field(min_length=1, max_length=300)],
        file_path: str | None = None,
        branch_name: str | None = None,
        correlation_key: str | None = None,
        parent_task_id: str | None = None,
        provider: Literal["ollama", "openai"] = "ollama",
        git_sha: str | None = None,
        pr_url: str | None = None,
    ) -> dict[str, Any]:
        """Create a governed card; low confidence routes to Drafts and related work clusters."""
        payload = {
            "title": title, "prompt": prompt, "agentId": agent_id, "sessionId": session_id,
            "confidenceScore": confidence_score, "idempotencyKey": idempotency_key,
            "filePath": file_path, "branchName": branch_name,
            "correlationKey": correlation_key, "parentTaskId": parent_task_id,
            "provider": provider, "gitSha": git_sha, "prUrl": pr_url, "source": "mcp",
        }
        async with write_lock:
            return engine.ingest_create_task(payload)

    @server.tool(
        name="update_task",
        title="Update governed Taskloom task",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False,
        ),
        structured_output=True,
    )
    async def update_task(
        task_id: Annotated[str, Field(min_length=1)],
        agent_id: Annotated[str, Field(min_length=1)],
        session_id: Annotated[str, Field(min_length=1)],
        idempotency_key: Annotated[str, Field(min_length=1)],
        status: Literal[
            "draft", "backlog", "active", "blocked", "needs_approval", "completed",
            "failed", "cancelled",
        ] | None = None,
        summary: str | None = None,
        expected_version: Annotated[int | None, Field(ge=1)] = None,
        progress_current: Annotated[int | None, Field(ge=0)] = None,
        progress_total: Annotated[int | None, Field(ge=0)] = None,
        agent_status: Literal[
            "active", "waiting_for_human", "error_stuck", "idle", "completed",
        ] = "active",
        git_sha: str | None = None,
        pr_url: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Update state or progress with idempotency and optimistic version protection."""
        payload = {
            "taskId": task_id, "agentId": agent_id, "sessionId": session_id,
            "idempotencyKey": idempotency_key, "status": status, "summary": summary,
            "expectedVersion": expected_version, "progressCurrent": progress_current,
            "progressTotal": progress_total, "agentStatus": agent_status,
            "gitSha": git_sha, "prUrl": pr_url, "error": error, "source": "mcp",
        }
        async with write_lock:
            return engine.ingest_update_task(payload)

    @server.tool(
        name="add_log",
        title="Attach governed worklog or execution trace",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False,
        ),
        structured_output=True,
    )
    async def add_log(
        task_id: Annotated[str, Field(min_length=1)],
        agent_id: Annotated[str, Field(min_length=1)],
        session_id: Annotated[str, Field(min_length=1)],
        message: Annotated[str, Field(min_length=1, max_length=20_000)],
        idempotency_key: Annotated[str, Field(min_length=1)],
        kind: Literal["progress", "command", "note", "error"] = "progress",
        command_executed: str | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
        exit_code: int | None = None,
        progress_current: Annotated[int | None, Field(ge=0)] = None,
        progress_total: Annotated[int | None, Field(ge=0)] = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any]:
        """Attach a bounded, secret-redacted terminal trace to a Taskloom worklog."""
        payload = {
            "taskId": task_id, "agentId": agent_id, "sessionId": session_id,
            "message": message, "idempotencyKey": idempotency_key, "kind": kind,
            "commandExecuted": command_executed, "stdout": stdout, "stderr": stderr,
            "exitCode": exit_code, "progressCurrent": progress_current,
            "progressTotal": progress_total, "startedAt": started_at,
            "completedAt": completed_at, "source": "mcp",
        }
        async with write_lock:
            return engine.ingest_add_log(payload)

    @server.tool(
        name="get_board_state",
        title="Read Taskloom board state",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        structured_output=True,
    )
    async def get_board_state() -> dict[str, Any]:
        """Return tasks, sessions, worklogs, approvals, agents, and workflow runs."""
        return engine.state_payload()

    return server, engine


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Taskloom's MCP server over stdio")
    parser.add_argument(
        "--workspace", type=Path,
        default=Path(os.environ.get("TASKLOOM_WORKSPACE", Path.cwd())),
        help="Workspace Taskloom is allowed to govern (default: current directory)",
    )
    args = parser.parse_args()
    server, _engine = create_mcp_server(args.workspace)
    server.run("stdio")


if __name__ == "__main__":
    main()
