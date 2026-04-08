"""
tool_loader.py — On-Demand Tool Categories

Zwei-Phasen-System:
  Phase 1: Agent startet mit nur wenigen Meta-Tools (request_tools + Kern-Tools)
  Phase 2: Agent ruft request_tools(categories=[...]) → Kategorie-Tools werden nachgeladen

Vorteil: Statt 43 Tool-Schemas pro Message nur 5-8 → ~70% Token-Einsparung bei reinen Chat-Messages.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Tools die IMMER im ersten Prompt dabei sind (kein request_tools nötig)
# WICHTIG: file_read, file_write, shell_exec, project_shell MÜSSEN hier sein!
# Ohne diese halluziniert der Agent Tool-Aufrufe als Text statt sie echt auszuführen,
# weil die soul.md diese Tools erwähnt aber sie nicht im API-Request sind.
META_TOOLS: list[str] = [
    "request_tools",
    # Dateien — Basis-Funktionalität die fast jeder Agent braucht
    "file_read",
    "file_write",
    # Shell — ohne diese schreibt der Agent "shell_exec" als Text
    "shell_exec",
    "project_shell",
    # Memory & Kommunikation
    "write_memory",
    "read_memory",
    "ask_agent",
    "delegate_agent",
    "read_handoff",
    "write_handoff",
    "create_skill",
    "list_skills",
    # Web — häufig in soul.md referenziert
    "web_search",
    "http_request",
]

# Mapping: Kategorie-Name → Tool-IDs
TOOL_CATEGORIES: dict[str, list[str]] = {
    "discord": [
        "discord_send", "discord_read", "discord_list_all_channels",
        "discord_list_channels", "discord_create_category", "discord_create_channel",
        "discord_delete_channel", "discord_set_topic", "discord_rename_channel",
        "discord_list_members", "discord_list_roles", "discord_delete_message",
        "discord_pin_message",
    ],
    "git": [
        "gitea_repo_inspect", "gitea_repo_tree", "gitea_repo_file",
        "gitea_repo_commits", "gitea_repo_diff",
        "gitea_create_issue", "gitea_update_issue", "gitea_comment_issue",
    ],
    "web": [
        "http_request", "web_search",
    ],
    "file": [
        "file_read", "file_write", "read_system_file", "write_system_file",
    ],
    "shell": [
        "shell_exec",
    ],
    "mail": [
        "send_mail", "receive_mail",
    ],
    "wks": [
        "wks_file_read", "wks_file_write", "wks_shell_exec",
    ],
    "skills": [
        "create_skill", "list_skills", "delete_skill",
    ],
    "a2a": [
        "remote_agent",
    ],
    "browser": [
        "browser_navigate", "browser_screenshot", "browser_click",
        "browser_fill", "browser_evaluate", "browser_close",
    ],
}


def tools_for_categories(
    registry,
    agent_tool_ids: list[str],
    agent_permissions: list[str] | None,
    categories: list[str],
) -> list:
    """
    Gibt Tool-Objekte für die angegebenen Kategorien zurück.
    Nur Tools die in agent.yaml konfiguriert sind und die Permissions haben.
    """
    wanted: list[str] = []
    unknown: list[str] = []
    for cat in categories:
        cat_tools = TOOL_CATEGORIES.get(cat.lower())
        if cat_tools is None:
            unknown.append(cat)
        else:
            wanted.extend(cat_tools)

    if unknown:
        logger.warning("Unbekannte Tool-Kategorien angefragt: %s", unknown)

    # Intersection mit agent.yaml-Liste
    available = set(agent_tool_ids)
    wanted_filtered = [t for t in wanted if t in available]

    return registry.tools_for_agent(wanted_filtered, agent_permissions=agent_permissions)
