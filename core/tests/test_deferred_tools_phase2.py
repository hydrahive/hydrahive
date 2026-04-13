"""
test_deferred_tools_phase2.py — ToolSearch + Session-State + Prompt-Block (#620)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.tool_registry import (
    BaseTool, ToolRegistry, ToolSearchTool, registry,
    mark_tool_loaded, is_tool_loaded, loaded_deferred_ids,
    clear_loaded_deferred, session_key, render_deferred_tools_block,
    _loaded_deferred,
)


# =========================================================================
# Test-Fixtures
# =========================================================================

class _FakeGiteaTool(BaseTool):
    @property
    def id(self) -> str: return "_fake_gitea_create_issue"
    @property
    def name(self) -> str: return "Gitea Issue anlegen"
    @property
    def description(self) -> str: return "Erstellt ein Issue im Gitea-Repo."
    @property
    def parameters(self) -> dict: return {"type": "object", "properties": {}}
    @property
    def always_loaded(self) -> bool: return False
    @property
    def category(self) -> str: return "gitea"
    @property
    def semantic_tags(self) -> list[str]: return ["gitea", "issue", "repo"]
    async def execute(self, agent_id: str, project_id: str, **kwargs):
        return {"ok": True}


class _FakeWebFetch(BaseTool):
    @property
    def id(self) -> str: return "_fake_web_fetch"
    @property
    def name(self) -> str: return "Web Fetch"
    @property
    def description(self) -> str: return "Holt eine URL und gibt Markdown zurück."
    @property
    def parameters(self) -> dict: return {"type": "object", "properties": {}}
    @property
    def always_loaded(self) -> bool: return False
    @property
    def semantic_tags(self) -> list[str]: return ["web", "fetch", "http", "url"]
    async def execute(self, agent_id: str, project_id: str, **kwargs):
        return {"markdown": "# ok"}


@pytest.fixture(autouse=True)
def _register_deferred_fakes():
    """Registriert Fakes in der globalen Registry, räumt nach Test auf."""
    gitea = _FakeGiteaTool()
    web = _FakeWebFetch()
    registry.register(gitea)
    registry.register(web)
    _loaded_deferred.clear()
    yield
    registry._tools.pop(gitea.id, None)
    registry._tools.pop(web.id, None)
    _loaded_deferred.clear()


# =========================================================================
# Session-State
# =========================================================================

def test_session_key_stable():
    assert session_key("proj_a", "agent_x") == "proj_a::agent_x"


def test_mark_and_check_loaded():
    sk = session_key("p", "a")
    assert not is_tool_loaded(sk, "_fake_gitea_create_issue")
    mark_tool_loaded(sk, "_fake_gitea_create_issue")
    assert is_tool_loaded(sk, "_fake_gitea_create_issue")


def test_always_loaded_is_loaded_without_marking():
    sk = session_key("p", "a")
    # shell_exec ist always_loaded=True → ohne Markierung als geladen
    assert is_tool_loaded(sk, "shell_exec")


def test_clear_wipes_session():
    sk = session_key("p", "a")
    mark_tool_loaded(sk, "_fake_gitea_create_issue")
    clear_loaded_deferred(sk)
    assert not is_tool_loaded(sk, "_fake_gitea_create_issue")


def test_sessions_isolated():
    sk1 = session_key("p1", "a")
    sk2 = session_key("p2", "a")
    mark_tool_loaded(sk1, "_fake_gitea_create_issue")
    assert is_tool_loaded(sk1, "_fake_gitea_create_issue")
    assert not is_tool_loaded(sk2, "_fake_gitea_create_issue")


# =========================================================================
# Prompt-Block
# =========================================================================

def test_render_block_contains_deferred_tools():
    block = render_deferred_tools_block()
    assert "_fake_gitea_create_issue" in block
    assert "_fake_web_fetch" in block
    assert "ToolSearch" in block


def test_render_block_sorted_by_id():
    block = render_deferred_tools_block()
    # _fake_gitea kommt vor _fake_web alphabetisch
    gitea_pos = block.find("_fake_gitea_create_issue")
    web_pos = block.find("_fake_web_fetch")
    assert 0 < gitea_pos < web_pos


def test_render_block_omits_always_loaded():
    block = render_deferred_tools_block()
    assert "shell_exec" not in block
    assert "file_read" not in block


# =========================================================================
# ToolSearch
# =========================================================================

@pytest.mark.asyncio
async def test_tool_search_select_syntax():
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="a", project_id="p",
        query="select:_fake_gitea_create_issue",
    )
    assert "_fake_gitea_create_issue" in res["loaded"]
    assert len(res["schemas"]) == 1
    assert res["schemas"][0]["function"]["name"] == "_fake_gitea_create_issue"
    # Side-Effect: geladen
    assert is_tool_loaded(session_key("p", "a"), "_fake_gitea_create_issue")


@pytest.mark.asyncio
async def test_tool_search_select_multiple():
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="a", project_id="p",
        query="select:_fake_gitea_create_issue,_fake_web_fetch",
    )
    assert set(res["loaded"]) == {"_fake_gitea_create_issue", "_fake_web_fetch"}


@pytest.mark.asyncio
async def test_tool_search_keyword_match_by_tag():
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="a", project_id="p",
        query="gitea issue",
    )
    assert "_fake_gitea_create_issue" in res["loaded"]
    # Side-Effect
    assert is_tool_loaded(session_key("p", "a"), "_fake_gitea_create_issue")


@pytest.mark.asyncio
async def test_tool_search_keyword_match_by_description():
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="a", project_id="p",
        query="markdown",
    )
    # Description enthält "Markdown"
    assert "_fake_web_fetch" in res["loaded"]


@pytest.mark.asyncio
async def test_tool_search_no_match_returns_hint():
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="a", project_id="p",
        query="völligunpassenderblödsinnxyz",
    )
    assert res["matches"] == []
    assert "select:" in res["message"]


@pytest.mark.asyncio
async def test_tool_search_select_rejects_always_loaded():
    """select:shell_exec darf kein Laden auslösen (schon always_loaded)."""
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="a", project_id="p",
        query="select:shell_exec",
    )
    assert res["matches"] == []


@pytest.mark.asyncio
async def test_tool_search_respects_max_results():
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="a", project_id="p",
        query="fake",  # matched beide Fakes in description/id
        max_results=1,
    )
    assert len(res["loaded"]) == 1
