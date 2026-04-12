"""butler_executor.py — Flow-Evaluierung für eingehende Messenger-Events

Wird von whatsapp_agent / telegram_agent / discord_agent aufgerufen bevor
ein eingehende Nachricht ans LLM weitergegeben wird.

Gibt eine Liste von Aktionen zurück die ausgeführt werden sollen:
  [{"subtype": "agent_reply", "params": {"agent_id": "..."}, "node_id": "..."}]
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime
from typing import Any

import re
from pathlib import Path

from .butler_rule import ButlerFlow, load_flows
from .settings import settings

logger = logging.getLogger(__name__)

AGENTS_DIR = settings.agents_dir


def get_agent_display_name(agent_id: str) -> str:
    """Liest den Anzeigenamen eines Agenten aus agent.yaml (identity-Feld).
    Entfernt Markdown-Formatierungen wie ** Lilith ** → Lilith.
    Fallback: agent_id."""
    try:
        import yaml as _yaml
        yaml_path = AGENTS_DIR / agent_id / "agent.yaml"
        if yaml_path.exists():
            data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            name = str(data.get("identity", "")).strip()
            # ** Lilith ** → Lilith
            name = re.sub(r"[\*_]+", "", name).strip()
            if name:
                return name
    except Exception:
        pass
    return agent_id


@dataclass
class ButlerEvent:
    """Ein eingehendes Event — Messenger, Webhook, E-Mail, Discord-Event, ..."""
    channel: str          # "whatsapp" | "telegram" | "discord" | "github" | "gitea" | "email" | ...
    contact_id: str = ""
    event_type: str = "message"   # "message" | "webhook" | "email" | "discord_event" | "cron"
    contact_name: str = ""
    is_known: bool = False
    message_text: str = ""
    extra: dict = field(default_factory=dict)
    # extra-Felder je nach event_type:
    #   webhook:       payload (dict), headers (dict)
    #   email:         from, subject, body_plain, to, date
    #   discord_event: event (str), emoji, message_id, channel_id, user_id, username, guild_id
    #   github/gitea:  event (str), repo, branch, author, action, payload (dict)


def has_active_flows(channel: str, event_type: str = "message", owner: str | None = None) -> bool:
    """Gibt True zurück wenn mindestens ein aktiver Flow für diesen Kanal/Event-Typ existiert."""
    for flow in load_flows(owner=owner):
        if not flow.enabled:
            continue
        for node in flow.nodes:
            if node.get("type") == "triggerNode":
                params = node.get("data", {}).get("params", {})
                node_channel = params.get("channel", "all")
                node_event_type = params.get("event_type", "message")
                if node_event_type != event_type:
                    continue
                if node_channel == "all" or node_channel == channel:
                    return True
    return False


async def check_flows(event: ButlerEvent, owner: str | None = None) -> list[dict[str, Any]]:
    """Prüft aktive Flows gegen ein Event. Gibt Aktionsliste zurück.
    owner=None → alle User-Flows (system events wie Webhooks/Heartbeats).
    owner=str  → nur Flows dieses Users (user-spezifische Kanäle wie WhatsApp/Telegram).
    """
    result: list[dict[str, Any]] = []
    for flow in load_flows(owner=owner):
        if not flow.enabled:
            continue
        try:
            actions = _evaluate_flow(flow, event)
            result.extend(actions)
        except Exception as exc:
            logger.warning("Butler flow %s evaluation error: %s", flow.id, exc)
    return result


async def check_flows_for_project(event: ButlerEvent, project_id: str) -> list[dict[str, Any]]:
    """Prüft projekt-scoped Butler-Flows gegen ein Event (#566)."""
    from .butler_rule import load_flows_for_project
    result: list[dict[str, Any]] = []
    for flow in load_flows_for_project(project_id):
        if not flow.enabled:
            continue
        try:
            actions = _evaluate_flow(flow, event)
            result.extend(actions)
        except Exception as exc:
            logger.warning("Butler project flow %s evaluation error: %s", flow.id, exc)
    return result


def _evaluate_flow(flow: ButlerFlow, event: ButlerEvent) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    nodes_by_id: dict[str, dict] = {n["id"]: n for n in flow.nodes}

    for node in flow.nodes:
        node_type = node.get("type", "")
        if "trigger" not in node_type:
            continue
        data = node.get("data", {})
        if _matches_trigger(data, event):
            _traverse(flow, node["id"], "output", nodes_by_id, event, actions)

    return actions


def _matches_trigger(data: dict, event: ButlerEvent) -> bool:
    subtype = data.get("subtype", "")
    params  = data.get("params", {})

    if subtype == "message_received":
        # Rückwärtskompatibel: event_type muss "message" sein
        if event.event_type != "message":
            return False
        channel = params.get("channel", "all")
        return channel == "all" or channel == event.channel

    if subtype == "webhook_received":
        if event.event_type != "webhook":
            return False
        hook_id = params.get("hook_id", "")
        return not hook_id or hook_id == event.channel

    if subtype == "email_received":
        if event.event_type != "email":
            return False
        from_filter = params.get("from_filter", "").lower()
        return not from_filter or from_filter in event.extra.get("from", "").lower()

    if subtype == "discord_event_received":
        if event.event_type != "discord_event":
            return False
        event_name = params.get("discord_event", "")
        return not event_name or event_name == event.extra.get("event", "")

    if subtype == "heartbeat_fired":
        if event.event_type != "cron":
            return False
        agent_filter   = params.get("agent_id", "all")
        task_id_filter = params.get("task_id", "")
        agent_match    = agent_filter == "all" or agent_filter == event.extra.get("agent_id", "")
        task_match     = not task_id_filter or task_id_filter == event.extra.get("task_id", "")
        return agent_match and task_match

    if subtype == "git_event_received":
        if event.event_type != "webhook":
            return False
        channel_filter = params.get("channel", "both")
        if channel_filter != "both" and event.channel != channel_filter:
            return False
        if event.channel not in ("github", "gitea"):
            return False
        git_event  = params.get("git_event", "")
        repo_filter = params.get("repo", "").lower()
        event_match = not git_event  or git_event == event.extra.get("event", "")
        repo_match  = not repo_filter or repo_filter in event.extra.get("repo", "").lower()
        return event_match and repo_match

    return False


def _traverse(
    flow: ButlerFlow,
    node_id: str,
    handle: str,
    nodes_by_id: dict[str, dict],
    event: ButlerEvent,
    actions: list[dict[str, Any]],
    depth: int = 0,
) -> None:
    if depth > 20:
        return  # Cycle-Guard
    for edge in flow.edges:
        if edge.get("source") != node_id:
            continue
        if (edge.get("sourceHandle") or "output") != handle:
            continue
        target_id = edge.get("target")
        target = nodes_by_id.get(target_id or "")
        if not target:
            continue

        node_type = target.get("type", "")
        data = target.get("data", {})

        if "condition" in node_type:
            result = _eval_condition(data, event)
            next_handle = "true" if result else "false"
            _traverse(flow, target_id, next_handle, nodes_by_id, event, actions, depth + 1)
        elif "action" in node_type:
            actions.append({
                "subtype": data.get("subtype"),
                "params":  data.get("params", {}),
                "node_id": target_id,
            })
            _traverse(flow, target_id, "output", nodes_by_id, event, actions, depth + 1)


def _eval_condition(data: dict, event: ButlerEvent) -> bool:
    subtype = data.get("subtype", "")
    params  = data.get("params", {})

    if subtype == "time_window":
        now    = datetime.now().time()
        t_from = _pt(params.get("from", "00:00"))
        t_to   = _pt(params.get("to",   "23:59"))
        if t_from <= t_to:
            return t_from <= now <= t_to
        return now >= t_from or now <= t_to   # Mitternacht-überschreitend

    if subtype == "day_of_week":
        days  = params.get("days", ["mo", "di", "mi", "do", "fr", "sa", "so"])
        today = ["mo", "di", "mi", "do", "fr", "sa", "so"][datetime.now().weekday()]
        return today in days

    if subtype == "contact_known":
        return event.is_known

    if subtype == "contact_in_list":
        return event.contact_id in params.get("contacts", [])

    if subtype == "message_contains":
        kw = params.get("keyword", "").lower()
        return bool(kw) and kw in event.message_text.lower()

    # ── E-Mail-Bedingungen ────────────────────────────────────────────────────
    if subtype == "email_from_contains":
        kw = params.get("keyword", "").lower()
        return bool(kw) and kw in event.extra.get("from", "").lower()

    if subtype == "email_subject_contains":
        kw = params.get("keyword", "").lower()
        return bool(kw) and kw in event.extra.get("subject", "").lower()

    if subtype == "email_body_contains":
        kw = params.get("keyword", "").lower()
        return bool(kw) and kw in event.extra.get("body_plain", "").lower()

    # ── Webhook / Git-Bedingungen ─────────────────────────────────────────────
    if subtype == "payload_field_contains":
        field_path = params.get("field", "")
        kw = params.get("value", "").lower()
        if not field_path or not kw:
            return True
        # Punkt-Notation: "pull_request.state" → payload["pull_request"]["state"]
        val = event.extra.get("payload", {})
        for key in field_path.split("."):
            if isinstance(val, dict):
                val = val.get(key, "")
            else:
                val = ""
                break
        return kw in str(val).lower()

    if subtype == "git_branch_is":
        branch = params.get("branch", "")
        return bool(branch) and branch == event.extra.get("branch", "")

    if subtype == "git_author_is":
        author = params.get("author", "").lower()
        return bool(author) and author == event.extra.get("author", "").lower()

    if subtype == "git_action_is":
        action = params.get("action", "")
        return bool(action) and action == event.extra.get("action", "")

    # ── Discord-Event-Bedingungen ─────────────────────────────────────────────
    if subtype == "discord_event_is":
        evt = params.get("discord_event", "")
        return not evt or evt == event.extra.get("event", "")

    if subtype == "discord_emoji_is":
        emoji = params.get("emoji", "")
        return bool(emoji) and emoji == event.extra.get("emoji", "")

    if subtype == "discord_user_in_list":
        users = params.get("users", [])
        return event.extra.get("user_id", "") in users

    return True


def _pt(s: str) -> dtime:
    try:
        h, m = map(int, s.split(":"))
        return dtime(h, m)
    except Exception:
        return dtime(0, 0)


# ── Template-Engine ────────────────────────────────────────────────────────────

def _render(text: str, event: ButlerEvent) -> str:
    """Ersetzt {{event.field}} und {{event.extra.subfield}} Platzhalter."""
    def _replace(m: re.Match) -> str:
        path = m.group(1).strip()
        parts = path.split(".")
        if parts and parts[0] == "event":
            parts = parts[1:]
        obj: Any = event
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part, "")
            elif hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                obj = ""
                break
        return str(obj) if obj is not None else ""
    return re.sub(r"\{\{([^}]+)\}\}", _replace, text)


