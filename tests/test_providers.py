from __future__ import annotations

from pathlib import Path

import pytest

from engine.main import TaskloomEngine
from engine.providers import ExternalIssue, GitHubCLIAdapter, ProviderError, validate_repository


class FakeGitHubProvider:
    def __init__(
        self, *, failure: ProviderError | None = None,
        issues: list[ExternalIssue] | None = None,
    ) -> None:
        self.failure = failure
        self.outbound_failure: ProviderError | None = None
        self.issues = issues or []
        self.tested: list[str] = []
        self.listed: list[str] = []
        self.closed: list[tuple[str, int]] = []

    async def test_connection(self, repository: str) -> dict[str, str]:
        self.tested.append(repository)
        if self.failure:
            raise self.failure
        return {"provider": "github", "repository": repository, "status": "connected"}

    async def list_open_issues(self, repository: str) -> list[ExternalIssue]:
        self.listed.append(repository)
        if self.failure:
            raise self.failure
        return self.issues

    async def get_issue(self, repository: str, issue_number: int) -> ExternalIssue:
        if self.outbound_failure:
            raise self.outbound_failure
        return next(issue for issue in self.issues if issue.number == issue_number)

    async def close_issue(self, repository: str, issue_number: int) -> ExternalIssue:
        if self.outbound_failure:
            raise self.outbound_failure
        self.closed.append((repository, issue_number))
        current = await self.get_issue(repository, issue_number)
        closed = ExternalIssue(**{
            **current.__dict__, "state": "closed", "updated_at": "2026-09-01T14:00:00Z",
        })
        self.issues = [closed if item.number == issue_number else item for item in self.issues]
        return closed


@pytest.mark.parametrize(
    "repository",
    ["PlainJane20/taskloom", "open-ai/example.repo", "owner_name/repo-name"],
)
def test_validate_repository_accepts_owner_name(repository: str) -> None:
    assert validate_repository(f"  {repository}  ") == repository


@pytest.mark.parametrize(
    "repository",
    ["taskloom", "https://github.com/owner/repo", "owner/repo/extra", "owner/repo?tab=readme"],
)
def test_validate_repository_rejects_unsafe_or_ambiguous_values(repository: str) -> None:
    with pytest.raises(ProviderError) as caught:
        validate_repository(repository)
    assert caught.value.code == "invalid_repository"


def test_github_issue_mapping_preserves_link_metadata() -> None:
    issue = GitHubCLIAdapter._issue_from_payload({
        "id": 123, "number": 42, "title": "Ship sync", "body": None,
        "state": "open", "html_url": "https://github.com/acme/app/issues/42",
        "updated_at": "2026-09-01T00:00:00Z",
        "labels": [{"name": "feature"}], "assignees": [{"login": "octocat"}],
    })

    assert issue.external_id == "123"
    assert issue.number == 42
    assert issue.body == ""
    assert issue.labels == ("feature",)
    assert issue.assignees == ("octocat",)


@pytest.mark.asyncio
async def test_provider_connection_is_persisted_and_tested_without_credentials(tmp_path: Path) -> None:
    provider = FakeGitHubProvider()
    engine = TaskloomEngine(tmp_path, providers={"github": provider})

    created = await engine.handle({
        "id": "create", "type": "create_provider_connection",
        "payload": {
            "connectionId": "github-main", "provider": "github",
            "repository": "PlainJane20/taskloom", "syncDirection": "bidirectional",
            "autoClose": True,
        },
    })
    assert created[0]["payload"]["connection"]["status"] == "not_tested"

    tested = await engine.handle({
        "id": "test", "type": "test_provider_connection",
        "payload": {"connectionId": "github-main"},
    })
    assert tested[0]["payload"]["connection"]["status"] == "connected"
    assert provider.tested == ["PlainJane20/taskloom"]

    restarted = TaskloomEngine(tmp_path, providers={"github": provider})
    state = restarted.state_payload()
    assert state["providerConnections"][0]["repository"] == "PlainJane20/taskloom"
    assert state["syncEvents"][0]["action"] == "test_connection"
    assert state["syncEvents"][0]["status"] == "completed"

    database = restarted.state.connection
    columns = {row["name"] for row in database.execute("PRAGMA table_info(provider_connections)")}
    assert "token" not in columns
    assert "secret" not in columns


