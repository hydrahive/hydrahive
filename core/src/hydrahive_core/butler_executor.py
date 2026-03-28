"""butler_executor.py — Flow-Evaluierung für eingehende Messenger-Events

Wird von whatsapp_agent / telegram_agent / discord_agent aufgerufen bevor
ein eingehende Nachricht ans LLM weitergegeben wird.

Gibt eine Liste von Aktionen zurück die ausgeführt werden sollen:
  [{"subtype": "agent_reply", "params": {"agent_id": "..."}, "node_id": "..."}]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime
from typing import Any

import re
from pathlib import Path

from .butler_rule import ButlerFlow, load_flows

logger = logging.getLogger(__name__)

AGENTS_DIR = Path("/agents")


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


def has_active_flows(channel: str, event_type: str = "message") -> bool:
    """Gibt True zurück wenn mindestens ein aktiver Flow für diesen Kanal/Event-Typ existiert."""
    for flow in load_flows():
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


async def check_flows(event: ButlerEvent) -> list[dict[str, Any]]:
    """Prüft alle aktiven Flows gegen ein Event. Gibt Aktionsliste zurück."""
    result: list[dict[str, Any]] = []
    for flow in load_flows():
        if not flow.enabled:
            continue
        try:
            actions = _evaluate_flow(flow, event)
            result.extend(actions)
        except Exception as exc:
            logger.warning("Butler flow %s evaluation error: %s", flow.id, exc)
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
        return event.event_type == "email"

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
