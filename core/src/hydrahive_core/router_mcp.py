from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class McpServerEntry(BaseModel):
    id: str
    name: str
    transport: str = "streamableHttp"
    url: str
    headers: dict = {}
    meta: dict = {}


class MiniMaxMcpSetup(BaseModel):
    """Body für POST /admin/mcp/servers/minimax — optionaler Prefix."""
    prefix: str = ""


def _load_mcp_servers(mcp_servers_file: str) -> list[dict]:
    import json as _json

    try:
        data = _json.loads(Path(mcp_servers_file).read_text())
        return data.get("servers", [])
    except (OSError, ValueError):
        return []


def _save_mcp_servers(mcp_servers_file: str, servers: list[dict]) -> None:
    import json as _json

    path = Path(mcp_servers_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps({"servers": servers}, indent=2), encoding="utf-8")
    # #284: MCP-Config enthält ggf. Tokens — nicht world-readable
    path.chmod(0o600)


def register_mcp_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    mcp_servers_file: str,
    audit_log,
) -> None:
    @auth_router.get("/mcp/servers")
    def list_mcp_servers():
        return {"servers": _load_mcp_servers(mcp_servers_file)}
    @admin_router.post("/mcp/servers", status_code=201)
    def create_mcp_server(req: McpServerEntry):
        import re as _re

        if not _re.match(r"^[a-z0-9_-]+$", req.id):
            raise HTTPException(400, "ID darf nur a-z, 0-9, _ und - enthalten")
        servers = _load_mcp_servers(mcp_servers_file)
        if any(server["id"] == req.id for server in servers):
            raise HTTPException(409, f"MCP-Server '{req.id}' existiert bereits")
        servers.append(req.model_dump())
        _save_mcp_servers(mcp_servers_file, servers)
        audit_log("mcp.create", target=req.id, details={"url": req.url})
        return {"created": True, "server": req.model_dump()}

    @admin_router.put("/mcp/servers/{server_id}")
    def update_mcp_server(server_id: str, req: McpServerEntry):
        # #285: server_id im Pfad muss mit req.id übereinstimmen
        if req.id != server_id:
            raise HTTPException(400, f"server_id Mismatch: URL='{server_id}', Body='{req.id}'")
        servers = _load_mcp_servers(mcp_servers_file)
        idx = next((i for i, server in enumerate(servers) if server["id"] == server_id), None)
        if idx is None:
            raise HTTPException(404, f"MCP-Server '{server_id}' nicht gefunden")
        servers[idx] = req.model_dump()
        _save_mcp_servers(mcp_servers_file, servers)
        return {"updated": True, "server": req.model_dump()}

    @admin_router.delete("/mcp/servers/{server_id}")
    def delete_mcp_server(server_id: str):
        servers = _load_mcp_servers(mcp_servers_file)
        new_servers = [server for server in servers if server["id"] != server_id]
        if len(new_servers) == len(servers):
            raise HTTPException(404, f"MCP-Server '{server_id}' nicht gefunden")
        _save_mcp_servers(mcp_servers_file, new_servers)
        audit_log("mcp.delete", target=server_id)
        return {"deleted": True, "server_id": server_id}

    # ---------------------------------------------------------------- MiniMax MCP
    @admin_router.post("/mcp/servers/minimax", status_code=201)
    def setup_minimax_mcp(req: MiniMaxMcpSetup = MiniMaxMcpSetup()):
        """Richtet den MiniMax MCP-Server ein (stdio, uvx).

        Der API-Key wird aus ``MINIMAX_API_KEY`` in der Prozesumgebung
        gelesen — NICHT in der Config-Datei gespeichert. Das ist korrekt,
        weil der MCP-Server ``minimax-coding-plan-mcp`` den Key als
        ``MINIMAX_API_KEY``-Env an seinen Subprocess durchreicht.

        Das Dict wird direkt in die mcp_servers.json geschrieben —
        Transport ``stdio`` mit ``command=uvx``, ``args=['minimax-coding-plan-mcp']``
        und dem Env-Passthrough.
        """
        server_id = "minimax"
        servers = _load_mcp_servers(mcp_servers_file)

        # Wenn bereits vorhanden: erst löschen (Upsert-Semantik)
        new_servers = [s for s in servers if s["id"] != server_id]

        api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
        if not api_key:
            raise HTTPException(409, "MINIMAX_API_KEY ist nicht in der Prozess-Umgebung gesetzt")

        prefix = req.prefix.strip()
        entry = {
            "id": server_id,
            "name": "MiniMax MCP (web_search + understand_image)",
            "transport": "stdio",
            "command": "uvx",
            "args": ["minimax-coding-plan-mcp"],
            "env": {"MINIMAX_API_KEY": api_key},
            "cwd": None,
            "headers": {},
            "meta": {"prefix": prefix} if prefix else {},
        }
        new_servers.append(entry)
        _save_mcp_servers(mcp_servers_file, new_servers)
        audit_log("mcp.create", target=server_id, details={"transport": "stdio"})
        return {"created": True, "server": entry}
