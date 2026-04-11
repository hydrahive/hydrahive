"""
messenger_router.py — Messenger-Routing für v2-Projekte

Liest messenger.yaml aus allen Projekten und baut Routing-Tabellen:
  - WhatsApp agent_id → project_id
  - Discord channel_id → project_id
  - Telegram chat_id → project_id

Wird beim Core-Start und bei Projekt-Änderungen aktualisiert.

Nutzung:
    from .messenger_router import messenger_router
    project_id = messenger_router.resolve_whatsapp("personal_admin")
    project_id = messenger_router.resolve_discord("123456789")
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .settings import settings

logger = logging.getLogger(__name__)


class MessengerRouter:
    """Routet eingehende Messenger-Nachrichten zum richtigen Projekt."""

    def __init__(self) -> None:
        # agent_id / session_id → project_id
        self._whatsapp_routes: dict[str, str] = {}
        # channel_id → project_id
        self._discord_routes: dict[str, str] = {}
        # chat_id → project_id
        self._telegram_routes: dict[str, str] = {}
        # room_id → project_id
        self._matrix_routes: dict[str, str] = {}

    def rebuild(self, projects_dir: Path | None = None) -> None:
        """Scannt alle Projekte und baut die Routing-Tabellen neu auf.

        Wird beim Core-Start aufgerufen und wenn Projekte sich ändern.
        """
        pdir = projects_dir or settings.projects_dir
        if not pdir.exists():
            return

        wa: dict[str, str] = {}
        dc: dict[str, str] = {}
        tg: dict[str, str] = {}
        mx: dict[str, str] = {}

        for project_dir in pdir.iterdir():
            if not project_dir.is_dir() or project_dir.name.startswith("_deleted"):
                continue

            project_id = project_dir.name
            messenger_yaml = project_dir / "messenger.yaml"

            if not messenger_yaml.exists():
                # Kein messenger.yaml → Fallback: WhatsApp über project_id
                # (abwärtskompatibel: personal_admin → personal_admin)
                wa[project_id] = project_id
                continue

            try:
                cfg = yaml.safe_load(messenger_yaml.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("messenger.yaml Fehler in %s: %s", project_id, e)
                continue

            if not isinstance(cfg, dict):
                continue

            # WhatsApp
            wa_cfg = cfg.get("whatsapp", {})
            if wa_cfg:
                # Session-IDs die zu diesem Projekt routen
                for sid in wa_cfg.get("session_ids", [project_id]):
                    wa[str(sid)] = project_id

            # Discord
            dc_cfg = cfg.get("discord", {})
            if dc_cfg:
                for ch_id in dc_cfg.get("channels", []):
                    dc[str(ch_id)] = project_id

            # Telegram
            tg_cfg = cfg.get("telegram", {})
            if tg_cfg:
                for chat_id in tg_cfg.get("chat_ids", []):
                    tg[str(chat_id)] = project_id

            # Matrix
            mx_cfg = cfg.get("matrix", {})
            if mx_cfg:
                room = mx_cfg.get("room", "")
                if room:
                    mx[room] = project_id

        self._whatsapp_routes = wa
        self._discord_routes = dc
        self._telegram_routes = tg
        self._matrix_routes = mx

        total = len(wa) + len(dc) + len(tg) + len(mx)
        if total > 0:
            logger.info(
                "MessengerRouter: %d Routen (WhatsApp=%d, Discord=%d, Telegram=%d, Matrix=%d)",
                total, len(wa), len(dc), len(tg), len(mx),
            )

    # ── Routing-Lookups ────────────────────────────────────────────────

    def resolve_whatsapp(self, agent_id: str) -> str | None:
        """WhatsApp agent_id → project_id. None wenn kein Routing gefunden."""
        return self._whatsapp_routes.get(agent_id)

    def resolve_discord(self, channel_id: str) -> str | None:
        """Discord channel_id → project_id."""
        return self._discord_routes.get(str(channel_id))

    def resolve_telegram(self, chat_id: str) -> str | None:
        """Telegram chat_id → project_id."""
        return self._telegram_routes.get(str(chat_id))

    def resolve_matrix(self, room_id: str) -> str | None:
        """Matrix room_id → project_id."""
        return self._matrix_routes.get(room_id)

    # ── Info ───────────────────────────────────────────────────────────

    def routes_for_project(self, project_id: str) -> dict:
        """Alle Messenger-Routen für ein Projekt."""
        return {
            "whatsapp": [k for k, v in self._whatsapp_routes.items() if v == project_id],
            "discord": [k for k, v in self._discord_routes.items() if v == project_id],
            "telegram": [k for k, v in self._telegram_routes.items() if v == project_id],
            "matrix": [k for k, v in self._matrix_routes.items() if v == project_id],
        }

    def all_routes(self) -> dict:
        """Alle Routing-Tabellen."""
        return {
            "whatsapp": dict(self._whatsapp_routes),
            "discord": dict(self._discord_routes),
            "telegram": dict(self._telegram_routes),
            "matrix": dict(self._matrix_routes),
        }


# Globale Singleton-Instanz
messenger_router = MessengerRouter()
