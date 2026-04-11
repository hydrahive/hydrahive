"""
tool_registry.py — HydraHive v2 Core Tool Registry

9 Core-Tools analog zu Claude Code:
  1. shell_exec   — Bash (Swiss Army Knife)
  2. file_read    — Read (Zeilennummern, Offset/Limit)
  3. file_write   — Write (Atomares Schreiben)
  4. file_patch   — Edit (Präzises String-Replacement)
  5. file_search  — Grep (Strukturierte Suche)
  6. web_search   — WebSearch (SearXNG/DDG)
  7. read_memory  — Projekt-/Global-Memory lesen
  8. write_memory — Projekt-/Global-Memory schreiben
  9. ask_agent    — Subagent spawnen / andere Agents fragen

Alles andere (Git, Discord, Server, Mail, etc.) geht über shell_exec.
~990 Schema-Tokens statt ~8.800. Fixer Prefix → Cache-Hit 80-90%.
"""

import asyncio
import contextvars
import json
import logging
import re as _re_shell
import shlex as _shlex_shell
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .settings import settings

logger = logging.getLogger(__name__)

PROJECTS_ROOT = settings.projects_dir
AGENTS_ROOT   = settings.agents_dir

# Wird von main.py im Lifespan gesetzt
_internal_secret: str = ""
_rate_limiter: Any = None

# Interrupt-Flags für laufende ask_agent-Requests (#34)
_interrupt_flags: dict[str, bool] = {}


def set_interrupt(context_id: str) -> None:
    _interrupt_flags[context_id] = True


def clear_interrupt(context_id: str) -> None:
    _interrupt_flags.pop(context_id, None)


# Admin-Tool-Globals — gesetzt von main.py im Lifespan
_discovery: Any = None
_projects_registry: Any = None
_get_provisioner: Any = None
_load_users_fn: Any = None
_audit_log_fn: Any = None
_admin_agents_dir: str = str(settings.agents_dir)
_admin_projects_dir: str = str(settings.projects_dir)
_admin_runtime: Any = None


# =========================================================================
# Notification-Helper
# =========================================================================

def _notify(project_id: str, type: str, title: str, body: str, link: str | None = None) -> None:
    try:
        from .notification_service import notification_service as _ns
        import asyncio as _asyncio_notif

        users: list[str] = []
        if project_id and project_id.startswith("personal_"):
            users = [project_id[len("personal_"):]]
        elif _load_users_fn:
            try:
                all_users = _load_users_fn()
                users = [u for u, d in all_users.items() if d.get("role") == "admin"]
            except Exception:
                pass
        if not users:
            users = ["admin"]

        for user in users:
            _asyncio_notif.create_task(
                _ns.push(user=user, type=type, title=title, body=body, link=link)
            )
    except Exception:
        pass


# =========================================================================
# Path Safety (#54)
# =========================================================================

class PathSafetyError(PermissionError):
    pass


_active_worktree: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "_active_worktree", default=None,
)


def assert_path_within_project(
    path: str | Path,
    project_id: str,
    *,
    agent_permissions: list[str] | None = None,
) -> Path:
    import os

    wt = _active_worktree.get()
    if wt is not None:
        wt_resolved = wt.resolve()
        assert str(wt_resolved).startswith("/tmp/hydrahive-git"), \
            f"Worktree-Safety-Violation: {wt_resolved} liegt nicht unter /tmp/hydrahive-git/"
        project_root = wt_resolved
    else:
        project_root = (PROJECTS_ROOT / project_id).resolve()

    target = Path(path)
    if not target.is_absolute():
        target = project_root / target

    normalized = Path(os.path.normpath(target))
    try:
        normalized = normalized.resolve()
    except OSError:
        pass

    if agent_permissions is not None and "filesystem.read_all" in agent_permissions:
        try:
            normalized.relative_to(PROJECTS_ROOT.resolve())
        except ValueError:
            raise PathSafetyError(
                f"Zugriff verweigert: '{normalized}' liegt ausserhalb von '{PROJECTS_ROOT}'."
            )
        return normalized

    try:
        normalized.relative_to(project_root)
    except ValueError:
        raise PathSafetyError(
            f"Zugriff verweigert: '{normalized}' liegt ausserhalb von '{project_root}'."
        )

    return normalized


# =========================================================================
# BaseTool & ToolRegistry
# =========================================================================

class BaseTool(ABC):

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    def permissions_required(self) -> list[str]:
        return []

    @property
    def parallel_safe(self) -> bool:
        return False

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return False

    @property
    @abstractmethod
    def parameters(self) -> dict: ...

    @abstractmethod
    async def execute(self, agent_id: str, project_id: str, **kwargs) -> Any: ...

    def as_litellm_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name":        self.id,
                "description": self.description,
                "parameters":  self.parameters,
            },
        }


class ToolRegistry:

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.id] = tool
        logger.debug("Tool registriert: %s", tool.id)

    def get(self, tool_id: str) -> BaseTool | None:
        return self._tools.get(tool_id)

    def all_ids(self) -> list[str]:
        return list(self._tools.keys())

    _ALIASES: dict[str, str] = {
        "read_file":  "file_read",
        "write_file": "file_write",
    }

    def tools_for_agent(
        self,
        agent_tool_ids: list[str],
        agent_permissions: list[str] | None = None,
    ) -> list[BaseTool]:
        result = []
        for tool_id in agent_tool_ids:
            resolved = self._ALIASES.get(tool_id, tool_id)
            tool = self._tools.get(resolved)
            if tool is None:
                continue
            result.append(tool)
        return result

    def as_litellm_tools(self, tools: list[BaseTool]) -> list[dict]:
        return [t.as_litellm_tool() for t in tools]

    def all_tools(self) -> list[BaseTool]:
        """Gibt alle 9 Core-Tools zurück — keine Filterung nötig."""
        return list(self._tools.values())


