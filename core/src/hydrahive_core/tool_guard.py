"""
tool_guard.py — #717: Harter ToolGuard für gefährliche Tool-Aufrufe.

Erste Sicherheitsschicht, bevor IsolationMode, Permission-Classifier oder
PreToolUse-Hooks greifen. Verhindert technisch, dass Agenten im falschen
Checkout schreiben, committen, pushen oder patchen.

Der Guard bewertet:
- `shell_exec` / `server_shell`: wenn `cwd` oder `command` auf einen stalen
  Checkout zeigt, wird das Kommando klassifiziert. Read-only-Diagnose
  (git status/log/diff, ls, rg, cat, ...) bleibt erlaubt; Writes
  (git commit/push/reset, rm/mv/cp/tee, sed -i, npm/yarn install, ...)
  werden blockiert.
- `file_write` / `file_patch` / `server_file_write`: wenn der Pfad in
  einen stalen Checkout fällt, Block.

Canonical Schreib-Checkout: /home/till/octopos
Stale Roots (Default): /projects/hydrahivedev/repo,
                       /home/octopos/hydrahive,
                       /opt/hydrahive/core

Optional Override: /etc/hydrahive/tool_guard.json. Fehlt oder defekt →
sichere Defaults, kein Abbruch, Warn-Log.

Rückgabe: `ToolGuardDecision` mit `allowed`, `code`, `message`, `hint`,
`detected_path`, `canonical_path`. Der Aufrufer baut daraus das
Tool-Result — keine Exceptions, keine Raise.
"""
from __future__ import annotations

import json
import logging
import shlex
from dataclasses import dataclass
from pathlib import Path

from .settings import settings

logger = logging.getLogger(__name__)


# ── Defaults (im Code verankert, Config kann ergänzen) ────────────────────

_DEFAULT_CANONICAL = "/home/till/octopos"
_DEFAULT_STALE_ROOTS: tuple[str, ...] = (
    "/projects/hydrahivedev/repo",
    "/home/octopos/hydrahive",
    "/opt/hydrahive/core",
)
_CONFIG_PATH = settings.tool_guard_config


# Shell-Binaries die nur lesen.
_READ_ONLY_BINS: frozenset[str] = frozenset({
    "pwd", "ls", "rg", "grep", "cat", "find", "head", "tail",
    "wc", "stat", "file", "which", "echo", "env", "printenv",
    "date", "whoami", "id", "uname", "hostname", "tree", "diff",
    "cmp", "column", "awk",  # awk ohne Redirect ist read
})

# Git-Subcommands die nur lesen.
_READ_ONLY_GIT: frozenset[str] = frozenset({
    "status", "log", "diff", "show", "remote", "branch", "reflog",
    "describe", "rev-parse", "rev-list", "blame", "ls-files",
    "ls-tree", "cat-file", "fsck", "for-each-ref", "shortlog",
    "whatchanged", "grep",
})

# Git-Subcommands die schreiben.
_WRITE_GIT: frozenset[str] = frozenset({
    "add", "commit", "push", "reset", "checkout", "clean",
    "rebase", "merge", "pull", "fetch",
    "tag", "cherry-pick", "revert", "rm", "mv", "restore",
    "apply", "am", "format-patch", "stash",
    "init", "clone", "gc", "prune", "repack",
    "update-ref", "symbolic-ref", "config",  # config kann schreiben
    "worktree", "submodule", "bisect",
})

# Klare Write-Binaries (first token).
_WRITE_BINS: frozenset[str] = frozenset({
    "rm", "mv", "cp", "tee", "mkdir", "rmdir", "touch",
    "chmod", "chown", "chgrp", "install", "dd", "ln",
    "truncate", "shred", "patch",
})

