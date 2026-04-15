"""
subagent_patch_artifacts.py — Patch-Artefakt-Flow für patch_only Sub-Agenten (#665)

V1 liefert nur Extraktion, Validierung, Persistenz. KEIN Auto-Apply,
KEIN Merge. Der Caller entscheidet, ob/wie der Patch angewendet wird.

Nur wirksam wenn `isolation_mode == "patch_only"` (siehe ask_agent). Für
read_only/full_worktree wird dieses Modul nicht aufgerufen.

Fenced-Block-Konvention
-----------------------
Sub-Agent-Response muss den Patch in einen fenced Code-Block packen:

    ```diff
    diff --git a/core/foo.py b/core/foo.py
    --- a/core/foo.py
    +++ b/core/foo.py
    @@ -1,3 +1,3 @@
    -old
    +new
    ```

Alias ` ```patch ` ist ebenfalls akzeptiert. Kein raw-diff-Detection in V1 —
zu viele false positives durch Code-Erklärungen, Separatoren, YAML-Frontmatter.

Safety
------
Alle extrahierten Pfade werden auf:
- absolut (`/etc/passwd`) → unsafe
- `..`-Segment → unsafe
- Backslash `\\` → unsafe
- NUL-Byte → unsafe
- leer → unsafe

Zusätzlich gegen WriteScope (#653) geprüft: `.git/**` ist implicit-deny,
`deny`-Pattern gewinnt, leere allow-Liste = erlaubt außer deny.

Persistenz
----------
Artifact wird unter `<worktrees_dir>/artifacts/<worktree_id>.patch`
gespeichert (außerhalb des Worktrees selbst). Auch `invalid=True`-Artifacts
werden persistiert für Audit-Trail. Atomisch via tempfile + os.replace.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .subagent_write_scope import WriteScope, path_allowed

logger = logging.getLogger(__name__)

_FENCED_DIFF_RE = re.compile(r"```(?:diff|patch)\s*\n(.*?)\n```", re.DOTALL)


@dataclass(frozen=True)
class PatchArtifactReport:
    present:       bool
    valid:         bool
    paths:         tuple[str, ...] = ()
    violations:    tuple[str, ...] = ()
    error:         str | None = None
    artifact_path: str | None = None
    bytes:         int | None = None


class PatchArtifactError(ValueError):
    """Interner Parser-/Persistenz-Fehler."""


# ── Extraktion ───────────────────────────────────────────────────────────────

def extract_fenced_diff(text: str) -> tuple[str | None, int]:
    """
    Extrahiert den ersten fenced ```diff/```patch Block.
    Returns: (content_or_None, total_block_count).
    """
    if not isinstance(text, str) or not text:
        return (None, 0)
    matches = _FENCED_DIFF_RE.findall(text)
    if not matches:
        return (None, 0)
    return (matches[0], len(matches))


# ── Pfad-Parsing ─────────────────────────────────────────────────────────────

_RE_GIT_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
_RE_MINUS_HEADER = re.compile(r"^--- (?:a/)?(.+?)$", re.MULTILINE)
_RE_PLUS_HEADER = re.compile(r"^\+\+\+ (?:b/)?(.+?)$", re.MULTILINE)
_RE_RENAME_FROM = re.compile(r"^rename from (.+?)$", re.MULTILINE)
_RE_RENAME_TO = re.compile(r"^rename to (.+?)$", re.MULTILINE)


def _strip_trailing_tab_meta(s: str) -> str:
    """Unified-Diff-Header kann Timestamp nach TAB haben. Wir nehmen nur den Pfad."""
    return s.split("\t", 1)[0].rstrip()


def parse_unified_diff_paths(diff: str) -> list[str]:
    """
    Extrahiert alle Pfade aus einem Unified-Diff.
    Berücksichtigt `diff --git a/... b/...`, `--- a/...`, `+++ b/...`,
    `rename from`, `rename to`. `/dev/null` wird übersprungen (neuer/gelöschter
    File-Gegenstück liefert den Namen).
    """
    if not isinstance(diff, str):
        return []

    paths: set[str] = set()

    for m in _RE_GIT_HEADER.finditer(diff):
        a_p = _strip_trailing_tab_meta(m.group(1))
        b_p = _strip_trailing_tab_meta(m.group(2))
        if a_p and a_p != "/dev/null":
            paths.add(a_p)
        if b_p and b_p != "/dev/null":
            paths.add(b_p)

    for m in _RE_MINUS_HEADER.finditer(diff):
        p = _strip_trailing_tab_meta(m.group(1))
        if p and p != "/dev/null":
            paths.add(p)

    for m in _RE_PLUS_HEADER.finditer(diff):
        p = _strip_trailing_tab_meta(m.group(1))
        if p and p != "/dev/null":
            paths.add(p)

    for m in _RE_RENAME_FROM.finditer(diff):
        p = m.group(1).strip()
        if p:
            paths.add(p)
    for m in _RE_RENAME_TO.finditer(diff):
        p = m.group(1).strip()
        if p:
            paths.add(p)

    return sorted(paths)


# ── Safety + WriteScope ──────────────────────────────────────────────────────

def _is_unsafe_path(p: str) -> str | None:
    """Returns reason-string if unsafe, else None."""
    if not isinstance(p, str) or not p:
        return "empty_path"
    if "\x00" in p:
        return "nul_byte"
    if "\\" in p:
        return "backslash"
    if p.startswith("/"):
        return "absolute_path"
    for seg in p.split("/"):
        if seg == "..":
            return "dotdot_segment"
    return None


def validate_patch_paths(
    paths: list[str],
    write_scope: WriteScope | None,
) -> tuple[list[str], list[str]]:
    """
    Prüft jede Pfadangabe gegen Safety + WriteScope.
    Returns (safe_paths, violations).
    """
    safe: list[str] = []
    violations: list[str] = []

    for p in paths:
        unsafe_reason = _is_unsafe_path(p)
        if unsafe_reason is not None:
            violations.append(f"unsafe_path: {p!r}: {unsafe_reason}")
            continue

        # WriteScope: None → nur Safety prüfen, Pfad durchlassen.
        if write_scope is None:
            safe.append(p)
            continue

        ok, reason = path_allowed(write_scope, p)
        if not ok:
            violations.append(f"{p}: {reason}")
        else:
            safe.append(p)

    return (safe, violations)


# ── Persistenz ───────────────────────────────────────────────────────────────

def _resolved_worktrees_root(override: Path | None) -> Path:
    if override is not None:
        return Path(override)
    env = os.environ.get("HYDRAHIVE_WORKTREES_DIR")
    return Path(env) if env else Path("/var/lib/hydrahive/worktrees")


def _artifacts_dir(root: Path) -> Path:
    return root / "artifacts"


_WORKTREE_ID_RE = re.compile(r"^wt-[A-Za-z0-9_-]{1,96}$")


def persist_patch_artifact(
    worktree_id: str,
    diff: str,
    *,
    worktrees_dir: Path | None = None,
) -> Path:
    """Schreibt den Patch-Inhalt atomisch an `<worktrees_dir>/artifacts/<id>.patch`."""
    if not _WORKTREE_ID_RE.match(worktree_id or ""):
        raise PatchArtifactError(f"invalid worktree_id: {worktree_id!r}")
    if not isinstance(diff, str):
        raise PatchArtifactError("diff must be str")

    root = _resolved_worktrees_root(worktrees_dir).resolve()
    artifacts = _artifacts_dir(root)
    try:
        artifacts.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PatchArtifactError(f"cannot create artifacts dir: {exc}") from exc

    target = artifacts / f"{worktree_id}.patch"
    # Prefix-Check (defensiv)
    try:
        target.resolve().relative_to(artifacts.resolve())
    except ValueError as exc:
        raise PatchArtifactError(f"path traversal blocked: {target}") from exc

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(diff, encoding="utf-8")
    os.replace(tmp, target)
    return target


# ── High-Level Orchestration ─────────────────────────────────────────────────

def build_patch_artifact_report(
    text: str,
    write_scope: WriteScope | None,
    worktree_id: str,
    *,
    worktrees_dir: Path | None = None,
    persist: bool = True,
) -> PatchArtifactReport:
    """
    Extrahiert → parst → validiert → (optional) persistiert.
    Liefert vollständigen Report.
    """
    content, block_count = extract_fenced_diff(text)

    if content is None:
        return PatchArtifactReport(
            present=False,
            valid=False,
            error="no_diff_block_found",
        )

    paths = parse_unified_diff_paths(content)
    safe, violations = validate_patch_paths(paths, write_scope)

    # Multiple diff blocks → valid=False, error markiert
    multiple_blocks_err = "multiple_diff_blocks" if block_count > 1 else None

    valid = not violations and multiple_blocks_err is None

    artifact_path: str | None = None
    byte_count: int | None = None
    if persist:
        try:
            written = persist_patch_artifact(
                worktree_id, content, worktrees_dir=worktrees_dir,
            )
            artifact_path = str(written)
            byte_count = len(content.encode("utf-8"))
        except PatchArtifactError as exc:
            logger.warning("persist_patch_artifact failed: %s", exc)
            # Report bleibt ohne artifact_path; error ergänzen
            if multiple_blocks_err is None:
                multiple_blocks_err = f"persist_failed: {exc}"
            valid = False

    return PatchArtifactReport(
        present=True,
        valid=valid,
        paths=tuple(sorted(set(paths))),
        violations=tuple(violations),
        error=multiple_blocks_err,
        artifact_path=artifact_path,
        bytes=byte_count,
    )