registry = ToolRegistry()


# =========================================================================
# Shell Blocklist & Security
# =========================================================================

_PIPE_DANGEROUS_COMMANDS: frozenset[str] = frozenset({
    "rm", "rmdir", "dd", "mkfs", "fdisk", "parted", "shred", "wipefs",
    "kill", "killall", "pkill", "reboot", "shutdown", "poweroff", "halt", "init",
    "systemctl", "chmod", "chown", "chattr",
})

_ESCALATION_WRAPPERS: frozenset[str] = frozenset({
    "sudo", "pkexec", "doas", "su", "runuser", "machinectl",
})

_DANGEROUS_ENV_VARS: frozenset[str] = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH",
    "NODE_OPTIONS", "NODE_TLS_REJECT_UNAUTHORIZED",
    "GOFLAGS", "RUSTFLAGS",
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "PYTHONSTARTUP", "PERL5OPT", "RUBYOPT",
})

_ENV_VAR_PATTERN = _re_shell.compile(
    r'\$\{?(' + '|'.join(_re_shell.escape(v) for v in _DANGEROUS_ENV_VARS) + r')\}?'
)

_OBFUSCATION_PATTERNS: list[tuple[_re_shell.Pattern, str]] = [
    (_re_shell.compile(r'''(?:^|[\s;|&])([a-z]{0,3}["'][a-z]{1,3}["'][a-z]{0,3})\s+-[a-zA-Z]*[rf]''', _re_shell.IGNORECASE),
     "Obfuskierter Befehl (Anführungszeichen-Verkettung) verboten"),
    (_re_shell.compile(r"\$'[^']*\\x[0-9a-fA-F]{2}"),
     "Hex-Escape ($'\\x..') in Befehlen verboten"),
    (_re_shell.compile(r"\$'[^']*\\[0-7]{3}"),
     "Octal-Escape ($'\\NNN') in Befehlen verboten"),
    (_re_shell.compile(r'\bprintf\b.*\\x[0-9a-fA-F].*\|\s*(sh|bash|dash|zsh|ksh)\b'),
     "printf mit hex-escape nach Shell gepipet — verboten"),
    (_re_shell.compile(r'\b(base64|b64decode)\b.*\|\s*(sh|bash|dash|zsh|ksh|source)\b'),
     "base64-decode nach Shell gepipet — verboten"),
    (_re_shell.compile(r'\{[a-z],[a-z]\}'),
     "Brace-Expansion als Verschleierung verboten"),
    (_re_shell.compile(r'\b(eval|source)\s+["\$]'),
     "eval/source mit Variable/Substitution verboten"),
]


def _ast_check_command(command: str) -> str | None:
    for ch in command:
        cp = ord(ch)
        if cp < 0x20 and cp not in (0x09, 0x0A, 0x0D):
            return f"Kontrollzeichen U+{cp:04X} im Befehl verboten"
        if cp in (0x00A0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004,
                  0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200A, 0x200B,
                  0x202F, 0x205F, 0x2060, 0x3000, 0xFEFF):
            return f"Unicode-Whitespace U+{cp:04X} im Befehl verboten"

    for pattern, reason in _OBFUSCATION_PATTERNS:
        if pattern.search(command):
            return reason

    if '$(' in command:
        return "Command Substitution $(...) verboten"
    if '`' in command:
        return "Backticks (Command Substitution) verboten"
    if _re_shell.search(r'[<>]\(', command):
        return "Process Substitution <()/> () verboten"
    if _re_shell.search(r'\bzmodload\b', command):
        return "zmodload verboten"
    if _re_shell.search(r'\bemulate\b.*-c\b', command):
        return "emulate -c verboten"
    if _re_shell.search(r'(?:^|\s)=[a-zA-Z]', command):
        return "Zsh =cmd Expansion verboten"
    if _re_shell.search(r'\bIFS=', command):
        return "IFS-Manipulation verboten"
    if _re_shell.search(r'/proc/[0-9]+/environ\b|/proc/self/environ\b', command):
        return "/proc/environ Zugriff verboten"
    if _re_shell.search(r'\\-\\-[a-z]', command):
        return "Obfuskierte Flags (\\-\\-) verboten"

    env_match = _ENV_VAR_PATTERN.search(command)
    if env_match:
        return f"Zugriff auf gefährliche Variable ${env_match.group(1)} verboten"

    try:
        tokens = _shlex_shell.split(command)
    except ValueError as e:
        return f"Befehl konnte nicht sicher geparst werden (shlex: {e}) — FAIL-CLOSED"

    if not tokens:
        return None

    segments = _re_shell.split(r'\s*(?:\|(?!\|)|\|\||&&|;)\s*', command)
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        try:
            seg_tokens = _shlex_shell.split(segment)
        except ValueError:
            return f"Segment konnte nicht geparst werden: {segment[:60]} — FAIL-CLOSED"
        if not seg_tokens:
            continue

        resolved = _resolve_escalation(seg_tokens)
        first_exe = _re_shell.sub(r'^.*/', '', seg_tokens[0]).lower() if seg_tokens else ""
        if first_exe in _ESCALATION_WRAPPERS:
            if resolved and resolved != seg_tokens:
                resolved_cmd = " ".join(resolved)
                inner_block = _check_shell_blocklist(resolved_cmd)
                if inner_block:
                    return f"Escalation-Wrapper ({seg_tokens[0]}) um blockierten Befehl: {inner_block}"

    return None


