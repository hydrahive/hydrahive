"""
mcp_client.py — MCP Client für HydraHive

Verbindet sich mit MCP-Servern (SSE + streamableHttp + stdio) und macht
deren Tools für Agenten verfügbar.

Tool-Namenskonvention: mcp_{server_id}_{tool_name}
(z.B. mcp_amem_add_note, mcp_godot_mcp_get_scene)

Config-Beispiele in /etc/hydrahive/mcp_servers.json:

  SSE:
    {"id": "x", "transport": "sse", "url": "http://host/sse", "headers": {}}
  streamableHttp:
    {"id": "x", "transport": "streamableHttp", "url": "http://host/mcp"}
  stdio:
    {"id": "github", "transport": "stdio",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
     "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..."}}
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_TOOLS_CACHE_TTL = 300  # Sekunden

# server_id → {"tools": [...], "ts": float}
_tools_cache: dict[str, dict] = {}


# ---------------------------------------------------------------- SSE Transport

async def _sse_list_tools(url: str, headers: dict) -> list[dict]:
    from mcp.client.sse import sse_client
    from mcp import ClientSession

    async with sse_client(url, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tools = []
            for t in result.tools:
                schema = {}
                if hasattr(t, "inputSchema"):
                    schema = t.inputSchema if isinstance(t.inputSchema, dict) else {}
                elif hasattr(t, "input_schema"):
                    schema = t.input_schema if isinstance(t.input_schema, dict) else {}
                tools.append({
                    "name":        t.name,
                    "description": t.description or "",
                    "inputSchema": schema,
                })
            return tools


async def _sse_call_tool(url: str, headers: dict, tool_name: str, arguments: dict) -> str:
    from mcp.client.sse import sse_client
    from mcp import ClientSession

    async with sse_client(url, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            content = result.content
            if isinstance(content, list):
                texts = [c.text for c in content if hasattr(c, "text") and c.text]
                return "\n".join(texts) if texts else str(content)
            return str(result)


# ---------------------------------------------------------------- streamableHttp Transport

async def _http_jsonrpc(url: str, headers: dict, method: str, params: dict) -> dict:
    """Deprecated — nur noch intern von _http_list_tools/_http_call_tool verwendet."""
    import httpx
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    merged = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, json=payload, headers=merged)
        r.raise_for_status()
        return _parse_jsonrpc_response(r)


def _parse_jsonrpc_response(r) -> dict:
    """Parst JSON-RPC Response (application/json oder text/event-stream)."""
    import json as _json
    ct = r.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in r.text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    obj = _json.loads(data)
                    if "error" in obj:
                        raise RuntimeError(f"JSON-RPC Error: {obj['error']}")
                    return obj.get("result", {})
        return {}
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"JSON-RPC Error: {data['error']}")
    return data.get("result", {})


async def _http_list_tools(url: str, headers: dict) -> list[dict]:
    import httpx
    base_headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}

    async with httpx.AsyncClient(timeout=20.0) as client:
        # Initialize — Server gibt mcp-session-id zurück
        r = await client.post(url, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "hydrahive", "version": "1.0"}},
        }, headers=base_headers)
        r.raise_for_status()
        _parse_jsonrpc_response(r)

        # Session-ID für Folge-Requests übernehmen
        session_headers = dict(base_headers)
        session_id = r.headers.get("mcp-session-id")
        if session_id:
            session_headers["mcp-session-id"] = session_id

        # tools/list mit Session-ID
        r2 = await client.post(url, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        }, headers=session_headers)
        r2.raise_for_status()
        result = _parse_jsonrpc_response(r2)

    raw_tools = result.get("tools", [])
    return [{
        "name":        t.get("name", ""),
        "description": t.get("description", ""),
        "inputSchema": t.get("inputSchema") or t.get("input_schema") or {},
    } for t in raw_tools]


async def _http_call_tool(url: str, headers: dict, tool_name: str, arguments: dict) -> str:
    import httpx
    base_headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}

    async with httpx.AsyncClient(timeout=20.0) as client:
        # Initialize
        r = await client.post(url, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "hydrahive", "version": "1.0"}},
        }, headers=base_headers)
        r.raise_for_status()
        _parse_jsonrpc_response(r)

        session_headers = dict(base_headers)
        session_id = r.headers.get("mcp-session-id")
        if session_id:
            session_headers["mcp-session-id"] = session_id

        # tools/call mit Session-ID
        r2 = await client.post(url, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }, headers=session_headers)
        r2.raise_for_status()
        result = _parse_jsonrpc_response(r2)

    content = result.get("content", [])
    if isinstance(content, list):
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else str(content)
    return str(result)


# ---------------------------------------------------------------- stdio Transport
#
# stdio-MCP-Server laufen als Subprocess, JSON-RPC über stdin/stdout.
# Wir nutzen das offizielle MCP-SDK (`mcp.client.stdio.stdio_client`),
# das die JSON-RPC-Framing + initialize/ready-Handshake abwickelt.
#
# Lifecycle: pro tools/list bzw. tools/call wird der Subprocess
# kurzzeitig gestartet — analog zu HTTP/SSE (keine Long-lived-Session).
# Für häufig genutzte MCP-Server könnte ein Process-Pool sinnvoll sein,
# aber für den ersten Wurf halten wir es einfach.

def _stdio_params(server_cfg: dict):
    """Baut StdioServerParameters aus server_cfg."""
    from mcp import StdioServerParameters
    cmd = server_cfg.get("command")
    if not cmd:
        raise ValueError("stdio-MCP: 'command' fehlt in Config")
    args = list(server_cfg.get("args") or [])
    env = dict(server_cfg.get("env") or {})
    # PATH durchreichen, sonst findet Subprocess nichts (npx etc.)
    import os as _os
    if "PATH" not in env:
        env["PATH"] = _os.environ.get("PATH", "")
    cwd = server_cfg.get("cwd")
    return StdioServerParameters(command=cmd, args=args, env=env, cwd=cwd)


async def _stdio_list_tools(server_cfg: dict) -> list[dict]:
    from mcp.client.stdio import stdio_client
    from mcp import ClientSession

    params = _stdio_params(server_cfg)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tools = []
            for t in result.tools:
                schema = {}
                if hasattr(t, "inputSchema"):
                    schema = t.inputSchema if isinstance(t.inputSchema, dict) else {}
                elif hasattr(t, "input_schema"):
                    schema = t.input_schema if isinstance(t.input_schema, dict) else {}
                tools.append({
                    "name":        t.name,
                    "description": t.description or "",
                    "inputSchema": schema,
                })
            return tools


async def _stdio_call_tool(server_cfg: dict, tool_name: str, arguments: dict) -> str:
    from mcp.client.stdio import stdio_client
    from mcp import ClientSession

    params = _stdio_params(server_cfg)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            content = result.content
            if isinstance(content, list):
                texts = [c.text for c in content if hasattr(c, "text") and c.text]
                return "\n".join(texts) if texts else str(content)
            return str(result)


# ---------------------------------------------------------------- Public API

async def list_mcp_tools(server_id: str, server_cfg: dict) -> list[dict]:
    """
    Lädt Tool-Liste von einem MCP-Server (gecacht für 5 Minuten).
    Gibt Liste von {name, description, inputSchema} zurück.
    """
    cached = _tools_cache.get(server_id)
    if cached and time.time() - cached["ts"] < _TOOLS_CACHE_TTL:
        return cached["tools"]

    transport = server_cfg.get("transport", "streamableHttp")

    try:
        if transport == "stdio":
            tools = await _stdio_list_tools(server_cfg)
        elif transport == "sse":
            tools = await _sse_list_tools(server_cfg["url"], server_cfg.get("headers", {}) or {})
        else:
            tools = await _http_list_tools(server_cfg["url"], server_cfg.get("headers", {}) or {})
        _tools_cache[server_id] = {"tools": tools, "ts": time.time()}
        logger.info("MCP '%s' (%s): %d Tools geladen", server_id, transport, len(tools))
        return tools
    except Exception as e:
        logger.warning("MCP tools/list für '%s' fehlgeschlagen: %s", server_id, e)
        if cached:
            logger.info("MCP '%s': nutze gecachte Tool-Liste (%d Tools)", server_id, len(cached["tools"]))
            return cached["tools"]
        return []


async def call_mcp_tool(server_id: str, server_cfg: dict, tool_name: str, arguments: dict) -> Any:
    """
    Ruft ein MCP-Tool auf und gibt das Ergebnis als String zurück.
    """
    transport = server_cfg.get("transport", "streamableHttp")

    logger.info("MCP call '%s/%s' (%s) args=%s", server_id, tool_name, transport, list(arguments.keys()))

    if transport == "stdio":
        return await _stdio_call_tool(server_cfg, tool_name, arguments)
    if transport == "sse":
        return await _sse_call_tool(server_cfg["url"], server_cfg.get("headers", {}) or {}, tool_name, arguments)
    return await _http_call_tool(server_cfg["url"], server_cfg.get("headers", {}) or {}, tool_name, arguments)


def invalidate_cache(server_id: str | None = None) -> None:
    """Cache-Eintrag(e) invalidieren (z.B. nach Server-Neustart)."""
    if server_id:
        _tools_cache.pop(server_id, None)
    else:
        _tools_cache.clear()
