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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from .settings import settings

# Discord-Client-Registry — wird von router_user_integrations + butler_executor genutzt.
# Dict {personal_agent_id: AgentDiscordClient}
_discord_clients: dict = {}

logger = logging.getLogger(__name__)

PROJECTS_ROOT = settings.projects_dir
AGENTS_ROOT   = settings.agents_dir


# =========================================================================
# Workspace-SSOT (#635)
# =========================================================================
# `workspace_root(project_id)` ist die EINZIGE Quelle für Workspace-Pfade.
# `file_*`, `file_patch`, `shell_exec` und `git_*` operieren alle auf
# diesem identischen Pfad — kein Sub-Tree, keine "Familie von Roots",
# kein Parent-Trick. Ein Projekt hat genau einen effektiven Working Tree.
# #662/#664: Per-Request Workspace-Runtime-Context via ContextVar.
# Wird vom Router für internal-auth Sub-Agent-Aufrufe gesetzt. Async-Task-
# lokal, damit parallele Requests sich nicht beeinflussen. Set vor
# handle_message(), reset in finally.
#
# Trägt:
#   - path:              Worktree-Pfad → workspace_root() liefert diesen.
#   - worktree_id:       Meta-Handle (Observability/Debugging).
#   - isolation_mode:    read_only | patch_only | full_worktree | None.
#                        None = kein Enforcement (Backward-Compat für
#                        alte Path-only-Aufrufer). Sonst konsultiert der
#                        Tool-Dispatcher allow_tool() am Funktionseingang.
#   - parent_project_id: Boss-Projekt (Observability).
#
# Ohne Context fällt workspace_root() auf den Default zurück — bestehende
# Tests und normale Agents bleiben unverändert.


@dataclass(frozen=True)
class WorkspaceRuntimeContext:
    path:              Path
    worktree_id:       str | None = None
    isolation_mode:    str | None = None
    parent_project_id: str | None = None


_workspace_override_var: contextvars.ContextVar[WorkspaceRuntimeContext | None] = (
    contextvars.ContextVar("hydrahive_workspace_override", default=None)
)


def set_workspace_override(
    ctx_or_path: Union[Path, "WorkspaceRuntimeContext"],
) -> contextvars.Token:
    """Setzt den Workspace-Runtime-Context für den aktuellen async-Task.

    Akzeptiert `Path` (Backward-Compat für #662/#663-Aufrufer — wird intern
    zu WorkspaceRuntimeContext(path=..., isolation_mode=None) gewrappt, d.h.
    kein Enforcement) oder `WorkspaceRuntimeContext` mit vollem Kontext
    inkl. `isolation_mode` für #664-Enforcement.

    Rückgabe: Token für späteren reset_workspace_override().
    """
    if isinstance(ctx_or_path, WorkspaceRuntimeContext):
        ctx = ctx_or_path
    elif isinstance(ctx_or_path, Path):
        ctx = WorkspaceRuntimeContext(path=ctx_or_path)
    else:
        raise TypeError(
            f"set_workspace_override expects Path or WorkspaceRuntimeContext, "
            f"got {type(ctx_or_path).__name__}"
        )
    return _workspace_override_var.set(ctx)


def reset_workspace_override(token: contextvars.Token) -> None:
    _workspace_override_var.reset(token)


def current_workspace_context() -> "WorkspaceRuntimeContext | None":
    """Liefert den aktuellen Workspace-Runtime-Context des async-Tasks.

    None, wenn kein Sub-Agent-Worktree-Kontext aktiv ist (normale Agents).
    """
    return _workspace_override_var.get()


def _current_workspace_override() -> Path | None:
    """Backward-compat getter — liefert path des aktuellen Context oder None."""
    ctx = _workspace_override_var.get()
    return ctx.path if ctx is not None else None


def workspace_root(project_id: str) -> Path:
    """Autoritativer Workspace-Pfad eines Projekts.

    Identisch genutzt von file-, shell- und git-Tools. Reine Resolver-
    Funktion ohne Side-Effects — kein mkdir, kein I/O. Verbraucher legen
    Verzeichnisse selbst an.

    #662: Wenn im aktuellen async-Context ein Workspace-Override gesetzt
    ist (via set_workspace_override, vom Router für Sub-Agent-Worktrees),
    wird dieser Pfad zurückgegeben. Die Invariante "workspace_root ist die
    einzige Quelle" bleibt — nur ihr Ergebnis ist per-Task override-bar.
    """
    ctx = _workspace_override_var.get()
    if ctx is not None:
        return ctx.path
    return (PROJECTS_ROOT / project_id).resolve()


