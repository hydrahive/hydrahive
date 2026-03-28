"""router_webhooks_butler.py — Eingehende Webhooks als Butler-Flow-Trigger

Öffentlicher Endpunkt: POST /webhooks/butler/{hook_id}
  - Kein Login erforderlich
  - Optionale HMAC-Signaturprüfung (X-Hub-Signature-256)
  - Sucht aktive Flows mit passendem webhook_received-Trigger
  - Führt Aktionen asynchron aus (fire and forget)

Admin-Endpunkte: GET/POST/DELETE /admin/butler/hooks
  - Globales Webhook-Secret verwalten
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .butler_executor import ButlerEvent, check_flows

logger = logging.getLogger(__name__)

HOOKS_CONFIG = Path("/etc/hydrahive/butler_webhooks.json")


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if HOOKS_CONFIG.exists():
        try:
            return json.loads(HOOKS_CONFIG.read_text())
        except Exception:
            pass
    return {"secret": ""}


def _save_config(cfg: dict) -> None:
    HOOKS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    HOOKS_CONFIG.write_text(json.dumps(cfg, indent=2))
    HOOKS_CONFIG.chmod(0o600)


# ── HMAC validation ────────────────────────────────────────────────────────────

def _verify_signature(secret: str, body: bytes, sig_header: str) -> bool:
    """Prüft X-Hub-Signature-256 Header. True wenn kein Secret konfiguriert."""
    if not secret:
        return True
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header or "")


# ── Action executor ────────────────────────────────────────────────────────────

async def _run_agent(agent_id: str, cfg: Any, message: str, orchestrator: Any) -> None:
    """Agent im Hintergrund ausführen (fire and forget)."""
    try:
        async for _ in orchestrator.handle_message_stream(
            project_id=agent_id,
            project_cfg=cfg,
            content=message,
            sender="butler:webhook",
        ):
            pass
    except Exception as e:
        logger.error("Butler webhook: Agent %s Fehler: %s", agent_id, e)


async def execute_webhook_actions(
    actions: list[dict],
    event: ButlerEvent,
    orchestrator: Any,
    load_project_cfg: Any,
) -> None:
    """Führt Butler-Aktionen für einen Webhook-Trigger aus."""
    for act in actions:
        sub    = act.get("subtype")
        params = act.get("params", {})

        if sub == "ignore":
            return

        if sub in ("agent_reply", "agent_reply_guided", "forward"):
            agent_id = str(params.get("agent_id", "")).strip()
            if not agent_id:
                continue
            # Nachricht aus Webhook-Payload oder Fallback
            payload = event.extra.get("payload", {})
            msg = (
                str(payload.get("message") or payload.get("text") or payload.get("body") or "")
                or f"Webhook empfangen: hook_id={event.channel}"
            )
            if sub == "agent_reply_guided":
                instr = str(params.get("instruction", "")).strip()
                if instr:
                    msg = f"[BUTLER-VORGABE: {instr}]\n{msg}"
            try:
                cfg = load_project_cfg(agent_id)
            except Exception:
                logger.warning("Butler webhook: Projekt '%s' nicht gefunden", agent_id)
                continue
            asyncio.create_task(_run_agent(agent_id, cfg, msg, orchestrator))

        elif sub == "reply_fixed":
            # Für Webhooks: fixed reply ins Log (kein Kanal zum Antworten)
            logger.info("Butler webhook: reply_fixed — %s", str(params.get("text", ""))[:80])


# ── Route registration ─────────────────────────────────────────────────────────

class HooksConfigBody(BaseModel):
    secret: str = ""


def register_webhook_butler_routes(
    public_router: APIRouter,
    admin_router:  APIRouter,
    *,
    require_admin: Any,
    orchestrator:  Any,
    load_project_cfg: Any,
) -> None:

    # ── Public: eingehender Webhook ──────────────────────────────────────────
    @public_router.post("/webhooks/butler/{hook_id}")
    async def receive_butler_webhook(hook_id: str, request: Request):
        body = await request.body()

        # HMAC-Prüfung
        cfg = _load_config()
        secret = cfg.get("secret", "")
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(secret, body, sig):
            logger.warning("Butler webhook [%s]: ungültige Signatur", hook_id)
            raise HTTPException(status_code=401, detail="Invalid signature")

        # Payload parsen
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {"raw": body.decode(errors="replace")}

        event = ButlerEvent(
            event_type="webhook",
            channel=hook_id,
            extra={
                "payload": payload,
                "headers": dict(request.headers),
            },
        )

        actions = await check_flows(event)
        if actions:
            asyncio.create_task(
                execute_webhook_actions(actions, event, orchestrator, load_project_cfg)
            )
            logger.info("Butler webhook [%s]: %d Aktion(en) ausgelöst", hook_id, len(actions))
        else:
            logger.debug("Butler webhook [%s]: kein Flow-Match", hook_id)

        return {"ok": True, "hook_id": hook_id, "actions": len(actions)}

    # ── Admin: Secret verwalten ──────────────────────────────────────────────
    @admin_router.get("/admin/butler/hooks/config")
    def get_hooks_config(auth=Depends(require_admin)):
        cfg = _load_config()
        return {"secret_set": bool(cfg.get("secret"))}

    @admin_router.put("/admin/butler/hooks/config")
    def update_hooks_config(body: HooksConfigBody, auth=Depends(require_admin)):
        cfg = _load_config()
        cfg["secret"] = body.secret
        _save_config(cfg)
        return {"updated": True}
