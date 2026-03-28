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
    """Ein eingehendes Messenger-Event."""
    channel: str          # "whatsapp" | "telegram" | "discord" | "matrix"
    contact_id: str
    contact_name: str = ""
    is_known: bool = False
    message_text: str = ""
    extra: dict = field(default_factory=dict)


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
    if subtype == "message_received":
        channel = data.get("params", {}).get("channel", "all")
        return channel == "all" or channel == event.channel
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

    return True


def _pt(s: str) -> dtime:
    try:
        h, m = map(int, s.split(":"))
        return dtime(h, m)
    except Exception:
        return dtime(0, 0)
