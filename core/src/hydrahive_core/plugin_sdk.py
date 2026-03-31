"""
plugin_sdk.py — HydraHive Plugin SDK (#110)

Stellt die API bereit die Plugin-Entwickler nutzen:

    # plugins/mein-plugin/plugin.py
    def register(api):
        @api.tool(description="CSV analysieren", parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        })
        async def analyze_csv(path: str, **ctx) -> str:
            import pandas as pd
            return str(pd.read_csv(path).describe())

        @api.hook("message.after")
        async def on_done(project_id, response, **_):
            print(f"Antwort: {response[:80]}")

Klassen:
    PluginAPI     — Interface das Plugins in register(api) erhalten
    PluginTool    — Wrapper: @api.tool-Funktion → BaseTool für die Registry
    ToolSpec      — Metadaten eines dekorierten Tools (interner Typ)
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .tool_registry import BaseTool

logger = logging.getLogger(__name__)


# ── Tool-Metadaten ──────────────────────────────────────────────────────────

@dataclass
class ToolSpec:
    """Von @api.tool() auf der Handler-Funktion gespeicherte Metadaten."""
    tool_id:     str
    description: str
    parameters:  dict
    handler:     Callable


# ── PluginTool: Wrapper @tool → BaseTool ────────────────────────────────────

class PluginTool(BaseTool):
    """Macht eine Plugin-Funktion als BaseTool in der Registry verfügbar.

    Tool-ID-Format: plg_{plugin_id}_{tool_id}
    Dadurch keine Kollision mit Core-Tools oder anderen Plugins.
    """

    def __init__(
        self,
        plugin_id: str,
        spec: ToolSpec,
        permissions: list[str] | None = None,
    ):
        self._id = f"plg_{plugin_id}_{spec.tool_id}"
        self._raw_id = spec.tool_id
        self._name = spec.tool_id
        self._description = spec.description
        self._parameters = spec.parameters
        self._handler = spec.handler
        self._permissions = permissions or []
        self._plugin_id = plugin_id

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return self._parameters

    @property
    def permissions_required(self) -> list[str]:
        return self._permissions

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    async def execute(self, agent_id: str, project_id: str, **kwargs) -> Any:
        try:
            result = self._handler(agent_id=agent_id, project_id=project_id, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as e:
            logger.error("Plugin-Tool '%s' Fehler: %s", self._id, e)
            return f"Plugin-Tool-Fehler: {e}"


# ── PluginAPI: Das Interface für Plugins ────────────────────────────────────

class PluginAPI:
    """API-Objekt das Plugins in ihrer register(api)-Funktion erhalten.

    Bietet Dekoratoren zum Registrieren von Tools und Hooks:

        def register(api):
            @api.tool(description="...", parameters={...})
            async def my_tool(path: str, **ctx) -> str:
                ...

            @api.hook("message.before")
            async def on_msg(content, **_):
                ...

            # Zugriff auf Plugin-Verzeichnis und Config:
            print(api.plugin_dir)
            print(api.config)
    """

    def __init__(self, plugin_id: str, plugin_dir: Path, config: dict | None = None):
        self.plugin_id  = plugin_id
        self.plugin_dir = plugin_dir
        self.config     = config or {}

        # Gesammelte Registrierungen
        self.tools: list[ToolSpec] = []
        self.hooks: dict[str, list[Callable]] = {}

    # ── Tool-Registrierung ──────────────────────────────────────────────

    def tool(
        self,
        tool_id: str | None = None,
        description: str = "",
        parameters: dict | None = None,
    ) -> Callable:
        """Dekorator: Funktion als Agent-Tool registrieren.

        @api.tool(description="Dateien zählen", parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        })
        async def count_files(path: str, **ctx) -> str:
            ...

        Die Funktion erhält automatisch agent_id und project_id als kwargs.
        """
        def decorator(fn: Callable) -> Callable:
            spec = ToolSpec(
                tool_id=tool_id or fn.__name__,
                description=description or fn.__doc__ or fn.__name__,
                parameters=parameters or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                handler=fn,
            )
            self.tools.append(spec)
            return fn
        return decorator

    # ── Hook-Registrierung ──────────────────────────────────────────────

    def hook(self, event: str) -> Callable:
        """Dekorator: Handler für ein Core-Event registrieren.

        Events: message.before, message.after, tool.before, tool.after,
                session.compact, agent.spawn, schedule.run, pipeline.file

        @api.hook("message.after")
        async def log_response(project_id, response, **_):
            ...
        """
        def decorator(fn: Callable) -> Callable:
            self.hooks.setdefault(event, []).append(fn)
            return fn
        return decorator

    # ── Zusammenfassung ─────────────────────────────────────────────────

    @property
    def summary(self) -> dict:
        """Übersicht der registrierten Capabilities."""
        return {
            "tools": [t.tool_id for t in self.tools],
            "hooks": {e: len(fns) for e, fns in self.hooks.items()},
        }
