"""router_webhooks_butler.py — Eingehende Webhooks als Butler-Flow-Trigger

Endpunkte:
  POST /webhooks/butler/{hook_id}  — generischer Webhook-Trigger
  POST /webhooks/github            — GitHub Events (Push, PR, Issues, ...)
  POST /webhooks/gitea-butler      — Gitea Events (Butler-Flows, getrennt von /webhooks/gitea/{project_id})

Admin:
  GET/PUT /admin/butler/hooks/config  — Secrets verwalten
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

from .butler_executor import ButlerEvent, check_flows, execute_generic_actions

from .settings import settings

logger = logging.getLogger(__name__)

HOOKS_CONFIG = settings.butler_webhooks_config


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if HOOKS_CONFIG.exists():
        try:
            return json.loads(HOOKS_CONFIG.read_text())
        except Exception as e:
            logger.warning("Failed to load webhook config: %s", e)
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
    asyncio.create_task(execute_generic_actions(actions, event))
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
    secret:         str = ""
    github_secret:  str = ""


# ── Git-Payload Parser ─────────────────────────────────────────────────────────

def _parse_git_event(event_name: str, payload: dict) -> dict:
    """Extrahiert Kurzfelder aus GitHub/Gitea Webhook-Payload."""
    extra: dict = {
        "event":   event_name,
        "payload": payload,
        "repo":    payload.get("repository", {}).get("full_name", ""),
        "action":  payload.get("action", ""),
    }

    if event_name == "push":
        ref = payload.get("ref", "")
        extra["branch"] = ref.removeprefix("refs/heads/")
        extra["author"] = (
            payload.get("pusher", {}).get("name", "")
            or payload.get("pusher", {}).get("login", "")
        )
        commits = payload.get("commits") or []
        extra["commit_message"] = commits[0].get("message", "") if commits else ""

    elif event_name in ("pull_request", "pull_request_review"):
        pr = payload.get("pull_request", {})
        extra["branch"]        = pr.get("head", {}).get("ref", "")
        extra["target_branch"] = pr.get("base", {}).get("ref", "")
        extra["author"]        = (
            pr.get("user", {}).get("login", "")
            or payload.get("sender", {}).get("login", "")
        )
        extra["title"]      = pr.get("title", "")
        extra["pr_number"]  = pr.get("number", 0)
        extra["merged"]     = bool(pr.get("merged", False))

    elif event_name in ("issues", "issue"):
        issue = payload.get("issue", {})
        extra["author"]       = issue.get("user", {}).get("login", "")
        extra["title"]        = issue.get("title", "")
        extra["issue_number"] = issue.get("number", 0)
        extra["labels"]       = [lb.get("name", "") for lb in issue.get("labels", [])]

    elif event_name == "issue_comment":
        comment = payload.get("comment", {})
        issue   = payload.get("issue", {})
        extra["author"]       = comment.get("user", {}).get("login", "")
        extra["comment_body"] = comment.get("body", "")
        extra["issue_number"] = issue.get("number", 0)

    elif event_name == "release":
        release = payload.get("release", {})
        extra["tag"]          = release.get("tag_name", "")
        extra["release_name"] = release.get("name", "")
        extra["author"]       = release.get("author", {}).get("login", "")

    return extra


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
        except Exception as e:
            logger.debug("Failed to parse webhook payload as JSON: %s", e)
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

    # ── GitHub Webhook ───────────────────────────────────────────────────────
    @public_router.post("/webhooks/github")
    async def github_webhook(request: Request):
        body = await request.body()

        cfg    = _load_config()
        secret = cfg.get("github_secret", "")
        sig    = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(secret, body, sig):
            logger.warning("GitHub webhook: ungültige Signatur")
            raise HTTPException(status_code=401, detail="Invalid signature")

        event_name = request.headers.get("X-GitHub-Event", "push")
        try:
            payload = json.loads(body) if body else {}
        except Exception as e:
            logger.debug("Failed to parse GitHub webhook payload: %s", e)
            payload = {}

        extra = _parse_git_event(event_name, payload)
        event = ButlerEvent(
            event_type="webhook",
            channel="github",
            extra=extra,
        )
        actions = await check_flows(event)
        if actions:
            asyncio.create_task(
                execute_webhook_actions(actions, event, orchestrator, load_project_cfg)
            )
        logger.info("GitHub webhook [%s] repo=%s: %d Aktion(en)", event_name, extra.get("repo"), len(actions))
        return {"ok": True, "event": event_name, "actions": len(actions)}

    # ── Gitea Butler Webhook ─────────────────────────────────────────────────
    @public_router.post("/webhooks/gitea-butler")
    async def gitea_butler_webhook(request: Request):
        body = await request.body()

        # Gitea-Secret aus bestehender gitea_config.json lesen
        try:
            from .gitea import _load_config as _gitea_cfg
            gitea_cfg = _gitea_cfg()
            secret = gitea_cfg.get("webhook_secret", "")
        except Exception as e:
            logger.debug("Failed to load gitea config for webhook secret: %s", e)
            secret = ""

        sig = request.headers.get("X-Gitea-Signature", "")
        if secret:
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                logger.warning("Gitea Butler webhook: ungültige Signatur")
                raise HTTPException(status_code=401, detail="Invalid signature")

        event_name = request.headers.get("X-Gitea-Event", "push")
        try:
            payload = json.loads(body) if body else {}
        except Exception as e:
            logger.debug("Failed to parse Gitea webhook payload: %s", e)
            payload = {}

        extra = _parse_git_event(event_name, payload)
        event = ButlerEvent(
            event_type="webhook",
            channel="gitea",
            extra=extra,
        )
        actions = await check_flows(event)
        if actions:
            asyncio.create_task(
                execute_webhook_actions(actions, event, orchestrator, load_project_cfg)
            )
        logger.info("Gitea Butler webhook [%s] repo=%s: %d Aktion(en)", event_name, extra.get("repo"), len(actions))
        return {"ok": True, "event": event_name, "actions": len(actions)}

    # ── Admin: Secrets verwalten ─────────────────────────────────────────────
    @admin_router.get("/admin/butler/hooks/config")
    def get_hooks_config(auth=Depends(require_admin)):
        cfg = _load_config()
        return {
            "secret_set":        bool(cfg.get("secret")),
            "github_secret_set": bool(cfg.get("github_secret")),
        }

    @admin_router.put("/admin/butler/hooks/config")
    def update_hooks_config(body: HooksConfigBody, auth=Depends(require_admin)):
        cfg = _load_config()
        cfg["secret"]        = body.secret
        cfg["github_secret"] = body.github_secret
        _save_config(cfg)
        return {"updated": True}
