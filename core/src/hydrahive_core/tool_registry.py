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

# Discord-Client-Registry — wird von router_user_integrations + butler_executor genutzt.
# Dict {personal_agent_id: AgentDiscordClient}
_discord_clients: dict = {}

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

    # ── Deferred-Tools (#620 Phase 1) ─────────────────────────────────────
    # always_loaded=True: volles Schema immer im Prompt (wie bisher)
    # always_loaded=False: nur Name+one_line in <available-deferred-tools>,
    #   Schema lädt Model via ToolSearch bei Bedarf.
    # Default bleibt True → Verhalten identisch bis Tools aktiv migrieren.
    @property
    def always_loaded(self) -> bool:
        return True

    @property
    def category(self) -> str:
        return "core"

    @property
    def semantic_tags(self) -> list[str]:
        return []

    @property
    def one_line(self) -> str:
        first = self.description.split("\n", 1)[0].strip()
        return first[:120]

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

    # ── Deferred-Tools Partitionierung (#620 Phase 1) ─────────────────────
    def always_loaded_tools(self) -> list[BaseTool]:
        return [t for t in self._tools.values() if t.always_loaded]

    def deferred_tools(self) -> list[BaseTool]:
        return [t for t in self._tools.values() if not t.always_loaded]

    def resolve_many(self, ids: list[str]) -> list[BaseTool]:
        out: list[BaseTool] = []
        for tid in ids:
            resolved = self._ALIASES.get(tid, tid)
            t = self._tools.get(resolved)
            if t is not None:
                out.append(t)
        return out


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
    # Git-Hook-Manipulation blocken (#622)
    # mv/cp/rm/chmod auf Hook-Dateien verhindern — Hooks sind kritisch
    # für Branch-Protection, Commit-Validation, Signaturen.
    (r"\b(mv|cp|rm|chmod|chown)\b[^|;&]*/hooks/",
     "Manipulation von Git-/Service-Hooks verboten"),
    (r"\.git/hooks/[a-zA-Z_-]+\b",
     "Direkter Zugriff auf .git/hooks/* verboten"),
    (r"/opt/gitea/(git|data)/repositories",
     "Gitea-Datenverzeichnis verboten — nutze git_* oder gitea_* Tools"),
    # Secret-Pfade: jeder Zugriff auf Konfig-/Token-Dateien wird geblockt.
    # Muster matcht in ganzem Command-String, egal ob cat, less, grep, find,
    # python -c "open(...)", awk -f, ls -la, tail, head, stat, file …
    (r"/etc/hydrahive(/|_|\b)",    "/etc/hydrahive/* ist für Agenten tabu (Tokens!)"),
    (r"/etc/octopos(/|_|\b)",      "/etc/octopos/* ist für Agenten tabu (Tokens!)"),
    (r"/etc/shadow\b",             "/etc/shadow verboten"),
    (r"/etc/gshadow\b",            "/etc/gshadow verboten"),
    (r"/etc/sudoers(\.d)?\b",      "/etc/sudoers verboten"),
    (r"\.ssh/id_[a-z0-9_]+\b",     "SSH-Private-Keys verboten"),
    (r"\.ssh/authorized_keys\b",   "authorized_keys verboten"),
    (r"/root/",                    "Zugriff auf /root/ verboten"),
    (r"\.aws/credentials\b",       "AWS-Credentials verboten"),
    (r"/var/run/hydrahive",        "hydrahive-Runtime-State verboten"),
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
        _mode = kwargs.get("_execution_mode")
        unrestricted = _mode == "unrestricted"
        # v2 (#606): safe UND elevated werden sandboxed. Nur safe hat Blocklist.
        # elevated erlaubt freie Commands aber im bwrap-Scope (kein Host-Escape).
        is_sandboxed = _mode in ("safe", "elevated")

        # Blocklist nur fuer safe — elevated darf npm/git/apt-get etc. nutzen
        if _mode == "safe":
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
        # bwrap-Funktionstest: einmalig cachen ob Sandbox lauffaehig ist.
        # #605: Nutzt dieselbe Mount-Topologie wie die echte Sandbox unten,
        # damit der Test nicht "ok-wenn-/bin-Symlink-auf-/usr/bin"-faelschlich
        # positiv oder negativ ausfaellt.
        if not hasattr(ShellExecTool, "_bwrap_works"):
            ShellExecTool._bwrap_works = False
            if shutil.which("bwrap"):
                try:
                    import subprocess as _sp
                    _test_bind: list[str] = ["bwrap",
                        "--ro-bind", "/usr", "/usr",
                        "--ro-bind-try", "/bin", "/bin",
                        "--ro-bind-try", "/lib", "/lib",
                        "--ro-bind-try", "/lib64", "/lib64",
                        "--ro-bind-try", "/sbin", "/sbin",
                        "--proc", "/proc",
                        "--dev", "/dev",
                        "--die-with-parent",
                        "--", "/usr/bin/true",  # garantiert in /usr
                    ]
                    _test = _sp.run(_test_bind, capture_output=True, timeout=5, check=False)
                    ShellExecTool._bwrap_works = (_test.returncode == 0)
                    if not ShellExecTool._bwrap_works:
                        logger.warning("bwrap ist installiert aber nicht funktionsfaehig: %s",
                                       _test.stderr.decode(errors='replace')[:200])
                except Exception as _e:
                    logger.warning("bwrap-Funktionstest fehlgeschlagen: %s", _e)

        _use_sandbox = not unrestricted and ShellExecTool._bwrap_works
        # Fail-closed: safe + elevated ohne funktionierende Sandbox → verweigern (#593, #606)
        if is_sandboxed and not _use_sandbox:
            return {
                "error": f"shell_exec im {_mode}-Modus verweigert: Sandbox (bwrap) nicht funktionsfaehig. "
                         "Administrator muss bwrap mit subuid/subgid einrichten oder "
                         "execution_mode auf 'unrestricted' setzen (Admin-only).",
                "command": command, "exit_code": -1, "blocked": True,
            }
        if _use_sandbox and is_sandboxed:
            import shlex as _shlex
            _quoted = _shlex.quote(command)
            # Projekt-Verzeichnis ermitteln (fuer Scope)
            _project_dir = f"/projects/{project_id}" if project_id else ""
            # Sicherstellen dass cwd im Projekt-Dir oder /tmp liegt
            _cwd_resolved = str(Path(safe_cwd).resolve())
            _cwd_in_scope = (
                _cwd_resolved.startswith("/tmp") or
                (_project_dir and _cwd_resolved.startswith(_project_dir))
            )
            if not _cwd_in_scope:
                # Fallback auf Projekt-Dir oder /tmp
                safe_cwd = _project_dir if _project_dir and Path(_project_dir).exists() else "/tmp"
                _cwd_resolved = safe_cwd

            # Minimale Sandbox — nur System-Binaries/Libs + Projekt + /tmp
            # NICHT `/` komplett mounten (#593: verhindert Host-Leak)
            _bind_args = [
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/bin", "/bin",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/lib64", "/lib64",
                "--ro-bind", "/sbin", "/sbin",
                "--ro-bind-try", "/etc/alternatives", "/etc/alternatives",
                "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
                "--ro-bind-try", "/etc/ld.so.conf", "/etc/ld.so.conf",
                "--ro-bind-try", "/etc/ld.so.conf.d", "/etc/ld.so.conf.d",
                "--ro-bind-try", "/etc/ssl/certs", "/etc/ssl/certs",
                "--ro-bind-try", "/etc/ca-certificates", "/etc/ca-certificates",
                "--ro-bind-try", "/etc/resolv.conf", "/etc/resolv.conf",
                "--bind", "/tmp", "/tmp",
                "--dev", "/dev",
                "--proc", "/proc",
                "--setenv", "HOME", "/tmp",
                "--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            ]
            # Projekt-Dir read-write binden (wenn vorhanden)
            if _project_dir and Path(_project_dir).exists():
                _bind_args += ["--bind", _project_dir, _project_dir]
            # cwd read-write binden falls ausserhalb /tmp/Projekt
            if safe_cwd not in {"/tmp", _project_dir} and Path(safe_cwd).exists():
                _bind_args += ["--bind", safe_cwd, safe_cwd]

            _bind_cmd = " ".join(_shlex.quote(a) for a in _bind_args)
            exec_command = (
                f"bwrap {_bind_cmd} "
                f"--die-with-parent "
                f"-- bash -c {_quoted}"
            )
            logger.info("shell_exec [%s] (SANDBOX/bwrap mode=%s scope=%s): %s",
                        agent_id, _mode or "safe", _project_dir or "/tmp", command[:120])
        elif unrestricted:
            # UNRESTRICTED: kein Sandbox, aber als Projekt-User statt root.
            # Vorteile: Files gehören dem Projekt-User → menschlicher Admin
            # (auch ohne sudo) kann nachschauen/eingreifen, .git/rebase-merge
            # blockt nicht mehr Core-Writes auf memory/_last_session.md.
            import shlex as _shlex
            _quoted = _shlex.quote(command)
            _proj_user: str | None = None
            if project_id:
                # Konvention aus project_config.effective_system_user(): proj_<id>
                _candidate = f"proj_{project_id}"
                try:
                    import pwd as _pwd
                    _pwd.getpwnam(_candidate)
                    _proj_user = _candidate
                except KeyError:
                    _proj_user = None
                except Exception as _u_err:
                    logger.debug("pwd lookup failed (%s): %s", _candidate, _u_err)
                    _proj_user = None
            if _proj_user and _proj_user != "root":
                exec_command = f"sudo -n -u {_shlex.quote(_proj_user)} bash -c {_quoted}"
                logger.info("shell_exec [%s] (UNRESTRICTED/user=%s): %s",
                            agent_id, _proj_user, command[:120])
            else:
                # Fallback: kein proj_<id>-User vorhanden → root via sudo
                exec_command = f"sudo bash -c {_quoted}"
                logger.info("shell_exec [%s] (UNRESTRICTED/sudo-root fallback): %s",
                            agent_id, command[:120])
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

            # Auto-Push nach git commit (#617): verhindert Datenverlust bei Session-Ende
            # Wenn 'git commit' erfolgreich war, automatisch 'git push' hinterherschicken.
            _cmd_stripped = command.strip()
            _is_git_commit = (
                proc.returncode == 0
                and "git commit" in _cmd_stripped
                and "git push" not in _cmd_stripped
                and "--dry-run" not in _cmd_stripped
                and "--amend" not in _cmd_stripped.lower()
            )
            if _is_git_commit:
                try:
                    push_proc = await asyncio.create_subprocess_shell(
                        "git push",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=safe_cwd,
                    )
                    push_out, push_err = await asyncio.wait_for(push_proc.communicate(), timeout=60)
                    push_stdout = push_out.decode(errors="replace")
                    push_stderr = push_err.decode(errors="replace")
                    if push_proc.returncode == 0:
                        out += f"\n[Auto-Push: OK]\n{push_stdout}".rstrip()
                        logger.info("shell_exec auto-push nach git commit: OK (cwd=%s)", safe_cwd)
                    else:
                        out += f"\n[Auto-Push fehlgeschlagen — bitte manuell pushen]\n{push_stderr[:500]}"
                        logger.warning("shell_exec auto-push fehlgeschlagen: %s", push_stderr[:200])
                except Exception as push_exc:
                    out += f"\n[Auto-Push Fehler: {push_exc}]"

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
            # Diagnose 1: Whitespace-normalisiert suchen
            _search_stripped = "\n".join(l.rstrip() for l in search.split("\n"))
            _content_stripped = "\n".join(l.rstrip() for l in lines)
            _ws_match = _search_stripped in _content_stripped
            # Diagnose 2: Erste Zeile des Suchtexts finden → Kandidaten-Zeilen zeigen
            _first_search_line = search.split("\n")[0].strip()
            _candidate_lines = []
            for i, line in enumerate(lines):
                if _first_search_line and _first_search_line in line:
                    start = max(0, i - 1)
                    end = min(len(lines), i + len(search.split("\n")) + 2)
                    _candidate_lines.append(
                        "\n".join(f"{start+j+1:4d} | {lines[start+j]}" for j in range(end - start))
                    )
                    if len(_candidate_lines) >= 3:
                        break
            hint = (
                "WHITESPACE-PROBLEM: Der Text existiert, aber mit anderen Zeilenenden/Einrückungen. "
                "Kopiere den Suchtext exakt aus file_read (inkl. Tabs/Spaces)."
                if _ws_match
                else "Text nicht im Dokument. Prüfe ob du die richtige Datei hast."
            )
            return {
                "error": "Suchtext nicht gefunden",
                "hint": hint,
                "whitespace_match": _ws_match,
                "occurrences": 0,
                "file_lines": len(lines),
                "file_size": len(content),
                "candidates_for_first_line": _candidate_lines if _candidate_lines else ["(keine ähnlichen Zeilen gefunden)"],
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

        # #597: Path-Validation — niemals ausserhalb des Projekt-Roots suchen
        project_root = Path(f"/projects/{project_id}").resolve()
        if path and Path(path).is_absolute():
            requested = Path(path).resolve()
            try:
                requested.relative_to(project_root)
                search_dir = requested
            except ValueError:
                return {
                    "error": f"Pfad ausserhalb des Projekts nicht erlaubt: {path}",
                    "blocked": True,
                }
        else:
            # Relativer Pfad — gegen Projekt-Root aufloesen und pruefen
            candidate = (project_root / (path or "")).resolve()
            try:
                candidate.relative_to(project_root)
                search_dir = candidate
            except ValueError:
                return {
                    "error": f"Pfad traversiert das Projekt-Root: {path}",
                    "blocked": True,
                }
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
            "Liest aus dem persistenten Gedächtnis. "
            "scope='project' (Standard): lokales Projekt-Memory. "
            "scope='global': globale Wissensdatenbank (A-MEM). "
            "Ohne filename: listet alle vorhandenen Dateien auf. "
            "Mit filename/query: gibt den Inhalt zurück."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Dateiname (z.B. 'facts.md'). Leer = alle auflisten."},
                "query": {"type": "string", "description": "Suchanfrage für scope=global (A-MEM Suche)."},
                "scope": {"type": "string", "enum": ["project", "global"], "description": "project=lokal (Standard), global=A-MEM Wissensdatenbank."},
            },
            "required": [],
        }

    async def execute(self, agent_id: str, project_id: str, filename: str = "", query: str = "", scope: str = "project", **kwargs) -> dict:
        # v2: scope=global → A-MEM Suche via MCP
        if scope == "global":
            return await self._amem_search(query or filename)

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

    @staticmethod
    async def _amem_search(query: str) -> dict:
        """A-MEM globale Suche via MCP."""
        if not query or len(query.strip()) < 2:
            return {"error": "query darf nicht leer sein", "scope": "global"}
        try:
            from .mcp_client import call_mcp_tool
            _amem_cfg = {
                "url": "http://127.0.0.1:8020/sse",
                "transport": "sse",
                "headers": {},
            }
            result = await asyncio.wait_for(
                call_mcp_tool("amem", _amem_cfg, "amem_search", {"query": query, "k": 5}),
                timeout=10.0,
            )
            return {"scope": "global", "query": query, "result": result or "(keine Treffer)"}
        except asyncio.TimeoutError:
            return {"error": "A-MEM Timeout (10s)", "scope": "global"}
        except Exception as e:
            return {"error": f"A-MEM nicht erreichbar: {e}", "scope": "global"}


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
            "scope='project' (Standard): lokales Projekt-Memory. "
            "scope='global': globale Wissensdatenbank (A-MEM). "
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
                "scope": {"type": "string", "enum": ["project", "global"], "description": "project=lokal (Standard), global=A-MEM Wissensdatenbank."},
            },
            "required": ["filename", "content"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        filename: str = "", content: str = "", mode: str = "overwrite",
        importance: float = 0.5, category: str = "fact",
        scope: str = "project",
        **kwargs,
    ) -> dict:
        import sqlite3 as _sqlite3
        # v2: scope=global → A-MEM via MCP
        if scope == "global":
            return await self._amem_add_note(content, filename)

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

    @staticmethod
    async def _amem_add_note(content: str, title: str = "") -> dict:
        """Globalen Eintrag in A-MEM speichern via MCP."""
        if not content or len(content.strip()) < 3:
            return {"error": "content darf nicht leer sein", "scope": "global"}
        try:
            from .mcp_client import call_mcp_tool
            _amem_cfg = {
                "url": "http://127.0.0.1:8020/sse",
                "transport": "sse",
                "headers": {},
            }
            args = {"content": content}
            if title:
                args["title"] = title
            result = await asyncio.wait_for(
                call_mcp_tool("amem", _amem_cfg, "amem_add_note", args),
                timeout=10.0,
            )
            return {"saved": True, "scope": "global", "result": result or "OK"}
        except asyncio.TimeoutError:
            return {"error": "A-MEM Timeout (10s)", "scope": "global"}
        except Exception as e:
            return {"error": f"A-MEM nicht erreichbar: {e}", "scope": "global"}


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
# Deferred-Tools: Session-State + ToolSearch (#620 Phase 2)
# =========================================================================
#
# Mechanik analog Claude Code:
#   1. Deferred Tools stehen im Prompt nur als Name + one_line (via
#      render_deferred_tools_block).
#   2. Model ruft ToolSearch → Scoring-Match → Antwort enthält volles
#      JSONSchema der Treffer + Side-Effect: Tools für diese Session
#      freigegeben.
#   3. Runtime-Guard in orchestrator_tools verhindert direkten Aufruf
#      nicht-freigegebener deferred Tools.
#
# Session-Key-Konvention: f"{project_id}::{agent_id}" — stabil pro Kombi.

