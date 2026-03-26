"""
tool_registry.py — Zentrales Tool-Registry (#8, #54, TL1-TL5)

BaseTool ABC definiert das Interface. ToolRegistry haelt alle verfuegbaren Tools.
Was ein Agent nutzen darf: agent.yaml ∩ Registry ∩ permissions = LLM-sichtbar.
Tool nicht in Registry = existiert nicht (egal was in agent.yaml steht).

#54: Filesystem-Tools pruefen ob angeforderter Pfad innerhalb /projects/<id>/ liegt.
Path-Traversal und Zugriff ausserhalb des Projekt-Verzeichnisses werden verweigert.
"""

import logging
import shlex as _shlex_shell
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path("/projects")
AGENTS_ROOT   = Path("/agents")

# Wird von main.py im Lifespan gesetzt; ermöglicht interne Core-Calls ohne IP-Bypass
_internal_secret: str = ""

# Wird von main.py im Lifespan gesetzt; None = Rate-Limiting deaktiviert
_rate_limiter: Any = None

# Admin-Tool-Globals — gesetzt von main.py im Lifespan
_discovery: Any = None
_projects_registry: Any = None
_get_provisioner: Any = None
_load_users_fn: Any = None
_audit_log_fn: Any = None
_admin_agents_dir: str = "/agents"
_admin_projects_dir: str = "/projects"
_admin_runtime: Any = None


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
    Einheitliches Interface fuer alle HydraHive-Tools (TL2).
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
        perms  = set(agent_permissions) if agent_permissions is not None else None
        result = []
        for tool_id in agent_tool_ids:
            tool = self._tools.get(tool_id)
            if tool is None:
                logger.debug("Tool '%s' nicht in Registry — ignoriert", tool_id)
                continue
            if perms is not None and tool.permissions_required and not perms.issuperset(tool.permissions_required):
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
        path: str, content: str = "", mode: str = "overwrite", **kwargs,
    ) -> dict:
        if content is None or content == "":
            return {"error": "content darf nicht leer sein — bitte Dateiinhalt übergeben", "path": path}
        try:
            safe_path = assert_path_within_project(path, project_id)
        except PathSafetyError as e:
            return {"error": str(e), "allowed": False}

        try:
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            write_mode = "a" if mode == "append" else "w"
            with safe_path.open(write_mode, encoding="utf-8") as handle:
                handle.write(content)
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

def _handoff_dir(project_id: str):
    """
    Gibt das Handoff-Basisverzeichnis zurück.
    Normale Projekte: /projects/{id}/
    Persönliche Agenten (personal_*) und direkte Agent-Chats: /agents/{id}/
    """
    from pathlib import Path as _P
    project_path = _P("/projects") / project_id
    if project_path.exists():
        return project_path
    agent_path = _P("/agents") / project_id
    if agent_path.exists():
        return agent_path
    return project_path  # Fallback — wird ggf. angelegt


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
    def permissions_required(self) -> list[str]:
        return ["handoff.write"]

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
        from . import agentlink_client as _alc
        if _alc.is_available():
            try:
                return await _alc.write_handoff_remote(
                    from_agent=agent_id,
                    to_agent=to_agent or None,
                    context=context,
                    data=data or {},
                )
            except Exception as e:
                logger.warning("AgentLink write_handoff remote fehlgeschlagen, Fallback: %s", e)
        # Fallback: file-basiert
        from .agentlink import write_handoff as _wh
        project_dir = _handoff_dir(project_id)
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
    def permissions_required(self) -> list[str]:
        return ["handoff.read"]

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
        from . import agentlink_client as _alc
        if _alc.is_available():
            try:
                entry = await _alc.read_handoff_remote(agent_id=agent_id, consume=consume)
                if entry is not None:
                    return {"handoff": entry, "found": True}
                # Kein Handoff remote → auch file-basiert prüfen
            except Exception as e:
                logger.warning("AgentLink read_handoff remote fehlgeschlagen, Fallback: %s", e)
        # Fallback: file-basiert
        from .agentlink import read_handoff as _rh
        project_dir = _handoff_dir(project_id)
        entry = _rh(project_dir, to_agent=agent_id, consume=consume)
        if entry is None:
            return {"handoff": None, "found": False}
        return {"handoff": entry, "found": True}


# ============================================================= System Tools (Superagent)

import re as _re_shell

# Kommandos die niemals ausgeführt werden dürfen — unabhängig vom Agenten
_SHELL_BLOCKLIST: list[tuple[str, str]] = [
    # Rekursives Löschen
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f?|-[a-zA-Z]*f[a-zA-Z]*r|--recursive)\b", "rm -r / rm -rf verboten"),
    (r"\brm\b.*\s/opt/",            "rm auf /opt/ verboten"),
    (r"\brmdir\s+--parents\b",      "rmdir --parents verboten"),
    # Disk-Destruktion
    (r"\bdd\b.*\bof=/dev/",         "dd auf Blockdevice verboten"),
    (r"\bmkfs\b",                   "mkfs verboten"),
    (r"\bfdisk\b",                  "fdisk verboten"),
    (r"\bparted\b",                 "parted verboten"),
    (r"\bshred\b",                  "shred verboten"),
    (r"\bwipefs\b",                 "wipefs verboten"),
    # Service-Sabotage (HydraHive selbst killen)
    (r"\bsystemctl\s+(stop|disable|mask|kill)\s+(hydrahive|hydrahive)",  "systemctl stop/disable hydrahive/hydrahive verboten"),
    (r"\bkillall\s+uvicorn\b",      "killall uvicorn verboten"),
    (r"\bkill\b.*\buvicorn\b",      "kill uvicorn verboten"),
    # Fork-Bombe / Wildcard-Gefahr
    (r":\(\)\s*\{",                 "Fork-Bombe verboten"),
    (r"\brm\s+(-[a-zA-Z]+ +)?/\s", "rm / verboten"),
    (r"\brm\s+(-[a-zA-Z]+ +)?/$",  "rm / verboten"),
    # Schreiben in geschützte Systempfade via Redirects / tee / install / cp
    (r">\s*/opt/(hydrahive|hydrahive)/", "Redirect nach /opt/hydrahive/ verboten"),
    (r">\s*/etc/",                  "Redirect nach /etc/ verboten"),
    (r">\s*/bin/",                  "Redirect nach /bin/ verboten"),
    (r">\s*/usr/",                  "Redirect nach /usr/ verboten"),
    (r">\s*/lib",                   "Redirect nach /lib verboten"),
    (r">\s*/boot/",                 "Redirect nach /boot/ verboten"),
    (r">\s*/dev/",                  "Redirect nach /dev/ verboten"),
    (r">\s*/sys/",                  "Redirect nach /sys/ verboten"),
    (r">\s*/proc/",                 "Redirect nach /proc/ verboten"),
    (r"\btee\s+(/etc/|/opt/|/usr/|/bin/|/boot/|/lib|/sys/|/proc/)", "tee auf Systempfad verboten"),
    (r"\bcp\b.+\s(/etc/|/opt/(hydrahive|hydrahive)/|/usr/|/bin/|/boot/)", "cp nach Systempfad verboten"),
    (r"\b(wget|curl)\b.*\s-[a-zA-Z]*[oO]\s+/etc/", "Download nach /etc/ verboten"),
    (r"\b(wget|curl)\b.*\s-[a-zA-Z]*[oO]\s+/opt/(hydrahive|hydrahive)/", "Download nach /opt/hydrahive/ verboten"),
    # chmod/chown auf Systempfade
    (r"\b(chmod|chown)\b.*/opt/",   "chmod/chown auf /opt/ verboten"),
    (r"\b(chmod|chown)\b.*/etc/",   "chmod/chown auf /etc/ verboten"),
    (r"\b(chmod|chown)\b.*/bin/",   "chmod/chown auf /bin/ verboten"),
    # git clone/reset --hard auf /opt/
    (r"\bgit\b.*--hard\b.*\s/opt/", "git reset --hard auf /opt/ verboten"),
    (r"\bgit\s+clone\b.*\s/opt/",   "git clone nach /opt/ verboten"),
    (r"cd\s+/opt/(hydrahive|hydrahive)\b.*&&.*\bgit\b", "git in /opt/hydrahive/ verboten"),
    # Inline-Code-Ausführung in Interpreter (python -c, perl -e, etc.)
    (r"\bpython[23]?\s+-[a-zA-Z]*c\b", "python -c (Inline-Code) verboten"),
    (r"\bperl\s+-[a-zA-Z]*e\b",     "perl -e (Inline-Code) verboten"),
    (r"\bruby\s+-[a-zA-Z]*e\b",     "ruby -e (Inline-Code) verboten"),
    (r"\bnode\s+-[a-zA-Z]*e\b",     "node -e (Inline-Code) verboten"),
    (r"\bnodejs\s+-[a-zA-Z]*e\b",   "nodejs -e (Inline-Code) verboten"),
    # sudo — Agenten brauchen keine Root-Rechte
    (r"\bsudo\b",                   "sudo verboten — Agenten laufen ohne Root"),
    # Subshell-/Command-Substitution-Gefahr
    (r"\$\(",                       "Command Substitution $(...) verboten"),
    (r"`",                          "Backticks verboten"),
    # CWD-Manipulation zu Systempfaden
    (r"\bcd\s+(/etc|/opt/(hydrahive|hydrahive)|/bin|/usr|/boot|/lib|/sys|/proc)\b", "cd in Systempfad verboten"),
]

# Shell-Wrapper-Programme: erste Token prüfen, ob -c folgt
_SHELL_WRAPPERS = {"bash", "sh", "zsh", "fish", "dash", "ksh"}
# Wrapper-Programme die weitere Befehle einleiten (env, nohup, etc.)
_EXEC_WRAPPERS  = {"env", "nohup", "nice", "ionice", "timeout", "xargs", "sudo", "su"}


def _check_shell_blocklist(command: str) -> str | None:
    """
    Gibt die Fehlermeldung zurück wenn der Befehl blockiert ist, sonst None.
    Prüft Regex-Patterns und löst Shell-/Exec-Wrapper rekursiv auf.
    """
    for pattern, reason in _SHELL_BLOCKLIST:
        if _re_shell.search(pattern, command, _re_shell.IGNORECASE):
            return reason

    try:
        tokens = _shlex_shell.split(command)
    except ValueError:
        return None

    if not tokens:
        return None

    for token in tokens:
        if token == "eval":
            return "eval verboten"

    exe = Path(tokens[0]).name.lower()

    # Shell-Wrapper: bash -c "...", sh -c "..."
    if exe in _SHELL_WRAPPERS:
        for idx, token in enumerate(tokens[1:], start=1):
            if token == "-c" or (token.startswith("-") and "c" in token[1:]):
                if idx + 1 < len(tokens):
                    return _check_shell_blocklist(tokens[idx + 1])
                break

    # Exec-Wrapper: env bash -c "...", nohup rm -rf ..., timeout 30 rm -rf ...
    if exe in _EXEC_WRAPPERS:
        # Überspringe Optionen und env-Variablen (VAR=value), prüfe den echten Befehl
        rest = tokens[1:]
        while rest and (rest[0].startswith("-") or "=" in rest[0]):
            rest = rest[1:]
        if rest:
            return _check_shell_blocklist(" ".join(rest))

    return None


# Erlaubte CWD-Präfixe für shell_exec (verhindert Ausführung aus Systempfaden)
_ALLOWED_CWD_PREFIXES = ("/tmp", "/projects", "/home", "/agents", "/var/tmp")


