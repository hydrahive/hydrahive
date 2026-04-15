"""
subagent_worktrees.py — Git-Worktree-Isolation für Sub-Agent-Tasks (#651)

Bewusst NICHT `worktree_manager` genannt — das war ein Legacy-Modul
aus der Pre-#643-Ära (siehe Invariante 13). Architektur-Invariante
`test_invariant13b_worktree_manager_module_gone` schützt weiterhin
den alten Namen. Dieses Modul hat einen anderen Zweck:
Sub-Agent-Isolation via `git worktree add`, nicht Workspace-Kopieren.

V1 liefert NUR den Manager + Metadaten. Keine Orchestrator- oder
ask_agent-Integration, kein Auto-Cleanup, kein Auto-Merge. Runtime-
Anschluss folgt in einem separaten Issue.

Verzeichnis-Layout
------------------
worktrees_dir/
  trees/<worktree_id>/         Git-Worktree (detached HEAD auf base_commit)
  meta/<worktree_id>.json      Metadaten (JSON)

Lifecycle
---------
create_worktree(...)        → legt Tree + Meta an, status="active"
release_worktree(id)        → status="released", released_at, Tree bleibt
remove_worktree(id)         → entfernt Tree; Meta bleibt mit
                              status="removed", removed_at, tree_removed=True
list_worktrees()            → Liste aller Meta-Einträge

Sicherheitsmodell
-----------------
Worktrees werden unter einem fixierten Root-Verzeichnis angelegt
(<worktrees_dir>). Eingabefelder (sub_agent_id, task_id) werden auf
sichere Zeichenklassen beschränkt. Jeder zusammengesetzte Pfad wird
gegen Pfad-Traversal geprüft (resolve + is_relative_to).

Kein Auto-Cleanup, kein Auto-Merge. Rückführung ist Sache des Aufrufers.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_COLLISION_RETRIES = 3


class WorktreeError(Exception):
    """Worktree-Erzeugung oder -Verwaltung fehlgeschlagen."""


@dataclass
class WorktreeMeta:
    worktree_id: str
    parent_project_id: str
    parent_agent_id: str
    sub_agent_id: str
    task_id: str
    base_repo: str
    base_branch: str | None
    base_commit: str
    dirty: bool
    worktree_path: str
    created_at: str
    status: str = "active"                # active | released | removed
    released_at: str | None = None
    removed_at: str | None = None
    tree_removed: bool = False
    # Reserved für #652/#653 — Schema-Stabilität
    isolation_mode: str = "worktree"
    write_scope: Any = None


# ── Pfad-Helpers ─────────────────────────────────────────────────────────────

def _resolved_worktrees_root() -> Path:
    env = os.environ.get("HYDRAHIVE_WORKTREES_DIR")
    return Path(env) if env else Path("/var/lib/hydrahive/worktrees")


def _trees_dir(root: Path) -> Path:
    return root / "trees"


def _meta_dir(root: Path) -> Path:
    return root / "meta"


def _ensure_dirs(root: Path) -> None:
    try:
        _trees_dir(root).mkdir(parents=True, exist_ok=True)
        _meta_dir(root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorktreeError(f"cannot create worktrees_dir '{root}': {exc}") from exc


def _validate_identifier(name: str, field_name: str) -> str:
    if not isinstance(name, str) or not _ID_RE.match(name):
        raise WorktreeError(
            f"invalid {field_name}: must match {_ID_RE.pattern} (got {name!r})"
        )
    return name


def _assert_within(root: Path, candidate: Path, kind: str) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve() if candidate.exists() else (
        Path(str(candidate)).absolute()
    )
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise WorktreeError(
            f"path traversal blocked for {kind}: {candidate} not within {resolved_root}"
        ) from exc
    return resolved


# ── Git-Helpers ──────────────────────────────────────────────────────────────

def _run_git(args: list[str], cwd: Path | str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )


def is_git_repo(path: Path | str) -> bool:
    p = Path(path)
    if not p.is_dir():
        return False
    r = _run_git(["rev-parse", "--show-toplevel"], cwd=p)
    return r.returncode == 0 and bool(r.stdout.strip())


def _git_toplevel(path: Path) -> Path:
    r = _run_git(["rev-parse", "--show-toplevel"], cwd=path)
    if r.returncode != 0:
        raise WorktreeError(f"not a git repository: {path} ({r.stderr.strip()})")
    return Path(r.stdout.strip())


def _git_head_commit(repo: Path) -> str:
    r = _run_git(["rev-parse", "HEAD"], cwd=repo)
    if r.returncode != 0:
        raise WorktreeError(f"cannot read HEAD: {r.stderr.strip()}")
    return r.stdout.strip()


def _git_current_branch(repo: Path) -> str | None:
    r = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    if r.returncode != 0:
        return None
    branch = r.stdout.strip()
    return None if branch in ("", "HEAD") else branch


def _git_is_dirty(repo: Path) -> bool:
    r = _run_git(["status", "--porcelain"], cwd=repo)
    return bool(r.stdout.strip())


# ── Metadaten-IO ─────────────────────────────────────────────────────────────

def _meta_path(root: Path, worktree_id: str) -> Path:
    return _meta_dir(root) / f"{worktree_id}.json"


def _write_meta(root: Path, meta: WorktreeMeta) -> None:
    target = _meta_path(root, meta.worktree_id)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")
    os.replace(tmp, target)


def _read_meta(root: Path, worktree_id: str) -> WorktreeMeta:
    path = _meta_path(root, worktree_id)
    if not path.exists():
        raise WorktreeError(f"worktree not found: {worktree_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return WorktreeMeta(**data)


# ── Public API ───────────────────────────────────────────────────────────────

def _build_worktree_id(sub_agent_id: str) -> str:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rand = secrets.token_hex(4)
    return f"wt-{ts}-{sub_agent_id}-{rand}"


def create_worktree(
    *,
    base_repo: str | Path,
    parent_project_id: str,
    parent_agent_id: str,
    sub_agent_id: str,
    task_id: str,
    allow_dirty: bool = False,
    worktrees_dir: Path | None = None,
) -> WorktreeMeta:
    """
    Legt einen Git-Worktree mit detached HEAD auf dem aktuellen Commit an.
    Persistiert Metadaten. Haupt-Workspace bleibt unverändert.

    Raises WorktreeError bei fehlendem Git-Repo, dirty worktree (wenn
    allow_dirty=False), ungültigen Identifiern, Pfad-Traversal oder
    gescheitertem `git worktree add`.
    """
    _validate_identifier(parent_project_id, "parent_project_id")
    _validate_identifier(parent_agent_id, "parent_agent_id")
    _validate_identifier(sub_agent_id, "sub_agent_id")
    _validate_identifier(task_id, "task_id")

    base_repo_path = Path(base_repo).resolve()
    if not is_git_repo(base_repo_path):
        raise WorktreeError(f"not a git repository: {base_repo_path}")
    toplevel = _git_toplevel(base_repo_path)

    dirty = _git_is_dirty(toplevel)
    if dirty and not allow_dirty:
        raise WorktreeError(
            f"base repo has uncommitted changes: {toplevel} "
            "(pass allow_dirty=True to override)"
        )

    commit = _git_head_commit(toplevel)
    branch = _git_current_branch(toplevel)

    root = (worktrees_dir or _resolved_worktrees_root()).resolve()
    _ensure_dirs(root)

    trees = _trees_dir(root).resolve()

    # Eindeutige ID — bei Kollision retry.
    worktree_id = ""
    tree_path = trees  # placeholder, wird im Loop ersetzt
    for _ in range(_MAX_COLLISION_RETRIES):
        worktree_id = _build_worktree_id(sub_agent_id)
        tree_path_candidate = trees / worktree_id
        tree_path = _assert_within(trees, tree_path_candidate, "tree")
        if not tree_path.exists() and not _meta_path(root, worktree_id).exists():
            break
    else:
        raise WorktreeError("could not generate unique worktree_id after retries")

    # git worktree add --detach <tree_path> <commit>
    r = _run_git(
        ["worktree", "add", "--detach", str(tree_path), commit],
        cwd=toplevel,
    )
    if r.returncode != 0:
        stderr = r.stderr.strip()
        logger.warning(
            "git worktree add failed: repo=%s path=%s rc=%d",
            toplevel, tree_path, r.returncode,
        )
        raise WorktreeError(f"git worktree add failed: {stderr}")

    created_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    meta = WorktreeMeta(
        worktree_id=worktree_id,
        parent_project_id=parent_project_id,
        parent_agent_id=parent_agent_id,
        sub_agent_id=sub_agent_id,
        task_id=task_id,
        base_repo=str(toplevel),
        base_branch=branch,
        base_commit=commit,
        dirty=dirty,
        worktree_path=str(tree_path),
        created_at=created_at,
    )

    try:
        _write_meta(root, meta)
    except OSError as exc:
        # Rollback — Worktree entfernen, damit wir keine Waisen hinterlassen.
        logger.warning("meta write failed, rolling back worktree: %s", exc)
        _run_git(["worktree", "remove", "--force", str(tree_path)], cwd=toplevel)
        raise WorktreeError(f"cannot persist metadata: {exc}") from exc

    return meta


_WORKTREE_ID_RE = re.compile(r"^wt-[A-Za-z0-9_-]{1,96}$")


def _validate_worktree_id(worktree_id: str) -> None:
    if not isinstance(worktree_id, str) or not _WORKTREE_ID_RE.match(worktree_id):
        raise WorktreeError(f"invalid worktree_id: {worktree_id!r}")
    if "/" in worktree_id or ".." in worktree_id:
        raise WorktreeError(f"invalid worktree_id: {worktree_id!r}")


def get_worktree(
    worktree_id: str,
    *,
    worktrees_dir: Path | None = None,
) -> WorktreeMeta:
    _validate_worktree_id(worktree_id)
    root = (worktrees_dir or _resolved_worktrees_root()).resolve()
    return _read_meta(root, worktree_id)


def list_worktrees(
    *,
    worktrees_dir: Path | None = None,
) -> list[WorktreeMeta]:
    root = (worktrees_dir or _resolved_worktrees_root()).resolve()
    meta_d = _meta_dir(root)
    if not meta_d.is_dir():
        return []
    out: list[WorktreeMeta] = []
    for entry in sorted(meta_d.glob("*.json")):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            out.append(WorktreeMeta(**data))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("skipping corrupt meta %s: %s", entry, exc)
    return out


def release_worktree(
    worktree_id: str,
    *,
    worktrees_dir: Path | None = None,
) -> WorktreeMeta:
    """Markiert den Worktree als released. Tree und Meta bleiben auf Disk."""
    _validate_worktree_id(worktree_id)
    root = (worktrees_dir or _resolved_worktrees_root()).resolve()
    meta = _read_meta(root, worktree_id)
    meta.status = "released"
    meta.released_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    _write_meta(root, meta)
    return meta


def remove_worktree(
    worktree_id: str,
    *,
    force: bool = False,
    worktrees_dir: Path | None = None,
) -> WorktreeMeta:
    """
    Entfernt den Git-Worktree via `git worktree remove`. Metadaten bleiben
    erhalten, werden aber auf status="removed" gesetzt und mit removed_at/
    tree_removed ergänzt. Kein Auto-Cleanup der Meta in V1.
    """
    _validate_worktree_id(worktree_id)
    root = (worktrees_dir or _resolved_worktrees_root()).resolve()
    meta = _read_meta(root, worktree_id)
    base_repo = Path(meta.base_repo)
    tree_path = Path(meta.worktree_path)

    tree_removed = False
    if tree_path.exists():
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(tree_path))
        r = _run_git(args, cwd=base_repo)
        if r.returncode != 0:
            logger.warning(
                "git worktree remove failed (%s): %s",
                tree_path, r.stderr.strip(),
            )
            # Fallback: Baum direkt entfernen. Danach `git worktree prune`
            # separat möglich.
            try:
                shutil.rmtree(tree_path)
                tree_removed = True
            except OSError as exc:
                raise WorktreeError(
                    f"cannot remove worktree {tree_path}: {exc}"
                ) from exc
        else:
            tree_removed = True
    else:
        # Tree bereits weg — nur Meta-Status aktualisieren.
        tree_removed = True

    meta.status = "removed"
    meta.removed_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    meta.tree_removed = tree_removed
    _write_meta(root, meta)
    return meta