def _resolve_sandbox_scope(
    project_id: str, cwd: str | Path,
) -> tuple[Path, list[str]]:
    """Pure helper: berechnet (effective_cwd, bwrap_bind_args) für die Shell-Sandbox.

    Extrahiert aus ShellExecTool.execute (#635, B2 aus #639), damit der Shell-
    Sandbox-Scope ohne Prozessstart testbar wird.

    Verwendet `workspace_root(project_id)` als Working-Tree-Mount — derselbe
    Pfad den `file_*` und `git_*` nutzen.

    Pure: nur Path-Operationen + .exists()-Checks, kein subprocess, kein I/O.
    """
    project_dir = workspace_root(project_id) if project_id else None
    cwd_path = Path(cwd).resolve() if cwd else Path("/tmp")

    # cwd in Scope? — entweder unter /tmp oder unter project_dir
    cwd_in_scope = (
        str(cwd_path).startswith("/tmp")
        or (project_dir is not None
            and (cwd_path == project_dir or _path_within(cwd_path, project_dir)))
    )
    if not cwd_in_scope:
        cwd_path = project_dir if (project_dir is not None and project_dir.exists()) else Path("/tmp")

    # Minimale Sandbox — nur System-Binaries/Libs + Projekt + /tmp
    # NICHT `/` komplett mounten (#593: verhindert Host-Leak)
    bind_args: list[str] = [
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
    # Workspace-Root rw binden — DAS ist der gemeinsame Tree für file_*/git_*/shell
    if project_dir is not None and project_dir.exists():
        bind_args += ["--bind", str(project_dir), str(project_dir)]
    # cwd extra rw binden, falls außerhalb /tmp UND außerhalb project_dir
    cwd_str = str(cwd_path)
    extra_cwd = (
        cwd_str != "/tmp"
        and cwd_path.exists()
        and (project_dir is None or cwd_path != project_dir)
        and not (project_dir is not None and _path_within(cwd_path, project_dir))
    )
    if extra_cwd:
        bind_args += ["--bind", cwd_str, cwd_str]

    return cwd_path, bind_args


def _path_within(child: Path, parent: Path) -> bool:
    """Hilfsfunktion: True wenn child ein Pfad unter parent ist (str-prefix sicher)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


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


def assert_path_within_project(
    path: str | Path,
    project_id: str,
) -> Path:
    """#638: Pfadsicherheit auf Projekt-Workspace begrenzt.

    Permission-Parameter (`agent_permissions=`) und der ehemalige
    `filesystem.read_all`-Branch wurden entfernt — sie waren toter Code,
    weil `effective_permissions()` immer leer lieferte.
    """
    import os

    project_root = workspace_root(project_id)

    target = Path(path)
    if not target.is_absolute():
        target = project_root / target

    normalized = Path(os.path.normpath(target))
    try:
        normalized = normalized.resolve()
    except OSError:
        pass

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
            # #635: Pure helper für Scope-Auflösung (ersetzt inline-Bau).
            # Mountet workspace_root(project_id) — derselbe Tree wie file_*/git_*.
            _resolved_cwd, _bind_args = _resolve_sandbox_scope(project_id, safe_cwd)
            safe_cwd = str(_resolved_cwd)
            _quoted = _shlex.quote(command)
            _bind_cmd = " ".join(_shlex.quote(a) for a in _bind_args)
            exec_command = (
                f"bwrap {_bind_cmd} "
                f"--die-with-parent "
                f"-- bash -c {_quoted}"
            )
            logger.info("shell_exec [%s] (SANDBOX/bwrap mode=%s scope=%s): %s",
                        agent_id, _mode or "safe",
                        str(workspace_root(project_id)) if project_id else "/tmp",
                        command[:120])
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

            # #635: Auto-Push nach git commit entfernt.
            # Hing am Shell-Tool, kannte das Workspace-Modell nicht, war ein
            # versteckter Fremdeffekt. Wer pushen will, ruft `git_push`.
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
        try:
            safe_path = assert_path_within_project(path, project_id)
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
        # #635: gemeinsamer Workspace via assert_path_within_project —
        # kein /projects/<id>/files-Sonderpfad mehr.
        try:
            file_path = assert_path_within_project(path, project_id)
        except PathSafetyError as e:
            return {"error": str(e), "allowed": False}

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
                "isolation_mode": {
                    "type": "string",
                    "enum": ["read_only", "patch_only", "full_worktree"],
                    "description": (
                        "Sub-Agent-Isolation (#652). Default: full_worktree. "
                        "Nur wirksam bei settings.worktree_isolation=True + Git-Repo. "
                        "read_only/patch_only blockieren Writes via Tool-Dispatch-Enforcement (#664)."
                    ),
                },
                "write_scope": {
                    "type": "object",
                    "description": (
                        "Write-Scope (#653) für Sub-Agent-Worktree. "
                        "Nur wirksam bei Worktree-Isolation. V1 informativ (Scope-Report), "
                        "keine direkte Tool-Blockade."
                    ),
                    "properties": {
                        "allow":       {"type": "array", "items": {"type": "string"}},
                        "deny":        {"type": "array", "items": {"type": "string"}},
                        "description": {"type": "string"},
                    },
                },
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

        # #669: _execute_tool poppt project_id immer aus args (Security), legt
        # für ask_agent den Wert aber als _requested_project_id zurück.
        explicit_project_id = (
            kwargs.get("_requested_project_id") or kwargs.get("project_id") or ""
        )
        if explicit_project_id.strip():
            session_id = explicit_project_id.strip()
        elif project_id and not project_id.startswith("personal_"):
            session_id = project_id
        else:
            session_id = f"{target}_{_uuid.uuid4().hex[:8]}"

        # #662/#667: Optionale Worktree-Isolation. Nur aktiv wenn Feature-Flag
        # gesetzt UND Parent-Workspace ein Git-Repo ist. Payload bekommt ein
        # workspace_override-Feld, das der Router ausschließlich für
        # internal-auth Aufrufe akzeptiert.
        # #667: isolation_mode + write_scope sind optionale Caller-Args. Werden
        # nur durchgereicht, wenn explizit gesetzt — sonst nutzt create_worktree
        # seinen eigenen Default (full_worktree / None).
        _wt_meta = None
        _wt_skipped_reason: str | None = None
        _wt_setup_error: str | None = None
        _extra_body: dict = {}
        _raw_iso = kwargs.get("isolation_mode")
        _raw_scope = kwargs.get("write_scope")
        _explicit_iso_args = _raw_iso is not None or _raw_scope is not None

        # #667: Validierung der Caller-Args (vor Git-Check, vor create_worktree,
        # vor HTTP). Bei settings.worktree_isolation=False werden Args stumm
        # ignoriert (Feature global aus — Legacy-Pfad bleibt stabil).
        _validated_iso = None
        _validated_scope_raw = None
        if getattr(settings, "worktree_isolation", False):
            from .subagent_isolation import (
                IsolationError as _IsoErr,
                validate_isolation_mode as _validate_iso,
            )
            from .subagent_write_scope import (
                WriteScopeError as _WSErr,
                validate_write_scope as _validate_scope,
            )
            if _raw_iso is not None:
                try:
                    _validated_iso = _validate_iso(_raw_iso).value
                except _IsoErr as _iso_err:
                    return {
                        "error": f"ask_agent: ungültiges isolation_mode: {_iso_err}",
                        "agent_id": target,
                        "worktree_skipped": "invalid_args",
                        "field": "isolation_mode",
                        "reason": str(_iso_err),
                    }
            if _raw_scope is not None:
                try:
                    _validate_scope(_raw_scope)  # validiert, wir geben das dict weiter
                    _validated_scope_raw = _raw_scope
                except _WSErr as _ws_err:
                    return {
                        "error": f"ask_agent: ungültiger write_scope: {_ws_err}",
                        "agent_id": target,
                        "worktree_skipped": "invalid_args",
                        "field": "write_scope",
                        "reason": str(_ws_err),
                    }

        if getattr(settings, "worktree_isolation", False):
            from .subagent_worktrees import (
                WorktreeError,
                create_worktree,
                is_git_repo,
                release_worktree,
            )

            parent_workspace = workspace_root(project_id) if project_id else None
            if parent_workspace is None or not is_git_repo(parent_workspace):
                if _explicit_iso_args:
                    # #667: Caller hat Isolation explizit angefordert, aber
                    # Projekt ist kein Git-Repo → fail-closed, kein HTTP.
                    return {
                        "error": (
                            "worktree isolation requested but project is not a git repo"
                        ),
                        "agent_id": target,
                        "worktree_skipped": "non_git_repo_but_isolation_requested",
                    }
                _wt_skipped_reason = "non_git_repo"
            else:
                # task_id: session_id sanitized auf Identifier-Whitelist.
                _raw_task = session_id or f"{target}_{_uuid.uuid4().hex[:8]}"
                _task_id = _re_shell.sub(r"[^A-Za-z0-9_-]", "_", _raw_task)[:64] or "task"
                _sub_id = _re_shell.sub(r"[^A-Za-z0-9_-]", "_", target)[:64] or "sub"
                _parent_pid = _re_shell.sub(r"[^A-Za-z0-9_-]", "_", project_id or "p")[:64] or "p"
                _parent_aid = _re_shell.sub(r"[^A-Za-z0-9_-]", "_", agent_id or "a")[:64] or "a"
                try:
                    # Nur explizit gesetzte Args durchreichen; sonst nutzt
                    # create_worktree seinen eigenen Default.
                    _cw_kwargs: dict = {}
                    if _validated_iso is not None:
                        _cw_kwargs["isolation_mode"] = _validated_iso
                    if _validated_scope_raw is not None:
                        _cw_kwargs["write_scope"] = _validated_scope_raw
                    _wt_meta = create_worktree(
                        base_repo=str(parent_workspace),
                        parent_project_id=_parent_pid,
                        parent_agent_id=_parent_aid,
                        sub_agent_id=_sub_id,
                        task_id=_task_id,
                        **_cw_kwargs,
                    )
                    _extra_body["workspace_override"] = {
                        "path": _wt_meta.worktree_path,
                        "worktree_id": _wt_meta.worktree_id,
                        "parent_project_id": _wt_meta.parent_project_id,
                    }
                except WorktreeError as _wt_err:
                    _wt_setup_error = str(_wt_err)
                    logger.warning("ask_agent worktree setup failed: %s", _wt_err)

        if _wt_setup_error is not None:
            # fail-closed: KEIN HTTP-Call wenn Isolation gewünscht aber nicht möglich.
            return {
                "error": f"worktree setup failed: {_wt_setup_error}",
                "worktree_skipped": "error",
                "agent_id": target,
            }

        _post_body = {"content": content, "sender": agent_id, "project_id": session_id}
        _post_body.update(_extra_body)

        async def _call_and_parse() -> dict:
            try:
                async def _do_post():
                    async with _aio.ClientSession() as _s:
                        async with _s.post(
                            f"http://127.0.0.1:8765/agents/{target}/message",
                            json=_post_body,
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
                _r: dict = {"agent_id": target, "response": response, "success": True}
                if worker_errors:
                    _r["worker_reported_errors"] = True
                    _r["hint"] = "Der Worker hat Fehler gemeldet (siehe response)."
                    _notify(project_id, "task_failed", f"Fehler: {target}", response[:120], link=f"/chat/{project_id}")
                else:
                    _notify(project_id, "task_done", f"Fertig: {target}", response[:120], link=f"/chat/{project_id}")
                return _r
            except Exception as e:
                _notify(project_id, "task_failed", f"Kommunikationsfehler: {target}", str(e)[:120], link=f"/chat/{project_id}")
                return {"error": f"Fehler bei Kommunikation mit '{target}': {e}", "agent_id": target}

        # Echtes try/finally: release_worktree muss auch laufen, wenn
        # _call_and_parse() eine unerwartete Exception wirft.
        result: Any = None
        try:
            result = await _call_and_parse()
        finally:
            if _wt_meta is not None:
                from .subagent_worktrees import release_worktree
                from .subagent_write_scope import WriteScope, evaluate_worktree_scope

                _scope_dict: dict
                try:
                    _report = evaluate_worktree_scope(_wt_meta.worktree_path, WriteScope())
                    _scope_dict = {
                        "ok":                 _report.ok,
                        "violations_count":   _report.violations_count,
                        "allowed_files":      list(_report.allowed_files),
                        "denied_files":       list(_report.denied_files),
                        "out_of_scope_files": list(_report.out_of_scope_files),
                    }
                except Exception as _serr:
                    logger.warning("scope eval failed: %s", _serr)
                    _scope_dict = {"error": f"scope eval failed: {_serr}"}

                try:
                    release_worktree(_wt_meta.worktree_id)
                except Exception as _rerr:
                    logger.warning("release failed: %s", _rerr)

                if isinstance(result, dict):
                    result["worktree_meta"] = {
                        "worktree_id":    _wt_meta.worktree_id,
                        "worktree_path":  _wt_meta.worktree_path,
                        "isolation_mode": _wt_meta.isolation_mode,
                        "write_scope":    _wt_meta.write_scope,
                        "scope_report":   _scope_dict,
                    }

                # #665: Patch-Artefakt-Extraktion nur für isolation_mode="patch_only".
                # Sub-Agent-Response wird auf fenced ```diff/```patch Block geprüft,
                # validiert, (auch bei invalid) für Audit persistiert. KEIN Auto-Apply.
                if (
                    isinstance(result, dict)
                    and _wt_meta.isolation_mode == "patch_only"
                ):
                    try:
                        from dataclasses import asdict as _asdict
                        from .subagent_patch_artifacts import (
                            build_patch_artifact_report as _build_patch_report,
                        )
                        from .subagent_write_scope import (
                            WriteScope as _WS, validate_write_scope as _validate_ws,
                        )
                        _ws_obj: _WS | None
                        if _wt_meta.write_scope:
                            try:
                                _ws_obj = _validate_ws(_wt_meta.write_scope)
                            except Exception as _ws_err:
                                logger.warning("patch_artifact: ws validate failed: %s", _ws_err)
                                _ws_obj = None
                        else:
                            _ws_obj = None
                        _response_text = result.get("response") or ""
                        _patch_report = _build_patch_report(
                            _response_text, _ws_obj, _wt_meta.worktree_id,
                        )
                        result["patch_artifact"] = _asdict(_patch_report)
                    except Exception as _patch_err:
                        logger.warning("patch_artifact extraction failed: %s", _patch_err)

        if _wt_skipped_reason is not None and isinstance(result, dict):
            result["worktree_skipped"] = _wt_skipped_reason

        return result


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
# #584-C — Project-Target-Tools (server_shell, server_file_*, wks_shell_exec)
# =========================================================================

class ServerShellTool(BaseTool):
    """SSH-Befehl auf zugewiesenem Root-/Remote-Server ausführen."""

    @property
    def id(self) -> str: return "server_shell"
    @property
    def is_destructive(self) -> bool: return True
    @property
    def name(self) -> str: return "Server-Shell"
    @property
    def description(self) -> str:
        return (
            "Führt einen Shell-Befehl auf einem dem Projekt zugewiesenen "
            "Root-/Remote-Server via SSH aus (stdout/stderr/exit_code). "
            "`server_id` muss in den Projekt-Zielsystemen stehen."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "server_id": {"type": "string", "description": "ID des zugewiesenen Servers"},
                "command":   {"type": "string", "description": "Bash-Befehl"},
                "timeout":   {"type": "integer", "description": "SSH-Timeout in s (Default 60, max 120)"},
            },
            "required": ["server_id", "command"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        server_id: str = "", command: str = "", timeout: int = 60,
        **kwargs,
    ) -> dict:
        from .target_resolution import (
            resolve_server_target, run_ssh_command, TargetAccessError,
        )
        try:
            target = resolve_server_target(agent_id, server_id, project_id=project_id)
        except TargetAccessError as e:
            return {"error": str(e), "exit_code": -1, "server_id": server_id}

        timeout = min(max(int(timeout or 60), 1), 120)
        result = await run_ssh_command(
            target.ip, target.ssh_user, target.ssh_port,
            target.ssh_key_path, command,
            target_type="server", target_id=target.server_id,
            timeout=timeout,
        )
        result["server_id"] = server_id
        result["command"] = command
        return result


class ServerFileReadTool(BaseTool):
    """Datei von zugewiesenem Server lesen (via dd, POSIX-minimal)."""

    @property
    def id(self) -> str: return "server_file_read"
    @property
    def is_read_only(self) -> bool: return True
    @property
    def name(self) -> str: return "Server-Datei lesen"
    @property
    def description(self) -> str:
        return (
            "Liest eine Datei von einem zugewiesenen Root-/Remote-Server. "
            "Maximal max_bytes (Default 200000, max 1000000). "
            "Für Textdateien optimiert (UTF-8)."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "server_id": {"type": "string"},
                "path":      {"type": "string", "description": "Absoluter Pfad auf dem Ziel-Server"},
                "max_bytes": {"type": "integer", "description": "Default 200000, max 1000000"},
            },
            "required": ["server_id", "path"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        server_id: str = "", path: str = "", max_bytes: int = 200_000,
        **kwargs,
    ) -> dict:
        import shlex as _shlex
        from .target_resolution import (
            resolve_server_target, run_ssh_command, TargetAccessError,
        )
        try:
            target = resolve_server_target(agent_id, server_id, project_id=project_id)
        except TargetAccessError as e:
            return {"error": str(e), "exit_code": -1, "server_id": server_id, "path": path}

        if not path:
            return {"error": "path fehlt.", "exit_code": -1}
        max_bytes = min(max(int(max_bytes or 200_000), 1), 1_000_000)
        # dd + head ist POSIX-minimal, kein python3 nötig. count=max_bytes+1 um
        # truncation-Flag setzen zu können.
        read_limit = max_bytes + 1
        remote_cmd = (
            f"dd if={_shlex.quote(path)} bs=1 count={read_limit} "
            f"2>/dev/null | head -c {read_limit}"
        )
        # #670: max_output=None — stdout wird NICHT vom Runner gekappt.
        # Remote begrenzt bereits per `head -c read_limit` auf max_bytes+1,
        # also bleibt der Speicher-Footprint beschränkt.
        result = await run_ssh_command(
            target.ip, target.ssh_user, target.ssh_port,
            target.ssh_key_path, remote_cmd,
            target_type="server", target_id=target.server_id,
            timeout=60,
            max_output=None,
        )
        if result.get("exit_code") != 0:
            out = {
                "error": result.get("stderr") or result.get("error") or "read failed",
                "exit_code": result.get("exit_code", -1),
                "server_id": server_id, "path": path,
            }
            for k in ("host_key_unverified", "host_key_changed", "host_key_mode"):
                if k in result:
                    out[k] = result[k]
            return out
        content = result.get("stdout", "")
        content_bytes = len(content.encode("utf-8", errors="replace"))
        truncated = content_bytes > max_bytes
        if truncated:
            # Naiv-sichere Kürzung auf Zeichenebene. Byte-Genauigkeit wäre nur
            # bei harten Byte-Grenzen relevant; hier reicht Char-Kürzung, weil
            # max_bytes primär als Kontext-Budget-Schutz dient.
            content = content[:max_bytes]
        return {
            "server_id": server_id,
            "path": path,
            "content": content,
            "truncated": truncated,
            "bytes": len(content.encode("utf-8", errors="replace")),
        }


class ServerFileWriteTool(BaseTool):
    """Datei auf zugewiesenem Server schreiben (atomar via base64)."""

    @property
    def id(self) -> str: return "server_file_write"
    @property
    def is_destructive(self) -> bool: return True
    @property
    def name(self) -> str: return "Server-Datei schreiben"
    @property
    def description(self) -> str:
        return (
            "Schreibt eine Datei atomar auf einen zugewiesenen Root-/Remote-Server. "
            "Content wird base64-codiert übertragen (binary-safe). Maximal 1 MiB. "
            "Optional `mode` (oktal, z.B. \"0644\")."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "server_id": {"type": "string"},
                "path":      {"type": "string"},
                "content":   {"type": "string"},
                "mode":      {"type": "string", "description": "oktal z.B. '0644' (optional)"},
            },
            "required": ["server_id", "path", "content"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        server_id: str = "", path: str = "", content: str = "", mode: str = "",
        **kwargs,
    ) -> dict:
        import base64 as _b64
        import shlex as _shlex
        import re as _re
        from .target_resolution import (
            resolve_server_target, run_ssh_command, TargetAccessError,
        )
        try:
            target = resolve_server_target(agent_id, server_id, project_id=project_id)
        except TargetAccessError as e:
            return {"error": str(e), "exit_code": -1, "server_id": server_id, "path": path}

        if not path:
            return {"error": "path fehlt.", "exit_code": -1}
        MAX_CONTENT = 1_048_576  # 1 MiB
        payload = content or ""
        payload_bytes = payload.encode("utf-8")
        if len(payload_bytes) > MAX_CONTENT:
            return {
                "error": f"Content > {MAX_CONTENT} Bytes nicht erlaubt (aktuell {len(payload_bytes)}).",
                "exit_code": -1,
            }
        b64 = _b64.b64encode(payload_bytes).decode("ascii")

        mode_part = ""
        if mode:
            if not _re.match(r"^[0-7]{3,4}$", str(mode)):
                return {"error": f"Ungültiger mode '{mode}' (erwartet: oktal, z.B. 0644).", "exit_code": -1}
            mode_part = f" && chmod {mode} {_shlex.quote(path)}"

        # Atomic via tmp-Datei im selben Verzeichnis + mv
        remote_cmd = (
            f"tmp=$(mktemp {_shlex.quote(path)}.XXXXXX) && "
            f"printf %s {_shlex.quote(b64)} | base64 -d > \"$tmp\" && "
            f"mv \"$tmp\" {_shlex.quote(path)}"
            f"{mode_part}"
        )
        result = await run_ssh_command(
            target.ip, target.ssh_user, target.ssh_port,
            target.ssh_key_path, remote_cmd,
            target_type="server", target_id=target.server_id,
            timeout=60,
        )
        if result.get("exit_code") != 0:
            out = {
                "error": result.get("stderr") or result.get("error") or "write failed",
                "exit_code": result.get("exit_code", -1),
                "server_id": server_id, "path": path,
            }
            for k in ("host_key_unverified", "host_key_changed", "host_key_mode"):
                if k in result:
                    out[k] = result[k]
            return out
        return {
            "server_id": server_id,
            "path": path,
            "bytes": len(payload_bytes),
            "mode": mode or None,
        }


class WksShellExecTool(BaseTool):
    """SSH-Befehl auf einer dem Projekt zugewiesenen Workstation ausführen."""

    @property
    def id(self) -> str: return "wks_shell_exec"
    @property
    def is_destructive(self) -> bool: return True
    @property
    def name(self) -> str: return "WKS-Shell"
    @property
    def description(self) -> str:
        return (
            "Führt einen Shell-Befehl auf der Workstation (WKS) eines Users aus — "
            "NICHT auf einem Server. `username` ist optional wenn genau eine WKS "
            "dem Projekt zugewiesen ist; bei mehreren ist er Pflicht."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "WKS-Owner (optional bei genau 1 zugewiesener WKS)"},
                "command":  {"type": "string", "description": "Bash-Befehl"},
                "timeout":  {"type": "integer", "description": "SSH-Timeout in s (Default 60, max 120)"},
            },
            "required": ["command"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        command: str = "", username: str = "", timeout: int = 60,
        **kwargs,
    ) -> dict:
        from .target_resolution import (
            resolve_wks_target, run_ssh_command, TargetAccessError,
        )
        try:
            target = resolve_wks_target(username or None, project_id=project_id)
        except TargetAccessError as e:
            return {"error": str(e), "exit_code": -1, "username": username}

        timeout = min(max(int(timeout or 60), 1), 120)
        result = await run_ssh_command(
            target.ip, target.ssh_user, target.ssh_port,
            target.ssh_key_path, command,
            target_type="wks", target_id=target.username,
            timeout=timeout,
        )
        result["username"] = target.username
        result["command"] = command
        return result


# =========================================================================
# Register all Core Tools
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
# #584-C: Projekt-Target-Tools
registry.register(ServerShellTool())
registry.register(ServerFileReadTool())
registry.register(ServerFileWriteTool())
registry.register(WksShellExecTool())