def _resolve_escalation(tokens: list[str]) -> list[str]:
    result = tokens[:]
    for _ in range(5):
        if not result:
            break
        exe = _re_shell.sub(r'^.*/', '', result[0]).lower()
        if exe not in _ESCALATION_WRAPPERS:
            break
        rest = result[1:]
        while rest and rest[0].startswith('-'):
            if exe == 'su' and rest[0] == '-c' and len(rest) > 1:
                try:
                    return _shlex_shell.split(rest[1])
                except ValueError:
                    return rest[1:]
            rest = rest[1:]
        if exe in ('su', 'runuser') and rest and not rest[0].startswith('-'):
            rest = rest[1:]
        result = rest
    return result


_SHELL_BLOCKLIST: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f?|-[a-zA-Z]*f[a-zA-Z]*r|--recursive)\b", "rm -r / rm -rf verboten"),
    (r"\brm\b.*\s/opt/",            "rm auf /opt/ verboten"),
    (r"\brmdir\s+--parents\b",      "rmdir --parents verboten"),
    (r"\bdd\b.*\bof=/dev/",         "dd auf Blockdevice verboten"),
    (r"\bmkfs\b",                   "mkfs verboten"),
    (r"\bfdisk\b",                  "fdisk verboten"),
    (r"\bparted\b",                 "parted verboten"),
    (r"\bshred\b",                  "shred verboten"),
    (r"\bwipefs\b",                 "wipefs verboten"),
    (r"\bsystemctl\s+(stop|disable|mask|kill)\s+(hydrahive|octopos)", "systemctl stop/disable hydrahive verboten"),
    (r"\bkillall\s+uvicorn\b",      "killall uvicorn verboten"),
    (r"\bkill\b.*\buvicorn\b",      "kill uvicorn verboten"),
    (r":\(\)\s*\{",                 "Fork-Bombe verboten"),
    (r"\brm\s+(-[a-zA-Z]+ +)?/\s", "rm / verboten"),
    (r"\brm\s+(-[a-zA-Z]+ +)?/$",  "rm / verboten"),
    (r">\s*/opt/(hydrahive|octopos)/", "Redirect nach /opt/hydrahive/ verboten"),
    (r">\s*/etc/",                  "Redirect nach /etc/ verboten"),
    (r">\s*/bin/",                  "Redirect nach /bin/ verboten"),
    (r">\s*/usr/",                  "Redirect nach /usr/ verboten"),
    (r">\s*/lib",                   "Redirect nach /lib verboten"),
    (r">\s*/boot/",                 "Redirect nach /boot/ verboten"),
    (r">\s*/dev/",                  "Redirect nach /dev/ verboten"),
    (r">\s*/sys/",                  "Redirect nach /sys/ verboten"),
    (r">\s*/proc/",                 "Redirect nach /proc/ verboten"),
    (r"\btee\s+(/etc/|/opt/|/usr/|/bin/|/boot/|/lib|/sys/|/proc/)", "tee auf Systempfad verboten"),
    (r"\bcp\b.+\s(/etc/|/opt/(hydrahive|octopos)/|/usr/|/bin/|/boot/)", "cp nach Systempfad verboten"),
    (r"\b(wget|curl)\b.*\s-[a-zA-Z]*[oO]\s+/etc/", "Download nach /etc/ verboten"),
    (r"\b(wget|curl)\b.*\s-[a-zA-Z]*[oO]\s+/opt/(hydrahive|octopos)/", "Download nach /opt/hydrahive/ verboten"),
    (r"\b(chmod|chown)\b.*/opt/",   "chmod/chown auf /opt/ verboten"),
    (r"\b(chmod|chown)\b.*/etc/",   "chmod/chown auf /etc/ verboten"),
    (r"\b(chmod|chown)\b.*/bin/",   "chmod/chown auf /bin/ verboten"),
    (r"\bgit\b.*--hard\b.*\s/opt/", "git reset --hard auf /opt/ verboten"),
    (r"\bgit\s+clone\b.*\s/opt/",   "git clone nach /opt/ verboten"),
    (r"cd\s+/opt/(hydrahive|octopos)\b.*&&.*\bgit\b", "git in /opt/hydrahive/ verboten"),
    (r"\bperl\s+-[a-zA-Z]*e\b",     "perl -e (Inline-Code) verboten"),
    (r"\bruby\s+-[a-zA-Z]*e\b",     "ruby -e (Inline-Code) verboten"),
    (r"\bnode\s+-[a-zA-Z]*e\b",     "node -e (Inline-Code) verboten"),
    (r"\bnodejs\s+-[a-zA-Z]*e\b",   "nodejs -e (Inline-Code) verboten"),
    (r"\bsudo\b",                   "sudo verboten — Agenten laufen ohne Root"),
    (r"\$\(",                       "Command Substitution $(...) verboten"),
    (r"`",                          "Backticks verboten"),
    (r"\bcd\s+(/etc|/opt/(hydrahive|octopos)|/bin|/usr|/boot|/lib|/sys|/proc)\b", "cd in Systempfad verboten"),
    (r"\bLD_PRELOAD=",              "LD_PRELOAD verboten"),
    (r"\bLD_LIBRARY_PATH=",         "LD_LIBRARY_PATH verboten"),
    (r"\bDYLD_",                    "DYLD_* verboten"),
    (r"\bNODE_OPTIONS=",            "NODE_OPTIONS verboten"),
    (r"\bGOFLAGS=",                "GOFLAGS verboten"),
    (r"\bRUSTFLAGS=",              "RUSTFLAGS verboten"),
    (r"\bNODE_TLS_REJECT_UNAUTHORIZED=", "NODE_TLS_REJECT_UNAUTHORIZED verboten"),
    (r"\bHTTP_PROXY=",             "HTTP_PROXY verboten"),
    (r"\bHTTPS_PROXY=",            "HTTPS_PROXY verboten"),
    (r"\bhttp_proxy=",             "http_proxy verboten"),
    (r"\bhttps_proxy=",            "https_proxy verboten"),
    (r"\bPYTHONSTARTUP=",         "PYTHONSTARTUP verboten"),
    (r"\bPERL5OPT=",              "PERL5OPT verboten"),
    (r"\bRUBYOPT=",               "RUBYOPT verboten"),
]