def _validate_shell_cwd(cwd: str) -> str | None:
    """Gibt Fehlermeldung zurück wenn CWD nicht in einem erlaubten Verzeichnis liegt."""
    try:
        import os
        normalized = os.path.normpath(cwd)
        for prefix in _ALLOWED_CWD_PREFIXES:
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return None
        return f"CWD '{cwd}' nicht erlaubt — nur {', '.join(_ALLOWED_CWD_PREFIXES)}"
    except Exception:
        return None  # Im Zweifel: nicht blockieren


def _validate_gitea_issue_text(title: str, body: str = "") -> str | None:
    title = title.strip()
    if len(title) > 256:
        return "Issue-Titel zu lang (max. 256 Zeichen)"
    if len(body) > 20000:
        return "Issue-Body zu lang (max. 20000 Zeichen)"
    return None


class ShellExecTool(BaseTool):
    """
    Fuehrt einen Shell-Befehl aus und gibt stdout/stderr zurueck.
    Nur fuer Superagenten — kein Sandbox, voller Systemzugriff.
    Destruktive Kommandos (rm -rf, dd, mkfs, ...) und Zugriffe auf
    /opt/hydrahive/ (bzw. /opt/hydrahive/) werden blockiert.
    """

    @property
    def id(self) -> str:   return "shell_exec"
    @property
    def name(self) -> str: return "Shell-Befehl ausführen"
    @property
    def description(self) -> str:
        return (
            "Führt einen Bash-Befehl aus (stdout/stderr/exit_code). Timeout max 120s. "
            "VERBOTEN: rm -rf, dd, mkfs, fdisk, Schreiben nach /opt/hydrahive/, systemctl stop hydrahive."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["shell.exec"]

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
                    "description": "Arbeitsverzeichnis (Standard: /tmp)",
                },
            },
            "required": ["command"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        command: str, timeout: int = 30, cwd: str = "/tmp",
    ) -> dict:
        import asyncio

        blocked = _check_shell_blocklist(command)
        if blocked:
            logger.warning("shell_exec BLOCKED [%s]: %s — %s", agent_id, command[:120], blocked)
            return {
                "error":     f"Befehl blockiert: {blocked}",
                "command":   command,
                "exit_code": -1,
                "blocked":   True,
            }

        cwd_error = _validate_shell_cwd(cwd)
        if cwd_error:
            logger.warning("shell_exec CWD BLOCKED [%s]: %s", agent_id, cwd_error)
            return {"error": cwd_error, "command": command, "exit_code": -1, "blocked": True}

        timeout = min(max(timeout, 1), 120)
        safe_cwd = cwd if Path(cwd).exists() else "/tmp"
        logger.info("shell_exec [%s]: %s", agent_id, command[:120])
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=safe_cwd,
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
    def permissions_required(self) -> list[str]:
        return ["system.read"]

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
            for base in [Path("/opt/hydrahive"), Path("/opt/hydrahive")]:
                if base.exists():
                    p = base / path
                    break
            else:
                p = Path("/opt/hydrahive") / path
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
    def permissions_required(self) -> list[str]:
        return ["system.write"]

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
            for base in [Path("/opt/hydrahive"), Path("/opt/hydrahive")]:
                if base.exists():
                    p = base / path
                    break
            else:
                p = Path("/opt/hydrahive") / path
        logger.info("write_system_file [%s]: %s (%s, %d bytes)", agent_id, p, mode, len(content))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            write_mode = "a" if mode == "append" else "w"
            with p.open(write_mode, encoding="utf-8") as handle:
                handle.write(content)
            return {"written": True, "path": str(p), "bytes": len(content.encode())}
        except OSError as e:
            return {"error": str(e)}


# ============================================================= Memory Tools (#85)

def _safe_memory_filename(filename: str) -> str:
    """Normalisiert Dateinamen: a-z, A-Z, 0-9, _ und - erlaubt, erzwingt .md Extension."""
    base = filename.removesuffix(".md").strip()
    if not _re_shell.match(r"^[a-zA-Z0-9_-]+$", base):
        raise ValueError(f"Ungültiger Dateiname: '{filename}'. Nur a-z, A-Z, 0-9, _ und - erlaubt.")
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
    def permissions_required(self) -> list[str]:
        return ["memory.read"]

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
        return "Speichert Text dauerhaft im Gedächtnis. mode=overwrite ersetzt, append hängt an."

    @property
    def permissions_required(self) -> list[str]:
        return ["memory.write"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type":        "string",
                    "description": "Dateiname (z.B. 'learned-facts')",
                },
                "content": {
                    "type":        "string",
                    "description": "Inhalt (Markdown)",
                },
                "mode": {
                    "type":        "string",
                    "enum":        ["overwrite", "append"],
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
        with p.open(write_mode, encoding="utf-8") as handle:
            handle.write(content)
        logger.info("write_memory [%s]: %s (%s, %d bytes)", agent_id, safe, mode, len(content))
        return {"saved": True, "filename": safe, "bytes": len(content.encode())}


# ============================================================= Self-Learning Skills (#7)

def _safe_skill_filename(filename: str) -> str:
    """Normalisiert Skill-Dateinamen: a-z, A-Z, 0-9, _ und - erlaubt, erzwingt .md Extension."""
    base = filename.removesuffix(".md").strip()
    if not _re_shell.match(r"^[a-zA-Z0-9_-]+$", base):
        raise ValueError(f"Ungültiger Dateiname: '{filename}'. Nur a-z, A-Z, 0-9, _ und - erlaubt.")
    return base + ".md"


_SKILL_FRONTMATTER_TEMPLATE = """\
---
skill: {skill_id}
version: "1.0"
scope: {scope}
author: agent
triggers:
{triggers_yaml}priority: {priority}
---

{content}"""


class CreateSkillTool(BaseTool):
    """Erstellt oder aktualisiert einen eigenen Skill im persönlichen Skill-Verzeichnis."""

    @property
    def id(self) -> str:   return "create_skill"
    @property
    def name(self) -> str: return "Skill erstellen"
    @property
    def description(self) -> str:
        return (
            "Erstellt/aktualisiert einen Skill (wiederverwendbares Wissen). "
            "on-demand: bei Keyword-Match geladen; always: immer geladen."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["memory.write"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type":        "string",
                    "description": "Dateiname (z.B. 'deploy-prozess')",
                },
                "skill_id": {
                    "type":        "string",
                    "description": "Skill-Bezeichner",
                },
                "triggers": {
                    "type":        "array",
                    "items":       {"type": "string"},
                    "description": "Aktivierungs-Keywords",
                },
                "content": {
                    "type":        "string",
                    "description": "Skill-Inhalt (Markdown)",
                },
                "scope": {
                    "type":        "string",
                    "enum":        ["on-demand", "always"],
                },
                "priority": {
                    "type":        "integer",
                    "description": "Sortierung (Standard: 50)",
                },
            },
            "required": ["filename", "skill_id", "triggers", "content"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        filename: str, skill_id: str, triggers: list, content: str,
        scope: str = "on-demand", priority: int = 50,
    ) -> dict:
        try:
            safe = _safe_skill_filename(filename)
        except ValueError as e:
            return {"error": str(e)}

        skills_dir = AGENTS_ROOT / agent_id / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        triggers_yaml = "".join(f"  - {t}\n" for t in triggers)
        text = _SKILL_FRONTMATTER_TEMPLATE.format(
            skill_id=skill_id,
            scope=scope,
            triggers_yaml=triggers_yaml,
            priority=priority,
            content=content.strip(),
        )

        p = skills_dir / safe
        p.write_text(text, encoding="utf-8")
        try:
            p.chmod(0o600)
        except Exception:
            pass

        logger.info("create_skill [%s]: %s (triggers=%s)", agent_id, safe, triggers)
        return {"created": True, "filename": safe, "skill_id": skill_id, "triggers": triggers}


class ListSkillsTool(BaseTool):
    """Listet alle eigenen Skills mit Metadaten auf."""

    @property
    def id(self) -> str:   return "list_skills"
    @property
    def name(self) -> str: return "Skills auflisten"
    @property
    def description(self) -> str:
        return (
            "Listet alle vorhandenen Skills des Agenten auf (Dateiname, Skill-ID, Scope, Triggers, Author). "
            "Hilft zu entscheiden ob ein neuer Skill nötig ist oder ein bestehender aktualisiert werden soll."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["memory.read"]

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, agent_id: str, project_id: str) -> dict:
        import yaml as _yaml
        import re as _re
        skills_dir = AGENTS_ROOT / agent_id / "skills"
        if not skills_dir.exists():
            return {"skills": [], "count": 0}

        FRONTMATTER_RE = _re.compile(r"^---\s*\n(.*?)\n---\s*\n", _re.DOTALL)
        skills = []
        for p in sorted(skills_dir.glob("*.md")):
            entry: dict = {"filename": p.name, "skill_id": "", "scope": "", "triggers": [], "author": "system"}
            try:
                text = p.read_text(encoding="utf-8")
                m = FRONTMATTER_RE.match(text)
                if m:
                    fm = _yaml.safe_load(m.group(1)) or {}
                    entry["skill_id"] = fm.get("skill", "")
                    entry["scope"]    = fm.get("scope", "")
                    entry["triggers"] = fm.get("triggers", [])
                    entry["author"]   = fm.get("author", "system")
            except Exception:
                pass
            skills.append(entry)

        return {"skills": skills, "count": len(skills)}


class DeleteSkillTool(BaseTool):
    """Löscht einen selbst angelegten Skill. Nur Skills mit author=agent können gelöscht werden."""

    @property
    def id(self) -> str:   return "delete_skill"
    @property
    def name(self) -> str: return "Skill löschen"
    @property
    def description(self) -> str:
        return (
            "Löscht einen eigenen Skill aus dem Skill-Verzeichnis. "
            "Nur Skills die mit create_skill angelegt wurden (author=agent) können gelöscht werden. "
            "System-Skills (author=system oder kein author) sind geschützt."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["memory.write"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type":        "string",
                    "description": "Dateiname des zu löschenden Skills (z.B. 'deploy-prozess.md')",
                },
            },
            "required": ["filename"],
        }

    async def execute(self, agent_id: str, project_id: str, filename: str) -> dict:
        import yaml as _yaml
        import re as _re

        try:
            safe = _safe_skill_filename(filename)
        except ValueError as e:
            return {"error": str(e)}

        p = AGENTS_ROOT / agent_id / "skills" / safe
        if not p.exists():
            return {"error": f"Skill '{safe}' nicht gefunden."}

        # Author-Prüfung: nur agent-erstellte Skills dürfen gelöscht werden
        FRONTMATTER_RE = _re.compile(r"^---\s*\n(.*?)\n---\s*\n", _re.DOTALL)
        try:
            text = p.read_text(encoding="utf-8")
            m = FRONTMATTER_RE.match(text)
            author = "system"
            if m:
                fm = _yaml.safe_load(m.group(1)) or {}
                author = fm.get("author", "system")
        except Exception:
            author = "system"

        if author != "agent":
            return {"error": f"Skill '{safe}' ist ein System-Skill (author={author}) und kann nicht gelöscht werden."}

        p.unlink()
        logger.info("delete_skill [%s]: %s gelöscht", agent_id, safe)
        return {"deleted": True, "filename": safe}


# ============================================================= Agenten-Delegation (#107)

