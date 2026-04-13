"""
test_deferred_tools_phase5.py — Metriken (#620 Phase 5)

Sicherstellen, dass:
  - ToolSearch-Aufrufe gezählt werden
  - Erfolgreich geladene deferred Tools in einer Set tracked werden
  - Deferred-Halluzinationen (Aufruf ohne Laden) erkannt werden
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.tool_registry import (
    BaseTool, ToolSearchTool, registry, _loaded_deferred,
)
from hydrahive_core.session_metrics import metrics


class _FakeGiteaTool(BaseTool):
    @property
    def id(self) -> str: return "_ph5_gitea"
    @property
    def name(self) -> str: return "fake gitea"
    @property
    def description(self) -> str: return "fake gitea tool."
    @property
    def parameters(self) -> dict: return {"type": "object", "properties": {}}
    @property
    def always_loaded(self) -> bool: return False
    @property
    def semantic_tags(self) -> list[str]: return ["gitea"]
    async def execute(self, agent_id: str, project_id: str, **kwargs):
        return {"ok": True}


@pytest.fixture(autouse=True)
def _setup():
    tool = _FakeGiteaTool()
    registry.register(tool)
    _loaded_deferred.clear()
    metrics.reset("ph5_proj")
    yield
    registry._tools.pop(tool.id, None)
    _loaded_deferred.clear()
    metrics.reset("ph5_proj")


@pytest.mark.asyncio
async def test_toolsearch_call_counter():
    ts = ToolSearchTool()
    await ts.execute(agent_id="a", project_id="ph5_proj", query="select:_ph5_gitea")
    await ts.execute(agent_id="a", project_id="ph5_proj", query="gitea")
    snap = metrics.snapshot("ph5_proj")
    assert snap["toolsearch_calls"] == 2


@pytest.mark.asyncio
async def test_deferred_loaded_tracked_unique():
    ts = ToolSearchTool()
    # Zweimal dasselbe Tool laden → nur 1 unique
    await ts.execute(agent_id="a", project_id="ph5_proj", query="select:_ph5_gitea")
    await ts.execute(agent_id="a", project_id="ph5_proj", query="select:_ph5_gitea")
    snap = metrics.snapshot("ph5_proj")
    assert snap["deferred_tools_loaded"] == ["_ph5_gitea"]
    assert snap["deferred_tools_loaded_count"] == 1


def test_hallucination_counter_and_rate():
    metrics.record_tool_round("ph5_proj", tool_call_count=4)  # 4 Calls
    metrics.record_deferred_hallucination("ph5_proj")
    metrics.record_deferred_hallucination("ph5_proj")
    snap = metrics.snapshot("ph5_proj")
    assert snap["deferred_hallucinations"] == 2
    assert snap["deferred_hallucination_rate"] == 0.5


def test_hallucination_rate_zero_without_tool_calls():
    snap = metrics.snapshot("ph5_proj")
    assert snap["deferred_hallucination_rate"] == 0.0
