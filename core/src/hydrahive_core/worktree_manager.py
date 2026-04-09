"""
worktree_manager.py — Git-Worktree-Isolation pro Task (#521)

Erstellt isolierte Git-Worktrees für Worker-Tasks. Jeder Worker bekommt
seinen eigenen Arbeitsbaum — kein Merge-Konflikt bei paralleler Arbeit.

Safety:
- Worktrees liegen IMMER unter /tmp/hydrahive-git/ (nie im Projekt-Root)
- Branch-Namen sind eindeutig per Task-ID
- Cleanup ist idempotent
- Max 10 gleichzeitige Worktrees pro Repo
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

WORKTREE_BASE = Path("/tmp/hydrahive-git")
MAX_WORKTREES_PER_REPO = 10


@dataclass
class WorktreeInfo:
    """Informationen über einen aktiven Worktree."""
    path: Path                    # Absoluter Pfad zum Worktree
    branch: str                   # Branch-Name
    task_id: str                  # Zugehöriger Task
    worker_id: str                # Worker der darin arbeitet
    created_at: float             # time.monotonic()
    base_workspace: Path          # Original-Workspace (Haupt-Repo)


class WorktreeManager:
    """Verwaltet Git-Worktrees für Task-Isolation."""

    def __init__(self):
        self._active: dict[str, WorktreeInfo] = {}  # task_id → WorktreeInfo
        self._lock = asyncio.Lock()

    async def create_worktree(
        self,
        workspace: Path,
        task_id: str,
        worker_id: str,
        branch_prefix: str = "task",
    ) -> WorktreeInfo:
        """
        Erstellt einen neuen Worktree für einen Task.

        Args:
            workspace: Haupt-Workspace (git repo root)
            task_id: Eindeutige Task-ID
            worker_id: Worker der den Worktree nutzt
            branch_prefix: Prefix für den Branch-Namen

        Returns:
            WorktreeInfo mit dem Pfad zum Worktree

        Raises:
            RuntimeError: Wenn zu viele Worktrees aktiv sind oder kein Git-Repo
        """
        async with self._lock:
            # Safety: Prüfen ob workspace ein Git-Repo ist
            if not (workspace / ".git").exists():
                raise RuntimeError(f"Kein Git-Repo: {workspace}")

            # Max-Limit prüfen
            repo_count = sum(
                1 for wt in self._active.values()
                if wt.base_workspace == workspace
            )
            if repo_count >= MAX_WORKTREES_PER_REPO:
                raise RuntimeError(
                    f"Max. {MAX_WORKTREES_PER_REPO} Worktrees pro Repo erreicht"
                )

            # Branch- und Pfad-Namen
            short_id = task_id[:8]
            branch = f"{branch_prefix}/{worker_id}/{short_id}"
            wt_dir = WORKTREE_BASE / f".worktrees/{short_id}"

            # Aufräumen falls der Pfad schon existiert (Crash-Recovery)
            if wt_dir.exists():
                logger.warning("Worktree-Pfad existiert bereits, räume auf: %s", wt_dir)
                shutil.rmtree(wt_dir, ignore_errors=True)

            wt_dir.parent.mkdir(parents=True, exist_ok=True)

            # Git-Worktree erstellen
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "add", "-b", branch, str(wt_dir),
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                # Branch existiert schon? Ohne -b versuchen
                if "already exists" in err_msg:
                    proc2 = await asyncio.create_subprocess_exec(
                        "git", "worktree", "add", str(wt_dir), branch,
                        cwd=str(workspace),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=30)
                    if proc2.returncode != 0:
                        raise RuntimeError(f"Git worktree add fehlgeschlagen: {stderr2.decode()[:200]}")
                else:
                    raise RuntimeError(f"Git worktree add fehlgeschlagen: {err_msg[:200]}")

            info = WorktreeInfo(
                path=wt_dir,
                branch=branch,
                task_id=task_id,
                worker_id=worker_id,
                created_at=time.monotonic(),
                base_workspace=workspace,
            )
            self._active[task_id] = info
            logger.info("Worktree erstellt: %s → %s (Branch: %s)", task_id[:8], wt_dir, branch)
            return info

    async def cleanup_worktree(self, task_id: str, merge: bool = False) -> dict:
        """
        Räumt einen Worktree auf.

        Args:
            task_id: Task dessen Worktree aufgeräumt werden soll
            merge: Wenn True, Branch in Hauptbranch mergen vor Cleanup

        Returns:
            {"ok": True/False, "merged": bool, "conflicts": bool}
        """
        async with self._lock:
            info = self._active.pop(task_id, None)
            if not info:
                return {"ok": True, "reason": "kein aktiver Worktree"}

            result = {"ok": True, "merged": False, "conflicts": False}

            # Optional: Merge zurück in Hauptbranch
            if merge and info.base_workspace.exists():
                try:
                    # Prüfen ob es Commits gibt die gemergt werden müssen
                    proc = await asyncio.create_subprocess_exec(
                        "git", "log", f"HEAD..{info.branch}", "--oneline",
                        cwd=str(info.base_workspace),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                    has_commits = bool(stdout.decode().strip())

                    if has_commits:
                        merge_proc = await asyncio.create_subprocess_exec(
                            "git", "merge", info.branch, "--no-edit",
                            cwd=str(info.base_workspace),
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        _, merge_err = await asyncio.wait_for(merge_proc.communicate(), timeout=30)
                        if merge_proc.returncode == 0:
                            result["merged"] = True
                        else:
                            # Merge-Konflikt — abort und User benachrichtigen
                            await asyncio.create_subprocess_exec(
                                "git", "merge", "--abort",
                                cwd=str(info.base_workspace),
                            )
                            result["conflicts"] = True
                            result["ok"] = False
                            logger.warning("Worktree merge Konflikt: %s", info.branch)
                except Exception as e:
                    logger.warning("Worktree merge fehlgeschlagen: %s", e)

            # Worktree entfernen
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "worktree", "remove", str(info.path), "--force",
                    cwd=str(info.base_workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=15)
            except Exception:
                # Fallback: Verzeichnis manuell löschen
                if info.path.exists():
                    shutil.rmtree(info.path, ignore_errors=True)

            # Branch löschen (nur wenn nicht gemergt oder kein Merge gewünscht)
            if not result.get("conflicts"):
                try:
                    await asyncio.create_subprocess_exec(
                        "git", "branch", "-D", info.branch,
                        cwd=str(info.base_workspace),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                except Exception:
                    pass

            # Git worktree prune (aufräumen)
            try:
                await asyncio.create_subprocess_exec(
                    "git", "worktree", "prune",
                    cwd=str(info.base_workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except Exception:
                pass

            logger.info("Worktree aufgeräumt: %s (merged=%s, conflicts=%s)",
                        task_id[:8], result["merged"], result["conflicts"])
            return result

    async def cleanup_stale(self, max_age_hours: float = 4) -> int:
        """Räumt verwaiste Worktrees auf (Crash-Recovery beim Start)."""
        cleaned = 0
        wt_base = WORKTREE_BASE / ".worktrees"
        if not wt_base.exists():
            return 0

        for entry in wt_base.iterdir():
            if not entry.is_dir():
                continue
            try:
                age_hours = (time.time() - entry.stat().st_mtime) / 3600
                if age_hours > max_age_hours:
                    shutil.rmtree(entry, ignore_errors=True)
                    cleaned += 1
                    logger.info("Stale Worktree entfernt: %s (%.1fh alt)", entry.name, age_hours)
            except OSError:
                continue

        if cleaned:
            logger.info("Worktree cleanup: %d verwaiste Worktrees entfernt", cleaned)
        return cleaned

    def get_worktree(self, task_id: str) -> WorktreeInfo | None:
        """Gibt den aktiven Worktree für einen Task zurück."""
        return self._active.get(task_id)

    def list_active(self) -> list[dict]:
        """Alle aktiven Worktrees."""
        return [
            {
                "task_id": wt.task_id,
                "worker_id": wt.worker_id,
                "path": str(wt.path),
                "branch": wt.branch,
                "age_seconds": time.monotonic() - wt.created_at,
            }
            for wt in self._active.values()
        ]


# Globale Singleton-Instanz
worktree_manager = WorktreeManager()
