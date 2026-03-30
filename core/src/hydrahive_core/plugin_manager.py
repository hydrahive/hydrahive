"""plugin_manager.py — Plugin-Hook-System für HydraHive (#49)

Leichtgewichtiger Event-Bus: Plugins registrieren Hooks, der Core emittiert Events.

Verwendung im Core:
    from .plugin_manager import plugin_manager
    await plugin_manager.emit("message.after", project_id="...", response="...")

Verwendung in einem Plugin (/agents/{agent_id}/plugins/mein_plugin.py):
    from hydrahive_core.plugin_manager import plugin_manager

    async def on_message_after(project_id, response, **_):
        ...

    plugin_manager.on("message.after", on_message_after)

Unterstützte Events:
    message.before      — kwargs: project_id, content, sender
    message.after       — kwargs: project_id, content, response
    tool.before         — kwargs: project_id, tool_name, tool_input
    tool.after          — kwargs: project_id, tool_name, result
    session.compact     — kwargs: project_id, summary
    agent.spawn         — kwargs: agent_id
    schedule.run        — kwargs: schedule_id, project_id
    pipeline.file       — kwargs: pipeline_id, file_path
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class PluginManager:
    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable]] = {}
        self._loaded: list[dict[str, Any]] = []   # Metadaten der geladenen Plugins

    def on(self, event: str, fn: Callable) -> None:
        """Hook für ein Event registrieren."""
        self._hooks.setdefault(event, []).append(fn)

    def off(self, event: str, fn: Callable) -> None:
        """Hook wieder entfernen."""
        if event in self._hooks:
            self._hooks[event] = [f for f in self._hooks[event] if f is not fn]

    async def emit(self, event: str, **kwargs: Any) -> None:
        """Event emittieren — alle registrierten Hooks werden aufgerufen."""
        hooks = self._hooks.get(event, [])
        if not hooks:
            return
        for fn in hooks:
            try:
                result = fn(**kwargs)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning("Plugin-Hook '%s' in %s Fehler: %s", event, fn.__module__, e)

    def load_plugins_from_dir(self, plugins_dir: Path, agent_id: str) -> int:
        """Lädt alle *.py Dateien aus plugins_dir als Plugins."""
        loaded = 0
        if not plugins_dir.is_dir():
            return 0
        for py_file in sorted(plugins_dir.glob("*.py")):
            try:
                module_name = f"_hh_plugin_{agent_id}_{py_file.stem}"
                if module_name in sys.modules:
                    continue   # bereits geladen
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)   # type: ignore[union-attr]
                self._loaded.append({
                    "module": module_name,
                    "file": str(py_file),
                    "agent_id": agent_id,
                    "events": [e for e, hooks in self._hooks.items()
                               for h in hooks if getattr(h, "__module__", "") == module_name],
                })
                loaded += 1
                logger.info("Plugin geladen: %s (Agent: %s)", py_file.name, agent_id)
            except Exception as e:
                logger.error("Plugin-Ladefehler %s/%s: %s", agent_id, py_file.name, e)
        return loaded

    def load_all_agent_plugins(self, agents_dir: str) -> int:
        """Scannt /agents/*/plugins/ und lädt alle Plugins."""
        total = 0
        agents_path = Path(agents_dir)
        if not agents_path.is_dir():
            return 0
        for agent_dir in sorted(agents_path.iterdir()):
            plugins_dir = agent_dir / "plugins"
            total += self.load_plugins_from_dir(plugins_dir, agent_dir.name)
        if total:
            logger.info("Insgesamt %d Plugin(s) geladen", total)
        return total

    def reload_all(self, agents_dir: str) -> int:
        """Alle Plugins entladen und neu laden."""
        # Alle plugin-Module aus sys.modules entfernen
        for entry in self._loaded:
            sys.modules.pop(entry["module"], None)
        self._hooks.clear()
        self._loaded.clear()
        return self.load_all_agent_plugins(agents_dir)

    @property
    def loaded(self) -> list[dict[str, Any]]:
        """Liste der geladenen Plugins mit Metadaten."""
        # Events nachträglich aktualisieren
        for entry in self._loaded:
            mod = entry["module"]
            entry["events"] = sorted({
                event for event, hooks in self._hooks.items()
                for h in hooks if getattr(h, "__module__", "") == mod
            })
        return self._loaded

    @property
    def hook_summary(self) -> dict[str, int]:
        """Anzahl der registrierten Hooks pro Event."""
        return {event: len(hooks) for event, hooks in self._hooks.items() if hooks}


# Globale Singleton-Instanz
plugin_manager = PluginManager()
