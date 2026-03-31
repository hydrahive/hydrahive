"""
router_plugins.py — Plugin-Verwaltung Admin-API (#49, #110)

GET    /plugins                    → Alle Plugins (Manifest + Legacy) + Hooks
GET    /plugins/{id}               → Plugin-Details
POST   /plugins/{id}/enable        → Plugin aktivieren
POST   /plugins/{id}/disable       → Plugin deaktivieren
POST   /plugins/{id}/reload        → Plugin neu laden
POST   /plugins/reload             → ALLE Plugins neu laden
GET    /plugins/agents/{agent_id}  → Plugins eines Agents
PUT    /plugins/agents/{agent_id}  → Plugin-Zuweisungen setzen
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .plugin_manager import plugin_manager

logger = logging.getLogger(__name__)


class AgentPluginsRequest(BaseModel):
    plugin_ids: list[str]


def register_plugin_routes(
    admin_router: APIRouter,
    *,
    require_admin,
    agents_dir: str,
) -> None:

    # ── Alle Plugins listen ─────────────────────────────────────────────

    @admin_router.get("/plugins")
    def list_plugins(_a: tuple = Depends(require_admin)):
        manifest_plugins = plugin_manager.list_plugins()
        legacy_plugins = plugin_manager.loaded
        return {
            "plugins":        manifest_plugins,
            "legacy_plugins": legacy_plugins,
            "hooks":          plugin_manager.hook_summary,
            "total":          len(manifest_plugins) + len(legacy_plugins),
        }

    # ── Plugin-Details ──────────────────────────────────────────────────

    @admin_router.get("/plugins/{plugin_id}")
    def get_plugin(plugin_id: str, _a: tuple = Depends(require_admin)):
        lp = plugin_manager.get_plugin(plugin_id)
        if not lp:
            raise HTTPException(404, f"Plugin '{plugin_id}' nicht gefunden")
        # Agents die dieses Plugin nutzen
        from .plugin_manager import _load_state
        state = _load_state()
        agents = state.get("plugins", {}).get(plugin_id, {}).get("agents", [])
        return {
            **lp.info,
            "agents": agents,
        }

    # ── Plugin aktivieren ───────────────────────────────────────────────

    @admin_router.post("/plugins/{plugin_id}/enable")
    def enable_plugin(plugin_id: str, _a: tuple = Depends(require_admin)):
        lp = plugin_manager.get_plugin(plugin_id)
        if not lp:
            raise HTTPException(404, f"Plugin '{plugin_id}' nicht gefunden")
        ok = plugin_manager.enable_plugin(plugin_id)
        lp = plugin_manager.get_plugin(plugin_id)
        return {"ok": ok, "plugin": lp.info if lp else None}

    # ── Plugin deaktivieren ─────────────────────────────────────────────

    @admin_router.post("/plugins/{plugin_id}/disable")
    def disable_plugin(plugin_id: str, _a: tuple = Depends(require_admin)):
        lp = plugin_manager.get_plugin(plugin_id)
        if not lp:
            raise HTTPException(404, f"Plugin '{plugin_id}' nicht gefunden")
        ok = plugin_manager.disable_plugin(plugin_id)
        lp = plugin_manager.get_plugin(plugin_id)
        return {"ok": ok, "plugin": lp.info if lp else None}

    # ── Plugin neu laden ────────────────────────────────────────────────

    @admin_router.post("/plugins/{plugin_id}/reload")
    def reload_plugin(plugin_id: str, _a: tuple = Depends(require_admin)):
        lp = plugin_manager.get_plugin(plugin_id)
        if not lp:
            raise HTTPException(404, f"Plugin '{plugin_id}' nicht gefunden")
        ok = plugin_manager.reload_plugin(plugin_id)
        lp = plugin_manager.get_plugin(plugin_id)
        return {"ok": ok, "plugin": lp.info if lp else None}

    # ── ALLE Plugins neu laden ──────────────────────────────────────────

    @admin_router.post("/plugins/reload")
    def reload_all_plugins(_a: tuple = Depends(require_admin)):
        count = plugin_manager.reload_all(agents_dir)
        logger.info("Plugins neu geladen: %d", count)
        return {
            "reloaded": count,
            "plugins":  plugin_manager.list_plugins(),
            "hooks":    plugin_manager.hook_summary,
        }

    # ── Plugins eines Agents ────────────────────────────────────────────

    @admin_router.get("/plugins/agents/{agent_id}")
    def get_agent_plugins(agent_id: str, _a: tuple = Depends(require_admin)):
        plugin_ids = plugin_manager.get_agent_plugins(agent_id)
        return {
            "agent_id": agent_id,
            "plugins":  plugin_ids,
        }

    # ── Plugin-Zuweisungen setzen ───────────────────────────────────────

    @admin_router.put("/plugins/agents/{agent_id}")
    def set_agent_plugins(
        agent_id: str,
        body: AgentPluginsRequest,
        _a: tuple = Depends(require_admin),
    ):
        plugin_manager.set_agent_plugins(agent_id, body.plugin_ids)
        return {
            "ok": True,
            "agent_id": agent_id,
            "plugins":  body.plugin_ids,
        }
