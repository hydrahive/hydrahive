import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from hydrahive_core.agent_config import AgentConfig
from hydrahive_core.orchestrator import Orchestrator
from hydrahive_core.orchestrator_context import build_system_prompt
from hydrahive_core.orchestrator_dispatch import _tool_loop


def _agent_cfg(agent_dir: Path) -> AgentConfig:
    return AgentConfig(
        id="test-agent",
        type="boss",
        identity="Test Agent",
        llm={"model": "test-model", "temperature": 0, "max_tokens": 256},
        max_tool_rounds=3,
        agent_dir=agent_dir,
    )


def _tool_response(path: str = "README.md"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="",
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            item_id="item_1",
                            function=SimpleNamespace(
                                name="file_read",
                                arguments=f'{{"path":"{path}"}}',
                            ),
                        )
                    ],
                )
            )
        ]
    )


@pytest.mark.asyncio
async def test_forced_abort_handoff_writer_persists_llm_summary(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    boss_cfg = _agent_cfg(agent_dir)
    llm_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Workspace: /projects/demo. Repo: demo. TODO: continue fixes."
                )
            )
        ]
    )
    orch = SimpleNamespace(_llm_call=mock.AsyncMock(return_value=llm_resp))

    ok = await Orchestrator._write_forced_abort_handoff(
        orch,
        boss_cfg,
        [{"role": "user", "content": "mach security fixes"}],
        reason="fuzzy_loop_abort",
        execution_mode="root",
    )

    handoff = agent_dir / "memory" / "_last_handoff.md"
    assert ok is True
    assert handoff.exists()
    text = handoff.read_text(encoding="utf-8")
    assert "fuzzy_loop_abort" in text
    assert "Workspace: /projects/demo" in text
    assert "verifizieren" in text
    orch._llm_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_system_prompt_injects_last_handoff(tmp_path):
    agent_dir = tmp_path / "agent"
    memory_dir = agent_dir / "memory"
    memory_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text("# Agent\n\nTest persona.", encoding="utf-8")
    (memory_dir / "_last_handoff.md").write_text(
        "Workspace: /projects/demo\nTODO: file_patch admin.php",
        encoding="utf-8",
    )
    boss_cfg = _agent_cfg(agent_dir)

    static_p, dynamic_p = await build_system_prompt(
        boss_cfg,
        "mach weiter",
        invalidate=True,
        session=None,
    )
    prompt = (static_p + "\n\n" + dynamic_p).strip() if dynamic_p else static_p

    assert "Forced-Abort-Handoff" in prompt
    assert "Workspace: /projects/demo" in prompt
    assert "Verifiziere Pfade, Repo und TODOs" in prompt


def test_clear_forced_abort_handoff_only_when_unchanged(tmp_path):
    agent_dir = tmp_path / "agent"
    memory_dir = agent_dir / "memory"
    memory_dir.mkdir(parents=True)
    handoff = memory_dir / "_last_handoff.md"
    handoff.write_text("old handoff", encoding="utf-8")
    boss_cfg = _agent_cfg(agent_dir)
    mtime = Orchestrator._forced_abort_handoff_mtime(boss_cfg)

    assert Orchestrator._clear_forced_abort_handoff_if_unchanged(boss_cfg, mtime) is True
    assert not handoff.exists()

    handoff.write_text("new handoff", encoding="utf-8")
    assert mtime is not None
    os.utime(handoff, (mtime + 10, mtime + 10))
    assert Orchestrator._clear_forced_abort_handoff_if_unchanged(boss_cfg, mtime) is False
    assert handoff.exists()


@pytest.mark.asyncio
async def test_tool_loop_writes_handoff_on_max_rounds(tmp_path):
    boss_cfg = _agent_cfg(tmp_path / "agent")
    boss_cfg.agent_dir.mkdir()
    response = _tool_response()
    orch = SimpleNamespace(
        _mcp_schemas_for_agent=mock.AsyncMock(return_value=[]),
        _allowed_tools=mock.Mock(return_value=[]),
        _allowed_tool_map=mock.Mock(return_value={}),
        _write_forced_abort_handoff=mock.AsyncMock(return_value=True),
        _finalize_tool_loop_response=mock.AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="final"))]
            )
        ),
    )

    result, _ = await _tool_loop(
        orch,
        boss_cfg,
        "project-demo",
        mock.MagicMock(),
        [{"role": "user", "content": "do work"}],
        response,
        max_rounds=1,
    )

    assert result == "final"
    orch._write_forced_abort_handoff.assert_awaited_once()
    assert orch._write_forced_abort_handoff.await_args.kwargs["reason"] == "max_rounds_hit:1"


@pytest.mark.asyncio
async def test_tool_loop_writes_handoff_on_signature_abort(tmp_path):
    boss_cfg = _agent_cfg(tmp_path / "agent")
    boss_cfg.agent_dir.mkdir()
    response = _tool_response()

    class FakeTool:
        parallel_safe = False

    orch = SimpleNamespace(
        _mcp_schemas_for_agent=mock.AsyncMock(return_value=[]),
        _allowed_tools=mock.Mock(return_value=[]),
        _allowed_tool_map=mock.Mock(return_value={}),
        _resolve_allowed_tool=mock.Mock(return_value=FakeTool()),
        _execute_tool=mock.AsyncMock(return_value={"ok": True}),
        _llm_call=mock.AsyncMock(return_value=response),
        _sessions=SimpleNamespace(
            get_active=mock.Mock(return_value=None),
            append=mock.AsyncMock(),
        ),
        _write_forced_abort_handoff=mock.AsyncMock(return_value=True),
        _finalize_tool_loop_response=mock.AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="final"))]
            )
        ),
    )

    result, _ = await _tool_loop(
        orch,
        boss_cfg,
        "project-demo",
        mock.MagicMock(),
        [{"role": "user", "content": "do work"}],
        response,
        max_rounds=5,
    )

    assert result == "final"
    orch._write_forced_abort_handoff.assert_awaited_once()
    assert orch._write_forced_abort_handoff.await_args.kwargs["reason"] == "signature_abort"
