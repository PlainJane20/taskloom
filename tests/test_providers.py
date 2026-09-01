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
        self.issues = issues or []
        self.tested: list[str] = []
        self.listed: list[str] = []

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

    async def close_issue(self, repository: str, issue_number: int) -> object:
        raise NotImplementedError


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