class AskAgentTool(BaseTool):
    """
    Synchrone Frage / Aufgabe an einen anderen Agenten.
    Der Ziel-Agent antwortet direkt — ideal für kurze Delegationen.
    Ruft intern POST /agents/{target}/message auf (localhost:8765).
    """

    @property
    def id(self) -> str:   return "ask_agent"
    @property
    def name(self) -> str: return "Agenten fragen (sync)"
    @property
    def description(self) -> str:
        return "Synchrone Frage/Task an einen anderen Agenten — antwortet direkt."

    @property
    def permissions_required(self) -> list[str]:
        return ["agents.ask"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type":        "string",
                    "description": "Ziel-Agent-ID",
                },
                "question": {
                    "type":        "string",
                    "description": "Frage/Task",
                },
                "context": {
                    "type":        "string",
                    "description": "Zusätzlicher Kontext (optional)",
                },
                "project_id": {
                    "type":        "string",
                    "description": "Projekt-ID in dessen Kontext der Ziel-Agent arbeiten soll (optional). Standardmäßig bekommt der Agent eine eigene Sandbox. Übergib die eigene project_id damit der Ziel-Agent auf dieselben Projektdateien zugreifen kann.",
                },
            },
            "required": ["target", "question"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        target: str, question: str, context: str = "", **kwargs
    ) -> dict:
        import aiohttp as _aio

        import hmac as _hmac
        import time as _time

        if _rate_limiter is not None:
            _rate_limiter.check_agent_call(agent_id)

        import uuid as _uuid
        content = f"{context}\n\n{question}".strip() if context else question
        logger.info("ask_agent [%s] → %s: %s…", agent_id, target, question[:60])
        headers: dict = {}
        if _internal_secret:
            ts = str(int(_time.time()))
            sig = _hmac.new(_internal_secret.encode(), ts.encode(), "sha256").hexdigest()
            headers = {"X-Internal-Timestamp": ts, "X-Internal-Signature": sig}
        # Optionale project_id: wenn angegeben, arbeitet der Ziel-Agent im selben Projektkontext
        explicit_project_id = kwargs.get("project_id", "")
        session_id = explicit_project_id.strip() if explicit_project_id else f"{target}_{_uuid.uuid4().hex[:8]}"
        try:
            async with _aio.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:8765/agents/{target}/message",
                    json={"content": content, "sender": agent_id, "project_id": session_id},
                    headers=headers,
                    timeout=_aio.ClientTimeout(total=300),
                ) as resp:
                    if resp.status == 404:
                        return {"error": f"Agent '{target}' nicht gefunden", "agent_id": target}
                    data = await resp.json()
                    return {
                        "agent_id": target,
                        "response": data.get("response", ""),
                        "success":  True,
                    }
        except Exception as e:
            return {"error": f"Fehler bei Kommunikation mit '{target}': {e}", "agent_id": target}


class DelegateAgentTool(BaseTool):
    """
    Asynchrone Delegation: schreibt einen AgentLink-Handoff an den Ziel-Agenten.
    Nützlich für lange Tasks die im Hintergrund laufen sollen.
    Das Ergebnis wird via read_handoff abgerufen wenn fertig.
    """

    @property
    def id(self) -> str:   return "delegate_agent"
    @property
    def name(self) -> str: return "Agenten beauftragen (async)"
    @property
    def description(self) -> str:
        return "Beauftragt einen Agenten asynchron via Handoff — kein direktes Warten auf Ergebnis."

    @property
    def permissions_required(self) -> list[str]:
        return ["agents.delegate"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type":        "string",
                    "description": "Ziel-Agent-ID",
                },
                "task": {
                    "type":        "string",
                    "description": "Task für den Agenten",
                },
                "context": {
                    "type":        "string",
                    "description": "Kontext (optional)",
                },
                "ttl_seconds": {
                    "type":        "integer",
                    "description": "Gültigkeit in Sekunden (Standard: 3600)",
                },
            },
            "required": ["target", "task"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        target: str, task: str, context: str = "", ttl_seconds: int = 3600,
    ) -> dict:
        from pathlib import Path as _Path
        from .agentlink import write_handoff as _wh

        if _rate_limiter is not None:
            _rate_limiter.check_agent_call(agent_id)

        # Handoff im /agents-Verzeichnis des Ziel-Agenten ablegen
        # (agent_id hier = aufrufender Agent, target = Ziel-Agent)
        handoff_base = _Path("/agents") / target
        handoff_base.mkdir(parents=True, exist_ok=True)

        entry = _wh(
            handoff_base,
            from_agent=agent_id,
            to_agent=target,
            context=context or task,
            data={"task": task, "from": agent_id},
            ttl_seconds=ttl_seconds,
        )
        logger.info("delegate_agent [%s] → %s: %s…", agent_id, target, task[:60])
        return {
            "delegated":   True,
            "target":      target,
            "handoff_id":  entry.get("id", ""),
            "ttl_seconds": ttl_seconds,
        }


# ============================================================= Git Tools (#gitea)

class GitStatusTool(BaseTool):
    """Zeigt den Git-Status des Projekt-Workspaces."""

    @property
    def id(self) -> str:   return "git_status"
    @property
    def name(self) -> str: return "Git Status"
    @property
    def description(self) -> str:
        return (
            "Zeigt den aktuellen Git-Status des Projekt-Workspaces: "
            "geänderte, neue und gelöschte Dateien, aktueller Branch. "
            "repo: optionales Ziel-Repo als URL, owner/repo oder Repo-Name, "
            "wenn das Ziel nicht dem aktuellen Projektkontext entspricht."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.read"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Projekt-ID (z.B. 'testprojekt')"},
                "repo": {"type": "string", "description": "Optionales Ziel-Repo als URL, owner/repo oder Repo-Name"},
            },
            "required": [],
        }

    async def execute(self, agent_id: str, project_id: str, **kwargs) -> dict:
        pid = kwargs.get("project_id") or project_id
        repo_ref = kwargs.get("repo", "")
        from .gitea import GiteaClient, get_gitea_client, resolve_git_target
        try:
            target = await resolve_git_target(get_gitea_client(), project_id=pid, repo=repo_ref)
            ws = await GiteaClient.git_workspace(
                target["workspace_key"],
                owner=target["owner"],
                repo=target["repo"],
            )
        except Exception as e:
            return {"error": str(e)}
        stdout, stderr, rc = await GiteaClient._git(["status", "--short", "--branch"], ws)
        branch_out, _, _ = await GiteaClient._git(["branch", "--show-current"], ws)
        return {
            "project_id": pid,
            "repo":      target["repo"],
            "owner":     target["owner"],
            "full_name": target["full_name"],
            "status":    stdout.strip(),
            "branch":    branch_out.strip(),
            "clean":     stdout.strip() == "" or stdout.strip().startswith("##") and "\n" not in stdout.strip(),
            "exit_code": rc,
        }


class GiteaRepoInspectTool(BaseTool):
    """Liest Repo-Metadaten und letzte Commits direkt via Gitea-API."""

    @property
    def id(self) -> str:   return "gitea_repo_inspect"
    @property
    def name(self) -> str: return "Gitea Repo pruefen"
    @property
    def description(self) -> str:
        return (
            "Prueft ein Gitea-Repository per URL oder owner/repo. "
            "Liefert Repo-Metadaten, Branch-Infos und die letzten Commits. "
            "Nutze dieses Tool fuer Repo-, Review- und Aenderungsfragen statt eines rohen URL-GET."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.read"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repo-Referenz als URL, owner/repo oder repo-Name",
                },
                "limit": {
                    "type": "integer",
                    "description": "Anzahl letzter Commits (Standard: 5)",
                },
            },
            "required": ["repo"],
        }

    async def execute(self, agent_id: str, project_id: str, repo: str, limit: int = 5) -> dict:
        from .gitea import get_gitea_client, resolve_repo_ref

        client = get_gitea_client()
        try:
            owner, name = resolve_repo_ref(repo, default_owner=client.org)
        except ValueError as e:
            return {"error": str(e), "repo": repo}

        try:
            info = await client.get_repo_by_full_name(owner, name)
            commits = await client.list_commits(owner, name, limit=max(1, min(limit, 10)))
        except Exception as e:
            return {"error": str(e), "owner": owner, "repo": name}

        recent_commits = []
        for item in commits:
            commit = item.get("commit", {})
            author = commit.get("author", {}) or {}
            recent_commits.append(
                {
                    "sha": (item.get("sha") or "")[:12],
                    "message": (commit.get("message") or "").splitlines()[0],
                    "author": author.get("name") or item.get("author", {}).get("login"),
                    "date": author.get("date"),
                }
            )

        return {
            "owner": owner,
            "repo": name,
            "full_name": info.get("full_name"),
            "html_url": info.get("html_url"),
            "default_branch": info.get("default_branch"),
            "private": info.get("private"),
            "updated_at": info.get("updated_at"),
            "description": info.get("description") or "",
            "open_pr_count": info.get("open_pr_counter"),
            "recent_commits": recent_commits,
        }


class GiteaRepoTreeTool(BaseTool):
    """Listet eine Repo-Struktur oder einen Unterordner via Gitea-API."""

    @property
    def id(self) -> str:   return "gitea_repo_tree"
    @property
    def name(self) -> str: return "Gitea Repo Struktur"
    @property
    def description(self) -> str:
        return (
            "Listet Dateien und Ordner eines Gitea-Repositories. "
            "Nutze dieses Tool fuer Deep-Dives in die Repo-Struktur."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.read"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo (URL/owner/repo/Name)"},
                "path": {"type": "string", "description": "Optionaler Unterpfad im Repo"},
                "ref": {"type": "string", "description": "Optionaler Branch, Tag oder Commit"},
            },
            "required": ["repo"],
        }

    async def execute(self, agent_id: str, project_id: str, repo: str, path: str = "", ref: str = "") -> dict:
        from .gitea import get_gitea_client, resolve_repo_ref

        client = get_gitea_client()
        try:
            owner, name = resolve_repo_ref(repo, default_owner=client.org)
            entries = await client.list_repo_tree(owner, name, path=path, ref=ref)
        except Exception as e:
            return {"error": str(e), "repo": repo, "path": path, "ref": ref}

        normalized = entries if isinstance(entries, list) else [entries]
        return {
            "owner": owner,
            "repo": name,
            "path": path.strip("/"),
            "ref": ref or "",
            "entries": [
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "type": item.get("type"),
                    "size": item.get("size"),
                    "sha": (item.get("sha") or "")[:12],
                }
                for item in normalized
            ],
        }


class GiteaRepoFileTool(BaseTool):
    """Liest eine konkrete Datei aus einem Gitea-Repo."""

    @property
    def id(self) -> str:   return "gitea_repo_file"
    @property
    def name(self) -> str: return "Gitea Repo Datei"
    @property
    def description(self) -> str:
        return (
            "Liest eine konkrete Datei aus einem Gitea-Repository. "
            "Nutze dieses Tool fuer gezielte Dateiansichten bei Reviews oder Deep-Dives."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.read"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo (URL/owner/repo/Name)"},
                "path": {"type": "string", "description": "Dateipfad im Repo"},
                "ref": {"type": "string", "description": "Optionaler Branch, Tag oder Commit"},
            },
            "required": ["repo", "path"],
        }

    async def execute(self, agent_id: str, project_id: str, repo: str, path: str, ref: str = "") -> dict:
        from .gitea import get_gitea_client, resolve_repo_ref

        client = get_gitea_client()
        try:
            owner, name = resolve_repo_ref(repo, default_owner=client.org)
            content = await client.get_repo_file(owner, name, path, ref=ref)
        except Exception as e:
            return {"error": str(e), "repo": repo, "path": path, "ref": ref}

        return {
            "owner": owner,
            "repo": name,
            "path": path.strip("/"),
            "ref": ref or "",
            "content": content[:20000],
            "truncated": len(content) > 20000,
        }


