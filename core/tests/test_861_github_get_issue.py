"""
test_861_github_get_issue.py — Unit-Tests fuer github_get_issue (#861).

Testet die GitHub Issue-Lesen-Funktionalitaet via public API.
Netzwerk-Calls werden gemockt via aiohttp.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class FakeResponse:
    def __init__(self, status: int, json_data: dict):
        self._status = status
        self._json = json_data

    @property
    def status(self) -> int:
        return self._status

    async def json(self) -> dict:
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeSession:
    def __init__(self, response: FakeResponse):
        self._response = response

    def get(self, url: str, *, headers: dict, timeout):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def tool():
    from hydrahive_core.tools_github import GitHubGetIssueTool
    return GitHubGetIssueTool()


@pytest.fixture
def project_id():
    return "test-project-861"


@pytest.fixture
def mock_github_issue():
    return {
        "number": 42,
        "title": "Test Issue",
        "body": "## Description\n\nTest body content.",
        "state": "open",
        "html_url": "https://github.com/hydrahive/hydrahive/issues/42",
        "labels": [
            {"id": 1, "name": "bug"},
            {"id": 2, "name": "priority:high"},
        ],
        "user": {"login": "testuser"},
        "created_at": "2025-01-15T10:00:00Z",
        "updated_at": "2025-01-16T12:30:00Z",
        "comments": 3,
        "assignees": [
            {"login": "dev1"},
            {"login": "dev2"},
        ],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_schema_parameters_are_correct(tool):
    """Parameter-Schema muss korrekt definiert sein."""
    params = tool.parameters
    assert params["type"] == "object"
    assert "issue_number" in params["properties"]
    assert "repo" in params["properties"]
    assert "issue_number" in params["required"]
    assert "repo" not in params["required"]


def test_schema_issue_number_is_integer(tool):
    params = tool.parameters
    assert params["properties"]["issue_number"]["type"] == "integer"


def test_tool_is_deferred(tool):
    assert tool.always_loaded is False


def test_tool_is_read_only(tool):
    assert tool.is_read_only is True


def test_tool_category(tool):
    assert tool.category == "github"


def test_tool_id(tool):
    assert tool.id == "github_get_issue"


@pytest.mark.asyncio
async def test_execute_returns_correct_issue_data(tool, project_id, mock_github_issue, tmp_path):
    """github_get_issue gibt alle relevanten Felder korrekt zurueck."""
    fake_resp = FakeResponse(200, mock_github_issue)
    fake_session = FakeSession(fake_resp)

    with patch("aiohttp.ClientSession", return_value=fake_session):
        with patch("hydrahive_core.tools_github.workspace_root", return_value=tmp_path):
            result = await tool.execute(
                agent_id="agent-1",
                project_id=project_id,
                issue_number=42,
                repo="hydrahive/hydrahive",
            )

    assert result["ok"] is True
    assert result["number"] == 42
    assert result["title"] == "Test Issue"
    assert result["body"] == "## Description\n\nTest body content."
    assert result["state"] == "open"
    assert result["url"] == "https://github.com/hydrahive/hydrahive/issues/42"
    assert result["labels"] == ["bug", "priority:high"]
    assert result["author"] == "testuser"
    assert result["created_at"] == "2025-01-15T10:00:00Z"
    assert result["updated_at"] == "2025-01-16T12:30:00Z"
    assert result["comments"] == 3
    assert result["assignees"] == ["dev1", "dev2"]


@pytest.mark.asyncio
async def test_execute_uses_project_github_repo_when_repo_not_provided(tool, project_id, mock_github_issue, tmp_path):
    """Wenn repo nicht angegeben, wird project.github_repo aus der Config gelesen."""
    fake_resp = FakeResponse(200, mock_github_issue)
    fake_session = FakeSession(fake_resp)

    mock_config = MagicMock()
    mock_config.github_repo = "myorg/myrepo"

    with patch("aiohttp.ClientSession", return_value=fake_session):
        with patch("hydrahive_core.tools_github.load_project_config", return_value=mock_config):
            with patch("hydrahive_core.tools_github.workspace_root", return_value=tmp_path):
                result = await tool.execute(
                    agent_id="agent-1",
                    project_id=project_id,
                    issue_number=42,
                )

    assert result["ok"] is True
    assert result["number"] == 42


@pytest.mark.asyncio
async def test_execute_404_returns_error(tool, project_id, tmp_path):
    """404 gibt einen verstaendlichen Fehler zurueck."""
    fake_resp = FakeResponse(404, {"message": "Not Found"})
    fake_session = FakeSession(fake_resp)

    with patch("aiohttp.ClientSession", return_value=fake_session):
        with patch("hydrahive_core.tools_github.workspace_root", return_value=tmp_path):
            result = await tool.execute(
                agent_id="agent-1",
                project_id=project_id,
                issue_number=99999,
                repo="hydrahive/hydrahive",
            )

    assert "error" in result
    assert "nicht gefunden" in result["error"]


@pytest.mark.asyncio
async def test_execute_no_github_repo_returns_error(tool, project_id, tmp_path):
    """Wenn weder repo noch project.github_repo vorhanden, Fehler."""
    with patch("hydrahive_core.tools_github.load_project_config", return_value=None):
        with patch("hydrahive_core.tools_github.workspace_root", return_value=tmp_path):
            result = await tool.execute(
                agent_id="agent-1",
                project_id=project_id,
                issue_number=42,
            )

    assert "error" in result
    assert "Kein github_repo" in result["error"]


@pytest.mark.asyncio
async def test_execute_network_error_returns_error(tool, project_id, tmp_path):
    """Netzwerkfehler werden als Fehler-Dict zurueckgegeben."""
    fake_resp = FakeResponse(500, {})
    fake_session = FakeSession(fake_resp)

    with patch("aiohttp.ClientSession", return_value=fake_session):
        with patch("hydrahive_core.tools_github.workspace_root", return_value=tmp_path):
            result = await tool.execute(
                agent_id="agent-1",
                project_id=project_id,
                issue_number=1,
                repo="hydrahive/hydrahive",
            )

    assert "error" in result
    assert "GitHub API" in result["error"]


@pytest.mark.asyncio
async def test_execute_empty_body_becomes_empty_string(tool, project_id, tmp_path):
    """Ein body=None wird zu '' statt None."""
    issue = {
        "number": 1, "title": "T", "body": None, "state": "open",
        "html_url": "https://github.com/x/y/issues/1",
        "labels": [], "user": {"login": "u"},
        "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        "comments": 0, "assignees": [],
    }
    fake_resp = FakeResponse(200, issue)
    fake_session = FakeSession(fake_resp)

    with patch("aiohttp.ClientSession", return_value=fake_session):
        with patch("hydrahive_core.tools_github.workspace_root", return_value=tmp_path):
            result = await tool.execute(
                agent_id="agent-1",
                project_id=project_id,
                issue_number=1,
                repo="x/y",
            )

    assert result["body"] == ""
