from __future__ import annotations

import json
import sqlite3
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
    assert restored[0]["payload"]["tasks"][0] | {
        "createdAt": None, "updatedAt": None, "links": [], "worklogs": [],
    } == {
        "id": "persistent-task",
        "title": "Persistent task",
        "prompt": "Keep this task",
        "status": "backlog",
        "filePath": "notes/persistent.md",
        "provider": "ollama",
        "error": None,
        "source": "manual",
        "governanceState": "accepted",
        "confidenceScore": None,
        "agentId": None,
        "sessionId": None,
        "branchName": None,
        "parentTaskId": None,
        "clusterKey": None,
        "progressCurrent": 0,
        "progressTotal": 0,
        "version": 1,
        "createdAt": None,
        "updatedAt": None,
        "links": [],
        "worklogs": [],
    }


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


async def create_file_watch_workflow(
    engine: TaskloomEngine, workflow_id: str = "watched", *,
    approval_mode: str = "observe", kind: str = "analysis",
) -> None:
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": workflow_id, "name": "Watched workflow",
            "description": "Runs for changed files", "approvalMode": approval_mode,
            "steps": [{
                "id": "work", "name": "Work",
                "agentId": "builder" if kind == "file_edit" else "planner",
                "kind": kind, "instruction": "Handle the changed file", "dependsOn": [],
            }],
        },
    })