_SHELL_WRAPPERS = {"bash", "sh", "zsh", "fish", "dash", "ksh"}
_EXEC_WRAPPERS  = {"env", "nohup", "nice", "ionice", "timeout", "xargs", "sudo", "su"}


def _check_shell_blocklist(command: str) -> str | None:
    ast_block = _ast_check_command(command)
    if ast_block:
        return ast_block

    for pattern, reason in _SHELL_BLOCKLIST:
        if _re_shell.search(pattern, command, _re_shell.IGNORECASE):
            return reason

    try:
        tokens = _shlex_shell.split(command)
    except ValueError:
        return "Befehl konnte nicht sicher geparst werden — FAIL-CLOSED"

    if not tokens:
        return None

    for token in tokens:
        if token == "eval":
            return "eval verboten"

    exe = Path(tokens[0]).name.lower()

    if exe in _SHELL_WRAPPERS:
        for idx, token in enumerate(tokens[1:], start=1):
            if token == "-c" or (token.startswith("-") and "c" in token[1:]):
                if idx + 1 < len(tokens):
                    return _check_shell_blocklist(tokens[idx + 1])
                break

    if exe in _EXEC_WRAPPERS:
        rest = tokens[1:]
        while rest and (rest[0].startswith("-") or "=" in rest[0]):
            rest = rest[1:]
        if rest:
            return _check_shell_blocklist(" ".join(rest))

    return None


_ALLOWED_CWD_PREFIXES = ("/tmp", str(settings.projects_dir), "/home", str(settings.agents_dir), "/var/tmp")


def _validate_shell_cwd(cwd: str) -> str | None:
    try:
        import os
        normalized = os.path.normpath(cwd)
        for prefix in _ALLOWED_CWD_PREFIXES:
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return None
        return f"CWD '{cwd}' nicht erlaubt — nur {', '.join(_ALLOWED_CWD_PREFIXES)}"
    except Exception:
        return None


# =========================================================================
# Memory Helper
# =========================================================================

def _safe_memory_filename(filename: str) -> str:
    base = filename.removesuffix(".md").strip()
    if not _re_shell.match(r"^[a-zA-Z0-9_-]+$", base):
        raise ValueError(f"Ungültiger Dateiname: '{filename}'. Nur a-z, A-Z, 0-9, _ und - erlaubt.")
    return base + ".md"


# =========================================================================
# Core Tool #1: shell_exec
# =========================================================================

