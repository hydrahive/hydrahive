"""
orchestrator_mcp.py — MCP + Plugin Tool-Integration (#128)

Lädt MCP-Server-Schemas, dispatcht MCP-Tool-Calls,
liefert Plugin-Schemas für Agenten.
"""

import json as _json
import logging
from pathlib import Path

from .agent_config import AgentConfig

logger = logging.getLogger(__name__)


def _load_mcp_server_map(mcp_servers_file: str) -> dict[str, dict]:
    """Lädt mcp_servers.json als {id: cfg} Dict."""
    try:
        data = _json.loads(Path(mcp_servers_file).read_text())
        return {s["id"]: s for s in data.get("servers", [])}
    except Exception as e:
        logger.debug("Failed to load MCP servers config: %s", e)
        return {}


async def _mcp_schemas_for_agent(
    agent_cfg: AgentConfig,
    mcp_servers_file: str,
) -> list[dict]:
    """
    Holt litellm-kompatible Tool-Schemas von allen MCP-Servern des Agenten.
    Tool-Namen werden mit mcp_{server_id}_ präfixiert.
    """
    if not agent_cfg.mcp_servers:
        return []
    from .mcp_client import list_mcp_tools
    server_map = _load_mcp_server_map(mcp_servers_file)
    schemas: list[dict] = []
    for server_id in agent_cfg.mcp_servers:
        srv = server_map.get(server_id)
        if not srv:
            logger.warning("MCP-Server '%s' nicht in mcp_servers.json gefunden", server_id)
            continue
        tools = await list_mcp_tools(server_id, srv)
        for t in tools:
            schemas.append({
                "type": "function",
                "function": {
                    "name":        f"mcp_{server_id}_{t['name']}",
                    "description": f"[{server_id}] {t.get('description', '')}",
                    "parameters":  t.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
    return schemas


def _plugin_schemas_for_agent(agent_cfg: AgentConfig) -> list[dict]:
    """v2: Plugin-System entfernt — gibt immer leere Liste zurück."""
    return []


async def _mcp_deferred_entries(
    agent_cfg: AgentConfig,
    mcp_servers_file: str,
) -> list[tuple[str, str]]:
    """
    Liefert [(prefixed_name, one_line_description), ...] für den
    <available-deferred-tools> Block. Wird vom Prompt-Builder genutzt.
    #620 Phase 4.
    """
    schemas = await _mcp_schemas_for_agent(agent_cfg, mcp_servers_file)
    out: list[tuple[str, str]] = []
    for s in schemas:
        fn = s.get("function") or {}
        name = fn.get("name") or ""
        desc = (fn.get("description") or "").split("\n", 1)[0].strip()[:120]
        if name:
            out.append((name, desc))
    return out


def filter_mcp_schemas_by_loaded(
    mcp_schemas: list[dict],
    loaded_names: set[str],
) -> list[dict]:
    """Lässt nur MCP-Schemas durch deren Name in loaded_names steht (#620 Phase 4)."""
    return [s for s in mcp_schemas if (s.get("function", {}).get("name") or "") in loaded_names]


async def _execute_mcp_tool(
    agent_cfg: AgentConfig,
    mcp_servers_file: str,
    prefixed_name: str,
    args: dict,
    runtime=None,
) -> str:
    """
    Dispatcht einen MCP-Tool-Call an den richtigen Server.
    Erwartet prefixed_name im Format mcp_{server_id}_{tool_name}.
    """
    from .mcp_client import call_mcp_tool
    server_map = _load_mcp_server_map(mcp_servers_file)
    for server_id in agent_cfg.mcp_servers:
        prefix = f"mcp_{server_id}_"
        if prefixed_name.startswith(prefix):
            srv = server_map.get(server_id)
            if not srv:
                raise ValueError(f"MCP-Server '{server_id}' nicht konfiguriert")
            tool_name = prefixed_name[len(prefix):]
            if runtime:
                runtime.set_activity(agent_cfg.id, f"MCP: {server_id}/{tool_name}")
            try:
                return await call_mcp_tool(server_id, srv, tool_name, args)
            finally:
                if runtime:
                    runtime.set_activity(agent_cfg.id, "Denkt…")
    raise ValueError(f"Kein MCP-Server für Tool '{prefixed_name}' gefunden")
