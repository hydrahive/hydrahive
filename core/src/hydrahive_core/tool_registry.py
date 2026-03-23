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
        path: str, content: str, mode: str = "overwrite",
    ) -> dict:
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
    # Service-Sabotage (OctopOS selbst killen)
    (r"\bsystemctl\s+(stop|disable|mask|kill)\s+octopos",  "systemctl stop/disable octopos verboten"),
    (r"\bkillall\s+uvicorn\b",      "killall uvicorn verboten"),
    (r"\bkill\b.*\buvicorn\b",      "kill uvicorn verboten"),
    # Fork-Bombe / Wildcard-Gefahr
    (r":\(\)\s*\{",                 "Fork-Bombe verboten"),
    (r"\brm\s+(-[a-zA-Z]+ +)?/\s", "rm / verboten"),
    (r"\brm\s+(-[a-zA-Z]+ +)?/$",  "rm / verboten"),
    # Schreiben in geschützte Systempfade via Redirects
    (r">\s*/opt/octopos/(?!.*\bjournald?\b)",  "Redirect nach /opt/octopos/ verboten"),
    (r">\s*/etc/",                  "Redirect nach /etc/ verboten"),
    (r">\s*/bin/",                  "Redirect nach /bin/ verboten"),
    (r">\s*/usr/",                  "Redirect nach /usr/ verboten"),
    (r">\s*/lib",                   "Redirect nach /lib verboten"),
    (r">\s*/boot/",                 "Redirect nach /boot/ verboten"),
    (r">\s*/dev/",                  "Redirect nach /dev/ verboten"),
    (r">\s*/sys/",                  "Redirect nach /sys/ verboten"),
    (r">\s*/proc/",                 "Redirect nach /proc/ verboten"),
    # chmod/chown auf Systempfade
    (r"\b(chmod|chown)\b.*/opt/",   "chmod/chown auf /opt/ verboten"),
    (r"\b(chmod|chown)\b.*/etc/",   "chmod/chown auf /etc/ verboten"),
    (r"\b(chmod|chown)\b.*/bin/",   "chmod/chown auf /bin/ verboten"),
    # git clone/reset --hard auf /opt/
    (r"\bgit\b.*--hard\b.*\s/opt/", "git reset --hard auf /opt/ verboten"),
    (r"\bgit\s+clone\b.*\s/opt/",   "git clone nach /opt/ verboten"),
    (r"cd\s+/opt/octopos\b.*&&.*\bgit\b", "git in /opt/octopos/ verboten"),
    # Subshell-/Command-Substitution-Gefahr
    (r"\$\(",                     "Command Substitution $(...) verboten"),
    (r"`",                        "Backticks verboten"),
]

_SHELL_WRAPPERS = {"bash", "sh", "zsh", "fish", "dash", "ksh"}