class ShellExecTool(BaseTool):

    @property
    def id(self) -> str: return "shell_exec"
    @property
    def is_destructive(self) -> bool: return True
    @property
    def name(self) -> str: return "Shell-Befehl ausführen"
    @property
    def description(self) -> str:
        return (
            "Führt einen Bash-Befehl aus (stdout/stderr/exit_code). "
            "Nutze dieses Tool für: Git, System-Befehle, Pakete, SSH, curl, etc. "
            "Timeout bis 600s. cwd Standard: /tmp."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Bash-Befehl der ausgeführt werden soll",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in Sekunden (Standard: 30, max: 120)",
                },
                "cwd": {
                    "type": "string",
                    "description": "Arbeitsverzeichnis (Standard: /tmp)",
                },
            },
            "required": ["command"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        command: str, timeout: int = 30, cwd: str = "/tmp",
        **kwargs,
    ) -> dict:
        unrestricted = kwargs.get("_execution_mode") == "unrestricted"

        if not unrestricted:
            blocked = _check_shell_blocklist(command)
            if blocked:
                logger.warning("shell_exec BLOCKED [%s]: %s — %s", agent_id, command[:120], blocked)
                return {
                    "error": f"Befehl blockiert: {blocked}",
                    "command": command, "exit_code": -1, "blocked": True,
                }
            cwd_error = _validate_shell_cwd(cwd)
            if cwd_error:
                logger.warning("shell_exec CWD BLOCKED [%s]: %s", agent_id, cwd_error)
                return {"error": cwd_error, "command": command, "exit_code": -1, "blocked": True}

        max_timeout = 1800 if unrestricted else 120
        timeout = min(max(timeout, 1), max_timeout)
        safe_cwd = cwd if Path(cwd).exists() else "/tmp"

        import shutil
        _use_sandbox = not unrestricted and shutil.which("bwrap") is not None
        if _use_sandbox and kwargs.get("_execution_mode") == "safe":
            _quoted = __import__('shlex').quote(command)
            exec_command = (
                f"bwrap --ro-bind / / "
                f"--bind {__import__('shlex').quote(safe_cwd)} {__import__('shlex').quote(safe_cwd)} "
                f"--bind /tmp /tmp "
                f"--dev /dev --proc /proc "
                f"--unshare-net "
                f"--die-with-parent "
                f"-- bash -c {_quoted}"
            )
            logger.info("shell_exec [%s] (SANDBOX/bwrap): %s", agent_id, command[:120])
        elif unrestricted:
            exec_command = f"sudo bash -c {__import__('shlex').quote(command)}"
            logger.info("shell_exec [%s] (UNRESTRICTED/sudo): %s", agent_id, command[:120])
        else:
            exec_command = command
            logger.info("shell_exec [%s]: %s", agent_id, command[:120])

        try:
            proc = await asyncio.create_subprocess_shell(
                exec_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=safe_cwd,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return {"error": f"Timeout nach {timeout}s", "command": command, "exit_code": -1}

            out = stdout.decode(errors="replace")
            err = stderr.decode(errors="replace")
            max_out = 32000
            if len(out) > max_out:
                out = out[:max_out] + f"\n...[stdout gekürzt: {len(out)} Zeichen total]"
            if len(err) > max_out:
                err = err[:max_out] + f"\n...[stderr gekürzt: {len(err)} Zeichen total]"
            return {
                "stdout": out, "stderr": err,
                "exit_code": proc.returncode, "command": command,
            }
        except Exception as e:
            return {"error": str(e), "command": command, "exit_code": -1}


# =========================================================================
# Core Tool #2: file_read
# =========================================================================

class FileReadTool(BaseTool):

    @property
    def id(self) -> str: return "file_read"
    @property
    def parallel_safe(self) -> bool: return True
    @property
    def is_read_only(self) -> bool: return True
    @property
    def name(self) -> str: return "Datei lesen"
    @property
    def description(self) -> str:
        return (
            "Liest den Inhalt einer Datei aus dem Projekt-Verzeichnis. "
            "Pfad relativ zum Projekt-Root. "
            "Bei großen Dateien offset+limit nutzen — has_more=true zeigt weitere Daten an."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zur Datei, relativ zum Projekt-Verzeichnis"},
                "offset": {"type": "integer", "description": "Zeichen-Offset (Standard: 0)"},
                "limit": {"type": "integer", "description": "Max. Zeichen (Standard: 8000, Max: 32000)"},
            },
            "required": ["path"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        path: str, offset: int = 0, limit: int = 8000, **kwargs,
    ) -> dict:
        agent_permissions = kwargs.pop("_agent_permissions", None)
        try:
            safe_path = assert_path_within_project(path, project_id, agent_permissions=agent_permissions)
        except PathSafetyError as e:
            return {"error": str(e), "allowed": False}

        if not safe_path.exists():
            return {"error": f"Datei nicht gefunden: {path}", "path": str(safe_path)}
        if not safe_path.is_file():
            return {"error": f"Keine reguläre Datei: {path}", "path": str(safe_path)}

        try:
            content = safe_path.read_text(encoding="utf-8", errors="replace")
            total = len(content)
            limit = min(max(1, limit), 32000)
            offset = max(0, offset)
            chunk = content[offset:offset + limit]
            has_more = (offset + limit) < total
            logger.info("file_read: %s liest %s offset=%d limit=%d", agent_id, safe_path, offset, limit)
            FileWriteTool.mark_read(agent_id, str(safe_path))
            result = {"content": chunk, "path": str(safe_path), "total_size": total, "offset": offset}
            if has_more:
                result["has_more"] = True
                result["next_offset"] = offset + limit
            return result
        except OSError as e:
            return {"error": f"Lesefehler: {e}", "path": str(safe_path)}


# =========================================================================
# Core Tool #3: file_write
# =========================================================================

class FileWriteTool(BaseTool):

    _read_state: dict[str, set[str]] = {}
    _MAX_READ_STATE_PER_AGENT = 500
    _checkpoints: dict[str, list[tuple[str, str]]] = {}
    _MAX_CHECKPOINTS = 50

    @classmethod
    def mark_read(cls, agent_id: str, path: str) -> None:
        paths = cls._read_state.setdefault(agent_id, set())
        paths.add(path)
        if len(paths) > cls._MAX_READ_STATE_PER_AGENT:
            to_keep = list(paths)[cls._MAX_READ_STATE_PER_AGENT // 2:]
            cls._read_state[agent_id] = set(to_keep)

    @property
    def id(self) -> str: return "file_write"
    @property
    def is_destructive(self) -> bool: return True
    @property
    def name(self) -> str: return "Datei schreiben"
    @property
    def description(self) -> str:
        return (
            "Schreibt Inhalt in eine Datei im Projekt-Verzeichnis. "
            "Erstellt Datei und Unterordner wenn nötig. "
            "WICHTIG für große Dateien (>100 Zeilen): Erst overwrite, dann append."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zur Datei, relativ zum Projekt-Verzeichnis"},
                "content": {"type": "string", "description": "Inhalt der geschrieben werden soll"},
                "mode": {"type": "string", "enum": ["overwrite", "append"], "description": "overwrite (Standard) oder append"},
            },
            "required": ["path", "content"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        path: str, content: str = "", mode: str = "overwrite", **kwargs,
    ) -> dict:
        if content is None or content == "":
            return {"error": "content darf nicht leer sein", "path": path}
        try:
            safe_path = assert_path_within_project(path, project_id)
        except PathSafetyError as e:
            return {"error": str(e), "allowed": False}

        if safe_path.exists() and mode == "overwrite":
            read_files = self._read_state.get(agent_id, set())
            if str(safe_path) not in read_files:
                return {
                    "error": f"Read-Before-Edit: '{path}' nicht vorher gelesen.",
                    "path": str(safe_path), "hint": "file_read zuerst aufrufen",
                }

        try:
            safe_path.parent.mkdir(parents=True, exist_ok=True)

            if safe_path.exists() and mode == "overwrite":
                try:
                    old_content = safe_path.read_text(encoding="utf-8", errors="replace")
                    stack = self._checkpoints.setdefault(agent_id, [])
                    stack.append((str(safe_path), old_content))
                    if len(stack) > self._MAX_CHECKPOINTS:
                        stack[:] = stack[-self._MAX_CHECKPOINTS:]
                except OSError:
                    pass

            write_mode = "a" if mode == "append" else "w"
            with safe_path.open(write_mode, encoding="utf-8") as handle:
                handle.write(content)
            logger.info("file_write: %s schreibt %s (%s)", agent_id, safe_path, mode)
            return {"written": True, "path": str(safe_path), "bytes": len(content.encode())}
        except OSError as e:
            return {"error": f"Schreibfehler: {e}", "path": str(safe_path)}


# =========================================================================
# Core Tool #4: file_patch
# =========================================================================

class FilePatchTool(BaseTool):

    @property
    def id(self) -> str: return "file_patch"
    @property
    def name(self) -> str: return "Datei patchen (Suchen & Ersetzen)"
    @property
    def description(self) -> str:
        return (
            "Sucht einen Text-Abschnitt in einer Datei und ersetzt ihn. "
            "Ideal für gezielte Änderungen ohne die ganze Datei zu lesen. "
            "Unterstützt mehrzeilige Suche und Ersetzung."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Dateipfad (relativ zum Projekt oder absolut)"},
                "search": {"type": "string", "description": "Text der gesucht werden soll (exakt)"},
                "replace": {"type": "string", "description": "Ersetzungstext"},
                "count": {"type": "integer", "description": "Max. Ersetzungen (0=alle, Standard: 1)"},
            },
            "required": ["path", "search", "replace"],
        }

    async def execute(self, agent_id: str, project_id: str, path: str, search: str, replace: str, count: int = 1, **kwargs) -> dict:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = Path(f"/projects/{project_id}/files") / path
            if not file_path.exists():
                file_path = Path(f"/projects/{project_id}") / path

        if not file_path.exists():
            return {"error": f"Datei nicht gefunden: {path}"}

        resolved = str(file_path.resolve())
        read_files = FileWriteTool._read_state.get(agent_id, set())
        if resolved not in read_files:
            return {
                "error": f"Read-Before-Edit: '{path}' nicht vorher gelesen.",
                "path": resolved, "hint": "file_read zuerst aufrufen",
            }

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"error": f"Datei nicht lesbar: {e}"}

        occurrences = content.count(search)
        if occurrences == 0:
            lines = content.split("\n")
            snippet = "\n".join(lines[:30]) if len(lines) > 30 else content[:2000]
            return {
                "error": "Suchtext nicht gefunden",
                "occurrences": 0, "file_lines": len(lines),
                "file_size": len(content), "first_30_lines": snippet,
            }

        if count == 0:
            new_content = content.replace(search, replace)
            replaced = occurrences
        else:
            new_content = content.replace(search, replace, count)
            replaced = min(count, occurrences)

        try:
            file_path.write_text(new_content, encoding="utf-8")
        except PermissionError:
            import subprocess, tempfile
            with tempfile.NamedTemporaryFile(mode="w", suffix=".tmp", delete=False, encoding="utf-8") as tmp:
                tmp.write(new_content)
                tmp_path = tmp.name
            r = await asyncio.to_thread(lambda: subprocess.run(
                ["sudo", "cp", tmp_path, str(file_path)], capture_output=True, timeout=10))
            Path(tmp_path).unlink(missing_ok=True)
            if r.returncode != 0:
                return {"error": f"Schreibfehler (auch mit sudo): {r.stderr.decode()[:200]}"}

        new_lines = new_content.split("\n")
        context_lines = []
        for i, line in enumerate(new_lines):
            if replace in line or (i > 0 and replace in new_lines[i-1]):
                start = max(0, i - 3)
                end = min(len(new_lines), i + 4)
                context_lines = [f"{start+j+1:4d} | {new_lines[start+j]}" for j in range(end - start)]
                break

        return {
            "ok": True, "path": str(file_path),
            "occurrences_found": occurrences, "replaced": replaced,
            "context": "\n".join(context_lines) if context_lines else "(keine Kontextzeilen)",
        }


