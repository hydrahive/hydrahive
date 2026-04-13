"""
test_deferred_tools_phase4.py — MCP als deferred (#620 Phase 4)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import tools_git, tools_gitea  # noqa: F401  register deferred tools
from hydrahive_core.tool_registry import (
    ToolSearchTool, set_current_mcp_entries, get_current_mcp_entries,
    clear_current_mcp_entries, render_deferred_tools_block,
    loaded_deferred_ids, session_key, is_tool_loaded, _loaded_deferred,
)
from hydrahive_core.orchestrator_mcp import filter_mcp_schemas_by_loaded


@pytest.fixture(autouse=True)
def _reset_state():
    _loaded_deferred.clear()
    yield
    _loaded_deferred.clear()
    for aid in ["agt_phase4_a", "agt_phase4_b"]:
        clear_current_mcp_entries(aid)


# =========================================================================
# MCP-Entries-State
# =========================================================================

def test_set_get_mcp_entries():
    entries = [("mcp_github_create_issue", "Creates a GitHub issue"),
               ("mcp_github_list_pr", "Lists PRs")]
    set_current_mcp_entries("agt_phase4_a", entries)
    # via skey
    got = get_current_mcp_entries(session_key("proj", "agt_phase4_a"))
    assert len(got) == 2
    assert ("mcp_github_create_issue", "Creates a GitHub issue") in got


def test_mcp_entries_isolated_per_agent():
    set_current_mcp_entries("agt_phase4_a", [("mcp_a_x", "x")])
    set_current_mcp_entries("agt_phase4_b", [("mcp_b_y", "y")])
    assert get_current_mcp_entries(session_key("p", "agt_phase4_a")) == [("mcp_a_x", "x")]
    assert get_current_mcp_entries(session_key("p", "agt_phase4_b")) == [("mcp_b_y", "y")]


# =========================================================================
# Prompt-Block mit MCP
# =========================================================================

def test_render_block_shows_mcp_section():
    mcp = [("mcp_github_create_issue", "Create GH issue"),
           ("mcp_slack_send", "Send Slack")]
    block = render_deferred_tools_block(mcp_entries=mcp)
    assert "MCP-Tools" in block
    assert "mcp_github_create_issue" in block
    assert "mcp_slack_send" in block
    assert "[MCP] Create GH issue" in block


def test_render_block_empty_when_nothing():
    _saved = dict(_loaded_deferred)
    _loaded_deferred.clear()
    # Note: registry kann immer noch deferred Tools haben (Gitea, Git)
    # → Block ist nicht leer, aber enthält kein MCP
    block = render_deferred_tools_block(mcp_entries=[])
    assert "MCP-Tools" not in block
    _loaded_deferred.update(_saved)


# =========================================================================
# ToolSearch mit MCP
# =========================================================================

@pytest.mark.asyncio
async def test_tool_search_select_mcp_tool():
    set_current_mcp_entries("agt_phase4_a", [
        ("mcp_github_create_issue", "Creates GitHub issue"),
    ])
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="agt_phase4_a", project_id="proj",
        query="select:mcp_github_create_issue",
    )
    assert "mcp_github_create_issue" in res.get("mcp_loaded", [])
    # Side-Effect: geladen
    assert is_tool_loaded(session_key("proj", "agt_phase4_a"), "mcp_github_create_issue")


@pytest.mark.asyncio
async def test_tool_search_keyword_finds_mcp():
    set_current_mcp_entries("agt_phase4_a", [
        ("mcp_github_create_issue", "Creates GitHub issue"),
        ("mcp_slack_send_message", "Send Slack message"),
    ])
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="agt_phase4_a", project_id="proj",
        query="slack",
    )
    assert "mcp_slack_send_message" in res.get("loaded", [])


@pytest.mark.asyncio
async def test_tool_search_mixed_local_and_mcp():
    """Gleichzeitige Treffer — lokal + MCP — werden beide geladen."""
    set_current_mcp_entries("agt_phase4_a", [
        ("mcp_custom_git_push", "MCP git push"),
    ])
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="agt_phase4_a", project_id="proj",
        query="git push",
        max_results=5,
    )
    loaded = res.get("loaded", [])
    # Sowohl lokales git_push als auch mcp_custom_git_push erwartet
    assert "git_push" in loaded
    assert "mcp_custom_git_push" in loaded


@pytest.mark.asyncio
async def test_tool_search_select_unknown_mcp_returns_error():
    set_current_mcp_entries("agt_phase4_a", [])
    tool = ToolSearchTool()
    res = await tool.execute(
        agent_id="agt_phase4_a", project_id="proj",
        query="select:mcp_nonexistent",
    )
    assert res.get("matches") == []


# =========================================================================
# filter_mcp_schemas_by_loaded
# =========================================================================

def test_filter_mcp_schemas():
    schemas = [
        {"type": "function", "function": {"name": "mcp_a_x"}},
        {"type": "function", "function": {"name": "mcp_b_y"}},
        {"type": "function", "function": {"name": "mcp_c_z"}},
    ]
    filtered = filter_mcp_schemas_by_loaded(schemas, {"mcp_a_x", "mcp_c_z"})
    assert {s["function"]["name"] for s in filtered} == {"mcp_a_x", "mcp_c_z"}


def test_filter_empty_loaded_returns_nothing():
    schemas = [{"type": "function", "function": {"name": "mcp_a_x"}}]
    assert filter_mcp_schemas_by_loaded(schemas, set()) == []


def test_filter_handles_missing_function_key():
    schemas = [
        {"type": "function"},  # no function key
        {"type": "function", "function": {}},  # no name
        {"type": "function", "function": {"name": "mcp_valid"}},
    ]
    assert filter_mcp_schemas_by_loaded(schemas, {"mcp_valid"}) == [schemas[2]]