# Pattern (binary, substring): wenn beides zutrifft, gilt als write.
_WRITE_KEYWORD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("python",  ".write("),
    ("python",  ".write_text"),
    ("python",  "open("),        # grobe Heuristik — fängt open(..., 'w')
    ("python3", ".write("),
    ("python3", ".write_text"),
    ("python3", "open("),
    ("node",    "writeFile"),
    ("node",    "writeFileSync"),
    ("perl",    "-i"),
    ("perl",    "open(OUT"),
    ("npm",     "install"),
    ("npm",     "run build"),
    ("npm",     "run"),
    ("pnpm",    "install"),
    ("pnpm",    "run build"),
    ("pnpm",    "build"),
    ("yarn",    "install"),
    ("yarn",    "build"),
)


@dataclass
class ToolGuardDecision:
    """Ergebnis einer Guard-Prüfung. Im Block-Fall liefert der Aufrufer das
    als Tool-Result-Dict weiter — kein Raise."""
    allowed: bool
    code: str = ""
    message: str = ""
    hint: str | None = None
    detected_path: str | None = None
    canonical_path: str | None = None


@dataclass(frozen=True)
class _GuardConfig:
    canonical_path: str
    stale_write_roots: tuple[str, ...]
    enabled: bool = True

    @classmethod
    def defaults(cls) -> "_GuardConfig":
        return cls(
            canonical_path=_DEFAULT_CANONICAL,
            stale_write_roots=_DEFAULT_STALE_ROOTS,
        )


# ── Config-Loader ────────────────────────────────────────────────────────

_cached_config: _GuardConfig | None = None


def _load_config() -> _GuardConfig:
    """Liest `/etc/hydrahive/tool_guard.json`. Fehlt oder defekt → Defaults.
    Kein Startup-Abbruch, nur Warn-Log."""
    try:
        if not _CONFIG_PATH.exists():
            return _GuardConfig.defaults()
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "tool_guard: Config %s nicht lesbar (%s) — sichere Defaults aktiv",
            _CONFIG_PATH, exc,
        )
        return _GuardConfig.defaults()
    if not isinstance(raw, dict):
        logger.warning("tool_guard: Config ist kein JSON-Objekt — Defaults aktiv")
        return _GuardConfig.defaults()
    try:
        canonical = str(raw.get("canonical_path") or _DEFAULT_CANONICAL)
        stale_raw = raw.get("stale_write_roots")
        stale: tuple[str, ...]
        if isinstance(stale_raw, (list, tuple)) and stale_raw:
            stale = tuple(str(p) for p in stale_raw if isinstance(p, str) and p)
        else:
            stale = _DEFAULT_STALE_ROOTS
        enabled = bool(raw.get("enabled", True))
        return _GuardConfig(
            canonical_path=canonical,
            stale_write_roots=stale,
            enabled=enabled,
        )
    except Exception as exc:  # noqa: BLE001 — defensiv, nie Startup brechen
        logger.warning("tool_guard: Config-Parse-Fehler (%s) — Defaults aktiv", exc)
        return _GuardConfig.defaults()


def _get_config() -> _GuardConfig:
    global _cached_config
    if _cached_config is None:
        _cached_config = _load_config()
    return _cached_config


def reset_config_cache() -> None:
    """Testhook: zwingt `_get_config` beim nächsten Aufruf zum Neulesen."""
    global _cached_config
    _cached_config = None


# ── Helpers ──────────────────────────────────────────────────────────────

def _is_under(path: str, root: str) -> bool:
    path_norm = path.rstrip("/")
    root_norm = root.rstrip("/")
    return path_norm == root_norm or path_norm.startswith(root_norm + "/")


def _path_in_stale(
    path_str: str,
    stale_roots: tuple[str, ...],
    canonical_path: str = "",
) -> str | None:
    """Liefert den Stale-Root zurück, in dem dieser Pfad liegt — oder None.
    Relative Pfade werden nicht aufgelöst (ohne cwd-Kontext nicht sicher
    zu bestimmen) und gelten damit als `None`."""
    if not path_str:
        return None
    try:
        p = Path(path_str)
        if not p.is_absolute():
            return None
        p_norm = str(p).rstrip("/")
    except (OSError, ValueError):
        p_norm = path_str.rstrip("/")
    if canonical_path and _is_under(p_norm, canonical_path):
        return None
    for root in stale_roots:
        root_norm = root.rstrip("/")
        if canonical_path and _is_under(root_norm, canonical_path):
            continue
        if _is_under(p_norm, root_norm):
            return root_norm
    return None


