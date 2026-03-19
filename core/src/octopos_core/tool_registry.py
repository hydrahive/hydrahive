"""
tool_registry.py — Zentrales Tool-Registry (#8, #54, TL1-TL5)

BaseTool ABC definiert das Interface. ToolRegistry haelt alle verfuegbaren Tools.
Was ein Agent nutzen darf: agent.yaml ∩ Registry ∩ permissions = LLM-sichtbar.
Tool nicht in Registry = existiert nicht (egal was in agent.yaml steht).

#54: Filesystem-Tools pruefen ob angeforderter Pfad innerhalb /projects/<id>/ liegt.
Path-Traversal und Zugriff ausserhalb des Projekt-Verzeichnisses werden verweigert.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path("/projects")


# ============================================================= Path Safety (#54)

class PathSafetyError(PermissionError):
    """Wird geworfen wenn ein Tool ausserhalb des Projekt-Verzeichnisses zugreifen wuerde."""


def assert_path_within_project(path: str | Path, project_id: str) -> Path:
    """
    Prueft ob path innerhalb /projects/<project_id>/ liegt.
    Loest PathSafetyError aus wenn nicht — kein stilles Ignorieren.

    Verhindert:
    - Path-Traversal: ../../etc/passwd  (durch resolve + normpath)
    - Absolute Pfade ausserhalb: /etc/passwd
    - Symlink-Escapes: wird durch resolve() aufgeloest
    """
    import os
    project_root = (PROJECTS_ROOT / project_id).resolve()

    # Relativer Pfad wird relativ zum Projekt-Root aufgeloest
    target = Path(path)
    if not target.is_absolute():
        target = project_root / target

    # os.path.normpath loest .. auf ohne Filesystem-Zugriff (Path-Traversal-Schutz)
    # resolve() loest zusaetzlich Symlinks auf wenn die Datei existiert
    normalized = Path(os.path.normpath(target))
    try:
        normalized = normalized.resolve()
    except OSError:
        pass  # Datei existiert noch nicht — normpath reicht

    # Sicherheitscheck: muss mit project_root beginnen
    try:
        normalized.relative_to(project_root)
    except ValueError:
        raise PathSafetyError(
            f"Zugriff verweigert: '{normalized}' liegt ausserhalb von '{project_root}'. "
            f"Agenten duerfen nur auf /projects/{project_id}/ zugreifen."
        )

    return normalized


# ============================================================= BaseTool

class BaseTool(ABC):
    """
    Einheitliches Interface fuer alle OctopOS-Tools (TL2).
    parameters = Function-Calling-Schema direkt fuer litellm (TL3).

    execute() bekommt agent_id und project_id — Filesystem-Tools muessen
    assert_path_within_project() aufrufen bevor sie auf Dateien zugreifen.
    """

    @property
    @abstractmethod
    def id(self) -> str:
        """Eindeutiger Bezeichner, z.B. 'file_read'."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Lesbarer Name fuer Logs und UI."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Beschreibung fuer das LLM (erscheint im Tool-Schema)."""

    @property
    def permissions_required(self) -> list[str]:
        """Berechtigungen die ein Agent braucht um dieses Tool zu nutzen."""
        return []

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """
        JSON-Schema fuer litellm function calling (TL3).
        Format: {"type": "object", "properties": {...}, "required": [...]}
        """

    @abstractmethod
    async def execute(self, agent_id: str, project_id: str, **kwargs) -> Any:
        """
        Tool ausfuehren.
        agent_id:   Welcher Agent ruft das Tool auf.
        project_id: Projekt-Kontext fuer Path-Safety (#54).
        """

    def as_litellm_tool(self) -> dict:
        """Schema-Format das litellm fuer function calling erwartet."""
        return {
            "type": "function",
            "function": {
                "name":        self.id,
                "description": self.description,
                "parameters":  self.parameters,
            },
        }


# ============================================================= ToolRegistry

class ToolRegistry:
    """
    Singleton-Registry aller verfuegbaren Tools (TL1).
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
        perms  = set(agent_permissions or [])
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


# ============================================================= Built-in Tools

class DispatchTaskTool(BaseTool):
    """Boss-Agent delegiert Tasks an Worker-Agenten."""

    @property
    def id(self) -> str:       return "dispatch_task"
    @property
    def name(self) -> str:     return "Task an Worker delegieren"
    @property
    def description(self) -> str:
        return (
            "Delegiert einen spezifischen Task an einen Worker-Agenten. "
            "Nutze dies wenn du eine Aufgabe an einen spezialisierten Agenten "
            "weitergeben willst. Der Worker erledigt den Task und gibt das Ergebnis zurueck."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "worker_id": {
                    "type":        "string",
                    "description": "ID des Worker-Agenten aus der Projekt-Konfiguration",
                },
                "task": {
                    "type":        "string",
                    "description": "Klare Beschreibung des Tasks den der Worker erledigen soll",
                },
                "context": {
                    "type":        "string",
                    "description": "Optionaler Kontext den der Worker fuer den Task braucht",
                },
            },
            "required": ["worker_id", "task"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        worker_id: str, task: str, context: str = "",
    ) -> dict:
        # Wird vom Orchestrator ueberschrieben
        return {"worker_id": worker_id, "task": task, "context": context}


class FileReadTool(BaseTool):
    """Liest eine Datei aus dem Projekt-Verzeichnis. (#18, #54)"""

    @property
    def id(self) -> str:   return "file_read"
    @property
    def name(self) -> str: return "Datei lesen"
    @property
    def description(self) -> str:
        return (
            "Liest den Inhalt einer Datei aus dem Projekt-Verzeichnis. "
            "Pfad relativ zu /projects/<projekt>/ oder absolut innerhalb davon."
        )
    @property
    def permissions_required(self) -> list[str]:
        return ["filesystem.read"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type":        "string",
                    "description": "Pfad zur Datei, relativ zum Projekt-Verzeichnis",
                },
            },
            "required": ["path"],
        }

    async def execute(self, agent_id: str, project_id: str, path: str) -> dict:
        try:
            safe_path = assert_path_within_project(path, project_id)
        except PathSafetyError as e:
            return {"error": str(e), "allowed": False}

        if not safe_path.exists():
            return {"error": f"Datei nicht gefunden: {path}", "path": str(safe_path)}
        if not safe_path.is_file():
            return {"error": f"Kein regulaere Datei: {path}", "path": str(safe_path)}

        try:
            content = safe_path.read_text(encoding="utf-8", errors="replace")
            logger.info("file_read: %s liest %s (Projekt: %s)", agent_id, safe_path, project_id)
            return {"content": content, "path": str(safe_path), "size": len(content)}
        except OSError as e:
            return {"error": f"Lesefehler: {e}", "path": str(safe_path)}


