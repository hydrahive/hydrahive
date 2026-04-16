"""
test_tool_project_id_override.py — #584-C Security-Fix für _execute_tool.

Regression-Test: Der Agent darf project_id NICHT via Tool-Input fälschen.
Vor dem Fix ließ `args.pop('project_id', None) or project_id` den LLM
einen fremden project_id übergeben und gegen dessen Targets resolven —
Auth-Bypass.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_tools import _execute_tool


@pytest.fixture
def capture_tool():
    """Fake-Tool, das aufzeichnet, mit welchem project_id es aufgerufen wurde."""
    captured = {}

    class _FakeTool:
        id = "spy"
        async def execute(self, agent_id, project_id, **kwargs):
            captured["agent_id"] = agent_id
            captured["project_id"] = project_id
            captured["kwargs"] = kwargs
            return {"stdout": "ok", "stderr": "", "exit_code": 0}

    return _FakeTool(), captured


def _make_boss_cfg():
    cfg = MagicMock()
    cfg.id = "projectA"
    cfg.effective_execution_mode = MagicMock(return_value="safe")
    return cfg


class TestProjectIdOverride:

    async def test_runtime_project_id_wins_over_tool_input(self, capture_tool):
        """Agent sendet {'project_id': 'projectB', ...} → Runtime 'projectA' bleibt."""
        tool, captured = capture_tool
        await _execute_tool(
            tool,
            boss_cfg=_make_boss_cfg(),
            project_id="projectA",
            tool_name="spy",
            tool_input={"project_id": "projectB", "command": "whoami"},
            execution_mode="safe",
        )
        assert captured["project_id"] == "projectA"
        # Der "command"-Parameter muss erhalten bleiben
        assert captured["kwargs"].get("command") == "whoami"
        # project_id darf NICHT in kwargs landen (sonst doppelt belegt)
        assert "project_id" not in captured["kwargs"]

    async def test_runtime_project_id_used_when_tool_input_empty(self, capture_tool):
        tool, captured = capture_tool
        await _execute_tool(
            tool,
            boss_cfg=_make_boss_cfg(),
            project_id="projectA",
            tool_name="spy",
            tool_input={"command": "x"},
            execution_mode="safe",
        )
        assert captured["project_id"] == "projectA"

    async def test_empty_runtime_project_id_stays_empty(self, capture_tool):
        """Wenn Runtime-pid leer ist (Legacy-Fall), darf Tool-Input auch
        keinen Override erzwingen."""
        tool, captured = capture_tool
        await _execute_tool(
            tool,
            boss_cfg=_make_boss_cfg(),
            project_id="",
            tool_name="spy",
            tool_input={"project_id": "projectX", "command": "x"},
            execution_mode="safe",
        )
        # Runtime gewinnt auch wenn leer
        assert captured["project_id"] == ""
