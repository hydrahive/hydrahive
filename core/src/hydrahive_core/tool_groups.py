"""
tool_groups.py — On-Demand Tool Selection

Filtert Tool-Schemas analog zu select_skills: nur kontextrelevante
Tools werden an den LLM übergeben. Reduziert Token-Verbrauch erheblich
für alltägliche Nachrichten die keine spezialisierten Tools brauchen.

Gruppen:
  always   — immer geladen (Core-Funktionalität)
  discord  — Discord-Kanal/Server-Operationen
  git      — Gitea/Git Repos, Issues, PRs
  system   — Shell, Systemdateien, Workstation-Shell
  agents   — Agent-Delegation, Handoffs
  skills   — Skill-Verwaltung

Fallback: wenn keine On-Demand-Gruppe matcht → nur always-Tools.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Immer geladen — unabhängig vom Nachrichteninhalt
# ---------------------------------------------------------------------------

ALWAYS_TOOLS: frozenset[str] = frozenset({
    "file_read",
    "file_write",
    "read_memory",
    "write_memory",
    "web_search",
    "http_request",
    "send_mail",
    "receive_mail",
    "wks_file_read",
    "ask_agent",
    "delegate_agent",
    "read_handoff",
    "write_handoff",
})

# ---------------------------------------------------------------------------
# On-Demand-Gruppen: (trigger_keywords, tool_ids)
# ---------------------------------------------------------------------------

_GROUPS: list[tuple[tuple[str, ...], frozenset[str]]] = [
    # Discord
    (
        ("discord", "kanal", "channel", "server-", "serverregel", "rolle", "mitglied"),
        frozenset({
            "discord_send", "discord_read",
            "discord_create_channel", "discord_delete_channel",
            "discord_rename_channel", "discord_set_topic",
            "discord_list_channels", "discord_list_all_channels",
            "discord_list_members", "discord_pin_message",
            "discord_delete_message", "discord_create_category",
            "discord_list_roles",
        }),
    ),
    # Git / Gitea
    (
        ("git", "gitea", "repo", "repository", "issue", "commit", "pull request",
         " pr ", " pr:", "branch", "diff ", "patch", "clone", "merge"),
        frozenset({
            "gitea_create_issue", "gitea_update_issue", "gitea_comment_issue",
            "gitea_repo_inspect", "gitea_repo_tree", "gitea_repo_file",
            "gitea_repo_commits", "gitea_repo_diff",
            "git_status", "git_diff", "git_commit", "git_push", "git_create_pr",
        }),
    ),
    # System / Shell
    (
        ("shell", "bash", "befehl", "systemctl", "service ", "prozess",
         "journal", "install ", "pip ", "apt ", "chmod", "chown",
         "skript", "script", "ausführen", "starten", "stoppen"),
        frozenset({
            "shell_exec",
            "read_system_file", "write_system_file",
            "wks_file_write", "wks_shell_exec",
        }),
    ),
    # Skills
    (
        ("skill", "fähigkeit", "lern", "wissen speich", "prozedur"),
        frozenset({
            "create_skill", "list_skills", "delete_skill",
        }),
    ),
]


def select_tools(tool_ids: list[str], user_text: str) -> list[str]:
    """
    Gibt relevante Tool-IDs zurück:
    - ALWAYS_TOOLS immer enthalten (falls in tool_ids)
    - On-Demand-Gruppen nur bei Keyword-Match im user_text
    - Unbekannte Tools (nicht in Gruppen) landen immer in der Auswahl
      (Forward-Compat: neue Tools nicht versehentlich blockieren)
    """
    text = (user_text or "").lower()

    selected: set[str] = set()

    # 1. Always-Tools
    selected.update(ALWAYS_TOOLS)

    # 2. Gematchte On-Demand-Gruppen
    all_grouped: set[str] = set()
    for triggers, group_tools in _GROUPS:
        all_grouped.update(group_tools)
        if any(t in text for t in triggers):
            selected.update(group_tools)

    # 3. Tools die in keiner Gruppe sind → immer laden (forward-compat)
    for tid in tool_ids:
        if tid not in all_grouped:
            selected.add(tid)

    # Schnittmenge mit agent.yaml-Tools
    return [tid for tid in tool_ids if tid in selected]