class FileWriteTool(BaseTool):
    """Schreibt eine Datei ins Projekt-Verzeichnis. (#19, #54)"""

    @property
    def id(self) -> str:   return "file_write"
    @property
    def name(self) -> str: return "Datei schreiben"
    @property
    def description(self) -> str:
        return (
            "Schreibt Inhalt in eine Datei im Projekt-Verzeichnis. "
            "Erstellt die Datei wenn sie nicht existiert. "
            "Pfad relativ zu /projects/<projekt>/ oder absolut innerhalb davon."
        )
    @property
    def permissions_required(self) -> list[str]:
        return ["filesystem.write"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type":        "string",
                    "description": "Pfad zur Datei, relativ zum Projekt-Verzeichnis",
                },
                "content": {
                    "type":        "string",
                    "description": "Inhalt der geschrieben werden soll",
                },
                "mode": {
                    "type":        "string",
                    "enum":        ["overwrite", "append"],
                    "description": "overwrite (Standard) oder append",
                    "default":     "overwrite",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        path: str, content: str, mode: str = "overwrite",
    ) -> dict:
        try:
            safe_path = assert_path_within_project(path, project_id)
        except PathSafetyError as e:
            return {"error": str(e), "allowed": False}

        try:
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            write_mode = "a" if mode == "append" else "w"
            safe_path.open(write_mode, encoding="utf-8").write(content)
            logger.info(
                "file_write: %s schreibt %s (%s, Projekt: %s)",
                agent_id, safe_path, mode, project_id,
            )
            return {"written": True, "path": str(safe_path), "bytes": len(content.encode())}
        except OSError as e:
            return {"error": f"Schreibfehler: {e}", "path": str(safe_path)}


class WebSearchTool(BaseTool):
    """Web-Suche via DuckDuckGo Instant Answer API (#17). Kein API-Key noetig."""

    @property
    def id(self) -> str:   return "web_search"
    @property
    def name(self) -> str: return "Web-Suche"
    @property
    def description(self) -> str:
        return (
            "Sucht im Web nach aktuellen Informationen. "
            "Gibt eine Liste von Ergebnissen mit Titel, URL und Zusammenfassung zurueck."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchanfrage"},
                "max_results": {
                    "type":        "integer",
                    "description": "Maximale Anzahl Ergebnisse (Standard: 5)",
                    "default":     5,
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

        logger.info("web_search: %s sucht '%s' -> %d Ergebnisse", agent_id, query, len(results))
        return {"query": query, "results": results[:max_results]}


class HttpRequestTool(BaseTool):
    """HTTP-Request an externe URLs (#20). GET/POST mit optionalem JSON-Body."""

    @property
    def id(self) -> str:   return "http_request"
    @property
    def name(self) -> str: return "HTTP-Request"
    @property
    def description(self) -> str:
        return (
            "Sendet einen HTTP-Request an eine URL und gibt Status-Code und Body zurueck. "
            "Nuetzlich um externe APIs abzufragen oder Webhooks auszuloesen."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url":     {"type": "string", "description": "Ziel-URL"},
                "method": {
                    "type":        "string",
                    "enum":        ["GET", "POST", "PUT", "DELETE"],
                    "description": "HTTP-Methode (Standard: GET)",
                    "default":     "GET",
                },
                "json_body": {
                    "type":        "object",
                    "description": "JSON-Body fuer POST/PUT (optional)",
                },
                "headers": {
                    "type":        "object",
                    "description": "Zusaetzliche Headers als Key-Value (optional)",
                },
                "timeout": {
                    "type":        "integer",
                    "description": "Timeout in Sekunden (Standard: 15)",
                    "default":     15,
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

        logger.info("http_request: %s -> %s %s", agent_id, method, url)
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


# ============================================================= Globale Registry

registry = ToolRegistry()
registry.register(DispatchTaskTool())
registry.register(FileReadTool())
registry.register(FileWriteTool())
registry.register(WebSearchTool())
registry.register(HttpRequestTool())