class GiteaRepoCommitsTool(BaseTool):
    """Listet Commits eines Repositories fuer Review-/Deep-Dive-Arbeit."""

    @property
    def id(self) -> str:   return "gitea_repo_commits"
    @property
    def name(self) -> str: return "Gitea Repo Commits"
    @property
    def description(self) -> str:
        return (
            "Listet die letzten Commits eines Gitea-Repositories. "
            "Nutze dieses Tool fuer Aenderungsserien, Review-Kontext und Verlauf."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.read"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo (URL/owner/repo/Name)"},
                "limit": {"type": "integer", "description": "Anzahl der Commits (Standard: 10, max: 30)"},
            },
            "required": ["repo"],
        }

    async def execute(self, agent_id: str, project_id: str, repo: str, limit: int = 10) -> dict:
        from .gitea import get_gitea_client, resolve_repo_ref

        client = get_gitea_client()
        try:
            owner, name = resolve_repo_ref(repo, default_owner=client.org)
            commits = await client.list_commits(owner, name, limit=max(1, min(limit, 30)))
        except Exception as e:
            return {"error": str(e), "repo": repo, "limit": limit}

        items = []
        for item in commits:
            commit = item.get("commit", {})
            author = commit.get("author", {}) or {}
            items.append(
                {
                    "sha": (item.get("sha") or "")[:12],
                    "message": (commit.get("message") or "").splitlines()[0],
                    "author": author.get("name") or item.get("author", {}).get("login"),
                    "date": author.get("date"),
                }
            )

        return {
            "owner": owner,
            "repo": name,
            "count": len(items),
            "commits": items,
        }


class GiteaRepoDiffTool(BaseTool):
    """Zeigt einen Diff fuer ein Repository ueber den lokalen Repo-Workspace."""

    @property
    def id(self) -> str:   return "gitea_repo_diff"
    @property
    def name(self) -> str: return "Gitea Repo Diff"
    @property
    def description(self) -> str:
        return (
            "Zeigt einen Diff fuer ein Gitea-Repository. "
            "Standardmaessig wird der letzte Commit gegen seinen Vorgaenger verglichen. "
            "Optional mit base, head und path eingrenzbar."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.read"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo (URL/owner/repo/Name)"},
                "base": {"type": "string", "description": "Basis-Ref oder Commit (optional)"},
                "head": {"type": "string", "description": "Ziel-Ref oder Commit (optional)"},
                "path": {"type": "string", "description": "Optionaler Pfad im Repo"},
                "stat_only": {"type": "boolean", "description": "Nur Diff-Stat statt vollem Patch"},
            },
            "required": ["repo"],
        }

    async def execute(
        self,
        agent_id: str,
        project_id: str,
        repo: str,
        base: str = "",
        head: str = "",
        path: str = "",
        stat_only: bool = False,
    ) -> dict:
        from .gitea import GiteaClient, get_gitea_client, resolve_repo_ref, repo_workspace_key

        client = get_gitea_client()
        try:
            owner, name = resolve_repo_ref(repo, default_owner=client.org)
            ws = await GiteaClient.git_workspace(repo_workspace_key(owner, name), owner=owner, repo=name)
            await GiteaClient._git(["fetch", "--all", "--prune"], ws)

            diff_head = head or "origin/main"
            diff_base = base
            if not diff_base:
                commits = await client.list_commits(owner, name, limit=2)
                if len(commits) >= 2:
                    diff_head = commits[0].get("sha") or diff_head
                    diff_base = commits[1].get("sha") or f"{diff_head}~1"
                else:
                    diff_base = f"{diff_head}~1"

            stat_args = ["diff", "--stat", f"{diff_base}..{diff_head}"]
            patch_args = ["diff", f"{diff_base}..{diff_head}"]
            if path:
                stat_args += ["--", path]
                patch_args += ["--", path]

            stat_out, stat_err, stat_rc = await GiteaClient._git(stat_args, ws)
            patch_out, patch_err, patch_rc = await GiteaClient._git(patch_args, ws)
        except Exception as e:
            return {
                "error": str(e),
                "repo": repo,
                "base": base,
                "head": head,
                "path": path,
            }

        patch_text = patch_out[:20000]
        return {
            "owner": owner,
            "repo": name,
            "base": diff_base,
            "head": diff_head,
            "path": path.strip("/"),
            "stat": stat_out.strip(),
            "diff": "" if stat_only else patch_text,
            "truncated": (not stat_only) and len(patch_out) > 20000,
            "stat_exit_code": stat_rc,
            "diff_exit_code": patch_rc,
            "stderr": (stat_err or patch_err)[:500],
        }


class GiteaCreateIssueTool(BaseTool):
    """Erstellt ein Gitea-Issue in einem Ziel-Repository."""

    @property
    def id(self) -> str:   return "gitea_create_issue"
    @property
    def name(self) -> str: return "Gitea Issue erstellen"
    @property
    def description(self) -> str:
        return (
            "Erstellt ein neues Issue in einem Gitea-Repository. "
            "Nutze dieses Tool fuer Findings, Review-Ergebnisse, Features oder Aufgaben."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.issue"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo (URL/owner/repo/Name)"},
                "title": {"type": "string", "description": "Issue-Titel"},
                "body": {"type": "string", "description": "Issue-Beschreibung in Markdown"},
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optionale Labels",
                },
            },
            "required": ["repo", "title"],
        }

    async def execute(
        self,
        agent_id: str,
        project_id: str,
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
    ) -> dict:
        from .gitea import get_gitea_client, resolve_repo_ref

        invalid = _validate_gitea_issue_text(title, body)
        if invalid:
            return {"error": invalid, "repo": repo, "title": title}

        client = get_gitea_client()
        try:
            owner, name = resolve_repo_ref(repo, default_owner=client.org)
            issue = await client.create_issue_for_repo(owner, name, title, body=body, labels=labels or [])
        except Exception as e:
            return {"error": str(e), "repo": repo, "title": title}

        return {
            "created": True,
            "owner": owner,
            "repo": name,
            "full_name": f"{owner}/{name}",
            "issue_number": issue.get("number"),
            "issue_url": issue.get("html_url"),
            "title": issue.get("title"),
        }


class GiteaCommentIssueTool(BaseTool):
    """Kommentiert ein bestehendes Gitea-Issue."""

    @property
    def id(self) -> str:   return "gitea_comment_issue"
    @property
    def name(self) -> str: return "Gitea Issue kommentieren"
    @property
    def description(self) -> str:
        return "Schreibt einen Kommentar in ein bestehendes Gitea-Issue."

    @property
    def permissions_required(self) -> list[str]:
        return ["git.issue"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo (URL/owner/repo/Name)"},
                "issue_number": {"type": "integer", "description": "Nummer des Ziel-Issues"},
                "body": {"type": "string", "description": "Kommentar in Markdown"},
            },
            "required": ["repo", "issue_number", "body"],
        }

    async def execute(
        self,
        agent_id: str,
        project_id: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> dict:
        from .gitea import get_gitea_client, resolve_repo_ref

        invalid = _validate_gitea_issue_text("", body)
        if invalid:
            return {"error": invalid, "repo": repo, "issue_number": issue_number}

        client = get_gitea_client()
        try:
            owner, name = resolve_repo_ref(repo, default_owner=client.org)
            comment = await client.comment_issue_for_repo(owner, name, issue_number, body)
        except Exception as e:
            return {"error": str(e), "repo": repo, "issue_number": issue_number}

        return {
            "commented": True,
            "owner": owner,
            "repo": name,
            "full_name": f"{owner}/{name}",
            "issue_number": issue_number,
            "comment_id": comment.get("id"),
            "comment_url": comment.get("html_url"),
        }


class GiteaUpdateIssueTool(BaseTool):
    """Aktualisiert oder schliesst ein bestehendes Gitea-Issue."""

    @property
    def id(self) -> str:   return "gitea_update_issue"
    @property
    def name(self) -> str: return "Gitea Issue aktualisieren"
    @property
    def description(self) -> str:
        return "Aktualisiert Titel/Body/Labels oder schliesst ein bestehendes Gitea-Issue."

    @property
    def permissions_required(self) -> list[str]:
        return ["git.issue"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo (URL/owner/repo/Name)"},
                "issue_number": {"type": "integer", "description": "Nummer des Ziel-Issues"},
                "title": {"type": "string", "description": "Neuer Titel (optional)"},
                "body": {"type": "string", "description": "Neuer Body (optional)"},
                "state": {
                    "type": "string",
                    "enum": ["open", "closed"],
                    "description": "Issue-Status (optional)",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optionale Label-Liste",
                },
            },
            "required": ["repo", "issue_number"],
        }

    async def execute(
        self,
        agent_id: str,
        project_id: str,
        repo: str,
        issue_number: int,
        title: str = "",
        body: str = "",
        state: str = "",
        labels: list[str] | None = None,
    ) -> dict:
        from .gitea import get_gitea_client, resolve_repo_ref

        invalid = _validate_gitea_issue_text(title or "", body or "")
        if invalid:
            return {"error": invalid, "repo": repo, "issue_number": issue_number}

        client = get_gitea_client()
        try:
            owner, name = resolve_repo_ref(repo, default_owner=client.org)
            issue = await client.update_issue_for_repo(
                owner,
                name,
                issue_number,
                title=title or None,
                body=body or None,
                state=state or None,
                labels=labels,
            )
        except Exception as e:
            return {"error": str(e), "repo": repo, "issue_number": issue_number}

        return {
            "updated": True,
            "owner": owner,
            "repo": name,
            "full_name": f"{owner}/{name}",
            "issue_number": issue.get("number"),
            "issue_url": issue.get("html_url"),
            "state": issue.get("state"),
            "title": issue.get("title"),
        }


class GitDiffTool(BaseTool):
    """Zeigt Änderungen im Workspace verglichen mit dem letzten Commit."""

    @property
    def id(self) -> str:   return "git_diff"
    @property
    def name(self) -> str: return "Git Diff"
    @property
    def description(self) -> str:
        return (
            "Zeigt die Unterschiede zwischen dem aktuellen Workspace und dem letzten Commit. "
            "path: optional — nur Diff für diese Datei/Verzeichnis. "
            "repo: optionales Ziel-Repo wenn es vom Projektkontext abweicht."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.read"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Projekt-ID (z.B. 'testprojekt')"},
                "repo": {"type": "string", "description": "Optionales Ziel-Repo als URL, owner/repo oder Repo-Name"},
                "path": {
                    "type":        "string",
                    "description": "Optionaler Pfad (relativ zum Workspace-Root)",
                },
                "staged": {
                    "type":        "boolean",
                    "description": "True um gestagete Änderungen zu zeigen (Standard: False)",
                },
            },
            "required": [],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        path: str = "", staged: bool = False, **kwargs,
    ) -> dict:
        pid = kwargs.get("project_id") or project_id
        repo_ref = kwargs.get("repo", "")
        from .gitea import GiteaClient, get_gitea_client, resolve_git_target
        try:
            target = await resolve_git_target(get_gitea_client(), project_id=pid, repo=repo_ref)
            ws = await GiteaClient.git_workspace(
                target["workspace_key"],
                owner=target["owner"],
                repo=target["repo"],
            )
        except Exception as e:
            return {"error": str(e)}
        args = ["diff"]
        if staged:
            args.append("--cached")
        if path:
            args += ["--", path]
        stdout, stderr, rc = await GiteaClient._git(args, ws)
        # Diff auf 10000 Zeichen begrenzen
        diff = stdout[:10000]
        return {
            "project_id": pid,
            "repo":      target["repo"],
            "owner":     target["owner"],
            "full_name": target["full_name"],
            "diff":      diff,
            "truncated": len(stdout) > 10000,
            "exit_code": rc,
        }