_loaded_deferred: dict[str, set[str]] = {}


def session_key(project_id: str, agent_id: str) -> str:
    return f"{project_id}::{agent_id}"


def mark_tool_loaded(skey: str, tool_id: str) -> None:
    _loaded_deferred.setdefault(skey, set()).add(tool_id)


def is_tool_loaded(skey: str, tool_id: str) -> bool:
    # Session-Load hat Vorrang — gilt auch für MCP-Tools (nicht in registry)
    if tool_id in _loaded_deferred.get(skey, set()):
        return True
    t = registry.get(tool_id)
    if t is None:
        return False
    return t.always_loaded


def loaded_deferred_ids(skey: str) -> set[str]:
    return set(_loaded_deferred.get(skey, set()))


def clear_loaded_deferred(skey: str) -> None:
    _loaded_deferred.pop(skey, None)


def render_deferred_tools_block(
    mcp_entries: list[tuple[str, str]] | None = None,
) -> str:
    """
    Block der in den System-Prompt eingefügt wird. Listet alle deferred
    Tools mit Name + one_line. Das volle Schema lädt das Model via
    ToolSearch nach. Wenn keine deferred Tools registriert und keine MCP-
    Einträge: leerer String (kein Block im Prompt).

    #620 Phase 4: mcp_entries enthält [(prefixed_name, one_line), ...]
    für MCP-Tools des aktuellen Agents (per-Request, nicht global).
    """
    deferred = registry.deferred_tools()
    mcp_entries = mcp_entries or []

    if not deferred and not mcp_entries:
        return ""

    local_lines = [f"- **{t.id}**: {t.one_line}" for t in sorted(deferred, key=lambda x: x.id)]
    mcp_lines = [f"- **{name}**: [MCP] {desc}" for name, desc in sorted(mcp_entries)]

    sections = []
    if local_lines:
        sections.append("\n".join(local_lines))
    if mcp_lines:
        sections.append("### MCP-Tools\n" + "\n".join(mcp_lines))

    return (
        "## Available Deferred Tools (via ToolSearch)\n\n"
        "These tools exist but their JSON schemas are NOT loaded — calling "
        "them directly will fail. Use `tool_search` with `select:<id>` (or "
        "keywords) to load their schemas, then call them on the next turn.\n\n"
        + "\n\n".join(sections)
    )


