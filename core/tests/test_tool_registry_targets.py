"""
test_tool_registry_targets.py — #584-C Registry-Integration + Consistency.

Prüft, dass die Tool-Namen konsistent sind zwischen:
- tool_registry.registry (Klassen registriert)
- orchestrator._V2_CORE_TOOL_IDS (Core-Whitelist)
- context_lifecycle._TOOL_OP_TYPES / _TOOL_POLICIES (Policy-Engine)
- permission_classifier (Risk-Assessment)
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.tool_registry import registry
from hydrahive_core.context_lifecycle import _TOOL_OP_TYPES, _TOOL_POLICIES
from hydrahive_core.orchestrator import Orchestrator


TARGET_TOOL_IDS = [
    "server_shell",
    "server_file_read",
    "server_file_write",
    "wks_shell_exec",
]


class TestRegistryIntegration:

    @pytest.mark.parametrize("tool_id", TARGET_TOOL_IDS)
    def test_tool_registered(self, tool_id):
        tool = registry.get(tool_id)
        assert tool is not None, f"Tool {tool_id!r} nicht in registry"
        assert tool.id == tool_id
        assert tool.description  # nicht leer
        assert "properties" in tool.parameters

    @pytest.mark.parametrize("tool_id", TARGET_TOOL_IDS)
    def test_tool_in_v2_core_whitelist(self, tool_id):
        assert tool_id in Orchestrator._V2_CORE_TOOL_IDS

    @pytest.mark.parametrize("tool_id", TARGET_TOOL_IDS)
    def test_tool_policy_exists(self, tool_id):
        """context_lifecycle muss eine Policy kennen, sonst fällt Tool auf
        Default-Unknown-Path zurück."""
        assert tool_id in _TOOL_POLICIES, f"Policy für {tool_id} fehlt"

    def test_server_shell_destructive(self):
        assert registry.get("server_shell").is_destructive is True

    def test_server_file_read_read_only(self):
        assert registry.get("server_file_read").is_read_only is True

    def test_server_file_write_destructive(self):
        assert registry.get("server_file_write").is_destructive is True

    def test_wks_shell_exec_destructive(self):
        assert registry.get("wks_shell_exec").is_destructive is True

    def test_all_target_tools_have_server_id_or_username_param(self):
        """Schemas müssen eine Zuweisungsreferenz verlangen — sonst kann
        Target-Resolve nicht greifen."""
        assert "server_id" in registry.get("server_shell").parameters["properties"]
        assert "server_id" in registry.get("server_file_read").parameters["properties"]
        assert "server_id" in registry.get("server_file_write").parameters["properties"]
        # wks_shell_exec hat username optional (Default bei 1 zugewiesener WKS)
        assert "username" in registry.get("wks_shell_exec").parameters["properties"]

    def test_no_target_tool_requests_project_id_in_schema(self):
        """Kritisch: project_id darf NICHT als Input-Parameter im Schema stehen.
        Der Agent darf es nicht selbst setzen (#584-C-Security-Fix)."""
        for tool_id in TARGET_TOOL_IDS:
            props = registry.get(tool_id).parameters["properties"]
            assert "project_id" not in props, (
                f"{tool_id}: project_id darf nicht Teil des Input-Schemas sein"
            )
