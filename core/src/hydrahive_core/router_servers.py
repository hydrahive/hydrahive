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

from . import ssh_known_hosts
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


class HostKeyVerifyRequest(BaseModel):
    fingerprint_sha256: str
    action: str  # "approve" | "reject"
    approver: str = "admin"


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

    @admin_router.get("/admin/wks")
    def list_all_wks():
        """#584-A: Cross-User-WKS-Liste für Projekt-Target-Auswahl.
        Admin-only. Liefert whitelisted Felder — keine Keys, keine ssh_key_path."""
        try:
            users = json.loads(settings.users_config.read_text(encoding="utf-8"))
        except Exception:
            users = {}
        wks_keys_dir = settings.wks_keys_dir
        out = []
        for username, udata in sorted(users.items()):
            wks = (udata or {}).get("wks") or {}
            ip = (wks.get("ip") or "").strip()
            # #677: ssh_port backward-compatible (Default 22)
            try:
                wks_ssh_port = int(wks.get("ssh_port") or 22)
                if not (1 <= wks_ssh_port <= 65535):
                    wks_ssh_port = 22
            except (TypeError, ValueError):
                wks_ssh_port = 22
            out.append({
                "username":    username,
                "ip":          ip,
                "ssh_user":    wks.get("ssh_user", username) or username,
                "ssh_port":    wks_ssh_port,
                "configured":  bool(ip),
                "has_ssh_key": (wks_keys_dir / username).exists(),
            })
        return {"wks": out}

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
        # #674-B: Host-Key-Pins gleich mit aufräumen, sonst Orphans im Store
        try:
            ssh_known_hosts.remove_host("server", server_id)
        except Exception as exc:
            logger.warning(
                "ssh_known_hosts.remove_host('server','%s') fehlgeschlagen: %s",
                server_id, type(exc).__name__,
            )
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

        ssh_port = int(srv.get("ssh_port", 22) or 22)
        ssh_user = srv.get("ssh_user", "root")
        ip = srv.get("ip", "")

        response: dict = {}
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-i", use_key,
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "GlobalKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                "-o", "ConnectTimeout=5",
                "-o", "BatchMode=yes",
                "-p", str(ssh_port),
                "-l", ssh_user,
                ip,
                "echo ok",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                response = {"ok": True, "output": stdout.decode().strip()}
            else:
                response = {"ok": False, "error": stderr.decode().strip()[:200]}
        except asyncio.TimeoutError:
            response = {"ok": False, "error": "Timeout (10s)"}
        except Exception as e:
            response = {"ok": False, "error": str(e)[:200]}

        # #674-A: Zusätzlich Host-Keys via ssh-keyscan erfassen. Scan-Fehler
        # kapseln — bestehender SSH-Test bleibt unabhängig davon gültig.
        scan = await ssh_known_hosts.scan_host(ip, ssh_port)
        if scan.get("keys"):
            try:
                ssh_known_hosts.record_scan_result(
                    "server", server_id,
                    ip=ip, ssh_port=ssh_port, ssh_user=ssh_user,
                    scanned_keys=scan["keys"],
                )
            except Exception as exc:
                logger.warning("record_scan_result fehlgeschlagen: %s", type(exc).__name__)
        response["host_keys"] = {
            "scan_error": scan.get("scan_error"),
            "keys": [
                {
                    "algorithm": k["algorithm"],
                    "fingerprint_sha256": k["fingerprint_sha256"],
                }
                for k in scan.get("keys") or []
            ],
        }
        return response

    # ── #674-A: Host-Key-Management für Target-Tools ──────────────────────

    @admin_router.get("/admin/servers/{server_id}/hostkeys")
    def list_server_hostkeys(server_id: str):
        if not _SAFE_ID.match(server_id):
            raise HTTPException(400, "Ungültige Server-ID")
        entry = ssh_known_hosts.get_host_entry("server", server_id)
        if not entry:
            return {
                "server_id": server_id,
                "status": "unknown",
                "host_keys": [],
                "last_checked": None,
                "enforcement_mode": ssh_known_hosts.get_enforcement_mode(),
            }
        return {
            "server_id": server_id,
            "status": entry.get("status", "unknown"),
            "ip": entry.get("ip", ""),
            "ssh_port": entry.get("ssh_port", 22),
            "ssh_user": entry.get("ssh_user", ""),
            "last_checked": entry.get("last_checked"),
            "host_keys": [
                {
                    "fingerprint_sha256": hk["fingerprint_sha256"],
                    "algorithm": hk.get("algorithm", ""),
                    "status": hk.get("status", "unverified"),
                    "verified_at": hk.get("verified_at"),
                    "verified_by": hk.get("verified_by"),
                    "verified_method": hk.get("verified_method"),
                }
                for hk in (entry.get("host_keys") or {}).values()
            ],
            "enforcement_mode": ssh_known_hosts.get_enforcement_mode(),
        }

    @admin_router.post("/admin/servers/{server_id}/verify-hostkey")
    def verify_server_hostkey(server_id: str, req: HostKeyVerifyRequest):
        if not _SAFE_ID.match(server_id):
            raise HTTPException(400, "Ungültige Server-ID")
        action = (req.action or "").strip().lower()
        if action not in ("approve", "reject"):
            raise HTTPException(400, "action muss 'approve' oder 'reject' sein")
        try:
            if action == "approve":
                updated = ssh_known_hosts.approve_key(
                    "server", server_id, req.fingerprint_sha256,
                    approver=req.approver or "admin",
                )
            else:
                updated = ssh_known_hosts.delete_key(
                    "server", server_id, req.fingerprint_sha256,
                )
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not updated:
            raise HTTPException(404, "Host oder Fingerprint nicht gefunden")
        return {"ok": True, "action": action, "status": updated.get("status")}

    @admin_router.delete(
        "/admin/servers/{server_id}/hostkeys/{fingerprint:path}",
        status_code=204,
    )
    def delete_server_hostkey(server_id: str, fingerprint: str):
        if not _SAFE_ID.match(server_id):
            raise HTTPException(400, "Ungültige Server-ID")
        try:
            updated = ssh_known_hosts.delete_key(
                "server", server_id, fingerprint,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not updated:
            raise HTTPException(404, "Host oder Fingerprint nicht gefunden")
        return None

    # ── #686: Orphan-Cleanup für ssh_known_hosts ─────────────────────────

    def _load_known_sets() -> tuple[set[str], set[str]]:
        """Stammdaten für Orphan-Check. fail-closed bei korrupter
        users.json, damit nicht versehentlich alle wks:*-Keys als orphan
        erkannt werden. Fehlende Datei ist harmlos (leeres User-Set)."""
        server_ids = {s["id"] for s in _load_servers() if s.get("id")}
        users_path = settings.users_config
        if not users_path.exists():
            return server_ids, set()
        try:
            users = json.loads(users_path.read_text(encoding="utf-8"))
        except Exception:
            raise HTTPException(
                500,
                "users.json nicht lesbar — Orphan-Cleanup abgebrochen",
            )
        if not isinstance(users, dict):
            raise HTTPException(
                500,
                "users.json hat unerwartetes Format — Orphan-Cleanup abgebrochen",
            )
        return server_ids, set(users.keys())

    @admin_router.get("/admin/hostkeys/orphans")
    def list_hostkey_orphans():
        server_ids, usernames = _load_known_sets()
        orphans = ssh_known_hosts.classify_orphans(server_ids, usernames)
        return {"count": len(orphans), "orphans": orphans}

    @admin_router.delete("/admin/hostkeys/orphans")
    def delete_hostkey_orphans():
        server_ids, usernames = _load_known_sets()
        removed = ssh_known_hosts.remove_orphans(server_ids, usernames)
        if removed:
            logger.info(
                "#686 hostkey orphan cleanup: %d entries removed", len(removed),
            )
        return {"removed_count": len(removed), "removed": removed}

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