class GitCommitTool(BaseTool):
    """
    Erstellt einen Git-Commit aus Dateiinhalten im Projekt-Workspace.
    Schreibt die Dateien in den Workspace, staged sie und committet.
    """

    @property
    def id(self) -> str:   return "git_commit"
    @property
    def name(self) -> str: return "Git Commit"
    @property
    def description(self) -> str:
        return (
            "Schreibt Dateien in den Git-Workspace und erstellt einen Commit. "
            "files: Liste von {path, content} Objekten. "
            "message: Commit-Nachricht. "
            "branch: Branch-Name (Standard: feature/agent-<agent_id>)."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.write"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "files": {
                    "type":        "array",
                    "description": "Liste von Dateien die commitet werden sollen",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path":    {"type": "string", "description": "Pfad relativ zum Repo-Root"},
                            "content": {"type": "string", "description": "Dateiinhalt"},
                        },
                        "required": ["path", "content"],
                    },
                },
                "message": {
                    "type":        "string",
                    "description": "Commit-Nachricht",
                },
                "branch": {
                    "type":        "string",
                    "description": "Branch-Name (Standard: feature/agent-<agent_id>)",
                },
                "project_id": {"type": "string", "description": "Projekt-ID (z.B. 'testprojekt')"},
                "repo": {"type": "string", "description": "Optionales Ziel-Repo als URL, owner/repo oder Repo-Name"},
            },
            "required": ["files", "message"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        files: list, message: str, branch: str = "", **kwargs,
    ) -> dict:
        pid = kwargs.get("project_id") or project_id
        repo_ref = kwargs.get("repo", "")
        from .gitea import GiteaClient, get_gitea_client, resolve_git_target

        if not branch:
            safe_id = agent_id.replace("_", "-").replace(" ", "-")[:30]
            branch = f"feature/agent-{safe_id}"

        try:
            target = await resolve_git_target(get_gitea_client(), project_id=pid, repo=repo_ref)
            ws = await GiteaClient.git_workspace(
                target["workspace_key"],
                owner=target["owner"],
                repo=target["repo"],
            )
        except Exception as e:
            return {"error": str(e)}

        # Branch anlegen/wechseln
        out, err, rc = await GiteaClient._git(["checkout", "-B", branch], ws)
        if rc != 0:
            return {"error": f"Branch-Wechsel fehlgeschlagen: {err[:200]}"}

        # Dateien schreiben
        written = []
        for f in files:
            fpath = ws / f["path"].lstrip("/")
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(f["content"], encoding="utf-8")
            written.append(f["path"])
            logger.info("git_commit [%s]: schreibe %s", agent_id, f["path"])

        # git add + commit
        _, err, rc = await GiteaClient._git(["add", "-A"], ws)
        if rc != 0:
            return {"error": f"git add fehlgeschlagen: {err[:200]}"}

        # Prüfen ob es was zu committen gibt
        stat_out, _, _ = await GiteaClient._git(["status", "--porcelain"], ws)
        if not stat_out.strip():
            return {"committed": False, "reason": "Keine Änderungen zu committen", "branch": branch}

        _, err, rc = await GiteaClient._git(["commit", "-m", message], ws)
        if rc != 0:
            return {"error": f"git commit fehlgeschlagen: {err[:200]}"}

        # letzte Commit-Hash
        hash_out, _, _ = await GiteaClient._git(["rev-parse", "--short", "HEAD"], ws)

        return {
            "committed": True,
            "project_id": pid,
            "repo":      target["repo"],
            "owner":     target["owner"],
            "full_name": target["full_name"],
            "branch":    branch,
            "files":     written,
            "commit":    hash_out.strip(),
            "message":   message,
        }


class GitPushTool(BaseTool):
    """Pusht den aktuellen Branch auf Gitea."""

    @property
    def id(self) -> str:   return "git_push"
    @property
    def name(self) -> str: return "Git Push"
    @property
    def description(self) -> str:
        return (
            "Pusht den aktuellen Branch auf Gitea. "
            "Normalerweise nach git_commit aufrufen. "
            "create_pr=True erstellt automatisch einen Pull Request nach main."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.push"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "create_pr": {
                    "type":        "boolean",
                    "description": "True um automatisch einen PR nach main zu erstellen",
                },
                "pr_title": {
                    "type":        "string",
                    "description": "Titel des PRs (nur wenn create_pr=True)",
                },
                "pr_body": {
                    "type":        "string",
                    "description": "Beschreibung des PRs (optional)",
                },
                "project_id": {"type": "string", "description": "Projekt-ID (z.B. 'testprojekt')"},
                "repo": {"type": "string", "description": "Optionales Ziel-Repo als URL, owner/repo oder Repo-Name"},
            },
            "required": [],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        create_pr: bool = False, pr_title: str = "", pr_body: str = "", **kwargs,
    ) -> dict:
        pid = kwargs.get("project_id") or project_id
        repo_ref = kwargs.get("repo", "")
        from .gitea import GiteaClient, get_gitea_client, _load_config, resolve_git_target
        cfg = _load_config()

        try:
            target = await resolve_git_target(get_gitea_client(), project_id=pid, repo=repo_ref)
            ws = await GiteaClient.git_workspace(
                target["workspace_key"],
                owner=target["owner"],
                repo=target["repo"],
            )
        except Exception as e:
            return {"error": str(e)}

        # Remote-URL sauber setzen (kein Token in URL — Auth via GIT_ASKPASS)
        remote_url = f"{cfg['url']}/{target['owner']}/{target['repo']}.git"
        await GiteaClient._git(["remote", "set-url", "origin", remote_url], ws)

        # Branch ermitteln
        branch_out, _, _ = await GiteaClient._git(["branch", "--show-current"], ws)
        branch = branch_out.strip()
        if not branch:
            return {"error": "Kein aktiver Branch"}

        # Push mit Token via GIT_ASKPASS (Token landet nicht in URL oder History)
        _, err, rc = await GiteaClient._git(
            ["push", "-u", "origin", branch], ws, token=cfg.get("token", "")
        )
        if rc != 0:
            return {"error": f"git push fehlgeschlagen: {err[:300]}", "branch": branch}

        result: dict = {
            "pushed": True,
            "branch": branch,
            "project_id": pid,
            "repo": target["repo"],
            "owner": target["owner"],
            "full_name": target["full_name"],
        }

        if create_pr and branch != "main":
            title = pr_title or f"Agent-Änderung: {branch}"
            try:
                client = get_gitea_client()
                pr = await client.create_pr_for_repo(target["owner"], target["repo"], title, branch, body=pr_body)
                result["pr"] = {
                    "number": pr.get("number"),
                    "url":    pr.get("html_url"),
                    "title":  pr.get("title"),
                }
            except Exception as e:
                result["pr_error"] = str(e)

        return result


class GitCreatePRTool(BaseTool):
    """Erstellt einen Pull Request auf Gitea."""

    @property
    def id(self) -> str:   return "git_create_pr"
    @property
    def name(self) -> str: return "Pull Request erstellen"
    @property
    def description(self) -> str:
        return (
            "Erstellt einen Pull Request von einem Feature-Branch nach main. "
            "Nutze dies nach git_push wenn du Änderungen zur Review einreichen möchtest."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["git.pr"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type":        "string",
                    "description": "Titel des Pull Requests",
                },
                "head": {
                    "type":        "string",
                    "description": "Quell-Branch (z.B. feature/agent-xyz)",
                },
                "base": {
                    "type":        "string",
                    "description": "Ziel-Branch (Standard: main)",
                },
                "body": {
                    "type":        "string",
                    "description": "Beschreibung / Changelog des PRs",
                },
                "project_id": {"type": "string", "description": "Projekt-ID (z.B. 'testprojekt')"},
                "repo": {"type": "string", "description": "Optionales Ziel-Repo als URL, owner/repo oder Repo-Name"},
            },
            "required": ["title", "head"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        title: str, head: str, base: str = "main", body: str = "", **kwargs,
    ) -> dict:
        pid = kwargs.get("project_id") or project_id
        repo_ref = kwargs.get("repo", "")
        from .gitea import get_gitea_client, resolve_git_target
        try:
            client = get_gitea_client()
            target = await resolve_git_target(client, project_id=pid, repo=repo_ref)
            pr = await client.create_pr_for_repo(target["owner"], target["repo"], title, head, base, body)
            return {
                "created": True,
                "project_id": pid,
                "repo": target["repo"],
                "owner": target["owner"],
                "full_name": target["full_name"],
                "pr_number": pr.get("number"),
                "pr_url":    pr.get("html_url"),
                "title":     pr.get("title"),
            }
        except Exception as e:
            return {"error": str(e)}


# ============================================================= WKS Tools (Workstation-Zugriff via SSH)

def _get_wks_config(project_id: str) -> dict | None:
    """WKS-Config des Users laden, der zum project_id gehört.
    Persönliche Agenten heißen personal_<username> → username extrahieren."""
    import json as _j
    for uf in [Path("/etc/hydrahive/users.json"), Path("/etc/hydrahive/users.json")]:
        if uf.exists():
            USERS_FILE = uf
            break
    else:
        USERS_FILE = Path("/etc/hydrahive/users.json")
    if not project_id.startswith("personal_"):
        return None
    username = project_id[len("personal_"):]
    try:
        users = _j.loads(USERS_FILE.read_text())
        wks = users.get(username, {}).get("wks", {})
        if wks.get("ip"):
            return wks
    except Exception:
        pass
    return None


def _make_ssh_client(wks: dict):
    """Erstellt einen paramiko SSHClient mit Host-Key-Verifikation (RejectPolicy)."""
    import paramiko
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    connect_kw: dict = {
        "hostname": wks["ip"],
        "username": wks.get("ssh_user", ""),
        "timeout":  30,
    }
    key_path = wks.get("ssh_key_path", "")
    if key_path and Path(key_path).exists():
        connect_kw["key_filename"] = key_path
    client.connect(**connect_kw)
    return client


class WksShellExecTool(BaseTool):
    """Führt einen Shell-Befehl auf der Workstation des Users aus (via SSH)."""

    @property
    def id(self) -> str:   return "wks_shell_exec"
    @property
    def name(self) -> str: return "WKS Shell-Befehl"
    @property
    def description(self) -> str:
        return (
            "Führt einen Shell-Befehl auf der eigenen Workstation des Users aus (SSH). "
            "Nur für persönliche Agenten mit konfigurierter WKS verfügbar."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["workstation.shell"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Der auszuführende Shell-Befehl"},
                "cwd":     {"type": "string", "description": "Arbeitsverzeichnis (optional)"},
            },
            "required": ["command"],
        }

    async def execute(self, agent_id: str, project_id: str, command: str, cwd: str = "", **kwargs) -> dict:
        import asyncio, shlex
        wks = _get_wks_config(project_id)
        if not wks:
            return {"error": "Keine WKS-Konfiguration für diesen Agenten — bitte in Mein Agent → WKS einrichten"}

        blocked = _check_shell_blocklist(command)
        if blocked:
            logger.warning("wks_shell_exec BLOCKED [%s]: %s — %s", agent_id, command[:120], blocked)
            return {
                "error": f"Befehl blockiert: {blocked}",
                "command": command,
                "exit_code": -1,
                "blocked": True,
            }

        def _run():
            client = _make_ssh_client(wks)
            full_cmd = f"cd {shlex.quote(cwd)} && {command}" if cwd else command
            _, stdout, stderr = client.exec_command(full_cmd, timeout=60)
            out      = stdout.read().decode("utf-8", errors="replace")
            err      = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()
            client.close()
            return {"stdout": out, "stderr": err, "exit_code": exit_code}

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _run)
        except Exception as e:
            return {"error": str(e)}