# =========================================================================
# Core Tool #5: file_search
# =========================================================================

class FileSearchTool(BaseTool):

    @property
    def id(self) -> str: return "file_search"
    @property
    def parallel_safe(self) -> bool: return True
    @property
    def is_read_only(self) -> bool: return True
    @property
    def name(self) -> str: return "In Dateien suchen (grep)"
    @property
    def description(self) -> str:
        return (
            "Durchsucht alle Dateien im Projektverzeichnis nach einem Text oder Pattern. "
            "Gibt Dateinamen, Zeilennummern und Kontext zurück."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Suchtext oder Pattern"},
                "path": {"type": "string", "description": "Verzeichnis oder Datei (optional)"},
                "file_pattern": {"type": "string", "description": "Dateiname-Filter z.B. '*.py' (optional)"},
                "max_results": {"type": "integer", "description": "Max. Treffer (Standard: 20)"},
            },
            "required": ["pattern"],
        }

    async def execute(self, agent_id: str, project_id: str, pattern: str, path: str = "", file_pattern: str = "", max_results: int = 20, **kwargs) -> dict:
        import subprocess

        search_dir = Path(path) if path and Path(path).is_absolute() else Path(f"/projects/{project_id}") / (path or "")
        if not search_dir.exists():
            return {"error": f"Verzeichnis nicht gefunden: {search_dir}"}

        cmd = ["grep", "-rn", "--include", file_pattern or "*", "-m", str(max_results * 3), pattern, str(search_dir)]
        try:
            r = await asyncio.to_thread(lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30))
        except subprocess.TimeoutExpired:
            return {"error": "Suche dauerte zu lange (>30s)"}

        if r.returncode == 1:
            return {"matches": [], "count": 0, "pattern": pattern}

        lines = r.stdout.strip().split("\n")[:max_results]
        matches = []
        for line in lines:
            if ":" in line:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    matches.append({
                        "file": parts[0].replace(str(search_dir) + "/", ""),
                        "line": int(parts[1]) if parts[1].isdigit() else 0,
                        "text": parts[2][:200].strip(),
                    })

        return {"matches": matches, "count": len(matches), "pattern": pattern, "search_dir": str(search_dir)}


# =========================================================================
# Core Tool #6: web_search
# =========================================================================

