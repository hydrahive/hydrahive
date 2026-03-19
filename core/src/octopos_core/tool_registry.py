"""
tool_registry.py — Zentrales Tool-Registry (#8, #17-#21, #54, TL1-TL5)

BaseTool ABC definiert das Interface. ToolRegistry hält alle verfügbaren Tools.
Was ein Agent nutzen darf: agent.yaml ∩ Registry ∩ permissions = LLM-sichtbar.
Tool nicht in Registry = existiert nicht (egal was in agent.yaml steht).

Path-Safety (#54): assert_path_within_project() blockiert Traversal-Versuche.
Alle filesystem-Tools prüfen Pfade vor dem Zugriff.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECTS_BASE = "/projects"


# ======================================================== Path-Safety (#54)

class PathSafetyError(PermissionError):
    """Wird geworfen wenn ein Tool außerhalb des Projekt-Verzeichnisses zugreift."""


def assert_path_within_project(user_path: str, project_id: str) -> Path:
    """
    Stellt sicher dass user_path innerhalb /projects/<project_id>/ liegt.
    Gibt aufgelösten absoluten Pfad zurück oder wirft PathSafetyError.
    """
    if "\x00" in user_path:
        raise PathSafetyError(f"Null-Byte in Pfad: {user_path!r}")
    if user_path.startswith("/"):
        raise PathSafetyError(f"Absolute Pfade nicht erlaubt: {user_path!r}")

    project_root = Path(PROJECTS_BASE) / project_id
    target = (project_root / user_path).resolve()
    root_resolved = project_root.resolve()

    try:
        target.relative_to(root_resolved)
    except ValueError:
        raise PathSafetyError(
            f"Pfad-Traversal blockiert: '{user_path}' liegt außerhalb von {root_resolved}"
        )
    return target


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
    async def execute(self, agent_id: str, project_id: str, **kwargs) -> Any:
        """
        Tool ausführen.
        agent_id:   welcher Agent ruft auf (für Logging)
        project_id: aktuelles Projekt (für Pfad-Checks)
        """

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

    async def execute(self, agent_id: str, project_id: str, worker_id: str, task: str, context: str = "") -> dict:
        # Wird vom Orchestrator überschrieben — der Stub signalisiert nur die Intention
        return {"worker_id": worker_id, "task": task, "context": context}


class FileReadTool(BaseTool):
    """Datei aus dem Projekt-Verzeichnis lesen (#18)."""

    @property
    def id(self) -> str:
        return "file_read"

    @property
    def name(self) -> str:
        return "Datei lesen"

    @property
    def description(self) -> str:
        return (
            "Liest den Inhalt einer Datei aus dem Projekt-Verzeichnis. "
            "Der Pfad ist relativ zum Projekt-Stammverzeichnis, z.B. 'files/bericht.txt'. "
            "Zugriff außerhalb des Projekts ist nicht möglich."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relativer Pfad zur Datei, z.B. 'files/notizen.txt'",
                },
            },
            "required": ["path"],
        }

    async def execute(self, agent_id: str, project_id: str, path: str) -> dict:
        try:
            target = assert_path_within_project(path, project_id)
        except PathSafetyError as e:
            return {"error": str(e), "blocked": True}

        if not target.exists():
            return {"error": f"Datei nicht gefunden: {path}"}
        if not target.is_file():
            return {"error": f"Kein reguläre Datei: {path}"}

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            logger.info("file_read: %s liest %s (%d Zeichen)", agent_id, target, len(content))
            return {"path": path, "content": content, "size": len(content)}
        except OSError as e:
            return {"error": f"Lesefehler: {e}"}


class FileWriteTool(BaseTool):
    """Datei im Projekt-Verzeichnis schreiben (#19)."""

    @property
    def id(self) -> str:
        return "file_write"

    @property
    def name(self) -> str:
        return "Datei schreiben"

    @property
    def description(self) -> str:
        return (
            "Schreibt Text in eine Datei im Projekt-Verzeichnis. "
            "Erstellt die Datei und fehlende Unterverzeichnisse automatisch. "
            "Der Pfad ist relativ zum Projekt-Stammverzeichnis, z.B. 'files/ergebnis.txt'."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relativer Pfad zur Datei, z.B. 'files/ergebnis.md'",
                },
                "content": {
                    "type": "string",
                    "description": "Inhalt der in die Datei geschrieben werden soll",
                },
                "append": {
                    "type": "boolean",
                    "description": "True = anhängen, False = überschreiben (Standard)",
                    "default": False,
                },
            },
            "required": ["path", "content"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        path: str, content: str, append: bool = False,
    ) -> dict:
        try:
            target = assert_path_within_project(path, project_id)
        except PathSafetyError as e:
            return {"error": str(e), "blocked": True}

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with target.open(mode, encoding="utf-8") as f:
                f.write(content)
            logger.info("file_write: %s schreibt %s (%d Zeichen)", agent_id, target, len(content))
            return {"path": path, "written": len(content), "append": append}
        except OSError as e:
            return {"error": f"Schreibfehler: {e}"}


