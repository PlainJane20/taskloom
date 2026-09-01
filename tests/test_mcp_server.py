from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from engine.mcp_server import create_mcp_server


@pytest.mark.asyncio
async def test_mcp_lists_governed_tools_with_required_schema(tmp_path: Path) -> None:
    server, _engine = create_mcp_server(tmp_path)

    async with Client(server) as client:
        result = await client.list_tools()

    tools = {tool.name: tool for tool in result.tools}
    assert set(tools) == {"create_task", "update_task", "add_log", "get_board_state"}
    create_schema = tools["create_task"].input_schema
    assert set(create_schema["required"]) >= {
        "title", "prompt", "agent_id", "session_id", "confidence_score", "idempotency_key",
    }
    assert create_schema["properties"]["confidence_score"]["minimum"] == 0.0
    assert create_schema["properties"]["confidence_score"]["maximum"] == 1.0
    assert tools["get_board_state"].annotations.read_only_hint is True
    assert tools["create_task"].annotations.idempotent_hint is True


@pytest.mark.asyncio
async def test_mcp_calls_share_governance_and_trace_service(tmp_path: Path) -> None:
    server, engine = create_mcp_server(tmp_path)

    async with Client(server) as client:
        created = await client.call_tool("create_task", {
            "title": "MCP test", "prompt": "Create a safe note", "file_path": "scratch/mcp.md",
            "agent_id": "codex", "session_id": "session-mcp", "confidence_score": 0.95,
            "idempotency_key": "mcp-create-1", "git_sha": "abc12345",
        })
        duplicate = await client.call_tool("create_task", {
            "title": "MCP test", "prompt": "Create a safe note", "file_path": "scratch/mcp.md",
            "agent_id": "codex", "session_id": "session-mcp", "confidence_score": 0.95,
            "idempotency_key": "mcp-create-1", "git_sha": "abc12345",
        })
        task_id = created.structured_content["task"]["id"]
        logged = await client.call_tool("add_log", {
            "task_id": task_id, "agent_id": "codex", "session_id": "session-mcp",
            "message": "Ran validation", "idempotency_key": "mcp-log-1",
            "command_executed": "TOKEN=private npm test", "stdout": "passed", "exit_code": 0,
        })

    # Structured results are available without scraping text content.
    assert created.structured_content["disposition"] == "created"
    assert duplicate.structured_content["disposition"] == "duplicate"
    assert logged.is_error is False
    assert "private" not in logged.structured_content["worklog"]["trace"]["commandExecuted"]
    task = next(iter(engine.tasks.values()))
    assert task.agent_id == "codex"
    assert engine.state.load_task_links(task.id)[0]["gitSha"] == "abc12345"


@pytest.mark.asyncio
async def test_mcp_low_confidence_card_lands_in_drafts(tmp_path: Path) -> None:
    server, _engine = create_mcp_server(tmp_path)

    async with Client(server) as client:
        result = await client.call_tool("create_task", {
            "title": "Uncertain work", "prompt": "Maybe change something",
            "agent_id": "agent-a", "session_id": "session-a", "confidence_score": 0.2,
            "idempotency_key": "draft-1",
        })

    assert result.structured_content["task"]["status"] == "draft"
    assert result.structured_content["task"]["governanceState"] == "pending_review"


@pytest.mark.asyncio
async def test_mcp_server_runs_over_real_stdio_transport(tmp_path: Path) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "engine.mcp_server", "--workspace", str(tmp_path)],
        cwd=Path(__file__).parents[1],
    )

    async with Client(parameters) as client:
        result = await client.list_tools()

    assert {tool.name for tool in result.tools} >= {"create_task", "update_task", "add_log"}
