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
AGENTS_ROOT   = Path("/agents")


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
                },
                "json_body": {
                    "type":                 "object",
                    "description":          "JSON-Body fuer POST/PUT (optional)",
                    "properties":           {},
                    "additionalProperties": True,
                },
                "headers": {
                    "type":                 "object",
                    "description":          "Zusaetzliche Headers als Key-Value (optional)",
                    "properties":           {},
                    "additionalProperties": True,
                },
                "timeout": {
                    "type":        "integer",
                    "description": "Timeout in Sekunden (Standard: 15)",
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



class SpawnAgentTool(BaseTool):
    """
    Boss-Agent spawnt einen Task-Agenten on-demand. (#21)
    Der gespawnte Agent erbt den Projekt-Kontext und erledigt einen Task.
    """

    @property
    def id(self) -> str:   return "spawn_agent"
    @property
    def name(self) -> str: return "Task-Agent spawnen"
    @property
    def description(self) -> str:
        return (
            "Spawnt einen spezialisierten Task-Agenten fuer eine einmalige Aufgabe. "
            "Der Agent wird nach Abschluss automatisch beendet. "
            "Nutze dies fuer komplexe Teilaufgaben die einen eigenen Kontext benoetigen."
        )
    @property
    def permissions_required(self) -> list[str]:
        return ["spawn_agents"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type":        "string",
                    "description": "ID des zu spawnenden Agenten aus dem Agent-Pool",
                },
                "task": {
                    "type":        "string",
                    "description": "Aufgabe die der gespawnte Agent erledigen soll",
                },
                "context": {
                    "type":        "string",
                    "description": "Kontext und Daten die der Agent benoetigt",
                },
            },
            "required": ["agent_id", "task"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        agent_id_to_spawn: str, task: str, context: str = "",  # type: ignore[override]
    ) -> dict:
        """
        Wird vom Orchestrator abgefangen und an AgentRuntime.spawn_task_agent() weitergeleitet.
        Stub gibt Intention zurueck — Orchestrator uebernimmt die echte Ausfuehrung.
        """
        return {
            "spawning":  agent_id_to_spawn,
            "task":      task,
            "context":   context,
            "initiated_by": agent_id,
            "project":   project_id,
        }

# ============================================================= AgentLink Tools

class WriteHandoffTool(BaseTool):
    """Schreibt einen AgentLink-Handoff — State-Transfer an anderen Agenten."""

    @property
    def id(self) -> str:   return "write_handoff"
    @property
    def name(self) -> str: return "AgentLink Handoff schreiben"
    @property
    def description(self) -> str:
        return (
            "Speichert einen Handoff im AgentLink-System damit ein anderer Agent "
            "den Auftrag und den Kontext uebernehmen kann. "
            "to_agent leer lassen damit jeder Agent lesen kann."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to_agent": {
                    "type":        "string",
                    "description": "Ziel-Agent-ID (leer = any)",
                },
                "context": {
                    "type":        "string",
                    "description": "Freitext-Kontext fuer den empfangenden Agenten",
                },
                "data": {
                    "type":        "object",
                    "description": "Strukturierte Daten (JSON) die uebergeben werden",
                },
                "ttl_seconds": {
                    "type":        "integer",
                    "description": "Gueltigkeit in Sekunden (Standard: 3600)",
                },
            },
            "required": [],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        to_agent: str = "",
        context: str = "",
        data: dict | None = None,
        ttl_seconds: int = 3600,
    ) -> dict:
        from pathlib import Path as _Path
        from .agentlink import write_handoff as _wh
        project_dir = _Path("/projects") / project_id
        return _wh(
            project_dir,
            from_agent=agent_id,
            to_agent=to_agent or None,
            context=context,
            data=data or {},
            ttl_seconds=ttl_seconds,
        )


class ReadHandoffTool(BaseTool):
    """Liest den naechsten AgentLink-Handoff fuer diesen Agenten."""

    @property
    def id(self) -> str:   return "read_handoff"
    @property
    def name(self) -> str: return "AgentLink Handoff lesen"
    @property
    def description(self) -> str:
        return (
            "Liest den naechsten Handoff aus dem AgentLink-System der fuer diesen "
            "Agenten bestimmt ist. consume=true loescht den Handoff nach dem Lesen "
            "(Standard). Gibt null zurueck wenn kein Handoff vorhanden."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "consume": {
                    "type":        "boolean",
                    "description": "Handoff nach dem Lesen loeschen (Standard: true)",
                },
            },
            "required": [],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        consume: bool = True,
    ) -> dict:
        from pathlib import Path as _Path
        from .agentlink import read_handoff as _rh
        project_dir = _Path("/projects") / project_id
        entry = _rh(project_dir, to_agent=agent_id, consume=consume)
        if entry is None:
            return {"handoff": None, "found": False}
        return {"handoff": entry, "found": True}


