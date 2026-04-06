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

from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

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

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
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
