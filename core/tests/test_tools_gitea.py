"""
test_tools_gitea.py — Native Gitea-Tools als deferred (#619/#620 Phase 3)
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import tools_gitea  # side-effect: Registration
from hydrahive_core.tool_registry import registry


def test_all_gitea_tools_registered():
    ids = ["gitea_create_issue", "gitea_comment_issue",
           "gitea_list_issues", "gitea_get_issue", "gitea_close_issue"]
    for tid in ids:
        t = registry.get(tid)
        assert t is not None, f"{tid} nicht registriert"
        assert t.always_loaded is False, f"{tid} muss deferred sein"
        assert t.category == "gitea"


def test_gitea_tools_have_semantic_tags():
    for tid in ["gitea_create_issue", "gitea_list_issues", "gitea_close_issue"]:
        t = registry.get(tid)
        assert "gitea" in t.semantic_tags
        assert len(t.semantic_tags) >= 3


def test_read_only_flags_correct():
    assert registry.get("gitea_list_issues").is_read_only is True
    assert registry.get("gitea_get_issue").is_read_only is True
    assert registry.get("gitea_create_issue").is_read_only is False
    assert registry.get("gitea_close_issue").is_destructive is True


def test_tool_search_finds_gitea_by_keyword():
    """ToolSearch muss Gitea-Tools per Keyword finden."""
    from hydrahive_core.tool_registry import ToolSearchTool, clear_loaded_deferred, session_key
    import asyncio

    async def run():
        clear_loaded_deferred(session_key("p", "a"))
        tool = ToolSearchTool()
        res = await tool.execute(
            agent_id="a", project_id="p", query="gitea issue create",
        )
        return res

    res = asyncio.run(run())
    assert "gitea_create_issue" in res.get("loaded", [])


@pytest.mark.asyncio
async def test_create_issue_calls_client():
    """Smoke: Tool ruft client.create_issue_for_repo mit passenden Args."""
    from hydrahive_core.tools_gitea import GiteaCreateIssueTool

    mock_client = MagicMock()
    mock_client.create_issue_for_repo = AsyncMock(
        return_value={"number": 42, "html_url": "http://x/42", "title": "T"},
    )

    with patch("hydrahive_core.tools_gitea.get_gitea_client", return_value=mock_client), \
         patch("hydrahive_core.tools_gitea.resolve_repo_ref", return_value=("octopos", "myrepo")):
        tool = GiteaCreateIssueTool()
        res = await tool.execute(
            agent_id="a", project_id="p",
            repo="octopos/myrepo", title="T", body="B",
        )

    assert res == {"ok": True, "number": 42, "url": "http://x/42", "title": "T"}
    mock_client.create_issue_for_repo.assert_awaited_once_with(
        "octopos", "myrepo", "T", "B",
    )


@pytest.mark.asyncio
async def test_list_issues_returns_compact_rows():
    from hydrahive_core.tools_gitea import GiteaListIssuesTool

    mock_client = MagicMock()
    mock_client._get = AsyncMock(return_value=[
        {"number": 1, "title": "A", "state": "open", "html_url": "http://x/1"},
        {"number": 2, "title": "B", "state": "open", "html_url": "http://x/2"},
    ])

    with patch("hydrahive_core.tools_gitea.get_gitea_client", return_value=mock_client), \
         patch("hydrahive_core.tools_gitea.resolve_repo_ref", return_value=("o", "r")):
        tool = GiteaListIssuesTool()
        res = await tool.execute(agent_id="a", project_id="p", repo="o/r")

    assert res["ok"] is True
    assert res["count"] == 2
    assert res["issues"][0] == {"number": 1, "title": "A", "state": "open", "url": "http://x/1"}


@pytest.mark.asyncio
async def test_close_issue_with_comment_calls_both_endpoints():
    from hydrahive_core.tools_gitea import GiteaCloseIssueTool

    mock_client = MagicMock()
    mock_client.comment_issue_for_repo = AsyncMock(return_value={})
    mock_client.update_issue_for_repo = AsyncMock(
        return_value={"number": 5, "state": "closed"},
    )

    with patch("hydrahive_core.tools_gitea.get_gitea_client", return_value=mock_client), \
         patch("hydrahive_core.tools_gitea.resolve_repo_ref", return_value=("o", "r")):
        tool = GiteaCloseIssueTool()
        res = await tool.execute(
            agent_id="a", project_id="p",
            repo="o/r", issue_number=5, comment="fertig",
        )

    assert res["ok"] is True
    assert res["state"] == "closed"
    mock_client.comment_issue_for_repo.assert_awaited_once()
    mock_client.update_issue_for_repo.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_handling_returns_dict():
    """Fehler landen als dict, nicht als Exception → Agent sieht saubere Msg."""
    from hydrahive_core.tools_gitea import GiteaGetIssueTool

    mock_client = MagicMock()
    mock_client._get = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("hydrahive_core.tools_gitea.get_gitea_client", return_value=mock_client), \
         patch("hydrahive_core.tools_gitea.resolve_repo_ref", return_value=("o", "r")):
        tool = GiteaGetIssueTool()
        res = await tool.execute(
            agent_id="a", project_id="p", repo="o/r", issue_number=1,
        )

    assert "error" in res
    assert "boom" in res["error"]


def test_prompt_block_now_contains_gitea_tools():
    """Wenn Gitea-Tools registriert sind, müssen sie im deferred-Block auftauchen."""
    from hydrahive_core.tool_registry import render_deferred_tools_block
    block = render_deferred_tools_block()
    assert "gitea_create_issue" in block
    assert "gitea_list_issues" in block
