"""
test_orchestrator_tools.py — Tests für orchestrator_tools.py

Testet standalone-Hilfsfunktionen:
- _truncate_tool_result: typ-abhängige Kürzung
- _tool_call_signature: Fingerprint-Berechnung
- _execute_tool: Tool-Ausführung inkl. None-Tool-Handling
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_tools import (
    DispatchResult,
    _truncate_tool_result,
    _tool_call_signature,
    _execute_tool,
    execute_tool_call,
)


# ================================================================= DispatchResult

class TestDispatchResult:

    def test_default_success(self):
        r = DispatchResult(worker_id="w1", task="task", result="ok")
        assert r.success is True
        assert r.error is None

    def test_fehler_fall(self):
        r = DispatchResult(worker_id="w1", task="task", result="", success=False, error="Timeout")
        assert r.success is False
        assert r.error == "Timeout"


# ================================================================= _truncate_tool_result

class TestTruncateToolResult:

    def test_kurze_ausgabe_unveraendert(self):
        assert _truncate_tool_result("hello") == "hello"

    def test_json_blob_bei_limit_gekuerzt(self):
        # 35000 Zeichen: über 32k Safety-Cap → wird gekürzt
        long_json = '{"data": "' + "x" * 35000 + '"}'
        result = _truncate_tool_result(long_json)
        assert len(result) < len(long_json)
        assert "gekürzt" in result

    def test_diff_bekommt_mehr_platz(self):
        diff = "diff --git a/foo b/foo\n" + "+" * 10000
        result = _truncate_tool_result(diff)
        assert len(result) > 8000  # Limit 12000 für Diffs

    def test_diff_bei_limit_gekuerzt(self):
        diff = "diff --git a/foo b/foo\n" + "+" * 35000
        result = _truncate_tool_result(diff)
        assert "gekürzt" in result

    def test_repo_tree_bei_limit_gekuerzt(self):
        repo_tree = '[{"path": "src/foo.py"}, ' + '{"path": "x"},' * 3000 + "]"
        result = _truncate_tool_result(repo_tree)
        assert len(result) <= 33000  # 32000 + Marker
        assert "gekürzt" in result

    def test_patch_format_erkennt_diff(self):
        patch = "--- a/file\n+++ b/file\n@@ -1 +1 @@\n" + "+" * 7000
        result = _truncate_tool_result(patch)
        assert len(result) > 4000  # als Diff erkannt


# ================================================================= _tool_call_signature

class TestToolCallSignature:

    def _make_tc(self, name: str, arguments: str = "{}"):
        tc = MagicMock()
        tc.function.name = name
        tc.function.arguments = arguments
        return tc

    def test_einzelner_tool_call(self):
        sig = _tool_call_signature([self._make_tc("read_file", '{"path":"x"}')])
        assert sig == ('read_file:{"path":"x"}',)

    def test_mehrere_tool_calls(self):
        sig = _tool_call_signature([
            self._make_tc("read_file", '{"path":"a"}'),
            self._make_tc("write_file", '{"path":"b"}'),
        ])
        assert len(sig) == 2
        assert "read_file" in sig[0]
        assert "write_file" in sig[1]

    def test_gleiche_calls_gleiche_signatur(self):
        tcs = [self._make_tc("foo", '{"x":1}')]
        assert _tool_call_signature(tcs) == _tool_call_signature(tcs)

    def test_unterschiedliche_calls_unterschiedliche_signatur(self):
        tc1 = [self._make_tc("foo", '{"x":1}')]
        tc2 = [self._make_tc("bar", '{"x":1}')]
        assert _tool_call_signature(tc1) != _tool_call_signature(tc2)

    def test_leere_liste(self):
        assert _tool_call_signature([]) == ()


# ================================================================= _execute_tool

class TestExecuteTool:

    def _make_boss(self, agent_id="boss"):
        cfg = MagicMock()
        cfg.id = agent_id
        return cfg

    async def test_tool_none_gibt_fehler_dict(self):
        result = await _execute_tool(
            None,
            boss_cfg=self._make_boss(),
            project_id="proj",
            tool_name="missing_tool",
        )
        assert "error" in result
        assert "missing_tool" in result["error"]

    async def test_tool_wird_aufgerufen(self):
        tool = MagicMock()
        tool.execute = AsyncMock(return_value={"ok": True})
        result = await _execute_tool(
            tool,
            boss_cfg=self._make_boss(),
            project_id="proj",
            tool_name="my_tool",
            tool_input={"key": "value"},
        )
        assert result == {"ok": True}
        # _execution_mode wird automatisch übergeben
        call_kwargs = tool.execute.call_args[1]
        assert call_kwargs["agent_id"] == "boss"
        assert call_kwargs["project_id"] == "proj"
        assert call_kwargs["key"] == "value"

    async def test_request_user_wird_an_kwargs_tools_weitergereicht(self):
        tool = MagicMock()
        tool.execute = AsyncMock(return_value={"ok": True})
        await _execute_tool(
            tool,
            boss_cfg=self._make_boss(),
            project_id="proj",
            tool_name="my_tool",
            tool_input={},
            request_user="alice",
        )
        call_kwargs = tool.execute.call_args.kwargs
        assert call_kwargs["_request_user"] == "alice"

    async def test_internal_request_user_wird_nicht_weitergereicht(self):
        tool = MagicMock()
        tool.execute = AsyncMock(return_value={"ok": True})
        await _execute_tool(
            tool,
            boss_cfg=self._make_boss(),
            project_id="proj",
            tool_name="my_tool",
            tool_input={},
            request_user="internal",
        )
        call_kwargs = tool.execute.call_args.kwargs
        assert "_request_user" not in call_kwargs

    async def test_project_id_im_input_wird_ignoriert(self):
        tool = MagicMock()
        tool.execute = AsyncMock(return_value={})
        await _execute_tool(
            tool,
            boss_cfg=self._make_boss(),
            project_id="default-proj",
            tool_name="t",
            tool_input={"project_id": "override-proj"},
        )
        call_kwargs = tool.execute.call_args.kwargs
        assert call_kwargs["project_id"] == "default-proj"

    async def test_leeres_tool_input(self):
        tool = MagicMock()
        tool.execute = AsyncMock(return_value={"done": True})
        result = await _execute_tool(
            tool,
            boss_cfg=self._make_boss(),
            project_id="proj",
            tool_name="t",
            tool_input=None,
        )
        assert result == {"done": True}


class TestExecuteToolCall:

    def _make_boss(self):
        cfg = MagicMock()
        cfg.id = "boss"
        cfg.mcp_servers = []
        cfg.risk_policy = "trusted"
        return cfg

    async def test_request_user_wird_an_orchestrator_execute_tool_weitergereicht(self):
        tool = MagicMock()
        orch = MagicMock()
        orch._resolve_allowed_tool.return_value = tool
        orch._execute_tool = AsyncMock(return_value={"ok": True})
        orch._sessions.get_active.return_value = None

        result, is_error = await execute_tool_call(
            orch,
            boss_cfg=self._make_boss(),
            project_id="proj",
            tool_name="shell_exec",
            tool_input={"command": "uname -a"},
            request_user="alice",
        )

        assert result == {"ok": True}
        assert is_error is False
        assert orch._execute_tool.await_args.kwargs["request_user"] == "alice"