class WksFileReadTool(BaseTool):
    """Liest eine Datei von der Workstation des Users (via SFTP)."""

    @property
    def id(self) -> str:   return "wks_file_read"
    @property
    def name(self) -> str: return "WKS Datei lesen"
    @property
    def description(self) -> str:
        return "Liest eine Datei von der eigenen Workstation des Users via SFTP."

    @property
    def permissions_required(self) -> list[str]:
        return ["workstation.read"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absoluter Pfad zur Datei auf der WKS"},
            },
            "required": ["path"],
        }

    async def execute(self, agent_id: str, project_id: str, path: str, **kwargs) -> dict:
        import asyncio, io
        wks = _get_wks_config(project_id)
        if not wks:
            return {"error": "Keine WKS-Konfiguration für diesen Agenten"}

        def _run():
            client = _make_ssh_client(wks)
            sftp = client.open_sftp()
            buf = io.BytesIO()
            sftp.getfo(path, buf)
            content = buf.getvalue().decode("utf-8", errors="replace")
            sftp.close(); client.close()
            return {"content": content, "path": path}

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _run)
        except Exception as e:
            return {"error": str(e)}


class WksFileWriteTool(BaseTool):
    """Schreibt eine Datei auf die Workstation des Users (via SFTP)."""

    @property
    def id(self) -> str:   return "wks_file_write"
    @property
    def name(self) -> str: return "WKS Datei schreiben"
    @property
    def description(self) -> str:
        return "Schreibt/überschreibt eine Datei auf der eigenen Workstation des Users via SFTP."

    @property
    def permissions_required(self) -> list[str]:
        return ["workstation.write"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Absoluter Pfad zur Datei auf der WKS"},
                "content": {"type": "string", "description": "Dateiinhalt"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, agent_id: str, project_id: str, path: str, content: str, **kwargs) -> dict:
        import asyncio, io
        wks = _get_wks_config(project_id)
        if not wks:
            return {"error": "Keine WKS-Konfiguration für diesen Agenten"}

        def _run():
            client = _make_ssh_client(wks)
            sftp = client.open_sftp()
            buf = io.BytesIO(content.encode("utf-8"))
            sftp.putfo(buf, path)
            sftp.close(); client.close()
            return {"written": True, "path": path, "bytes": len(content.encode())}

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _run)
        except Exception as e:
            return {"error": str(e)}


# ============================================================= Discord-Tools

# _discord_clients: {agent_id: AgentDiscordClient} — wird von main.py befüllt
_discord_clients: dict[str, object] = {}


def _get_discord_client(agent_id: str) -> object | None:
    """Discord-Client für agent_id — fällt auf ersten verfügbaren Client zurück.
    Ermöglicht Spezialisten-Agenten den Discord-Zugriff ohne eigenen Bot-Token."""
    return _discord_clients.get(agent_id) or (next(iter(_discord_clients.values()), None))


class SendMailTool(BaseTool):
    """E-Mail über den konfigurierten Agenten-Mailaccount senden."""

    @property
    def id(self) -> str:          return "send_mail"
    @property
    def name(self) -> str:        return "Send Mail"
    @property
    def description(self) -> str: return "Sendet eine E-Mail vom konfigurierten Agenten-Mailaccount."
    @property
    def permissions_required(self) -> list[str]: return ["mail"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to":      {"type": "string",  "description": "Empfänger-Adresse"},
                "subject": {"type": "string",  "description": "Betreff"},
                "body":    {"type": "string",  "description": "Nachrichtentext (plain text)"},
                "cc":      {"type": "string",  "description": "CC-Adresse (optional)"},
            },
            "required": ["to", "subject", "body"],
        }

    async def execute(self, agent_id: str, project_id: str,
                      to: str, subject: str, body: str, cc: str = "", **kwargs) -> dict:
        import json, smtplib
        from email.mime.text import MIMEText
        from pathlib import Path
        from .main import AGENTS_DIR

        cfg_path = Path(AGENTS_DIR) / agent_id / "mail.json"
        if not cfg_path.exists():
            return {"error": "Kein Mailaccount konfiguriert für diesen Agenten"}
        try:
            cfg = json.loads(cfg_path.read_text())
        except Exception:
            return {"error": "Mail-Config konnte nicht gelesen werden"}

        msg = MIMEText(body, "plain", "utf-8")
        msg["From"]    = cfg["mail_address"]
        msg["To"]      = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc

        try:
            with smtplib.SMTP(cfg["smtp_host"], cfg.get("smtp_port", 587), timeout=15) as s:
                s.starttls()
                s.login(cfg["smtp_user"], cfg["smtp_password"])
                recipients = [to] + ([cc] if cc else [])
                s.sendmail(cfg["mail_address"], recipients, msg.as_string())
            return {"sent": True, "from": cfg["mail_address"], "to": to}
        except Exception as e:
            return {"error": str(e)}


class ReceiveMailTool(BaseTool):
    """E-Mails vom konfigurierten Agenten-Mailaccount abrufen (IMAP)."""

    @property
    def id(self) -> str:          return "receive_mail"
    @property
    def name(self) -> str:        return "Receive Mail"
    @property
    def description(self) -> str: return "Ruft E-Mails vom konfigurierten Agenten-Mailaccount ab (IMAP). Gibt Betreff, Absender, Datum und Text zurück."
    @property
    def permissions_required(self) -> list[str]: return ["mail"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "folder":  {"type": "string",  "description": "IMAP-Ordner (default: INBOX)"},
                "limit":   {"type": "integer", "description": "Maximale Anzahl Mails (default: 10, max: 50)"},
                "unread_only": {"type": "boolean", "description": "Nur ungelesene Mails (default: false)"},
            },
            "required": [],
        }

    async def execute(self, agent_id: str, project_id: str,
                      folder: str = "INBOX", limit: int = 10, unread_only: bool = False, **kwargs) -> dict:
        import json, imaplib, email
        from email.header import decode_header
        from pathlib import Path
        from .main import AGENTS_DIR

        cfg_path = Path(AGENTS_DIR) / agent_id / "mail.json"
        if not cfg_path.exists():
            return {"error": "Kein Mailaccount konfiguriert für diesen Agenten"}
        try:
            cfg = json.loads(cfg_path.read_text())
        except Exception:
            return {"error": "Mail-Config konnte nicht gelesen werden"}

        imap_host = cfg.get("imap_host", cfg.get("smtp_host", "").replace("smtp.", "imap."))
        imap_port = cfg.get("imap_port", 993)
        limit = min(int(limit), 50)

        def _decode_header(value: str) -> str:
            parts = decode_header(value)
            result = []
            for part, enc in parts:
                if isinstance(part, bytes):
                    result.append(part.decode(enc or "utf-8", errors="replace"))
                else:
                    result.append(part)
            return "".join(result)

        def _get_body(msg) -> str:
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                        payload = part.get_payload(decode=True)
                        if payload:
                            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            return ""

        try:
            with imaplib.IMAP4_SSL(imap_host, imap_port) as imap:
                imap.login(cfg["smtp_user"], cfg["smtp_password"])
                imap.select(folder)
                search = "UNSEEN" if unread_only else "ALL"
                _, data = imap.search(None, search)
                ids = data[0].split()
                ids = ids[-limit:]  # neueste zuerst
                mails = []
                for mid in reversed(ids):
                    _, raw = imap.fetch(mid, "(RFC822)")
                    msg = email.message_from_bytes(raw[0][1])
                    mails.append({
                        "id":      mid.decode(),
                        "from":    _decode_header(msg.get("From", "")),
                        "subject": _decode_header(msg.get("Subject", "")),
                        "date":    msg.get("Date", ""),
                        "body":    _get_body(msg)[:2000],
                    })
            return {"mails": mails, "count": len(mails), "folder": folder}
        except Exception as e:
            return {"error": str(e)}


