"""Tests für Router-seitige workspace_override Validierung (#662)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from hydrahive_core.router_agent_chat import _validate_workspace_override
from hydrahive_core.router_core_misc import WorkspaceOverride


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
def valid_meta(tmp_path, monkeypatch):
    """Erzeugt einen echten Worktree + Meta und liefert dessen Override-Struct."""
    projects = tmp_path / "projects"
    projects.mkdir()
    repo = projects / "p_parent"
    _init_repo(repo)

    worktrees_dir = tmp_path / "wt"
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(worktrees_dir))

    from hydrahive_core.subagent_worktrees import create_worktree
    meta = create_worktree(
        base_repo=str(repo),
        parent_project_id="p_parent",
        parent_agent_id="boss",
        sub_agent_id="sub",
        task_id="task1",
    )
    return meta, worktrees_dir


# ── Happy path: internal + valid ─────────────────────────────────────────────

def test_valid_internal_passes(valid_meta):
    meta, _ = valid_meta
    ov = WorkspaceOverride(
        path=meta.worktree_path,
        worktree_id=meta.worktree_id,
        parent_project_id=meta.parent_project_id,
    )
    ctx = _validate_workspace_override(ov, auth_user="internal", auth_parent_project_id="p_parent")
    # #664: validator returniert WorkspaceRuntimeContext statt Path.
    from hydrahive_core.tool_registry import WorkspaceRuntimeContext
    assert isinstance(ctx, WorkspaceRuntimeContext)
    assert ctx.path == Path(meta.worktree_path).resolve()
    assert ctx.worktree_id == meta.worktree_id
    assert ctx.parent_project_id == meta.parent_project_id
    assert ctx.isolation_mode == meta.isolation_mode


# ── External auth → 400 ──────────────────────────────────────────────────────

def test_external_auth_rejected(valid_meta):
    meta, _ = valid_meta
    ov = WorkspaceOverride(
        path=meta.worktree_path,
        worktree_id=meta.worktree_id,
        parent_project_id=meta.parent_project_id,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(ov, auth_user="till")
    assert exc.value.status_code == 400
    assert "requires internal auth" in exc.value.detail


# ── Ungültiges worktree_id Regex ─────────────────────────────────────────────

def test_invalid_worktree_id_format():
    ov = WorkspaceOverride(path="/tmp/x", worktree_id="bad id", parent_project_id="p")
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(ov, auth_user="internal", auth_parent_project_id="p_parent")
    assert exc.value.status_code == 400
    assert "invalid worktree_id format" in exc.value.detail


def test_unknown_worktree_id(valid_meta):
    _, _ = valid_meta
    ov = WorkspaceOverride(
        path="/tmp/x", worktree_id="wt-20260101T000000Z-sub-aaaaaaaa",
        parent_project_id="p",
    )
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(ov, auth_user="internal", auth_parent_project_id="p_parent")
    assert exc.value.status_code == 400
    assert "unknown worktree_id" in exc.value.detail


# ── Path ≠ meta.worktree_path ────────────────────────────────────────────────

def test_path_mismatch(valid_meta, tmp_path):
    meta, _ = valid_meta
    # Legal path (existiert und unter worktrees_dir/trees), aber != meta.worktree_path
    other = Path(meta.worktree_path).parent / "wt-20260101T000000Z-x-bbbbbbbb"
    other.mkdir()
    ov = WorkspaceOverride(
        path=str(other),
        worktree_id=meta.worktree_id,
        parent_project_id=meta.parent_project_id,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(ov, auth_user="internal", auth_parent_project_id="p_parent")
    assert exc.value.status_code == 400
    assert "path does not match" in exc.value.detail


# ── parent_project_id mismatch ───────────────────────────────────────────────

def test_parent_project_mismatch(valid_meta):
    meta, _ = valid_meta
    ov = WorkspaceOverride(
        path=meta.worktree_path,
        worktree_id=meta.worktree_id,
        parent_project_id="different_parent",
    )
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(ov, auth_user="internal", auth_parent_project_id="p_parent")
    assert exc.value.status_code == 400
    assert "parent_project_id mismatch" in exc.value.detail


# ── Status != active → 400 ───────────────────────────────────────────────────

def test_released_status_rejected(valid_meta):
    meta, _ = valid_meta
    from hydrahive_core.subagent_worktrees import release_worktree
    release_worktree(meta.worktree_id)

    ov = WorkspaceOverride(
        path=meta.worktree_path,
        worktree_id=meta.worktree_id,
        parent_project_id=meta.parent_project_id,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(ov, auth_user="internal", auth_parent_project_id="p_parent")
    assert exc.value.status_code == 400
    assert "not active" in exc.value.detail


# ── Relativer Pfad → 400 ─────────────────────────────────────────────────────

def test_relative_path_rejected(valid_meta):
    meta, _ = valid_meta
    ov = WorkspaceOverride(
        path="relative/path",
        worktree_id=meta.worktree_id,
        parent_project_id=meta.parent_project_id,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(ov, auth_user="internal", auth_parent_project_id="p_parent")
    assert exc.value.status_code == 400
    assert "must be absolute" in exc.value.detail


# ── Prefix-Check: Pfad außerhalb worktrees_dir → 400 ─────────────────────────

def test_path_outside_worktrees_dir(valid_meta, tmp_path):
    meta, worktrees_dir = valid_meta
    # Legaler absoluter Pfad, aber nicht unter worktrees_dir/trees
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    # Baue eine Meta-Datei, die auf diesen Pfad zeigt (Meta-Bypass-Versuch):
    # get_worktree lädt via worktree_id; wir manipulieren Meta-JSON.
    import json
    meta_file = worktrees_dir / "meta" / f"{meta.worktree_id}.json"
    data = json.loads(meta_file.read_text())
    data["worktree_path"] = str(outside)
    meta_file.write_text(json.dumps(data))

    ov = WorkspaceOverride(
        path=str(outside),
        worktree_id=meta.worktree_id,
        parent_project_id=meta.parent_project_id,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(ov, auth_user="internal", auth_parent_project_id="p_parent")
    assert exc.value.status_code == 400
    assert "must be under" in exc.value.detail


# ── Pfad existiert nicht → 400 ───────────────────────────────────────────────

def test_nonexistent_path_rejected(valid_meta):
    meta, worktrees_dir = valid_meta
    # Lösche den Tree, aber setze Status NICHT released zurück
    import shutil
    shutil.rmtree(meta.worktree_path)

    ov = WorkspaceOverride(
        path=meta.worktree_path,
        worktree_id=meta.worktree_id,
        parent_project_id=meta.parent_project_id,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(ov, auth_user="internal", auth_parent_project_id="p_parent")
    assert exc.value.status_code == 400
    assert "not an existing directory" in exc.value.detail


# ── Pydantic-Model-Tests ─────────────────────────────────────────────────────

def test_incoming_message_accepts_workspace_override():
    from hydrahive_core.router_core_misc import IncomingMessage
    msg = IncomingMessage.model_validate({
        "content": "hi",
        "workspace_override": {
            "path": "/var/lib/hydrahive/worktrees/trees/wt-x",
            "worktree_id": "wt-20260101T000000Z-sub-aaaaaaaa",
            "parent_project_id": "p",
        },
    })
    assert msg.workspace_override is not None
    assert msg.workspace_override.worktree_id.startswith("wt-")


def test_incoming_message_default_none():
    from hydrahive_core.router_core_misc import IncomingMessage
    msg = IncomingMessage.model_validate({"content": "hi"})
    assert msg.workspace_override is None


# ── #774 Cross-Project Block (neuer Auth-Faktor via HMAC-ppid) ───────────────

def test_774_legacy_hmac_without_parent_project_rejected(valid_meta):
    """Legacy-HMAC (ohne X-Internal-Parent-Project Header) liefert leeren
    auth_parent_project_id. Ohne den Header kann der Server nicht beweisen
    aus welchem Projekt der Aufrufer kommt → workspace_override verboten.
    """
    meta, _ = valid_meta
    ov = WorkspaceOverride(
        path=meta.worktree_path,
        worktree_id=meta.worktree_id,
        parent_project_id=meta.parent_project_id,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(ov, auth_user="internal", auth_parent_project_id="")
    assert exc.value.status_code == 400
    assert "updated HMAC protocol" in exc.value.detail


def test_774_cross_project_block(valid_meta):
    """Angreifer mit Internal-Secret signiert HMAC ueber sein eigenes Projekt
    (attacker_project) und versucht einen Worktree aus p_parent zu aktivieren.
    Der Validator muss 403 werfen, weil meta.parent_project_id ('p_parent')
    != auth_parent_project_id ('attacker_project').
    """
    meta, _ = valid_meta
    ov = WorkspaceOverride(
        path=meta.worktree_path,
        worktree_id=meta.worktree_id,
        parent_project_id=meta.parent_project_id,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_workspace_override(
            ov, auth_user="internal", auth_parent_project_id="attacker_project",
        )
    assert exc.value.status_code == 403
    assert "not owned by calling project" in exc.value.detail


def test_774_same_project_allowed(valid_meta):
    """Positiv-Test: auth_parent_project_id == meta.parent_project_id → passt."""
    meta, _ = valid_meta
    ov = WorkspaceOverride(
        path=meta.worktree_path,
        worktree_id=meta.worktree_id,
        parent_project_id=meta.parent_project_id,
    )
    ctx = _validate_workspace_override(
        ov, auth_user="internal", auth_parent_project_id=meta.parent_project_id,
    )
    from hydrahive_core.tool_registry import WorkspaceRuntimeContext
    assert isinstance(ctx, WorkspaceRuntimeContext)
