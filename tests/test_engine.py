from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine.main import ProtocolError, SnapshotStore, TaskloomEngine, normalize_generated_file, parse_message


class FakeLLM:
    def __init__(self, result: str = "new content\n") -> None:
        self.result = result
        self.calls: list[tuple[str, str, str, str | None]] = []

    async def generate(
        self, prompt: str, current: str, provider: str, model: str | None = None,
    ) -> str:
        assert prompt
        assert provider in {"ollama", "openai"}
        self.calls.append((prompt, current, provider, model))
        return self.result


class FlakyLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__("recovered\n")
        self.attempts = 0

    async def generate(
        self, prompt: str, current: str, provider: str, model: str | None = None,
    ) -> str:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("temporary provider failure")
        return await super().generate(prompt, current, provider, model)


def test_parse_message_accepts_valid_json() -> None:
    raw = json.dumps({"id": "request-1", "type": "ping", "payload": {}})
    assert parse_message(raw) == {"id": "request-1", "type": "ping", "payload": {}}


@pytest.mark.parametrize(
    ("raw", "code"),
    [("", "empty_message"), ("not json", "invalid_json"), ("[]", "invalid_message"), ('{"payload": {}}', "missing_type")],
)
def test_parse_message_rejects_invalid_input(raw: str, code: str) -> None:
    with pytest.raises(ProtocolError) as caught:
        parse_message(raw)
    assert caught.value.code == code


def test_normalize_generated_file_removes_outer_markdown_fence() -> None:
    generated = "```typescript\nexport const answer: number = 42;\n```\n"

    assert normalize_generated_file(generated) == "export const answer: number = 42;\n"


def test_normalize_generated_file_preserves_unfenced_content() -> None:
    generated = "export const answer: number = 42;\n"

    assert normalize_generated_file(generated) == generated


def test_normalize_generated_file_preserves_embedded_fence() -> None:
    generated = 'const documentation = "```ts example";\n'

    assert normalize_generated_file(generated) == generated


