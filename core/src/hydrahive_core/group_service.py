"""
group_service.py — Gruppen-basiertes Berechtigungssystem (#165)

Gruppen werden in /etc/hydrahive/groups.json gespeichert.
Jeder User gehört genau einer Gruppe an (Feld "group" in users.json).
Admins bypassen alle Gruppen-Checks.

Permissions pro Gruppe:
  - pages:   Sichtbare Menüpunkte (Route-IDs)
  - tools:   Erlaubte Tools (Tool-IDs)
  - plugins: Erlaubte Plugins (Plugin-IDs)
  - agents:  Erlaubte Agenten (Agent-IDs)

["*"] = alles erlaubt, [] = nichts erlaubt (persönlicher Agent bleibt immer zugänglich)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GROUPS_FILE = Path("/etc/hydrahive/groups.json")

# Default-Gruppen die beim ersten Start erstellt werden
_DEFAULT_GROUPS: dict[str, dict[str, Any]] = {
    "admin": {
        "label": "Administrator",
        "description": "Vollzugriff auf alle Funktionen",
        "builtin": True,
        "permissions": {
            "pages": ["*"],
            "tools": ["*"],
            "plugins": ["*"],
            "agents": ["*"],
        },
    },
    "standard": {
        "label": "Standard",
        "description": "Chat, Workspace und gängige Tools",
        "builtin": True,
        "permissions": {
            "pages": [
                "dashboard", "my-agent", "projects", "blueprint", "tools",
                "tools/skill-packages", "code-editor", "brain", "voice",
                "activity", "search", "hub",
            ],
            "tools": [
                "file_read", "file_write", "web_search", "http_request",
                "read_memory", "write_memory", "ask_agent", "delegate_agent",
            ],
            "plugins": ["*"],
            "agents": ["*"],
        },
    },
    "gast": {
        "label": "Gast",
        "description": "Nur Chat mit dem eigenen Agenten",
        "builtin": False,
        "permissions": {
            "pages": ["dashboard", "my-agent"],
            "tools": ["file_read", "web_search", "read_memory"],
            "plugins": [],
            "agents": [],
        },
    },
}


class GroupService:
    """Verwaltet Gruppen und prüft Berechtigungen."""

    def __init__(self, users_fn=None):
        """users_fn: Callable das users.json als dict zurückgibt (für User-Lookup)."""
        self._users_fn = users_fn
        self._groups: dict[str, dict] = {}
        self._load()

    # ── Laden / Speichern ────────────────────────────────────────

    def _load(self) -> None:
        if GROUPS_FILE.exists():
            try:
                self._groups = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
                return
            except Exception as e:
                logger.error("groups.json konnte nicht geladen werden: %s", e)
        # Defaults schreiben
        self._groups = dict(_DEFAULT_GROUPS)
        self._save()
        logger.info("groups.json mit Defaults erstellt")

    def _save(self) -> None:
        try:
            GROUPS_FILE.write_text(
                json.dumps(self._groups, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            import os
            os.chmod(GROUPS_FILE, 0o644)
        except Exception as e:
            logger.error("groups.json konnte nicht gespeichert werden: %s", e)

    def reload(self) -> None:
        self._load()

    # ── CRUD ─────────────────────────────────────────────────────

    def list_groups(self) -> dict[str, dict]:
        return dict(self._groups)

    def get_group(self, group_id: str) -> dict | None:
        return self._groups.get(group_id)

    def create_group(self, group_id: str, data: dict) -> dict:
        if group_id in self._groups:
            raise ValueError(f"Gruppe '{group_id}' existiert bereits")
        group = {
            "label": data.get("label", group_id),
            "description": data.get("description", ""),
            "builtin": False,
            "permissions": {
                "pages": data.get("permissions", {}).get("pages", ["dashboard", "my-agent"]),
                "tools": data.get("permissions", {}).get("tools", []),
                "plugins": data.get("permissions", {}).get("plugins", []),
                "agents": data.get("permissions", {}).get("agents", []),
            },
        }
        self._groups[group_id] = group
        self._save()
        return group

    def update_group(self, group_id: str, data: dict) -> dict | None:
        group = self._groups.get(group_id)
        if group is None:
            return None
        if "label" in data:
            group["label"] = data["label"]
        if "description" in data:
            group["description"] = data["description"]
        if "permissions" in data:
            perms = data["permissions"]
            for key in ("pages", "tools", "plugins", "agents"):
                if key in perms:
                    group["permissions"][key] = perms[key]
        self._save()
        return group

    def delete_group(self, group_id: str) -> bool:
        group = self._groups.get(group_id)
        if group is None or group.get("builtin"):
            return False
        del self._groups[group_id]
        self._save()
        return True

    # ── Berechtigungs-Checks ─────────────────────────────────────

    def _get_user_group(self, username: str) -> str:
        if self._users_fn:
            users = self._users_fn()
            user = users.get(username, {})
            return user.get("group", "standard")
        return "standard"

    def _get_user_role(self, username: str) -> str:
        if self._users_fn:
            users = self._users_fn()
            user = users.get(username, {})
            return user.get("role", "user")
        return "user"

    def get_permissions(self, username: str) -> dict:
        """Gibt die effektiven Berechtigungen eines Users zurück."""
        role = self._get_user_role(username)
        if role == "admin":
            return {"pages": ["*"], "tools": ["*"], "plugins": ["*"], "agents": ["*"]}
        group_id = self._get_user_group(username)
        group = self._groups.get(group_id, self._groups.get("standard", {}))
        return group.get("permissions", {})

    def _check(self, username: str, category: str, item_id: str) -> bool:
        """Generische Berechtigungsprüfung."""
        role = self._get_user_role(username)
        if role == "admin":
            return True
        perms = self.get_permissions(username)
        allowed = perms.get(category, [])
        if "*" in allowed:
            return True
        return item_id in allowed

    def has_page_access(self, username: str, page_id: str) -> bool:
        return self._check(username, "pages", page_id)

    def has_tool_access(self, username: str, tool_id: str) -> bool:
        return self._check(username, "tools", tool_id)

    def has_plugin_access(self, username: str, plugin_id: str) -> bool:
        return self._check(username, "plugins", plugin_id)

    def has_agent_access(self, username: str, agent_id: str) -> bool:
        """Prüft ob User den Agenten nutzen darf. Persönlicher Agent ist immer erlaubt."""
        if agent_id == f"personal_{username}":
            return True
        return self._check(username, "agents", agent_id)

    def filter_tools(self, username: str, tool_ids: list[str]) -> list[str]:
        """Filtert eine Tool-Liste auf erlaubte Tools."""
        role = self._get_user_role(username)
        if role == "admin":
            return tool_ids
        perms = self.get_permissions(username)
        allowed = perms.get("tools", [])
        if "*" in allowed:
            return tool_ids
        return [t for t in tool_ids if t in allowed]