# ============================================================= System Tools (Superagent)

class ShellExecTool(BaseTool):
    """
    Fuehrt einen Shell-Befehl aus und gibt stdout/stderr zurueck.
    Nur fuer Superagenten — kein Sandbox, voller Systemzugriff.
    """

    @property
    def id(self) -> str:   return "shell_exec"
    @property
    def name(self) -> str: return "Shell-Befehl ausführen"
    @property
    def description(self) -> str:
        return (
            "Führt einen Bash-Befehl aus und gibt stdout, stderr und Exit-Code zurück. "
            "Verwende dies für Systemverwaltung, git, pip, systemctl, Dateioperationen, etc. "
            "Timeout standard 30 Sekunden, maximal 120 Sekunden."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type":        "string",
                    "description": "Bash-Befehl der ausgeführt werden soll",
                },
                "timeout": {
                    "type":        "integer",
                    "description": "Timeout in Sekunden (Standard: 30, max: 120)",
                },
                "cwd": {
                    "type":        "string",
                    "description": "Arbeitsverzeichnis (Standard: /opt/octopos)",
                },
            },
            "required": ["command"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        command: str, timeout: int = 30, cwd: str = "/opt/octopos",
    ) -> dict:
        import asyncio
        timeout = min(max(timeout, 1), 120)
        logger.info("shell_exec [%s]: %s", agent_id, command[:120])
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd if Path(cwd).exists() else "/opt/octopos",
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return {"error": f"Timeout nach {timeout}s", "command": command, "exit_code": -1}

            return {
                "stdout":    stdout.decode(errors="replace"),
                "stderr":    stderr.decode(errors="replace"),
                "exit_code": proc.returncode,
                "command":   command,
            }
        except Exception as e:
            return {"error": str(e), "command": command, "exit_code": -1}


class ReadSystemFileTool(BaseTool):
    """Liest eine beliebige Datei auf dem System — kein Pfad-Sandboxing."""

    @property
    def id(self) -> str:   return "read_system_file"
    @property
    def name(self) -> str: return "Systemdatei lesen"
    @property
    def description(self) -> str:
        return (
            "Liest den Inhalt einer beliebigen Datei auf dem System. "
            "Kein Pfad-Sandboxing — Zugriff auf Sourcen, Configs, Logs etc. "
            "Für große Dateien: offset und limit nutzen."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type":        "string",
                    "description": "Absoluter oder relativer Dateipfad",
                },
                "offset": {
                    "type":        "integer",
                    "description": "Zeile ab der gelesen wird (0-basiert, Standard: 0)",
                },
                "limit": {
                    "type":        "integer",
                    "description": "Maximale Anzahl Zeilen (Standard: 200, max: 1000)",
                },
            },
            "required": ["path"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        path: str, offset: int = 0, limit: int = 200,
    ) -> dict:
        limit = min(limit, 1000)
        p = Path(path)
        if not p.is_absolute():
            p = Path("/opt/octopos") / path
        logger.info("read_system_file [%s]: %s (offset=%d limit=%d)", agent_id, p, offset, limit)
        if not p.exists():
            return {"error": f"Datei nicht gefunden: {p}"}
        if not p.is_file():
            return {"error": f"Kein reguläre Datei: {p}"}
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            sliced = lines[offset:offset + limit]
            return {
                "content":      "\n".join(sliced),
                "path":         str(p),
                "total_lines":  total,
                "offset":       offset,
                "returned":     len(sliced),
            }
        except OSError as e:
            return {"error": str(e)}


class WriteSystemFileTool(BaseTool):
    """Schreibt eine beliebige Datei auf dem System — kein Pfad-Sandboxing."""

    @property
    def id(self) -> str:   return "write_system_file"
    @property
    def name(self) -> str: return "Systemdatei schreiben"
    @property
    def description(self) -> str:
        return (
            "Schreibt Inhalt in eine beliebige Datei auf dem System. "
            "Erstellt die Datei und fehlende Verzeichnisse wenn nötig. "
            "mode=overwrite (Standard) oder append."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type":        "string",
                    "description": "Absoluter Dateipfad",
                },
                "content": {
                    "type":        "string",
                    "description": "Inhalt der geschrieben werden soll",
                },
                "mode": {
                    "type":        "string",
                    "enum":        ["overwrite", "append"],
                    "description": "overwrite (Standard) oder append",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        path: str, content: str, mode: str = "overwrite",
    ) -> dict:
        p = Path(path)
        if not p.is_absolute():
            p = Path("/opt/octopos") / path
        logger.info("write_system_file [%s]: %s (%s, %d bytes)", agent_id, p, mode, len(content))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            write_mode = "a" if mode == "append" else "w"
            p.open(write_mode, encoding="utf-8").write(content)
            return {"written": True, "path": str(p), "bytes": len(content.encode())}
        except OSError as e:
            return {"error": str(e)}


