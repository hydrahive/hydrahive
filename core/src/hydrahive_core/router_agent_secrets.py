"""
router_agent_secrets.py — Agent Secret-Store (#54)

Einfacher Key/Value-Store in /etc/hydrahive/agent_secrets.json.
Nur für Root lesbar (chmod 600). Agents lesen via get_secret Tool.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .settings import settings

_SECRETS_PATH = settings.agent_secrets_config


def _load() -> dict[str, str]:
    if _SECRETS_PATH.exists():
        try:
            return json.loads(_SECRETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(data: dict[str, str]) -> None:
    _SECRETS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _SECRETS_PATH.chmod(0o600)
    except Exception:
        pass


class SecretIn(BaseModel):
    value: str
    description: str = ""


def register_agent_secret_routes(app, get_current_admin):
    router = APIRouter(prefix="/admin/agent-secrets", tags=["agent-secrets"])

    @router.get("")
    async def list_secrets(_user=Depends(get_current_admin)):
        data = _load()
        # Wert wird maskiert zurückgegeben — UI zeigt nur Namen
        return [
            {"name": k, "masked": "•" * min(len(v), 8), "has_value": bool(v)}
            for k, v in data.items()
        ]

    @router.put("/{name}")
    async def upsert_secret(name: str, body: SecretIn, _user=Depends(get_current_admin)):
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(400, "Name darf nur Buchstaben, Zahlen, _ und - enthalten")
        data = _load()
        data[name] = body.value
        _save(data)
        return {"ok": True, "name": name}

    @router.get("/{name}/reveal")
    async def reveal_secret(name: str, _user=Depends(get_current_admin)):
        data = _load()
        if name not in data:
            raise HTTPException(404, "Secret nicht gefunden")
        return {"name": name, "value": data[name]}

    @router.delete("/{name}")
    async def delete_secret(name: str, _user=Depends(get_current_admin)):
        data = _load()
        if name not in data:
            raise HTTPException(404, "Secret nicht gefunden")
        del data[name]
        _save(data)
        return {"ok": True}

    app.include_router(router)
