"""
tool_registry.py — Zentrales Tool-Registry (#8, TL1-TL5)

BaseTool ABC definiert das Interface. ToolRegistry hält alle verfügbaren Tools.
Was ein Agent nutzen darf: agent.yaml ∩ Registry ∩ permissions = LLM-sichtbar.
Tool nicht in Registry = existiert nicht (egal was in agent.yaml steht).
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    """
    Einheitliches Interface für alle OctopOS-Tools (TL2).
    parameters = Function-Calling-Schema direkt für litellm (TL3).
    """

    @property
    @abstractmethod
    def id(self) -> str:
        """Eindeutiger Bezeichner, z.B. 'web_search'."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Lesbarer Name für Logs und UI."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Beschreibung für das LLM (erscheint im Tool-Schema)."""

    @property
    def permissions_required(self) -> list[str]:
        """Berechtigungen die ein Agent braucht um dieses Tool zu nutzen."""
        return []

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """
        JSON-Schema für litellm function calling (TL3).
        Format: {"type": "object", "properties": {...}, "required": [...]}
        """

    @abstractmethod
    async def execute(self, agent_id: str, **kwargs) -> Any:
        """Tool ausführen. Gibt beliebiges JSON-serialisierbares Ergebnis zurück."""

    def as_litellm_tool(self) -> dict:
        """Schema-Format das litellm für function calling erwartet."""
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """
    Singleton-Registry aller verfügbaren Tools (TL1).
    Tools werden beim Core-Start registriert.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.id] = tool
        logger.debug("Tool registriert: %s", tool.id)

    def get(self, tool_id: str) -> BaseTool | None:
        return self._tools.get(tool_id)

    def all_ids(self) -> list[str]:
        return list(self._tools.keys())

    def tools_for_agent(
        self,
        agent_tool_ids: list[str],
        agent_permissions: list[str] | None = None,
    ) -> list[BaseTool]:
        """
        Schnittmenge: agent.yaml ∩ Registry ∩ permissions (TL4).
        Tools die nicht in der Registry sind werden stillschweigend ignoriert (TL5).
        """
        perms = set(agent_permissions or [])
        result = []
        for tool_id in agent_tool_ids:
            tool = self._tools.get(tool_id)
            if tool is None:
                logger.debug("Tool '%s' nicht in Registry — ignoriert", tool_id)
                continue
            if tool.permissions_required and not perms.issuperset(tool.permissions_required):
                logger.debug("Tool '%s' fehlen Berechtigungen — ignoriert", tool_id)
                continue
            result.append(tool)
        return result

    def as_litellm_tools(self, tools: list[BaseTool]) -> list[dict]:
        return [t.as_litellm_tool() for t in tools]


# ======================================================== Built-in Tools

class DispatchTaskTool(BaseTool):
    """
    Boss-Agent kann damit einen Worker-Agenten mit einem Task beauftragen.
    Kern-Tool für den Orchestrator.
    """

    @property
    def id(self) -> str:
        return "dispatch_task"

    @property
    def name(self) -> str:
        return "Task an Worker delegieren"

    @property
    def description(self) -> str:
        return (
            "Delegiert einen spezifischen Task an einen Worker-Agenten. "
            "Nutze dies wenn du eine Aufgabe an einen spezialisierten Agenten "
            "weitergeben willst. Der Worker erledigt den Task und gibt das Ergebnis zurück."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "worker_id": {
                    "type": "string",
                    "description": "ID des Worker-Agenten aus der Projekt-Konfiguration",
                },
                "task": {
                    "type": "string",
                    "description": "Klare Beschreibung des Tasks den der Worker erledigen soll",
                },
                "context": {
                    "type": "string",
                    "description": "Optionaler Kontext den der Worker für den Task braucht",
                },
            },
            "required": ["worker_id", "task"],
        }

    async def execute(self, agent_id: str, worker_id: str, task: str, context: str = "") -> dict:
        # Wird vom Orchestrator überschrieben — der Stub signalisiert nur die Intention
        return {"worker_id": worker_id, "task": task, "context": context}


# Globale Registry-Instanz
registry = ToolRegistry()
registry.register(DispatchTaskTool())
