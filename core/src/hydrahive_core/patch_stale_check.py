"""
patch_stale_check.py — Gate 2: Pre-Patch Stale-Check (#838).

Prueft VOR file_patch ob die Datei in einem Git-Repo liegt und ob upstream
neue Commits diese Datei beruehren. Wenn ja: Patch abort.

Verhindert das beobachtete Pattern (Boss arbeitet auf alter Baseline,
patcht bereits-gefixte Code-Stellen).

Nicht umgehbar:
- check_stale wird vom Tool aufgerufen, nicht vom LLM
- offline → skip mit indicator (nicht silent)
- non-git → skip mit indicator
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _git(args: list[str], cwd: Path, timeout: int = 15) -> tuple[int, str, str]:
    """Run git command. Returns (rc, stdout, stderr). Errors → (-1, "", err)."""
    git_bin = shutil.which("git")
    if not git_bin:
        return (-1, "", "git binary not found in PATH")
    try:
        # safe.directory damit wir in fremden Repos nicht blockiert werden
        cmd = [git_bin, "-c", f"safe.directory={cwd}"] + args
        r = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout,
        )
        return (r.returncode, r.stdout or "", r.stderr or "")
    except subprocess.TimeoutExpired:
        return (-1, "", f"git timeout after {timeout}s")
    except FileNotFoundError:
        return (-1, "", "git binary missing")


def find_repo_root(path: Path) -> Path | None:
    """Sucht aufwaerts nach .git-Dir. None wenn nicht gefunden."""
    try:
        p = path.resolve()
    except OSError:
        return None
    for ancestor in [p] + list(p.parents):
        if (ancestor / ".git").exists():
            return ancestor
    return None


def _current_branch(repo: Path) -> str | None:
    rc, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo, timeout=5)
    if rc == 0:
        b = out.strip()
        if b and b != "HEAD":
            return b
    return None


def check_stale(file_path: Path, *, fetch_timeout: int = 15) -> dict[str, Any]:
    """Sync version of stale check.

    Returns:
      {"stale": False} wenn alles aktuell oder Check uebersprungen
      {"stale": True, "behind_by": N, "commits": [...]} bei stale
      {"stale": False, "skipped": "<reason>"} bei skip
    """
    repo = find_repo_root(file_path)
    if repo is None:
        return {"stale": False, "skipped": "not_in_git_repo"}

    branch = _current_branch(repo)
    if branch is None:
        return {"stale": False, "skipped": "detached_head_or_no_branch"}

    # Fetch
    rc, _, ferr = _git(["fetch", "--quiet", "origin", branch], repo, timeout=fetch_timeout)
    if rc != 0:
        # Offline / network / permission — kein Block
        return {
            "stale": False,
            "skipped": "fetch_failed",
            "fetch_error": ferr.strip()[:200],
        }

    # Relativer Pfad fuer git
    try:
        rel = file_path.resolve().relative_to(repo.resolve())
    except ValueError:
        return {"stale": False, "skipped": "file_not_under_repo"}

    # Welche Commits sind in origin/<branch> aber noch nicht in HEAD die DIESE Datei beruehren?
    rc, log_out, log_err = _git(
        ["log", "--oneline", f"HEAD..origin/{branch}", "--", str(rel)],
        repo, timeout=10,
    )
    if rc != 0:
        return {
            "stale": False,
            "skipped": "log_failed",
            "log_error": log_err.strip()[:200],
        }

    commits = [line.strip() for line in log_out.splitlines() if line.strip()]
    if not commits:
        return {"stale": False}

    return {
        "stale": True,
        "behind_by": len(commits),
        "commits": commits[:10],  # max 10
        "branch": branch,
        "file": str(rel),
    }


async def check_stale_async(file_path: Path, *, fetch_timeout: int = 15) -> dict[str, Any]:
    """Async-Wrapper."""
    return await asyncio.to_thread(check_stale, file_path, fetch_timeout=fetch_timeout)


def stale_response(stale_result: dict[str, Any]) -> dict[str, Any]:
    """Formatiert stale-Result als Tool-Response-Dict mit hint."""
    commits_str = "\n  - ".join(stale_result.get("commits", []))
    return {
        "ok": False,
        "stale": stale_result,
        "error": "Pre-Patch-Gate (#838): Upstream hat neue Commits auf diese Datei",
        "hint": (
            f"Branch '{stale_result.get('branch')}' ist {stale_result.get('behind_by')} "
            f"Commits hinter origin fuer '{stale_result.get('file')}'.\n"
            f"Naechste Commits:\n  - {commits_str}\n"
            "Bitte git pull oder review zuerst, dann erneut patchen."
        ),
    }