class WebSearchTool(BaseTool):

    SEARXNG_URL = "http://127.0.0.1:8888/search"

    @property
    def id(self) -> str: return "web_search"
    @property
    def parallel_safe(self) -> bool: return True
    @property
    def is_read_only(self) -> bool: return True
    @property
    def name(self) -> str: return "Web-Suche"
    @property
    def description(self) -> str:
        return "Sucht im Web nach aktuellen Informationen. Gibt Ergebnisse mit Titel, URL und Zusammenfassung zurück."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchanfrage"},
                "max_results": {"type": "integer", "description": "Max. Ergebnisse (Standard: 8)"},
            },
            "required": ["query"],
        }

    async def execute(self, agent_id: str, project_id: str, query: str, max_results: int = 8, **kwargs) -> dict:
        import aiohttp
        from urllib.parse import urlencode

        params: dict = {"q": query, "format": "json"}
        searxng_url = f"{self.SEARXNG_URL}?{urlencode(params)}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(searxng_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        results = [
                            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", ""), "engine": r.get("engine", "")}
                            for r in data.get("results", []) if r.get("url")
                        ]
                        return {"query": query, "engine": "searxng", "results": results[:max_results]}
        except Exception as e:
            logger.debug("web_search: SearXNG nicht erreichbar (%s) — Fallback auf DDG", e)

        ddg_params = urlencode({"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"})
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.duckduckgo.com/?{ddg_params}", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json(content_type=None)
        except Exception as e:
            return {"error": f"Suche fehlgeschlagen: {e}", "results": []}

        results = []
        if data.get("AbstractText"):
            results.append({"title": data.get("Heading", "Direkte Antwort"), "url": data.get("AbstractURL", ""), "snippet": data["AbstractText"], "engine": "duckduckgo"})
        for topic in data.get("RelatedTopics", []):
            if len(results) >= max_results:
                break
            if "Topics" in topic:
                continue
            text = topic.get("Text", "")
            link = topic.get("FirstURL", "")
            if text and link:
                results.append({"title": link.split("/")[-1].replace("_", " "), "url": link, "snippet": text, "engine": "duckduckgo"})
        return {"query": query, "engine": "duckduckgo_fallback", "results": results[:max_results]}


# =========================================================================
# Core Tool #7: read_memory
# =========================================================================

class ReadMemoryTool(BaseTool):

    @property
    def id(self) -> str: return "read_memory"
    @property
    def parallel_safe(self) -> bool: return True
    @property
    def is_read_only(self) -> bool: return True
    @property
    def name(self) -> str: return "Gedächtnis lesen"
    @property
    def description(self) -> str:
        return (
            "Liest Dateien aus dem persistenten Gedächtnis. "
            "Ohne filename: listet alle vorhandenen Dateien auf. "
            "Mit filename: gibt den Inhalt zurück."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Dateiname (z.B. 'facts.md'). Leer = alle auflisten."},
            },
            "required": [],
        }

    async def execute(self, agent_id: str, project_id: str, filename: str = "", **kwargs) -> dict:
        import sqlite3 as _sqlite3
        from .memory_decay import touch_recall as _touch_recall

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

        content = p.read_text(encoding="utf-8")

        db_path = AGENTS_ROOT / agent_id / "memory_index.db"
        if db_path.exists():
            try:
                _conn = _sqlite3.connect(str(db_path))
                _conn.execute("PRAGMA foreign_keys=ON")
                try:
                    ids = [r[0] for r in _conn.execute(
                        "SELECT id FROM chunks WHERE source = ?",
                        (safe.removesuffix(".md"),),
                    ).fetchall()]
                    if ids:
                        _touch_recall(_conn, ids)
                        _conn.commit()
                finally:
                    _conn.close()
            except Exception:
                pass

        return {"filename": safe, "content": content}


# =========================================================================
# Core Tool #8: write_memory
# =========================================================================

class WriteMemoryTool(BaseTool):

    @property
    def id(self) -> str: return "write_memory"
    @property
    def name(self) -> str: return "Gedächtnis schreiben"
    @property
    def description(self) -> str:
        return (
            "Speichert Text dauerhaft im Gedächtnis. "
            "mode=overwrite ersetzt, append hängt an. "
            "importance (0-1) und category steuern Lebensdauer."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Dateiname (z.B. 'learned-facts')"},
                "content": {"type": "string", "description": "Inhalt (Markdown)"},
                "mode": {"type": "string", "enum": ["overwrite", "append"]},
                "importance": {"type": "number", "description": "Wichtigkeit 0.0-1.0 (Standard: 0.5)"},
                "category": {"type": "string", "enum": ["strategy", "fact", "assumption", "failure"], "description": "Kategorie (Standard: fact)"},
            },
            "required": ["filename", "content"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        filename: str, content: str, mode: str = "overwrite",
        importance: float = 0.5, category: str = "fact",
        **kwargs,
    ) -> dict:
        import sqlite3 as _sqlite3
        from .memory_decay import (
            set_file_meta as _set_file_meta,
            dedup_decision as _dedup_decision,
            detect_contradiction as _detect_contradiction,
            VALID_CATEGORIES,
        )
        from .semantic_index import find_similar_chunk as _find_similar

        importance = max(0.0, min(1.0, float(importance)))
        if category not in VALID_CATEGORIES:
            category = "fact"

        try:
            safe = _safe_memory_filename(filename)
        except ValueError as e:
            return {"error": str(e)}

        agent_dir = AGENTS_ROOT / agent_id
        memory_dir = agent_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        db_path = agent_dir / "memory_index.db"
        if db_path.exists():
            try:
                _conn = _sqlite3.connect(str(db_path))
                _conn.execute("PRAGMA foreign_keys=ON")
                try:
                    _set_file_meta(_conn, safe.removesuffix(".md"), importance, category)
                    _conn.commit()
                finally:
                    _conn.close()
            except Exception:
                pass

        dedup_action = "new"
        similar_text = ""
        try:
            _loop = asyncio.get_event_loop()
            similar = await _loop.run_in_executor(
                None, lambda: _find_similar(agent_dir, content, threshold=0.65)
            )
            if similar is not None:
                similar_text, sim_score = similar
                is_contra = _detect_contradiction(similar_text, content)
                dedup_action = _dedup_decision(sim_score, is_contra)
        except Exception:
            pass

        if dedup_action == "reinforce":
            return {
                "saved": False, "action": "reinforce", "filename": safe,
                "hint": "Ähnliche Erinnerung existiert bereits — recall_count erhöht.",
            }

        if dedup_action == "merge" and similar_text:
            p = memory_dir / safe
            existing = p.read_text(encoding="utf-8").strip() if p.exists() else ""
            if existing and existing != content.strip():
                content = existing + "\n\n---\n\n" + content

        p = memory_dir / safe
        write_mode = "a" if mode == "append" else "w"
        with p.open(write_mode, encoding="utf-8") as handle:
            handle.write(content)

        logger.info("write_memory [%s]: %s (%s, importance=%.1f, category=%s, action=%s)",
                     agent_id, safe, mode, importance, category, dedup_action)
        return {
            "saved": True, "filename": safe, "bytes": len(content.encode()),
            "importance": importance, "category": category, "action": dedup_action,
        }