@pytest.mark.asyncio
async def test_file_watch_baselines_then_runs_once_for_matching_change(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    watched = inbox / "note.md"
    watched.write_text("baseline\n", encoding="utf-8")
    engine = TaskloomEngine(tmp_path, llm=FakeLLM("analyzed\n"))
    await create_file_watch_workflow(engine)

    created = await engine.handle({
        "type": "create_file_trigger",
        "payload": {
            "triggerId": "markdown-watch", "workflowId": "watched", "name": "Markdown",
            "watchPath": "inbox", "pattern": "**/*.md", "cooldownSeconds": 15,
            "goal": "Review {file}",
        },
    })
    assert created[0]["payload"]["fileTrigger"]["trackedFiles"] == 1
    assert await engine.run_due_file_triggers() == []

    watched.write_text("changed and larger\n", encoding="utf-8")
    first = await engine.run_due_file_triggers()
    second = await engine.run_due_file_triggers()

    assert len(first) == 1
    assert first[0].target_file == "inbox/note.md"
    assert first[0].goal == "Review inbox/note.md"
    assert first[0].status == "completed"
    assert second == []
    assert engine.file_triggers["markdown-watch"].last_run_id == first[0].id


@pytest.mark.asyncio
async def test_file_watch_ignores_nonmatching_and_generated_directories(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    generated = inbox / "node_modules" / "package"
    generated.mkdir(parents=True)
    inbox.joinpath("note.txt").write_text("before\n", encoding="utf-8")
    generated.joinpath("generated.md").write_text("before\n", encoding="utf-8")
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await create_file_watch_workflow(engine)
    await engine.handle({
        "type": "create_file_trigger",
        "payload": {
            "triggerId": "filtered", "workflowId": "watched", "name": "Filtered",
            "watchPath": "inbox", "pattern": "**/*.md", "cooldownSeconds": 15,
            "goal": "Review {file}",
        },
    })

    inbox.joinpath("note.txt").write_text("changed\n", encoding="utf-8")
    generated.joinpath("generated.md").write_text("changed\n", encoding="utf-8")

    assert await engine.run_due_file_triggers() == []
    assert engine.file_triggers["filtered"].baseline == {}


@pytest.mark.asyncio
async def test_file_watch_cooldown_defers_a_second_change(tmp_path: Path) -> None:
    watched = tmp_path / "input.md"
    watched.write_text("one\n", encoding="utf-8")
    engine = TaskloomEngine(tmp_path, llm=FakeLLM("done\n"))
    await create_file_watch_workflow(engine)
    await engine.handle({
        "type": "create_file_trigger",
        "payload": {
            "triggerId": "cooldown", "workflowId": "watched", "name": "Cooldown",
            "watchPath": "input.md", "pattern": "*", "cooldownSeconds": 30,
            "goal": "Review {file}",
        },
    })
    now = datetime.now(timezone.utc)
    watched.write_text("two and larger\n", encoding="utf-8")
    assert len(await engine.run_due_file_triggers(now)) == 1
    watched.write_text("three is even larger\n", encoding="utf-8")

    assert await engine.run_due_file_triggers(now + timedelta(seconds=10)) == []
    assert len(await engine.run_due_file_triggers(now + timedelta(seconds=31))) == 1


@pytest.mark.asyncio
async def test_file_watch_and_baseline_survive_restart(tmp_path: Path) -> None:
    watched = tmp_path / "persistent.md"
    watched.write_text("before\n", encoding="utf-8")
    first = TaskloomEngine(tmp_path, llm=FakeLLM())
    await create_file_watch_workflow(first)
    await first.handle({
        "type": "create_file_trigger",
        "payload": {
            "triggerId": "persistent-watch", "workflowId": "watched", "name": "Persistent",
            "watchPath": "persistent.md", "pattern": "*", "cooldownSeconds": 15,
            "goal": "Review {file}",
        },
    })

    restarted = TaskloomEngine(tmp_path, llm=FakeLLM("restored\n"))
    assert await restarted.run_due_file_triggers() == []
    watched.write_text("after restart\n", encoding="utf-8")
    runs = await restarted.run_due_file_triggers()

    assert len(runs) == 1
    assert restarted.state_payload()["fileTriggers"][0]["id"] == "persistent-watch"


@pytest.mark.asyncio
async def test_file_watch_rejects_workspace_escape(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await create_file_watch_workflow(engine)

    with pytest.raises(ProtocolError) as caught:
        await engine.handle({
            "type": "create_file_trigger",
            "payload": {
                "workflowId": "watched", "name": "Unsafe", "watchPath": "../outside",
                "pattern": "*", "cooldownSeconds": 15, "goal": "Review {file}",
            },
        })

    assert caught.value.code == "unsafe_path"


@pytest.mark.asyncio
async def test_file_watch_suppresses_workflow_write_feedback_loop(tmp_path: Path) -> None:
    watched = tmp_path / "loop.md"
    watched.write_text("baseline\n", encoding="utf-8")
    engine = TaskloomEngine(tmp_path, llm=FakeLLM("workflow output\n"))
    await create_file_watch_workflow(engine, approval_mode="trusted", kind="file_edit")
    await engine.handle({
        "type": "create_file_trigger",
        "payload": {
            "triggerId": "loop-safe", "workflowId": "watched", "name": "Loop safe",
            "watchPath": "loop.md", "pattern": "*", "cooldownSeconds": 15,
            "goal": "Update {file}",
        },
    })
    now = datetime.now(timezone.utc)
    watched.write_text("external change\n", encoding="utf-8")

    first = await engine.run_due_file_triggers(now)
    repeated = await engine.run_due_file_triggers(now + timedelta(seconds=16))

    assert len(first) == 1
    assert watched.read_text(encoding="utf-8") == "workflow output\n"
    assert repeated == []


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


@pytest.mark.asyncio
async def test_validation_command_captures_output_and_completes(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "command-success", "name": "Command success",
            "description": "Run a validation command", "approvalMode": "observe", "steps": [{
                "id": "test", "name": "Test", "agentId": "reviewer", "kind": "validate",
                "instruction": "Run validation", "dependsOn": [],
                "command": ["python3", "-c", "print('validation ok')"], "timeoutSeconds": 10,
            }],
        },
    })

    await engine.handle({
        "type": "run_workflow",
        "payload": {"workflowId": "command-success", "goal": "Validate", "targetFile": "result.txt"},
    })

    run = next(run for run in engine.workflow_runs.values() if run.workflow_id == "command-success")
    step = next(step for step in engine.step_runs.values() if step.workflow_run_id == run.id)
    assert run.status == "completed"
    assert step.status == "completed"
    assert step.output == "validation ok\n"


@pytest.mark.asyncio
async def test_validation_command_failure_preserves_output_and_exit_code(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "command-failure", "name": "Command failure",
            "description": "Fail validation", "approvalMode": "observe", "steps": [{
                "id": "test", "name": "Test", "agentId": "reviewer", "kind": "validate",
                "instruction": "Run validation", "dependsOn": [],
                "command": ["python3", "-c", "print('failed check'); raise SystemExit(3)"],
                "timeoutSeconds": 10,
            }],
        },
    })

    await engine.handle({
        "type": "run_workflow",
        "payload": {"workflowId": "command-failure", "goal": "Validate", "targetFile": "result.txt"},
    })

    run = next(run for run in engine.workflow_runs.values() if run.workflow_id == "command-failure")
    step = next(step for step in engine.step_runs.values() if step.workflow_run_id == run.id)
    assert run.status == "failed"
    assert "exited with code 3" in (run.error or "")
    assert step.output == "failed check\n"


