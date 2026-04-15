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
from typing import Literal

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


# ── Scope-Overlap-Erkennung (#666) ───────────────────────────────────────────
#
# V1 — konservativ: deklarativer Overlap-Check zwischen zwei WriteScopes für
# Multi-Agent-Policies. KEINE Runtime-Integration, KEIN Glob-Solver.
#
# Regeln:
# - `safe` entsteht NUR aus eindeutig disjunkter Allow-Geometrie.
# - Deny darf ein erkanntes Overlap höchstens zu `uncertain` herabstufen,
#   nie zu `safe`.
# - Unentscheidbare Pattern-Klassen → `uncertain`.


OverlapStatus = Literal["safe", "overlap", "uncertain"]
_PairVerdict = Literal["safe", "overlap", "uncertain"]


@dataclass(frozen=True)
class ScopeOverlap:
    status:               OverlapStatus
    overlapping_patterns: tuple[tuple[str, str], ...] = ()
    uncertain_patterns:   tuple[tuple[str, str, str], ...] = ()
    reason:               str = ""


# Pattern-Klassifikation

_RE_LITERAL_EXT = re.compile(r"^\*\.([A-Za-z0-9_][A-Za-z0-9_.-]*)$")


def _has_wildcard(s: str) -> bool:
    return any(c in s for c in "*?")


def _classify_pattern(p: str) -> tuple[str, tuple[str, ...]]:
    """
    Returns (kind, payload).
      kind = "literal"       payload=(path,)
      kind = "catch_all"     payload=()
      kind = "prefix"        payload=(prefix_no_slash,)       # "core" für "core/**"
      kind = "ext_any"       payload=(ext,)                   # "py" für "**/*.py"
      kind = "ext_scoped"    payload=(prefix_no_slash, ext)   # ("core","py") für "core/*.py"
      kind = "complex"       payload=()
    """
    if not p:
        return ("complex", ())
    if p in ("**", "**/*"):
        return ("catch_all", ())
    if not _has_wildcard(p):
        return ("literal", (p,))
    # prefix_subtree: "<literal>/**"
    if p.endswith("/**"):
        pref = p[:-3]
        if pref and not _has_wildcard(pref):
            return ("prefix", (pref,))
    # ext_any: "**/*.<ext>" (ext literal)
    if p.startswith("**/"):
        tail = p[3:]
        m = _RE_LITERAL_EXT.match(tail)
        if m is not None:
            return ("ext_any", (m.group(1),))
        return ("complex", ())
    # ext_scoped: "<literal>/*.<ext>"
    if "/" in p:
        head, tail = p.rsplit("/", 1)
        if head and not _has_wildcard(head):
            m = _RE_LITERAL_EXT.match(tail)
            if m is not None:
                return ("ext_scoped", (head, m.group(1)))
    else:
        # reines "*.<ext>" am Top-Level: segment-local ext_scoped mit leerem Prefix
        m = _RE_LITERAL_EXT.match(p)
        if m is not None:
            return ("ext_scoped", ("", m.group(1)))
    return ("complex", ())


def _is_segment_prefix_or_equal(parent: str, child: str) -> bool:
    """True, wenn child == parent oder child startet mit parent + '/'."""
    if parent == child:
        return True
    return child.startswith(parent + "/")


def _literal_under_prefix(lit: str, pref: str) -> bool:
    """Literal-Pfad liegt im Subtree `pref/**`."""
    if pref == "":
        return True  # catch-all-like
    return _is_segment_prefix_or_equal(pref, lit) and lit != pref


def _literal_ends_with_ext(lit: str, ext: str) -> bool:
    return lit.endswith("." + ext)


def _literal_dirname(lit: str) -> str:
    return lit.rsplit("/", 1)[0] if "/" in lit else ""


def _classify_pair(pa: str, pb: str) -> _PairVerdict:
    """Konservativer Overlap-Check für zwei Patterns. Symmetrisch."""
    if pa == pb:
        return "overlap"

    ka, va = _classify_pattern(pa)
    kb, vb = _classify_pattern(pb)

    # Normalisiere Reihenfolge für die Matrix
    if (ka, kb) > (kb, ka):
        ka, kb = kb, ka
        va, vb = vb, va

    # complex → uncertain
    if ka == "complex" or kb == "complex":
        return "uncertain"

    # catch_all schluckt alles
    if ka == "catch_all" or kb == "catch_all":
        return "overlap"

    # literal × literal
    if ka == "literal" and kb == "literal":
        return "overlap" if va[0] == vb[0] else "safe"

    # literal × prefix
    if ka == "literal" and kb == "prefix":
        return "overlap" if _literal_under_prefix(va[0], vb[0]) else "safe"

    # literal × ext_any
    if ka == "ext_any" and kb == "literal":
        return "overlap" if _literal_ends_with_ext(vb[0], va[0]) else "safe"

    # literal × ext_scoped
    if ka == "ext_scoped" and kb == "literal":
        lit = vb[0]
        pref, ext = va
        return (
            "overlap"
            if _literal_dirname(lit) == pref and _literal_ends_with_ext(lit, ext)
            else "safe"
        )

    # prefix × prefix
    if ka == "prefix" and kb == "prefix":
        p1, p2 = va[0], vb[0]
        if _is_segment_prefix_or_equal(p1, p2) or _is_segment_prefix_or_equal(p2, p1):
            return "overlap"
        return "safe"

    # prefix × ext_any — Subtree kann .ext-Dateien enthalten
    if ka == "ext_any" and kb == "prefix":
        return "overlap"

    # prefix × ext_scoped
    if ka == "ext_scoped" and kb == "prefix":
        sp, _ext = va
        pp = vb[0]
        # ext_scoped matcht nur direkt in sp/ ; prefix matcht alles unter pp/.
        # overlap nur wenn sp unter pp/ liegt (oder gleich).
        if _is_segment_prefix_or_equal(pp, sp):
            return "overlap"
        return "safe"

    # ext_any × ext_any
    if ka == "ext_any" and kb == "ext_any":
        return "overlap" if va[0] == vb[0] else "safe"

    # ext_any × ext_scoped
    if ka == "ext_any" and kb == "ext_scoped":
        return "overlap" if va[0] == vb[1] else "safe"

    # ext_scoped × ext_scoped
    if ka == "ext_scoped" and kb == "ext_scoped":
        return "overlap" if va == vb else "safe"

    # Safety-Net: unbekannte Kombination → uncertain
    return "uncertain"


