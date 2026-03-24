"""
router_vpn.py — VPN-Verwaltung (Tailscale / Headscale)

Endpoints:
  GET  /admin/vpn/status   — aktueller VPN-Status + Peers
  POST /admin/vpn/connect  — Auth-Key setzen und tailscale up ausführen
  POST /admin/vpn/down     — tailscale down
  GET  /admin/vpn/peers    — verbundene Peers
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

VPN_CONFIG = Path("/etc/hydrahive/vpn.json")


# ── Config-Helpers ─────────────────────────────────────────────────────────────

def _load_vpn_config() -> dict:
    try:
        return json.loads(VPN_CONFIG.read_text()) if VPN_CONFIG.exists() else {}
    except Exception:
        return {}


def _save_vpn_config(cfg: dict) -> None:
    VPN_CONFIG.write_text(json.dumps(cfg, indent=2))
    VPN_CONFIG.chmod(0o600)


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Führt einen Shell-Befehl aus, gibt (returncode, stdout, stderr) zurück."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", f"{cmd[0]} nicht gefunden"


def _tailscale_status() -> dict:
    """Gibt den aktuellen tailscale status als Dict zurück."""
    rc, out, err = _run(["tailscale", "status", "--json"])
    if rc != 0:
        return {}
    try:
        return json.loads(out)
    except Exception:
        return {}


def _get_tailscale_ip() -> str | None:
    rc, out, _ = _run(["tailscale", "ip", "--4"])
    return out if rc == 0 and out else None


# ── Router ─────────────────────────────────────────────────────────────────────

def register_vpn_routes(admin_router: APIRouter, require_admin) -> None:

    class ConnectRequest(BaseModel):
        auth_key: str
        login_server: str | None = None  # None = Tailscale Standard
        hostname:     str | None = None

    @admin_router.get("/vpn/status")
    async def vpn_status(_=require_admin):
        cfg = _load_vpn_config()
        mode = cfg.get("mode", "none")

        if mode == "none":
            return {"mode": "none", "configured": False, "connected": False}

        # Tailscale installiert?
        rc, _, _ = _run(["which", "tailscale"])
        if rc != 0:
            return {"mode": mode, "configured": False, "connected": False,
                    "error": "tailscale nicht installiert"}

        ts = _tailscale_status()
        tailscale_ip = _get_tailscale_ip()
        backend_state = ts.get("BackendState", "unknown")
        connected = backend_state == "Running"

        # Peers aufbereiten
        peers = []
        for peer_id, peer in (ts.get("Peer") or {}).items():
            peers.append({
                "id":       peer_id[:8],
                "hostname": peer.get("HostName", ""),
                "ip":       (peer.get("TailscaleIPs") or [""])[0],
                "online":   peer.get("Online", False),
                "os":       peer.get("OS", ""),
            })

        # Headscale-Status zusätzlich
        headscale_running = False
        if mode == "headscale":
            hs_rc, _, _ = _run(["systemctl", "is-active", "headscale"])
            headscale_running = hs_rc == 0

        return {
            "mode":              mode,
            "configured":        cfg.get("configured", False),
            "connected":         connected,
            "backend_state":     backend_state,
            "tailscale_ip":      tailscale_ip,
            "login_server":      cfg.get("login_server", ""),
            "hostname":          cfg.get("hostname", ""),
            "peers":             peers,
            "headscale_running": headscale_running if mode == "headscale" else None,
        }

    @admin_router.post("/vpn/connect")
    async def vpn_connect(body: ConnectRequest, _=require_admin):
        cfg = _load_vpn_config()
        mode = cfg.get("mode", "tailscale")

        if mode == "none":
            raise HTTPException(400, "VPN nicht installiert — Installer erneut ausführen")

        # Auth-Key speichern
        cfg["auth_key"]     = body.auth_key
        cfg["configured"]   = True
        if body.login_server:
            cfg["login_server"] = body.login_server
        if body.hostname:
            cfg["hostname"] = body.hostname
        _save_vpn_config(cfg)

        # tailscale up bauen
        cmd = ["tailscale", "up", f"--authkey={body.auth_key}", "--accept-routes"]
        login_server = cfg.get("login_server", "")
        if login_server and "tailscale.com" not in login_server:
            cmd.append(f"--login-server={login_server}")
        if cfg.get("hostname"):
            cmd.append(f"--hostname={cfg['hostname']}")

        rc, out, err = _run(cmd, timeout=30)
        if rc != 0:
            raise HTTPException(500, f"tailscale up fehlgeschlagen: {err or out}")

        tailscale_ip = _get_tailscale_ip()
        if tailscale_ip:
            cfg["tailscale_ip"] = tailscale_ip
            _save_vpn_config(cfg)

        return {"connected": True, "tailscale_ip": tailscale_ip, "mode": mode}

    @admin_router.post("/vpn/down")
    async def vpn_down(_=require_admin):
        rc, out, err = _run(["tailscale", "down"])
        if rc != 0:
            raise HTTPException(500, f"tailscale down fehlgeschlagen: {err or out}")
        cfg = _load_vpn_config()
        cfg["configured"] = False
        cfg["tailscale_ip"] = ""
        _save_vpn_config(cfg)
        return {"disconnected": True}

    @admin_router.get("/vpn/peers")
    async def vpn_peers(_=require_admin):
        ts = _tailscale_status()
        peers = []
        for peer_id, peer in (ts.get("Peer") or {}).items():
            peers.append({
                "id":       peer_id[:8],
                "hostname": peer.get("HostName", ""),
                "ip":       (peer.get("TailscaleIPs") or [""])[0],
                "online":   peer.get("Online", False),
                "os":       peer.get("OS", ""),
                "last_seen": peer.get("LastSeen", ""),
            })
        return {"peers": peers, "count": len(peers)}

    @admin_router.post("/vpn/headscale/authkey")
    async def headscale_create_authkey(_=require_admin):
        """Erstellt einen neuen Headscale Auth-Key für neue Nodes."""
        cfg = _load_vpn_config()
        if cfg.get("mode") != "headscale":
            raise HTTPException(400, "Nur im Headscale-Modus verfügbar")

        rc, out, err = _run(
            ["headscale", "preauthkeys", "create", "--user", "hydrahive",
             "--reusable", "--expiration", "90d"],
            timeout=10,
        )
        if rc != 0:
            raise HTTPException(500, f"Headscale Auth-Key Fehler: {err or out}")

        # Letzte Zeile enthält den Key
        key = out.strip().splitlines()[-1].strip()
        return {"auth_key": key, "expiration": "90d", "reusable": True}