def test_snapshot_create_and_restore_existing_file(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("before", encoding="utf-8")
    store = SnapshotStore(tmp_path)

    snapshot_id = store.create("notes.txt")
    file_path.write_text("after", encoding="utf-8")
    restored = store.restore(snapshot_id)

    assert restored == "notes.txt"
    assert file_path.read_text(encoding="utf-8") == "before"
    metadata = json.loads((tmp_path / ".taskloom" / "snapshots" / snapshot_id / "snapshot.json").read_text())
    assert metadata["existed"] is True


def test_snapshot_restore_removes_new_file(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    snapshot_id = store.create("created-later.txt")
    (tmp_path / "created-later.txt").write_text("temporary", encoding="utf-8")

    store.restore(snapshot_id)

    assert not (tmp_path / "created-later.txt").exists()


def test_snapshot_rejects_workspace_escape(tmp_path: Path) -> None:
    with pytest.raises(ProtocolError, match="inside the workspace"):
        SnapshotStore(tmp_path).resolve_path("../secret.txt")


@pytest.mark.asyncio
async def test_task_moves_through_approval_and_writes_only_after_approval(tmp_path: Path) -> None:
    target = tmp_path / "README.md"
    target.write_text("old content\n", encoding="utf-8")
    engine = TaskloomEngine(tmp_path, llm=FakeLLM("new content\n"))

    created = await engine.handle({
        "id": "1", "type": "create_task",
        "payload": {"taskId": "task-1", "title": "Rewrite", "prompt": "Improve it", "filePath": "README.md", "provider": "ollama"},
    })
    assert created[0]["payload"]["task"]["status"] == "backlog"
    assert created[0]["payload"]["task"]["filePath"] == "README.md"
    assert "file_path" not in created[0]["payload"]["task"]

    responses = await engine.handle({"id": "2", "type": "run_task", "payload": {"taskId": "task-1"}})
    request = responses[1]["payload"]
    assert engine.tasks["task-1"].status == "needs_approval"
    assert target.read_text(encoding="utf-8") == "old content\n"
    assert request["before"] == "old content\n"
    assert request["after"] == "new content\n"

    approved = await engine.handle({
        "id": "3", "type": "approval_decision",
        "payload": {"requestId": request["requestId"], "decision": "approve"},
    })
    assert approved[0]["payload"]["task"]["status"] == "completed"
    assert approved[0]["payload"]["snapshotId"]
    assert target.read_text(encoding="utf-8") == "new content\n"


@pytest.mark.asyncio
async def test_reject_returns_task_to_backlog_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("safe", encoding="utf-8")
    engine = TaskloomEngine(tmp_path, llm=FakeLLM("unsafe"))
    await engine.handle({"type": "create_task", "payload": {"taskId": "t", "title": "Edit", "prompt": "change", "filePath": "file.txt"}})
    run = await engine.handle({"type": "run_task", "payload": {"taskId": "t"}})

    await engine.handle({"type": "approval_decision", "payload": {"requestId": run[1]["payload"]["requestId"], "decision": "reject"}})

    assert engine.tasks["t"].status == "backlog"
    assert target.read_text(encoding="utf-8") == "safe"


@pytest.mark.asyncio
async def test_tasks_survive_engine_restart(tmp_path: Path) -> None:
    first_engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await first_engine.handle({
        "type": "create_task",
        "payload": {
            "taskId": "persistent-task",
            "title": "Persistent task",
            "prompt": "Keep this task",
            "filePath": "notes/persistent.md",
            "provider": "ollama",
        },
    })

    restarted_engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    restored = await restarted_engine.handle({"id": "restore", "type": "list_tasks", "payload": {}})

    assert (tmp_path / ".taskloom" / "taskloom.db").is_file()
    assert restored[0]["payload"]["tasks"] == [{
        "id": "persistent-task",
        "title": "Persistent task",
        "prompt": "Keep this task",
        "status": "backlog",
        "filePath": "notes/persistent.md",
        "provider": "ollama",
        "error": None,
    }]


@pytest.mark.asyncio
async def test_pending_approval_survives_restart_and_can_be_approved(tmp_path: Path) -> None:
    target = tmp_path / "draft.md"
    target.write_text("before\n", encoding="utf-8")
    first_engine = TaskloomEngine(tmp_path, llm=FakeLLM("after\n"))
    await first_engine.handle({
        "type": "create_task",
        "payload": {"taskId": "approval-task", "title": "Edit", "prompt": "Improve", "filePath": "draft.md"},
    })
    run = await first_engine.handle({"type": "run_task", "payload": {"taskId": "approval-task"}})
    request_id = run[1]["payload"]["requestId"]

    restarted_engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    restored = await restarted_engine.handle({"type": "list_tasks", "payload": {}})

    assert restored[0]["payload"]["tasks"][0]["status"] == "needs_approval"
    assert restored[0]["payload"]["approvals"][0]["requestId"] == request_id
    assert restored[0]["payload"]["approvals"][0]["before"] == "before\n"
    assert restored[0]["payload"]["approvals"][0]["after"] == "after\n"

    await restarted_engine.handle({
        "type": "approval_decision",
        "payload": {"requestId": request_id, "decision": "approve"},
    })
    third_engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    final_state = await third_engine.handle({"type": "list_tasks", "payload": {}})

    assert target.read_text(encoding="utf-8") == "after\n"
    assert final_state[0]["payload"]["tasks"][0]["status"] == "completed"
    assert final_state[0]["payload"]["approvals"] == []


@pytest.mark.asyncio
async def test_interrupted_active_task_returns_to_backlog_on_restart(tmp_path: Path) -> None:
    first_engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await first_engine.handle({
        "type": "create_task",
        "payload": {"taskId": "interrupted", "title": "Task", "prompt": "Work", "filePath": "file.txt"},
    })
    first_engine.update_task("interrupted", "active")

    restarted_engine = TaskloomEngine(tmp_path, llm=FakeLLM())

    assert restarted_engine.tasks["interrupted"].status == "backlog"


@pytest.mark.asyncio
async def test_default_agent_team_and_workflow_are_seeded(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())

    response = await engine.handle({"id": "state", "type": "list_state", "payload": {}})
    payload = response[0]["payload"]

    assert {agent["id"] for agent in payload["agents"]} == {"planner", "builder", "reviewer"}
    assert payload["workflows"][0]["id"] == "safe-delivery"
    assert payload["workflows"][0]["approvalMode"] == "approve_plan"
    assert [step["kind"] for step in payload["workflows"][0]["steps"]] == [
        "analysis", "file_edit", "validate", "analysis",
    ]


@pytest.mark.asyncio
async def test_approve_plan_pauses_then_executes_all_agents(tmp_path: Path) -> None:
    target = tmp_path / "scratch" / "result.md"
    target.parent.mkdir()
    target.write_text("before\n", encoding="utf-8")
    llm = FakeLLM("after\n")
    engine = TaskloomEngine(tmp_path, llm=llm)

    await engine.handle({
        "id": "run", "type": "run_workflow",
        "payload": {
            "workflowId": "safe-delivery", "goal": "Improve the result",
            "targetFile": "scratch/result.md",
        },
    })

    run = next(iter(engine.workflow_runs.values()))
    approval = next(iter(engine.plan_approvals.values()))
    assert run.status == "needs_approval"
    assert target.read_text(encoding="utf-8") == "before\n"
    assert llm.calls == []

    await engine.handle({
        "id": "approve", "type": "plan_approval_decision",
        "payload": {"requestId": approval.request_id, "decision": "approve"},
    })

    assert run.status == "completed"
    assert run.plan_approved is True
    assert target.read_text(encoding="utf-8") == "after\n"
    assert {step.status for step in engine.step_runs.values()} == {"completed"}
    assert len(llm.calls) == 3
    assert list((tmp_path / ".taskloom" / "snapshots").iterdir())


@pytest.mark.asyncio
async def test_approve_changes_pauses_at_mutation_and_reject_cancels_run(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("safe\n", encoding="utf-8")
    engine = TaskloomEngine(tmp_path, llm=FakeLLM("proposed\n"))
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "review-each", "name": "Review each", "description": "Test",
            "approvalMode": "approve_changes",
            "steps": [{
                "id": "edit", "name": "Edit", "agentId": "builder", "kind": "file_edit",
                "instruction": "Edit the file", "dependsOn": [],
            }],
        },
    })
    await engine.handle({
        "type": "run_workflow",
        "payload": {"workflowId": "review-each", "goal": "Change it", "targetFile": "file.txt"},
    })

    run = next(run for run in engine.workflow_runs.values() if run.workflow_id == "review-each")
    change = next(change for change in engine.pending.values() if change.workflow_run_id == run.id)
    assert run.status == "needs_approval"
    assert target.read_text(encoding="utf-8") == "safe\n"

    await engine.handle({
        "type": "approval_decision",
        "payload": {"requestId": change.request_id, "decision": "reject"},
    })

    assert run.status == "cancelled"
    assert target.read_text(encoding="utf-8") == "safe\n"