def _command_contains_stale_path(
    command: str,
    stale_roots: tuple[str, ...],
    canonical_path: str = "",
) -> str | None:
    """Sucht jeden Stale-Root als Substring im rohen Command-String."""
    if not command:
        return None
    for root in stale_roots:
        root_norm = root.rstrip("/")
        if canonical_path and _is_under(root_norm, canonical_path):
            continue
        if root_norm in command:
            return root_norm
    return None


def _classify_shell_command(command: str) -> tuple[str, str]:
    """Gibt ('read'|'write', Begründung) zurück.

    Safer-Default: bei Zweifel 'write'. Ein versehentliches Blocken ist
    reversibel (Agent kann auf den kanonischen Checkout wechseln); ein
    versehentliches Durchlassen in stale Checkouts nicht.
    """
    if not command or not command.strip():
        return "read", "leer"

    # Shell-Redirects sind immer write-seitig (auch wenn das Kommando
    # selbst nur liest, wird die Output-Datei im stale Checkout geschrieben).
    padded = f" {command} "
    for op in (" > ", " >> ", " >|"):
        if op in padded:
            return "write", f"Redirect '{op.strip()}'"
    if " | tee " in padded:
        return "write", "Pipe in 'tee'"

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return "write", "Command mit unsicheren Quotes"
    if not tokens:
        return "read", "leer nach Parse"

    # env-var-Präfixe (FOO=bar ...) überspringen.
    idx = 0
    while idx < len(tokens):
        t = tokens[idx]
        if "=" in t and not t.startswith("=") and "/" not in t.split("=", 1)[0]:
            idx += 1
        else:
            break
    if idx >= len(tokens):
        return "read", "nur env-Zuweisungen"

    first = tokens[idx]
    if first == "sudo":
        idx += 1
        while idx < len(tokens) and tokens[idx].startswith("-"):
            idx += 1
        if idx >= len(tokens):
            return "write", "sudo ohne Command"
        first = tokens[idx]

    first_base = Path(first).name  # strip /usr/bin/

    # git-Subcommand
    if first_base == "git":
        j = idx + 1
        while j < len(tokens) and tokens[j].startswith("-"):
            j += 1
        if j >= len(tokens):
            return "read", "git ohne Subcommand"
        sub = tokens[j]
        if sub in _READ_ONLY_GIT:
            return "read", f"git {sub}"
        if sub in _WRITE_GIT:
            return "write", f"git {sub}"
        return "write", f"git {sub} (unbekannt)"

    # sed: -i / --in-place bedeutet write, sonst read
    if first_base == "sed":
        rest = tokens[idx + 1:]
        if any(t == "-i" or t.startswith("-i") or t == "--in-place" for t in rest):
            return "write", "sed -i"
        return "read", "sed (ohne -i)"

    if first_base in _READ_ONLY_BINS:
        return "read", first_base

    if first_base in _WRITE_BINS:
        return "write", first_base

    # Scripting-Interpreter: Pattern-Check
    if first_base in {"python", "python3", "perl", "node", "npm", "pnpm", "yarn", "ruby", "bash", "sh"}:
        rest_str = " ".join(tokens[idx + 1:])
        for bin_name, kw in _WRITE_KEYWORD_PATTERNS:
            if first_base == bin_name and kw in rest_str:
                return "write", f"{first_base} … {kw}"
        # Unbekanntes Script → safer: write
        return "write", f"Script-Aufruf '{first_base}' — potenziell schreibend"

    return "write", f"unbekannter Command '{first_base}'"