@pytest.mark.asyncio
async def test_validation_command_timeout_stops_process(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "command-timeout", "name": "Command timeout",
            "description": "Time out validation", "approvalMode": "observe", "steps": [{
                "id": "test", "name": "Test", "agentId": "reviewer", "kind": "validate",
                "instruction": "Run validation", "dependsOn": [],
                "command": ["python3", "-c", "import time; time.sleep(2)"], "timeoutSeconds": 1,
            }],
        },
    })

    await engine.handle({
        "type": "run_workflow",
        "payload": {"workflowId": "command-timeout", "goal": "Validate", "targetFile": "result.txt"},
    })

    run = next(run for run in engine.workflow_runs.values() if run.workflow_id == "command-timeout")
    assert run.status == "failed"
    assert "exceeded 1 seconds" in (run.error or "")


@pytest.mark.asyncio
async def test_validation_command_rejects_shells_and_workspace_escape(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())

    with pytest.raises(ProtocolError) as shell_error:
        await engine.handle({
            "type": "create_workflow",
            "payload": {
                "name": "Unsafe shell", "description": "Unsafe", "approvalMode": "observe",
                "steps": [{
                    "id": "test", "name": "Test", "agentId": "reviewer", "kind": "validate",
                    "instruction": "Run", "dependsOn": [], "command": ["sh", "-c", "echo unsafe"],
                }],
            },
        })
    assert shell_error.value.code == "unsafe_executable"

    with pytest.raises(ProtocolError) as path_error:
        await engine.handle({
            "type": "create_workflow",
            "payload": {
                "name": "Unsafe path", "description": "Unsafe", "approvalMode": "observe",
                "steps": [{
                    "id": "test", "name": "Test", "agentId": "reviewer", "kind": "validate",
                    "instruction": "Run", "dependsOn": [],
                    "command": ["python3", "../outside.py"],
                }],
            },
        })
    assert path_error.value.code == "unsafe_argument"


@pytest.mark.asyncio
async def test_validation_command_configuration_survives_restart(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await engine.handle({
        "type": "create_workflow",
        "payload": {
            "workflowId": "persistent-command", "name": "Persistent command",
            "description": "Persist command", "approvalMode": "observe", "steps": [{
                "id": "test", "name": "Test", "agentId": "reviewer", "kind": "validate",
                "instruction": "Run validation", "dependsOn": [],
                "command": ["npm", "test"], "timeoutSeconds": 240,
            }],
        },
    })

    restarted = TaskloomEngine(tmp_path, llm=FakeLLM())
    step = restarted.workflows["persistent-command"].steps[0]
    serialized = restarted.serialize_workflow(restarted.workflows["persistent-command"])
    assert step.command == ("npm", "test")
    assert step.timeout_seconds == 240
    assert serialized["steps"][0]["command"] == ["npm", "test"]


def governed_task_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "taskId": "governed-1", "title": "Update authentication module",
        "prompt": "Implement the scoped authentication update", "filePath": "src/auth/index.ts",
        "agentId": "builder-1", "sessionId": "session-1", "branchName": "feature/auth",
        "confidenceScore": 0.91, "idempotencyKey": "event-1", "source": "mcp",
    }
    payload.update(changes)
    return payload


@pytest.mark.asyncio
async def test_low_confidence_autonomous_task_is_routed_to_drafts(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())

    response = await engine.handle({
        "type": "ingest_create_task",
        "payload": governed_task_payload(confidenceScore=0.42),
    })

    result = response[0]["payload"]
    assert result["disposition"] == "drafted"
    assert result["task"]["status"] == "draft"
    assert result["task"]["governanceState"] == "pending_review"
    assert result["task"]["confidenceScore"] == 0.42
    assert result["task"]["sessionId"] == "session-1"


