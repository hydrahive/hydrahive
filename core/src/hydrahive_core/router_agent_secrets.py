"""
router_agent_secrets.py — Unified Secret-Store (#54, #569)

Vereint zwei Quellen:
  1. /etc/hydrahive/agent_secrets.json — Agent-Secrets (get_secret Tool)
  2. /etc/hydrahive/llm_env — LLM API-Keys + Bot-Tokens (os.environ)

Die Secrets-Seite zeigt beide. Neue Secrets werden in BEIDE geschrieben,
damit sowohl das Settings-Panel-Dropdown als auch get_secret sie findet.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .settings import settings

_SECRETS_PATH = settings.agent_secrets_config
_LLM_ENV_PATH = settings.llm_env

# #599: Lock um konkurrierende Writes zu serialisieren
_SECRETS_LOCK = threading.Lock()


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    """Atomarer Write via Tempfile + os.replace (#599)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        tmp.chmod(mode)
    except Exception:
        pass
    os.replace(tmp, path)


def _validate_env_value(value: str) -> None:
    """#599: Werte mit Newlines/NUL brechen llm_env format — ablehnen."""
    if "\n" in value or "\r" in value or "\x00" in value:
        raise HTTPException(400, "Secret-Wert darf keine Newlines/NUL enthalten.")


def _load() -> dict[str, str]:
    """Lädt Secrets aus agent_secrets.json."""
    if _SECRETS_PATH.exists():
        try:
            return json.loads(_SECRETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_llm_env() -> dict[str, str]:
    """Lädt Keys aus llm_env (.env Format)."""
    result: dict[str, str] = {}
    if _LLM_ENV_PATH.exists():
        try:
            for line in _LLM_ENV_PATH.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                result[key.strip()] = val.strip()
        except Exception:
            pass
    return result


def _save(data: dict[str, str]) -> None:
    # #599: atomar + chmod 600
    _atomic_write(_SECRETS_PATH, json.dumps(data, ensure_ascii=False, indent=2))


def _save_to_llm_env(name: str, value: str) -> None:
    """Fügt einen Key zur llm_env Datei hinzu oder aktualisiert ihn (atomar)."""
    lines: list[str] = []
    found = False
    if _LLM_ENV_PATH.exists():
        for line in _LLM_ENV_PATH.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                lines.append(f"{name}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{name}={value}")
    _atomic_write(_LLM_ENV_PATH, "\n".join(lines) + "\n")
    # Auch in os.environ setzen damit sofort verfügbar
    os.environ[name] = value


def _delete_from_llm_env(name: str) -> None:
    """Entfernt einen Key aus der llm_env Datei (atomar)."""
    if not _LLM_ENV_PATH.exists():
        return
    lines = [l for l in _LLM_ENV_PATH.read_text().splitlines()
             if not l.strip().startswith(f"{name}=")]
    _atomic_write(_LLM_ENV_PATH, "\n".join(lines) + "\n")
    os.environ.pop(name, None)


class SecretIn(BaseModel):
    value: str
    description: str = ""


def register_agent_secret_routes(app, get_current_admin):
    router = APIRouter(prefix="/admin/agent-secrets", tags=["agent-secrets"])

    @router.get("")
    async def list_secrets(_user=Depends(get_current_admin)):
        # Beide Quellen mergen — llm_env als Basis, agent_secrets überschreibt
        merged: dict[str, str] = {}
        merged.update(_load_llm_env())
        merged.update(_load())
        return [
            {
                "name": k,
                "masked": "•" * min(len(v), 8),
                "has_value": bool(v),
                "source": "both" if k in _load() and k in _load_llm_env()
                    else "secrets" if k in _load()
                    else "env",
            }
            for k, v in sorted(merged.items())
        ]

    @router.put("/{name}")
    async def upsert_secret(name: str, body: SecretIn, _user=Depends(get_current_admin)):
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(400, "Name darf nur Buchstaben, Zahlen, _ und - enthalten")
        # #599: Werte ohne Newlines erzwingen + Lock gegen Race
        _validate_env_value(body.value)
        with _SECRETS_LOCK:
            data = _load()
            data[name] = body.value
            _save(data)
            _save_to_llm_env(name, body.value)
        return {"ok": True, "name": name}

    @router.get("/{name}/reveal")
    async def reveal_secret(name: str, _user=Depends(get_current_admin)):
        # Aus beiden Quellen suchen
        data = _load()
        if name in data:
            return {"name": name, "value": data[name]}
        env_data = _load_llm_env()
        if name in env_data:
            return {"name": name, "value": env_data[name]}
        raise HTTPException(404, "Secret nicht gefunden")

    @router.delete("/{name}")
    async def delete_secret(name: str, _user=Depends(get_current_admin)):
        # #599: Lock gegen Race
        with _SECRETS_LOCK:
            data = _load()
            deleted = False
            if name in data:
                del data[name]
                _save(data)
                deleted = True
            # Auch aus llm_env entfernen
            env_data = _load_llm_env()
            if name in env_data:
                _delete_from_llm_env(name)
                deleted = True
        if not deleted:
            raise HTTPException(404, "Secret nicht gefunden")
        return {"ok": True}

    app.include_router(router)
