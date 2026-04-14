"""
tools_git.py — Native Git-Tools (#621, #635 SSOT)

Ersetzt shell_exec(git ...) Improvisation durch deferred, server-side
authenticated Git-Operationen. Agent sieht keine Tokens, kann keine
Gitea-Hooks manipulieren.

#635 Workspace-Modell:
    workspace_root(project_id) IST der git-Working-Tree.
    Ein Projekt = ein Repo. Kein workspace-Parameter mehr in den Tool-
    Schemas — die git_*-Tools operieren immer auf workspace_root(project_id).
    Damit sehen file_*, shell_exec und git_* exakt denselben Tree.
"""
from __future__ import annotations

import json as _json
import logging
import re
from pathlib import Path
from typing import Any

from .gitea import GiteaClient, _load_config
from .tool_registry import BaseTool, registry, workspace_root

logger = logging.getLogger(__name__)

# Erlaubte Branch-/Repo-Namen: Alphanumerisch + . _ - ; keine Pfad-Traversal.
# Muss mit Buchstabe/Ziffer beginnen (kein . → keine .., kein .hidden).
_SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def _is_safe(name: str) -> bool:
    if not name or not _SAFE_NAME.match(name):
        return False
    # Explizit .. anywhere blockieren (auch wenn regex passt für "a..b")
    if ".." in name:
        return False
    return True


def _project_workspace(project_id: str) -> Path:
    """#635: Working-Tree eines Projekts. Identisch mit workspace_root.

    Dünner Wrapper, damit lokal in tools_git.py klar ist: hier wird der
    GEMEINSAME Workspace genutzt, kein Sub-Tree, kein Sonderpfad.
    """
    if not project_id or not _is_safe(project_id):
        raise ValueError(f"Unsicherer project_id: '{project_id}'")
    return workspace_root(project_id)


def _parse_remote(repo: str) -> tuple[str, str, str]:
    """
    Gibt (provider, owner, name) zurück.
    Aktuell nur Gitea-internal. GitHub kommt in Phase 2 von #621.
    """
    cfg = _load_config()
    default_owner = cfg.get("org", "hydrahive")
    if "://" in repo:
        # volle URL — für Phase 1 nicht unterstützt
        raise ValueError("Volle URLs nicht unterstützt — nutze 'owner/name' oder 'name'")
    parts = repo.strip("/").split("/")
    if len(parts) == 1:
        return "gitea", default_owner, parts[0]
    if len(parts) == 2:
        return "gitea", parts[0], parts[1]
    raise ValueError(f"Unparsbare repo-Referenz: '{repo}'")


async def _run_git(args: list[str], cwd: Path, with_auth: bool = False) -> tuple[str, str, int]:
    if with_auth:
        cfg = _load_config()
        return await GiteaClient._git(args, cwd, token=cfg["token"], username=cfg.get("org", "hydrahive"))
    return await GiteaClient._git(args, cwd)


class _GitToolBase(BaseTool):
    @property
    def always_loaded(self) -> bool: return False
    @property
    def category(self) -> str: return "git"


# =========================================================================
# git_clone
# =========================================================================