def _check_shell_blocklist(command: str) -> str | None:
    """Gibt die Fehlermeldung zurück wenn der Befehl blockiert ist, sonst None."""
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

    shell_name = Path(tokens[0]).name.lower()
    if shell_name in _SHELL_WRAPPERS:
        for idx, token in enumerate(tokens[1:], start=1):
            if token == "-c" or (token.startswith("-") and "c" in token[1:]):
                if idx + 1 < len(tokens):
                    return _check_shell_blocklist(tokens[idx + 1])
                break
    return None


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
    /opt/octopos/ werden blockiert.
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
            "Timeout standard 30 Sekunden, maximal 120 Sekunden. "
            "VERBOTEN: rm -rf, dd auf Blockdevices, mkfs, fdisk, Schreiben nach /opt/octopos/, "
            "git clone/reset in /opt/octopos/, systemctl stop/disable octopos."
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
            p = Path("/opt/octopos") / path
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
        return (
            "Speichert Information dauerhaft im eigenen Gedächtnis. "
            "Verwende aussagekräftige Dateinamen wie 'project-context', 'learned-facts', 'user-preferences'. "
            "mode=overwrite (Standard) ersetzt die Datei, mode=append hängt Text an."
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
        with p.open(write_mode, encoding="utf-8") as handle:
            handle.write(content)
        logger.info("write_memory [%s]: %s (%s, %d bytes)", agent_id, safe, mode, len(content))
        return {"saved": True, "filename": safe, "bytes": len(content.encode())}


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
        return (
            "Stellt eine synchrone Frage oder gibt einen Task an einen anderen Agenten. "
            "Der Ziel-Agent antwortet direkt. Nutze dies um spezialisierte Agenten "
            "für bestimmte Aufgaben einzusetzen. Gibt die Antwort des Agenten zurück."
        )

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
                    "description": "ID des Ziel-Agenten (z.B. 'octopos-dev', 'claude_boss')",
                },
                "question": {
                    "type":        "string",
                    "description": "Frage oder Aufgabe für den Ziel-Agenten",
                },
                "context": {
                    "type":        "string",
                    "description": "Zusätzlicher Kontext der für den Agenten hilfreich ist (optional)",
                },
            },
            "required": ["target", "question"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        target: str, question: str, context: str = "",
    ) -> dict:
        import aiohttp as _aio

        content = f"{context}\n\n{question}".strip() if context else question
        logger.info("ask_agent [%s] → %s: %s…", agent_id, target, question[:60])
        try:
            async with _aio.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:8765/agents/{target}/message",
                    json={"content": content, "sender": agent_id},
                    timeout=_aio.ClientTimeout(total=120),
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
        return (
            "Beauftragt einen anderen Agenten asynchron mit einem Task via AgentLink-Handoff. "
            "Für lange Tasks die im Hintergrund laufen — kein direktes Warten auf Ergebnis. "
            "Gibt eine handoff_id zurück. Der Ziel-Agent holt sich den Auftrag über read_handoff."
        )

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
                    "description": "ID des Ziel-Agenten",
                },
                "task": {
                    "type":        "string",
                    "description": "Aufgabe die der Ziel-Agent erledigen soll",
                },
                "context": {
                    "type":        "string",
                    "description": "Kontext und Daten für den Agenten (optional)",
                },
                "ttl_seconds": {
                    "type":        "integer",
                    "description": "Gültigkeit des Handoffs in Sekunden (Standard: 3600)",
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
                "repo": {"type": "string", "description": "Repo-Referenz als URL, owner/repo oder Repo-Name"},
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
                "repo": {"type": "string", "description": "Repo-Referenz als URL, owner/repo oder Repo-Name"},
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
                "repo": {"type": "string", "description": "Repo-Referenz als URL, owner/repo oder Repo-Name"},
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
                "repo": {"type": "string", "description": "Repo-Referenz als URL, owner/repo oder Repo-Name"},
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
                "repo": {"type": "string", "description": "Repo-Referenz als URL, owner/repo oder Repo-Name"},
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
                "repo": {"type": "string", "description": "Repo-Referenz als URL, owner/repo oder Repo-Name"},
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
                "repo": {"type": "string", "description": "Repo-Referenz als URL, owner/repo oder Repo-Name"},
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

        # Remote-URL mit Token setzen
        remote_url = f"{cfg['url']}/{target['owner']}/{target['repo']}.git"
        token_url  = remote_url.replace("://", f"://octopos:{cfg['token']}@")
        await GiteaClient._git(["remote", "set-url", "origin", token_url], ws)

        # Branch ermitteln
        branch_out, _, _ = await GiteaClient._git(["branch", "--show-current"], ws)
        branch = branch_out.strip()
        if not branch:
            return {"error": "Kein aktiver Branch"}

        # Push
        _, err, rc = await GiteaClient._git(["push", "-u", "origin", branch], ws)
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
    USERS_FILE = Path("/etc/octopos/users.json")
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
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_kw: dict = {
                "hostname": wks["ip"],
                "username": wks.get("ssh_user", ""),
                "timeout":  30,
            }
            key_path = wks.get("ssh_key_path", "")
            if key_path and Path(key_path).exists():
                connect_kw["key_filename"] = key_path
            client.connect(**connect_kw)
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
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_kw: dict = {"hostname": wks["ip"], "username": wks.get("ssh_user", ""), "timeout": 30}
            key_path = wks.get("ssh_key_path", "")
            if key_path and Path(key_path).exists():
                connect_kw["key_filename"] = key_path
            client.connect(**connect_kw)
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
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_kw: dict = {"hostname": wks["ip"], "username": wks.get("ssh_user", ""), "timeout": 30}
            key_path = wks.get("ssh_key_path", "")
            if key_path and Path(key_path).exists():
                connect_kw["key_filename"] = key_path
            client.connect(**connect_kw)
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
        client = _discord_clients.get(agent_id)
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
        client = _discord_clients.get(agent_id)
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
        client = _discord_clients.get(agent_id)
        if not client:
            return {"error": "Discord nicht konfiguriert für diesen Agenten"}
        try:
            channels = await client.list_channels()
            return {"channels": channels}
        except Exception as e:
            return {"error": str(e)}


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
registry.register(DiscordSendTool())
registry.register(DiscordReadTool())
registry.register(DiscordListChannelsTool())
