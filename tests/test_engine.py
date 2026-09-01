import json
from pathlib import Path

import pytest

from engine.main import ProtocolError, SnapshotStore, TaskloomEngine, normalize_generated_file, parse_message


class FakeLLM:
    def __init__(self, result: str = "new content\n") -> None:
        self.result = result

    async def generate(self, prompt: str, current: str, provider: str) -> str:
        assert prompt
        assert provider in {"ollama", "openai"}
        return self.result


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