class DiscordSendTool(BaseTool):
    """Nachricht in einen Discord-Channel senden."""

    @property
    def id(self) -> str:          return "discord_send"
    @property
    def name(self) -> str:        return "Discord Send"
    @property
    def description(self) -> str: return "Sendet eine Textnachricht in einen Discord-Channel."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Discord Channel-ID"},
                "text":       {"type": "string", "description": "Nachrichtentext"},
            },
            "required": ["channel_id", "text"],
        }

    async def execute(self, agent_id: str, project_id: str,
                      channel_id: str, text: str, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client:
            return {"error": "Discord nicht konfiguriert für diesen Agenten"}
        try:
            await client.send_message(channel_id, text)
            return {"sent": True, "channel_id": channel_id}
        except Exception as e:
            return {"error": str(e)}


class DiscordReadTool(BaseTool):
    """Letzte Nachrichten aus einem Discord-Channel lesen."""

    @property
    def id(self) -> str:          return "discord_read"
    @property
    def name(self) -> str:        return "Discord Read"
    @property
    def description(self) -> str: return "Liest die letzten Nachrichten aus einem Discord-Channel."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Discord Channel-ID"},
                "limit":      {"type": "integer", "description": "Anzahl Nachrichten (max 50)", "default": 20},
            },
            "required": ["channel_id"],
        }

    async def execute(self, agent_id: str, project_id: str,
                      channel_id: str, limit: int = 20, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client:
            return {"error": "Discord nicht konfiguriert für diesen Agenten"}
        try:
            messages = await client.read_messages(channel_id, limit=min(limit, 50))
            return {"messages": messages}
        except Exception as e:
            return {"error": str(e)}


class DiscordListChannelsTool(BaseTool):
    """Alle Text-Channels der konfigurierten Discord-Guild auflisten."""

    @property
    def id(self) -> str:          return "discord_list_channels"
    @property
    def name(self) -> str:        return "Discord List Channels"
    @property
    def description(self) -> str: return "Listet alle Text-Channels der konfigurierten Discord-Guild auf."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, agent_id: str, project_id: str, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client:
            return {"error": "Discord nicht konfiguriert für diesen Agenten"}
        try:
            channels = await client.list_channels()
            return {"channels": channels}
        except Exception as e:
            return {"error": str(e)}


class DiscordListAllChannelsTool(BaseTool):
    """Alle Discord-Channels inkl. Kategorien und Voice auflisten."""
    @property
    def id(self) -> str:          return "discord_list_all_channels"
    @property
    def name(self) -> str:        return "Discord List All Channels"
    @property
    def description(self) -> str: return "Listet alle Channels der Guild auf inkl. Kategorien, Voice-Channels und deren Positionen."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]
    @property
    def parameters(self) -> dict: return {"type": "object", "properties": {}, "required": []}
    async def execute(self, agent_id: str, project_id: str, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client: return {"error": "Discord nicht konfiguriert"}
        try: return {"channels": await client.list_all_channels()}
        except Exception as e: return {"error": str(e)}


class DiscordCreateCategoryTool(BaseTool):
    """Neue Kategorie in der Discord-Guild erstellen."""
    @property
    def id(self) -> str:          return "discord_create_category"
    @property
    def name(self) -> str:        return "Discord Create Category"
    @property
    def description(self) -> str: return "Erstellt eine neue Kategorie im Discord-Server."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]
    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"name": {"type": "string", "description": "Name der Kategorie"}}, "required": ["name"]}
    async def execute(self, agent_id: str, project_id: str, name: str, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client: return {"error": "Discord nicht konfiguriert"}
        try: return await client.create_category(name)
        except Exception as e: return {"error": str(e)}


class DiscordCreateChannelTool(BaseTool):
    """Neuen Text-Channel im Discord-Server erstellen."""
    @property
    def id(self) -> str:          return "discord_create_channel"
    @property
    def name(self) -> str:        return "Discord Create Channel"
    @property
    def description(self) -> str: return "Erstellt einen neuen Text-Channel, optional in einer Kategorie."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name":        {"type": "string",  "description": "Channel-Name (lowercase, keine Leerzeichen)"},
                "category_id": {"type": "string",  "description": "ID der Eltern-Kategorie (optional)"},
                "topic":       {"type": "string",  "description": "Channel-Beschreibung (optional)"},
            },
            "required": ["name"],
        }
    async def execute(self, agent_id: str, project_id: str, name: str, category_id: str = "", topic: str = "", **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client: return {"error": "Discord nicht konfiguriert"}
        try: return await client.create_channel(name, category_id=category_id, topic=topic)
        except Exception as e: return {"error": str(e)}


class DiscordDeleteChannelTool(BaseTool):
    """Channel oder Kategorie aus der Discord-Guild löschen."""
    @property
    def id(self) -> str:          return "discord_delete_channel"
    @property
    def name(self) -> str:        return "Discord Delete Channel"
    @property
    def description(self) -> str: return "Löscht einen Channel oder eine Kategorie aus dem Discord-Server."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]
    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"channel_id": {"type": "string", "description": "ID des zu löschenden Channels/der Kategorie"}}, "required": ["channel_id"]}
    async def execute(self, agent_id: str, project_id: str, channel_id: str, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client: return {"error": "Discord nicht konfiguriert"}
        try: return await client.delete_channel(channel_id)
        except Exception as e: return {"error": str(e)}


class DiscordSetTopicTool(BaseTool):
    """Channel-Topic/Beschreibung setzen."""
    @property
    def id(self) -> str:          return "discord_set_topic"
    @property
    def name(self) -> str:        return "Discord Set Topic"
    @property
    def description(self) -> str: return "Setzt das Topic (Beschreibung) eines Discord-Channels."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]
    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"channel_id": {"type": "string"}, "topic": {"type": "string", "description": "Neues Channel-Topic"}}, "required": ["channel_id", "topic"]}
    async def execute(self, agent_id: str, project_id: str, channel_id: str, topic: str, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client: return {"error": "Discord nicht konfiguriert"}
        try: return await client.set_channel_topic(channel_id, topic)
        except Exception as e: return {"error": str(e)}


class DiscordRenameChannelTool(BaseTool):
    """Channel umbenennen."""
    @property
    def id(self) -> str:          return "discord_rename_channel"
    @property
    def name(self) -> str:        return "Discord Rename Channel"
    @property
    def description(self) -> str: return "Benennt einen Discord-Channel um."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]
    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"channel_id": {"type": "string"}, "name": {"type": "string", "description": "Neuer Channel-Name"}}, "required": ["channel_id", "name"]}
    async def execute(self, agent_id: str, project_id: str, channel_id: str, name: str, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client: return {"error": "Discord nicht konfiguriert"}
        try: return await client.rename_channel(channel_id, name)
        except Exception as e: return {"error": str(e)}


class DiscordListMembersTool(BaseTool):
    """Mitglieder der Discord-Guild auflisten."""
    @property
    def id(self) -> str:          return "discord_list_members"
    @property
    def name(self) -> str:        return "Discord List Members"
    @property
    def description(self) -> str: return "Listet Mitglieder der Discord-Guild mit ihren Rollen auf."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]
    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"limit": {"type": "integer", "description": "Max. Anzahl Mitglieder", "default": 100}}, "required": []}
    async def execute(self, agent_id: str, project_id: str, limit: int = 100, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client: return {"error": "Discord nicht konfiguriert"}
        try: return {"members": await client.list_members(limit=min(limit, 200))}
        except Exception as e: return {"error": str(e)}


class DiscordListRolesTool(BaseTool):
    """Alle Rollen der Discord-Guild auflisten."""
    @property
    def id(self) -> str:          return "discord_list_roles"
    @property
    def name(self) -> str:        return "Discord List Roles"
    @property
    def description(self) -> str: return "Listet alle Rollen des Discord-Servers auf."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]
    @property
    def parameters(self) -> dict: return {"type": "object", "properties": {}, "required": []}
    async def execute(self, agent_id: str, project_id: str, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client: return {"error": "Discord nicht konfiguriert"}
        try: return {"roles": await client.list_roles()}
        except Exception as e: return {"error": str(e)}


class DiscordDeleteMessageTool(BaseTool):
    """Nachricht in einem Discord-Channel löschen."""
    @property
    def id(self) -> str:          return "discord_delete_message"
    @property
    def name(self) -> str:        return "Discord Delete Message"
    @property
    def description(self) -> str: return "Löscht eine bestimmte Nachricht aus einem Discord-Channel."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]
    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"channel_id": {"type": "string"}, "message_id": {"type": "string", "description": "ID der zu löschenden Nachricht"}}, "required": ["channel_id", "message_id"]}
    async def execute(self, agent_id: str, project_id: str, channel_id: str, message_id: str, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client: return {"error": "Discord nicht konfiguriert"}
        try: return await client.delete_message(channel_id, message_id)
        except Exception as e: return {"error": str(e)}


class DiscordPinMessageTool(BaseTool):
    """Nachricht in einem Discord-Channel anpinnen."""
    @property
    def id(self) -> str:          return "discord_pin_message"
    @property
    def name(self) -> str:        return "Discord Pin Message"
    @property
    def description(self) -> str: return "Pinnt eine Nachricht in einem Discord-Channel an."
    @property
    def permissions_required(self) -> list[str]: return ["discord"]
    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"channel_id": {"type": "string"}, "message_id": {"type": "string"}}, "required": ["channel_id", "message_id"]}
    async def execute(self, agent_id: str, project_id: str, channel_id: str, message_id: str, **kwargs) -> dict:
        client = _get_discord_client(agent_id)
        if not client: return {"error": "Discord nicht konfiguriert"}
        try: return await client.pin_message(channel_id, message_id)
        except Exception as e: return {"error": str(e)}


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
registry.register(CreateSkillTool())
registry.register(ListSkillsTool())
registry.register(DeleteSkillTool())
registry.register(AskAgentTool())
registry.register(DelegateAgentTool())
registry.register(GitStatusTool())
registry.register(GiteaRepoInspectTool())
registry.register(GiteaRepoTreeTool())
registry.register(GiteaRepoFileTool())
registry.register(GiteaRepoCommitsTool())
registry.register(GiteaRepoDiffTool())
registry.register(GiteaCreateIssueTool())
registry.register(GiteaCommentIssueTool())
registry.register(GiteaUpdateIssueTool())
registry.register(GitDiffTool())
registry.register(GitCommitTool())
registry.register(GitPushTool())
registry.register(GitCreatePRTool())
registry.register(WksShellExecTool())
registry.register(WksFileReadTool())
registry.register(WksFileWriteTool())
registry.register(SendMailTool())
registry.register(ReceiveMailTool())
registry.register(DiscordSendTool())
registry.register(DiscordReadTool())
registry.register(DiscordListChannelsTool())
registry.register(DiscordListAllChannelsTool())
registry.register(DiscordCreateCategoryTool())
registry.register(DiscordCreateChannelTool())
registry.register(DiscordDeleteChannelTool())
registry.register(DiscordSetTopicTool())
registry.register(DiscordRenameChannelTool())
registry.register(DiscordListMembersTool())
registry.register(DiscordListRolesTool())
registry.register(DiscordDeleteMessageTool())
registry.register(DiscordPinMessageTool())


# ============================================================= Admin Tools (Danger)

def _verify_admin_permission(calling_agent_id: str) -> None:
    """
    Sicherheits-Gate für Admin-Tools.
    Personal-Agenten (personal_{username}): prüft ob Owner Admin ist.
    Andere Agenten: vertraut dem permissions_required-Filter in tools_for_agent().
    """
    if _load_users_fn is None:
        raise PermissionError("Admin-Tools nicht konfiguriert (kein User-Store gesetzt)")
    if calling_agent_id.startswith("personal_"):
        username = calling_agent_id[len("personal_"):]
        users = _load_users_fn()
        if users.get(username, {}).get("role") != "admin":
            raise PermissionError(f"User '{username}' hat keine Admin-Berechtigung für dieses Tool")


