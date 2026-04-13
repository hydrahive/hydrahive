"""
test_tools_git.py — Native Git-Tools (#621)
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import tools_git  # side-effect: Registration
from hydrahive_core.tools_git import (
    _default_workspace_name, _parse_remote, _workspace_path, _SAFE_NAME,
)
from hydrahive_core.tool_registry import registry


# =========================================================================
# Registration
# =========================================================================

def test_all_git_tools_registered():
    ids = ["git_clone", "git_status", "git_log", "git_diff",
           "git_commit_all", "git_push", "git_pull", "git_branch"]
    for tid in ids:
        t = registry.get(tid)
        assert t is not None, f"{tid} nicht registriert"
        assert t.always_loaded is False
        assert t.category == "git"


def test_read_only_flags():
    for tid in ["git_status", "git_log", "git_diff"]:
        assert registry.get(tid).is_read_only is True
    for tid in ["git_commit_all", "git_clone", "git_pull"]:
        assert registry.get(tid).is_read_only is False
    assert registry.get("git_push").is_destructive is True


# =========================================================================
# Helpers
# =========================================================================

def test_default_workspace_name():
    assert _default_workspace_name("myrepo") == "myrepo"
    assert _default_workspace_name("owner/myrepo") == "myrepo"
    assert _default_workspace_name("owner/myrepo.git") == "myrepo"
    assert _default_workspace_name("owner/myrepo/") == "myrepo"


def test_parse_remote_with_owner():
    p, o, n = _parse_remote("hydrahive/myrepo")
    assert (p, o, n) == ("gitea", "hydrahive", "myrepo")


def test_parse_remote_name_only(monkeypatch):
    # _load_config liefert default org → nimmt die
    monkeypatch.setattr(
        "hydrahive_core.tools_git._load_config",
        lambda: {"org": "myorg", "url": "http://x", "token": "t"},
    )
    p, o, n = _parse_remote("solonamed")
    assert (o, n) == ("myorg", "solonamed")


def test_parse_remote_rejects_urls():
    with pytest.raises(ValueError):
        _parse_remote("http://example.com/x/y")


def test_workspace_path_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    # Gute Namen
    p = _workspace_path("proj", "agt", "repo")
    assert p == tmp_path / "proj" / "agt" / "repo"
    # Path-Traversal, Shell-Chars
    for bad in ["..", "a/b", "a;rm", "$PATH", "a b", "../../etc"]:
        with pytest.raises(ValueError):
            _workspace_path("proj", "agt", bad)


def test_workspace_path_rejects_bad_project_id(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    with pytest.raises(ValueError):
        _workspace_path("../etc", "agt", "repo")


def test_safe_name_regex():
    from hydrahive_core.tools_git import _is_safe
    assert _is_safe("good-name")
    assert _is_safe("good.name_1")
    assert not _is_safe("")
    assert not _is_safe("..")
    assert not _is_safe("../escape")
    assert not _is_safe("a/b")
    assert not _is_safe("a\nb")
    assert not _is_safe("x" * 65)  # >64 chars
    assert not _is_safe(".hidden")  # darf nicht mit Punkt beginnen
    assert not _is_safe("a..b")     # .. irgendwo


# =========================================================================
# Tools — mocked git-calls
# =========================================================================

@pytest.mark.asyncio
async def test_clone_calls_git_with_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(
        tools_git, "_load_config",
        lambda: {"url": "http://gitea:3001", "org": "hydrahive", "token": "secret"},
    )

    async def _mock_git(args, cwd, token=None, username="hydrahive"):
        # Simulate successful clone: create .git dir
        (cwd / "myrepo" / ".git").mkdir(parents=True, exist_ok=True)
        return ("", "", 0)

    with patch("hydrahive_core.tools_git.GiteaClient._git", _mock_git):
        tool = tools_git.GitCloneTool()
        res = await tool.execute(
            agent_id="agtA", project_id="projX",
            repo="hydrahive/myrepo",
        )

    assert res["ok"] is True
    assert "myrepo" in res["workspace"]
    assert "projX" in res["workspace"]
    assert "agtA" in res["workspace"]


@pytest.mark.asyncio
async def test_clone_idempotent_when_already_cloned(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(
        tools_git, "_load_config",
        lambda: {"url": "http://x", "org": "o", "token": "t"},
    )
    # Workspace schon vorhanden (.git existiert)
    ws = tmp_path / "p" / "a" / "myrepo"
    (ws / ".git").mkdir(parents=True)

    with patch("hydrahive_core.tools_git.GiteaClient._git", new_callable=AsyncMock) as m:
        tool = tools_git.GitCloneTool()
        res = await tool.execute(
            agent_id="a", project_id="p", repo="o/myrepo",
        )

    assert res["ok"] is True
    assert res.get("note") == "bereits geklont"
    m.assert_not_called()


@pytest.mark.asyncio
async def test_status_in_non_git_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    (tmp_path / "p" / "a" / "empty").mkdir(parents=True)

    tool = tools_git.GitStatusTool()
    res = await tool.execute(agent_id="a", project_id="p", workspace="empty")
    assert "error" in res


@pytest.mark.asyncio
async def test_status_parses_porcelain(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    ws = tmp_path / "p" / "a" / "r"
    (ws / ".git").mkdir(parents=True)

    async def _mock(args, cwd, token=None, username="hydrahive"):
        return ("## main\n M foo.py\n?? new.py\n", "", 0)

    with patch("hydrahive_core.tools_git.GiteaClient._git", _mock):
        tool = tools_git.GitStatusTool()
        res = await tool.execute(agent_id="a", project_id="p", workspace="r")

    assert res["ok"] is True
    assert res["branch"] == "## main"
    assert "M foo.py" in " ".join(res["changes"])
    assert "?? new.py" in " ".join(res["changes"])
    assert res["clean"] is False


@pytest.mark.asyncio
async def test_commit_all_returns_nothing_to_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    ws = tmp_path / "p" / "a" / "r"
    (ws / ".git").mkdir(parents=True)

    call_count = 0

    async def _mock(args, cwd, token=None, username="hydrahive"):
        nonlocal call_count
        call_count += 1
        if args[0] == "add":
            return ("", "", 0)
        if args[0] == "commit":
            return ("nothing to commit, working tree clean", "", 1)
        return ("", "", 0)

    with patch("hydrahive_core.tools_git.GiteaClient._git", _mock):
        tool = tools_git.GitCommitAllTool()
        res = await tool.execute(
            agent_id="a", project_id="p", workspace="r", message="test",
        )

    assert res["ok"] is True
    assert res.get("committed") is False


@pytest.mark.asyncio
async def test_commit_all_rejects_empty_message(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    tool = tools_git.GitCommitAllTool()
    res = await tool.execute(agent_id="a", project_id="p", workspace="r", message="   ")
    assert "error" in res


@pytest.mark.asyncio
async def test_push_rejects_unsafe_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    ws = tmp_path / "p" / "a" / "r"
    (ws / ".git").mkdir(parents=True)
    tool = tools_git.GitPushTool()
    res = await tool.execute(
        agent_id="a", project_id="p", workspace="r", branch="main;rm -rf /",
    )
    assert "error" in res
    assert "Unsicher" in res["error"]


@pytest.mark.asyncio
async def test_branch_list(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    ws = tmp_path / "p" / "a" / "r"
    (ws / ".git").mkdir(parents=True)

    async def _mock(args, cwd, token=None, username="hydrahive"):
        return ("* main\n  dev\n  remotes/origin/main\n", "", 0)

    with patch("hydrahive_core.tools_git.GiteaClient._git", _mock):
        tool = tools_git.GitBranchTool()
        res = await tool.execute(
            agent_id="a", project_id="p", workspace="r", action="list",
        )

    assert res["ok"] is True
    assert "main" in res["branches"]
    assert "dev" in res["branches"]


@pytest.mark.asyncio
async def test_diff_truncates_long_output(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_git, "_WORKSPACE_ROOT", tmp_path)
    ws = tmp_path / "p" / "a" / "r"
    (ws / ".git").mkdir(parents=True)

    async def _mock(args, cwd, token=None, username="hydrahive"):
        return ("x" * 20000, "", 0)

    with patch("hydrahive_core.tools_git.GiteaClient._git", _mock):
        tool = tools_git.GitDiffTool()
        res = await tool.execute(agent_id="a", project_id="p", workspace="r")

    assert res["ok"] is True
    assert len(res["diff"]) < 9000
    assert "gekürzt" in res["diff"]


def test_tool_search_finds_git_clone():
    """Keyword-Matching muss git_clone finden."""
    import asyncio
    from hydrahive_core.tool_registry import (
        ToolSearchTool, clear_loaded_deferred, session_key,
    )

    async def run():
        clear_loaded_deferred(session_key("p", "a"))
        t = ToolSearchTool()
        return await t.execute(agent_id="a", project_id="p", query="git clone repo")

    res = asyncio.run(run())
    assert "git_clone" in res.get("loaded", [])


# =========================================================================
# #622 — Hook-Blocklist
# =========================================================================

def test_blocklist_blocks_hook_rename():
    from hydrahive_core.tool_registry import _check_shell_blocklist
    cmd = "mv /opt/gitea/data/repositories/org/r.git/hooks/pre-receive /tmp/x.bak"
    assert _check_shell_blocklist(cmd) is not None


def test_blocklist_blocks_cp_to_hook():
    from hydrahive_core.tool_registry import _check_shell_blocklist
    assert _check_shell_blocklist("cp /tmp/evil /opt/gitea/data/repositories/o/r.git/hooks/pre-receive") is not None


def test_blocklist_blocks_chmod_hook():
    from hydrahive_core.tool_registry import _check_shell_blocklist
    assert _check_shell_blocklist("chmod -x /srv/git/r.git/hooks/post-receive") is not None


def test_blocklist_blocks_dotgit_hooks():
    from hydrahive_core.tool_registry import _check_shell_blocklist
    assert _check_shell_blocklist("cat /tmp/myrepo/.git/hooks/pre-commit") is not None


def test_blocklist_blocks_gitea_repo_dir():
    from hydrahive_core.tool_registry import _check_shell_blocklist
    assert _check_shell_blocklist("ls /opt/gitea/data/repositories/") is not None
    assert _check_shell_blocklist("cd /opt/gitea/git/repositories/x && ls") is not None


def test_blocklist_does_not_block_normal_git():
    from hydrahive_core.tool_registry import _check_shell_blocklist
    # Normale git-Operationen weiter erlaubt (kommen über git_* Tools)
    assert _check_shell_blocklist("git status") is None
    assert _check_shell_blocklist("git log --oneline") is None
    assert _check_shell_blocklist("ls /tmp/myrepo") is None