@pytest.mark.asyncio
async def test_approved_workflow_change_resumes_run_to_completion(tmp_path: Path) -> None:
    target = tmp_path / "approved.txt"
    target.write_text("before\n", encoding="utf-8")
    engine = TaskloomEngine(tmp_path, llm=FakeLLM("after\n"))
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "approve-one", "name": "Approve one", "description": "Test",
            "approvalMode": "approve_changes",
            "steps": [{
                "id": "edit", "name": "Edit", "agentId": "builder", "kind": "file_edit",
                "instruction": "Edit the file", "dependsOn": [],
            }],
        },
    })
    await engine.handle({
        "type": "run_workflow",
        "payload": {"workflowId": "approve-one", "goal": "Change", "targetFile": "approved.txt"},
    })
    run = next(run for run in engine.workflow_runs.values() if run.workflow_id == "approve-one")
    change = next(change for change in engine.pending.values() if change.workflow_run_id == run.id)

    await engine.handle({
        "type": "approval_decision",
        "payload": {"requestId": change.request_id, "decision": "approve"},
    })

    assert run.status == "completed"
    assert target.read_text(encoding="utf-8") == "after\n"
    assert not engine.pending


@pytest.mark.asyncio
async def test_observe_mode_never_writes_generated_file(tmp_path: Path) -> None:
    target = tmp_path / "observed.txt"
    target.write_text("original\n", encoding="utf-8")
    engine = TaskloomEngine(tmp_path, llm=FakeLLM("proposal\n"))
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "observe", "name": "Observe", "description": "No writes",
            "approvalMode": "observe",
            "steps": [{
                "id": "draft", "name": "Draft", "agentId": "builder", "kind": "file_edit",
                "instruction": "Draft a change", "dependsOn": [],
            }],
        },
    })

    await engine.handle({
        "type": "run_workflow",
        "payload": {"workflowId": "observe", "goal": "Suggest", "targetFile": "observed.txt"},
    })

    run = next(run for run in engine.workflow_runs.values() if run.workflow_id == "observe")
    step = next(step for step in engine.step_runs.values() if step.workflow_run_id == run.id)
    assert run.status == "completed"
    assert step.output == "proposal\n"
    assert target.read_text(encoding="utf-8") == "original\n"