def _deny_touches_overlap(
    overlapping_pairs: list[tuple[str, str]],
    deny_a: tuple[str, ...],
    deny_b: tuple[str, ...],
) -> bool:
    """
    Konservativ: wenn IRGENDEIN deny-Pattern einer Seite mit einem
    overlappenden Allow-Pattern potenziell interagiert (verdict != safe),
    gilt der Overlap als durch deny möglicherweise beeinflusst.
    Führt zu Downgrade overlap → uncertain.
    """
    denies = tuple(deny_a) + tuple(deny_b)
    if not denies:
        return False
    for pa, pb in overlapping_pairs:
        for d in denies:
            if _classify_pair(d, pa) != "safe":
                return True
            if _classify_pair(d, pb) != "safe":
                return True
    return False


def compare_scopes(a: WriteScope, b: WriteScope) -> ScopeOverlap:
    """
    Deklarativer Overlap-Check. V1 — konservativ.
    Regeln:
      - safe nur aus eindeutig disjunkter Allow-Geometrie.
      - Deny darf overlap → uncertain herabstufen, niemals zu safe.
    """
    if not isinstance(a, WriteScope) or not isinstance(b, WriteScope):
        raise WriteScopeError("compare_scopes: both args must be WriteScope")

    a_empty = a.is_empty_allow
    b_empty = b.is_empty_allow

    # Beide catch-all (leere allow): overlap-Gebiet = alles.
    if a_empty and b_empty:
        status: OverlapStatus = "overlap"
        reason = "both_empty_allow_cover_everything"
        if a.deny or b.deny:
            status = "uncertain"
            reason = "both_empty_allow_with_deny"
        return ScopeOverlap(
            status=status,
            overlapping_patterns=(("<empty>", "<empty>"),) if status == "overlap" else (),
            uncertain_patterns=(("<empty>", "<empty>", "deny_may_narrow"),)
            if status == "uncertain" else (),
            reason=reason,
        )

    # Eine Seite catch-all, andere non-empty.
    if a_empty or b_empty:
        non_empty = a.allow if b_empty else b.allow
        pairs = tuple(
            (("<empty>", p) if a_empty else (p, "<empty>"))
            for p in non_empty
        )
        if a.deny or b.deny:
            return ScopeOverlap(
                status="uncertain",
                uncertain_patterns=tuple((x, y, "empty_allow_with_deny") for x, y in pairs),
                reason="one_empty_allow_and_deny_present",
            )
        return ScopeOverlap(
            status="overlap",
            overlapping_patterns=pairs,
            reason="one_empty_allow_covers_other",
        )

    # Beide non-empty: Kreuzprodukt klassifizieren.
    overlaps: list[tuple[str, str]] = []
    uncertains: list[tuple[str, str, str]] = []
    for pa in a.allow:
        for pb in b.allow:
            v = _classify_pair(pa, pb)
            if v == "overlap":
                overlaps.append((pa, pb))
            elif v == "uncertain":
                uncertains.append((pa, pb, "complex_glob"))

    if not overlaps and not uncertains:
        return ScopeOverlap(
            status="safe",
            reason="disjoint_allow_geometry",
        )

    if overlaps:
        if _deny_touches_overlap(overlaps, a.deny, b.deny):
            return ScopeOverlap(
                status="uncertain",
                overlapping_patterns=tuple(overlaps),
                uncertain_patterns=tuple(uncertains)
                + tuple((pa, pb, "deny_may_exclude_overlap") for pa, pb in overlaps),
                reason="allow_overlap_with_deny_interaction",
            )
        return ScopeOverlap(
            status="overlap",
            overlapping_patterns=tuple(overlaps),
            uncertain_patterns=tuple(uncertains),
            reason="allow_overlap_detected",
        )

    # nur uncertains
    return ScopeOverlap(
        status="uncertain",
        uncertain_patterns=tuple(uncertains),
        reason="complex_glob_patterns_undecidable",
    )


def compare_many_scopes(
    scopes: dict[str, WriteScope],
) -> dict[tuple[str, str], ScopeOverlap]:
    """
    Paarweise Overlap-Auswertung für Multi-Agent-Setups.
    Liefert für jedes ungeordnete Paar (name_a < name_b) einen Report.
    """
    if not isinstance(scopes, dict):
        raise WriteScopeError("compare_many_scopes: scopes must be dict")
    for name, s in scopes.items():
        if not isinstance(name, str) or not name:
            raise WriteScopeError(f"compare_many_scopes: invalid name {name!r}")
        if not isinstance(s, WriteScope):
            raise WriteScopeError(f"compare_many_scopes: {name!r} is not WriteScope")

    names = sorted(scopes.keys())
    out: dict[tuple[str, str], ScopeOverlap] = {}
    for i, na in enumerate(names):
        for nb in names[i + 1:]:
            out[(na, nb)] = compare_scopes(scopes[na], scopes[nb])
    return out
