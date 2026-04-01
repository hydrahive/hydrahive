"""
git-extended Plugin — Erweiterte Git-Operationen als Agent-Tools.

Ergänzt die Core-Tools (git_status, git_diff, git_commit, git_push) um:
  - git_clone:     Repository klonen
  - git_pull:      Remote-Änderungen holen
  - git_branch:    Branches listen, erstellen, wechseln
  - git_checkout:  Branch oder Datei auschecken
  - git_log:       Commit-History anzeigen
  - git_stash:     Änderungen zwischenspeichern / wiederherstellen
  - git_stats:     Repository-Statistiken (Commits, Contributors, Dateien)
  - git_blame:     Wer hat welche Zeile geschrieben
  - git_tag:       Tags listen und erstellen
"""
import subprocess
import os


PROJECTS_DIR = "/projects"


def _run(cmd: list[str], cwd: str = "", timeout: int = 60) -> tuple[int, str, str]:
    """Git-Befehl ausführen."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=cwd or None,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return 1, "", "git nicht gefunden"
    except subprocess.TimeoutExpired:
        return 1, "", f"Timeout ({timeout}s)"


def _resolve_cwd(project_id: str = "", path: str = "") -> str:
    """Arbeitsverzeichnis bestimmen."""
    if path and os.path.isdir(path):
        return path
    if project_id:
        p = os.path.join(PROJECTS_DIR, project_id)
        if os.path.isdir(p):
            return p
    return ""


def register(api):

    @api.tool(
        tool_id="git_clone",
        description="Klont ein Git-Repository. Unterstützt GitHub, GitLab, Gitea und beliebige URLs. Für private Repos Token in der URL: https://TOKEN@github.com/owner/repo.git",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Repository-URL (z.B. https://github.com/owner/repo.git)"},
                "target": {"type": "string", "description": "Zielverzeichnis (z.B. /projects/mein-projekt/repo)"},
                "branch": {"type": "string", "description": "Branch (default: Standard-Branch des Repos)"},
                "depth": {"type": "integer", "description": "Shallow clone Tiefe (0 = vollständig, default: 0)"},
            },
            "required": ["url", "target"],
        },
    )
    def git_clone(url: str, target: str, branch: str = "", depth: int = 0, **_) -> str:
        cmd = ["git", "clone"]
        if branch:
            cmd += ["--branch", branch, "--single-branch"]
        if depth and depth > 0:
            cmd += ["--depth", str(depth)]
        cmd += [url, target]
        rc, out, err = _run(cmd, timeout=300)
        if rc != 0:
            return f"Clone fehlgeschlagen: {err}"
        return f"Repository geklont nach {target}\n{out}\n{err}".strip()

    @api.tool(
        tool_id="git_pull",
        description="Holt und merged Remote-Änderungen (git pull). Nutze dieses Tool um ein Repo auf den neuesten Stand zu bringen.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Projekt-ID (Verzeichnis unter /projects/)"},
                "path": {"type": "string", "description": "Alternativ: direkter Pfad zum Repo"},
                "remote": {"type": "string", "description": "Remote-Name (default: origin)"},
                "branch": {"type": "string", "description": "Branch (default: aktueller Branch)"},
            },
            "required": [],
        },
    )
    def git_pull(project_id: str = "", path: str = "", remote: str = "origin", branch: str = "", **_) -> str:
        cwd = _resolve_cwd(project_id, path)
        if not cwd:
            return "Kein gültiges Verzeichnis. Gib project_id oder path an."
        cmd = ["git", "pull", remote]
        if branch:
            cmd.append(branch)
        rc, out, err = _run(cmd, cwd=cwd)
        if rc != 0:
            return f"Pull fehlgeschlagen: {err}"
        return out or "Bereits aktuell"

    @api.tool(
        tool_id="git_branch",
        description="Git Branches verwalten — auflisten, erstellen oder löschen.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Projekt-ID"},
                "path": {"type": "string", "description": "Alternativ: direkter Pfad"},
                "action": {"type": "string", "enum": ["list", "create", "delete"], "description": "Aktion (default: list)"},
                "name": {"type": "string", "description": "Branch-Name (für create/delete)"},
                "all": {"type": "boolean", "description": "Auch Remote-Branches zeigen (für list)"},
            },
            "required": [],
        },
    )
    def git_branch(project_id: str = "", path: str = "", action: str = "list", name: str = "", all: bool = False, **_) -> str:
        cwd = _resolve_cwd(project_id, path)
        if not cwd:
            return "Kein gültiges Verzeichnis."
        if action == "list":
            cmd = ["git", "branch"]
            if all:
                cmd.append("-a")
            cmd.append("-v")
            rc, out, err = _run(cmd, cwd=cwd)
            return out if rc == 0 else f"Fehler: {err}"
        elif action == "create":
            if not name:
                return "Branch-Name fehlt."
            rc, out, err = _run(["git", "checkout", "-b", name], cwd=cwd)
            return f"Branch '{name}' erstellt und ausgecheckt" if rc == 0 else f"Fehler: {err}"
        elif action == "delete":
            if not name:
                return "Branch-Name fehlt."
            rc, out, err = _run(["git", "branch", "-d", name], cwd=cwd)
            return f"Branch '{name}' gelöscht" if rc == 0 else f"Fehler: {err}"
        return "Unbekannte Aktion. Nutze: list, create, delete"

    @api.tool(
        tool_id="git_checkout",
        description="Wechselt den Branch oder stellt eine Datei wieder her.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Projekt-ID"},
                "path": {"type": "string", "description": "Alternativ: direkter Pfad"},
                "target": {"type": "string", "description": "Branch-Name oder Datei-Pfad"},
            },
            "required": ["target"],
        },
    )
    def git_checkout(target: str, project_id: str = "", path: str = "", **_) -> str:
        cwd = _resolve_cwd(project_id, path)
        if not cwd:
            return "Kein gültiges Verzeichnis."
        rc, out, err = _run(["git", "checkout", target], cwd=cwd)
        if rc != 0:
            return f"Checkout fehlgeschlagen: {err}"
        return f"Gewechselt zu: {target}\n{out}\n{err}".strip()

    @api.tool(
        tool_id="git_log",
        description="Zeigt die Commit-History eines Repositories. Nützlich um zu sehen was sich geändert hat.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Projekt-ID"},
                "path": {"type": "string", "description": "Alternativ: direkter Pfad"},
                "limit": {"type": "integer", "description": "Anzahl Commits (default: 20)"},
                "oneline": {"type": "boolean", "description": "Kompakte Ausgabe (default: true)"},
                "file": {"type": "string", "description": "Nur Commits für diese Datei zeigen (optional)"},
            },
            "required": [],
        },
    )
    def git_log(project_id: str = "", path: str = "", limit: int = 20, oneline: bool = True, file: str = "", **_) -> str:
        cwd = _resolve_cwd(project_id, path)
        if not cwd:
            return "Kein gültiges Verzeichnis."
        limit = min(max(1, limit), 100)
        if oneline:
            cmd = ["git", "log", f"--max-count={limit}", "--oneline", "--decorate"]
        else:
            cmd = ["git", "log", f"--max-count={limit}", "--format=%h %ad %an: %s", "--date=short"]
        if file:
            cmd += ["--", file]
        rc, out, err = _run(cmd, cwd=cwd)
        return out if rc == 0 else f"Fehler: {err}"

    @api.tool(
        tool_id="git_stash",
        description="Git Stash — Änderungen zwischenspeichern oder wiederherstellen.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Projekt-ID"},
                "path": {"type": "string", "description": "Alternativ: direkter Pfad"},
                "action": {"type": "string", "enum": ["save", "pop", "list", "drop"], "description": "Aktion (default: save)"},
                "message": {"type": "string", "description": "Stash-Nachricht (für save)"},
            },
            "required": [],
        },
    )
    def git_stash(project_id: str = "", path: str = "", action: str = "save", message: str = "", **_) -> str:
        cwd = _resolve_cwd(project_id, path)
        if not cwd:
            return "Kein gültiges Verzeichnis."
        if action == "save":
            cmd = ["git", "stash", "push"]
            if message:
                cmd += ["-m", message]
        elif action == "pop":
            cmd = ["git", "stash", "pop"]
        elif action == "list":
            cmd = ["git", "stash", "list"]
        elif action == "drop":
            cmd = ["git", "stash", "drop"]
        else:
            return "Unbekannte Aktion. Nutze: save, pop, list, drop"
        rc, out, err = _run(cmd, cwd=cwd)
        return out if rc == 0 else f"Fehler: {err}"

    @api.tool(
        tool_id="git_stats",
        description="Repository-Statistiken: Commits-Anzahl, Contributors, Dateien, Branches, Tags, Repo-Größe.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Projekt-ID"},
                "path": {"type": "string", "description": "Alternativ: direkter Pfad"},
            },
            "required": [],
        },
    )
    def git_stats(project_id: str = "", path: str = "", **_) -> str:
        cwd = _resolve_cwd(project_id, path)
        if not cwd:
            return "Kein gültiges Verzeichnis."
        lines = []

        # Commits
        rc, out, _ = _run(["git", "rev-list", "--count", "HEAD"], cwd=cwd)
        if rc == 0:
            lines.append(f"Commits: {out}")

        # Contributors
        rc, out, _ = _run(["git", "shortlog", "-sn", "--all", "HEAD"], cwd=cwd)
        if rc == 0:
            contribs = [l.strip() for l in out.splitlines() if l.strip()]
            lines.append(f"Contributors: {len(contribs)}")
            for c in contribs[:10]:
                lines.append(f"  {c}")
            if len(contribs) > 10:
                lines.append(f"  ... und {len(contribs) - 10} weitere")

        # Branches
        rc, out, _ = _run(["git", "branch", "-a"], cwd=cwd)
        if rc == 0:
            branches = [l.strip() for l in out.splitlines() if l.strip()]
            lines.append(f"Branches: {len(branches)}")

        # Tags
        rc, out, _ = _run(["git", "tag", "-l"], cwd=cwd)
        if rc == 0:
            tags = [l.strip() for l in out.splitlines() if l.strip()]
            lines.append(f"Tags: {len(tags)}")

        # Dateien
        rc, out, _ = _run(["git", "ls-files"], cwd=cwd)
        if rc == 0:
            files = out.splitlines()
            lines.append(f"Dateien (tracked): {len(files)}")

        # Repo-Größe
        rc, out, _ = _run(["du", "-sh", os.path.join(cwd, ".git")], cwd=cwd)
        if rc == 0:
            lines.append(f"Git-Verzeichnis: {out.split()[0]}")

        # Letzter Commit
        rc, out, _ = _run(["git", "log", "-1", "--format=%h %ad %an: %s", "--date=short"], cwd=cwd)
        if rc == 0:
            lines.append(f"Letzter Commit: {out}")

        return "\n".join(lines) or "Keine Git-Statistiken verfügbar"

    @api.tool(
        tool_id="git_blame",
        description="Zeigt wer welche Zeile einer Datei zuletzt geändert hat (git blame).",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Projekt-ID"},
                "path": {"type": "string", "description": "Alternativ: direkter Pfad zum Repo"},
                "file": {"type": "string", "description": "Datei die geblamed werden soll"},
                "lines": {"type": "string", "description": "Zeilenbereich z.B. '10,20' (optional)"},
            },
            "required": ["file"],
        },
    )
    def git_blame(file: str, project_id: str = "", path: str = "", lines: str = "", **_) -> str:
        cwd = _resolve_cwd(project_id, path)
        if not cwd:
            return "Kein gültiges Verzeichnis."
        cmd = ["git", "blame", "--date=short"]
        if lines:
            cmd += [f"-L{lines}"]
        cmd.append(file)
        rc, out, err = _run(cmd, cwd=cwd)
        if rc != 0:
            return f"Fehler: {err}"
        # Kürzen wenn zu lang
        if len(out) > 10000:
            out = out[:10000] + "\n... (gekürzt)"
        return out

    @api.tool(
        tool_id="git_tag",
        description="Git Tags verwalten — auflisten oder erstellen.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Projekt-ID"},
                "path": {"type": "string", "description": "Alternativ: direkter Pfad"},
                "action": {"type": "string", "enum": ["list", "create"], "description": "Aktion (default: list)"},
                "name": {"type": "string", "description": "Tag-Name (für create)"},
                "message": {"type": "string", "description": "Tag-Nachricht (für create, optional)"},
            },
            "required": [],
        },
    )
    def git_tag(project_id: str = "", path: str = "", action: str = "list", name: str = "", message: str = "", **_) -> str:
        cwd = _resolve_cwd(project_id, path)
        if not cwd:
            return "Kein gültiges Verzeichnis."
        if action == "list":
            rc, out, err = _run(["git", "tag", "-l", "-n1"], cwd=cwd)
            return out if rc == 0 else f"Fehler: {err}"
        elif action == "create":
            if not name:
                return "Tag-Name fehlt."
            cmd = ["git", "tag"]
            if message:
                cmd += ["-a", name, "-m", message]
            else:
                cmd.append(name)
            rc, out, err = _run(cmd, cwd=cwd)
            return f"Tag '{name}' erstellt" if rc == 0 else f"Fehler: {err}"
        return "Unbekannte Aktion. Nutze: list, create"