# ── Generische Action-Ausführung ───────────────────────────────────────────────

async def execute_generic_actions(actions: list[dict[str, Any]], event: ButlerEvent) -> None:
    """Führt generische Butler-Aktionen aus (HTTP, E-Mail, Gitea, Discord)."""
    for act in actions:
        sub    = act.get("subtype")
        params = act.get("params", {})
        try:
            if sub == "http_post":
                await _act_http_post(params, event)
            elif sub == "send_email":
                await _act_send_email(params, event)
            elif sub == "git_create_issue":
                await _act_git_create_issue(params, event)
            elif sub == "git_add_comment":
                await _act_git_add_comment(params, event)
            elif sub == "discord_post":
                await _act_discord_post(params, event)
        except Exception as exc:
            logger.warning("Butler action '%s' fehlgeschlagen: %s", sub, exc)


async def _act_http_post(params: dict, event: ButlerEvent) -> None:
    import aiohttp

    url = _render(str(params.get("url", "")), event).strip()
    if not url:
        logger.warning("Butler http_post: url fehlt")
        return

    raw_headers = params.get("headers") or {}
    headers = {k: _render(str(v), event) for k, v in raw_headers.items()}
    headers.setdefault("Content-Type", "application/json")

    body_tpl = str(params.get("body_template", "{}"))
    body_str = _render(body_tpl, event)
    try:
        body = json.loads(body_str)
    except Exception:
        body = {"text": body_str}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=15)) as resp:
            logger.info("Butler http_post → %s: HTTP %s", url, resp.status)


