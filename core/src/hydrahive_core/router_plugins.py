"""router_plugins.py — Plugin-Verwaltung Admin-API (#49)

GET  /plugins         → geladene Plugins + Hook-Übersicht
POST /plugins/reload  → alle Plugins neu laden
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from .plugin_manager import plugin_manager

logger = logging.getLogger(__name__)


def register_plugin_routes(
    admin_router: APIRouter,
    *,
    require_admin,
    agents_dir: str,
) -> None:

    @admin_router.get("/plugins")
    def list_plugins(_a: tuple = Depends(require_admin)):
        return {
            "plugins": plugin_manager.loaded,
            "hooks": plugin_manager.hook_summary,
            "total": len(plugin_manager.loaded),
        }

    @admin_router.post("/plugins/reload")
    def reload_plugins(_a: tuple = Depends(require_admin)):
        count = plugin_manager.reload_all(agents_dir)
        logger.info("Plugins neu geladen: %d", count)
        return {
            "reloaded": count,
            "plugins": plugin_manager.loaded,
            "hooks": plugin_manager.hook_summary,
        }
