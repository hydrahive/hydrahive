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


# ── #667: isolation_mode / write_scope konfigurierbar ───────────────────────

async def test_isolation_default_from_create_worktree(parent_repo, fake_aiohttp, monkeypatch):
    """Ohne explizite Args: create_worktree-Default (full_worktree) greift."""
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
    )
    assert result.get("success") is True
    wm = result["worktree_meta"]
    assert wm["isolation_mode"] == "full_worktree"
    assert wm["write_scope"] is None


@pytest.mark.parametrize("mode", ["read_only", "patch_only", "full_worktree"])
async def test_explicit_isolation_mode_persisted(parent_repo, fake_aiohttp, monkeypatch, mode):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
        isolation_mode=mode,
    )
    assert result.get("success") is True
    assert result["worktree_meta"]["isolation_mode"] == mode

    # Meta-Disk spiegelt den Wert
    from hydrahive_core.subagent_worktrees import get_worktree
    wid = fake_aiohttp.last_body["workspace_override"]["worktree_id"]
    meta = get_worktree(wid)
    assert meta.isolation_mode == mode


async def test_invalid_isolation_mode_rejected(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
        isolation_mode="yolo",
    )
    assert "error" in result
    assert result.get("worktree_skipped") == "invalid_args"
    assert result.get("field") == "isolation_mode"
    # Kein HTTP-Call
    assert fake_aiohttp.last_body is None


async def test_valid_write_scope_persisted(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    scope = {"allow": ["core/**"], "deny": ["**/*.env"], "description": "core-only"}
    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
        write_scope=scope,
    )
    assert result.get("success") is True
    assert result["worktree_meta"]["write_scope"] == scope

    # Disk-Persistenz
    from hydrahive_core.subagent_worktrees import get_worktree
    wid = fake_aiohttp.last_body["workspace_override"]["worktree_id"]
    meta = get_worktree(wid)
    assert meta.write_scope == scope


async def test_invalid_write_scope_rejected(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
        write_scope={"allow": ["/abs/path"]},  # absoluter Pfad → invalid
    )
    assert "error" in result
    assert result.get("worktree_skipped") == "invalid_args"
    assert result.get("field") == "write_scope"
    assert fake_aiohttp.last_body is None


async def test_non_git_default_args_legacy_fallback(tmp_path, fake_aiohttp, monkeypatch):
    """Non-Git + keine Args → Legacy-Fallback mit Marker."""
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_id = "p_non_git_default"
    (projects_root / project_id).mkdir()
    monkeypatch.setattr(tr, "PROJECTS_ROOT", projects_root)
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path / "wt"))

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
    )
    assert result.get("worktree_skipped") == "non_git_repo"
    # HTTP-Call ging durch
    assert fake_aiohttp.last_body is not None


@pytest.mark.parametrize("explicit_kwarg", [
    {"isolation_mode": "read_only"},
    {"write_scope": {"allow": ["core/**"]}},
    {"isolation_mode": "patch_only", "write_scope": {"deny": ["**/*.env"]}},
])
async def test_non_git_with_explicit_args_fail_closed(tmp_path, fake_aiohttp, monkeypatch, explicit_kwarg):
    """Non-Git + explizite Isolation-Args → fail-closed, kein HTTP."""
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_id = "p_non_git_strict"
    (projects_root / project_id).mkdir()
    monkeypatch.setattr(tr, "PROJECTS_ROOT", projects_root)
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path / "wt"))

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
        **explicit_kwarg,
    )
    assert "error" in result
    assert result.get("worktree_skipped") == "non_git_repo_but_isolation_requested"
    # KEIN HTTP-Call
    assert fake_aiohttp.last_body is None


async def test_isolation_off_ignores_explicit_args(parent_repo, fake_aiohttp, monkeypatch):
    """settings.worktree_isolation=False → Legacy-Pfad, explizite Args stumm ignoriert."""
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", False, raising=False)
    project_id, _, _ = parent_repo

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
        isolation_mode="read_only",
        write_scope={"allow": ["core/**"]},
    )
    assert result.get("success") is True
    # Legacy: kein Worktree, kein override im body
    assert "workspace_override" not in fake_aiohttp.last_body
    assert "worktree_meta" not in result


async def test_result_contains_isolation_and_scope_keys(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
    )
    wm = result["worktree_meta"]
    for k in ("worktree_id", "worktree_path", "isolation_mode", "write_scope", "scope_report"):
        assert k in wm, f"missing {k!r} in worktree_meta: {wm}"


# ── #665: Patch-Artefakt-Flow (nur patch_only) ──────────────────────────────

async def test_patch_only_extracts_valid_diff(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    # Fake-Sub-Agent-Response enthält fenced diff
    diff_text = (
        "Vorschlag:\n\n"
        "```diff\n"
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ @@\n"
        "-old\n"
        "+new\n"
        "```\n"
    )
    fake_aiohttp.response_payload = {"response": diff_text}

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="bitte patchen",
        isolation_mode="patch_only",
    )
    assert result.get("success") is True
    assert "patch_artifact" in result
    pa = result["patch_artifact"]
    assert pa["present"] is True
    assert pa["valid"] is True
    assert "README.md" in pa["paths"]
    assert pa["artifact_path"] is not None
    assert pa["bytes"] > 0


async def test_patch_only_no_diff_in_response(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    fake_aiohttp.response_payload = {"response": "Ich hätte Ideen, aber kein Patch."}

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="?",
        isolation_mode="patch_only",
    )
    assert "patch_artifact" in result
    pa = result["patch_artifact"]
    assert pa["present"] is False
    assert pa["valid"] is False
    assert pa["error"] == "no_diff_block_found"
    assert pa["artifact_path"] is None


async def test_read_only_no_patch_artifact_even_with_diff(parent_repo, fake_aiohttp, monkeypatch):
    """read_only + Response enthält diff block → kein patch_artifact-Key.
    Patch-Extraktion läuft ausschließlich bei patch_only.
    """
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    diff_text = (
        "```diff\n--- a/x\n+++ b/x\n@@ @@\n-old\n+new\n```\n"
    )
    fake_aiohttp.response_payload = {"response": diff_text}

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="lies",
        isolation_mode="read_only",
    )
    assert result.get("success") is True
    assert "patch_artifact" not in result


async def test_full_worktree_no_patch_artifact(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    diff_text = (
        "```diff\n--- a/x\n+++ b/x\n@@ @@\n```\n"
    )
    fake_aiohttp.response_payload = {"response": diff_text}

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="q",
        # kein isolation_mode → default full_worktree
    )
    assert result.get("success") is True
    assert "patch_artifact" not in result


async def test_patch_only_scope_violation(parent_repo, fake_aiohttp, monkeypatch):
    from hydrahive_core import tool_registry as tr
    monkeypatch.setattr(tr.settings, "worktree_isolation", True, raising=False)
    project_id, _, _ = parent_repo

    diff_text = (
        "```diff\n"
        "--- a/secrets.env\n"
        "+++ b/secrets.env\n"
        "@@ @@\n"
        "-A=1\n"
        "+A=2\n"
        "```\n"
    )
    fake_aiohttp.response_payload = {"response": diff_text}

    tool = tr.AskAgentTool()
    result = await tool.execute(
        agent_id="boss", project_id=project_id, target="sub", question="patch",
        isolation_mode="patch_only",
        write_scope={"allow": ["core/**"], "deny": ["**/*.env"]},
    )
    pa = result["patch_artifact"]
    assert pa["present"] is True
    assert pa["valid"] is False
    assert any(".env" in v and "deny" in v for v in pa["violations"])
