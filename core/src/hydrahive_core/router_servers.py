"""
router_servers.py — Zentrale Server/WKS-Verwaltung (#342)

Server registrieren, SSH-Keys verwalten, Agents zuweisen.

GET    /admin/servers              → Alle Server listen
POST   /admin/servers              → Server anlegen
PUT    /admin/servers/{id}         → Server bearbeiten
DELETE /admin/servers/{id}         → Server löschen
GET    /admin/servers/{id}/test    → SSH-Verbindung testen
GET    /agents/{agent_id}/servers  → Zugewiesene Server eines Agents
PUT    /agents/{agent_id}/servers  → Server-Zuweisung setzen
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from .settings import settings

logger = logging.getLogger(__name__)

SERVERS_DIR = settings.servers_dir
SERVERS_KEYS_DIR = settings.server_keys_dir
AGENT_SERVERS_FILE = settings.agent_servers_config

_SAFE_ID = re.compile(r"^[a-z0-9_-]+$")


class AgentServersRequest(BaseModel):
    server_ids: list[str]


class ServerRequest(BaseModel):
    id: str = ""
    name: str
    ip: str
    ssh_user: str = "root"
    ssh_port: int = 22
    description: str = ""
    use_wks_key: bool = False


def _load_servers() -> list[dict]:
    SERVERS_DIR.mkdir(parents=True, exist_ok=True)
    servers = []
    for f in sorted(SERVERS_DIR.glob("*.json")):
        try:
            servers.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return servers


def _save_server(srv: dict) -> None:
    SERVERS_DIR.mkdir(parents=True, exist_ok=True)
    p = SERVERS_DIR / f"{srv['id']}.json"
    p.write_text(json.dumps(srv, indent=2, ensure_ascii=False), encoding="utf-8")
    p.chmod(0o600)


def _load_agent_servers() -> dict[str, list[str]]:
    if not AGENT_SERVERS_FILE.exists():
        return {}
    try:
        return json.loads(AGENT_SERVERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_agent_servers(data: dict[str, list[str]]) -> None:
    AGENT_SERVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    AGENT_SERVERS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    AGENT_SERVERS_FILE.chmod(0o600)


def register_server_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    require_auth,
    require_admin,
) -> None:

    @admin_router.get("/admin/servers")
    def list_servers():
        servers = _load_servers()
        for srv in servers:
            key_path = SERVERS_KEYS_DIR / srv["id"]
            srv["has_ssh_key"] = key_path.exists()
            srv.pop("ssh_key", None)  # Key nie im Response
        return {"servers": servers}

    @admin_router.post("/admin/servers", status_code=201)
    def create_server(req: ServerRequest):
        sid = req.id or req.name.lower().replace(" ", "-").replace(".", "-")
        sid = re.sub(r"[^a-z0-9_-]", "", sid)[:30]
        if not sid:
            raise HTTPException(400, "Ungültige Server-ID")
        if (SERVERS_DIR / f"{sid}.json").exists():
            raise HTTPException(409, f"Server '{sid}' existiert bereits")

        srv = {
            "id": sid,
            "name": req.name,
            "ip": req.ip,
            "ssh_user": req.ssh_user,
            "ssh_port": req.ssh_port,
            "description": req.description,
        }
        _save_server(srv)

        # SSH-Key: bestehenden WKS-Key verlinken oder neuen generieren
        key_path = SERVERS_KEYS_DIR / sid
        SERVERS_KEYS_DIR.mkdir(parents=True, exist_ok=True)
        if req.use_wks_key:
            # WKS-Key des eingeloggten Users kopieren
            wks_keys_dir = settings.wks_keys_dir
            wks_key = None
            # Versuche: Username aus Auth, dann "admin", dann erster vorhandener Key
            for candidate in [req.id.split("-")[-1] if "-" in (req.id or "") else "", "admin"]:
                if candidate and (wks_keys_dir / candidate).exists():
                    wks_key = wks_keys_dir / candidate
                    break
            if not wks_key and wks_keys_dir.exists():
                for f in sorted(wks_keys_dir.iterdir()):
                    if f.is_file() and not f.name.endswith(".pub"):
                        wks_key = f
                        break
            if wks_key and wks_key.exists():
                import shutil
                shutil.copy2(str(wks_key), str(key_path))
                pub = Path(f"{wks_key}.pub")
                if pub.exists():
                    shutil.copy2(str(pub), f"{key_path}.pub")
                key_path.chmod(0o600)
                logger.info("Server '%s': WKS-Key kopiert von %s", sid, wks_key)
            else:
                logger.warning("Kein WKS-Key gefunden — generiere neuen Key")
                req.use_wks_key = False  # Fallback

        if not req.use_wks_key and not key_path.exists():
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", f"hydrahive-server-{sid}"],
                capture_output=True, timeout=10,
            )
            key_path.chmod(0o600)

        pub_key = ""
        pub_path = Path(f"{key_path}.pub")
        if pub_path.exists():
            pub_key = pub_path.read_text().strip()

        logger.info("Server angelegt: %s (%s@%s)", sid, req.ssh_user, req.ip)
        return {"created": True, "server_id": sid, "public_key": pub_key}

    @admin_router.put("/admin/servers/{server_id}")
    def update_server(server_id: str, req: ServerRequest):
        if not _SAFE_ID.match(server_id):
            raise HTTPException(400, "Ungültige Server-ID")
        p = SERVERS_DIR / f"{server_id}.json"
        if not p.exists():
            raise HTTPException(404, f"Server '{server_id}' nicht gefunden")
        srv = json.loads(p.read_text())
        srv.update({
            "name": req.name or srv.get("name", ""),
            "ip": req.ip or srv.get("ip", ""),
            "ssh_user": req.ssh_user or srv.get("ssh_user", "root"),
            "ssh_port": req.ssh_port or srv.get("ssh_port", 22),
            "description": req.description if req.description else srv.get("description", ""),
        })
        _save_server(srv)
        return {"updated": True, "server_id": server_id}

    @admin_router.delete("/admin/servers/{server_id}")
    def delete_server(server_id: str):
        if not _SAFE_ID.match(server_id):
            raise HTTPException(400, "Ungültige Server-ID")
        p = SERVERS_DIR / f"{server_id}.json"
        if not p.exists():
            raise HTTPException(404, f"Server '{server_id}' nicht gefunden")
        p.unlink()
        # Key behalten (für den Fall dass er noch gebraucht wird)
        # Agent-Zuweisungen aufräumen
        agent_servers = _load_agent_servers()
        for aid in list(agent_servers.keys()):
            agent_servers[aid] = [s for s in agent_servers[aid] if s != server_id]
        _save_agent_servers(agent_servers)
        return {"deleted": True, "server_id": server_id}

    @admin_router.get("/admin/servers/{server_id}/test")
    async def test_server(server_id: str):
        if not _SAFE_ID.match(server_id):
            raise HTTPException(400, "Ungültige Server-ID")
        p = SERVERS_DIR / f"{server_id}.json"
        if not p.exists():
            raise HTTPException(404, f"Server '{server_id}' nicht gefunden")
        srv = json.loads(p.read_text())
        # Server-eigenen Key oder WKS-Key des Users als Fallback
        key_path = SERVERS_KEYS_DIR / server_id
        wks_key = settings.wks_keys_dir / "admin"
        if not key_path.exists() and not wks_key.exists():
            return {"ok": False, "error": "Kein SSH-Key vorhanden"}
        use_key = str(key_path) if key_path.exists() else str(wks_key)
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-i", use_key,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=5",
                "-o", "BatchMode=yes",
                "-p", str(srv.get("ssh_port", 22)),
                "-l", srv["ssh_user"],
                srv["ip"],
                "echo ok",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                return {"ok": True, "output": stdout.decode().strip()}
            return {"ok": False, "error": stderr.decode().strip()[:200]}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Timeout (10s)"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @admin_router.get("/admin/servers/{server_id}/pubkey")
    def get_server_pubkey(server_id: str):
        if not _SAFE_ID.match(server_id):
            raise HTTPException(400, "Ungültige Server-ID")
        pub_path = SERVERS_KEYS_DIR / f"{server_id}.pub"
        if not pub_path.exists():
            raise HTTPException(404, "Kein SSH-Key vorhanden")
        return {"public_key": pub_path.read_text().strip()}

    # --- Agent-Server-Zuweisung ---

    @auth_router.get("/agents/{agent_id}/servers")
    def get_agent_servers(agent_id: str, _a=Depends(require_auth)):
        agent_servers = _load_agent_servers()
        assigned = agent_servers.get(agent_id, [])
        all_servers = {s["id"]: s for s in _load_servers()}
        result = []
        for sid in assigned:
            if sid in all_servers:
                srv = all_servers[sid]
                result.append({"id": sid, "name": srv.get("name", sid), "ip": srv.get("ip", "")})
        return {"agent_id": agent_id, "servers": result}

    @admin_router.put("/agents/{agent_id}/servers")
    def set_agent_servers(agent_id: str, req: AgentServersRequest):
        server_ids = req.server_ids
        # Validieren dass Server existieren
        existing = {s["id"] for s in _load_servers()}
        for sid in server_ids:
            if sid not in existing:
                raise HTTPException(404, f"Server '{sid}' nicht gefunden")
        agent_servers = _load_agent_servers()
        agent_servers[agent_id] = server_ids
        _save_agent_servers(agent_servers)
        return {"updated": True, "agent_id": agent_id, "server_ids": server_ids}


def get_server_for_agent(agent_id: str, server_id: str) -> dict | None:
    """Helper für Tool-Registry: Server-Config + Key-Pfad für einen Agent."""
    agent_servers = _load_agent_servers()
    if server_id not in agent_servers.get(agent_id, []):
        return None
    p = SERVERS_DIR / f"{server_id}.json"
    if not p.exists():
        return None
    srv = json.loads(p.read_text())
    key_path = SERVERS_KEYS_DIR / server_id
    srv["ssh_key_path"] = str(key_path) if key_path.exists() else None
    return srv