# =========================================================================
# Core Tool #9: ask_agent
# =========================================================================

class AskAgentTool(BaseTool):

    @property
    def id(self) -> str: return "ask_agent"
    @property
    def name(self) -> str: return "Agenten fragen"
    @property
    def description(self) -> str:
        return "Synchrone Frage/Task an einen anderen Agenten — antwortet direkt."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Ziel-Agent-ID"},
                "question": {"type": "string", "description": "Frage/Task"},
                "context": {"type": "string", "description": "Zusätzlicher Kontext (optional)"},
                "project_id": {"type": "string", "description": "Projekt-ID für den Ziel-Agent (optional)"},
            },
            "required": ["target", "question"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        target: str, question: str, context: str = "", **kwargs,
    ) -> dict:
        import aiohttp as _aio
        import hmac as _hmac
        import time as _time
        import uuid as _uuid

        if _rate_limiter is not None:
            _rate_limiter.check_agent_call(agent_id)

        content = f"{context}\n\n{question}".strip() if context else question
        logger.info("ask_agent [%s] → %s: %s…", agent_id, target, question[:60])

        headers: dict = {}
        if _internal_secret:
            ts = str(int(_time.time()))
            sig = _hmac.new(_internal_secret.encode(), ts.encode(), "sha256").hexdigest()
            headers = {"X-Internal-Timestamp": ts, "X-Internal-Signature": sig}

        explicit_project_id = kwargs.get("project_id", "")
        if explicit_project_id.strip():
            session_id = explicit_project_id.strip()
        elif project_id and not project_id.startswith("personal_"):
            session_id = project_id
        else:
            session_id = f"{target}_{_uuid.uuid4().hex[:8]}"

        try:
            async def _do_post():
                async with _aio.ClientSession() as _s:
                    async with _s.post(
                        f"http://127.0.0.1:8765/agents/{target}/message",
                        json={"content": content, "sender": agent_id, "project_id": session_id},
                        headers=headers,
                        timeout=_aio.ClientTimeout(total=300),
                    ) as _resp:
                        return _resp.status, await _resp.json()

            task = asyncio.create_task(_do_post())
            while not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                if _interrupt_flags.get(project_id) or _interrupt_flags.get(agent_id):
                    task.cancel()
                    clear_interrupt(project_id)
                    clear_interrupt(agent_id)
                    _notify(project_id, "task_failed", f"Abgebrochen: {target}",
                            f"Agent '{target}' wurde unterbrochen.", link=f"/chat/{project_id}")
                    return {
                        "interrupted": True, "agent_id": target,
                        "directive": "Der Nutzer hat den Request abgebrochen.",
                    }

            status, data = task.result()
            if status == 404:
                return {"error": f"Agent '{target}' nicht gefunden", "agent_id": target}
            response = data.get("response", "")
            if not response or not response.strip():
                return {
                    "agent_id": target, "worker_failed": True,
                    "directive": f"Agent '{target}' hat keine Antwort geliefert.",
                }

            _error_keywords = (
                "konnte nicht", "fehlgeschlagen", "fehler:", "error:", "permission denied",
                "nicht abgeschlossen", "nicht möglich", "nicht erlaubt", "nicht gefunden",
            )
            response_lower = response.lower()
            worker_errors = [kw for kw in _error_keywords if kw in response_lower]
            result: dict = {"agent_id": target, "response": response, "success": True}
            if worker_errors:
                result["worker_reported_errors"] = True
                result["hint"] = "Der Worker hat Fehler gemeldet (siehe response)."
                _notify(project_id, "task_failed", f"Fehler: {target}", response[:120], link=f"/chat/{project_id}")
            else:
                _notify(project_id, "task_done", f"Fertig: {target}", response[:120], link=f"/chat/{project_id}")
            return result
        except Exception as e:
            _notify(project_id, "task_failed", f"Kommunikationsfehler: {target}", str(e)[:120], link=f"/chat/{project_id}")
            return {"error": f"Fehler bei Kommunikation mit '{target}': {e}", "agent_id": target}


# =========================================================================
# Register all 9 Core Tools
# =========================================================================

registry.register(ShellExecTool())
registry.register(FileReadTool())
registry.register(FileWriteTool())
registry.register(FilePatchTool())
registry.register(FileSearchTool())
registry.register(WebSearchTool())
registry.register(ReadMemoryTool())
registry.register(WriteMemoryTool())
registry.register(AskAgentTool())
