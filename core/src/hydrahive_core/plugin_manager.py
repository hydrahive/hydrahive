"""
plugin_manager.py — HydraHive Plugin-System (#49, #110)

Verwaltet zwei Plugin-Arten:
  1. Manifest-Plugins (NEU): /plugins/<id>/plugin.yaml + plugin.py
     → Registrieren Tools + Hooks über PluginAPI (register-Pattern)
  2. Legacy-Plugins: /agents/<id>/plugins/*.py
     → Registrieren Hooks direkt via plugin_manager.on()

Lifecycle:
  discover → validate manifest → load module → register(api) → tools + hooks aktiv

State:  /etc/hydrahive/plugin_state.json
  {
    "plugins": {
      "csv-tools": {"enabled": true, "agents": ["personal_admin"]},
      ...
    }
  }

Events (Hook-System):
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
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)

PLUGIN_STATE_FILE = Path("/etc/hydrahive/plugin_state.json")
PLUGINS_DIR       = Path("/plugins")


# ── Plugin-Manifest ─────────────────────────────────────────────────────────

@dataclass
class PluginManifest:
    """Geparste plugin.yaml"""
    id:           str
    name:         str
    version:      str  = "0.1.0"
    description:  str  = ""
    author:       str  = ""
    type:         str  = "tool"       # tool | hook | service
    runtime:      str  = "python"
    permissions:  list[str] = field(default_factory=list)
    dependencies: dict = field(default_factory=dict)  # {"pip": ["pandas"]}
    sandbox:      bool = False
    auto_attach:  bool = False
    default_agents: list[str] = field(default_factory=list)
    ui:           dict = field(default_factory=dict)  # {tab: {label, icon, order}, config_fields: [...]}

    @classmethod
    def from_yaml(cls, path: Path) -> PluginManifest:
        """Liest plugin.yaml und gibt ein validiertes Manifest zurück."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not raw.get("id"):
            raise ValueError(f"plugin.yaml ohne 'id': {path}")
        return cls(
            id=raw["id"],
            name=raw.get("name", raw["id"]),
            version=raw.get("version", "0.1.0"),
            description=raw.get("description", ""),
            author=raw.get("author", ""),
            type=raw.get("type", "tool"),
            runtime=raw.get("runtime", "python"),
            permissions=raw.get("permissions", []),
            dependencies=raw.get("dependencies", {}),
            sandbox=raw.get("sandbox", False),
            auto_attach=raw.get("auto_attach", False),
            default_agents=raw.get("default_agents", []),
            ui=raw.get("ui", {}),
        )


# ── Geladenes Plugin ────────────────────────────────────────────────────────

@dataclass
class LoadedPlugin:
    """Laufzeit-Repräsentation eines geladenen Plugins."""
    manifest:   PluginManifest
    path:       Path
    enabled:    bool = True
    error:      str | None = None
    tools:      list = field(default_factory=list)    # list[PluginTool]
    hook_count: int = 0
    module:     Any = None

    @property
    def info(self) -> dict:
        """Serialisierbare Info für API-Responses."""
        return {
            "id":          self.manifest.id,
            "name":        self.manifest.name,
            "version":     self.manifest.version,
            "description": self.manifest.description,
            "author":      self.manifest.author,
            "type":        self.manifest.type,
            "enabled":     self.enabled,
            "error":       self.error,
            "path":        str(self.path),
            "tools":       [t.id for t in self.tools],
            "hook_count":  self.hook_count,
            "permissions": self.manifest.permissions,
        }


# ── Plugin State (Persistenz) ──────────────────────────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(PLUGIN_STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"plugins": {}}


def _save_state(state: dict) -> None:
    PLUGIN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLUGIN_STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Plugin Manager ──────────────────────────────────────────────────────────