@pytest.mark.asyncio
async def test_custom_agents_and_workflows_survive_restart(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await engine.handle({
        "type": "create_agent",
        "payload": {
            "agentId": "security", "name": "Security", "role": "Risk reviewer",
            "instructions": "Find security risks", "provider": "ollama",
            "capabilities": ["analysis", "validate"],
        },
    })
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "security-review", "name": "Security review",
            "description": "Review a goal", "approvalMode": "observe",
            "steps": [{
                "id": "review", "name": "Review", "agentId": "security", "kind": "analysis",
                "instruction": "Review it", "dependsOn": [],
            }],
        },
    })

    restarted = TaskloomEngine(tmp_path, llm=FakeLLM())

    assert restarted.agents["security"].role == "Risk reviewer"
    assert restarted.workflows["security-review"].steps[0].agent_id == "security"


@pytest.mark.asyncio
async def test_workflow_rejects_forward_or_unknown_dependency(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())

    with pytest.raises(ProtocolError) as caught:
        await engine.handle({
            "type": "create_workflow",
            "payload": {
                "name": "Broken", "description": "Invalid graph", "approvalMode": "observe",
                "steps": [{
                    "id": "first", "name": "First", "agentId": "planner", "kind": "analysis",
                    "instruction": "Plan", "dependsOn": ["missing"],
                }],
            },
        })

    assert caught.value.code == "invalid_dependency"


@pytest.mark.asyncio
async def test_workflow_management_updates_duplicates_and_archives(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "managed", "name": "Managed", "description": "Original",
            "approvalMode": "observe", "steps": [{
                "id": "plan", "name": "Plan", "agentId": "planner", "kind": "analysis",
                "instruction": "Make a plan", "dependsOn": [],
            }],
        },
    })

    updated = await engine.handle({
        "type": "update_workflow",
        "payload": {
            "workflowId": "managed", "name": "Managed v2", "description": "Updated",
            "approvalMode": "trusted", "enabled": True, "steps": [{
                "id": "build", "name": "Build", "agentId": "builder", "kind": "file_edit",
                "instruction": "Build it", "dependsOn": [],
            }],
        },
    })
    duplicate = await engine.handle({
        "type": "duplicate_workflow", "payload": {"workflowId": "managed"},
    })
    duplicate_id = duplicate[0]["payload"]["workflow"]["id"]
    await engine.handle({
        "type": "archive_workflow", "payload": {"workflowId": "managed"},
    })

    restarted = TaskloomEngine(tmp_path, llm=FakeLLM())
    state = restarted.state_payload()
    assert updated[0]["payload"]["workflow"]["name"] == "Managed v2"
    assert restarted.workflows["managed"].archived is True
    assert duplicate_id in restarted.workflows
    assert restarted.workflows[duplicate_id].enabled is False
    assert "managed" not in {workflow["id"] for workflow in state["workflows"]}


