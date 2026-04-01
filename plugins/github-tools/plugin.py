"""
github-tools Plugin — GitHub API als Agent-Tools.

Funktioniert ohne Token für Public Repos.
Optional: GitHub Token in /etc/hydrahive/github_token.json für höhere Rate-Limits und Private Repos.

Tools:
  - github_repo_info:  Repo-Details (Stars, Forks, Language, Description)
  - github_read_file:  Datei aus einem Repo lesen
  - github_list_files: Verzeichnis-Inhalt eines Repos listen
  - github_issues:     Issues auflisten (offen/geschlossen)
  - github_search:     Code oder Repos durchsuchen
  - github_commits:    Letzte Commits eines Repos
"""
import json
import urllib.request
import urllib.error
from pathlib import Path


def _get_token() -> str:
    """Liest optionalen GitHub Token aus der HydraHive-Config."""
    try:
        cfg = json.loads(Path("/etc/hydrahive/github_token.json").read_text())
        return cfg.get("token", "")
    except Exception:
        return ""


def _github_api(path: str, timeout: int = 15) -> dict | list | str:
    """GitHub API v3 Call."""
    url = f"https://api.github.com{path}" if path.startswith("/") else path
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "HydraHive/1.0",
    }
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return f"GitHub API Fehler {e.code}: {body[:300]}"
    except Exception as e:
        return f"Fehler: {e}"


def _github_raw(owner: str, repo: str, path: str, ref: str = "main") -> str:
    """Rohe Datei von GitHub laden."""
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    headers = {"User-Agent": "HydraHive/1.0"}
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return f"Fehler {e.code}: Datei nicht gefunden oder kein Zugriff"
    except Exception as e:
        return f"Fehler: {e}"


def _parse_repo(repo_str: str) -> tuple[str, str]:
    """'owner/repo' oder 'https://github.com/owner/repo' → (owner, repo)."""
    repo_str = repo_str.strip().rstrip("/")
    if "github.com/" in repo_str:
        parts = repo_str.split("github.com/")[1].split("/")
        return parts[0], parts[1] if len(parts) > 1 else ""
    parts = repo_str.split("/")
    if len(parts) == 2:
        return parts[0], parts[1]
    return repo_str, ""


