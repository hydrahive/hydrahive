"""
guard_utils.py — Zentrale Ownership- und Access-Guards (#307)

Einheitliche Guard-Funktionen für alle Router.
Statt in jedem Router eine eigene _check_agent_access zu definieren,
importieren alle Router diese Funktionen.
"""
from __future__ import annotations

from fastapi import HTTPException


def check_agent_access(
    agent_id: str,
    auth: tuple[str, str],
    group_service=None,
) -> None:
    """Prüft ob der User Zugriff auf den Agent hat.

    Erlaubt wenn:
    - User ist Admin
    - User ist Internal
    - Agent ist persönlicher Agent des Users (personal_{username})
    - group_service bestätigt Zugriff (Gruppenmitgliedschaft)
    """
    username, role = auth
    if role == "admin" or username == "internal":
        return
    if agent_id == f"personal_{username}":
        return
    if group_service and group_service.has_agent_access(username, agent_id):
        return
    raise HTTPException(403, f"Keine Berechtigung für Agent '{agent_id}'")


def check_project_access(
    project_id: str,
    auth: tuple[str, str],
    group_service=None,
) -> None:
    """Prüft ob der User Zugriff auf das Projekt hat.

    Erlaubt wenn:
    - User ist Admin
    - User ist Internal
    - group_service bestätigt Zugriff
    """
    username, role = auth
    if role == "admin" or username == "internal":
        return
    if group_service and group_service.has_project_access(username, project_id):
        return
    raise HTTPException(403, f"Keine Berechtigung für Projekt '{project_id}'")


def derive_sender(auth: tuple[str, str]) -> str:
    """Leitet den Sender aus dem Auth-Kontext ab — nie aus dem Request-Body."""
    username, _ = auth
    return username if username != "internal" else "user"
