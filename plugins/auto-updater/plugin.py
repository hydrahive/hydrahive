"""
auto-updater Plugin — GitHub Release-Check + Update-Trigger.

Tools:
  - check_update:   Prüft ob eine neue Version auf GitHub verfügbar ist
  - show_changelog: Zeigt die Release Notes der neuesten Version
  - run_update:     Führt das Update-Script aus (mit Bestätigung)
  - current_version: Zeigt die aktuell installierte Version
"""
import json
import subprocess
import urllib.request
from pathlib import Path


GITHUB_REPO = "hydrahive/hydrahive"
VERSION_FILE = Path("/opt/hydrahive/VERSION")
UPDATE_SCRIPT = Path("/opt/hydrahive/update.sh")


def _github_api(path: str) -> dict | list | str:
    """GitHub API Call."""
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "HydraHive-AutoUpdater/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return f"GitHub API Fehler: {e}"


def _get_current_version() -> str:
    """Aktuelle Version lesen."""
    # Aus VERSION-Datei
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    # Fallback: Git
    try:
        r = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=5,
            cwd="/opt/hydrahive",
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    # Fallback: letzter Commit
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%h %ad %s", "--date=short"],
            capture_output=True, text=True, timeout=5,
            cwd="/opt/hydrahive",
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "unbekannt"


def _get_latest_commits(count: int = 10) -> str:
    """Letzte Commits vom GitHub Repo."""
    data = _github_api(f"/repos/{GITHUB_REPO}/commits?per_page={count}")
    if isinstance(data, str):
        return data
    lines = []
    for c in data:
        sha = c.get("sha", "")[:7]
        msg = c.get("commit", {}).get("message", "").split("\n")[0][:80]
        date = c.get("commit", {}).get("author", {}).get("date", "")[:10]
        author = c.get("commit", {}).get("author", {}).get("name", "")
        lines.append(f"{sha} {date} {author}: {msg}")
    return "\n".join(lines)


def register(api):

    @api.tool(
        tool_id="current_version",
        description="Zeigt die aktuell installierte HydraHive-Version.",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    def current_version(**_) -> str:
        version = _get_current_version()
        return f"Installierte Version: {version}"

    @api.tool(
        tool_id="check_update",
        description="Prüft ob eine neue HydraHive-Version auf GitHub verfügbar ist. Vergleicht lokale Version mit den neuesten Commits auf GitHub.",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    def check_update(**_) -> str:
        current = _get_current_version()
        lines = [f"Installiert: {current}", ""]

        # Letzter lokaler Commit
        local_sha = ""
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd="/opt/hydrahive",
            )
            if r.returncode == 0:
                local_sha = r.stdout.strip()
        except Exception:
            pass

        # Remote Commits
        data = _github_api(f"/repos/{GITHUB_REPO}/commits?per_page=5")
        if isinstance(data, str):
            return f"{lines[0]}\n\nGitHub nicht erreichbar: {data}"

        remote_sha = data[0].get("sha", "")[:7] if data else ""

        if local_sha and remote_sha:
            if local_sha == remote_sha:
                lines.append("Status: Aktuell — keine neuen Updates verfügbar.")
            else:
                # Zähle wie viele Commits wir hinterher sind
                new_commits = []
                for c in data:
                    if c.get("sha", "")[:7] == local_sha:
                        break
                    msg = c.get("commit", {}).get("message", "").split("\n")[0][:60]
                    new_commits.append(f"  {c.get('sha','')[:7]} {msg}")
                if new_commits:
                    lines.append(f"Status: Update verfügbar! {len(new_commits)} neue Commit(s):")
                    lines.extend(new_commits)
                else:
                    lines.append(f"Status: Update verfügbar (lokal: {local_sha}, remote: {remote_sha})")
                lines.append("")
                lines.append("Nutze 'run_update' um das Update zu starten.")
        else:
            lines.append(f"Remote: {remote_sha}")
            lines.append("Konnte lokalen Stand nicht ermitteln.")

        return "\n".join(lines)

    @api.tool(
        tool_id="show_changelog",
        description="Zeigt die letzten Änderungen (Commits) auf GitHub. Nützlich um zu sehen was ein Update bringt.",
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Anzahl Commits (default: 15)"},
            },
            "required": [],
        },
    )
    def show_changelog(count: int = 15, **_) -> str:
        count = min(max(1, count), 50)
        result = _get_latest_commits(count)
        if not result:
            return "Keine Commits gefunden"
        return f"Letzte {count} Commits auf GitHub ({GITHUB_REPO}):\n\n{result}"

    @api.tool(
        tool_id="run_update",
        description="Führt das HydraHive-Update aus. Zieht den neuesten Code von GitHub, baut die Console neu und startet den Core neu. ACHTUNG: Der Server wird kurz nicht erreichbar sein!",
        parameters={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Muss true sein um das Update zu starten"},
            },
            "required": ["confirm"],
        },
    )
    def run_update(confirm: bool = False, **_) -> str:
        if not confirm:
            return "Update NICHT gestartet. Setze confirm=true um das Update wirklich auszuführen.\n\nHinweis: Der Server wird während des Updates kurz nicht erreichbar sein."

        if not UPDATE_SCRIPT.exists():
            return f"Update-Script nicht gefunden: {UPDATE_SCRIPT}"

        try:
            # Update im Hintergrund starten (non-blocking, da der Core neugestartet wird)
            r = subprocess.run(
                ["sudo", "bash", str(UPDATE_SCRIPT)],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                return f"Update fehlgeschlagen:\n{r.stderr[-1000:]}"
            return f"Update erfolgreich abgeschlossen!\n\n{r.stdout[-500:]}"
        except subprocess.TimeoutExpired:
            return "Update Timeout (10 Minuten). Prüfe den Server-Status manuell."
        except Exception as e:
            return f"Update-Fehler: {e}"