# ============================================================= Memory Tools (#85)

import re as _re

def _safe_memory_filename(filename: str) -> str:
    """Normalisiert Dateinamen: nur a-z0-9_- erlaubt, erzwingt .md Extension."""
    base = filename.removesuffix(".md").strip()
    if not _re.match(r"^[a-z0-9_-]+$", base):
        raise ValueError(f"Ungültiger Dateiname: '{filename}'. Nur a-z, 0-9, _ und - erlaubt.")
    return base + ".md"


class ReadMemoryTool(BaseTool):
    """Liest Dateien aus dem persönlichen Gedächtnis-Verzeichnis des Agenten."""

    @property
    def id(self) -> str:   return "read_memory"
    @property
    def name(self) -> str: return "Gedächtnis lesen"
    @property
    def description(self) -> str:
        return (
            "Liest Dateien aus dem eigenen persistenten Gedächtnis. "
            "Ohne filename: listet alle vorhandenen Gedächtnis-Dateien auf. "
            "Mit filename: gibt den Inhalt dieser Datei zurück."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type":        "string",
                    "description": "Dateiname (ohne Pfad, z.B. 'facts' oder 'facts.md'). Leer lassen um alle Dateien aufzulisten.",
                },
            },
            "required": [],
        }

    async def execute(self, agent_id: str, project_id: str, filename: str = "") -> dict:
        memory_dir = AGENTS_ROOT / agent_id / "memory"
        if not filename:
            if not memory_dir.exists():
                return {"files": [], "count": 0}
            files = sorted(p.name for p in memory_dir.glob("*.md"))
            return {"files": files, "count": len(files)}
        try:
            safe = _safe_memory_filename(filename)
        except ValueError as e:
            return {"error": str(e)}
        p = memory_dir / safe
        if not p.exists():
            return {"error": f"Datei '{safe}' nicht gefunden."}
        return {"filename": safe, "content": p.read_text(encoding="utf-8")}


class WriteMemoryTool(BaseTool):
    """Schreibt in das persönliche Gedächtnis-Verzeichnis des Agenten."""

    @property
    def id(self) -> str:   return "write_memory"
    @property
    def name(self) -> str: return "Gedächtnis schreiben"
    @property
    def description(self) -> str:
        return (
            "Speichert Information dauerhaft im eigenen Gedächtnis. "
            "Verwende aussagekräftige Dateinamen wie 'project-context', 'learned-facts', 'user-preferences'. "
            "mode=overwrite (Standard) ersetzt die Datei, mode=append hängt Text an."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type":        "string",
                    "description": "Dateiname ohne Pfad (z.B. 'learned-facts' oder 'learned-facts.md')",
                },
                "content": {
                    "type":        "string",
                    "description": "Inhalt der gespeichert werden soll (Markdown empfohlen)",
                },
                "mode": {
                    "type":        "string",
                    "enum":        ["overwrite", "append"],
                    "description": "overwrite (Standard) oder append",
                },
            },
            "required": ["filename", "content"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        filename: str, content: str, mode: str = "overwrite",
    ) -> dict:
        try:
            safe = _safe_memory_filename(filename)
        except ValueError as e:
            return {"error": str(e)}
        memory_dir = AGENTS_ROOT / agent_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        p = memory_dir / safe
        write_mode = "a" if mode == "append" else "w"
        p.open(write_mode, encoding="utf-8").write(content)
        logger.info("write_memory [%s]: %s (%s, %d bytes)", agent_id, safe, mode, len(content))
        return {"saved": True, "filename": safe, "bytes": len(content.encode())}


# ============================================================= Globale Registry

registry = ToolRegistry()
registry.register(DispatchTaskTool())
registry.register(FileReadTool())
registry.register(FileWriteTool())
registry.register(WebSearchTool())
registry.register(HttpRequestTool())
registry.register(SpawnAgentTool())
registry.register(WriteHandoffTool())
registry.register(ReadHandoffTool())
registry.register(ShellExecTool())
registry.register(ReadSystemFileTool())
registry.register(WriteSystemFileTool())
registry.register(ReadMemoryTool())
registry.register(WriteMemoryTool())

