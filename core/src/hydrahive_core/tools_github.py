"""
tools_github.py — Deferred GitHub-Tools (#861)

github_get_issue: Liest ein einzelnes Issue von der GitHub public API.
Nutzt project.github_repo aus der ProjectConfig und die GitHub REST API
(https://api.github.com/repos/{owner}/{repo}/issues/{number}).
Kein Token noetig fuer public Repos.

Alle Tools sind deferred (always_loaded=False) und werden via tool_search
aktiviert.
"""
from __future__ import annotations

import logging
from pathlib import Path

import aiohttp

from .project_config import load_project_config
from .tool_registry import BaseTool, registry, workspace_root

logger = logging.getLogger(__name__)


def _parse_err(e: Exception) -> dict:
    try:
        cls = aiohttp.ClientResponseError  # type: ignore[attr-defined]
        if isinstance(cls, type) and isinstance(e, cls):
            return {"error": str(e), "status": e.status}
    except Exception:
        pass
    return {"error": f"{type(e).__name__}: {e}"}


class _GitHubToolBase(BaseTool):
    @property
    def always_loaded(self) -> bool: return False
    @property
    def category(self) -> str: return "github"


class GitHubGetIssueTool(_GitHubToolBase):
    @property
    def id(self) -> str: return "github_get_issue"
    @property
    def name(self) -> str: return "GitHub Issue lesen"
    @property
    def description(self) -> str:
        return (
            "Liest ein einzelnes Issue inkl. Body + Meta von GitHub. "
            "Nutzt project.github_repo aus der ProjectConfig. "
            "repo als 'owner/name' (optional, ueberschreibt project.github_repo)."
        )

    @property
    def is_read_only(self) -> bool: return True
    @property
    def semantic_tags(self) -> list[str]:
        return ["github", "issue", "read", "get", "detail"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo":         {"type": "string", "description": "'owner/name' (optional — ueberschreibt project.github_repo)"},
                "issue_number": {"type": "integer"},
            },
            "required": ["issue_number"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        issue_number: int, repo: str = "", **kwargs,
    ) -> dict:
        # github_repo aus ProjectConfig, wenn nicht explizit ueberschrieben
        if not repo:
            config = load_project_config(workspace_root(project_id))
            repo = config.github_repo if config else ""

        if not repo:
            return {"error": "Kein github_repo in project.github_repo und kein repo explizit angegeben"}

        owner, name = repo.split("/", 1) if "/" in repo else (repo, "")

        url = f"https://api.github.com/repos/{owner}/{name}/issues/{int(issue_number)}"
        headers = {"Accept": "application/vnd.github.v3+json"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 404:
                        return {"error": f"Issue #{issue_number} nicht gefunden in {owner}/{name}"}
                    if resp.status != 200:
                        return {"error": f"GitHub API responded with {resp.status}"}
                    data = await resp.json()

            return {
                "ok":          True,
                "number":      data.get("number"),
                "title":       data.get("title"),
                "body":        data.get("body") or "",
                "state":       data.get("state"),
                "url":         data.get("html_url"),
                "labels":      [l.get("name") for l in (data.get("labels") or []) if isinstance(l, dict)],
                "author":      (data.get("user") or {}).get("login", ""),
                "created_at":  data.get("created_at"),
                "updated_at":  data.get("updated_at"),
                "comments":    data.get("comments", 0),
                "assignees":   [a.get("login") for a in (data.get("assignees") or []) if isinstance(a, dict)],
            }
        except Exception as e:
            return _parse_err(e)


# =========================================================================
# Registration
# =========================================================================

registry.register(GitHubGetIssueTool())
