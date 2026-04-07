"""
router_config_map.py — Zentrale Konfigurationslandkarte (#306)

GET /admin/config/map — Alle Config-Surfaces mit Live-Status.
Jede Surface: Name, Beschreibung, UI-Pfad, Config-Datei, Status (configured/unconfigured/partial).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends

from .settings import settings

logger = logging.getLogger(__name__)

# Statische Surface-Definitionen
_SURFACES: list[dict] = [
    {
        "id": "llm",
        "label": "LLM / AI-Provider",
        "description": "API-Keys und Modelle für Claude, OpenAI, Ollama etc.",
        "icon": "cpu",
        "ui_path": "/settings?tab=llm",
        "config_file": str(settings.llm_config),
        "owner": "admin",
    },
    {
        "id": "gitea",
        "label": "Gitea (lokaler Git-Server)",
        "description": "URL, Token und Organisation für den lokalen Gitea-Server.",
        "icon": "git-branch",
        "ui_path": "/settings?tab=gitea",
        "config_file": str(settings.gitea_config),
        "owner": "admin",
    },
    {
        "id": "github",
        "label": "GitHub",
        "description": "GitHub Personal Access Token für Repos und Issues.",
        "icon": "github",
        "ui_path": "/settings?tab=github",
        "config_file": str(settings.github_token_file),
        "owner": "admin",
    },
    {
        "id": "repos",
        "label": "Git-Repos",
        "description": "Zentrale Repo-Verwaltung mit Credentials und Agent-Zuweisung.",
        "icon": "git-branch",
        "ui_path": "/settings?tab=repos",
        "config_file": str(settings.repos_config),
        "owner": "admin",
    },
    {
        "id": "vpn",
        "label": "VPN / Tailscale",
        "description": "Tailscale-Netzwerk für sichere Verbindungen zwischen Servern.",
        "icon": "network",
        "ui_path": "/settings?tab=vpn",
        "config_file": str(settings.tailscale_config),
        "owner": "admin",
    },
    {
        "id": "kas",
        "label": "KAS / E-Mail",
        "description": "All-Inkl KAS-Zugangsdaten für E-Mail und Domain-Verwaltung.",
        "icon": "mail",
        "ui_path": "/settings?tab=kas",
        "config_file": str(settings.kas_config),
        "owner": "admin",
    },
    {
        "id": "mcp",
        "label": "MCP Server",
        "description": "Model Context Protocol Server für erweiterte Tool-Integration.",
        "icon": "plug",
        "ui_path": "/mcp",
        "config_file": str(settings.mcp_servers_config),
        "owner": "admin",
    },
    {
        "id": "agentlink",
        "label": "AgentLink",
        "description": "State/Handoff-API für Agent-Kommunikation.",
        "icon": "link",
        "ui_path": "/settings?tab=overview",
        "config_file": str(settings.agentlink_config),
        "owner": "admin",
    },
    {
        "id": "butler",
        "label": "Webhook Butler",
        "description": "Webhook-Routing und Automatisierungen.",
        "icon": "webhook",
        "ui_path": "/butler",
        "config_file": str(settings.butler_webhooks_config),
        "owner": "admin",
    },
    {
        "id": "secrets",
        "label": "Agent Secrets",
        "description": "API-Keys und Passwörter für Agent-Tools (get_secret).",
        "icon": "key",
        "ui_path": "/secrets",
        "config_file": str(settings.agent_secrets_config),
        "owner": "admin",
    },
    {
        "id": "users",
        "label": "Benutzer & Gruppen",
        "description": "User-Accounts, Rollen und Gruppenzugehörigkeiten.",
        "icon": "users",
        "ui_path": "/users",
        "config_file": str(settings.users_config),
        "owner": "admin",
    },
    {
        "id": "voice",
        "label": "Voice / STT / TTS",
        "description": "Spracherkennung und Text-to-Speech Konfiguration.",
        "icon": "mic",
        "ui_path": "/settings?tab=overview",
        "config_file": str(settings.voice_config),
        "owner": "admin",
    },
    {
        "id": "servers",
        "label": "Remote-Server (SSH)",
        "description": "SSH-Ziele für Agent-Server-Management.",
        "icon": "server",
        "ui_path": "/agents",
        "config_file": str(settings.agent_servers_config),
        "owner": "admin",
    },
    {
        "id": "a2a",
        "label": "A2A / Federation",
        "description": "Agent-to-Agent Peers für Server-übergreifende Kommunikation.",
        "icon": "globe",
        "ui_path": "/settings?tab=overview",
        "config_file": str(settings.a2a_peers_config),
        "owner": "admin",
    },
    {
        "id": "notifications",
        "label": "Benachrichtigungen",
        "description": "Routing-Regeln für Agent-Benachrichtigungen.",
        "icon": "bell",
        "ui_path": "/settings?tab=overview",
        "config_file": str(settings.notification_routes_config),
        "owner": "admin",
    },
]


def _check_status(config_file: str) -> str:
    """Prüft ob eine Config-Datei existiert und nicht leer ist."""
    p = Path(config_file)
    if not p.exists():
        return "unconfigured"
    try:
        content = p.read_text(encoding="utf-8").strip()
        if not content or content in ("{}", "[]", "null", ""):
            return "unconfigured"
        # JSON-Dateien: prüfen ob sinnvolle Daten drin sind
        if p.suffix == ".json":
            data = json.loads(content)
            if isinstance(data, dict) and not data:
                return "unconfigured"
            if isinstance(data, list) and not data:
                return "unconfigured"
        return "configured"
    except Exception:
        return "partial"


def register_config_map_routes(
    admin_router: APIRouter,
    *,
    require_admin,
) -> None:

    @admin_router.get("/config/map")
    def get_config_map(_a: tuple = Depends(require_admin)):
        surfaces = []
        for s in _SURFACES:
            status = _check_status(s["config_file"])
            surfaces.append({**s, "status": status})

        configured = sum(1 for s in surfaces if s["status"] == "configured")
        total = len(surfaces)

        return {
            "surfaces": surfaces,
            "summary": {
                "configured": configured,
                "unconfigured": total - configured,
                "total": total,
            },
        }
