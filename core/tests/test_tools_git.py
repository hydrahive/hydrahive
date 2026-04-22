"""
test_tools_git.py — Native Git-Tools (#621, #635 SSOT)
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import tools_git, tool_registry  # side-effect: Registration
from hydrahive_core.tools_git import (
    _parse_remote, _project_workspace, _SAFE_NAME, _is_safe,
)
from hydrahive_core.tool_registry import registry, workspace_root


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


def test_no_workspace_param_in_schemas():
    """#635: 'workspace'-Parameter wurde aus allen git_*-Schemas entfernt."""
    git_ids = ["git_clone", "git_status", "git_log", "git_diff",
               "git_commit_all", "git_push", "git_pull", "git_branch"]
    for tid in git_ids:
        params = registry.get(tid).parameters
        assert "workspace" not in params.get("properties", {}), (
            f"{tid} hat noch einen 'workspace'-Parameter"
        )


# =========================================================================
# Helpers
# =========================================================================

def test_parse_remote_with_owner():
    p, o, n = _parse_remote("hydrahive/myrepo")
    assert (p, o, n) == ("gitea", "hydrahive", "myrepo")


def test_parse_remote_name_only(monkeypatch):
    monkeypatch.setattr(
        "hydrahive_core.tools_git._load_config",
        lambda: {"org": "myorg", "url": "http://x", "token": "t"},
    )
    p, o, n = _parse_remote("solonamed")
    assert (o, n) == ("myorg", "solonamed")


def test_parse_remote_rejects_urls():
    with pytest.raises(ValueError):
        _parse_remote("http://example.com/x/y")


def test_project_workspace_uses_workspace_root(tmp_path, monkeypatch):
    """#635: _project_workspace ist dünner Wrapper über workspace_root."""
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    assert _project_workspace("proj") == workspace_root("proj") == (tmp_path / "proj").resolve()


def test_project_workspace_rejects_bad_project_id():
    for bad in ["", "..", "../etc", "a/b", "a;rm"]:
        with pytest.raises(ValueError):
            _project_workspace(bad)


def test_safe_name_regex():
    assert _is_safe("good-name")
    assert _is_safe("good.name_1")
    assert not _is_safe("")
    assert not _is_safe("..")
    assert not _is_safe("../escape")
    assert not _is_safe("a/b")
    assert not _is_safe("a\nb")
    assert not _is_safe("x" * 65)
    assert not _is_safe(".hidden")
    assert not _is_safe("a..b")


# =========================================================================
# Tools — mocked git-calls
# =========================================================================

@pytest.mark.asyncio
async def test_clone_into_empty_project_workspace(tmp_path, monkeypatch):
    """#635: git_clone klont direkt nach workspace_root(pid)."""
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(
        tools_git, "_load_config",
        lambda: {"url": "http://gitea:3001", "org": "hydrahive", "token": "secret"},
    )

    async def _mock_git(args, cwd, token=None, username="hydrahive"):
        # Simuliere: clone legt das Ziel-Dir mit .git an
        target = Path(args[-1])
        (target / ".git").mkdir(parents=True, exist_ok=True)
        return ("", "", 0)

    with patch("hydrahive_core.tools_git.GiteaClient._git", _mock_git):
        tool = tools_git.GitCloneTool()
        res = await tool.execute(
            agent_id="agtA", project_id="projX",
            repo="hydrahive/myrepo",
        )

    assert res["ok"] is True
    # #828: Default-target ist jetzt <workspace_root>/repo, nicht mehr root
    assert res["workspace"] == str(workspace_root("projX") / "repo")


