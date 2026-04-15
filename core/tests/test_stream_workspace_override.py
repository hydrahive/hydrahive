"""Tests für workspace_override im Streaming-Endpoint (#663).

Zwei Schichten:
- **HTTP-Integration** via FastAPI TestClient: Validation-Status-Codes,
  Stream-Content-Durchreichung.
- **Unit auf Generator-Lifecycle**: ContextVar set/reset-Reihenfolge inkl.
  Client-Abbruch nach Chunk-Konsum.

Der Unit-Test repliziert das im Route-Handler verwendete Generator-
Pattern 1:1 — er testet die Python-Mechanik (try/finally, ContextVar-
Task-Lokalität, GeneratorExit-Propagation) unabhängig von FastAPI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from hydrahive_core import tool_registry as tr
from hydrahive_core.router_agent_chat import (
    _validate_workspace_override,
    register_agent_chat_routes,
)
from hydrahive_core.router_core_misc import IncomingMessage, WorkspaceOverride


# ── Fixtures ─────────────────────────────────────────────────────────────────

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
    """Echter Worktree + Meta für Validation-Happy-Path."""
    projects = tmp_path / "projects"
    projects.mkdir()
    repo = projects / "p_parent"
    _init_repo(repo)
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path / "wt"))

    from hydrahive_core.subagent_worktrees import create_worktree
    meta = create_worktree(
        base_repo=str(repo),
        parent_project_id="p_parent",
        parent_agent_id="boss",
        sub_agent_id="sub",
        task_id="task1",
    )
    return meta


# ── FastAPI-App mit Stream-Route + Mock-Deps ─────────────────────────────────

class _FakeOrchestrator:
    """handle_message_stream emittiert den aktuell aktiven Override als
    chunk, damit Tests beobachten können, ob die ContextVar greift.
    """
    def __init__(self):
        self.raise_exc: Exception | None = None

    async def handle_message_stream(self, **kw):
        from hydrahive_core.tool_registry import workspace_root
        path = workspace_root("probe")
        yield f"ws={path}\n".encode()
        if self.raise_exc is not None:
            raise self.raise_exc
        yield b"done\n"


def _build_app(auth_user: str = "admin"):
    app = FastAPI()
    auth_router = APIRouter()
    orch = _FakeOrchestrator()

    discovery = MagicMock()
    _cfg = MagicMock()
    _cfg.identity = "Test"
    discovery.get = lambda _id: _cfg

    def _fake_auth():
        return (auth_user, "admin" if auth_user in ("internal", "admin") else "user")

    def _fake_exec_mode(*a, **kw):
        return None

    # resolve_request_execution_mode ist im Module importiert — monkeypatchen
    # wir auf Modul-Ebene nicht, sondern nutzen eine Audit-Mock, die sich nicht
    # beschwert.
    audit_log = MagicMock()

    register_agent_chat_routes(
        app, auth_router,
        require_auth=_fake_auth,
        require_auth_or_localhost=_fake_auth,
        check_message_rate=lambda *a, **kw: None,
        discovery=discovery,
        agent_sessions=MagicMock(),
        agent_orchestrator=orch,
        agents_dir="/tmp/agents",
        audit_log=audit_log,
        logger=logging.getLogger("test"),
        incoming_message_model=IncomingMessage,
        group_service=None,
    )
    app.include_router(auth_router)
    return app, orch


# ── 1. External auth + workspace_override → 400 ──────────────────────────────

def test_external_auth_with_override_rejected(valid_meta):
    app, _ = _build_app(auth_user="till")
    client = TestClient(app)
    body = {
        "content": "hi",
        "workspace_override": {
            "path": valid_meta.worktree_path,
            "worktree_id": valid_meta.worktree_id,
            "parent_project_id": valid_meta.parent_project_id,
        },
    }
    r = client.post("/agents/target/message/stream", json=body)
    assert r.status_code == 400
    assert "requires internal auth" in r.json()["detail"]


# ── 2. Invalid worktree_id format → 400 ──────────────────────────────────────

def test_internal_invalid_worktree_id_format():
    app, _ = _build_app(auth_user="internal")
    client = TestClient(app)
    body = {
        "content": "hi",
        "workspace_override": {
            "path": "/var/lib/hydrahive/worktrees/trees/wt-x",
            "worktree_id": "not-a-wt-id",
            "parent_project_id": "p",
        },
    }
    r = client.post("/agents/target/message/stream", json=body)
    assert r.status_code == 400
    assert "invalid worktree_id format" in r.json()["detail"]


# ── 3. Full validation greift: path mismatch → 400 ───────────────────────────

def test_internal_path_mismatch_rejected(valid_meta, tmp_path):
    # Ein alternativer Pfad, der zwar unter worktrees_dir/trees liegen müsste,
    # aber eben NICHT gleich meta.worktree_path ist.
    app, _ = _build_app(auth_user="internal")
    client = TestClient(app)

    # Pfad existiert unter worktrees_dir/trees (zweiter Worktree, nur Dir)
    alt_dir = Path(valid_meta.worktree_path).parent / "wt-20260101T000000Z-x-bbbbbbbb"
    alt_dir.mkdir()

    body = {
        "content": "hi",
        "workspace_override": {
            "path": str(alt_dir),
            "worktree_id": valid_meta.worktree_id,
            "parent_project_id": valid_meta.parent_project_id,
        },
    }
    r = client.post("/agents/target/message/stream", json=body)
    assert r.status_code == 400
    assert "path does not match" in r.json()["detail"]


def test_internal_parent_project_mismatch_rejected(valid_meta):
    app, _ = _build_app(auth_user="internal")
    client = TestClient(app)
    body = {
        "content": "hi",
        "workspace_override": {
            "path": valid_meta.worktree_path,
            "worktree_id": valid_meta.worktree_id,
            "parent_project_id": "different_parent",
        },
    }
    r = client.post("/agents/target/message/stream", json=body)
    assert r.status_code == 400
    assert "parent_project_id mismatch" in r.json()["detail"]


# ── 4. Happy path: Stream läuft, Override aktiv während Generator ────────────

def test_stream_runs_with_override_active(valid_meta):
    app, orch = _build_app(auth_user="internal")
    client = TestClient(app)
    body = {
        "content": "hi",
        "workspace_override": {
            "path": valid_meta.worktree_path,
            "worktree_id": valid_meta.worktree_id,
            "parent_project_id": valid_meta.parent_project_id,
        },
    }
    with client.stream("POST", "/agents/target/message/stream", json=body) as r:
        assert r.status_code == 200
        content = b"".join(r.iter_bytes())
    # Die erste Chunk enthält workspace_root("probe") = override-path
    assert valid_meta.worktree_path.encode() in content
    assert b"done" in content


# ── 5. Stream ohne workspace_override: unverändert, Override nie gesetzt ─────

def test_stream_without_override_unchanged():
    app, orch = _build_app(auth_user="admin")
    client = TestClient(app)
    body = {"content": "hi"}
    with client.stream("POST", "/agents/target/message/stream", json=body) as r:
        assert r.status_code == 200
        content = b"".join(r.iter_bytes())
    # workspace_root liefert Default /projects/probe — nicht den override-path
    assert b"ws=/projects/probe" in content or b"/projects/probe" in content
    assert b"done" in content


# ── 6. External ohne override: baseline 200 (Regression) ─────────────────────

def test_external_without_override_ok():
    app, _ = _build_app(auth_user="alice")
    client = TestClient(app)
    body = {"content": "hi"}
    with client.stream("POST", "/agents/target/message/stream", json=body) as r:
        assert r.status_code == 200


# ── 7–9. Unit: Generator-Lifecycle + Client-Abbruch + Exception ──────────────

async def _run_generator_pattern(validated_path: Path | None, *, raise_after: int | None = None,
                                  client_close_after: int | None = None,
                                  set_spy: list, reset_spy: list):
    """Repliziert den Route-Handler-Generator 1:1 — getrennt testbar."""

    orig_set = tr.set_workspace_override
    orig_reset = tr.reset_workspace_override

    def _set(path):
        tok = orig_set(path)
        set_spy.append((path, tok))
        return tok

    def _reset(tok):
        reset_spy.append(tok)
        orig_reset(tok)

    async def _mock_stream():
        from hydrahive_core.tool_registry import workspace_root
        for i in range(10):
            yield f"chunk {i} ws={workspace_root('probe')}\n".encode()
            if raise_after is not None and i >= raise_after:
                raise RuntimeError("boom")

    async def event_stream():
        _tok = _set(validated_path) if validated_path is not None else None
        try:
            async for chunk in _mock_stream():
                yield chunk
        finally:
            if _tok is not None:
                _reset(_tok)

    # Konsum
    gen = event_stream()
    collected: list[bytes] = []
    try:
        i = 0
        async for c in gen:
            collected.append(c)
            i += 1
            if client_close_after is not None and i >= client_close_after:
                await gen.aclose()
                break
    except RuntimeError:
        pass
    return collected


async def test_generator_lifecycle_normal(tmp_path):
    override = tmp_path / "override_path"
    override.mkdir()
    set_spy: list = []
    reset_spy: list = []
    chunks = await _run_generator_pattern(override, set_spy=set_spy, reset_spy=reset_spy)
    # Chunks enthalten override-path
    assert any(str(override).encode() in c for c in chunks)
    # set und reset je genau 1x
    assert len(set_spy) == 1
    assert len(reset_spy) == 1
    # reset kam mit demselben Token
    assert set_spy[0][1] is reset_spy[0]


async def test_generator_lifecycle_exception(tmp_path):
    """handle_message_stream wirft → reset muss trotzdem laufen."""
    override = tmp_path / "override_path"
    override.mkdir()
    set_spy: list = []
    reset_spy: list = []
    await _run_generator_pattern(override, raise_after=2, set_spy=set_spy, reset_spy=reset_spy)
    assert len(set_spy) == 1
    assert len(reset_spy) == 1


async def test_generator_lifecycle_client_abort_after_chunk(tmp_path):
    """Client bricht nach erstem Chunk ab (aclose) — set lief, reset läuft trotzdem."""
    override = tmp_path / "override_path"
    override.mkdir()
    set_spy: list = []
    reset_spy: list = []
    chunks = await _run_generator_pattern(
        override, client_close_after=1,
        set_spy=set_spy, reset_spy=reset_spy,
    )
    # Mindestens ein Chunk konsumiert (damit set() sicher gelaufen war)
    assert len(chunks) >= 1
    # aclose hat GeneratorExit geschickt → finally lief
    assert len(set_spy) == 1
    assert len(reset_spy) == 1


async def test_generator_no_override_no_set(tmp_path):
    """Ohne validated_path: weder set noch reset aufgerufen."""
    set_spy: list = []
    reset_spy: list = []
    chunks = await _run_generator_pattern(None, set_spy=set_spy, reset_spy=reset_spy)
    assert len(chunks) == 10
    assert set_spy == []
    assert reset_spy == []


# ── 10. Task-Lokalität: Override leakt nicht aus Generator ───────────────────

async def test_override_does_not_leak_to_other_task(tmp_path):
    """
    Nach Abschluss des Generator-Tasks darf der globale ContextVar-Zustand
    im aktuellen (Test-)Task-Kontext nicht die Generator-Override zeigen.
    Da ContextVars task-lokal sind, sieht der Test-Task den Override
    ohnehin nie — wir verifizieren, dass workspace_root() hier Default bleibt.
    """
    override = tmp_path / "override_path"
    override.mkdir()
    set_spy: list = []
    reset_spy: list = []
    await _run_generator_pattern(override, set_spy=set_spy, reset_spy=reset_spy)
    # Der Test-Task hat niemals set() gemacht
    from hydrahive_core.tool_registry import _current_workspace_override, workspace_root
    assert _current_workspace_override() is None
    assert workspace_root("whatever") != override