@pytest.mark.asyncio
async def test_failed_connection_check_persists_safe_error(tmp_path: Path) -> None:
    provider = FakeGitHubProvider(
        failure=ProviderError("github_request_failed", "Repository access denied."),
    )
    engine = TaskloomEngine(tmp_path, providers={"github": provider})
    await engine.handle({
        "type": "create_provider_connection",
        "payload": {"connectionId": "github-main", "provider": "github", "repository": "acme/app"},
    })

    with pytest.raises(Exception, match="Repository access denied"):
        await engine.handle({
            "type": "test_provider_connection", "payload": {"connectionId": "github-main"},
        })

    restarted = TaskloomEngine(tmp_path, providers={"github": provider})
    connection = restarted.state_payload()["providerConnections"][0]
    assert connection["status"] == "error"
    assert connection["error"] == "Repository access denied."


@pytest.mark.asyncio
async def test_duplicate_provider_connection_is_rejected(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, providers={"github": FakeGitHubProvider()})
    request = {
        "type": "create_provider_connection",
        "payload": {"provider": "github", "repository": "acme/app"},
    }
    await engine.handle(request)

    with pytest.raises(Exception, match="already connected"):
        await engine.handle(request)


@pytest.mark.asyncio
async def test_inbound_sync_is_idempotent_and_reconciles_remote_edits(tmp_path: Path) -> None:
    issue = ExternalIssue(
        external_id="9001", number=17, title="Add integration health panel",
        body="Show the last successful sync.", state="open",
        url="https://github.com/acme/app/issues/17",
        updated_at="2026-09-01T12:00:00Z", labels=("feature",),
    )
    provider = FakeGitHubProvider(issues=[issue])
    engine = TaskloomEngine(tmp_path, providers={"github": provider})
    await engine.handle({
        "type": "create_provider_connection",
        "payload": {"connectionId": "github-main", "provider": "github", "repository": "acme/app"},
    })
    await engine.handle({
        "type": "test_provider_connection", "payload": {"connectionId": "github-main"},
    })

    first = await engine.handle({
        "type": "sync_provider_inbound", "payload": {"connectionId": "github-main"},
    })
    assert first[0]["payload"]["summary"] == {"imported": 1, "updated": 0, "unchanged": 0}
    imported_task = next(iter(engine.tasks.values()))
    assert imported_task.source == "provider"
    assert imported_task.title == "Add integration health panel"
    assert engine.serialize_task(imported_task)["links"][0] == {
        "id": engine.serialize_task(imported_task)["links"][0]["id"],
        "kind": "issue", "provider": "github", "label": "acme/app#17",
        "url": "https://github.com/acme/app/issues/17", "gitSha": None,
        "createdAt": engine.serialize_task(imported_task)["links"][0]["createdAt"],
    }

    second = await engine.handle({
        "type": "sync_provider_inbound", "payload": {"connectionId": "github-main"},
    })
    assert second[0]["payload"]["summary"] == {"imported": 0, "updated": 0, "unchanged": 1}
    assert len(engine.tasks) == 1
    assert len(engine.external_issue_links) == 1

    provider.issues = [ExternalIssue(
        **{**issue.__dict__, "title": "Add provider health panel", "updated_at": "2026-09-01T13:00:00Z"},
    )]
    third = await engine.handle({
        "type": "sync_provider_inbound", "payload": {"connectionId": "github-main"},
    })
    assert third[0]["payload"]["summary"] == {"imported": 0, "updated": 1, "unchanged": 0}
    assert engine.tasks[imported_task.id].title == "Add provider health panel"

    restarted = TaskloomEngine(tmp_path, providers={"github": provider})
    assert len(restarted.tasks) == 1
    assert len(restarted.external_issue_links) == 1