@pytest.mark.asyncio
async def test_governed_ingestion_is_idempotent(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    message = {"type": "ingest_create_task", "payload": governed_task_payload()}

    first = await engine.handle(message)
    second = await engine.handle(message)

    assert first[0]["payload"]["disposition"] == "created"
    assert second[0]["payload"]["disposition"] == "duplicate"
    assert len(engine.tasks) == 1


@pytest.mark.asyncio
async def test_minor_updates_cluster_under_one_parent_task(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    first = governed_task_payload(correlationKey="auth-batch")
    second = governed_task_payload(
        taskId="governed-2", idempotencyKey="event-2", correlationKey="auth-batch",
        prompt="Finished a second small edit", summary="Updated token validation",
    )

    await engine.handle({"type": "ingest_create_task", "payload": first})
    response = await engine.handle({"type": "ingest_create_task", "payload": second})

    result = response[0]["payload"]
    assert result["disposition"] == "clustered"
    assert len(engine.tasks) == 1
    assert result["task"]["progressTotal"] == 2
    assert result["task"]["worklogs"][0]["kind"] == "clustered_update"
    assert result["task"]["worklogs"][0]["message"] == "Updated token validation"


@pytest.mark.asyncio
async def test_worklog_trace_is_redacted_bounded_and_persistent(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await engine.handle({"type": "ingest_create_task", "payload": governed_task_payload()})

    response = await engine.handle({
        "type": "ingest_add_log",
        "payload": {
            "taskId": "governed-1", "agentId": "builder-1", "sessionId": "session-1",
            "idempotencyKey": "log-1", "message": "Ran tests", "kind": "command",
            "commandExecuted": "TOKEN=super-secret npm test", "stdout": "x" * 70_000,
            "stderr": "Authorization: Bearer secret-token", "exitCode": 0,
        },
    })

    trace = response[0]["payload"]["worklog"]["trace"]
    assert trace["truncated"] is True
    assert len(trace["stdout"].encode("utf-8")) == engine.MAX_COMMAND_OUTPUT_BYTES
    assert "super-secret" not in trace["commandExecuted"]
    assert "secret-token" not in trace["stderr"]
    assert "[REDACTED]" in trace["commandExecuted"]

    restarted = TaskloomEngine(tmp_path, llm=FakeLLM())
    restored_task = restarted.serialize_task(restarted.tasks["governed-1"])
    assert restored_task["worklogs"][0]["trace"]["contentSha256"] == trace["contentSha256"]
    assert restored_task["worklogs"][0]["trace"]["worklogId"] == restored_task["worklogs"][0]["id"]
    assert restarted.serialize_session(restarted.sessions["session-1"])["status"] == "active"


@pytest.mark.asyncio
async def test_governed_update_uses_optimistic_version_and_links(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    created = await engine.handle({
        "type": "ingest_create_task",
        "payload": governed_task_payload(gitSha="abc123def", prUrl="https://github.com/acme/repo/pull/7"),
    })
    version = created[0]["payload"]["task"]["version"]

    updated = await engine.handle({
        "type": "ingest_update_task",
        "payload": {
            "taskId": "governed-1", "agentId": "builder-1", "sessionId": "session-1",
            "idempotencyKey": "update-1", "expectedVersion": version,
            "status": "active", "progressCurrent": 1, "progressTotal": 2,
        },
    })
    assert updated[0]["payload"]["task"]["version"] == version + 1
    assert {link["kind"] for link in updated[0]["payload"]["task"]["links"]} == {
        "commit", "pull_request",
    }

    with pytest.raises(ProtocolError) as conflict:
        await engine.handle({
            "type": "ingest_update_task",
            "payload": {
                "taskId": "governed-1", "agentId": "builder-1", "sessionId": "session-1",
                "idempotencyKey": "update-2", "expectedVersion": version,
                "status": "completed",
            },
        })
    assert conflict.value.code == "version_conflict"


@pytest.mark.asyncio
async def test_agent_controls_require_advertised_cooperative_capability(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, llm=FakeLLM())
    await engine.handle({
        "type": "ingest_create_task",
        "payload": governed_task_payload(controlCapabilities=["pause", "resume", "kill"]),
    })

    paused = await engine.handle({
        "type": "control_agent_session",
        "payload": {"sessionId": "session-1", "action": "pause"},
    })
    assert paused[0]["payload"]["session"]["status"] == "idle"
    resumed = await engine.handle({
        "type": "control_agent_session",
        "payload": {"sessionId": "session-1", "action": "resume"},
    })
    assert resumed[0]["payload"]["session"]["status"] == "active"

    engine.sessions["session-1"].control_capabilities = ()
    engine.state.save_session(engine.sessions["session-1"])
    with pytest.raises(ProtocolError) as unsupported:
        engine.control_agent_session("session-1", "kill")
    assert unsupported.value.code == "control_not_supported"


def test_v6_schema_migrates_legacy_tasks_without_data_loss(tmp_path: Path) -> None:
    database = tmp_path / ".taskloom" / "taskloom.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE tasks (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,
            status TEXT NOT NULL, file_path TEXT, provider TEXT NOT NULL,
            error TEXT, updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy", "Legacy", "Preserve me", "backlog", "legacy.md", "ollama", None,
         datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()
    connection.close()

    engine = TaskloomEngine(tmp_path, llm=FakeLLM())

    assert engine.tasks["legacy"].source == "manual"
    assert engine.tasks["legacy"].version == 1
    migration = engine.state.connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 6"
    ).fetchone()
    assert migration["name"] == "governance-foundation"
