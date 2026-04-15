"""
subagent_write_scope.py — Write-Scope Policy + Konfliktbericht (#653)

V1 liefert reine Pfad-/Scope-Policy und Report. KEINE Runtime-Enforcement,
KEIN Cross-Check gegen IsolationMode. Anwendung in Folge-Issues (#662
Runtime-Integration).

WriteScope
----------
{
  "allow": ["core/src/**", "core/tests/**"],
  "deny":  ["**/.env", "**/*secret*"],
  "description": "Core-only changes"
}

Pfade relativ zum Worktree-Root. Keine absoluten Pfade, kein "..", keine
Backslashes, keine NUL-Bytes, keine leeren Patterns. Max 200 Zeichen.

Regeln
------
1. `.git/**` (+ `.git`) ist IMMER implicit-denied — kein Bypass durch User.
2. Explizites deny gewinnt vor allow.
3. `allow` leer → **alles erlaubt außer deny** (implicit + user).
   Das ist die V1-Default-Semantik für full_worktree-Sub-Agenten: wer
   restriktiver will, setzt allow explizit.

Glob-Semantik
-------------
Eigener stdlib-only Glob-Matcher, keine neue Dependency.
- `*`  — matcht ein Segment (alles außer "/").
- `**` — matcht über Verzeichnis-Grenzen hinweg (0+ Segmente).
- `**/` am Anfang — optionales Dir-Prefix (gitignore-Style):
  `**/*.py` matcht `foo.py` UND `a/foo.py` UND `a/b/foo.py`.
- `?`  — matcht ein einzelnes Zeichen (außer "/").

Beispiele:
- `core/**` matcht `core/`, `core/x`, `core/a/b.py`; NICHT `core2/x`.
- `*.py` matcht `foo.py`; NICHT `a/foo.py`.
- `**/*.py` matcht `foo.py` und `a/foo.py`.
- `**/*secret*` matcht `secret.env` und `config/my_secret.yaml`.

Interaktion mit IsolationMode
-----------------------------
Dieses Modul macht KEINE Mode-Annahmen. Aufrufer entscheidet:
- read_only / patch_only: Isolation-Policy blockt Writes auf Tool-Ebene;
  WriteScope ist semantisch nur für Patch-Zielpfade (patch_only) oder
  informativ (read_only).
- full_worktree: WriteScope begrenzt direkte Writes im Worktree.
"""
from __future__ import annotations

import functools
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_PATTERN_LEN = 200
IMPLICIT_DENY_PATTERNS: tuple[str, ...] = (".git", ".git/**")
_ALLOWED_FIELDS: frozenset[str] = frozenset({"allow", "deny", "description"})


class WriteScopeError(ValueError):
    """Ungültiger WriteScope oder Pfad."""


@dataclass(frozen=True)
class WriteScope:
    allow:       tuple[str, ...] = ()
    deny:        tuple[str, ...] = ()
    description: str | None = None

    @property
    def is_empty_allow(self) -> bool:
        return not self.allow


@dataclass(frozen=True)
class WriteScopeViolation:
    path:   str
    reason: str    # "implicit_deny" | "deny: <pattern>" | "out_of_scope"


@dataclass(frozen=True)
class WriteScopeReport:
    allowed_files:      tuple[str, ...]
    denied_files:       tuple[str, ...]
    out_of_scope_files: tuple[str, ...]
    violations:         tuple[WriteScopeViolation, ...]
    ok:                 bool
    violations_count:   int


# ── Glob → Regex Converter ───────────────────────────────────────────────────

@functools.lru_cache(maxsize=512)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # `**`
                i += 2
                if i < n and pattern[i] == "/":
                    # `**/` — optional Dir-Prefix
                    out.append(r"(?:.*/)?")
                    i += 1
                else:
                    # `**` am Ende oder ohne nachfolgenden /
                    out.append(r".*")
            else:
                out.append(r"[^/]*")
                i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile(r"\A" + "".join(out) + r"\Z")


