"""router_invites.py — Einladungslinks für neue User (#5)

Admin generiert einen Token-Link. User öffnet den Link, setzt sein Passwort
und wird direkt angelegt — ohne dass der Admin das Passwort kennt.

Endpoints:
  POST /admin/invites                   → Token generieren
  GET  /admin/invites                   → alle offenen Einladungen
  DELETE /admin/invites/{token}         → Einladung widerrufen
  GET  /public/invites/{token}          → Token validieren (kein Auth)
  POST /public/invites/{token}/accept   → Account anlegen + Token verbrauchen
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .settings import settings

logger = logging.getLogger(__name__)

INVITES_FILE = str(settings.invites_config)
INVITE_TTL_SECONDS = 7 * 24 * 3600  # 7 Tage

# #458: Lock gegen Race Conditions bei concurrent Invite-Operationen
import asyncio as _aio
_invites_lock = _aio.Lock()


# ── Persistenz ────────────────────────────────────────────────────────────────

def _load_invites() -> dict[str, Any]:
    try:
        return json.loads(Path(INVITES_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_invites(invites: dict[str, Any]) -> None:
    p = Path(INVITES_FILE)
    p.write_text(json.dumps(invites, indent=2, ensure_ascii=False), encoding="utf-8")
    import os
    os.chmod(INVITES_FILE, 0o600)


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateInviteRequest(BaseModel):
    role: str = "user"
    group: str = "standard"
    allowed_projects: list[str] = []
    allowed_agents: list[str] = []
    note: str = ""                    # optionale Admin-Notiz (z.B. "für Max Mustermann")
    ttl_days: int = 7


class AcceptInviteRequest(BaseModel):
    username: str
    password: str


# ── Router-Registrierung ──────────────────────────────────────────────────────

def register_invite_routes(
    admin_router: APIRouter,
    public_router: APIRouter,
    *,
    require_admin,
    create_user_fn,
    base_url: str,
) -> None:

    @admin_router.post("/invites", status_code=201)
    def create_invite(req: CreateInviteRequest, _a: tuple = Depends(require_admin)):
        invites = _load_invites()
        # Abgelaufene bereinigen
        now = time.time()
        invites = {k: v for k, v in invites.items() if v.get("expires_at", 0) > now}

        token = secrets.token_urlsafe(32)
        invites[token] = {
            "role": req.role,
            "group": req.group,
            "allowed_projects": req.allowed_projects,
            "allowed_agents": req.allowed_agents,
            "note": req.note,
            "created_at": now,
            "expires_at": now + req.ttl_days * 24 * 3600,
            "used": False,
        }
        _save_invites(invites)
        link = f"{base_url}/invite/{token}"
        logger.info("Einladungslink erstellt: %s (note=%s)", token[:8], req.note)
        return {"token": token, "link": link, "expires_in_days": req.ttl_days}

    @admin_router.get("/invites")
    def list_invites(_a: tuple = Depends(require_admin)):
        invites = _load_invites()
        now = time.time()
        return [
            {
                "token": token,
                "role": inv["role"],
                "group": inv["group"],
                "allowed_projects": inv["allowed_projects"],
                "note": inv["note"],
                "expires_at": inv["expires_at"],
                "expired": inv["expires_at"] < now,
                "used": inv.get("used", False),
            }
            for token, inv in invites.items()
        ]

    @admin_router.delete("/invites/{token}")
    def revoke_invite(token: str, _a: tuple = Depends(require_admin)):
        invites = _load_invites()
        if token not in invites:
            raise HTTPException(404, "Einladung nicht gefunden")
        del invites[token]
        _save_invites(invites)
        return {"revoked": True, "token": token}

    @public_router.get("/invites/{token}")
    def validate_invite(token: str):
        """Kein Auth — prüft ob Token gültig ist und gibt Metadaten zurück."""
        invites = _load_invites()
        inv = invites.get(token)
        if not inv:
            raise HTTPException(404, "Einladung nicht gefunden oder abgelaufen")
        if inv.get("used"):
            raise HTTPException(410, "Einladung bereits verwendet")
        if inv["expires_at"] < time.time():
            raise HTTPException(410, "Einladung abgelaufen")
        return {
            "valid": True,
            "role": inv["role"],
            "group": inv["group"],
            "note": inv["note"],
        }

    @public_router.post("/invites/{token}/accept", status_code=201)
    async def accept_invite(token: str, req: AcceptInviteRequest):
        """Kein Auth — legt User an und markiert Token als verwendet."""
        async with _invites_lock:  # #458: Race Condition verhindern
            return await _accept_invite_locked(token, req)

    async def _accept_invite_locked(token: str, req: AcceptInviteRequest):
        invites = _load_invites()
        inv = invites.get(token)
        if not inv:
            raise HTTPException(404, "Einladung nicht gefunden")
        if inv.get("used"):
            raise HTTPException(410, "Einladung bereits verwendet")
        if inv["expires_at"] < time.time():
            raise HTTPException(410, "Einladung abgelaufen")

        # Username validieren
        import re
        if not re.match(r'^[a-z0-9_]{3,32}$', req.username):
            raise HTTPException(400, "Username: nur a-z, 0-9, _ erlaubt (3–32 Zeichen)")
        if len(req.password) < 8:
            raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")

        try:
            from .router_users import CreateUserRequest as _CUR
            result = await create_user_fn(_CUR(
                username=req.username,
                password=req.password,
                role=inv["role"],
                group=inv["group"],
                allowed_projects=inv["allowed_projects"],
                allowed_agents=inv["allowed_agents"],
            ))
        except HTTPException:
            raise
        except Exception as e:
            logger.error("User-Anlage via Einladung fehlgeschlagen: %s", e)
            raise HTTPException(500, "User-Anlage fehlgeschlagen")

        # Token als verwendet markieren
        invites[token]["used"] = True
        invites[token]["used_by"] = req.username
        invites[token]["used_at"] = time.time()
        _save_invites(invites)

        logger.info("Einladung %s... von '%s' akzeptiert", token[:8], req.username)
        return {"created": True, "username": req.username, **result}
