"""
router_repos.py — CRUD API für Git-Repo-Verwaltung

Endpoints:
  GET    /admin/repos          — Alle Repos listen
  POST   /admin/repos          — Neues Repo anlegen
  PUT    /admin/repos/{id}     — Repo aktualisieren
  DELETE /admin/repos/{id}     — Repo löschen
  POST   /admin/repos/{id}/test — Token testen (Verbindungscheck)
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .repo_config import (
    RepoConfig,
    delete_repo,
    get_repo,
    load_repos,
    upsert_repo,
)

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[a-z0-9_-]+$")


class RepoRequest(BaseModel):
    id: str
    name: str
    url: str
    token: str = ""
    branch: str = "main"
    provider: str = "github"
    agents: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)


def register_repo_routes(
    admin_router: APIRouter,
    *,
    require_admin,
) -> None:

    @admin_router.get("/repos")
    def list_repos(_a: tuple = Depends(require_admin)):
        repos = load_repos()
        # Token maskieren für die Anzeige
        return {
            "repos": [
                {
                    **r.model_dump(),
                    "token_preview": r.token[:8] + "..." if len(r.token) > 8 else ("***" if r.token else ""),
                }
                for r in repos
            ]
        }

    @admin_router.post("/repos", status_code=201)
    def create_repo(req: RepoRequest, _a: tuple = Depends(require_admin)):
        if not _SAFE_ID.fullmatch(req.id):
            raise HTTPException(400, "Repo-ID darf nur a-z, 0-9, _ und - enthalten")
        if get_repo(req.id):
            raise HTTPException(409, f"Repo '{req.id}' existiert bereits")
        repo = RepoConfig(
            id=req.id, name=req.name, url=req.url.rstrip("/"),
            token=req.token, branch=req.branch, provider=req.provider,
            agents=list(req.agents), projects=list(req.projects),
        )
        upsert_repo(repo)
        logger.info("Repo erstellt: %s (%s)", req.id, req.url)
        return {"created": True, "repo_id": req.id}

    @admin_router.put("/repos/{repo_id}")
    def update_repo(repo_id: str, req: RepoRequest, _a: tuple = Depends(require_admin)):
        if not _SAFE_ID.fullmatch(repo_id):
            raise HTTPException(400, "Ungültige Repo-ID")
        existing = get_repo(repo_id)
        if not existing:
            raise HTTPException(404, f"Repo '{repo_id}' nicht gefunden")
        # Wenn token leer gesendet wird, alten behalten
        token = req.token if req.token else existing.token
        repo = RepoConfig(
            id=repo_id, name=req.name, url=req.url.rstrip("/"),
            token=token, branch=req.branch, provider=req.provider,
            agents=list(req.agents), projects=list(req.projects),
        )
        upsert_repo(repo)
        logger.info("Repo aktualisiert: %s", repo_id)
        return {"updated": True, "repo_id": repo_id}

    @admin_router.delete("/repos/{repo_id}")
    def remove_repo(repo_id: str, _a: tuple = Depends(require_admin)):
        if not delete_repo(repo_id):
            raise HTTPException(404, f"Repo '{repo_id}' nicht gefunden")
        logger.info("Repo gelöscht: %s", repo_id)
        return {"deleted": True, "repo_id": repo_id}

    @admin_router.post("/repos/{repo_id}/test")
    async def test_repo(repo_id: str, _a: tuple = Depends(require_admin)):
        repo = get_repo(repo_id)
        if not repo:
            raise HTTPException(404, f"Repo '{repo_id}' nicht gefunden")

        import httpx

        # Provider-spezifischer API-Check
        if repo.provider == "github":
            api_url = repo.url.replace("github.com", "api.github.com/repos")
            headers = {"Accept": "application/vnd.github+json"}
            if repo.token:
                headers["Authorization"] = f"Bearer {repo.token}"
        elif repo.provider == "gitea":
            # Gitea: /api/v1/repos/{owner}/{repo}
            base = repo.url.rsplit("/", 2)
            api_url = f"{base[0]}/api/v1/repos/{base[1]}/{base[2]}" if len(base) >= 3 else repo.url
            headers = {}
            if repo.token:
                headers["Authorization"] = f"token {repo.token}"
        else:
            api_url = repo.url
            headers = {"Authorization": f"Bearer {repo.token}"} if repo.token else {}

        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as client:
                r = await client.get(api_url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                return {
                    "ok": True,
                    "status": r.status_code,
                    "repo_name": data.get("full_name") or data.get("name", "?"),
                    "private": data.get("private", False),
                    "default_branch": data.get("default_branch", "?"),
                }
            return {"ok": False, "status": r.status_code, "error": r.text[:200]}
        except Exception as e:
            return {"ok": False, "status": 0, "error": str(e)}
