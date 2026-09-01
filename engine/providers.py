"""External issue-provider adapters for Taskloom.

Adapters deliberately delegate credential storage to the provider's official
client. Taskloom persists connection metadata, never access tokens.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from typing import Any, Protocol


REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ProviderError(RuntimeError):
    """A safe, structured provider failure suitable for the IPC boundary."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ExternalIssue:
    external_id: str
    number: int
    title: str
    body: str
    state: str
    url: str
    updated_at: str
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()


class IssueProvider(Protocol):
    async def test_connection(self, repository: str) -> dict[str, str]: ...

    async def list_open_issues(self, repository: str) -> list[ExternalIssue]: ...

    async def close_issue(self, repository: str, issue_number: int) -> ExternalIssue: ...


def validate_repository(repository: str) -> str:
    normalized = repository.strip()
    if not REPOSITORY_PATTERN.fullmatch(normalized):
        raise ProviderError(
            "invalid_repository",
            "GitHub repository must use the 'owner/name' format.",
        )
    return normalized


class GitHubCLIAdapter:
    """GitHub Issues adapter backed by the authenticated official `gh` CLI."""

    def __init__(self, *, executable: str = "gh", timeout_seconds: int = 30) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    async def _run(self, *arguments: str) -> str:
        if shutil.which(self.executable) is None:
            raise ProviderError(
                "github_cli_missing",
                "GitHub CLI is not installed. Install `gh`, then run `gh auth login`.",
            )
        process = await asyncio.create_subprocess_exec(
            self.executable,
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ProviderError(
                "provider_timeout", "GitHub did not respond in time.", retryable=True,
            ) from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise ProviderError(
                "github_request_failed",
                detail[:1_000] or "GitHub CLI request failed.",
                retryable="rate limit" in detail.lower() or process.returncode in {1, 2},
            )
        return stdout.decode("utf-8", errors="replace")

    async def test_connection(self, repository: str) -> dict[str, str]:
        repository = validate_repository(repository)
        await self._run("auth", "status", "--active", "--hostname", "github.com")
        full_name = (await self._run(
            "api", f"repos/{repository}", "--jq", ".full_name",
        )).strip()
        if not full_name:
            raise ProviderError("repository_not_found", "GitHub repository was not found.")
        return {"provider": "github", "repository": full_name, "status": "connected"}

    @staticmethod
    def _issue_from_payload(payload: dict[str, Any]) -> ExternalIssue:
        return ExternalIssue(
            external_id=str(payload["id"]),
            number=int(payload["number"]),
            title=str(payload.get("title") or "Untitled GitHub issue"),
            body=str(payload.get("body") or ""),
            state=str(payload.get("state") or "open"),
            url=str(payload.get("html_url") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            labels=tuple(str(item.get("name")) for item in payload.get("labels", []) if item.get("name")),
            assignees=tuple(str(item.get("login")) for item in payload.get("assignees", []) if item.get("login")),
        )

    async def list_open_issues(self, repository: str) -> list[ExternalIssue]:
        repository = validate_repository(repository)
        raw = await self._run(
            "api", f"repos/{repository}/issues", "--method", "GET",
            "-f", "state=open", "-f", "per_page=100", "--paginate", "--slurp",
        )
        try:
            pages = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("invalid_provider_response", "GitHub returned invalid JSON.") from exc
        issues: list[ExternalIssue] = []
        for page in pages:
            for item in page:
                # GitHub's issues endpoint also returns pull requests.
                if "pull_request" not in item:
                    issues.append(self._issue_from_payload(item))
        return issues

    async def close_issue(self, repository: str, issue_number: int) -> ExternalIssue:
        repository = validate_repository(repository)
        if issue_number < 1:
            raise ProviderError("invalid_issue_number", "GitHub issue number must be positive.")
        raw = await self._run(
            "api", f"repos/{repository}/issues/{issue_number}",
            "--method", "PATCH", "-f", "state=closed",
        )
        try:
            return self._issue_from_payload(json.loads(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError("invalid_provider_response", "GitHub returned invalid issue data.") from exc