async def _act_send_email(params: dict, event: ButlerEvent) -> None:
    import asyncio as _asyncio
    import smtplib
    from email.mime.text import MIMEText

    kas_path = settings.kas_config
    if not kas_path.exists():
        logger.warning("Butler send_email: /etc/hydrahive/kas.json fehlt")
        return
    try:
        cfg = json.loads(kas_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Butler send_email: KAS-Config Lesefehler: %s", e)
        return

    to      = _render(str(params.get("to", "")), event).strip()
    subject = _render(str(params.get("subject", "(kein Betreff)")), event)
    body    = _render(str(params.get("body", "")), event)

    if not to:
        logger.warning("Butler send_email: Empfänger fehlt")
        return

    from_addr = cfg.get("mail_address", cfg.get("smtp_user", ""))
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]    = from_addr
    msg["To"]      = to
    msg["Subject"] = subject

    def _send_sync():
        with smtplib.SMTP(cfg["smtp_host"], cfg.get("smtp_port", 587), timeout=15) as s:
            s.starttls()
            s.login(cfg["smtp_user"], cfg["smtp_password"])
            s.sendmail(from_addr, [to], msg.as_string())

    await _asyncio.get_event_loop().run_in_executor(None, _send_sync)
    logger.info("Butler send_email → %s: OK", to)