@pytest.mark.asyncio
async def test_inbound_sync_requires_connected_inbound_connection(tmp_path: Path) -> None:
    engine = TaskloomEngine(tmp_path, providers={"github": FakeGitHubProvider()})
    await engine.handle({
        "type": "create_provider_connection",
        "payload": {
            "connectionId": "github-main", "provider": "github",
            "repository": "acme/app", "syncDirection": "outbound",
        },
    })

    with pytest.raises(Exception, match="Test the provider connection"):
        await engine.handle({
            "type": "sync_provider_inbound", "payload": {"connectionId": "github-main"},
        })


async def _engine_with_imported_issue(
    tmp_path: Path, provider: FakeGitHubProvider,
) -> tuple[TaskloomEngine, str]:
    engine = TaskloomEngine(tmp_path, providers={"github": provider})
    await engine.handle({
        "type": "create_provider_connection",
        "payload": {"connectionId": "github-main", "provider": "github", "repository": "acme/app"},
    })
    await engine.handle({
        "type": "test_provider_connection", "payload": {"connectionId": "github-main"},
    })
    await engine.handle({
        "type": "sync_provider_inbound", "payload": {"connectionId": "github-main"},
    })
    return engine, next(iter(engine.tasks))


def _outbound_issue() -> ExternalIssue:
    return ExternalIssue(
        external_id="9002", number=18, title="Close me when delivered",
        body="Two-way state synchronization.", state="open",
        url="https://github.com/acme/app/issues/18",
        updated_at="2026-09-01T12:00:00Z",
    )


@pytest.mark.asyncio
async def test_completed_imported_task_closes_linked_github_issue(tmp_path: Path) -> None:
    provider = FakeGitHubProvider(issues=[_outbound_issue()])
    engine, task_id = await _engine_with_imported_issue(tmp_path, provider)

    response = await engine.handle({
        "type": "update_task", "payload": {"taskId": task_id, "status": "completed"},
    })

    assert provider.closed == [("acme/app", 18)]
    link = next(iter(engine.external_issue_links.values()))
    assert link.external_state == "closed"
    event = engine.sync_events[0]
    assert event.direction == "outbound"
    assert event.status == "completed"
    assert response[0]["payload"]["events"][0]["message"] == "Closed acme/app#18"


@pytest.mark.asyncio
async def test_remote_edit_creates_conflict_instead_of_closing_issue(tmp_path: Path) -> None:
    provider = FakeGitHubProvider(issues=[_outbound_issue()])
    engine, task_id = await _engine_with_imported_issue(tmp_path, provider)
    provider.issues = [ExternalIssue(**{
        **_outbound_issue().__dict__, "body": "Edited remotely",
        "updated_at": "2026-09-01T13:00:00Z",
    })]

    await engine.handle({
        "type": "update_task", "payload": {"taskId": task_id, "status": "completed"},
    })

    assert provider.closed == []
    assert engine.sync_events[0].status == "conflict"
    assert "changed on GitHub" in engine.sync_events[0].message


@pytest.mark.asyncio
async def test_retryable_provider_failure_is_queued_and_retried(tmp_path: Path) -> None:
    provider = FakeGitHubProvider(issues=[_outbound_issue()])
    engine, task_id = await _engine_with_imported_issue(tmp_path, provider)
    provider.outbound_failure = ProviderError(
        "github_rate_limited", "GitHub rate limit exceeded.", retryable=True,
    )

    await engine.handle({
        "type": "update_task", "payload": {"taskId": task_id, "status": "completed"},
    })
    queued = engine.sync_events[0]
    assert queued.status == "queued"
    assert queued.attempt_count == 1
    assert queued.next_retry_at is not None

    provider.outbound_failure = None
    queued.next_retry_at = "2020-01-01T00:00:00+00:00"
    engine.state.save_sync_event(queued)
    retried = await engine.run_due_provider_retries()

    assert len(retried) == 1
    assert retried[0].status == "completed"
    assert retried[0].attempt_count == 2
    assert provider.closed == [("acme/app", 18)]
