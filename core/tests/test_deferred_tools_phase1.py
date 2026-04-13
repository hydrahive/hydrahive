"""
test_deferred_tools_phase1.py — Deferred-Tools Phase 1 (#620)

Verifiziert nur das Daten-Modell: BaseTool-Properties + ToolRegistry-Methoden.
Kein Verhaltenswechsel — alle 9 Core-Tools sollen weiter always_loaded=True sein.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.tool_registry import BaseTool, ToolRegistry, registry


class _DummyDeferred(BaseTool):
    @property
    def id(self) -> str: return "_dummy_deferred"
    @property
    def name(self) -> str: return "Dummy"
    @property
    def description(self) -> str: return "Nur ein Test-Dummy.\nZweite Zeile."
    @property
    def parameters(self) -> dict: return {"type": "object", "properties": {}}
    @property
    def always_loaded(self) -> bool: return False
    @property
    def category(self) -> str: return "test"
    @property
    def semantic_tags(self) -> list[str]: return ["dummy", "test"]
    async def execute(self, agent_id: str, project_id: str, **kwargs):
        return "ok"


class _DummyAlways(BaseTool):
    @property
    def id(self) -> str: return "_dummy_always"
    @property
    def name(self) -> str: return "AlwaysDummy"
    @property
    def description(self) -> str: return "Always loaded dummy"
    @property
    def parameters(self) -> dict: return {"type": "object", "properties": {}}
    async def execute(self, agent_id: str, project_id: str, **kwargs):
        return "ok"


def test_base_defaults_backward_compatible():
    t = _DummyAlways()
    assert t.always_loaded is True
    assert t.category == "core"
    assert t.semantic_tags == []
    assert t.one_line == "Always loaded dummy"


def test_deferred_override_works():
    t = _DummyDeferred()
    assert t.always_loaded is False
    assert t.category == "test"
    assert "dummy" in t.semantic_tags


def test_one_line_strips_newlines():
    t = _DummyDeferred()
    assert t.one_line == "Nur ein Test-Dummy."
    assert "\n" not in t.one_line


def test_one_line_truncates_long():
    class Long(_DummyAlways):
        @property
        def description(self) -> str: return "x" * 200
    assert len(Long().one_line) == 120


def test_registry_partitions_correctly():
    reg = ToolRegistry()
    a = _DummyAlways()
    d = _DummyDeferred()
    reg.register(a)
    reg.register(d)

    assert {t.id for t in reg.always_loaded_tools()} == {"_dummy_always"}
    assert {t.id for t in reg.deferred_tools()} == {"_dummy_deferred"}
    assert {t.id for t in reg.all_tools()} == {"_dummy_always", "_dummy_deferred"}


def test_resolve_many_respects_aliases():
    reg = ToolRegistry()

    class _FR(_DummyAlways):
        @property
        def id(self) -> str: return "file_read"
    reg.register(_FR())

    # Alias read_file → file_read
    resolved = reg.resolve_many(["read_file", "nonexistent"])
    assert len(resolved) == 1
    assert resolved[0].id == "file_read"


def test_core_tools_all_always_loaded():
    """
    Regression: alle 9 Kern-Tools müssen always_loaded=True bleiben.
    Deadlock-Schutz — ohne shell/fs/memory kann ToolSearch selbst nichts bewirken.
    """
    core_ids = {
        "shell_exec", "file_read", "file_write", "file_patch",
        "file_search", "web_search", "read_memory", "write_memory",
        "ask_agent",
    }
    for tid in core_ids:
        t = registry.get(tid)
        if t is None:
            continue  # Tool evtl. nicht registriert in reiner Test-Env
        assert t.always_loaded is True, f"Core-Tool {tid} darf nicht deferred sein"
