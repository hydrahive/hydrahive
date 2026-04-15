"""Tests für OnTaskStart/OnTaskDone im Streaming-Endpoint (#661).

Deckt ab:
- Kein hook_runtime-Setup → Stream unverändert.
- OnTaskStart allow/block/runtime-exception.
- OnTaskDone bei normalem Ende, Stream-Exception, Client-aclose(), CancelledError.
- OnTaskDone-Runtime-Fehler sind non-blocking.
- task.kind == "agent_turn", context.streaming == True.
- Kombination mit workspace_override (#663): beide Lifecycles laufen sauber.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from hydrahive_core import tool_registry as tr
from hydrahive_core.hook_runtime import (
    PostHookReport,
    PreHookDecision,
    reload_hook_runtime,
)
from hydrahive_core.router_agent_chat import register_agent_chat_routes
from hydrahive_core.router_core_misc import IncomingMessage


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


# ── Fake-Orchestrator ────────────────────────────────────────────────────────

class _FakeOrchestrator:
    def __init__(self):
        self.called = 0
        self.raise_after: int | None = None
        self.raise_what: BaseException | None = None
        self.num_chunks = 3

    async def handle_message_stream(self, **kw):
        self.called += 1
        for i in range(self.num_chunks):
            if self.raise_after is not None and i >= self.raise_after:
                assert self.raise_what is not None
                raise self.raise_what
            yield f"data: {json.dumps({'type': 'text_delta', 'text': f'chunk{i}'})}\n\n".encode()
        yield b'data: {"done": true}\n\n'


# ── App-Builder ──────────────────────────────────────────────────────────────

def _build_app(auth_user: str = "admin"):
    app = FastAPI()
    auth_router = APIRouter()
    orch = _FakeOrchestrator()

    discovery = MagicMock()
    _cfg = MagicMock()
    _cfg.identity = "Test"
    discovery.get = lambda _id: _cfg

    def _fake_auth():
        role = "admin" if auth_user in ("internal", "admin") else "user"
        return (auth_user, role)

    register_agent_chat_routes(
        app, auth_router,
        require_auth=_fake_auth,
        require_auth_or_localhost=_fake_auth,
        check_message_rate=lambda *a, **kw: None,
        discovery=discovery,
        agent_sessions=MagicMock(),
        agent_orchestrator=orch,
        agents_dir="/tmp/agents",
        audit_log=MagicMock(),
        logger=logging.getLogger("test"),
        incoming_message_model=IncomingMessage,
        group_service=None,
    )
    app.include_router(auth_router)
    return app, orch


# ── Hook-Monkeypatch-Spies ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _ensure_hook_runtime_reloaded():
    reload_hook_runtime()
    yield
    reload_hook_runtime()


@pytest.fixture
def start_spy(monkeypatch):
    calls: list[dict] = []
    result = {"value": PreHookDecision(action="allow")}

    async def _fake_start(task, context=None):
        calls.append({"task": task, "context": context})
        v = result["value"]
        if isinstance(v, BaseException):
            raise v
        return v

    monkeypatch.setattr(
        "hydrahive_core.router_agent_chat.run_task_start_hooks",
        _fake_start,
        raising=False,
    )
    # Router importiert lazy aus hook_runtime — deshalb muss hook_runtime
    # selbst gemockt werden.
    monkeypatch.setattr(
        "hydrahive_core.hook_runtime.run_task_start_hooks",
        _fake_start,
    )
    return calls, result


@pytest.fixture
def done_spy(monkeypatch):
    calls: list[dict] = []
    behavior = {"value": PostHookReport()}

    async def _fake_done(task, result, context=None):
        calls.append({"task": task, "result": result, "context": context})
        v = behavior["value"]
        if isinstance(v, BaseException):
            raise v
        return v

    monkeypatch.setattr(
        "hydrahive_core.hook_runtime.run_task_done_hooks",
        _fake_done,
    )
    return calls, behavior


# ── 1. No hook_runtime-Setup → Stream unverändert ────────────────────────────

def test_stream_unchanged_without_hook_settings(monkeypatch, tmp_path):
    """Ohne settings.json liefern echte Hook-Funktionen allow/empty — Stream läuft."""
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(tmp_path / "no.json"))
    reload_hook_runtime()
    app, orch = _build_app(auth_user="admin")
    client = TestClient(app)
    with client.stream("POST", "/agents/target/message/stream", json={"content": "hi"}) as r:
        assert r.status_code == 200
        content = b"".join(r.iter_bytes())
    assert b"chunk0" in content
    assert b"done" in content
    assert orch.called == 1


# ── 2. OnTaskStart allow → Stream läuft, Orch aufgerufen ────────────────────

def test_start_allow_stream_runs(start_spy, done_spy):
    calls, result = start_spy
    result["value"] = PreHookDecision(action="allow")
    done_calls, _ = done_spy

    app, orch = _build_app(auth_user="admin")
    client = TestClient(app)
    with client.stream("POST", "/agents/target/message/stream", json={"content": "hello"}) as r:
        content = b"".join(r.iter_bytes())
    assert orch.called == 1
    assert b"chunk0" in content
    assert len(calls) == 1
    assert len(done_calls) == 1


# ── 3. OnTaskStart warn → Stream läuft ──────────────────────────────────────

def test_start_warn_stream_runs(start_spy, done_spy):
    _, result = start_spy
    result["value"] = PreHookDecision(action="allow", warnings=["careful"])

    app, orch = _build_app(auth_user="admin")
    client = TestClient(app)
    with client.stream("POST", "/agents/target/message/stream", json={"content": "hi"}) as r:
        content = b"".join(r.iter_bytes())
    assert orch.called == 1
    assert b"chunk0" in content


# ── 4. OnTaskStart block → Blockiert-Chunk, Orch NICHT aufgerufen ───────────

def test_start_block_stops_stream(start_spy, done_spy):
    _, result = start_spy
    result["value"] = PreHookDecision(action="block", message="no-go")
    done_calls, _ = done_spy

    app, orch = _build_app(auth_user="admin")
    client = TestClient(app)
    with client.stream("POST", "/agents/target/message/stream", json={"content": "hi"}) as r:
        content = b"".join(r.iter_bytes())

    assert orch.called == 0
    assert b"[Blockiert] no-go" in content
    assert b'"done": true' in content
    # Bei Block: OnTaskDone läuft NICHT (return vor Stream-try/finally)
    assert len(done_calls) == 0


# ── 5. OnTaskStart Runtime-Exception → fail-closed block ────────────────────

def test_start_runtime_exception_fail_closed(start_spy, done_spy):
    _, result = start_spy
    # Message enthält realistisch aussehenden Fake-Token → Redaction greift
    _fake_body = ("A" * 32) + "WXYZ"
    result["value"] = RuntimeError(f"start hook boom ghp_{_fake_body}")
    done_calls, _ = done_spy

    app, orch = _build_app(auth_user="admin")
    client = TestClient(app)
    with client.stream("POST", "/agents/target/message/stream", json={"content": "hi"}) as r:
        content = b"".join(r.iter_bytes())

    assert orch.called == 0
    assert b"[Blockiert] OnTaskStart-Hook-Runtime-Fehler" in content
    # Redaction: Klartext-Token darf nicht im Stream erscheinen
    assert _fake_body.encode() not in content
    assert len(done_calls) == 0


# ── 6. OnTaskDone bei normalem Ende ─────────────────────────────────────────

def test_done_on_normal_end(start_spy, done_spy):
    _, s_result = start_spy
    s_result["value"] = PreHookDecision(action="allow")
    done_calls, _ = done_spy

    app, orch = _build_app(auth_user="admin")
    orch.num_chunks = 4
    client = TestClient(app)
    with client.stream("POST", "/agents/target/message/stream", json={"content": "hi"}) as r:
        _ = b"".join(r.iter_bytes())

    assert len(done_calls) == 1
    result = done_calls[0]["result"]
    assert result["ok"] is True
    assert result["error"] is None
    assert result["disconnected"] is False
    assert "chunks" in result["summary"]
    # 4 text_delta + 1 done-chunk = 5
    assert result["summary"] == "5 chunks"


# ── 7. OnTaskDone bei Stream-Exception ──────────────────────────────────────

def test_done_on_stream_exception(start_spy, done_spy):
    _, s_result = start_spy
    s_result["value"] = PreHookDecision(action="allow")
    done_calls, _ = done_spy

    app, orch = _build_app(auth_user="admin")
    orch.raise_after = 2
    orch.raise_what = RuntimeError("stream broke")

    client = TestClient(app)
    # Exception wird aus Stream propagiert — TestClient wraps das.
    try:
        with client.stream("POST", "/agents/target/message/stream", json={"content": "hi"}) as r:
            _ = b"".join(r.iter_bytes())
    except Exception:
        pass

    assert len(done_calls) == 1
    result = done_calls[0]["result"]
    assert result["ok"] is False
    assert "stream broke" in (result["error"] or "")
    assert result["disconnected"] is False


# ── 8. OnTaskDone bei CancelledError → disconnected=True ────────────────────

async def test_done_on_cancelled_error_direct(monkeypatch):
    """Direkt auf dem Generator-Pattern testen: CancelledError → disconnected=True.

    TestClient kann kein CancelledError-Szenario simulieren. Wir replizieren
    das Router-Muster minimal und prüfen das done_state-Flag.
    """
    from hydrahive_core import hook_runtime as hr

    done_calls: list[dict] = []

    async def _fake_start(task, context=None):
        return PreHookDecision(action="allow")

    async def _fake_done(task, result, context=None):
        done_calls.append(result)
        return PostHookReport()

    monkeypatch.setattr(hr, "run_task_start_hooks", _fake_start)
    monkeypatch.setattr(hr, "run_task_done_hooks", _fake_done)

    # Mini-Replikat des Router-Generator-Pattern:
    async def fake_stream():
        yield b"chunk0"
        raise asyncio.CancelledError()

    async def event_stream_like():
        import time as _perf
        from hydrahive_core.hook_runtime import run_task_done_hooks, run_task_start_hooks
        task = {"kind": "agent_turn", "project_id": "x", "agent_id": "x",
                "user": "u", "session_id": "", "message_preview": "",
                "started_at": ""}
        decision = await run_task_start_hooks(task, context={"streaming": True})
        assert decision.action == "allow"

        started = _perf.monotonic()
        state = {"ok": True, "error": None, "disconnected": False, "chunks": 0}
        try:
            try:
                async for c in fake_stream():
                    state["chunks"] += 1
                    yield c
            except GeneratorExit:
                state["disconnected"] = True
                raise
            except asyncio.CancelledError:
                state["disconnected"] = True
                raise
            except Exception as exc:
                state["ok"] = False
                state["error"] = str(exc)
                raise
        finally:
            await run_task_done_hooks(task, {
                "ok": state["ok"], "duration_ms": int((_perf.monotonic()-started)*1000),
                "summary": f"{state['chunks']} chunks", "error": state["error"],
                "disconnected": state["disconnected"],
            }, context={"streaming": True})

    gen = event_stream_like()
    chunks: list = []
    try:
        async for c in gen:
            chunks.append(c)
    except asyncio.CancelledError:
        pass

    assert len(chunks) == 1
    assert len(done_calls) == 1
    assert done_calls[0]["disconnected"] is True
    assert done_calls[0]["ok"] is True, "CancelledError darf NICHT als ok=False erscheinen"
    assert done_calls[0]["error"] is None


# ── 9. GeneratorExit (aclose) → disconnected=True ────────────────────────────

async def test_done_on_generator_exit(monkeypatch):
    from hydrahive_core import hook_runtime as hr

    done_calls: list[dict] = []

    async def _fake_start(task, context=None):
        return PreHookDecision(action="allow")

    async def _fake_done(task, result, context=None):
        done_calls.append(result)
        return PostHookReport()

    monkeypatch.setattr(hr, "run_task_start_hooks", _fake_start)
    monkeypatch.setattr(hr, "run_task_done_hooks", _fake_done)

    async def fake_stream():
        for i in range(10):
            yield f"c{i}".encode()

    async def event_stream_like():
        import time as _perf
        from hydrahive_core.hook_runtime import run_task_done_hooks, run_task_start_hooks
        task = {"kind": "agent_turn", "project_id": "x", "agent_id": "x",
                "user": "u", "session_id": "", "message_preview": "",
                "started_at": ""}
        await run_task_start_hooks(task, context={"streaming": True})
        started = _perf.monotonic()
        state = {"ok": True, "error": None, "disconnected": False, "chunks": 0}
        try:
            try:
                async for c in fake_stream():
                    state["chunks"] += 1
                    yield c
            except GeneratorExit:
                state["disconnected"] = True
                raise
            except asyncio.CancelledError:
                state["disconnected"] = True
                raise
            except Exception as exc:
                state["ok"] = False
                state["error"] = str(exc)
                raise
        finally:
            await run_task_done_hooks(task, {
                "ok": state["ok"], "duration_ms": int((_perf.monotonic()-started)*1000),
                "summary": f"{state['chunks']} chunks", "error": state["error"],
                "disconnected": state["disconnected"],
            }, context={"streaming": True})

    gen = event_stream_like()
    consumed = []
    i = 0
    async for c in gen:
        consumed.append(c)
        i += 1
        if i >= 2:
            await gen.aclose()
            break

    assert len(consumed) >= 1
    assert len(done_calls) == 1
    assert done_calls[0]["disconnected"] is True
    assert done_calls[0]["ok"] is True


# ── 10. OnTaskDone-Runtime-Fehler bleibt non-blocking ───────────────────────

def test_done_runtime_error_non_blocking(start_spy, done_spy):
    _, s_result = start_spy
    s_result["value"] = PreHookDecision(action="allow")
    _, d_behavior = done_spy
    d_behavior["value"] = RuntimeError("done broke")

    app, orch = _build_app(auth_user="admin")
    client = TestClient(app)
    with client.stream("POST", "/agents/target/message/stream", json={"content": "hi"}) as r:
        content = b"".join(r.iter_bytes())

    # Stream hat trotzdem alle Chunks geliefert
    assert orch.called == 1
    assert b"chunk0" in content
    assert b"done" in content


# ── 11. task.kind == "agent_turn", context.streaming == True ────────────────

def test_task_kind_and_context(start_spy, done_spy):
    calls, s_result = start_spy
    s_result["value"] = PreHookDecision(action="allow")
    done_calls, _ = done_spy

    app, _ = _build_app(auth_user="admin")
    client = TestClient(app)
    with client.stream("POST", "/agents/target_agent/message/stream",
                       json={"content": "hello world"}) as r:
        _ = b"".join(r.iter_bytes())

    assert len(calls) == 1
    t = calls[0]["task"]
    assert t["kind"] == "agent_turn"
    assert t["agent_id"] == "target_agent"
    assert t["message_preview"] == "hello world"
    ctx = calls[0]["context"]
    assert ctx == {"streaming": True}

    # Auch im Done-Call dasselbe Context/Task
    assert done_calls[0]["task"]["kind"] == "agent_turn"
    assert done_calls[0]["context"] == {"streaming": True}


# ── 12. Kombination mit workspace_override (#663) ───────────────────────────

def test_combined_with_workspace_override(tmp_path, monkeypatch, start_spy, done_spy):
    # Worktree setup
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

    _, s_result = start_spy
    s_result["value"] = PreHookDecision(action="allow")
    done_calls, _ = done_spy

    # Fake-Orch emittiert workspace_root("probe") als Chunk
    app = FastAPI()
    auth_router = APIRouter()

    class _OrchWithWS:
        called = 0
        async def handle_message_stream(self, **kw):
            _OrchWithWS.called += 1
            from hydrahive_core.tool_registry import workspace_root
            _wp = str(workspace_root("probe"))
            _payload = {"type": "text_delta", "text": f"ws={_wp}"}
            yield f"data: {json.dumps(_payload)}\n\n".encode()
            yield b'data: {"done": true}\n\n'

    orch = _OrchWithWS()
    discovery = MagicMock(); _cfg = MagicMock(); _cfg.identity = "T"; discovery.get = lambda _id: _cfg

    register_agent_chat_routes(
        app, auth_router,
        require_auth=lambda: ("internal", "admin"),
        require_auth_or_localhost=lambda: ("internal", "admin"),
        check_message_rate=lambda *a, **kw: None,
        discovery=discovery, agent_sessions=MagicMock(), agent_orchestrator=orch,
        agents_dir="/tmp/a", audit_log=MagicMock(),
        logger=logging.getLogger("t"), incoming_message_model=IncomingMessage,
        group_service=None,
    )
    app.include_router(auth_router)

    client = TestClient(app)
    body = {
        "content": "hi",
        "workspace_override": {
            "path": meta.worktree_path,
            "worktree_id": meta.worktree_id,
            "parent_project_id": meta.parent_project_id,
        },
    }
    with client.stream("POST", "/agents/target/message/stream", json=body) as r:
        assert r.status_code == 200
        content = b"".join(r.iter_bytes())

    # workspace_override war während Stream aktiv
    assert meta.worktree_path.encode() in content
    # Task-Start + Task-Done beide gelaufen
    assert len(done_calls) == 1
    assert done_calls[0]["result"]["ok"] is True
    # Nach Stream-Ende: Override reset (Test-Task sieht default)
    from hydrahive_core.tool_registry import _current_workspace_override
    assert _current_workspace_override() is None


# ── 13. Block-Kombination: Override-Reset läuft trotz frühem Return ─────────

def test_block_still_resets_workspace_override(tmp_path, monkeypatch, start_spy):
    projects = tmp_path / "projects"; projects.mkdir()
    repo = projects / "p_parent"; _init_repo(repo)
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path / "wt"))

    from hydrahive_core.subagent_worktrees import create_worktree
    meta = create_worktree(
        base_repo=str(repo), parent_project_id="p_parent",
        parent_agent_id="boss", sub_agent_id="sub", task_id="tblock",
    )

    _, s_result = start_spy
    s_result["value"] = PreHookDecision(action="block", message="nein")

    # Spy auf reset_workspace_override
    reset_calls: list = []
    orig_reset = tr.reset_workspace_override
    def _spy_reset(token):
        reset_calls.append(token)
        orig_reset(token)
    monkeypatch.setattr(tr, "reset_workspace_override", _spy_reset)

    app = FastAPI(); auth_router = APIRouter()
    orch = _FakeOrchestrator()
    discovery = MagicMock(); _cfg = MagicMock(); _cfg.identity = "T"; discovery.get = lambda _id: _cfg
    register_agent_chat_routes(
        app, auth_router,
        require_auth=lambda: ("internal", "admin"),
        require_auth_or_localhost=lambda: ("internal", "admin"),
        check_message_rate=lambda *a, **kw: None,
        discovery=discovery, agent_sessions=MagicMock(), agent_orchestrator=orch,
        agents_dir="/tmp/a", audit_log=MagicMock(),
        logger=logging.getLogger("t"), incoming_message_model=IncomingMessage,
        group_service=None,
    )
    app.include_router(auth_router)

    client = TestClient(app)
    body = {
        "content": "hi",
        "workspace_override": {
            "path": meta.worktree_path,
            "worktree_id": meta.worktree_id,
            "parent_project_id": meta.parent_project_id,
        },
    }
    with client.stream("POST", "/agents/target/message/stream", json=body) as r:
        content = b"".join(r.iter_bytes())

    assert orch.called == 0
    assert b"[Blockiert] nein" in content
    # Reset lief trotz Block (outer-finally)
    assert len(reset_calls) == 1
