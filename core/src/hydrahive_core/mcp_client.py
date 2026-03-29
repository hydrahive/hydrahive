"""
mcp_client.py — MCP Client für HydraHive

Verbindet sich mit MCP-Servern (SSE + streamableHttp) und macht
deren Tools für Agenten verfügbar.

Tool-Namenskonvention: mcp_{server_id}_{tool_name}
(z.B. mcp_amem_add_note, mcp_godot_mcp_get_scene)
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
    import httpx
    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  method,
        "params":  params,
    }
    merged_headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, json=payload, headers=merged_headers)
        r.raise_for_status()
        # streamableHttp kann SSE-Response zurückgeben → ersten JSON-Block parsen
        ct = r.headers.get("content-type", "")
        if "text/event-stream" in ct:
            import json as _json
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
    await _http_jsonrpc(url, headers, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities":    {},
        "clientInfo":      {"name": "hydrahive", "version": "1.0"},
    })
    result = await _http_jsonrpc(url, headers, "tools/list", {})
    raw_tools = result.get("tools", [])
    tools = []
    for t in raw_tools:
        tools.append({
            "name":        t.get("name", ""),
            "description": t.get("description", ""),
            "inputSchema": t.get("inputSchema") or t.get("input_schema") or {},
        })
    return tools


async def _http_call_tool(url: str, headers: dict, tool_name: str, arguments: dict) -> str:
    await _http_jsonrpc(url, headers, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities":    {},
        "clientInfo":      {"name": "hydrahive", "version": "1.0"},
    })
    result = await _http_jsonrpc(url, headers, "tools/call", {
        "name":      tool_name,
        "arguments": arguments,
    })
    content = result.get("content", [])
    if isinstance(content, list):
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
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
    url       = server_cfg["url"]
    headers   = server_cfg.get("headers", {}) or {}

    try:
        if transport == "sse":
            tools = await _sse_list_tools(url, headers)
        else:
            tools = await _http_list_tools(url, headers)
        _tools_cache[server_id] = {"tools": tools, "ts": time.time()}
        logger.info("MCP '%s': %d Tools geladen", server_id, len(tools))
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
    url       = server_cfg["url"]
    headers   = server_cfg.get("headers", {}) or {}

    logger.info("MCP call '%s/%s' args=%s", server_id, tool_name, list(arguments.keys()))

    if transport == "sse":
        return await _sse_call_tool(url, headers, tool_name, arguments)
    else:
        return await _http_call_tool(url, headers, tool_name, arguments)


def invalidate_cache(server_id: str | None = None) -> None:
    """Cache-Eintrag(e) invalidieren (z.B. nach Server-Neustart)."""
    if server_id:
        _tools_cache.pop(server_id, None)
    else:
        _tools_cache.clear()