class GitCloneTool(_GitToolBase):
    @property
    def id(self) -> str: return "git_clone"
    @property
    def name(self) -> str: return "Git-Repo klonen"
    @property
    def description(self) -> str:
        return (
            "Klont ein Repo direkt in den Project-Workspace (#635: ein Projekt = "
            "ein Repo). repo als 'owner/name' oder nur 'name'. Auth wird "
            "server-side gesetzt — kein Token in der Shell."
        )

    @property
    def is_read_only(self) -> bool: return False
    @property
    def semantic_tags(self) -> list[str]:
        return ["git", "clone", "checkout", "download", "repo"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo":   {"type": "string", "description": "'owner/name' oder 'name'"},
                "branch": {"type": "string", "description": "Optional — spezifischer Branch"},
                "depth":  {"type": "integer", "description": "Optional — shallow clone (z.B. 1 für nur letzten Commit)"},
            },
            "required": ["repo"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        repo: str, branch: str = "", depth: int = 0,
        **kwargs,
    ) -> dict:
        try:
            _, owner, name = _parse_remote(repo)
            ws_path = _project_workspace(project_id)
            cfg = _load_config()
            clone_url = f"{cfg['url']}/{owner}/{name}.git"

            # #635 Vorbedingungs-Check:
            # - bereits .git → idempotent "bereits geklont"
            # - leerer/nicht-existierender Workspace → klonen
            # - non-empty ohne .git → Konflikt, NICHT überschreiben
            if (ws_path / ".git").exists():
                return {"ok": True, "workspace": str(ws_path), "note": "bereits geklont"}
            if ws_path.exists():
                non_empty = any(ws_path.iterdir())
                if non_empty:
                    return {
                        "error": (
                            f"Workspace '{ws_path}' ist nicht leer und enthält kein .git. "
                            "git_clone würde existierende Dateien überschreiben — abgelehnt."
                        ),
                    }

            # Workspace-Parent muss existieren damit git clone schreiben kann
            ws_path.parent.mkdir(parents=True, exist_ok=True)

            args = ["clone"]
            if branch:
                if not _is_safe(branch):
                    return {"error": f"Unsicherer Branch-Name: '{branch}'"}
                args += ["--branch", branch]
            if depth and int(depth) > 0:
                args += ["--depth", str(int(depth))]
            args += [clone_url, str(ws_path)]

            stdout, stderr, rc = await GiteaClient._git(
                args, ws_path.parent,
                token=cfg["token"], username=cfg.get("org", "hydrahive"),
            )
            if rc != 0:
                return {"error": f"git clone rc={rc}", "stderr": stderr[:500]}
            return {
                "ok": True,
                "workspace": str(ws_path),
                "repo": f"{owner}/{name}",
                "branch": branch or "default",
            }
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


# =========================================================================
# git_status
# =========================================================================

class GitStatusTool(_GitToolBase):
    @property
    def id(self) -> str: return "git_status"
    @property
    def name(self) -> str: return "Git-Status"
    @property
    def description(self) -> str:
        return "Zeigt git status --porcelain für den Project-Workspace."

    @property
    def is_read_only(self) -> bool: return True
    @property
    def semantic_tags(self) -> list[str]:
        return ["git", "status", "changes", "dirty", "uncommitted"]

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(
        self, agent_id: str, project_id: str, **kwargs,
    ) -> dict:
        try:
            ws = _project_workspace(project_id)
            if not (ws / ".git").exists():
                return {"error": f"Workspace '{ws}' ist kein Git-Repo"}
            stdout, stderr, rc = await _run_git(["status", "--porcelain=v1", "-b"], ws)
            if rc != 0:
                return {"error": f"git status rc={rc}", "stderr": stderr[:400]}
            lines = stdout.splitlines()
            branch_line = lines[0] if lines else ""
            files = [l.strip() for l in lines[1:] if l.strip()]
            return {"ok": True, "branch": branch_line, "changes": files, "clean": len(files) == 0}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


# =========================================================================
# git_log
# =========================================================================

class GitLogTool(_GitToolBase):
    @property
    def id(self) -> str: return "git_log"
    @property
    def name(self) -> str: return "Git-Log"
    @property
    def description(self) -> str:
        return "Letzte N Commits des Project-Workspaces (oneline)."

    @property
    def is_read_only(self) -> bool: return True
    @property
    def semantic_tags(self) -> list[str]:
        return ["git", "log", "history", "commits", "blame"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "path":  {"type": "string", "description": "Optional — nur Commits die diese Datei/Ordner betreffen"},
            },
            "required": [],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        limit: int = 20, path: str = "", **kwargs,
    ) -> dict:
        try:
            ws = _project_workspace(project_id)
            limit = max(1, min(int(limit), 100))
            args = ["log", f"-{limit}", "--pretty=format:%h %an %ad %s", "--date=short"]
            if path:
                args += ["--", path]
            stdout, stderr, rc = await _run_git(args, ws)
            if rc != 0:
                return {"error": f"git log rc={rc}", "stderr": stderr[:400]}
            return {
                "ok": True,
                "commits": [l for l in stdout.splitlines() if l.strip()],
            }
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


