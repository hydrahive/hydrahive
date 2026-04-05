"""
router_a2a.py — FastA2A: HydraHive-zu-HydraHive Kommunikation (#50)

Endpunkte:
  GET  /.well-known/agent.json        → Agent Card (öffentlich)
  POST /a2a/tasks/send                → Task-Eingang von Remote-Peers (Secret-Auth)
  GET  /admin/a2a/peers               → Konfigurierte Peers auflisten
  PUT  /admin/a2a/peers               → Peer hinzufügen / aktualisieren
  DELETE /admin/a2a/peers/{name}      → Peer entfernen
  POST /admin/a2a/test/{name}         → Verbindungstest zu Peer

Konfiguration: /etc/hydrahive/a2a_peers.json
{
  "secret": "incoming-shared-secret",
  "peers": [
    {"name": "prod", "url": "http://192.168.178.181", "secret": "outgoing-secret"}
  ]
}

secret    = was andere Peers im X-A2A-Secret-Header senden müssen um uns zu erreichen
peer.secret = was wir zu diesem Peer senden (= dessen 'secret')
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

A2A_CONFIG = Path("/etc/hydrahive/a2a_peers.json")
_APP_VERSION = "0.1.0"


# ── Konfiguration laden / speichern ──────────────────────────────────────────

def _load_config() -> dict:
    try:
        return json.loads(A2A_CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"secret": "", "peers": []}


def _save_config(cfg: dict) -> None:
    A2A_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    A2A_CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    A2A_CONFIG.chmod(0o600)


# ── Pydantic-Modelle ─────────────────────────────────────────────────────────

class A2APeer(BaseModel):
    name:   str
    url:    str
    secret: str
    description: str = ""


class A2ATaskRequest(BaseModel):
    agent_id:    str
    message:     str
    sender_name: str = "remote"


class A2APeerUpsert(BaseModel):
    name:        str
    url:         str
    secret:      str
    description: str = ""


class A2ASendRequest(BaseModel):
    agent_id:    str
    message:     str
    sender_name: str = "HydraHive-Admin"


# ── HTTP-Helper für ausgehende A2A-Calls (in Thread-Pool wegen uvloop) ───────

def _ssl_ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _post_sync(url: str, headers: dict, body: dict, timeout: int = 60) -> tuple[int, dict]:
    import urllib.request as _urllib
    import urllib.error
    data = json.dumps(body).encode()
    req = _urllib.Request(url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with _urllib.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            return resp.getcode(), json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail", str(e))
        except Exception as parse_err:
            logger.debug("Failed to parse HTTP error body: %s", parse_err)
            detail = str(e)
        return e.code, {"error": detail}
    except Exception as e:
        return 0, {"error": str(e)}


async def _post_a2a(url: str, secret: str, body: dict, timeout: int = 60) -> tuple[int, dict]:
    headers = {"X-A2A-Secret": secret}
    return await asyncio.to_thread(_post_sync, url, headers, body, timeout)


# ── Route-Registrierung ───────────────────────────────────────────────────────

def register_a2a_routes(
    public_router: APIRouter,
    admin_router:  APIRouter,
    *,
    require_admin,
    discovery,
    orchestrator,
) -> None:

    # ── Agent Card (öffentlich) ───────────────────────────────────────────────
    @public_router.get("/.well-known/agent.json")
    async def agent_card(request: Request):
        agents = []
        try:
            for cfg in discovery.agents.values():
                agents.append({
                    "id":          cfg.id,
                    "name":        cfg.identity if isinstance(cfg.identity, str) else cfg.id,
                    "description": "",
                    "type":        cfg.type,
                })
        except Exception as e:
            logger.debug("agent_card: agents laden fehlgeschlagen: %s", e)

        base_url = str(request.base_url).rstrip("/")
        return {
            "name":         "HydraHive",
            "description":  "HydraHive AI Agent Framework",
            "url":          base_url,
            "version":      _APP_VERSION,
            "protocol":     "hydrahive-a2a/1",
            "capabilities": {"a2a": True, "streaming": False},
            "agents":       agents,
        }

    # ── Eingehende Tasks von Peers ────────────────────────────────────────────
    @public_router.post("/a2a/tasks/send")
    async def a2a_receive(req: A2ATaskRequest, x_a2a_secret: str = Header(default="")):
        cfg = _load_config()
        expected = cfg.get("secret", "")
        if not expected or x_a2a_secret != expected:
            raise HTTPException(403, "Ungültiges A2A-Secret")

        agent_id = req.agent_id.strip()
        if not agent_id:
            raise HTTPException(400, "agent_id fehlt")

        # Agent prüfen
        if agent_id not in discovery.agents:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        # Task via Orchestrator ausführen
        import uuid as _uuid
        session_id = f"a2a_{_uuid.uuid4().hex[:8]}"
        content = f"[A2A-Nachricht von {req.sender_name}]\n{req.message}"

        # Virtuelles ProjectConfig für den Agent erzeugen
        from .project_config import ProjectConfig as _PC, ProjectIdentity as _PI, ProjectAgents as _PA
        agent_cfg = discovery.agents.get(agent_id)
        project_cfg = _PC(
            id=session_id,
            identity=_PI(name=agent_cfg.identity if agent_cfg else agent_id),
            agents=_PA(boss=agent_id, workers=[]),
        )

        try:
            response = await orchestrator.handle_message(
                project_id=session_id,
                project_cfg=project_cfg,
                content=content,
                sender="a2a_remote",
            )
        except Exception as e:
            logger.error("a2a_receive: Orchestrator-Fehler: %s", e)
            raise HTTPException(500, f"Fehler beim Ausführen des Tasks: {e}")

        # handle_message gibt (text, workers) Tupel oder String zurück
        if isinstance(response, (list, tuple)):
            response_text = response[0] if response else ""
        elif isinstance(response, dict):
            response_text = response.get("response", str(response))
        else:
            response_text = str(response) if response else ""
        return {"response": response_text, "agent_id": agent_id}

    # ── Admin: Peers auflisten ────────────────────────────────────────────────
    @admin_router.get("/admin/a2a/peers")
    async def list_peers(_a=Depends(require_admin)):
        cfg = _load_config()
        peers = cfg.get("peers", [])
        # Secret maskieren
        masked = [
            {**p, "secret": "••••" + p.get("secret", "")[-4:] if p.get("secret") else ""}
            for p in peers
        ]
        return {
            "has_secret": bool(cfg.get("secret")),
            "peers":      masked,
        }

    # ── Admin: Secret setzen ──────────────────────────────────────────────────
    @admin_router.put("/admin/a2a/secret")
    async def set_secret(body: dict[str, Any], _a=Depends(require_admin)):
        secret = body.get("secret", "").strip()
        cfg = _load_config()
        cfg["secret"] = secret
        _save_config(cfg)
        return {"ok": True}

    # ── Admin: Peer hinzufügen / aktualisieren ────────────────────────────────
    @admin_router.put("/admin/a2a/peers")
    async def upsert_peer(peer: A2APeerUpsert, _a=Depends(require_admin)):
        if not peer.name.strip() or not peer.url.strip():
            raise HTTPException(400, "name und url sind Pflichtfelder")
        cfg = _load_config()
        peers: list = cfg.setdefault("peers", [])
        for i, p in enumerate(peers):
            if p.get("name") == peer.name:
                peers[i] = peer.model_dump()
                _save_config(cfg)
                return {"ok": True, "action": "updated"}
        peers.append(peer.model_dump())
        _save_config(cfg)
        return {"ok": True, "action": "added"}

    # ── Admin: Peer löschen ───────────────────────────────────────────────────
    @admin_router.delete("/admin/a2a/peers/{name}")
    async def delete_peer(name: str, _a=Depends(require_admin)):
        cfg = _load_config()
        before = len(cfg.get("peers", []))
        cfg["peers"] = [p for p in cfg.get("peers", []) if p.get("name") != name]
        if len(cfg["peers"]) == before:
            raise HTTPException(404, f"Peer '{name}' nicht gefunden")
        _save_config(cfg)
        return {"ok": True}

    # ── Admin: Verbindungstest ────────────────────────────────────────────────
    @admin_router.post("/admin/a2a/test/{name}")
    async def test_peer(name: str, _a=Depends(require_admin)):
        cfg = _load_config()
        peers = cfg.get("peers", [])
        peer = next((p for p in peers if p.get("name") == name), None)
        if not peer:
            raise HTTPException(404, f"Peer '{name}' nicht gefunden")

        url = peer["url"].rstrip("/") + "/.well-known/agent.json"

        def _get_sync(url: str, timeout: int = 10) -> tuple[int, dict]:
            import urllib.request as _req
            import urllib.error
            try:
                with _req.urlopen(url, timeout=timeout, context=_ssl_ctx()) as resp:
                    return resp.getcode(), json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                return e.code, {}
            except Exception as e:
                return 0, {"error": str(e)}

        status, data = await asyncio.to_thread(_get_sync, url)
        ok = status == 200
        return {
            "ok":          ok,
            "status":      status,
            "peer_name":   data.get("name", ""),
            "peer_version":data.get("version", ""),
            "agents":      data.get("agents", []),
            "error":       data.get("error", "") if not ok else "",
        }

    @admin_router.post("/admin/a2a/send/{name}")
    async def send_task(name: str, body: A2ASendRequest, _a=Depends(require_admin)):
        """Sendet einen Test-Task an einen Remote-Peer."""
        cfg = _load_config()
        peers = cfg.get("peers", [])
        peer = next((p for p in peers if p.get("name") == name), None)
        if not peer:
            raise HTTPException(404, f"Peer '{name}' nicht gefunden")

        url = peer["url"].rstrip("/") + "/a2a/tasks/send"
        secret = peer.get("secret", "")
        payload = {
            "agent_id":    body.agent_id,
            "message":     body.message,
            "sender_name": body.sender_name,
        }
        status, data = await _post_a2a(url, secret, payload, timeout=60)
        if status not in (200, 201):
            raise HTTPException(status or 502, data.get("error", "Fehler beim Senden"))
        return {"ok": True, "response": data.get("response", ""), "status": status}
