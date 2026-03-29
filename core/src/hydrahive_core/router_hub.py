"""router_hub.py — HydraHub API

GET  /hub/index           → Index vom GitHub-Repo (gecacht 5 Min)
POST /hub/install         → Agent aus Hub installieren
GET  /hub/installed       → Liste aller installierten Hub-Pakete
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

HUB_INDEX_URL = "https://raw.githubusercontent.com/hydrahive/hub/main/index.json"
HUB_RAW_BASE  = "https://raw.githubusercontent.com/hydrahive/hub/main"
CACHE_TTL     = 300  # 5 Minuten

_index_cache: dict[str, Any] = {}
_cache_ts: float = 0.0


def _fetch_text(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "HydraHive/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _get_index() -> dict[str, Any]:
    global _index_cache, _cache_ts
    if time.time() - _cache_ts < CACHE_TTL and _index_cache:
        return _index_cache
    try:
        raw = _fetch_text(HUB_INDEX_URL)
        _index_cache = json.loads(raw)
        _cache_ts = time.time()
    except Exception as e:
        logger.warning("Hub-Index konnte nicht geladen werden: %s", e)
        if not _index_cache:
            raise HTTPException(503, f"Hub nicht erreichbar: {e}")
    return _index_cache


class InstallRequest(BaseModel):
    id: str
    agent_id_override: str | None = None   # optionale eigene Agent-ID
    model_override: str | None = None


def register_hub_routes(router: APIRouter, require_admin, agents_dir: str, discovery=None) -> None:

    @router.get("/hub/index")
    async def hub_index(_auth=Depends(require_admin)):
        return _get_index()

    @router.get("/hub/installed")
    async def hub_installed(_auth=Depends(require_admin)):
        """Gibt zurück welche Hub-Pakete bereits installiert sind."""
        installed = []
        base = Path(agents_dir)
        if not base.exists():
            return installed
        for agent_dir in base.iterdir():
            meta_path = agent_dir / ".hub_meta.json"
            if meta_path.exists():
                try:
                    installed.append(json.loads(meta_path.read_text()))
                except Exception:
                    pass
        return installed

    @router.post("/hub/install")
    async def hub_install(req: InstallRequest, _auth=Depends(require_admin)):
        index = _get_index()
        pkg = next((p for p in index.get("packages", []) if p["id"] == req.id), None)
        if not pkg:
            raise HTTPException(404, f"Paket '{req.id}' nicht im Hub gefunden")

        pkg_path = pkg.get("_path", "")
        if not pkg_path:
            raise HTTPException(500, "Paket hat keinen _path — Index beschädigt?")

        # Dateien vom Hub laden
        try:
            soul_md   = _fetch_text(f"{HUB_RAW_BASE}/{pkg_path}/soul.md")
            agent_yaml_raw = _fetch_text(f"{HUB_RAW_BASE}/{pkg_path}/agent.yaml")
        except Exception as e:
            raise HTTPException(502, f"Hub-Dateien konnten nicht geladen werden: {e}")

        # agent.yaml parsen und ggf. überschreiben
        try:
            agent_cfg = yaml.safe_load(agent_yaml_raw) or {}
        except Exception as e:
            raise HTTPException(500, f"agent.yaml ungültig: {e}")

        agent_id = req.agent_id_override or agent_cfg.get("agent_id") or req.id
        # Sicherstellen dass agent_id nur erlaubte Zeichen enthält
        import re
        if not re.match(r'^[a-z0-9_-]{1,64}$', agent_id):
            raise HTTPException(400, "agent_id enthält unerlaubte Zeichen")

        agent_cfg["agent_id"] = agent_id
        if "type" not in agent_cfg:
            agent_cfg["type"] = "specialist"
        if req.model_override:
            agent_cfg["model"] = req.model_override

        # Zielverzeichnis anlegen
        target = Path(agents_dir) / agent_id
        if target.exists():
            raise HTTPException(409, f"Agent '{agent_id}' existiert bereits")

        target.mkdir(parents=True, exist_ok=True)
        (target / "memory").mkdir(exist_ok=True)

        # Dateien schreiben
        (target / "soul.md").write_text(soul_md, encoding="utf-8")
        (target / "agent.yaml").write_text(
            yaml.dump(agent_cfg, allow_unicode=True, default_flow_style=False),
            encoding="utf-8"
        )
        # Hub-Metadaten für "installed"-Tracking speichern
        hub_meta = {**pkg, "installed_agent_id": agent_id}
        (target / ".hub_meta.json").write_text(
            json.dumps(hub_meta, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # Berechtigungen setzen (hydrahive user)
        try:
            import subprocess
            subprocess.run(
                ["chown", "-R", "hydrahive:hydrahive", str(target)],
                check=False, capture_output=True
            )
        except Exception:
            pass

        # Agent sofort in discovery registrieren (kein Neustart nötig)
        if discovery is not None:
            try:
                discovery._register(target)
            except Exception as e:
                logger.warning("discovery._register fehlgeschlagen: %s", e)

        logger.info("Hub-Agent '%s' installiert (Paket: %s)", agent_id, req.id)
        return {
            "installed": True,
            "agent_id": agent_id,
            "name": pkg.get("name"),
            "category": pkg.get("category"),
        }

    @router.delete("/hub/installed/{agent_id}")
    async def hub_uninstall(agent_id: str, _auth=Depends(require_admin)):
        """Entfernt einen Hub-Agenten (nur wenn via Hub installiert)."""
        import re
        if not re.match(r'^[a-z0-9_-]{1,64}$', agent_id):
            raise HTTPException(400, "Ungültige agent_id")

        target = Path(agents_dir) / agent_id
        if not target.exists():
            raise HTTPException(404, "Agent nicht gefunden")
        if not (target / ".hub_meta.json").exists():
            raise HTTPException(403, "Dieser Agent wurde nicht über den Hub installiert")

        import shutil
        shutil.rmtree(target)
        if discovery is not None:
            try:
                discovery._unregister_dir(target)
            except Exception as e:
                logger.warning("discovery._unregister_dir fehlgeschlagen: %s", e)
        logger.info("Hub-Agent '%s' deinstalliert", agent_id)
        return {"uninstalled": True, "agent_id": agent_id}