@pytest.mark.asyncio
async def test_clone_idempotent_when_already_cloned(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(
        tools_git, "_load_config",
        lambda: {"url": "http://x", "org": "o", "token": "t"},
    )
    # #828: Idempotenz wird jetzt am target-Subdir geprueft (Default: 'repo')
    ws = workspace_root("p")
    (ws / "repo" / ".git").mkdir(parents=True)

    with patch("hydrahive_core.tools_git.GiteaClient._git", new_callable=AsyncMock) as m:
        tool = tools_git.GitCloneTool()
        res = await tool.execute(agent_id="a", project_id="p", repo="o/myrepo")

    assert res["ok"] is True
    # Boss hat note auf 'already cloned' geaendert (englisch)
    assert res.get("note") in ("bereits geklont", "already cloned")
    m.assert_not_called()


@pytest.mark.asyncio
async def test_clone_rejects_non_empty_target_without_git(tmp_path, monkeypatch):
    """#635/#828: non-empty target_dir ohne .git → kein Ueberschreiben.

    Geaendert durch #828: target wird jetzt auf das target_dir-Subdir
    geprueft (Default: 'repo'), nicht auf den Workspace-Root. Wir legen
    deshalb 'repo/' im Workspace an + befuellen es, damit der Check greift.
    """
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(
        tools_git, "_load_config",
        lambda: {"url": "http://x", "org": "o", "token": "t"},
    )
    ws = workspace_root("p")
    ws.mkdir(parents=True)
    # neuer Verhalten: clone-target ist <ws>/repo/, da legen wir Schmutz hin
    target = ws / "repo"
    target.mkdir()
    (target / "existing.txt").write_text("hands off", encoding="utf-8")

    tool = tools_git.GitCloneTool()
    res = await tool.execute(agent_id="a", project_id="p", repo="o/myrepo")
    assert "error" in res
    assert "nicht leer" in res["error"]


@pytest.mark.asyncio
async def test_status_in_non_git_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    workspace_root("p").mkdir(parents=True)

    tool = tools_git.GitStatusTool()
    res = await tool.execute(agent_id="a", project_id="p")
    assert "error" in res


@pytest.mark.asyncio
async def test_status_parses_porcelain(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    ws = workspace_root("p")
    (ws / ".git").mkdir(parents=True)

    async def _mock(args, cwd, token=None, username="hydrahive"):
        return ("## main\n M foo.py\n?? new.py\n", "", 0)

    with patch("hydrahive_core.tools_git.GiteaClient._git", _mock):
        tool = tools_git.GitStatusTool()
        res = await tool.execute(agent_id="a", project_id="p")

    assert res["ok"] is True
    assert res["branch"] == "## main"
    assert "M foo.py" in " ".join(res["changes"])
    assert "?? new.py" in " ".join(res["changes"])
    assert res["clean"] is False


@pytest.mark.asyncio
async def test_commit_all_returns_nothing_to_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    ws = workspace_root("p")
    (ws / ".git").mkdir(parents=True)

    async def _mock(args, cwd, token=None, username="hydrahive"):
        if args[0] == "add":
            return ("", "", 0)
        if args[0] == "commit":
            return ("nothing to commit, working tree clean", "", 1)
        return ("", "", 0)

    with patch("hydrahive_core.tools_git.GiteaClient._git", _mock):
        tool = tools_git.GitCommitAllTool()
        res = await tool.execute(agent_id="a", project_id="p", message="test")

    assert res["ok"] is True
    assert res.get("committed") is False


@pytest.mark.asyncio
async def test_commit_all_rejects_empty_message(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    tool = tools_git.GitCommitAllTool()
    res = await tool.execute(agent_id="a", project_id="p", message="   ")
    assert "error" in res


@pytest.mark.asyncio
async def test_push_rejects_unsafe_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    ws = workspace_root("p")
    (ws / ".git").mkdir(parents=True)
    tool = tools_git.GitPushTool()
    res = await tool.execute(agent_id="a", project_id="p", branch="main;rm -rf /")
    assert "error" in res
    assert "Unsicher" in res["error"]


@pytest.mark.asyncio
async def test_branch_list(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    ws = workspace_root("p")
    (ws / ".git").mkdir(parents=True)

    async def _mock(args, cwd, token=None, username="hydrahive"):
        return ("* main\n  dev\n  remotes/origin/main\n", "", 0)

    with patch("hydrahive_core.tools_git.GiteaClient._git", _mock):
        tool = tools_git.GitBranchTool()
        res = await tool.execute(agent_id="a", project_id="p", action="list")

    assert res["ok"] is True
    assert "main" in res["branches"]
    assert "dev" in res["branches"]


@pytest.mark.asyncio
async def test_diff_truncates_long_output(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    ws = workspace_root("p")
    (ws / ".git").mkdir(parents=True)

    async def _mock(args, cwd, token=None, username="hydrahive"):
        return ("x" * 20000, "", 0)

    with patch("hydrahive_core.tools_git.GiteaClient._git", _mock):
        tool = tools_git.GitDiffTool()
        res = await tool.execute(agent_id="a", project_id="p")

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
# #622 — Hook-Blocklist (unverändert)
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
    assert _check_shell_blocklist("git status") is None
    assert _check_shell_blocklist("git log --oneline") is None
    assert _check_shell_blocklist("ls /tmp/myrepo") is None
