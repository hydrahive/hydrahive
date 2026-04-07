"""
agentlink_client.py — HTTP-Client für den echten AgentLink-Hub

Liest Konfiguration aus /etc/hydrahive/agentlink.json:
  {"url": "http://127.0.0.1:8010", "enabled": true}

Wenn nicht konfiguriert oder nicht erreichbar → Fallback auf file-basiertes System.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from .settings import settings

logger = logging.getLogger(__name__)

_CONFIG_PATH = settings.agentlink_config
_config_cache: dict | None = None


def _load_config() -> dict | None:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not _CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(_CONFIG_PATH.read_text())
        if data.get("enabled") and data.get("url"):
            _config_cache = data
            return _config_cache
    except Exception as e:
        logger.warning("AgentLink-Config nicht lesbar: %s", e)
    return None


def is_available() -> bool:
    """Prüft ob AgentLink konfiguriert und (laut Config) aktiv ist."""
    return _load_config() is not None


async def write_handoff_remote(
    from_agent: str,
    to_agent: str | None,
    context: str,
    data: dict,
    task_type: str = "feature",
    priority: int = 3,
) -> dict:
    """Schreibt einen Handoff via AgentLink REST API."""
    import httpx

    cfg = _load_config()
    if not cfg:
        raise RuntimeError("AgentLink nicht konfiguriert")

    url = cfg["url"].rstrip("/")
    payload = {
        "agent_id": from_agent,
        "task": {
            "type":        task_type,
            "description": context,
            "priority":    priority,
            "status":      "pending",
        },
        "context":        {"files": [], "errors": []},
        "knowledge":      {"amem_ids": [], "qmd_refs": [], "external_urls": []},
        "working_memory": {"hypotheses": [], "open_questions": [], "decisions": [], "findings": []},
    }
    if data:
        payload["working_memory"]["findings"] = [json.dumps(data)]
    if to_agent:
        payload["handoff"] = {
            "to_agent":       to_agent,
            "reason":         context[:200],
            "required_skills": [],
        }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{url}/states", json=payload)
        resp.raise_for_status()
        result = resp.json()
        logger.info("AgentLink write_handoff: %s → %s (state_id=%s)",
                    from_agent, to_agent or "any", result.get("id"))
        return {
            "handoff_id": result.get("id"),
            "to_agent":   to_agent,
            "status":     "created",
            "backend":    "agentlink",
        }


async def read_handoff_remote(agent_id: str, consume: bool = True) -> dict | None:
    """Liest den nächsten Handoff für diesen Agenten via AgentLink REST API."""
    import httpx

    cfg = _load_config()
    if not cfg:
        return None

    url = cfg["url"].rstrip("/")

    async with httpx.AsyncClient(timeout=10) as client:
        # Pending States für diesen Agenten suchen
        resp = await client.get(f"{url}/states", params={
            "limit": 10,
        })
        resp.raise_for_status()
        states = resp.json()

        # Handoffs die an diesen Agenten gerichtet sind
        for state in states:
            handoff = state.get("handoff") or {}
            to_agent = handoff.get("to_agent")
            status = (state.get("task") or {}).get("status", "")
            if to_agent == agent_id and status == "pending":
                if consume:
                    # Als in_progress markieren via Claim
                    try:
                        await client.post(f"{url}/api/states/{state['id']}/claim",
                                          json={"agent_id": agent_id, "duration_minutes": 60})
                    except Exception:
                        pass
                return {
                    "handoff_id": state["id"],
                    "from_agent": state.get("agent_id"),
                    "to_agent":   agent_id,
                    "context":    (state.get("task") or {}).get("description", ""),
                    "data":       {"state": state},
                    "backend":    "agentlink",
                }

    return None


async def complete_handoff_remote(state_id: str, agent_id: str, findings: list[str] | None = None) -> dict:
    """Markiert einen AgentLink-State als erledigt."""
    import httpx

    cfg = _load_config()
    if not cfg:
        raise RuntimeError("AgentLink nicht konfiguriert")

    url = cfg["url"].rstrip("/")

    async with httpx.AsyncClient(timeout=10) as client:
        # Claim freigeben
        await client.post(f"{url}/api/states/{state_id}/release",
                          params={"agent_id": agent_id})
        logger.info("AgentLink complete_handoff: state_id=%s agent=%s", state_id, agent_id)
        return {"state_id": state_id, "status": "released", "backend": "agentlink"}
