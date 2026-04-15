"""Tests für IsolationMode-Enforcement im Tool-Dispatch (#664)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hydrahive_core import tool_registry as tr
from hydrahive_core.orchestrator_tools import execute_tool_call
from hydrahive_core.tool_registry import (
    WorkspaceRuntimeContext,
    current_workspace_context,
    reset_workspace_override,
    set_workspace_override,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_ctx():
    """Defensive: Task-ContextVar kann von Vortests nicht leaken (neue Task),
    aber wir resetten sicherheitshalber.
    """
    yield
    # Nichts zu tun — jede Test-Coroutine ist eigener Task.


def _mock_orch():
    orch = MagicMock()
    orch._execute_tool = AsyncMock(return_value={"ok": True})
    orch._resolve_allowed_tool = MagicMock(return_value=MagicMock(name="tool"))
    sess = MagicMock(); sess.id = "s1"
    orch._sessions = MagicMock()
    orch._sessions.get_active = MagicMock(return_value=sess)
    return orch


def _boss_cfg():
    c = MagicMock()
    c.mcp_servers = None
    c.id = "boss1"
    c.risk_policy = "trusted"  # überspringt CONFIRM-Round-Trip
    return c


async def _call_with_ctx(ctx: WorkspaceRuntimeContext | None, *, tool_name: str = "file_read"):
    """Setzt optional Context und ruft execute_tool_call."""
    orch = _mock_orch()
    token = None
    if ctx is not None:
        token = set_workspace_override(ctx)
    try:
        result, is_error = await execute_tool_call(
            orch, boss_cfg=_boss_cfg(), project_id="p1",
            tool_name=tool_name, tool_input={},
        )
    finally:
        if token is not None:
            reset_workspace_override(token)
    return orch, result, is_error


# ── 1–3. Keine Verhaltensänderung für normale Agents ────────────────────────

async def test_no_context_no_enforcement():
    orch, result, is_error = await _call_with_ctx(None, tool_name="file_write")
    assert is_error is False
    orch._execute_tool.assert_called_once()


async def test_context_without_mode_no_enforcement(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, worktree_id=None, isolation_mode=None)
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="file_write")
    assert is_error is False
    orch._execute_tool.assert_called_once()


async def test_backward_compat_set_override_with_path(tmp_path):
    """set_workspace_override(Path) bleibt funktional — kein Enforcement."""
    orch = _mock_orch()
    token = set_workspace_override(tmp_path)
    try:
        ctx = current_workspace_context()
        assert isinstance(ctx, WorkspaceRuntimeContext)
        assert ctx.path == tmp_path
        assert ctx.isolation_mode is None
        result, is_error = await execute_tool_call(
            orch, boss_cfg=_boss_cfg(), project_id="p1",
            tool_name="file_write", tool_input={},
        )
        assert is_error is False
        orch._execute_tool.assert_called_once()
    finally:
        reset_workspace_override(token)


# ── 4–9. Mode-Matrix ────────────────────────────────────────────────────────

async def test_read_only_allows_file_read(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="read_only")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="file_read")
    assert is_error is False
    orch._execute_tool.assert_called_once()


async def test_read_only_blocks_file_write(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="read_only")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="file_write")
    assert is_error is True
    assert result["risk"] == "isolation_block"
    assert result["isolation_mode"] == "read_only"
    assert result["tool_name"] == "file_write"
    orch._execute_tool.assert_not_called()


async def test_read_only_blocks_shell_exec(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="read_only")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="shell_exec")
    assert is_error is True
    assert result["risk"] == "isolation_block"
    orch._execute_tool.assert_not_called()


async def test_read_only_blocks_git_push(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="read_only")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="git_push")
    assert is_error is True
    assert result["risk"] == "isolation_block"


async def test_patch_only_blocks_file_write(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="patch_only")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="file_write")
    assert is_error is True
    assert result["isolation_mode"] == "patch_only"
    orch._execute_tool.assert_not_called()


async def test_patch_only_allows_file_read(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="patch_only")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="file_read")
    assert is_error is False
    orch._execute_tool.assert_called_once()


# ── 10–11. full_worktree: schreibt, aber kein Push ──────────────────────────

async def test_full_worktree_allows_file_write(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="full_worktree")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="file_write")
    assert is_error is False
    orch._execute_tool.assert_called_once()


async def test_full_worktree_blocks_git_push(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="full_worktree")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="git_push")
    assert is_error is True
    assert result["risk"] == "isolation_block"
    assert result["isolation_mode"] == "full_worktree"
    assert result["tool_name"] == "git_push"
    orch._execute_tool.assert_not_called()


# ── 12. Ungültiger Mode → fail-closed mit isolation_error ───────────────────

async def test_invalid_mode_fail_closed(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="yolo")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="file_read")
    assert is_error is True
    assert result["risk"] == "isolation_error"
    assert result["isolation_mode"] == "yolo"
    orch._execute_tool.assert_not_called()


# ── 13. Block-Result-Shape komplett ─────────────────────────────────────────

async def test_block_result_shape(tmp_path):
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="read_only")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="file_write")
    assert is_error is True
    for k in ("error", "risk", "isolation_mode", "tool_name", "reason", "hint"):
        assert k in result, f"missing key {k!r} in result: {result}"
    assert "isolation_block" == result["risk"]
    # Reason kommt aus allow_tool und enthält nur Mode/Category/Tool-Name,
    # keine Tool-Input-Echoes oder Pfade:
    assert "read_only" in result["reason"]
    assert "file_write" in result["reason"]


# ── 14. PreToolUse-Hook wird bei Isolation-Block NICHT aufgerufen ───────────

async def test_block_skips_pretooluse_hook(tmp_path, monkeypatch):
    """Isolation-Gate läuft vor PreToolUse — Admin-Hook sieht blockierte
    Tools nicht (built-in hard policy schützt Admin-Hooks vor ungültigem
    Kontext).
    """
    calls: list = []

    async def _fake_pre(*a, **kw):
        calls.append((a, kw))
        from hydrahive_core.hook_runtime import PreHookDecision
        return PreHookDecision(action="allow")

    monkeypatch.setattr("hydrahive_core.hook_runtime.run_pretool_hooks", _fake_pre)

    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="read_only")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="file_write")
    assert is_error is True
    assert result["risk"] == "isolation_block"
    assert len(calls) == 0, "PreToolUse-Hook darf bei Isolation-Block nicht laufen"


async def test_allow_still_runs_pretooluse_hook(tmp_path, monkeypatch):
    """Bei Isolation-Allow läuft die bestehende Pipeline weiter."""
    calls: list = []

    async def _fake_pre(*a, **kw):
        calls.append((a, kw))
        from hydrahive_core.hook_runtime import PreHookDecision
        return PreHookDecision(action="allow")

    monkeypatch.setattr("hydrahive_core.hook_runtime.run_pretool_hooks", _fake_pre)

    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="read_only")
    orch, result, is_error = await _call_with_ctx(ctx, tool_name="file_read")
    assert is_error is False
    # Hook wurde genau einmal gerufen
    assert len(calls) == 1


# ── 15. ContextVar set/reset Invariante ─────────────────────────────────────

async def test_context_set_and_reset(tmp_path):
    assert current_workspace_context() is None
    ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode="read_only")
    token = set_workspace_override(ctx)
    try:
        got = current_workspace_context()
        assert got is ctx
        assert got.isolation_mode == "read_only"
    finally:
        reset_workspace_override(token)
    assert current_workspace_context() is None


async def test_context_is_task_local(tmp_path):
    import asyncio as _asyncio
    results: dict[str, str | None] = {}

    async def _task(name: str, mode: str):
        ctx = WorkspaceRuntimeContext(path=tmp_path, isolation_mode=mode)
        tok = set_workspace_override(ctx)
        try:
            await _asyncio.sleep(0.01)
            c = current_workspace_context()
            results[name] = c.isolation_mode if c else None
        finally:
            reset_workspace_override(tok)

    await _asyncio.gather(
        _task("a", "read_only"),
        _task("b", "full_worktree"),
    )
    assert results["a"] == "read_only"
    assert results["b"] == "full_worktree"