@pytest.mark.asyncio
async def test_schedule_rejects_unsafe_interval(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())

    with pytest.raises(ProtocolError) as caught:
        await engine.handle({
            "type": "create_trigger",
            "payload": {
                "workflowId": "safe-delivery", "name": "Too frequent",
                "intervalMinutes": 1, "goal": "Run", "targetFile": "output.md",
            },
        })

    assert caught.value.code == "unsafe_interval"


@pytest.mark.asyncio
async def test_due_schedule_runs_once_and_persists_events(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM("analysis complete\n"))
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "scheduled-analysis", "name": "Scheduled analysis",
            "description": "Read-only schedule", "approvalMode": "observe", "steps": [{
                "id": "analyze", "name": "Analyze", "agentId": "planner", "kind": "analysis",
                "instruction": "Analyze the goal", "dependsOn": [],
            }],
        },
    })
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    created = await engine.handle({
        "type": "create_trigger",
        "payload": {
            "triggerId": "daily", "workflowId": "scheduled-analysis", "name": "Daily analysis",
            "intervalMinutes": 60, "goal": "Review status", "targetFile": "status.md",
            "nextRunAt": (now - timedelta(minutes=1)).isoformat(),
        },
    })

    first = await engine.run_due_triggers(now)
    second = await engine.run_due_triggers(now)

    assert created[0]["payload"]["trigger"]["id"] == "daily"
    assert len(first) == 1
    assert first[0].status == "completed"
    assert second == []
    assert engine.triggers["daily"].last_run_id == first[0].id
    assert datetime.fromisoformat(engine.triggers["daily"].next_run_at) == now + timedelta(hours=1)
    assert {event.event_type for event in engine.events if event.workflow_run_id == first[0].id} >= {
        "run_created", "triggered", "run_started", "step_completed", "run_completed",
    }

    restarted = TaskloomEngine(tmp_path, llm=FakeLLM())
    restored_run = restarted.serialize_workflow_run(restarted.workflow_runs[first[0].id])
    assert restarted.triggers["daily"].last_run_id == first[0].id
    assert restored_run["events"][-1]["type"] == "run_completed"


@pytest.mark.asyncio
async def test_run_now_executes_paused_schedule(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM("done\n"))
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "manual-schedule", "name": "Manual schedule", "description": "Test",
            "approvalMode": "observe", "steps": [{
                "id": "analyze", "name": "Analyze", "agentId": "planner", "kind": "analysis",
                "instruction": "Analyze", "dependsOn": [],
            }],
        },
    })
    await engine.handle({
        "type": "create_trigger",
        "payload": {
            "triggerId": "paused", "workflowId": "manual-schedule", "name": "Paused",
            "intervalMinutes": 30, "goal": "Run manually", "targetFile": "manual.md",
            "enabled": False,
        },
    })

    response = await engine.handle({
        "type": "run_trigger_now", "payload": {"triggerId": "paused"},
    })

    assert response[0]["payload"]["workflowRun"]["status"] == "completed"
    assert engine.triggers["paused"].enabled is False


@pytest.mark.asyncio
async def test_failed_workflow_can_retry_from_failed_step(tmp_path: Path) -> None:
    llm = FlakyLLM()
    engine = TaskloomEngine(tmp_path, llm=llm)
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "retryable", "name": "Retryable", "description": "Test retry",
            "approvalMode": "observe", "steps": [{
                "id": "analyze", "name": "Analyze", "agentId": "planner", "kind": "analysis",
                "instruction": "Analyze", "dependsOn": [],
            }],
        },
    })
    await engine.handle({
        "type": "run_workflow",
        "payload": {"workflowId": "retryable", "goal": "Recover", "targetFile": "retry.md"},
    })
    run = next(run for run in engine.workflow_runs.values() if run.workflow_id == "retryable")
    assert run.status == "failed"

    response = await engine.handle({
        "type": "retry_workflow", "payload": {"workflowRunId": run.id},
    })

    assert response[0]["payload"]["workflowRun"]["status"] == "completed"
    assert llm.attempts == 2
    assert "run_retried" in {
        event.event_type for event in engine.events if event.workflow_run_id == run.id
    }
