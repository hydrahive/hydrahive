"""
tools_gitea.py — Deferred Gitea-Tools (#620 + #619)

5 native Gitea-Tools, die server-side den Config-Token aus
/etc/hydrahive/gitea_config.json laden. Damit braucht der Agent keinen
Shell-Zugriff auf Token-Dateien mehr (war der Angriffsvektor aus #617).

Alle Tools sind deferred (always_loaded=False) und werden via tool_search
aktiviert:
    tool_search(query="select:gitea_create_issue")
    → Schema geladen
    gitea_create_issue(repo="hydrahive/hydrahive", title="...", body="...")

Repo-Syntax:
- "owner/repo" — vollqualifiziert
- "repo"      — nimmt Default-Org aus gitea_config.json
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .gitea import get_gitea_client, resolve_repo_ref
from .tool_registry import BaseTool, registry

logger = logging.getLogger(__name__)


def _parse_err(e: Exception) -> dict:
    try:
        cls = aiohttp.ClientResponseError  # type: ignore[attr-defined]
        if isinstance(cls, type) and isinstance(e, cls):
            return {"error": str(e), "status": e.status}
    except Exception:
        pass
    return {"error": f"{type(e).__name__}: {e}"}


class _GiteaToolBase(BaseTool):
    @property
    def always_loaded(self) -> bool: return False
    @property
    def category(self) -> str: return "gitea"
    @property
    def permissions_required(self) -> list[str]: return ["gitea:write"]


class GiteaCreateIssueTool(_GiteaToolBase):
    @property
    def id(self) -> str: return "gitea_create_issue"
    @property
    def name(self) -> str: return "Gitea Issue anlegen"
    @property
    def description(self) -> str:
        return (
            "Erstellt ein neues Issue in einem Gitea-Repo. Server-side "
            "authenticated — kein Token-Handling im Agent. repo als "
            "'owner/name' oder nur 'name' (Default-Org wird genutzt)."
        )

    @property
    def semantic_tags(self) -> list[str]:
        return ["gitea", "issue", "ticket", "bug", "task", "create", "new"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo":  {"type": "string", "description": "'owner/name' oder 'name'"},
                "title": {"type": "string", "description": "Issue-Titel"},
                "body":  {"type": "string", "description": "Markdown-Body (optional)"},
            },
            "required": ["repo", "title"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        repo: str, title: str, body: str = "", **kwargs,
    ) -> dict:
        try:
            owner, name = resolve_repo_ref(repo)
            client = get_gitea_client()
            result = await client.create_issue_for_repo(owner, name, title, body)
            return {
                "ok": True,
                "number": result.get("number"),
                "url": result.get("html_url"),
                "title": result.get("title"),
            }
        except Exception as e:
            return _parse_err(e)


class GiteaCommentIssueTool(_GiteaToolBase):
    @property
    def id(self) -> str: return "gitea_comment_issue"
    @property
    def name(self) -> str: return "Gitea Issue kommentieren"
    @property
    def description(self) -> str:
        return "Fügt einen Kommentar an ein Gitea-Issue. repo + issue_number."

    @property
    def semantic_tags(self) -> list[str]:
        return ["gitea", "issue", "comment", "reply", "update"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo":         {"type": "string", "description": "'owner/name' oder 'name'"},
                "issue_number": {"type": "integer"},
                "body":         {"type": "string", "description": "Kommentar-Text (Markdown)"},
            },
            "required": ["repo", "issue_number", "body"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        repo: str, issue_number: int, body: str, **kwargs,
    ) -> dict:
        try:
            owner, name = resolve_repo_ref(repo)
            client = get_gitea_client()
            result = await client.comment_issue_for_repo(owner, name, int(issue_number), body)
            return {
                "ok": True,
                "id": result.get("id"),
                "url": result.get("html_url"),
            }
        except Exception as e:
            return _parse_err(e)


class GiteaListIssuesTool(_GiteaToolBase):
    @property
    def id(self) -> str: return "gitea_list_issues"
    @property
    def name(self) -> str: return "Gitea Issues auflisten"
    @property
    def description(self) -> str:
        return "Listet Issues eines Repos. state: open|closed|all. limit default 20."

    @property
    def is_read_only(self) -> bool: return True
    @property
    def permissions_required(self) -> list[str]: return ["gitea:read"]
    @property
    def semantic_tags(self) -> list[str]:
        return ["gitea", "issue", "list", "search", "query", "open", "closed"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo":  {"type": "string"},
                "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["repo"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        repo: str, state: str = "open", limit: int = 20, **kwargs,
    ) -> dict:
        try:
            owner, name = resolve_repo_ref(repo)
            client = get_gitea_client()
            limit = max(1, min(int(limit), 50))
            items = await client._get(
                f"/repos/{owner}/{name}/issues?state={state}&limit={limit}&type=issues"
            )
            if not isinstance(items, list):
                items = []
            return {
                "ok": True,
                "count": len(items),
                "issues": [
                    {
                        "number": i.get("number"),
                        "title":  i.get("title"),
                        "state":  i.get("state"),
                        "url":    i.get("html_url"),
                    }
                    for i in items
                ],
            }
        except Exception as e:
            return _parse_err(e)


class GiteaGetIssueTool(_GiteaToolBase):
    @property
    def id(self) -> str: return "gitea_get_issue"
    @property
    def name(self) -> str: return "Gitea Issue lesen"
    @property
    def description(self) -> str:
        return "Liest ein einzelnes Issue inkl. Body + Meta. repo + issue_number."

    @property
    def is_read_only(self) -> bool: return True
    @property
    def permissions_required(self) -> list[str]: return ["gitea:read"]
    @property
    def semantic_tags(self) -> list[str]:
        return ["gitea", "issue", "read", "get", "detail"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo":         {"type": "string"},
                "issue_number": {"type": "integer"},
            },
            "required": ["repo", "issue_number"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        repo: str, issue_number: int, **kwargs,
    ) -> dict:
        try:
            owner, name = resolve_repo_ref(repo)
            client = get_gitea_client()
            data = await client._get(f"/repos/{owner}/{name}/issues/{int(issue_number)}")
            if not isinstance(data, dict):
                return {"error": "Unexpected response"}
            return {
                "ok":     True,
                "number": data.get("number"),
                "title":  data.get("title"),
                "body":   data.get("body"),
                "state":  data.get("state"),
                "url":    data.get("html_url"),
                "labels": [l.get("name") for l in (data.get("labels") or []) if isinstance(l, dict)],
            }
        except Exception as e:
            return _parse_err(e)


class GiteaCloseIssueTool(_GiteaToolBase):
    @property
    def id(self) -> str: return "gitea_close_issue"
    @property
    def name(self) -> str: return "Gitea Issue schließen"
    @property
    def description(self) -> str:
        return "Schließt ein Issue (optional mit Abschluss-Kommentar)."

    @property
    def is_destructive(self) -> bool: return True
    @property
    def semantic_tags(self) -> list[str]:
        return ["gitea", "issue", "close", "resolve", "done"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo":         {"type": "string"},
                "issue_number": {"type": "integer"},
                "comment":      {"type": "string", "description": "Optional Abschluss-Kommentar"},
            },
            "required": ["repo", "issue_number"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        repo: str, issue_number: int, comment: str = "", **kwargs,
    ) -> dict:
        try:
            owner, name = resolve_repo_ref(repo)
            client = get_gitea_client()
            num = int(issue_number)
            if comment:
                await client.comment_issue_for_repo(owner, name, num, comment)
            result = await client.update_issue_for_repo(owner, name, num, state="closed")
            return {
                "ok": True,
                "number": result.get("number"),
                "state": result.get("state"),
            }
        except Exception as e:
            return _parse_err(e)


# =========================================================================
# Registration
# =========================================================================

registry.register(GiteaCreateIssueTool())
registry.register(GiteaCommentIssueTool())
registry.register(GiteaListIssuesTool())
registry.register(GiteaGetIssueTool())
registry.register(GiteaCloseIssueTool())