# =========================================================================
# git_diff
# =========================================================================

class GitDiffTool(_GitToolBase):
    @property
    def id(self) -> str: return "git_diff"
    @property
    def name(self) -> str: return "Git-Diff"
    @property
    def description(self) -> str:
        return "Diff im Project-Workspace — unstaged, staged, oder beliebige Refs."

    @property
    def is_read_only(self) -> bool: return True
    @property
    def semantic_tags(self) -> list[str]:
        return ["git", "diff", "changes", "compare"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "staged": {"type": "boolean", "default": False},
                "ref_a":  {"type": "string", "description": "Optional Ref A"},
                "ref_b":  {"type": "string", "description": "Optional Ref B"},
            },
            "required": [],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        staged: bool = False, ref_a: str = "", ref_b: str = "",
        **kwargs,
    ) -> dict:
        try:
            ws = _project_workspace(project_id)
            args = ["diff"]
            if staged:
                args.append("--staged")
            if ref_a:
                args.append(ref_a)
            if ref_b:
                args.append(ref_b)
            stdout, stderr, rc = await _run_git(args, ws)
            if rc != 0:
                return {"error": f"git diff rc={rc}", "stderr": stderr[:400]}
            # Auf 8k Zeichen kürzen — Agenten sollen gezielt nachhaken
            if len(stdout) > 8000:
                stdout = stdout[:8000] + "\n…[Diff gekürzt — nutze git_diff mit ref_a/ref_b für Teilbereiche]"
            return {"ok": True, "diff": stdout}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


# =========================================================================
# git_commit_all
# =========================================================================

class GitCommitAllTool(_GitToolBase):
    @property
    def id(self) -> str: return "git_commit_all"
    @property
    def name(self) -> str: return "Git-Commit (alles)"
    @property
    def description(self) -> str:
        return (
            "git add -A + git commit -m MESSAGE im Project-Workspace. "
            "Author/Email server-seitig ('HydraHive Agent <agent@hydrahive.local>')."
        )

    @property
    def semantic_tags(self) -> list[str]:
        return ["git", "commit", "add", "save", "checkpoint"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message":     {"type": "string", "description": "Commit-Message (Pflicht)"},
                "allow_empty": {"type": "boolean", "default": False},
            },
            "required": ["message"],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        message: str, allow_empty: bool = False, **kwargs,
    ) -> dict:
        try:
            ws = _project_workspace(project_id)
            if not message or not message.strip():
                return {"error": "message darf nicht leer sein"}
            # 1) Add alles
            stdout, stderr, rc = await _run_git(["add", "-A"], ws)
            if rc != 0:
                return {"error": f"git add rc={rc}", "stderr": stderr[:300]}
            # 2) Commit
            args = ["commit", "-m", message]
            if allow_empty:
                args.append("--allow-empty")
            stdout, stderr, rc = await _run_git(args, ws)
            if rc != 0:
                if "nothing to commit" in stdout + stderr:
                    return {"ok": True, "note": "nothing to commit — workspace ist clean", "committed": False}
                return {"error": f"git commit rc={rc}", "stderr": stderr[:300], "stdout": stdout[:300]}
            # Kurze Info
            hash_out, _, _ = await _run_git(["rev-parse", "--short", "HEAD"], ws)
            return {"ok": True, "committed": True, "sha": hash_out.strip(), "summary": stdout.splitlines()[-1] if stdout.strip() else ""}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


# =========================================================================
# git_push
# =========================================================================