# #620 Phase 4: MCP-Deferred-Entries pro Agent. Prompt-Builder + Tool-
# Dispatch nutzen denselben Cache (gekeyt auf agent_id — MCP-Tools sind
# agent-scoped, nicht project-scoped). Ein Set-Call pro Anthropic-Request.
_current_mcp_entries: dict[str, list[tuple[str, str]]] = {}


def set_current_mcp_entries(agent_id: str, entries: list[tuple[str, str]]) -> None:
    _current_mcp_entries[agent_id] = list(entries)


def get_current_mcp_entries_for_agent(agent_id: str) -> list[tuple[str, str]]:
    return list(_current_mcp_entries.get(agent_id, []))


def clear_current_mcp_entries(agent_id: str) -> None:
    _current_mcp_entries.pop(agent_id, None)


# Alias fürs ToolSearch-Nutzungsmuster (skey = project_id::agent_id)
def get_current_mcp_entries(skey: str) -> list[tuple[str, str]]:
    """Split skey → agent_id, dann lookup."""
    if "::" in skey:
        _, agent_id = skey.split("::", 1)
    else:
        agent_id = skey
    return get_current_mcp_entries_for_agent(agent_id)


def _score_deferred_match(tool: "BaseTool", query: str) -> int:
    """Einfaches Keyword-Scoring nach Claude-Code-Vorbild (Phase 2 MVP)."""
    q = query.lower().strip()
    if not q:
        return 0
    score = 0
    tid = tool.id.lower()
    tname = tool.name.lower()
    desc = tool.description.lower()
    tags = [t.lower() for t in tool.semantic_tags]

    # Exact ID / Name match: starker Boost
    if q == tid or q == tname:
        score += 20

    # Query als Ganzes in ID / Tags / Name
    if q in tid:
        score += 10
    if q in tname:
        score += 5
    for tag in tags:
        if q == tag:
            score += 6
        elif q in tag:
            score += 3

    # Term-basiert
    for term in q.split():
        if not term:
            continue
        if term in tid:
            score += 3
        if term in tname:
            score += 2
        if term in tags:
            score += 2
        if term in desc:
            score += 1

    return score


