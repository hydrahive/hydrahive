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
import urllib.error
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


def _check_hydrahive(ip: str, timeout: float = 5) -> dict | None:
    """Prüft ob unter einer IP eine HydraHive-Instanz läuft.

    Probiert die wahrscheinlichsten Kombinationen zuerst:
    HTTPS:443/api/health ist Standard für nginx-Proxy-Setup.
    """
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # Reihenfolge: wahrscheinlichste zuerst
    probes = [
        ("https", 443, "/api/health"),
        ("https", 443, "/health"),
        ("http", 80, "/api/health"),
        ("http", 80, "/health"),
        ("http", 8765, "/health"),
    ]
    for scheme, port, path in probes:
        try:
            url = f"{scheme}://{ip}:{port}{path}"
            req = urllib.request.Request(url, headers={"User-Agent": "HydraHive-Discovery/1.0"})
            kw: dict = {"timeout": timeout}
            if scheme == "https":
                kw["context"] = ctx
            with urllib.request.urlopen(req, **kw) as r:
                data = json.loads(r.read().decode())
                if data.get("service") == "hydrahive-core" or data.get("status") == "ok":
                    return {"ip": ip, "port": port, "scheme": scheme, "health": data}
        except Exception as e:
            logger.debug("Probe %s://%s:%s failed: %s", scheme, ip, port, e)
            continue
    return None


class TailscaleConfigRequest(BaseModel):
    api_key: str
    tailnet: str = "-"


class AutoPeerRequest(BaseModel):
    hostname: str
    ip: str
    port: int = 80
    scheme: str = "https"
    name: str | None = None


