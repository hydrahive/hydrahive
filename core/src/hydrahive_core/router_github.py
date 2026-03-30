"""router_github.py — GitHub-Integration (#53)

Token-Verwaltung (global + per-User) und Repository-Zugriff.

Endpoints (Admin):
  POST   /github/token          → globalen Token speichern
  DELETE /github/token          → globalen Token löschen
  GET    /github/token/status   → Token testen, Account-Info abrufen
  GET    /github/repos          → Repos des verknüpften Accounts auflisten
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

GITHUB_TOKEN_FILE = Path("/etc/hydrahive/github_token")
GITHUB_API = "https://api.github.com"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_token() -> str:
    """Liest den gespeicherten Token oder gibt '' zurück."""
    try:
        return GITHUB_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _save_token(token: str) -> None:
    GITHUB_TOKEN_FILE.write_text(token.strip(), encoding="utf-8")
    import os
    os.chmod(GITHUB_TOKEN_FILE, 0o600)


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _test_token(token: str) -> dict:
    """Ruft /user und /user/installations ab, gibt Account-Info zurück."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{GITHUB_API}/user", headers=_headers(token))
    if r.status_code == 401:
        raise HTTPException(401, "GitHub-Token ungültig oder abgelaufen")
    if r.status_code != 200:
        raise HTTPException(502, f"GitHub API Fehler: {r.status_code}")
    d = r.json()
    scopes = r.headers.get("x-oauth-scopes", "")
    return {
        "login":      d.get("login"),
        "name":       d.get("name"),
        "avatar_url": d.get("avatar_url"),
        "html_url":   d.get("html_url"),
        "scopes":     [s.strip() for s in scopes.split(",") if s.strip()],
    }


# ── Schemas ───────────────────────────────────────────────────────────────────

class SaveTokenRequest(BaseModel):
    token: str


# ── Router-Registrierung ──────────────────────────────────────────────────────

def register_github_routes(
    admin_router: APIRouter,
    *,
    require_admin,
) -> None:

    @admin_router.post("/github/token", status_code=201)
    async def save_github_token(req: SaveTokenRequest, _a: tuple = Depends(require_admin)):
        """Globalen GitHub PAT speichern (nach Test)."""
        if not req.token.startswith(("ghp_", "github_pat_", "gho_", "ghs_")):
            raise HTTPException(400, "Kein gültiges GitHub-Token-Format")
        # Token direkt testen vor dem Speichern
        info = await _test_token(req.token)
        _save_token(req.token)
        logger.info("GitHub-Token gespeichert für Account: %s", info["login"])
        return {"saved": True, **info}

    @admin_router.delete("/github/token")
    def delete_github_token(_a: tuple = Depends(require_admin)):
        if GITHUB_TOKEN_FILE.exists():
            GITHUB_TOKEN_FILE.unlink()
        return {"deleted": True}

    @admin_router.get("/github/token/status")
    async def github_token_status(_a: tuple = Depends(require_admin)):
        """Token-Status abrufen ohne Token zu exponieren."""
        token = _load_token()
        if not token:
            return {"configured": False}
        info = await _test_token(token)
        return {"configured": True, **info}

    @admin_router.get("/github/repos")
    async def list_github_repos(
        _a: tuple = Depends(require_admin),
        affiliation: str = "owner,collaborator,organization_member",
        per_page: int = 100,
    ):
        """Alle zugänglichen Repos für Token-Picker im Projekt-Formular."""
        token = _load_token()
        if not token:
            raise HTTPException(400, "Kein GitHub-Token konfiguriert")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{GITHUB_API}/user/repos",
                headers=_headers(token),
                params={"affiliation": affiliation, "per_page": per_page, "sort": "pushed"},
            )
        if r.status_code != 200:
            raise HTTPException(502, f"GitHub API Fehler: {r.status_code}")
        repos = r.json()
        return [
            {
                "full_name":    repo["full_name"],
                "html_url":     repo["html_url"],
                "description":  repo.get("description") or "",
                "private":      repo["private"],
                "language":     repo.get("language"),
                "pushed_at":    repo.get("pushed_at"),
            }
            for repo in repos
        ]