class CreateAgentTool(BaseTool):
    """Legt einen neuen Agenten an (Verzeichnis, agent.yaml, soul.md)."""

    @property
    def id(self) -> str:
        return "create_agent"

    @property
    def name(self) -> str:
        return "Create Agent"

    @property
    def description(self) -> str:
        return (
            "Legt einen neuen Agenten an. "
            "Erstellt Verzeichnis, agent.yaml und soul.md. "
            "Agent-ID: nur Kleinbuchstaben, Ziffern, _ und - erlaubt."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["admin.manage"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Eindeutige Agent-ID (a-z, 0-9, _, -)",
                },
                "type": {
                    "type": "string",
                    "enum": ["boss", "specialist", "worker"],
                    "description": "Agent-Typ",
                },
                "identity": {
                    "type": "string",
                    "description": "Anzeigename / Persönlichkeit des Agenten",
                },
                "model": {
                    "type": "string",
                    "description": "LLM-Modell, z.B. claude-haiku-4-5-20251001",
                },
                "soul": {
                    "type": "string",
                    "description": "System-Prompt / Charakter (Markdown), optional",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Liste der Tool-IDs für diesen Agenten",
                },
            },
            "required": ["id", "type", "identity", "model"],
        }

    async def execute(self, agent_id: str, project_id: str, **kwargs) -> Any:
        import re as _re
        import yaml as _yaml

        _verify_admin_permission(agent_id)

        new_id = kwargs.get("id", "").strip()
        agent_type = kwargs.get("type", "worker")
        identity = kwargs.get("identity", new_id)
        model = kwargs.get("model", "claude-haiku-4-5-20251001")
        soul_text = kwargs.get("soul", "")
        tools = list(kwargs.get("tools") or [])

        if not _re.match(r"^[a-z0-9_-]+$", new_id):
            return {"error": "Agent-ID darf nur a-z, 0-9, _ und - enthalten"}
        if agent_type not in {"boss", "specialist", "worker"}:
            return {"error": f"Ungültiger Typ: {agent_type}"}
        if _discovery and _discovery.get(new_id):
            return {"error": f"Agent '{new_id}' existiert bereits"}

        agent_dir = Path(_admin_agents_dir) / new_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "skills").mkdir(exist_ok=True)
        (agent_dir / "memory").mkdir(exist_ok=True)

        agent_data = {
            "id": new_id,
            "type": agent_type,
            "identity": identity,
            "llm": {"model": model, "temperature": 0.7, "max_tokens": 4096, "fallback_models": []},
            "tools": tools,
            "allowed_agents": [],
            "mcp_servers": [],
            "heartbeat": {"interval": "30s", "timeout": "90s", "on_failure": "restart"},
        }
        if soul_text:
            agent_data["soul"] = "./soul.md"

        (agent_dir / "agent.yaml").write_text(
            _yaml.dump(agent_data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        (agent_dir / "soul.md").write_text(
            soul_text or f"# {identity}\n\nDu bist {identity}, ein KI-Agent.\n",
            encoding="utf-8",
        )

        if _audit_log_fn:
            _audit_log_fn("agent.create", target=new_id, details={"type": agent_type, "model": model, "by_agent": agent_id})

        if _discovery:
            try:
                import asyncio as _asyncio
                await _asyncio.sleep(0.2)
                from .agent_config import load_agent_config as _lac
                cfg = _lac(agent_dir)
                if cfg:
                    with _discovery._lock:
                        _discovery._agents[cfg.id] = cfg
            except Exception as _e:
                logger.warning("Discovery-Update nach create_agent fehlgeschlagen: %s", _e)

        logger.info("create_agent tool: %s (%s) angelegt von %s", new_id, agent_type, agent_id)
        return {"created": True, "agent_id": new_id, "agent_dir": str(agent_dir)}


class DeleteAgentTool(BaseTool):
    """Deaktiviert einen Agenten (umbenennen in _{id}_disabled)."""

    @property
    def id(self) -> str:
        return "delete_agent"

    @property
    def name(self) -> str:
        return "Delete Agent"

    @property
    def description(self) -> str:
        return (
            "Deaktiviert einen Agenten durch Umbenennen des Verzeichnisses. "
            "Kein Datenverlust — Verzeichnis bleibt als _{id}_disabled erhalten."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["admin.manage"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID des zu deaktivierenden Agenten",
                },
            },
            "required": ["agent_id"],
        }

    async def execute(self, agent_id: str, project_id: str, **kwargs) -> Any:
        _verify_admin_permission(agent_id)

        target_id = kwargs.get("agent_id", "").strip()
        if not target_id:
            return {"error": "agent_id fehlt"}
        if target_id == agent_id:
            return {"error": "Du kannst dich nicht selbst deaktivieren"}

        agent_dir = Path(_admin_agents_dir) / target_id
        if not agent_dir.exists():
            return {"error": f"Agent '{target_id}' nicht gefunden"}

        disabled_dir = Path(_admin_agents_dir) / f"_{target_id}_disabled"
        agent_dir.rename(disabled_dir)

        if _audit_log_fn:
            _audit_log_fn("agent.delete", target=target_id, details={"by_agent": agent_id})

        logger.info("delete_agent tool: %s deaktiviert von %s", target_id, agent_id)
        return {"disabled": True, "agent_id": target_id, "moved_to": str(disabled_dir)}


class CreateProjectTool(BaseTool):
    """Legt ein neues Projekt an inkl. Provisioning (Linux-User, Samba, Matrix)."""

    @property
    def id(self) -> str:
        return "create_project"

    @property
    def name(self) -> str:
        return "Create Project"

    @property
    def description(self) -> str:
        return (
            "Legt ein neues Projekt an und führt das vollständige Provisioning durch: "
            "Linux-User, Verzeichnisse, Samba-Share, Matrix-Room, Gitea-Repo. "
            "Projekt-ID: nur Kleinbuchstaben, Ziffern, _ und - erlaubt."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["admin.manage"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Eindeutige Projekt-ID (a-z, 0-9, _, -)",
                },
                "name": {
                    "type": "string",
                    "description": "Anzeigename des Projekts",
                },
                "description": {
                    "type": "string",
                    "description": "Kurze Projektbeschreibung",
                },
                "boss": {
                    "type": "string",
                    "description": "ID des Boss-Agenten für dieses Projekt",
                },
                "workers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Liste weiterer Agenten-IDs (optional)",
                },
                "samba": {
                    "type": "boolean",
                    "description": "Samba-Share anlegen (Standard: true)",
                },
            },
            "required": ["id", "name", "boss"],
        }

    async def execute(self, agent_id: str, project_id: str, **kwargs) -> Any:
        import re as _re
        import yaml as _yaml

        _verify_admin_permission(agent_id)

        new_id = kwargs.get("id", "").strip()
        name = kwargs.get("name", new_id)
        description = kwargs.get("description", "")
        boss = kwargs.get("boss", "").strip()
        workers = list(kwargs.get("workers") or [])
        samba = kwargs.get("samba", True)

        if not _re.match(r"^[a-z0-9_-]+$", new_id):
            return {"error": "Projekt-ID darf nur a-z, 0-9, _ und - enthalten"}
        if not boss:
            return {"error": "boss-Agent fehlt"}
        if _projects_registry and _projects_registry.get(new_id):
            return {"error": f"Projekt '{new_id}' existiert bereits"}
        if _discovery and not _discovery.get(boss):
            return {"error": f"Boss-Agent '{boss}' nicht in Discovery gefunden"}

        project_dir = Path(_admin_projects_dir) / new_id
        project_dir.mkdir(parents=True, exist_ok=True)

        project_data = {
            "id": new_id,
            "version": "1.0.0",
            "identity": {"name": name, "description": description},
            "agents": {"boss": boss, "workers": workers},
            "matrix": {"room": ""},
            "filesystem": {"path": f"/projects/{new_id}", "samba": samba, "nfs": False},
            "system": {"user": f"proj_{new_id}", "group": f"proj_{new_id}"},
            "chat": {"show_swarm": False},
        }
        (project_dir / "project.yaml").write_text(
            _yaml.dump(project_data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )

        if _audit_log_fn:
            _audit_log_fn("project.create", target=new_id, project_id=new_id, details={"boss": boss, "by_agent": agent_id})

        import asyncio as _asyncio
        await _asyncio.sleep(0.3)

        cfg = None
        if _projects_registry:
            cfg = _projects_registry.get(new_id) or _projects_registry.register(project_dir)
        if cfg is None:
            return {"error": "Projekt konnte nach Anlage nicht geladen werden", "project_id": new_id}

        provisioner = _get_provisioner() if _get_provisioner else None
        if provisioner is None:
            logger.warning("create_project tool: Provisioner nicht verfügbar für %s", new_id)
            return {"warning": "Provisioner nicht verfügbar — Projekt angelegt aber nicht provisioniert", "project_id": new_id}

        result = await provisioner.provision(cfg)
        if _audit_log_fn:
            _audit_log_fn("project.provision", target=new_id, project_id=new_id)

        if result.matrix_room and not cfg.matrix.room:
            try:
                from .router_project_lifecycle import update_project_matrix_room as _upmr
                _upmr(_admin_projects_dir, new_id, result.matrix_room, logger=logger)
            except Exception as _e:
                logger.warning("Matrix-Room konnte nicht gespeichert werden: %s", _e)

        gitea_repo_url = ""
        gitea_error = ""
        try:
            from .gitea import get_gitea_client
            gitea = get_gitea_client()
            repo = await gitea.create_repo(new_id, description=description or "")
            gitea_repo_url = repo.get("html_url", "")
            webhook_url = f"http://127.0.0.1:8765/webhooks/gitea/{new_id}"
            await gitea.create_webhook(new_id, webhook_url)
        except Exception as _e:
            gitea_error = str(_e)
            logger.warning("Gitea-Repo für '%s' fehlgeschlagen: %s", new_id, _e)

        logger.info("create_project tool: %s angelegt von %s", new_id, agent_id)
        return {
            "created": True,
            "project_id": new_id,
            "linux_user": result.linux_user,
            "files_dir": result.files_dir,
            "samba_share": result.samba_share,
            "matrix_room": result.matrix_room,
            "warnings": result.warnings,
            "ok": result.ok,
            "gitea_repo": gitea_repo_url,
            "gitea_error": gitea_error,
        }


class DeleteProjectTool(BaseTool):
    """Löscht ein Projekt (stoppt Agenten, entfernt Samba, verschiebt Verzeichnis)."""

    @property
    def id(self) -> str:
        return "delete_project"

    @property
    def name(self) -> str:
        return "Delete Project"

    @property
    def description(self) -> str:
        return (
            "Löscht ein Projekt: stoppt laufende Agenten, entfernt Samba-Share, "
            "verschiebt das Projektverzeichnis (kein Datenverlust, nur umbenannt). "
            "Muss mit confirm=true aufgerufen werden."
        )

    @property
    def permissions_required(self) -> list[str]:
        return ["admin.manage"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "ID des zu löschenden Projekts",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Muss true sein um den Löschvorgang zu bestätigen",
                },
            },
            "required": ["project_id", "confirm"],
        }

    async def execute(self, agent_id: str, project_id: str, **kwargs) -> Any:
        import time as _time

        _verify_admin_permission(agent_id)

        target_id = kwargs.get("project_id", "").strip()
        confirm = kwargs.get("confirm", False)

        if not target_id:
            return {"error": "project_id fehlt"}
        if not confirm:
            return {"error": "confirm muss true sein um das Projekt zu löschen"}

        cfg = _projects_registry.get(target_id) if _projects_registry else None
        if not cfg:
            return {"error": f"Projekt '{target_id}' nicht gefunden"}

        proj_dir = Path(_admin_projects_dir) / target_id
        if not proj_dir.exists():
            return {"error": "Projektverzeichnis nicht gefunden"}

        stopped_agents = []
        if _admin_runtime and cfg:
            boss_id = cfg.agents.boss
            handle = _admin_runtime.get_handle(boss_id)
            if handle:
                await _admin_runtime.stop_agent(boss_id)
                stopped_agents.append(boss_id)

        smb_conf = Path("/etc/samba/smb.conf")
        if smb_conf.exists():
            try:
                import re as _re
                import subprocess as _sub
                smb_content = smb_conf.read_text(encoding="utf-8")
                smb_content = _re.sub(rf"\[{_re.escape(target_id)}\][^\[]*", "", smb_content, flags=_re.DOTALL)
                smb_conf.write_text(smb_content, encoding="utf-8")
                _sub.run(["systemctl", "reload", "smbd"], check=False, timeout=5)
            except Exception as _e:
                logger.warning("Samba-Share Entfernung fehlgeschlagen: %s", _e)

        timestamp = int(_time.time())
        deleted_dir = Path(_admin_projects_dir) / f"_deleted_{target_id}_{timestamp}"
        proj_dir.rename(deleted_dir)

        if _audit_log_fn:
            _audit_log_fn("project.delete", target=target_id, project_id=target_id, details={"by_agent": agent_id})

        logger.info("delete_project tool: %s gelöscht von %s", target_id, agent_id)
        return {
            "deleted": True,
            "project_id": target_id,
            "moved_to": str(deleted_dir),
            "stopped_agents": stopped_agents,
        }


# ============================================================= Meta-Tools

class RequestToolsTool(BaseTool):
    """Meta-Tool: Lädt Tool-Kategorien on-demand nach."""

    @property
    def id(self) -> str:
        return "request_tools"

    @property
    def name(self) -> str:
        return "Request Tools"

    @property
    def description(self) -> str:
        from .tool_loader import TOOL_CATEGORIES
        cats = ", ".join(sorted(TOOL_CATEGORIES.keys()))
        return (
            "Request additional tools by category when you need capabilities beyond memory/coordination. "
            "Call this BEFORE using any tool in the requested category. "
            f"Available categories: {cats}. "
            "Example: request_tools(categories=['discord']) before using discord_send."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tool categories to load, e.g. ['discord', 'git']",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief reason why these tools are needed",
                },
            },
            "required": ["categories"],
        }

    async def execute(self, agent_id: str, project_id: str, **kwargs) -> Any:
        # Actual loading is handled by the orchestrator — this is just a stub
        categories = kwargs.get("categories", [])
        return {"ok": True, "categories": categories}


registry.register(CreateAgentTool())
registry.register(DeleteAgentTool())
registry.register(CreateProjectTool())
registry.register(DeleteProjectTool())
registry.register(RequestToolsTool())
