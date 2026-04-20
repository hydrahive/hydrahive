"""
mcp_server.py — HydraHive als MCP-Server (#128 OpenClaw Bridge Phase 1)

Exponiert HydraHive-Agenten als MCP-Tools für externe Clients
(Claude Code, OpenClaw, jeder MCP-kompatible Client).

MCP-Protokoll: JSON-RPC über HTTP (Streamable HTTP Transport)
Endpoint: POST /mcp

Tools:
  - hydrahive_list_agents: Alle Agenten auflisten
  - hydrahive_send_message: Nachricht an Agent senden
  - hydrahive_agent_status: Agent-Status abfragen
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# MCP-Protokoll Konstanten
MCP_VERSION = "2024-11-05"
SERVER_NAME = "hydrahive"
SERVER_VERSION = "1.0.0"

# Tool-Definitionen
MCP_TOOLS = [
    {
        "name": "hydrahive_list_agents",
        "description": "Liste alle verfügbaren HydraHive-Agenten auf. Zeigt ID, Name, Typ und Status.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "hydrahive_send_message",
        "description": "Sende eine Nachricht an einen HydraHive-Agenten und erhalte die Antwort. "
                       "Der Agent hat Zugriff auf seine konfigurierten Tools (Shell, Git, Web etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID des Ziel-Agenten (z.B. 'coder', 'personal_admin')",
                },
                "message": {
                    "type": "string",
                    "description": "Die Nachricht an den Agenten",
                },
            },
            "required": ["agent_id", "message"],
        },
    },
    {
        "name": "hydrahive_agent_status",
        "description": "Status eines bestimmten Agenten abfragen (online/offline, Model, Tools).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID des Agenten",
                },
            },
            "required": ["agent_id"],
        },
    },
]


def _jsonrpc_response(id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def register_mcp_server_routes(
    app,
    *,
    discovery,
    runtime,
    orchestrator,
    require_auth,
) -> None:
    """Registriert den MCP-Server Endpoint."""

    # MCP-API-Key aus Config laden (alternativ zum JWT Bearer-Token)
    # #780: TTL-aware — abgelaufene Keys werden als "nicht konfiguriert" behandelt.
    def _load_mcp_api_key() -> str:
        from .settings import settings
        try:
            cfg = json.loads(settings.mcp_servers_config.read_text())
        except (OSError, ValueError):
            return ""
        key = cfg.get("server_api_key", "")
        if not key:
            return ""
        # TTL-Check: wenn expires_at gesetzt und in der Vergangenheit → Key
        # gilt als abgelaufen. Admin muss `POST /admin/mcp/api-key/generate`
        # aufrufen. Best-effort — bei unparseable Timestamp wird der Key
        # als gueltig behandelt (fail-open fuer Config-Migrations-Faelle).
        expires_at = cfg.get("server_api_key_expires_at", "")
        if expires_at:
            try:
                from datetime import datetime, timezone
                _exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if _exp.tzinfo is None:
                    _exp = _exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > _exp:
                    logger.warning("MCP-API-Key abgelaufen (expires_at=%s) — ignoriert", expires_at)
                    return ""
            except (ValueError, AttributeError):
                pass
        return key

    def _verify_mcp_auth(creds: HTTPAuthorizationCredentials | None) -> bool:
        """Prüft Auth: JWT Bearer-Token ODER MCP-API-Key."""
        if not creds:
            return False
        token = creds.credentials
        # Versuch 1: JWT-Token
        try:
            require_auth(creds)
            return True
        except Exception:
            pass
        # Versuch 2: MCP-API-Key
        api_key = _load_mcp_api_key()
        if api_key and token == api_key:
            return True
        return False

    # Methoden die ohne Auth erlaubt sind (Discovery/Handshake)
    _PUBLIC_METHODS = {"initialize", "notifications/initialized", "tools/list"}

    @app.post("/mcp")
    async def mcp_endpoint(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ):
        """MCP JSON-RPC Endpoint (Streamable HTTP Transport)."""
        try:
            body = await request.json()
        except Exception:
            return Response(
                content=json.dumps(_jsonrpc_error(None, -32700, "Parse error")),
                media_type="application/json",
            )

        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id")

        # Auth prüfen — public methods ohne Token erlaubt
        if method not in _PUBLIC_METHODS and not _verify_mcp_auth(creds):
            return Response(
                content=json.dumps(_jsonrpc_error(req_id, -32000, "Unauthorized — Bearer-Token oder MCP-API-Key erforderlich")),
                media_type="application/json",
                status_code=401,
            )

        # MCP Lifecycle Methods
        if method == "initialize":
            result = {
                "protocolVersion": MCP_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            }
            return Response(
                content=json.dumps(_jsonrpc_response(req_id, result)),
                media_type="application/json",
            )

        if method == "notifications/initialized":
            # Client bestätigt Initialisierung — kein Response nötig
            return Response(status_code=204)

        if method == "tools/list":
            result = {"tools": MCP_TOOLS}
            return Response(
                content=json.dumps(_jsonrpc_response(req_id, result)),
                media_type="application/json",
            )

        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            try:
                tool_result = await _execute_mcp_tool(
                    tool_name, tool_args,
                    discovery=discovery,
                    runtime=runtime,
                    orchestrator=orchestrator,
                )
                result = {
                    "content": [{"type": "text", "text": tool_result}],
                    "isError": False,
                }
            except Exception as e:
                logger.error("MCP tool error (%s): %s", tool_name, e)
                result = {
                    "content": [{"type": "text", "text": f"Fehler: {e}"}],
                    "isError": True,
                }

            return Response(
                content=json.dumps(_jsonrpc_response(req_id, result)),
                media_type="application/json",
            )

        # Unknown method
        return Response(
            content=json.dumps(_jsonrpc_error(req_id, -32601, f"Method not found: {method}")),
            media_type="application/json",
        )

    # ── Admin-Endpoints für MCP-Server Auth ───────────────────────────

    from fastapi import APIRouter as _Router
    admin_router = _Router(prefix="/admin/mcp", tags=["mcp-admin"])

    @admin_router.get("/api-key")
    def get_mcp_api_key(_a=Depends(require_auth)):
        """Zeigt ob ein MCP-API-Key konfiguriert ist (nicht den Key selbst).

        #780: liefert zusaetzlich created_at + expires_at + expired-Flag
        damit die Admin-UI eine Rotation-Warnung anzeigen kann wenn der
        Key alt oder ablaufend ist.
        """
        from .settings import settings
        from datetime import datetime, timezone
        try:
            cfg = json.loads(settings.mcp_servers_config.read_text())
        except (OSError, ValueError):
            cfg = {}
        key = cfg.get("server_api_key", "")
        created_at = cfg.get("server_api_key_created_at", "")
        expires_at = cfg.get("server_api_key_expires_at", "")
        expired = False
        if expires_at:
            try:
                _exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if _exp.tzinfo is None:
                    _exp = _exp.replace(tzinfo=timezone.utc)
                expired = datetime.now(timezone.utc) > _exp
            except ValueError:
                pass
        return {
            "configured": bool(key) and not expired,
            "key_preview": f"{key[:8]}..." if key else None,
            "created_at":  created_at or None,
            "expires_at":  expires_at or None,
            "expired":     expired,
        }

    @admin_router.post("/api-key/generate")
    def generate_mcp_api_key(
        ttl_days: int | None = None,
        _a=Depends(require_auth),
    ):
        """Generiert einen neuen MCP-API-Key und speichert ihn.

        #780: optional ttl_days fuer TTL-Binding (0 oder None = kein Ablauf).
        created_at + expires_at werden persistiert, sodass die Admin-UI
        den Status anzeigen und rechtzeitig warnen kann.
        Audit-Log-Eintrag bei jeder Rotation.
        """
        import secrets
        from datetime import datetime, timedelta, timezone
        from .settings import settings
        try:
            from .main import audit_log as _audit
        except Exception:
            _audit = None

        new_key = f"hh-mcp-{secrets.token_urlsafe(32)}"
        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            cfg = json.loads(settings.mcp_servers_config.read_text())
        except (OSError, ValueError):
            cfg = {}
        had_previous = bool(cfg.get("server_api_key"))
        cfg["server_api_key"] = new_key
        cfg["server_api_key_created_at"] = now_iso
        if ttl_days and ttl_days > 0:
            _exp = (now + timedelta(days=int(ttl_days))).strftime("%Y-%m-%dT%H:%M:%SZ")
            cfg["server_api_key_expires_at"] = _exp
        else:
            cfg.pop("server_api_key_expires_at", None)

        settings.mcp_servers_config.parent.mkdir(parents=True, exist_ok=True)
        settings.mcp_servers_config.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        settings.mcp_servers_config.chmod(0o600)
        logger.info(
            "AUDIT [MCP-KEY] rotated%s (ttl_days=%s)",
            " (previous invalidated)" if had_previous else "", ttl_days,
        )
        if _audit is not None:
            try:
                _audit(
                    action="mcp_api_key_rotate",
                    user="admin",
                    target="mcp-server",
                    details={"ttl_days": ttl_days, "previous_invalidated": had_previous},
                )
            except Exception as _audit_err:
                logger.debug("audit_log for mcp_api_key_rotate failed: %s", _audit_err)

        return {
            "api_key":    new_key,
            "created_at": now_iso,
            "expires_at": cfg.get("server_api_key_expires_at"),
        }

    @admin_router.delete("/api-key")
    def delete_mcp_api_key(_a=Depends(require_auth)):
        """Löscht den MCP-API-Key sofort (Revocation). Audit-Log-Eintrag."""
        from .settings import settings
        try:
            from .main import audit_log as _audit
        except Exception:
            _audit = None

        try:
            cfg = json.loads(settings.mcp_servers_config.read_text())
        except (OSError, ValueError):
            cfg = {}
        had_key = bool(cfg.get("server_api_key"))
        cfg.pop("server_api_key", None)
        cfg.pop("server_api_key_created_at", None)
        cfg.pop("server_api_key_expires_at", None)
        settings.mcp_servers_config.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        logger.info("AUDIT [MCP-KEY] revoked (had_key=%s)", had_key)
        if _audit is not None:
            try:
                _audit(
                    action="mcp_api_key_revoke",
                    user="admin",
                    target="mcp-server",
                    details={"had_key": had_key},
                )
            except Exception as _audit_err:
                logger.debug("audit_log for mcp_api_key_revoke failed: %s", _audit_err)
        return {"deleted": True}

    app.include_router(admin_router)


async def _execute_mcp_tool(
    tool_name: str,
    args: dict,
    *,
    discovery,
    runtime,
    orchestrator,
) -> str:
    """Führt ein MCP-Tool aus und gibt das Ergebnis als Text zurück."""

    if tool_name == "hydrahive_list_agents":
        agents = []
        for agent_id, cfg in discovery.agents.items():
            status = runtime.status_all().get(agent_id, {})
            agents.append({
                "id": agent_id,
                "identity": cfg.identity,
                "type": cfg.type,
                "model": cfg.llm.model,
                "tools": cfg.tools[:10],  # erste 10
                "online": status.get("online", False) if isinstance(status, dict) else bool(status),
            })
        return json.dumps({"agents": agents, "count": len(agents)}, indent=2, ensure_ascii=False)

    elif tool_name == "hydrahive_send_message":
        agent_id = args.get("agent_id", "")
        message = args.get("message", "")
        if not agent_id or not message:
            raise ValueError("agent_id und message sind Pflichtfelder")

        cfg = discovery.get(agent_id)
        if not cfg:
            raise ValueError(f"Agent '{agent_id}' nicht gefunden")

        # Virtual Project-Config für Agent-Chat
        from .project_config import ProjectConfig, ProjectIdentity, ProjectAgents
        virtual_cfg = ProjectConfig(
            id=agent_id,
            identity=ProjectIdentity(name=cfg.identity),
            agents=ProjectAgents(boss=agent_id, workers=[]),
        )

        response, _ = await orchestrator.handle_message(
            project_id=agent_id,
            project_cfg=virtual_cfg,
            content=message,
            sender="mcp_client",
        )
        return response

    elif tool_name == "hydrahive_agent_status":
        agent_id = args.get("agent_id", "")
        cfg = discovery.get(agent_id)
        if not cfg:
            raise ValueError(f"Agent '{agent_id}' nicht gefunden")

        status = runtime.status_all().get(agent_id, {})
        return json.dumps({
            "id": agent_id,
            "identity": cfg.identity,
            "type": cfg.type,
            "model": cfg.llm.model,
            "tools": cfg.tools,
            "execution_mode": cfg.execution_modes.default if cfg.execution_modes else "legacy",
            "status": status,
        }, indent=2, ensure_ascii=False)

    else:
        raise ValueError(f"Unbekanntes Tool: {tool_name}")