class GitPushTool(_GitToolBase):
    @property
    def id(self) -> str: return "git_push"
    @property
    def name(self) -> str: return "Git-Push"
    @property
    def description(self) -> str:
        return (
            "Pusht Commits des Project-Workspaces zum Remote. Token wird "
            "server-side injiziert — Pre-Receive-Hooks greifen normal."
        )

    @property
    def is_destructive(self) -> bool: return True
    @property
    def semantic_tags(self) -> list[str]:
        return ["git", "push", "upload", "publish", "remote"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "branch": {"type": "string", "description": "Optional — default: aktueller Branch"},
                "force":  {"type": "boolean", "default": False, "description": "Force-Push (mit-lease)"},
            },
            "required": [],
        }

    async def execute(
        self, agent_id: str, project_id: str,
        branch: str = "", force: bool = False, **kwargs,
    ) -> dict:
        try:
            ws = _project_workspace(project_id)
            args = ["push"]
            if force:
                args.append("--force-with-lease")
            args.append("origin")
            if branch:
                if not _is_safe(branch):
                    return {"error": f"Unsicherer Branch-Name: '{branch}'"}
                args.append(branch)
            stdout, stderr, rc = await _run_git(args, ws, with_auth=True)
            if rc != 0:
                return {"error": f"git push rc={rc}", "stderr": stderr[:500]}
            return {"ok": True, "output": (stdout + stderr).strip()[:500]}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


# =========================================================================
# git_pull
# =========================================================================

class GitPullTool(_GitToolBase):
    @property
    def id(self) -> str: return "git_pull"
    @property
    def name(self) -> str: return "Git-Pull"
    @property
    def description(self) -> str:
        return "Pull --rebase im Project-Workspace. Authenticated gegen Gitea."

    @property
    def semantic_tags(self) -> list[str]:
        return ["git", "pull", "fetch", "update", "sync"]

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(
        self, agent_id: str, project_id: str, **kwargs,
    ) -> dict:
        try:
            ws = _project_workspace(project_id)
            stdout, stderr, rc = await _run_git(["pull", "--rebase", "origin"], ws, with_auth=True)
            if rc != 0:
                return {"error": f"git pull rc={rc}", "stderr": stderr[:500]}
            return {"ok": True, "output": (stdout + stderr).strip()[:500]}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


# =========================================================================
# git_branch
# =========================================================================

class GitBranchTool(_GitToolBase):
    @property
    def id(self) -> str: return "git_branch"
    @property
    def name(self) -> str: return "Git-Branch verwalten"
    @property
    def description(self) -> str:
        return "list | create | delete | checkout für Branches im Project-Workspace."

    @property
    def semantic_tags(self) -> list[str]:
        return ["git", "branch", "checkout", "switch"]

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action":   {"type": "string", "enum": ["list", "create", "delete", "checkout"]},
                "name":     {"type": "string", "description": "Branch-Name bei create/delete/checkout"},
                "from_ref": {"type": "string", "description": "Bei create optional — Start-Ref (default: HEAD)"},
            },
            "required": ["action"],
        }

    async def execute(
        self, agent_id: str, project_id: str, action: str,
        name: str = "", from_ref: str = "", **kwargs,
    ) -> dict:
        try:
            ws = _project_workspace(project_id)
            if action == "list":
                stdout, stderr, rc = await _run_git(["branch", "-a"], ws)
                if rc != 0:
                    return {"error": f"git branch rc={rc}", "stderr": stderr[:300]}
                return {"ok": True, "branches": [l.strip("* ").strip() for l in stdout.splitlines() if l.strip()]}
            if not _is_safe(name):
                return {"error": f"Unsicherer/fehlender Branch-Name: '{name}'"}
            if action == "create":
                args = ["checkout", "-b", name]
                if from_ref:
                    if not _is_safe(from_ref):
                        return {"error": f"Unsicherer from_ref: '{from_ref}'"}
                    args.append(from_ref)
                stdout, stderr, rc = await _run_git(args, ws)
            elif action == "delete":
                stdout, stderr, rc = await _run_git(["branch", "-D", name], ws)
            elif action == "checkout":
                stdout, stderr, rc = await _run_git(["checkout", name], ws)
            else:
                return {"error": f"Unbekannte action: '{action}'"}
            if rc != 0:
                return {"error": f"git {action} rc={rc}", "stderr": stderr[:300]}
            return {"ok": True, "action": action, "branch": name, "output": (stdout + stderr).strip()[:300]}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


# =========================================================================
# Registration
# =========================================================================

registry.register(GitCloneTool())
registry.register(GitStatusTool())
registry.register(GitLogTool())
registry.register(GitDiffTool())
registry.register(GitCommitAllTool())
registry.register(GitPushTool())
registry.register(GitPullTool())
registry.register(GitBranchTool())
