"""Tests für ask_agent Worktree-Isolation (#662) — Tool-Seite + ContextVar."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=False,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@e.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "init"], repo)


@pytest.fixture
def parent_repo(tmp_path, monkeypatch):
    """Fake /projects Layout: projects_root/<project_id>/ = git-Repo."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_id = "p_parent"
    repo_dir = projects_root / project_id
    _init_repo(repo_dir)

    # PROJECTS_ROOT im tool_registry ist aus settings.projects_dir geladen.
    # Monkeypatch die Modul-Level-Variable direkt.
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr, "PROJECTS_ROOT", projects_root)

    # Worktrees-Dir ebenfalls in tmp_path.
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path / "wt"))

    return project_id, repo_dir, tmp_path / "wt"


# ── ContextVar-Smoke ─────────────────────────────────────────────────────────

def test_contextvar_override_then_reset(tmp_path):
    from hydrahive_core.tool_registry import (
        reset_workspace_override,
        set_workspace_override,
        workspace_root,
    )
    default = workspace_root("any_project")
    token = set_workspace_override(tmp_path / "override")
    try:
        assert workspace_root("any_project") == tmp_path / "override"
    finally:
        reset_workspace_override(token)
    assert workspace_root("any_project") == default


async def test_contextvar_is_async_task_local(tmp_path):
    from hydrahive_core.tool_registry import (
        reset_workspace_override,
        set_workspace_override,
        workspace_root,
    )
    results: dict[str, Path] = {}

    async def _task(name: str, override: Path):
        tok = set_workspace_override(override)
        try:
            await asyncio.sleep(0.01)
            results[name] = workspace_root("x")
        finally:
            reset_workspace_override(tok)

    await asyncio.gather(
        _task("a", tmp_path / "A"),
        _task("b", tmp_path / "B"),
    )
    assert results["a"] == tmp_path / "A"
    assert results["b"] == tmp_path / "B"


# ── Fake aiohttp.ClientSession ───────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._payload


class _FakeSession:
    """Minimaler aiohttp.ClientSession-Stub. Speichert letzten POST-Body."""
    last_body: dict | None = None
    response_status = 200
    response_payload = {"response": "OK from sub"}

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    def post(self, url, json=None, headers=None, timeout=None):
        _FakeSession.last_body = json
        return _FakeResponse(_FakeSession.response_status, _FakeSession.response_payload)


@pytest.fixture
def fake_aiohttp(monkeypatch):
    _FakeSession.last_body = None
    _FakeSession.response_status = 200
    _FakeSession.response_payload = {"response": "OK from sub"}
    import aiohttp as _aio
    monkeypatch.setattr(_aio, "ClientSession", _FakeSession)
    monkeypatch.setattr(_aio, "ClientTimeout", lambda total=None: None)
    return _FakeSession


# ── Isolation OFF: Legacy unverändert ────────────────────────────────────────

async def test_legacy_off_no_worktree(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr

    monkeypatch.setattr(tr.settings, "worktree_isolation", False, raising=False)
    project_id, _, worktrees_root = parent_repo

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id,
        target="sub", question="q",
    )
    assert result.get("success") is True
    # Body NICHT mit workspace_override
    assert fake_aiohttp.last_body is not None
    assert "workspace_override" not in fake_aiohttp.last_body
    # Keine Worktrees erzeugt
    trees_dir = worktrees_root / "trees"
    assert not trees_dir.exists() or not any(trees_dir.iterdir())
    # result hat keine worktree_meta
    assert "worktree_meta" not in result
    assert "worktree_skipped" not in result


# ── Isolation ON + Git: Worktree erzeugt, Body hat override, release nach Lauf ─

async def test_isolation_on_git_creates_worktree(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr

    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, repo_dir, worktrees_root = parent_repo

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id,
        target="sub", question="do thing",
    )
    assert result.get("success") is True

    # Body hat workspace_override
    body = fake_aiohttp.last_body
    assert "workspace_override" in body
    ov = body["workspace_override"]
    assert ov["worktree_id"].startswith("wt-")
    assert ov["parent_project_id"] == project_id

    # Meta wurde persistiert, status nach Lauf = released
    from hydrahive_core.subagent_worktrees import get_worktree
    meta = get_worktree(ov["worktree_id"])
    assert meta.parent_project_id == project_id
    assert meta.parent_agent_id == "boss"
    assert meta.sub_agent_id == "sub"
    assert meta.isolation_mode == "full_worktree"
    assert meta.status == "released"
    assert meta.released_at is not None

    # scope_report im Result
    assert "worktree_meta" in result
    assert "scope_report" in result["worktree_meta"]
    assert result["worktree_meta"]["scope_report"]["ok"] is True  # clean worktree


# ── Parent-Workspace bleibt unverändert ──────────────────────────────────────

async def test_parent_workspace_untouched(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, repo_dir, _ = parent_repo

    before = (repo_dir / "README.md").read_text()
    before_head = _git(["rev-parse", "HEAD"], repo_dir).stdout.strip()

    tool = tr.AskAgentTool()
    await tool.execute(agent_id="boss", project_id=project_id, target="sub", question="q")

    assert (repo_dir / "README.md").read_text() == before
    assert _git(["rev-parse", "HEAD"], repo_dir).stdout.strip() == before_head


# ── Non-Git Parent: Fallback ─────────────────────────────────────────────────

async def test_non_git_parent_fallback(tmp_path, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_id = "p_non_git"
    (projects_root / project_id).mkdir()
    monkeypatch.setattr(tr, "PROJECTS_ROOT", projects_root)
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path / "wt"))

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
    )
    # HTTP ging durch
    assert fake_aiohttp.last_body is not None
    assert "workspace_override" not in fake_aiohttp.last_body
    # Result hat Marker
    assert result.get("worktree_skipped") == "non_git_repo"


# ── create_worktree Fehler: fail-closed ──────────────────────────────────────

async def test_create_worktree_error_fails_closed(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, repo_dir, _ = parent_repo

    # Dirty-Repo + kein allow_dirty-Override → create_worktree wirft
    (repo_dir / "README.md").write_text("dirty\n", encoding="utf-8")

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
    )
    assert result.get("error", "").startswith("worktree setup failed")
    assert result.get("worktree_skipped") == "error"
    # KEIN HTTP-Call
    assert fake_aiohttp.last_body is None


# ── HTTP-Fehler: Worktree wird trotzdem released ─────────────────────────────

async def test_http_error_still_releases_worktree(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    fake_aiohttp.response_status = 404
    fake_aiohttp.response_payload = {}

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
    )
    assert "error" in result
    # Body wurde gesendet, Override war drin
    assert fake_aiohttp.last_body is not None
    assert "workspace_override" in fake_aiohttp.last_body

    ov = fake_aiohttp.last_body["workspace_override"]
    from hydrahive_core.subagent_worktrees import get_worktree
    meta = get_worktree(ov["worktree_id"])
    assert meta.status == "released"


# ── Scope-Report wird erzeugt ────────────────────────────────────────────────

async def test_scope_report_in_result(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
    )
    assert "worktree_meta" in result
    report = result["worktree_meta"]["scope_report"]
    assert "ok" in report
    assert "violations_count" in report
    assert "allowed_files" in report
    assert "denied_files" in report
    assert "out_of_scope_files" in report
