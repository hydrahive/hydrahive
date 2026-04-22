"""
test_git_clone_security.py — Sicherheitstests für GitCloneTool (#828)
Deckt Path-Traversal-Angriffe ab.
"""
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock


# ---- Mock-Hilfen ----
def _mock_cfg():
    return {"url": "https://gitea.hydrahive.io", "token": "fake", "org": "hydrahive"}


def _mock_ws(project_id: str) -> Path:
    return Path(f"/projects/{project_id}")


# ---- Test: Path-Traversal über ../ aussenherum ----
# /projects/hydrahive-coding-evil/repo  startet MIT /projects/hydrahive-coding
# → ein naiver startswith-Check würde das durchlassen
@patch("hydrahive_core.tools_git.GiteaClient")
@patch("hydrahive_core.tools_git._load_config")
@patch("hydrahive_core.tools_git._project_workspace")
async def test_path_traversal_via_parent_dir(mock_ws, mock_cfg, mock_gitea):
    """#828: target_dir='../hydrahive-coding-evil/repo' darf NICHT durchgelassen werden."""
    mock_ws.return_value = Path("/projects/hydrahive-coding")
    mock_cfg.return_value = _mock_cfg()
    mock_gitea._git = AsyncMock(return_value=("ok", "", 0))

    from hydrahive_core.tools_git import GitCloneTool
    tool = GitCloneTool()

    result = await tool.execute(
        agent_id="agent-1",
        project_id="hydrahive-coding",
        repo="hydrahive/core",
        target_dir="../hydrahive-coding-evil/repo",
    )

    assert "error" in result
    assert "ausserhalb des Workspaces" in result["error"]
    # Wichtig: es darf KEIN clone gestartet werden
    mock_gitea._git.assert_not_called()


# ---- Test: Normaler Subdir-Clone muss funktionieren ----
@patch("hydrahive_core.tools_git.GiteaClient")
@patch("hydrahive_core.tools_git._load_config")
@patch("hydrahive_core.tools_git._project_workspace")
async def test_valid_target_dir_subdir(mock_ws, mock_cfg, mock_gitea, tmp_path):
    """target_dir='repo' (kanonischer Default) muss funktionieren."""
    # tmp_path nutzen damit der Test lokal ohne /projects/-Mount laeuft
    mock_ws.return_value = tmp_path
    mock_cfg.return_value = _mock_cfg()
    mock_gitea._git = AsyncMock(return_value=("ok", "", 0))

    from hydrahive_core.tools_git import GitCloneTool
    tool = GitCloneTool()

    result = await tool.execute(
        agent_id="agent-1",
        project_id="hydrahive-coding",
        repo="hydrahive/core",
        target_dir="repo",
    )

    assert result.get("ok") is True, f"got {result!r}"
    mock_gitea._git.assert_called_once()


# ---- Test: Idempotenz — bereits geklont (real fs) ----
@pytest.mark.asyncio
async def test_already_cloned_idempotent(tmp_path, monkeypatch):
    """Wenn .git existiert → sofort success, kein Git-Call."""
    from hydrahive_core import tools_git, tool_registry
    from hydrahive_core.tool_registry import workspace_root

    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(
        tools_git, "_load_config",
        lambda: {"url": "http://x", "org": "o", "token": "t"},
    )

    target = workspace_root("p") / "repo"
    (target / ".git").mkdir(parents=True)

    with patch("hydrahive_core.tools_git.GiteaClient._git", new_callable=AsyncMock) as m:
        tool = tools_git.GitCloneTool()
        result = await tool.execute(
            agent_id="a", project_id="p", repo="o/myrepo",
            target_dir="repo",
        )

    assert result["ok"] is True
    assert result.get("note") == "already cloned"
    m.assert_not_called()