def register(api):

    @api.tool(
        tool_id="github_repo_info",
        description="GitHub Repo-Details abrufen: Stars, Forks, Sprache, Beschreibung, Lizenz, letzte Aktivität. Akzeptiert 'owner/repo' oder volle GitHub-URL.",
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (z.B. 'hydrahive/hydrahive' oder GitHub-URL)"},
            },
            "required": ["repo"],
        },
    )
    def github_repo_info(repo: str, **_) -> str:
        owner, name = _parse_repo(repo)
        if not name:
            return "Ungültiges Format. Nutze 'owner/repo' oder GitHub-URL."
        data = _github_api(f"/repos/{owner}/{name}")
        if isinstance(data, str):
            return data
        lines = [
            f"**{data.get('full_name', '')}**",
            f"Beschreibung: {data.get('description', '—')}",
            f"Sprache: {data.get('language', '—')}",
            f"Stars: {data.get('stargazers_count', 0)} | Forks: {data.get('forks_count', 0)} | Issues: {data.get('open_issues_count', 0)}",
            f"Lizenz: {data.get('license', {}).get('name', '—') if data.get('license') else '—'}",
            f"Default Branch: {data.get('default_branch', 'main')}",
            f"Erstellt: {data.get('created_at', '—')} | Letzter Push: {data.get('pushed_at', '—')}",
            f"URL: {data.get('html_url', '')}",
        ]
        return "\n".join(lines)

    @api.tool(
        tool_id="github_read_file",
        description="Eine Datei aus einem GitHub-Repo lesen. Gibt den Inhalt als Text zurück. Akzeptiert 'owner/repo' oder volle GitHub-URL.",
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (z.B. 'hydrahive/hydrahive')"},
                "path": {"type": "string", "description": "Dateipfad im Repo (z.B. 'README.md' oder 'src/main.py')"},
                "branch": {"type": "string", "description": "Branch (default: main)"},
            },
            "required": ["repo", "path"],
        },
    )
    def github_read_file(repo: str, path: str, branch: str = "main", **_) -> str:
        owner, name = _parse_repo(repo)
        if not name:
            return "Ungültiges Format."
        content = _github_raw(owner, name, path.lstrip("/"), branch)
        if len(content) > 15000:
            return content[:15000] + f"\n\n... [gekürzt, {len(content)} Zeichen insgesamt]"
        return content

    @api.tool(
        tool_id="github_list_files",
        description="Verzeichnisinhalt eines GitHub-Repos anzeigen. Zeigt Dateien und Ordner mit Größe.",
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (z.B. 'hydrahive/hydrahive')"},
                "path": {"type": "string", "description": "Pfad im Repo (default: Root)"},
                "branch": {"type": "string", "description": "Branch (default: main)"},
            },
            "required": ["repo"],
        },
    )
    def github_list_files(repo: str, path: str = "", branch: str = "main", **_) -> str:
        owner, name = _parse_repo(repo)
        if not name:
            return "Ungültiges Format."
        ref_param = f"?ref={branch}" if branch != "main" else ""
        data = _github_api(f"/repos/{owner}/{name}/contents/{path.lstrip('/')}{ref_param}")
        if isinstance(data, str):
            return data
        if not isinstance(data, list):
            return "Kein Verzeichnis oder Fehler."
        lines = []
        for item in sorted(data, key=lambda x: (x.get("type") != "dir", x.get("name", ""))):
            t = "📁" if item.get("type") == "dir" else "📄"
            size = f" ({item.get('size', 0):,} B)" if item.get("type") == "file" else ""
            lines.append(f"{t} {item.get('name', '?')}{size}")
        return "\n".join(lines) or "Leeres Verzeichnis"

    @api.tool(
        tool_id="github_issues",
        description="Issues eines GitHub-Repos auflisten. Zeigt Titel, Labels, Autor und Datum.",
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (z.B. 'hydrahive/hydrahive')"},
                "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Filter: open, closed, all (default: open)"},
                "limit": {"type": "integer", "description": "Anzahl (default: 20, max: 100)"},
            },
            "required": ["repo"],
        },
    )
    def github_issues(repo: str, state: str = "open", limit: int = 20, **_) -> str:
        owner, name = _parse_repo(repo)
        if not name:
            return "Ungültiges Format."
        limit = min(max(1, limit), 100)
        data = _github_api(f"/repos/{owner}/{name}/issues?state={state}&per_page={limit}&sort=updated&direction=desc")
        if isinstance(data, str):
            return data
        if not data:
            return f"Keine {state} Issues gefunden."
        lines = []
        for issue in data:
            if issue.get("pull_request"):
                continue  # PRs rausfiltern
            labels = ", ".join(l.get("name", "") for l in issue.get("labels", []))
            labels_str = f" [{labels}]" if labels else ""
            lines.append(f"#{issue.get('number', '?')} {issue.get('title', '?')}{labels_str} — @{issue.get('user', {}).get('login', '?')} ({issue.get('updated_at', '')[:10]})")
        return "\n".join(lines) or "Keine Issues gefunden."

    @api.tool(
        tool_id="github_search",
        description="GitHub durchsuchen — Code, Repos oder Issues. Nutze dieses Tool wenn nach bestimmtem Code, Repos oder Themen gesucht werden soll.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchanfrage"},
                "type": {"type": "string", "enum": ["repositories", "code", "issues"], "description": "Suchtyp (default: repositories)"},
                "limit": {"type": "integer", "description": "Anzahl (default: 10)"},
            },
            "required": ["query"],
        },
    )
    def github_search(query: str, type: str = "repositories", limit: int = 10, **_) -> str:
        limit = min(max(1, limit), 30)
        encoded = urllib.request.quote(query)
        data = _github_api(f"/search/{type}?q={encoded}&per_page={limit}")
        if isinstance(data, str):
            return data
        items = data.get("items", [])
        if not items:
            return f"Keine Ergebnisse für '{query}' ({type})."
        lines = [f"Ergebnisse: {data.get('total_count', '?')} (zeige {len(items)})", ""]
        for item in items:
            if type == "repositories":
                lines.append(f"⭐ {item.get('stargazers_count', 0)} {item.get('full_name', '?')} — {item.get('description', '')[:100]}")
            elif type == "code":
                lines.append(f"📄 {item.get('repository', {}).get('full_name', '?')}/{item.get('path', '?')}")
            elif type == "issues":
                lines.append(f"#{item.get('number', '?')} {item.get('title', '?')} — {item.get('repository_url', '').split('/')[-1]}")
        return "\n".join(lines)

    @api.tool(
        tool_id="github_commits",
        description="Letzte Commits eines GitHub-Repos anzeigen. Zeigt Autor, Datum und Commit-Message.",
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (z.B. 'hydrahive/hydrahive')"},
                "branch": {"type": "string", "description": "Branch (default: main)"},
                "limit": {"type": "integer", "description": "Anzahl (default: 10)"},
            },
            "required": ["repo"],
        },
    )
    def github_commits(repo: str, branch: str = "main", limit: int = 10, **_) -> str:
        owner, name = _parse_repo(repo)
        if not name:
            return "Ungültiges Format."
        limit = min(max(1, limit), 50)
        data = _github_api(f"/repos/{owner}/{name}/commits?sha={branch}&per_page={limit}")
        if isinstance(data, str):
            return data
        lines = []
        for c in data:
            commit = c.get("commit", {})
            author = commit.get("author", {}).get("name", "?")
            date = commit.get("author", {}).get("date", "")[:10]
            msg = commit.get("message", "").split("\n")[0][:100]
            sha = c.get("sha", "")[:7]
            lines.append(f"{sha} {date} {author}: {msg}")
        return "\n".join(lines) or "Keine Commits gefunden."