def _extract_paths_from_tool_input(tool_name: str, tool_input: dict) -> list[str]:
    """Sammelt Kandidaten-Pfade aus dem Tool-Input (path-artige Keys)."""
    paths: list[str] = []
    if not isinstance(tool_input, dict):
        return paths
    for key in ("path", "cwd", "file", "target_path", "source", "destination", "dst", "dest"):
        val = tool_input.get(key)
        if isinstance(val, str) and val:
            paths.append(val)
    return paths


# ── Hauptfunktion ────────────────────────────────────────────────────────

# Tools die per Definition nur lesen — werden sofort durchgewunken.
_ALWAYS_READ_TOOLS: frozenset[str] = frozenset({
    "file_read", "file_search", "read_memory", "web_search",
    "server_file_read", "ask_agent", "list_skills",
})

# Tools die vom Guard aktiv überwacht werden — shell-like oder write.
_SHELL_TOOLS: frozenset[str] = frozenset({
    "shell_exec", "server_shell", "wks_shell_exec", "project_shell",
})
_FILE_WRITE_TOOLS: frozenset[str] = frozenset({
    "file_write", "file_patch", "server_file_write",
})


def check_tool_guard(
    tool_name: str,
    tool_input: dict,
    project_id: str | None = None,  # noqa: ARG001 — für spätere Project-Policy vorgesehen
) -> ToolGuardDecision:
    """Erste Guard-Stufe. Liefert `allowed=False` nur bei klaren
    Schreibversuchen in stale HydraHive-Checkouts; sonst `allowed=True`.
    Niemals Raise.
    """
    config = _get_config()
    if not config.enabled:
        return ToolGuardDecision(
            allowed=True,
            code="guard_disabled",
            canonical_path=config.canonical_path,
        )

    tool_name = tool_name or ""
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool_name in _ALWAYS_READ_TOOLS:
        return ToolGuardDecision(allowed=True, canonical_path=config.canonical_path)

    stale = config.stale_write_roots

    if tool_name in _SHELL_TOOLS:
        command = str(tool_input.get("command") or "")
        cwd = str(tool_input.get("cwd") or "")

        cwd_hit = _path_in_stale(cwd, stale, config.canonical_path) if cwd else None
        cmd_hit = _command_contains_stale_path(command, stale, config.canonical_path)
        stale_hit = cwd_hit or cmd_hit
        if not stale_hit:
            return ToolGuardDecision(allowed=True, canonical_path=config.canonical_path)

        klass, reason = _classify_shell_command(command)
        if klass == "read":
            return ToolGuardDecision(
                allowed=True,
                code="read_in_stale",
                message=f"Diagnose in stalem Checkout erlaubt ({reason})",
                detected_path=stale_hit,
                canonical_path=config.canonical_path,
            )
        return ToolGuardDecision(
            allowed=False,
            code="write_in_stale_checkout",
            message=(
                f"Falscher Checkout: {stale_hit}. "
                f"Schreibaktionen für HydraHive bitte in {config.canonical_path} ausführen."
            ),
            hint=f"Blockierte Aktion: {reason}. Schreib-/Commit-/Patch-Aktionen nur unter {config.canonical_path}.",
            detected_path=stale_hit,
            canonical_path=config.canonical_path,
        )

    if tool_name in _FILE_WRITE_TOOLS:
        for p in _extract_paths_from_tool_input(tool_name, tool_input):
            stale_hit = _path_in_stale(p, stale, config.canonical_path)
            if stale_hit:
                return ToolGuardDecision(
                    allowed=False,
                    code="write_in_stale_checkout",
                    message=(
                        f"Falscher Checkout: {stale_hit}. "
                        f"Schreibaktionen für HydraHive bitte in {config.canonical_path} ausführen."
                    ),
                    hint=f"{tool_name}({p}) würde in stalen Checkout schreiben.",
                    detected_path=stale_hit,
                    canonical_path=config.canonical_path,
                )
        return ToolGuardDecision(allowed=True, canonical_path=config.canonical_path)

    # Alle anderen Tools: keine Zuständigkeit, durchlassen.
    return ToolGuardDecision(allowed=True, canonical_path=config.canonical_path)