def _matches_any(path: str, patterns: tuple[str, ...]) -> str | None:
    for p in patterns:
        if _glob_to_regex(p).match(path):
            return p
    return None


# ── Validation ───────────────────────────────────────────────────────────────

def _validate_pattern(p: object, field: str) -> str:
    if not isinstance(p, str):
        raise WriteScopeError(f"{field}: pattern must be string, got {type(p).__name__}")
    if not p:
        raise WriteScopeError(f"{field}: empty pattern")
    if len(p) > MAX_PATTERN_LEN:
        raise WriteScopeError(f"{field}: pattern longer than {MAX_PATTERN_LEN} chars")
    if "\x00" in p:
        raise WriteScopeError(f"{field}: NUL byte in pattern")
    if "\\" in p:
        raise WriteScopeError(f"{field}: backslash not allowed: {p!r}")
    if p.startswith("/"):
        raise WriteScopeError(f"{field}: absolute path not allowed: {p!r}")
    for seg in p.split("/"):
        if seg == "..":
            raise WriteScopeError(f"{field}: '..' segment not allowed: {p!r}")
    return p


def validate_write_scope(data: dict | None) -> WriteScope:
    if data is None:
        return WriteScope()
    if not isinstance(data, dict):
        raise WriteScopeError(f"write_scope must be object or null, got {type(data).__name__}")

    unknown = set(data.keys()) - _ALLOWED_FIELDS
    if unknown:
        raise WriteScopeError(
            f"unknown fields: {sorted(unknown)}. allowed: {sorted(_ALLOWED_FIELDS)}"
        )

    allow_raw = data.get("allow", [])
    deny_raw = data.get("deny", [])
    if not isinstance(allow_raw, list):
        raise WriteScopeError(f"allow must be array, got {type(allow_raw).__name__}")
    if not isinstance(deny_raw, list):
        raise WriteScopeError(f"deny must be array, got {type(deny_raw).__name__}")

    allow = tuple(_validate_pattern(p, "allow") for p in allow_raw)
    deny = tuple(_validate_pattern(p, "deny") for p in deny_raw)

    description = data.get("description")
    if description is not None and not isinstance(description, str):
        raise WriteScopeError(
            f"description must be string or null, got {type(description).__name__}"
        )

    return WriteScope(allow=allow, deny=deny, description=description)


def write_scope_to_dict(scope: WriteScope) -> dict:
    """Kanonische JSON-serialisierbare Form (für Metadaten-Persistenz)."""
    return {
        "allow": list(scope.allow),
        "deny": list(scope.deny),
        "description": scope.description,
    }


# ── Path check ───────────────────────────────────────────────────────────────

def _normalize_rel(rel_path: str) -> str:
    if not isinstance(rel_path, str) or not rel_path:
        raise WriteScopeError(f"rel_path must be non-empty string, got {rel_path!r}")
    if "\\" in rel_path:
        raise WriteScopeError(f"backslash not allowed in path: {rel_path!r}")
    if rel_path.startswith("/"):
        raise WriteScopeError(f"absolute path not allowed: {rel_path!r}")
    for seg in rel_path.split("/"):
        if seg == "..":
            raise WriteScopeError(f"'..' segment not allowed: {rel_path!r}")
    # führende "./" entfernen
    while rel_path.startswith("./"):
        rel_path = rel_path[2:]
    return rel_path


def path_allowed(scope: WriteScope, rel_path: str) -> tuple[bool, str]:
    """
    Returns (allowed, reason).
    Reihenfolge: implicit deny → user deny → allow-check.
    """
    norm = _normalize_rel(rel_path)

    m = _matches_any(norm, IMPLICIT_DENY_PATTERNS)
    if m is not None:
        return (False, f"implicit_deny: {m}")

    m = _matches_any(norm, scope.deny)
    if m is not None:
        return (False, f"deny: {m}")

    if scope.is_empty_allow:
        return (True, "no allow-list (all permitted outside deny)")

    m = _matches_any(norm, scope.allow)
    if m is not None:
        return (True, f"allow: {m}")

    return (False, "out_of_scope")