class TailscaleConnectRequest(BaseModel):
    auth_key: str
    hostname: str | None = None


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
            r = await asyncio.to_thread(lambda: subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True, text=True, timeout=5,
            ))
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
        except Exception as e:
            logger.debug("Failed to get tailscale local status: %s", e)

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
                "online": dev.get("online"),  # kann null sein
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
            r = await asyncio.to_thread(lambda: subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5))
            if r.returncode == 0:
                my_ip = r.stdout.strip()
        except Exception as e:
            logger.debug("Failed to get own tailscale IP: %s", e)

        # Parallel alle Devices proben (online-Status kann null sein bei manchen Plänen)
        found = []
        tasks = []
        for dev in data.get("devices", []):
            ip = dev.get("addresses", [None])[0]
            if not ip or ip == my_ip:
                continue
            # online kann True, False oder null sein — bei null trotzdem proben
            if dev.get("online") is False:
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
                    "scheme": result.get("scheme", "http"),
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
        peer_url = f"{req.scheme}://{req.ip}:{req.port}"

        # A2A-Config laden und Peer hinzufügen
        a2a_config_path = Path("/etc/hydrahive/a2a_peers.json")
        try:
            a2a_cfg = json.loads(a2a_config_path.read_text()) if a2a_config_path.exists() else {}
        except Exception as e:
            logger.debug("Failed to load a2a_peers.json: %s", e)
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

    @admin_router.delete("/admin/tailscale/devices/{device_id}")
    async def ts_remove_device(device_id: str, _a=Depends(require_admin)):
        """Entfernt ein Gerät aus dem Tailnet + zugehörigen A2A-Peer."""
        cfg = _load_ts_config()
        api_key = cfg.get("api_key")
        if not api_key:
            raise HTTPException(400, "Tailscale API Key nicht konfiguriert")

        # Device-Info holen (Hostname + IP) für A2A-Cleanup
        device_hostname = ""
        device_ip = ""
        try:
            dev_data = await asyncio.to_thread(
                _ts_api, f"/device/{device_id}", api_key
            )
            device_hostname = dev_data.get("hostname", "")
            device_ip = (dev_data.get("addresses") or [None])[0] or ""
        except Exception as e:
            logger.debug("Failed to get device info for %s: %s", device_id, e)

        # Device aus Tailnet löschen
        try:
            req = urllib.request.Request(
                f"{TS_API_BASE}/device/{device_id}",
                method="DELETE",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            await asyncio.to_thread(urllib.request.urlopen, req, timeout=10)
        except urllib.error.HTTPError as e:
            raise HTTPException(e.code, f"Tailscale API: {e.reason}")
        except Exception as e:
            raise HTTPException(502, f"Fehler: {e}")

        # Zugehörigen A2A-Peer entfernen
        removed_peer = None
        a2a_path = Path("/etc/hydrahive/a2a_peers.json")
        try:
            if a2a_path.exists():
                a2a_cfg = json.loads(a2a_path.read_text())
                peers = a2a_cfg.get("peers", [])
                before = len(peers)
                peers = [p for p in peers
                         if p.get("name") != device_hostname
                         and device_ip not in p.get("url", "")]
                if len(peers) < before:
                    removed_peer = device_hostname or device_ip
                    a2a_cfg["peers"] = peers
                    a2a_path.write_text(json.dumps(a2a_cfg, indent=2, ensure_ascii=False))
                    logger.info("A2A-Peer '%s' entfernt (Tailscale Device gelöscht)", removed_peer)
        except Exception as e:
            logger.warning("A2A-Peer-Cleanup fehlgeschlagen: %s", e)

        return {"ok": True, "deleted": device_id, "removed_peer": removed_peer}

    @admin_router.post("/admin/tailscale/connect")
    async def ts_connect(req: TailscaleConnectRequest, _a=Depends(require_admin)):
        """Verbindet diesen Server mit dem Tailnet via Auth Key."""
        import subprocess
        hostname = req.hostname
        if not hostname:
            try:
                import socket
                hostname = f"hydrahive-{socket.gethostname()}"
            except Exception as e:
                logger.debug("Failed to get hostname for tailscale: %s", e)
                hostname = "hydrahive"
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["sudo", "tailscale", "up", f"--auth-key={req.auth_key}", f"--hostname={hostname}", "--reset"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                raise HTTPException(500, f"tailscale up fehlgeschlagen: {result.stderr.strip()[-300:]}")
            # Status abfragen
            await asyncio.sleep(2)
            r2 = await asyncio.to_thread(lambda: subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5))
            ip = r2.stdout.strip() if r2.returncode == 0 else None
            return {"ok": True, "ip": ip, "hostname": hostname}
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Timeout beim Verbinden")

    @admin_router.post("/admin/tailscale/disconnect")
    async def ts_disconnect(_a=Depends(require_admin)):
        """Trennt diesen Server vom Tailnet."""
        import subprocess
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["sudo", "tailscale", "down"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                raise HTTPException(500, f"tailscale down fehlgeschlagen: {result.stderr.strip()}")
            return {"ok": True}
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Timeout beim Trennen")

    @admin_router.post("/admin/tailscale/invite")
    async def ts_invite(_a=Depends(require_admin)):
        """Generiert einen einmaligen Pre-Auth Key zum Einladen eines externen Geräts."""
        cfg = _load_ts_config()
        api_key = cfg.get("api_key")
        if not api_key:
            raise HTTPException(400, "Tailscale API Key nicht konfiguriert")
        tailnet = cfg.get("tailnet", "-")

        try:
            payload = json.dumps({
                "capabilities": {
                    "devices": {
                        "create": {
                            "reusable": False,
                            "ephemeral": False,
                            "preauthorized": True,
                        }
                    }
                },
                "expirySeconds": 86400,  # 24h gültig
            }).encode()
            req = urllib.request.Request(
                f"{TS_API_BASE}/tailnet/{tailnet}/keys",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            data = await asyncio.to_thread(
                lambda: json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
            )
            return {
                "ok": True,
                "auth_key": data.get("key", ""),
                "expires": data.get("expires", ""),
            }
        except Exception as e:
            raise HTTPException(502, f"Auth Key konnte nicht generiert werden: {e}")

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
