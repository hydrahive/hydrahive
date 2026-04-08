"""
agent_roles.py — Agent Role Presets (#492)

Jede Rolle definiert tools + execution_modes.
resolve_role() löst eine Rolle in die konkreten Werte auf.
"""
from __future__ import annotations

from typing import Literal

AgentRole = Literal["reader", "assistant", "coder", "admin"]

ALL_ROLES: list[str] = ["reader", "assistant", "coder", "admin"]

# ── Permissions pro Rolle ─────────────────────────────────────────────────────
# Abgeleitet aus den permissions_required Properties der Tools in tool_registry.py

_PERM_READER = [
    "filesystem.read", "memory.read", "handoff.read",
    "agents.ask", "mail",
]

_PERM_ASSISTANT = [
    *_PERM_READER,
    "filesystem.write", "memory.write", "handoff.write",
    "agents.delegate",
]

_PERM_CODER_SAFE = [
    *_PERM_ASSISTANT,
    "git.read", "git.issue",
    "workstation.read",
]

_PERM_CODER_ELEVATED = [
    *_PERM_CODER_SAFE,
    "system.read", "system.write",
    "shell.exec",
    "git.write",
    "workstation.write", "workstation.shell",
]

_PERM_ADMIN = [
    *_PERM_CODER_ELEVATED,
    "git.push", "git.pr",
    "spawn_agents", "admin.manage",
    "discord", "vault",
    "filesystem.read_all",
]

# ── Tool-Listen pro Rolle ─────────────────────────────────────────────────────

_TOOLS_READER = [
    "file_read", "web_search", "http_request",
    "read_memory", "receive_mail",
    "read_handoff", "ask_agent",
]

_TOOLS_ASSISTANT = [
    *_TOOLS_READER,
    "file_write", "write_memory",
    "send_mail", "write_handoff", "delegate_agent",
]

_TOOLS_CODER = [
    *_TOOLS_ASSISTANT,
    "shell_exec", "project_shell",
    "read_system_file", "write_system_file",
    "gitea_repo_inspect", "gitea_repo_tree", "gitea_repo_file",
    "gitea_repo_commits", "gitea_repo_diff",
    "gitea_create_issue", "gitea_update_issue", "gitea_comment_issue",
    "wks_file_read", "wks_file_write", "wks_shell_exec",
]

_TOOLS_ADMIN = [
    *_TOOLS_CODER,
    "create_agent", "delete_agent", "create_project", "delete_project",
    "discord_send", "discord_read", "discord_list_channels",
    "discord_list_all_channels", "discord_create_category",
    "discord_create_channel", "discord_delete_channel",
    "discord_set_topic", "discord_rename_channel",
    "discord_list_members", "discord_list_roles",
    "discord_delete_message", "discord_pin_message",
]

# ── Presets ───────────────────────────────────────────────────────────────────

ROLE_PRESETS: dict[str, dict] = {
    "reader": {
        "description": "Nur lesen — sicher für Gäste",
        "tools": _TOOLS_READER,
        "execution_modes": {
            "default": "safe",
            "safe": {"permissions": _PERM_READER},
        },
    },
    "assistant": {
        "description": "Lesen & Schreiben — Standard-Agent",
        "tools": _TOOLS_ASSISTANT,
        "execution_modes": {
            "default": "safe",
            "safe": {"permissions": _PERM_ASSISTANT},
        },
    },
    "coder": {
        "description": "Shell, Git & Code — Entwickler-Agent",
        "tools": _TOOLS_CODER,
        "execution_modes": {
            "default": "elevated",
            "safe": {"permissions": _PERM_CODER_SAFE},
            "elevated": {"permissions": _PERM_CODER_ELEVATED},
        },
    },
    "admin": {
        "description": "Vollzugriff — nur für Admins",
        "tools": _TOOLS_ADMIN,
        "tool_selection": "always",
        "execution_modes": {
            "default": "root",
            "safe": {"permissions": _PERM_CODER_SAFE},
            "elevated": {"permissions": _PERM_CODER_ELEVATED},
            "root": {"permissions": _PERM_ADMIN},
        },
    },
}


def resolve_role(
    role: str | None,
    *,
    tools_extra: list[str] | None = None,
    tools_deny: list[str] | None = None,
) -> tuple[list[str], dict] | None:
    """Löst eine Rolle in (tools, execution_modes) auf.

    Returns None wenn role None ist (Legacy/Custom-Modus).
    """
    if role is None:
        return None
    preset = ROLE_PRESETS.get(role)
    if preset is None:
        return None

    tools = list(preset["tools"])

    if tools_extra:
        for t in tools_extra:
            if t not in tools:
                tools.append(t)
    if tools_deny:
        tools = [t for t in tools if t not in set(tools_deny)]

    return tools, dict(preset["execution_modes"])
