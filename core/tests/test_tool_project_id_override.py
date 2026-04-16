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


# ─────────────────────────────────────────────────── ask_agent passthrough


@pytest.fixture
def capture_ask_agent_tool():
    """Fake ask_agent-Tool, das _requested_project_id sichtbar macht."""
    captured = {}

    class _FakeAskAgent:
        id = "ask_agent"
        async def execute(self, agent_id, project_id, **kwargs):
            captured["runtime_project_id"] = project_id
            captured["kwargs"] = dict(kwargs)
            return {"answer": "ok"}

    return _FakeAskAgent(), captured


class TestAskAgentProjectIdPassthrough:

    async def test_ask_agent_gets_requested_project_id(self, capture_ask_agent_tool):
        """#669: ask_agent soll Tool-Input project_id als _requested_project_id
        im kwargs bekommen, damit es die Ziel-Session setzen kann."""
        tool, captured = capture_ask_agent_tool
        await _execute_tool(
            tool,
            boss_cfg=_make_boss_cfg(),
            project_id="project-a",
            tool_name="ask_agent",
            tool_input={
                "target": "helper",
                "question": "Hilf mir",
                "project_id": "target-session",
            },
            execution_mode="safe",
        )
        # Runtime-project_id bleibt unverandert
        assert captured["runtime_project_id"] == "project-a"
        # Der gewünschte Ziel-Session-Wert landet als _requested_project_id
        assert captured["kwargs"].get("_requested_project_id") == "target-session"
        # project_id darf nicht doppelt in kwargs stehen
        assert "project_id" not in captured["kwargs"]

    async def test_ask_agent_gets_requested_project_id_even_when_equal_to_runtime_personal_project(
        self, capture_ask_agent_tool
    ):
        """#669 Edge-Case: Tool-Input project_id == Runtime-project_id bei personal_*.
        Ohne Fix: Bedingung _maybe != runtime ist False → kein _requested_project_id.
        AskAgentTool würde wegen personal_*-Branch in UUID-Session-Fallback fallen.
        Mit Fix: expliziter Tool-Input wird immer weitergereicht."""
        tool, captured = capture_ask_agent_tool
        boss = _make_boss_cfg()
        boss.id = "personal_till"
        await _execute_tool(
            tool,
            boss_cfg=boss,
            project_id="personal_till",
            tool_name="ask_agent",
            tool_input={
                "target": "helper",
                "question": "Hilf mir",
                "project_id": "personal_till",
            },
            execution_mode="safe",
        )
        assert captured["runtime_project_id"] == "personal_till"
        assert captured["kwargs"].get("_requested_project_id") == "personal_till"
        assert "project_id" not in captured["kwargs"]

    async def test_ask_agent_no_override_when_no_input_project_id(self, capture_ask_agent_tool):
        """Wenn kein project_id im Tool-Input: _requested_project_id fehlt (normal)."""
        tool, captured = capture_ask_agent_tool
        await _execute_tool(
            tool,
            boss_cfg=_make_boss_cfg(),
            project_id="project-a",
            tool_name="ask_agent",
            tool_input={"target": "helper", "question": "Hilf mir"},
            execution_mode="safe",
        )
        assert captured["runtime_project_id"] == "project-a"
        assert "_requested_project_id" not in captured["kwargs"]

    async def test_target_tool_still_rejects_override(self, capture_tool):
        """Regression: server_shell ignoriert project_id aus Tool-Input weiterhin."""
        tool, captured = capture_tool
        # tool_name = "spy" via capture_tool, simuliert aber das Verhalten eines
        # Target-Tools — wir testen explizit mit einem Target-Tool-Namen.
        class _FakeTargetTool:
            id = "server_shell"
            async def execute(self, agent_id, project_id, **kwargs):
                captured["project_id"] = project_id
                captured["kwargs"] = kwargs
                return {"stdout": "", "stderr": "", "exit_code": 0}

        await _execute_tool(
            _FakeTargetTool(),
            boss_cfg=_make_boss_cfg(),
            project_id="project-a",
            tool_name="server_shell",
            tool_input={"project_id": "project-b", "server_id": "prod-web", "command": "id"},
            execution_mode="safe",
        )
        # Runtime-Wert bleibt autoritativ
        assert captured["project_id"] == "project-a"
        # _requested_project_id darf für Target-Tools NICHT gesetzt sein
        assert "_requested_project_id" not in captured["kwargs"]
        # project_id darf nicht doppelt in kwargs stehen
        assert "project_id" not in captured["kwargs"]