# ── Changed files (via git) ──────────────────────────────────────────────────

def _run_git(args: list[str], cwd: Path | str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=False,
        check=False,
    )


def changed_files(worktree_path: Path | str) -> list[str]:
    """
    Liefert eine sortierte Liste aller geänderten/neuen/gelöschten Pfade
    im angegebenen Worktree, POSIX-normalisiert, relativ zum Worktree-Root.
    Nutzt `git status --porcelain=v1 -z` → NUL-separiert für sauberes
    Rename-Handling.
    """
    wt = Path(worktree_path)
    if not wt.is_dir():
        raise WriteScopeError(f"worktree path does not exist: {wt}")

    r = _run_git(["status", "--porcelain=v1", "-z"], wt)
    if r.returncode != 0:
        raise WriteScopeError(
            f"git status failed in {wt}: rc={r.returncode} "
            f"err={(r.stderr or b'').decode('utf-8', 'replace')[:200]}"
        )

    data = r.stdout or b""
    if not data:
        return []

    # Porcelain -z: jeder Eintrag ist "XY path\0" (bei renames:
    # "R  newpath\0oldpath\0"). XY sind 2 Statuszeichen + Space.
    files: set[str] = set()
    tokens = data.split(b"\x00")
    # Letzter Eintrag nach trailing NUL ist leer.
    if tokens and tokens[-1] == b"":
        tokens.pop()

    i = 0
    while i < len(tokens):
        entry = tokens[i]
        if len(entry) < 4:
            i += 1
            continue
        xy = entry[:2]
        # Format: "XY path" — nach XY kommt Space, dann Pfad.
        path_bytes = entry[3:]
        path = path_bytes.decode("utf-8", "replace").replace("\\", "/")
        files.add(path)
        # Rename/Copy: nächster Token ist der alte Pfad.
        if xy[:1] in (b"R", b"C"):
            if i + 1 < len(tokens):
                old = tokens[i + 1].decode("utf-8", "replace").replace("\\", "/")
                files.add(old)
                i += 2
                continue
        i += 1

    return sorted(files)


# ── Scope evaluation ─────────────────────────────────────────────────────────

def evaluate_worktree_scope(
    worktree_path: Path | str,
    scope: WriteScope,
) -> WriteScopeReport:
    """
    Ermittelt alle geänderten Pfade im Worktree und klassifiziert sie
    gegen den Scope. Führt keine Aktion aus — reiner Report.
    """
    if not isinstance(scope, WriteScope):
        raise WriteScopeError(
            f"scope must be WriteScope, got {type(scope).__name__}"
        )

    files = changed_files(worktree_path)

    allowed: list[str] = []
    denied: list[str] = []
    out_of_scope: list[str] = []
    violations: list[WriteScopeViolation] = []

    for f in files:
        try:
            ok, reason = path_allowed(scope, f)
        except WriteScopeError as exc:
            # Defekter Pfad aus git-Output → als Violation klassifizieren,
            # nicht hart abbrechen.
            violations.append(WriteScopeViolation(path=f, reason=f"invalid_path: {exc}"))
            out_of_scope.append(f)
            continue
        if ok:
            allowed.append(f)
        elif reason.startswith("deny") or reason.startswith("implicit_deny"):
            denied.append(f)
            violations.append(WriteScopeViolation(path=f, reason=reason))
        else:
            out_of_scope.append(f)
            violations.append(WriteScopeViolation(path=f, reason=reason))

    return WriteScopeReport(
        allowed_files=tuple(allowed),
        denied_files=tuple(denied),
        out_of_scope_files=tuple(out_of_scope),
        violations=tuple(violations),
        ok=not violations,
        violations_count=len(violations),
    )
