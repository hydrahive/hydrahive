"""Tests für Issue #859 — github_repo in UpdateProjectSettingsRequest."""
import pytest
from hydrahive_core.router_projects import UpdateProjectSettingsRequest


def test_update_project_settings_request_has_github_repo_field():
    """#859: UpdateProjectSettingsRequest muss github_repo Feld haben."""
    req = UpdateProjectSettingsRequest(github_repo="hydrahive/hydrahive")
    assert req.github_repo == "hydrahive/hydrahive"


def test_update_project_settings_request_github_repo_optional():
    """#859: github_repo ist str | None, muss nicht gesetzt sein."""
    req = UpdateProjectSettingsRequest()
    assert req.github_repo is None


def test_update_project_settings_request_github_repo_empty_string():
    """#859: github_repo darf leer sein (kein Repo)."""
    req = UpdateProjectSettingsRequest(github_repo="")
    assert req.github_repo == ""


def test_update_project_settings_request_full_body_with_github_repo():
    """#859: Request mit allen relevanten Feldern + github_repo."""
    req = UpdateProjectSettingsRequest(
        name="Test Project",
        description="A test project",
        provider="anthropic",
        model="claude-sonnet-4-6",
        temperature=0.7,
        max_tokens=8192,
        members=["alice", "bob"],
        execution_mode="safe",
        max_tool_rounds=100,
        risk_policy="interactive",
        github_repo="alice/my-project",
    )
    assert req.github_repo == "alice/my-project"
    assert req.name == "Test Project"
    assert req.members == ["alice", "bob"]