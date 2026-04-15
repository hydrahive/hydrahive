"""
subagent_isolation.py — Isolation-Modes und Policy-Matrix für Sub-Agenten (#652)

V1 liefert NUR deklarative Policy + Validierung. KEINE Runtime-Enforcement,
KEINE Integration in ask_agent / Tool-Dispatch. Anwendung erfolgt in Folge-
Issues (#662 Enforcement, Patch-Artefakt-Flow, optionaler read-only-Shell).

Modes
-----
read_only       — Sub-Agent darf lesen/analysieren. Kein Write, kein Shell,
                  kein git-mutate, kein Push.
patch_only      — Wie read_only bzgl. Tool-Erlaubnis. Semantischer
                  Unterschied: Sub-Agent darf als Ergebnis einen
                  unified-diff-Patch liefern (Textoutput, kein Write-Tool).
                  Apply-Entscheidung liegt beim Parent-Agent.
full_worktree   — Sub-Agent darf im eigenen Worktree schreiben/committen
                  etc. Haupt-Workspace bleibt unverändert. KEIN git push
                  (Auto-Merge/Remote-Sperre). Rückführung manuell oder über
                  Folge-Issue.

Tool-Kategorien
---------------
READ        — Lesend, kein Seiteneffekt im Workspace.
WRITE       — Schreibt im lokalen Workspace (Dateien, Memory).
SHELL       — Shell-Ausführung. V1 pauschal. Eine differenzierende
              read-only-Shell-Heuristik ist Non-Scope und folgt separat.
GIT_MUTATE  — Lokale Git-Mutation (commit/branch/pull/reset/clone).
GIT_PUSH    — Remote-Push (git_push). Separat, da auch in full_worktree
              blockiert (Auto-Merge-Sperre).
NETWORK     — Netzwerk-Calls OHNE lokalen Schreibeffekt (V1-Klassifikation).
              Für V1 umfasst NETWORK konkret: ask_agent. ask_agent gilt
              isolations-technisch als "lokal nicht-schreibend" — der
              aufgerufene Sub-Agent läuft selbst in eigenem Kontext, evtl.
              mit eigener Isolation. TRANSITIVE Effekte des Ziel-Agenten
              (Schreibzugriffe dort, Netz-/Remote-Operationen) werden erst
              bei Runtime-Integration (#662/#653) adressiert. In V1 heißt
              NETWORK=allow also NICHT, dass das Ziel-Agent-Verhalten
              ebenfalls in dieser Sandbox gefangen wäre.
META        — Tool-Meta (tool_search, get_final_message).
UNKNOWN     — Nicht klassifiziertes Tool. Fail-closed in read_only/
              patch_only, fail-open in full_worktree (dort ist das
              Isolations-Modell selbst bereits full write).

Policy-Matrix (V1)
------------------
              read_only  patch_only  full_worktree
READ          allow      allow       allow
WRITE         block      block       allow
SHELL         block      block       allow
GIT_MUTATE    block      block       allow
GIT_PUSH      block      block       block
NETWORK       allow      allow       allow
META          allow      allow       allow
UNKNOWN       block      block       allow
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class IsolationMode(str, Enum):
    READ_ONLY = "read_only"
    PATCH_ONLY = "patch_only"
    FULL_WORKTREE = "full_worktree"


ALLOWED_ISOLATION_MODES: frozenset[str] = frozenset(m.value for m in IsolationMode)
DEFAULT_ISOLATION_MODE: IsolationMode = IsolationMode.FULL_WORKTREE


class ToolCategory(str, Enum):
    READ = "read"
    WRITE = "write"
    SHELL = "shell"
    GIT_MUTATE = "git_mutate"
    GIT_PUSH = "git_push"
    NETWORK = "network"
    META = "meta"
    UNKNOWN = "unknown"


TOOL_CATEGORIES: dict[str, ToolCategory] = {
    # READ
    "file_read":   ToolCategory.READ,
    "file_search": ToolCategory.READ,
    "read_memory": ToolCategory.READ,
    "web_search":  ToolCategory.READ,
    "git_status":  ToolCategory.READ,
    "git_log":     ToolCategory.READ,
    "git_diff":    ToolCategory.READ,
    # WRITE
    "file_write":   ToolCategory.WRITE,
    "file_patch":   ToolCategory.WRITE,
    "write_memory": ToolCategory.WRITE,
    # SHELL
    "shell_exec": ToolCategory.SHELL,
    # GIT_MUTATE
    "git_clone":       ToolCategory.GIT_MUTATE,
    "git_commit_all":  ToolCategory.GIT_MUTATE,
    "git_branch":      ToolCategory.GIT_MUTATE,
    "git_pull":        ToolCategory.GIT_MUTATE,
    "git_reset":       ToolCategory.GIT_MUTATE,
    # GIT_PUSH (separat)
    "git_push": ToolCategory.GIT_PUSH,
    # NETWORK (lokale Klassifikation — siehe Docstring)
    "ask_agent": ToolCategory.NETWORK,
    # META
    "tool_search":       ToolCategory.META,
    "get_final_message": ToolCategory.META,
}


# Matrix: (mode, category) → allowed?
_ALLOW: set[tuple[IsolationMode, ToolCategory]] = {
    # Everyone allows READ/NETWORK/META
    (m, c)
    for m in IsolationMode
    for c in (ToolCategory.READ, ToolCategory.NETWORK, ToolCategory.META)
} | {
    # full_worktree: WRITE, SHELL, GIT_MUTATE, UNKNOWN erlaubt (GIT_PUSH bleibt blocked)
    (IsolationMode.FULL_WORKTREE, ToolCategory.WRITE),
    (IsolationMode.FULL_WORKTREE, ToolCategory.SHELL),
    (IsolationMode.FULL_WORKTREE, ToolCategory.GIT_MUTATE),
    (IsolationMode.FULL_WORKTREE, ToolCategory.UNKNOWN),
}


class IsolationError(ValueError):
    """Ungültiger Isolation-Mode oder Policy-Verletzung beim Konfigurieren."""


@dataclass(frozen=True)
class IsolationDecision:
    allowed: bool
    reason: str


def validate_isolation_mode(mode: str | IsolationMode) -> IsolationMode:
    """Normalisiert Eingabe auf IsolationMode. Case-sensitive."""
    if isinstance(mode, IsolationMode):
        return mode
    if not isinstance(mode, str):
        raise IsolationError(
            f"isolation_mode must be str or IsolationMode, got {type(mode).__name__}"
        )
    if mode not in ALLOWED_ISOLATION_MODES:
        raise IsolationError(
            f"invalid isolation_mode '{mode}'. allowed: {sorted(ALLOWED_ISOLATION_MODES)} "
            "(case-sensitive)"
        )
    return IsolationMode(mode)


def tool_category(tool_name: str) -> ToolCategory:
    """Kategorisiert tool_name. Unbekannt → UNKNOWN (siehe Docstring)."""
    return TOOL_CATEGORIES.get(tool_name, ToolCategory.UNKNOWN)


def allow_tool(mode: IsolationMode | str, tool_name: str) -> IsolationDecision:
    """
    Deklarative Policy-Entscheidung. Führt KEINE Aktion aus — liefert nur
    `IsolationDecision(allowed, reason)`. Aufrufer entscheidet selbst, wie
    er reagiert (blockieren, loggen, ignorieren).
    """
    m = validate_isolation_mode(mode)
    cat = tool_category(tool_name)
    if (m, cat) in _ALLOW:
        return IsolationDecision(
            allowed=True,
            reason=f"mode={m.value} allows category={cat.value}",
        )
    return IsolationDecision(
        allowed=False,
        reason=f"mode={m.value} blocks category={cat.value} (tool={tool_name})",
    )