class WebSearchTool(BaseTool):
    """Web-Suche via DuckDuckGo Instant Answer API (#17). Kein API-Key nötig."""

    @property
    def id(self) -> str:
        return "web_search"

    @property
    def name(self) -> str:
        return "Web-Suche"

    @property
    def description(self) -> str:
        return (
            "Sucht im Web nach aktuellen Informationen. "
            "Gibt eine Liste von Ergebnissen mit Titel, URL und Zusammenfassung zurück."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchanfrage"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximale Anzahl Ergebnisse (Standard: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, agent_id: str, project_id: str, query: str, max_results: int = 5) -> dict:
        import aiohttp
        from urllib.parse import urlencode

        params = urlencode({"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"})
        url = f"https://api.duckduckgo.com/?{params}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json(content_type=None)
        except Exception as e:
            return {"error": f"Suche fehlgeschlagen: {e}", "results": []}

        results = []
        if data.get("AbstractText"):
            results.append({
                "title":   data.get("Heading", "Direkte Antwort"),
                "url":     data.get("AbstractURL", ""),
                "snippet": data["AbstractText"],
            })
        for topic in data.get("RelatedTopics", []):
            if len(results) >= max_results:
                break
            if "Topics" in topic:
                continue
            text = topic.get("Text", "")
            link = topic.get("FirstURL", "")
            if text and link:
                results.append({
                    "title":   link.split("/")[-1].replace("_", " "),
                    "url":     link,
                    "snippet": text,
                })

        logger.info("web_search: %s sucht '%s' → %d Ergebnisse", agent_id, query, len(results))
        return {"query": query, "results": results[:max_results]}


class HttpRequestTool(BaseTool):
    """HTTP-Request an externe URLs (#20). GET/POST mit optionalem JSON-Body."""

    @property
    def id(self) -> str:
        return "http_request"

    @property
    def name(self) -> str:
        return "HTTP-Request"

    @property
    def description(self) -> str:
        return (
            "Sendet einen HTTP-Request an eine URL und gibt Status-Code und Body zurück. "
            "Nützlich um externe APIs abzufragen oder Webhooks auszulösen."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Ziel-URL"},
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE"],
                    "description": "HTTP-Methode (Standard: GET)",
                    "default": "GET",
                },
                "json_body": {
                    "type": "object",
                    "description": "JSON-Body für POST/PUT (optional)",
                },
                "headers": {
                    "type": "object",
                    "description": "Zusätzliche Headers als Key-Value (optional)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in Sekunden (Standard: 15)",
                    "default": 15,
                },
            },
            "required": ["url"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        url: str, method: str = "GET",
        json_body: dict | None = None,
        headers: dict | None = None,
        timeout: int = 15,
    ) -> dict:
        import aiohttp

        logger.info("http_request: %s → %s %s", agent_id, method, url)
        try:
            async with aiohttp.ClientSession() as session:
                kwargs: dict = {
                    "timeout": aiohttp.ClientTimeout(total=timeout),
                    "headers": headers or {},
                }
                if json_body is not None:
                    kwargs["json"] = json_body

                async with session.request(method, url, **kwargs) as resp:
                    try:
                        body = await resp.json(content_type=None)
                        body_type = "json"
                    except Exception:
                        body = await resp.text()
                        body_type = "text"
                    return {"status": resp.status, "body": body, "body_type": body_type}
        except aiohttp.ClientError as e:
            return {"error": f"HTTP-Fehler: {e}"}
        except Exception as e:
            return {"error": f"Request fehlgeschlagen: {e}"}


# ======================================================== Globale Registry

registry = ToolRegistry()
registry.register(DispatchTaskTool())
registry.register(FileReadTool())
registry.register(FileWriteTool())
registry.register(WebSearchTool())
registry.register(HttpRequestTool())