class PluginManager:
    def __init__(self) -> None:
        # Hook-Bus (für Legacy + Manifest-Plugins)
        self._hooks: dict[str, list[Callable]] = {}

        # Legacy-Plugins (alte agent-spezifische .py-Dateien)
        self._legacy_loaded: list[dict[str, Any]] = []

        # Manifest-Plugins (neues System)
        self._plugins: dict[str, LoadedPlugin] = {}

        # Tool-Registry Referenz (gesetzt bei init)
        self._tool_registry: Any = None

    # ── Hook-Bus (unverändert für Backward-Compat) ──────────────────────

    def on(self, event: str, fn: Callable) -> None:
        """Hook für ein Event registrieren (Legacy-API)."""
        self._hooks.setdefault(event, []).append(fn)

    def off(self, event: str, fn: Callable) -> None:
        """Hook entfernen."""
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
                logger.warning("Plugin-Hook '%s' Fehler: %s", event, e)

    # ── Initialisierung ─────────────────────────────────────────────────

    def init(self, tool_registry: Any, plugins_dir: str | Path = PLUGINS_DIR) -> None:
        """Initialisiert das Plugin-System. Aufgerufen von main.py im Lifespan."""
        self._tool_registry = tool_registry
        self._plugins_dir = Path(plugins_dir)
        self._discover_and_load()

    def _discover_and_load(self) -> None:
        """Scannt /plugins/ und lädt alle Manifest-Plugins."""
        if not self._plugins_dir.is_dir():
            logger.debug("Plugin-Verzeichnis %s existiert nicht", self._plugins_dir)
            return

        state = _load_state()

        for plugin_dir in sorted(self._plugins_dir.iterdir()):
            manifest_path = plugin_dir / "plugin.yaml"
            if not manifest_path.exists():
                continue

            try:
                manifest = PluginManifest.from_yaml(manifest_path)
            except Exception as e:
                logger.error("Plugin-Manifest ungültig: %s — %s", manifest_path, e)
                self._plugins[plugin_dir.name] = LoadedPlugin(
                    manifest=PluginManifest(id=plugin_dir.name, name=plugin_dir.name),
                    path=plugin_dir,
                    enabled=False,
                    error=f"Manifest ungültig: {e}",
                )
                continue

            # State: enabled/disabled?
            plugin_state = state.get("plugins", {}).get(manifest.id, {})
            enabled = plugin_state.get("enabled", True)

            lp = LoadedPlugin(
                manifest=manifest,
                path=plugin_dir,
                enabled=enabled,
            )
            self._plugins[manifest.id] = lp

            if enabled:
                self._load_plugin(lp)

        loaded = [p for p in self._plugins.values() if p.enabled and not p.error]
        if loaded:
            total_tools = sum(len(p.tools) for p in loaded)
            total_hooks = sum(p.hook_count for p in loaded)
            logger.info(
                "Plugin-System: %d Plugin(s), %d Tool(s), %d Hook(s)",
                len(loaded), total_tools, total_hooks,
            )

    def _load_plugin(self, lp: LoadedPlugin) -> None:
        """Lädt ein einzelnes Plugin: plugin.py importieren, register(api) aufrufen."""
        from .plugin_sdk import PluginAPI, PluginTool

        plugin_py = lp.path / "plugin.py"
        if not plugin_py.exists():
            lp.error = "plugin.py nicht gefunden"
            lp.enabled = False
            return

        # Python-Modul dynamisch laden
        module_name = f"_hh_manifest_plugin_{lp.manifest.id}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_py)
            if spec is None or spec.loader is None:
                lp.error = "Modul-Spec fehlgeschlagen"
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            lp.module = module
        except Exception as e:
            lp.error = f"Import-Fehler: {e}"
            logger.error("Plugin '%s' Import-Fehler: %s", lp.manifest.id, e)
            return

        # register(api) aufrufen
        register_fn = getattr(module, "register", None)
        if register_fn is None:
            lp.error = "Keine register(api)-Funktion in plugin.py"
            logger.error("Plugin '%s': Keine register()-Funktion", lp.manifest.id)
            return

        api = PluginAPI(
            plugin_id=lp.manifest.id,
            plugin_dir=lp.path,
        )

        try:
            register_fn(api)
        except Exception as e:
            lp.error = f"register() Fehler: {e}"
            logger.error("Plugin '%s' register()-Fehler: %s", lp.manifest.id, e)
            return

        # Tools in globale Registry eintragen
        for tool_spec in api.tools:
            plugin_tool = PluginTool(
                plugin_id=lp.manifest.id,
                spec=tool_spec,
                permissions=lp.manifest.permissions,
            )
            if self._tool_registry:
                self._tool_registry.register(plugin_tool)
            lp.tools.append(plugin_tool)

        # Hooks in den Event-Bus eintragen
        for event, handlers in api.hooks.items():
            for fn in handlers:
                self.on(event, fn)
            lp.hook_count += len(handlers)

        logger.info(
            "Plugin '%s' v%s geladen: %d Tool(s), %d Hook(s)",
            lp.manifest.id, lp.manifest.version,
            len(lp.tools), lp.hook_count,
        )

    # ── Plugin-Verwaltung ───────────────────────────────────────────────

    def get_plugin(self, plugin_id: str) -> LoadedPlugin | None:
        return self._plugins.get(plugin_id)

    def list_plugins(self) -> list[dict]:
        """Alle Manifest-Plugins als serialisierbare Liste."""
        return [lp.info for lp in self._plugins.values()]

    def enable_plugin(self, plugin_id: str) -> bool:
        """Plugin aktivieren und laden."""
        lp = self._plugins.get(plugin_id)
        if not lp:
            return False
        if lp.enabled and not lp.error:
            return True  # Bereits aktiv

        lp.enabled = True
        lp.error = None
        lp.tools = []
        lp.hook_count = 0
        self._load_plugin(lp)

        # State persistieren
        state = _load_state()
        state.setdefault("plugins", {})[plugin_id] = {
            "enabled": True,
            "agents": state.get("plugins", {}).get(plugin_id, {}).get("agents", []),
        }
        _save_state(state)
        return not lp.error

    def disable_plugin(self, plugin_id: str) -> bool:
        """Plugin deaktivieren: Tools aus Registry entfernen, Hooks entfernen."""
        lp = self._plugins.get(plugin_id)
        if not lp:
            return False

        # Tools aus Registry entfernen
        if self._tool_registry:
            for tool in lp.tools:
                self._tool_registry._tools.pop(tool.id, None)

        # Modul aus sys.modules entfernen
        module_name = f"_hh_manifest_plugin_{plugin_id}"
        sys.modules.pop(module_name, None)

        lp.enabled = False
        lp.tools = []
        lp.hook_count = 0
        lp.module = None
        lp.error = None

        # State persistieren
        state = _load_state()
        state.setdefault("plugins", {})[plugin_id] = {
            "enabled": False,
            "agents": state.get("plugins", {}).get(plugin_id, {}).get("agents", []),
        }
        _save_state(state)
        return True

    def reload_plugin(self, plugin_id: str) -> bool:
        """Plugin komplett neu laden."""
        self.disable_plugin(plugin_id)
        return self.enable_plugin(plugin_id)

    # ── Agent-Zuweisung ─────────────────────────────────────────────────

    def get_agent_plugins(self, agent_id: str) -> list[str]:
        """Gibt Plugin-IDs zurück die einem Agent zugewiesen sind."""
        state = _load_state()
        result = []
        for pid, pstate in state.get("plugins", {}).items():
            if agent_id in pstate.get("agents", []):
                result.append(pid)
        # auto_attach Plugins (immer bei allen Agents aktiv)
        for pid, lp in self._plugins.items():
            if lp.manifest.auto_attach and lp.enabled and pid not in result:
                result.append(pid)
        return result

    def set_agent_plugins(self, agent_id: str, plugin_ids: list[str]) -> None:
        """Setzt die Plugin-Zuweisungen für einen Agent."""
        state = _load_state()
        for pid in self._plugins:
            pstate = state.setdefault("plugins", {}).setdefault(pid, {})
            agents = pstate.get("agents", [])
            if pid in plugin_ids and agent_id not in agents:
                agents.append(agent_id)
            elif pid not in plugin_ids and agent_id in agents:
                agents.remove(agent_id)
            pstate["agents"] = agents
        _save_state(state)

    def get_plugin_tools_for_agent(self, agent_id: str) -> list:
        """Gibt alle PluginTool-Objekte zurück die dem Agent zugewiesen sind."""
        plugin_ids = self.get_agent_plugins(agent_id)
        tools = []
        for pid in plugin_ids:
            lp = self._plugins.get(pid)
            if lp and lp.enabled and not lp.error:
                tools.extend(lp.tools)
        return tools

    def get_plugin_tool_ids_for_agent(self, agent_id: str) -> list[str]:
        """Tool-IDs für Agent (plg_xxx_yyy Format)."""
        return [t.id for t in self.get_plugin_tools_for_agent(agent_id)]

    # ── Legacy-System (Agent-Plugins) ───────────────────────────────────

    def load_plugins_from_dir(self, plugins_dir: Path, agent_id: str) -> int:
        """Lädt alle *.py Dateien aus plugins_dir als Legacy-Plugins."""
        loaded = 0
        if not plugins_dir.is_dir():
            return 0
        for py_file in sorted(plugins_dir.glob("*.py")):
            try:
                module_name = f"_hh_plugin_{agent_id}_{py_file.stem}"
                if module_name in sys.modules:
                    continue
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)  # type: ignore[union-attr]
                self._legacy_loaded.append({
                    "module": module_name,
                    "file":   str(py_file),
                    "agent_id": agent_id,
                    "events": [e for e, hooks in self._hooks.items()
                               for h in hooks if getattr(h, "__module__", "") == module_name],
                })
                loaded += 1
                logger.info("Legacy-Plugin geladen: %s (Agent: %s)", py_file.name, agent_id)
            except Exception as e:
                logger.error("Legacy-Plugin-Ladefehler %s/%s: %s", agent_id, py_file.name, e)
        return loaded

    def load_all_agent_plugins(self, agents_dir: str) -> int:
        """Scannt /agents/*/plugins/ und lädt alle Legacy-Plugins."""
        total = 0
        agents_path = Path(agents_dir)
        if not agents_path.is_dir():
            return 0
        for agent_dir in sorted(agents_path.iterdir()):
            plugins_dir = agent_dir / "plugins"
            total += self.load_plugins_from_dir(plugins_dir, agent_dir.name)
        return total

    def reload_all(self, agents_dir: str) -> int:
        """Alle Plugins (Legacy + Manifest) neu laden."""
        # Legacy entladen
        for entry in self._legacy_loaded:
            sys.modules.pop(entry["module"], None)
        self._hooks.clear()
        self._legacy_loaded.clear()

        # Manifest-Plugins entladen
        for pid in list(self._plugins.keys()):
            module_name = f"_hh_manifest_plugin_{pid}"
            sys.modules.pop(module_name, None)
            if self._tool_registry:
                lp = self._plugins[pid]
                for tool in lp.tools:
                    self._tool_registry._tools.pop(tool.id, None)
        self._plugins.clear()

        # Alles neu laden
        self._discover_and_load()
        legacy_count = self.load_all_agent_plugins(agents_dir)
        return len(self._plugins) + legacy_count

    # ── Properties für API-Responses ────────────────────────────────────

    @property
    def loaded(self) -> list[dict[str, Any]]:
        """Legacy-Format: Geladene Legacy-Plugins."""
        for entry in self._legacy_loaded:
            mod = entry["module"]
            entry["events"] = sorted({
                event for event, hooks in self._hooks.items()
                for h in hooks if getattr(h, "__module__", "") == mod
            })
        return self._legacy_loaded

    @property
    def hook_summary(self) -> dict[str, int]:
        """Anzahl der registrierten Hooks pro Event."""
        return {event: len(hooks) for event, hooks in self._hooks.items() if hooks}


# Globale Singleton-Instanz
plugin_manager = PluginManager()