async def _act_git_create_issue(params: dict, event: ButlerEvent) -> None:
    import aiohttp
    from .gitea import _load_config as _gitea_cfg

    repo  = _render(str(params.get("repo", "")), event).strip()
    title = _render(str(params.get("title", "")), event).strip()
    body  = _render(str(params.get("body", "")), event)

    if not repo or not title:
        logger.warning("Butler git_create_issue: repo oder title fehlt")
        return

    cfg   = _gitea_cfg()
    token = cfg.get("token", "")
    base  = cfg.get("url", "http://127.0.0.1:3001").rstrip("/")
    parts = repo.split("/", 1)
    owner = parts[0] if len(parts) == 2 else cfg.get("org", "hydrahive")
    repo_name = parts[1] if len(parts) == 2 else repo

    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            f"{base}/api/v1/repos/{owner}/{repo_name}/issues",
            json={"title": title, "body": body},
            headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15),
        )
        data = await resp.json()
        logger.info("Butler git_create_issue → %s/%s #%s", owner, repo_name, data.get("number", "?"))


async def _act_git_add_comment(params: dict, event: ButlerEvent) -> None:
    import aiohttp
    from .gitea import _load_config as _gitea_cfg

    repo         = _render(str(params.get("repo", "")), event).strip()
    issue_number = _render(str(params.get("issue_number", "")), event).strip()
    body         = _render(str(params.get("body", "")), event)

    if not repo or not issue_number or not body:
        logger.warning("Butler git_add_comment: repo, issue_number oder body fehlt")
        return

    cfg   = _gitea_cfg()
    token = cfg.get("token", "")
    base  = cfg.get("url", "http://127.0.0.1:3001").rstrip("/")
    parts = repo.split("/", 1)
    owner = parts[0] if len(parts) == 2 else cfg.get("org", "hydrahive")
    repo_name = parts[1] if len(parts) == 2 else repo

    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            f"{base}/api/v1/repos/{owner}/{repo_name}/issues/{issue_number}/comments",
            json={"body": body},
            headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15),
        )
        logger.info("Butler git_add_comment → %s/%s #%s: HTTP %s", owner, repo_name, issue_number, resp.status)


async def _act_discord_post(params: dict, event: ButlerEvent) -> None:
    from .tool_registry import _discord_clients

    channel_id = _render(str(params.get("channel_id", "")), event).strip()
    message    = _render(str(params.get("message", "")), event)

    if not channel_id or not message:
        logger.warning("Butler discord_post: channel_id oder message fehlt")
        return

    client = next(iter(_discord_clients.values()), None)
    if client is None:
        logger.warning("Butler discord_post: kein Discord-Client aktiv")
        return

    await client.send_message(channel_id, message)
    logger.info("Butler discord_post → channel %s: OK", channel_id)
