"""
gitea.py — Gitea-Integration für OctopOS

Lokales Gitea auf Port 3001 (intern) / 3002 (nginx proxy).
Jedes Projekt bekommt ein Git-Repo auf dem lokalen Gitea.
Agents nutzen git_commit/git_push/git_diff/git_status Tools.

Konfiguration in /etc/octopos/gitea_config.json:
{
  "url": "http://127.0.0.1:3001",
  "token": "<api-token>",
  "org": "octopos"
}
"""

import json
import logging
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

GITEA_CONFIG_FILE = Path("/etc/octopos/gitea_config.json")

_DEFAULT_CONFIG = {
    "url":   "http://127.0.0.1:3001",
    "token": "",   # wird aus /etc/octopos/gitea_config.json geladen
    "org":   "octopos",
}


def _load_config() -> dict:
    if GITEA_CONFIG_FILE.exists():
        try:
            return json.loads(GITEA_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Gitea-Config Lesefehler: %s — nutze Defaults", e)
    return _DEFAULT_CONFIG.copy()


class GiteaClient:
    """
    Async API-Client für lokales Gitea.
    Jede Methode öffnet eine eigene aiohttp-Session (stateless, kurze Calls).
    """

    def __init__(self, url: str, token: str, org: str) -> None:
        self.url   = url.rstrip("/")
        self.token = token
        self.org   = org

    @classmethod
    def from_config(cls) -> "GiteaClient":
        cfg = _load_config()
        return cls(cfg["url"], cfg["token"], cfg.get("org", "octopos"))

    def _headers(self) -> dict:
        return {
            "Authorization": f"token {self.token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    async def _get(self, path: str) -> dict | list:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.url}/api/v1{path}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _post(self, path: str, data: dict) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.url}/api/v1{path}",
                headers=self._headers(),
                json=data,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                if resp.status == 204:
                    return {}
                return await resp.json()

    async def _delete(self, path: str) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"{self.url}/api/v1{path}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()

    # ------------------------------------------------------------------ Repos

    async def repo_exists(self, project_id: str) -> bool:
        """Prüft ob ein Repo für dieses Projekt existiert."""
        try:
            await self._get(f"/repos/{self.org}/{project_id}")
            return True
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return False
            raise

    async def create_repo(self, project_id: str, description: str = "") -> dict:
        """
        Legt ein neues Repo für ein Projekt an.
        Repos liegen unter dem Owner (User oder Org — konfigurierbar, Standard: 'octopos').
        """
        data = {
            "name":           project_id,
            "description":    description,
            "private":        True,
            "auto_init":      True,
            "default_branch": "main",
        }
        try:
            # Erst als Org-Repo versuchen, dann als User-Repo
            try:
                result = await self._post(f"/orgs/{self.org}/repos", data)
            except aiohttp.ClientResponseError as e:
                if e.status in (404, 422):
                    # Org existiert nicht — unter dem User anlegen
                    result = await self._post("/user/repos", data)
                else:
                    raise
            logger.info("Gitea: Repo '%s/%s' angelegt", self.org, project_id)
            return result
        except aiohttp.ClientResponseError as e:
            if e.status == 409:
                logger.info("Gitea: Repo '%s/%s' existiert bereits", self.org, project_id)
                return await self._get(f"/repos/{self.org}/{project_id}")  # type: ignore[return-value]
            raise

    async def delete_repo(self, project_id: str) -> None:
        """Löscht das Repo eines Projekts."""
        try:
            await self._delete(f"/repos/{self.org}/{project_id}")
            logger.info("Gitea: Repo '%s/%s' gelöscht", self.org, project_id)
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return  # existiert nicht — kein Fehler
            raise

    async def get_repo_info(self, project_id: str) -> dict:
        """Repo-Metadaten: URL, default branch, letzte Commits etc."""
        return await self._get(f"/repos/{self.org}/{project_id}")  # type: ignore[return-value]

    # ------------------------------------------------------------------ PRs

    async def create_pr(
        self,
        project_id: str,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
    ) -> dict:
        """Erstellt einen Pull Request."""
        data = {
            "title": title,
            "head":  head,
            "base":  base,
            "body":  body,
        }
        return await self._post(f"/repos/{self.org}/{project_id}/pulls", data)

    async def list_prs(self, project_id: str, state: str = "open") -> list:
        """Listet PRs eines Repos."""
        return await self._get(f"/repos/{self.org}/{project_id}/pulls?state={state}&limit=20")  # type: ignore[return-value]

    # ------------------------------------------------------------------ Webhooks

    async def create_webhook(self, project_id: str, target_url: str, secret: str = "") -> dict:
        """
        Fügt einen Webhook hinzu der bei Push auf main den Deploy-Endpoint aufruft.
        target_url: z.B. http://127.0.0.1:8765/webhooks/gitea/{project_id}
        """
        data: dict[str, Any] = {
            "type": "gitea",
            "config": {
                "url":          target_url,
                "content_type": "json",
                "secret":       secret,
            },
            "events":        ["push"],
            "branch_filter": "main",
            "active":        True,
        }
        return await self._post(f"/repos/{self.org}/{project_id}/hooks", data)

    async def list_webhooks(self, project_id: str) -> list:
        return await self._get(f"/repos/{self.org}/{project_id}/hooks")  # type: ignore[return-value]

    # ------------------------------------------------------------------ Org

    async def _ensure_org(self) -> None:
        """Legt die OctopOS-Organisation an falls sie nicht existiert."""
        try:
            await self._get(f"/orgs/{self.org}")
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                await self._post("/orgs", {
                    "username":   self.org,
                    "visibility": "private",
                })
                logger.info("Gitea: Organisation '%s' angelegt", self.org)
            # 500 kann kommen wenn org schon existiert — ignorieren
            elif e.status not in (500, 422):
                raise

    # ------------------------------------------------------------------ Git (lokal via subprocess)

    @staticmethod
    async def git_workspace(project_id: str) -> Path:
        """
        Gibt das lokale Git-Workspace-Verzeichnis zurück.
        /tmp/octopos-git/{project_id}/ — wird bei Bedarf geclont.
        """
        import asyncio
        workspace = Path(f"/tmp/octopos-git/{project_id}")
        if not workspace.exists():
            cfg = _load_config()
            # clone_url nutzt lokale URL damit keine Auth nötig
            clone_url = f"{cfg['url']}/octopos/{project_id}.git"
            # URL mit Token für Auth
            token_url = clone_url.replace("://", f"://octopos:{cfg['token']}@")
            workspace.parent.mkdir(parents=True, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", token_url, str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"git clone fehlgeschlagen: {stderr.decode()[:300]}")
        return workspace

    @staticmethod
    async def _git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        """Führt einen git-Befehl aus und gibt (stdout, stderr, returncode) zurück."""
        import asyncio
        env = {
            "GIT_AUTHOR_NAME":     "OctopOS Agent",
            "GIT_AUTHOR_EMAIL":    "agent@octopos.local",
            "GIT_COMMITTER_NAME":  "OctopOS Agent",
            "GIT_COMMITTER_EMAIL": "agent@octopos.local",
            "HOME":                "/tmp",
            "PATH":                "/usr/bin:/bin",
        }
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode or 0


# ------------------------------------------------------------------ Singleton

_client: GiteaClient | None = None


def get_gitea_client() -> GiteaClient:
    global _client
    if _client is None:
        _client = GiteaClient.from_config()
    return _client


def reload_gitea_client() -> None:
    """Zwingt den Client beim nächsten get_gitea_client() zur Neukonfiguration."""
    global _client
    _client = None
