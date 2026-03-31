"""
gitea.py — Gitea-Integration für HydraHive

Lokales Gitea auf Port 3001 (intern) / 3002 (nginx proxy).
Jedes Projekt bekommt ein Git-Repo auf dem lokalen Gitea.
Agents nutzen git_commit/git_push/git_diff/git_status Tools.

Konfiguration in /etc/hydrahive/gitea_config.json:
{
  "url": "http://127.0.0.1:3001",
  "token": "<api-token>",
  "org": "hydrahive"
}
"""

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

_GITEA_CONFIG_PATHS = [Path("/etc/hydrahive/gitea_config.json"), Path("/etc/hydrahive/gitea_config.json")]
GITEA_CONFIG_FILE = next((p for p in _GITEA_CONFIG_PATHS if p.exists()), _GITEA_CONFIG_PATHS[0])

_DEFAULT_CONFIG = {
    "url":   "http://127.0.0.1:3001",
    "token": "",   # wird aus /etc/hydrahive/gitea_config.json geladen
    "org":   "hydrahive",
}


def _git_env_with_auth(token: str, username: str = "hydrahive") -> dict:
    """
    Gibt eine git-Umgebung zurück die den Token via GIT_ASKPASS übergibt
    statt ihn in die Remote-URL einzubetten.

    GIT_ASKPASS ist ein Script das git aufruft wenn es nach Credentials fragt.
    Der Token landet NICHT in git-URLs, git-History oder Prozesslisten.
    """
    import stat
    import tempfile

    # Einmaliges ASKPASS-Script in tmpfs schreiben
    askpass = Path(tempfile.mktemp(prefix="hydrahive-askpass-", suffix=".sh", dir="/tmp"))
    askpass.write_text(
        f"#!/bin/sh\n"
        f"case \"$1\" in\n"
        f"  *Username*) echo '{username}' ;;\n"
        f"  *Password*) echo '{token}' ;;\n"
        f"  *) echo '' ;;\n"
        f"esac\n",
        encoding="utf-8",
    )
    askpass.chmod(stat.S_IRWXU)  # 700

    env = {
        "GIT_AUTHOR_NAME":     "HydraHive Agent",
        "GIT_AUTHOR_EMAIL":    "agent@hydrahive.local",
        "GIT_COMMITTER_NAME":  "HydraHive Agent",
        "GIT_COMMITTER_EMAIL": "agent@hydrahive.local",
        "HOME":                "/tmp",
        "PATH":                "/usr/bin:/bin",
        "GIT_ASKPASS":         str(askpass),
        "GIT_TERMINAL_PROMPT": "0",   # Niemals interaktiv nach Credentials fragen
    }
    return env


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
        return cls(cfg["url"], cfg["token"], cfg.get("org", "hydrahive"))

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
                if not resp.ok:
                    try:
                        err_body = await resp.json()
                        msg = err_body.get("message", str(err_body))
                    except Exception:
                        msg = await resp.text()
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history,
                        status=resp.status, message=msg,
                    )
                if resp.status == 204:
                    return {}
                return await resp.json()

    async def _patch(self, path: str, data: dict) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{self.url}/api/v1{path}",
                headers=self._headers(),
                json=data,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
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
        Repos liegen unter dem Owner (User oder Org — konfigurierbar, Standard: 'hydrahive').
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

    async def get_repo_by_full_name(self, owner: str, repo: str) -> dict:
        return await self._get(f"/repos/{owner}/{repo}")  # type: ignore[return-value]

    async def list_commits(self, owner: str, repo: str, limit: int = 5) -> list:
        return await self._get(f"/repos/{owner}/{repo}/commits?limit={limit}")  # type: ignore[return-value]

    async def list_repo_tree(self, owner: str, repo: str, path: str = "", ref: str = "") -> list:
        query = ""
        if ref:
            query = f"?ref={quote(ref)}"
        safe_path = path.strip("/")
        if safe_path:
            return await self._get(f"/repos/{owner}/{repo}/contents/{quote(safe_path)}{query}")  # type: ignore[return-value]
        return await self._get(f"/repos/{owner}/{repo}/contents{query}")  # type: ignore[return-value]

    async def get_repo_file(self, owner: str, repo: str, path: str, ref: str = "") -> str:
        if not path.strip("/"):
            raise ValueError("Dateipfad fehlt")
        query = f"?ref={quote(ref)}" if ref else ""
        raw_path = quote(path.strip("/"), safe="/")
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.url}/api/v1/repos/{owner}/{repo}/raw/{raw_path}{query}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                return await resp.text()

    async def create_issue_for_repo(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
    ) -> dict:
        data: dict[str, Any] = {
            "title": title,
            "body": body,
        }
        # Gitea erwartet Label-IDs als Integer — String-Labels werden ignoriert
        # um 422-Fehler zu vermeiden. Labels müssen vorab per /labels aufgelöst werden.
        if labels:
            int_labels = [l for l in labels if isinstance(l, int)]
            if int_labels:
                data["labels"] = int_labels
        return await self._post(f"/repos/{owner}/{repo}/issues", data)

    async def comment_issue_for_repo(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> dict:
        return await self._post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            {"body": body},
        )

    async def update_issue_for_repo(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        labels: list[str] | None = None,
    ) -> dict:
        data: dict[str, Any] = {}
        if title is not None:
            data["title"] = title
        if body is not None:
            data["body"] = body
        if state is not None:
            data["state"] = state
        if labels is not None:
            data["labels"] = labels
        return await self._patch(f"/repos/{owner}/{repo}/issues/{issue_number}", data)

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

    async def create_pr_for_repo(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
    ) -> dict:
        data = {
            "title": title,
            "head":  head,
            "base":  base,
            "body":  body,
        }
        return await self._post(f"/repos/{owner}/{repo}/pulls", data)

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
        """Legt die HydraHive-Organisation an falls sie nicht existiert."""
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
    async def git_workspace(repo_key: str, owner: str | None = None, repo: str | None = None) -> Path:
        """
        Gibt das lokale Git-Workspace-Verzeichnis zurueck.
        /tmp/hydrahive-git/{repo_key}/ — wird bei Bedarf geclont.
        """
        import asyncio
        workspace = Path(f"/tmp/hydrahive-git/{repo_key}")
        if not workspace.exists():
            cfg = _load_config()
            repo_owner = owner or cfg.get("org", "hydrahive")
            repo_name = repo or repo_key
            clone_url = f"{cfg['url']}/{repo_owner}/{repo_name}.git"
            workspace.parent.mkdir(parents=True, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", clone_url, str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_git_env_with_auth(cfg["token"], username=cfg.get("org", "hydrahive")),
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"git clone fehlgeschlagen: {stderr.decode()[:300]}")
        return workspace

    @staticmethod
    async def _git(
        args: list[str], cwd: Path, token: str | None = None, username: str = "hydrahive",
    ) -> tuple[str, str, int]:
        """Führt einen git-Befehl aus und gibt (stdout, stderr, returncode) zurück.
        Wenn token übergeben wird, wird GIT_ASKPASS für sichere Auth genutzt
        (Token landet NICHT in der Remote-URL oder Git-History).
        """
        import asyncio
        env = _git_env_with_auth(token, username=username) if token else {
            "GIT_AUTHOR_NAME":     "HydraHive Agent",
            "GIT_AUTHOR_EMAIL":    "agent@hydrahive.local",
            "GIT_COMMITTER_NAME":  "HydraHive Agent",
            "GIT_COMMITTER_EMAIL": "agent@hydrahive.local",
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


def resolve_repo_ref(repo: str, default_owner: str | None = None) -> tuple[str, str]:
    candidate = (repo or "").strip()
    if not candidate:
        raise ValueError("Repo-Referenz fehlt")

    if "://" in candidate:
        parsed = urlparse(candidate)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            owner, name = parts[0], parts[1]
            if name.endswith(".git"):
                name = name[:-4]
            return owner, name
        raise ValueError(f"Repo-URL konnte nicht aufgeloest werden: {candidate}")

    parts = [part for part in candidate.split("/") if part]
    if len(parts) >= 2:
        owner, name = parts[0], parts[1]
    elif len(parts) == 1 and default_owner:
        owner, name = default_owner, parts[0]
    else:
        raise ValueError(f"Repo-Referenz ungueltig: {candidate}")

    if name.endswith(".git"):
        name = name[:-4]
    return owner, name


def repo_workspace_key(owner: str, repo: str) -> str:
    return f"{owner}__{repo}"


def _candidate_repo_names(project_id: str) -> list[str]:
    raw = (project_id or "").strip()
    if not raw:
        return []

    candidates: list[str] = []

    def add(value: str) -> None:
        value = value.strip()
        if value and value not in candidates:
            candidates.append(value)

    add(raw)

    for suffix in ("_dev", "-dev"):
        if raw.endswith(suffix):
            base = raw[: -len(suffix)]
            add(base)
            add(base.replace("_", "-"))
            add(base.replace("-", "_"))

    add(raw.replace("_", "-"))
    add(raw.replace("-", "_"))

    return candidates


async def resolve_git_target(
    client: GiteaClient,
    *,
    project_id: str,
    repo: str = "",
) -> dict[str, str]:
    if repo.strip():
        owner, name = resolve_repo_ref(repo, default_owner=client.org)
        info = await client.get_repo_by_full_name(owner, name)
        return {
            "owner": owner,
            "repo": name,
            "full_name": info.get("full_name") or f"{owner}/{name}",
            "workspace_key": repo_workspace_key(owner, name),
            "source": "repo",
        }

    last_error: Exception | None = None
    matches: list[dict[str, str]] = []
    for candidate in _candidate_repo_names(project_id):
        try:
            info = await client.get_repo_by_full_name(client.org, candidate)
            matches.append(
                {
                    "owner": client.org,
                    "repo": candidate,
                    "full_name": info.get("full_name") or f"{client.org}/{candidate}",
                    "workspace_key": repo_workspace_key(client.org, candidate),
                    "source": "project_id",
                }
            )
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                last_error = e
                continue
            raise

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        choices = ", ".join(match["full_name"] for match in matches)
        raise ValueError(
            f"Repository-Aufloesung mehrdeutig fuer '{project_id}'. "
            f"Bitte repo explizit angeben. Treffer: {choices}"
        )

    hint = project_id
    if project_id.startswith("personal_"):
        hint = "Bitte repo explizit angeben (URL, owner/repo oder Repo-Name)"
    raise ValueError(f"Repository konnte nicht aufgeloest werden: {hint}") from last_error
