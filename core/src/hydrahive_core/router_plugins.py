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
    auth_router: APIRouter | None = None,
    *,
    require_admin,
    require_auth=None,
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

    # ── User Apps (Plugins mit UI-Tab) ─────────────────────────────────

    if auth_router and require_auth:
        import json as _json
        from pathlib import Path as _P
        from fastapi import Body as _Body

        _USER_APP_CFG_DIR = _P("/etc/hydrahive/user_app_config")

        def _load_user_app_config(username: str, app_id: str) -> dict:
            path = _USER_APP_CFG_DIR / username / f"{app_id}.json"
            try:
                return _json.loads(path.read_text(encoding="utf-8"))
            except (OSError, _json.JSONDecodeError):
                return {}

        def _save_user_app_config(username: str, app_id: str, data: dict) -> None:
            user_dir = _USER_APP_CFG_DIR / username
            user_dir.mkdir(parents=True, exist_ok=True)
            path = user_dir / f"{app_id}.json"
            path.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            import os; os.chmod(path, 0o600)

        @auth_router.get("/me/user-apps")
        def list_user_apps(auth: tuple = Depends(require_auth)):
            """Nur Plugins mit UI-Tab die dem persönlichen Agent zugewiesen sind."""
            username, _ = auth
            # Prüfe welche Plugins dem persönlichen Agent zugewiesen sind
            agent_id = f"personal_{username}"
            assigned_plugins = set(plugin_manager.get_agent_plugins(agent_id))
            apps = []
            for pid, lp in plugin_manager._plugins.items():
                ui = lp.manifest.ui
                if ui and ui.get("tab") and pid in assigned_plugins:
                    apps.append({
                        "id": pid,
                        "name": lp.manifest.name,
                        "description": lp.manifest.description,
                        "version": lp.manifest.version,
                        "tab": ui["tab"],
                        "config_fields": ui.get("config_fields", []),
                        "config": _load_user_app_config(username, pid),
                        "enabled": lp.enabled,
                    })
            apps.sort(key=lambda a: a["tab"].get("order", 100))
            return {"apps": apps}

        @auth_router.get("/me/user-apps/{app_id}")
        def get_user_app(app_id: str, auth: tuple = Depends(require_auth)):
            """Config eines User-App Plugins."""
            username, _ = auth
            lp = plugin_manager._plugins.get(app_id)
            if not lp or not lp.manifest.ui.get("tab"):
                raise HTTPException(404, f"User-App '{app_id}' nicht gefunden")
            return {
                "id": app_id,
                "name": lp.manifest.name,
                "config_fields": lp.manifest.ui.get("config_fields", []),
                "config": _load_user_app_config(username, app_id),
            }

        @auth_router.put("/me/user-apps/{app_id}/config")
        def save_user_app_config(app_id: str, body: dict = _Body(...), auth: tuple = Depends(require_auth)):
            """User-spezifische App-Konfiguration speichern."""
            username, _ = auth
            lp = plugin_manager._plugins.get(app_id)
            if not lp or not lp.manifest.ui.get("tab"):
                raise HTTPException(404, f"User-App '{app_id}' nicht gefunden")
            _save_user_app_config(username, app_id, body)
            return {"saved": True, "app_id": app_id}

        @auth_router.get("/me/user-apps/dexcom-monitor/glucose")
        async def get_dexcom_glucose(minutes: int = 30, count: int = 6, auth: tuple = Depends(require_auth)):
            """Dexcom Glukosewerte direkt abrufen (ohne Agent-Message)."""
            username, _ = auth
            cfg = _load_user_app_config(username, "dexcom-monitor")
            if not cfg.get("dexcom_username") or not cfg.get("dexcom_password"):
                raise HTTPException(400, "Dexcom nicht konfiguriert")

            try:
                import httpx

                region = cfg.get("dexcom_region", "eu")
                dexcom_base = {
                    "us": "https://share2.dexcom.com/ShareWebServices/Services",
                    "eu": "https://shareous1.dexcom.com/ShareWebServices/Services",
                }
                base = dexcom_base.get(region, dexcom_base["eu"])
                app_id_dexcom = "d89443d2-327c-4a6f-89e5-496bbb0317db"

                # Login
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(f"{base}/General/LoginPublisherAccountByName", json={
                        "accountName": cfg["dexcom_username"],
                        "password": cfg["dexcom_password"],
                        "applicationId": app_id_dexcom,
                    })
                if r.status_code != 200:
                    raise HTTPException(502, f"Dexcom Login fehlgeschlagen (HTTP {r.status_code})")
                session_id = r.text.strip('"')

                # Readings
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(f"{base}/Publisher/ReadPublisherLatestGlucoseValues", params={
                        "sessionId": session_id, "minutes": minutes, "maxCount": count,
                    })
                if r.status_code != 200:
                    raise HTTPException(502, "Dexcom Readings fehlgeschlagen")

                trend_arrows = {0:"?",1:"↑↑",2:"↑",3:"↗",4:"→",5:"↘",6:"↓",7:"↓↓",8:"?",9:"?"}
                readings = []
                for rd in r.json():
                    ts_str = rd.get("WT", "")
                    ts_ms = int(ts_str.replace("/Date(", "").replace(")/", "")) if "Date" in ts_str else 0
                    readings.append({
                        "value": rd.get("Value", 0),
                        "trend": rd.get("Trend", 0),
                        "trend_arrow": trend_arrows.get(rd.get("Trend", 0), "?"),
                        "timestamp": ts_ms // 1000 if ts_ms else 0,
                    })

                if not readings:
                    return {"current": None, "readings": [], "alert_thresholds": {"low": cfg.get("alert_low", 70), "high": cfg.get("alert_high", 250)}}

                current = readings[0]
                alert_low = cfg.get("alert_low", 70)
                alert_high = cfg.get("alert_high", 250)
                status = "normal"
                if current["value"] < alert_low: status = "NIEDRIG"
                elif current["value"] > alert_high: status = "HOCH"

                return {
                    "current": {"value": current["value"], "unit": "mg/dL", "trend": current["trend_arrow"], "status": status},
                    "readings": readings,
                    "alert_thresholds": {"low": alert_low, "high": alert_high},
                }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"Dexcom API Fehler: {e}")