class ToolSearchTool(BaseTool):
    """
    Meta-Tool: Lädt JSON-Schemas deferred Tools on-demand.

    Syntax:
      - `select:tool_id` oder `select:a,b,c` — direkte Selektion (schnellster Pfad)
      - Sonst: Keyword-Suche über ID, Name, Tags, Beschreibung
    """

    @property
    def id(self) -> str: return "tool_search"
    @property
    def name(self) -> str: return "Tool-Suche (deferred)"
    @property
    def description(self) -> str:
        return (
            "Findet und lädt Schemas für deferred Tools. Nutze "
            "`select:<tool_id>` für direkte Selektion oder Keywords "
            "(z.B. 'web fetch' oder 'gitea issue'). Nach dem Call sind "
            "die Tools ab dem nächsten Turn aufrufbar."
        )

    @property
    def always_loaded(self) -> bool:
        return True  # ToolSearch selbst darf nie deferred werden

    @property
    def category(self) -> str:
        return "meta"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Query — entweder 'select:tool_id' / 'select:a,b,c' "
                        "für direkte Selektion, oder Keywords zur Suche."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max Anzahl Matches bei Keyword-Suche (default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        agent_id: str,
        project_id: str,
        query: str,
        max_results: int = 5,
        **kwargs,
    ) -> dict:
        skey = session_key(project_id, agent_id)
        # #620 Phase 5: jeden ToolSearch-Aufruf zählen
        try:
            from .session_metrics import metrics as _metrics
            _metrics.record_toolsearch_call(project_id)
        except Exception:
            pass
        deferred = registry.deferred_tools()
        mcp_entries = get_current_mcp_entries(skey)  # [(name, desc), ...]

        if not deferred and not mcp_entries:
            return {
                "matches": [],
                "message": "Keine deferred Tools registriert.",
            }

        q = (query or "").strip()

        # select:-Modus
        if q.lower().startswith("select:"):
            ids = [s.strip() for s in q[len("select:"):].split(",") if s.strip()]
            loaded_local = registry.resolve_many(ids)
            loaded_local = [t for t in loaded_local if not t.always_loaded]

            # MCP: direkte Namens-Zuordnung
            mcp_name_set = {name for name, _ in mcp_entries}
            loaded_mcp = [tid for tid in ids if tid in mcp_name_set]

            if not loaded_local and not loaded_mcp:
                return {
                    "matches": [],
                    "message": (
                        f"Kein deferred Tool gefunden für: {ids}. "
                        "Prüfe Schreibweise im <available-deferred-tools> Block."
                    ),
                }
            for t in loaded_local:
                mark_tool_loaded(skey, t.id)
            for name in loaded_mcp:
                mark_tool_loaded(skey, name)
            try:
                from .session_metrics import metrics as _metrics
                _metrics.record_deferred_loaded(
                    project_id, [t.id for t in loaded_local] + loaded_mcp
                )
            except Exception:
                pass
            return {
                "loaded": [t.id for t in loaded_local] + loaded_mcp,
                "schemas": [t.as_litellm_tool() for t in loaded_local],
                "mcp_loaded": loaded_mcp,
                "message": (
                    f"{len(loaded_local) + len(loaded_mcp)} Tool(s) geladen. "
                    "Ab dem nächsten Turn direkt aufrufbar."
                ),
            }

        # Keyword-Suche: lokal + MCP parallel scoren, zusammenführen
        local_scored = [(t, _score_deferred_match(t, q)) for t in deferred]
        local_scored = [(t, s) for t, s in local_scored if s > 0]

        mcp_scored: list[tuple[str, int]] = []
        q_low = q.lower()
        terms = [t for t in q_low.split() if t]
        for name, desc in mcp_entries:
            score = 0
            name_l = name.lower()
            desc_l = desc.lower()
            if q_low in name_l:
                score += 10
            if q_low in desc_l:
                score += 3
            for term in terms:
                if term in name_l:
                    score += 3
                if term in desc_l:
                    score += 1
            if score > 0:
                mcp_scored.append((name, score))

        combined: list[tuple[object, int, str]] = (
            [(t, s, "local") for t, s in local_scored]
            + [(n, s, "mcp") for n, s in mcp_scored]
        )
        combined.sort(
            key=lambda x: (-x[1], len(x[0].id) if x[2] == "local" else len(x[0])),
        )
        top = combined[: max(1, int(max_results))]

        if not top:
            return {
                "matches": [],
                "message": (
                    f"Keine Treffer für '{q}'. Versuche andere Begriffe oder "
                    "'select:<tool_id>' mit einem Namen aus "
                    "<available-deferred-tools>."
                ),
            }

        loaded_local: list[BaseTool] = []
        loaded_mcp: list[str] = []
        for item, _score, kind in top:
            if kind == "local":
                loaded_local.append(item)  # type: ignore[arg-type]
                mark_tool_loaded(skey, item.id)  # type: ignore[attr-defined]
            else:
                loaded_mcp.append(item)  # type: ignore[arg-type]
                mark_tool_loaded(skey, item)  # type: ignore[arg-type]

        try:
            from .session_metrics import metrics as _metrics
            _metrics.record_deferred_loaded(
                project_id, [t.id for t in loaded_local] + loaded_mcp
            )
        except Exception:
            pass

        return {
            "loaded": [t.id for t in loaded_local] + loaded_mcp,
            "schemas": [t.as_litellm_tool() for t in loaded_local],
            "mcp_loaded": loaded_mcp,
            "message": (
                f"{len(loaded_local) + len(loaded_mcp)} Tool(s) geladen via "
                "Keyword-Suche. Ab dem nächsten Turn direkt aufrufbar."
            ),
        }


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
registry.register(ToolSearchTool())
