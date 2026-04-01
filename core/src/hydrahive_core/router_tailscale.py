"""
router_tailscale.py — Tailscale Federation Discovery (#111)

GET  /admin/tailscale/devices    → Alle Geräte im Tailnet
POST /admin/tailscale/scan       → HydraHive-Instanzen im Tailnet finden
POST /admin/tailscale/auto-peer  → Gefundene Instanz als A2A-Peer registrieren
GET  /admin/tailscale/status     → Tailscale-Status (eingeloggt, IP, Hostname)
PUT  /admin/tailscale/config     → API-Key konfigurieren
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

TS_CONFIG_FILE = Path("/etc/hydrahive/tailscale.json")
TS_API_BASE = "https://api.tailscale.com/api/v2"


def _load_ts_config() -> dict:
    try:
        return json.loads(TS_CONFIG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_ts_config(cfg: dict) -> None:
    TS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    TS_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _ts_api(path: str, api_key: str, timeout: int = 15) -> Any:
    """Tailscale Admin API Call."""
    url = f"{TS_API_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _check_hydrahive(ip: str, port: int = 80, timeout: float = 3) -> dict | None:
    """Prüft ob unter einer IP eine HydraHive-Instanz läuft."""
    import socket
    for p in [port, 8765]:
        try:
            url = f"http://{ip}:{p}/health"
            req = urllib.request.Request(url, headers={"User-Agent": "HydraHive-Discovery/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode())
                if data.get("service") == "hydrahive-core" or data.get("status") == "ok":
                    return {"ip": ip, "port": p, "health": data}
        except Exception:
            continue
    return None


class TailscaleConfigRequest(BaseModel):
    api_key: str
    tailnet: str = "-"


class AutoPeerRequest(BaseModel):
    hostname: str
    ip: str
    port: int = 80
    name: str | None = None


def register_tailscale_routes(
    admin_router: APIRouter,
    *,
    require_admin,
) -> None:

    @admin_router.get("/admin/tailscale/status")
    async def ts_status(_a=Depends(require_admin)):
        """Tailscale-Status: API konfiguriert? Lokaler Node eingeloggt?"""
        cfg = _load_ts_config()
        has_key = bool(cfg.get("api_key"))

        # Lokaler Tailscale-Status
        import subprocess
        local_status = {"logged_in": False, "ip": None, "hostname": None}
        try:
            r = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                ts = json.loads(r.stdout)
                self_node = ts.get("Self", {})
                local_status = {
                    "logged_in": ts.get("BackendState") == "Running",
                    "ip": self_node.get("TailscaleIPs", [None])[0],
                    "hostname": self_node.get("HostName"),
                    "dns_name": self_node.get("DNSName", "").rstrip("."),
                    "online": self_node.get("Online", False),
                }
        except Exception:
            pass

        return {
            "api_configured": has_key,
            "local": local_status,
        }

    @admin_router.get("/admin/tailscale/devices")
    async def ts_devices(_a=Depends(require_admin)):
        """Alle Geräte im Tailnet listen."""
        cfg = _load_ts_config()
        api_key = cfg.get("api_key")
        if not api_key:
            raise HTTPException(400, "Tailscale API Key nicht konfiguriert")
        tailnet = cfg.get("tailnet", "-")

        try:
            data = await asyncio.to_thread(
                _ts_api, f"/tailnet/{tailnet}/devices", api_key
            )
        except Exception as e:
            raise HTTPException(502, f"Tailscale API Fehler: {e}")

        devices = []
        for dev in data.get("devices", []):
            devices.append({
                "id": dev.get("id"),
                "hostname": dev.get("hostname", ""),
                "name": dev.get("name", ""),
                "ip": dev.get("addresses", [None])[0],
                "os": dev.get("os", ""),
                "online": dev.get("online", False),
                "last_seen": dev.get("lastSeen"),
                "tags": dev.get("tags", []),
            })
        return {"devices": devices, "count": len(devices)}

    @admin_router.post("/admin/tailscale/scan")
    async def ts_scan(_a=Depends(require_admin)):
        """Scannt alle Online-Devices im Tailnet nach HydraHive-Instanzen."""
        cfg = _load_ts_config()
        api_key = cfg.get("api_key")
        if not api_key:
            raise HTTPException(400, "Tailscale API Key nicht konfiguriert")
        tailnet = cfg.get("tailnet", "-")

        # Devices laden
        try:
            data = await asyncio.to_thread(
                _ts_api, f"/tailnet/{tailnet}/devices", api_key
            )
        except Exception as e:
            raise HTTPException(502, f"Tailscale API Fehler: {e}")

        # Eigene IP ermitteln um sich selbst auszuschließen
        import subprocess
        my_ip = None
        try:
            r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                my_ip = r.stdout.strip()
        except Exception:
            pass

        # Parallel alle Devices proben
        found = []
        tasks = []
        for dev in data.get("devices", []):
            ip = dev.get("addresses", [None])[0]
            if not ip or ip == my_ip:
                continue
            if not dev.get("online", False):
                continue
            tasks.append((dev, ip))

        async def probe(dev, ip):
            result = await asyncio.to_thread(_check_hydrahive, ip)
            if result:
                return {
                    "hostname": dev.get("hostname", ""),
                    "name": dev.get("name", ""),
                    "ip": ip,
                    "port": result["port"],
                    "os": dev.get("os", ""),
                    "online": True,
                    "hydrahive": True,
                    "health": result.get("health"),
                }
            return None

        results = await asyncio.gather(*[probe(d, ip) for d, ip in tasks])
        found = [r for r in results if r is not None]

        return {
            "total_devices": len(data.get("devices", [])),
            "online_devices": len(tasks),
            "hydrahive_found": len(found),
            "instances": found,
        }

    @admin_router.post("/admin/tailscale/auto-peer")
    async def ts_auto_peer(req: AutoPeerRequest, _a=Depends(require_admin)):
        """Fügt eine gefundene HydraHive-Instanz als A2A-Peer hinzu."""
        peer_name = req.name or req.hostname
        peer_url = f"http://{req.ip}:{req.port}"

        # A2A-Config laden und Peer hinzufügen
        a2a_config_path = Path("/etc/hydrahive/a2a.json")
        try:
            a2a_cfg = json.loads(a2a_config_path.read_text()) if a2a_config_path.exists() else {}
        except Exception:
            a2a_cfg = {}

        peers = a2a_cfg.get("peers", [])

        # Duplikat-Check
        existing = next((p for p in peers if p.get("name") == peer_name), None)
        if existing:
            existing["url"] = peer_url
        else:
            peers.append({
                "name": peer_name,
                "url": peer_url,
                "secret": "",
                "description": f"Auto-discovered via Tailscale ({req.ip})",
            })
        a2a_cfg["peers"] = peers
        a2a_config_path.write_text(json.dumps(a2a_cfg, indent=2, ensure_ascii=False))

        logger.info("Tailscale Auto-Peer: %s → %s", peer_name, peer_url)
        return {"ok": True, "peer_name": peer_name, "url": peer_url}

    @admin_router.put("/admin/tailscale/config")
    async def ts_config(req: TailscaleConfigRequest, _a=Depends(require_admin)):
        """Tailscale API-Key speichern."""
        # Validieren: API-Key testen
        try:
            await asyncio.to_thread(
                _ts_api, f"/tailnet/{req.tailnet}/devices?fields=id", req.api_key, 10
            )
        except Exception as e:
            raise HTTPException(400, f"API-Key ungültig: {e}")

        _save_ts_config({"api_key": req.api_key, "tailnet": req.tailnet})
        return {"ok": True}
