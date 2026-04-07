"""
agentlink_listener.py — WebSocket-Listener für eingehende AgentLink-Handoffs

Verbindet sich beim Start mit dem AgentLink-Hub und subscribt auf
eingehende Handoffs für alle laufenden persönlichen Agenten.
Wenn ein Handoff ankommt, wird er als neue Message in den Orchestrator injiziert.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator as AgentOrchestrator

logger = logging.getLogger(__name__)

_listener_task: asyncio.Task | None = None


async def start_agentlink_listener(
    agent_ids: list[str],
    orchestrator: "AgentOrchestrator",
) -> asyncio.Task | None:
    """
    Startet den WebSocket-Listener für die angegebenen Agent-IDs.
    Gibt den Task zurück (zum späteren Cancelln beim Shutdown).
    """
    from .agentlink_client import _load_config

    cfg = _load_config()
    if not cfg:
        logger.debug("AgentLink nicht konfiguriert — WebSocket-Listener nicht gestartet")
        return None

    task = asyncio.create_task(
        _listener_loop(agent_ids, orchestrator, cfg["url"]),
        name="agentlink-ws-listener",
    )
    logger.info("AgentLink WebSocket-Listener gestartet für %d Agenten", len(agent_ids))
    return task


async def _listener_loop(
    agent_ids: list[str],
    orchestrator: "AgentOrchestrator",
    base_url: str,
) -> None:
    """Haupt-Loop: verbindet sich, subscribt, verarbeitet Events. Reconnect bei Fehler."""
    import websockets

    ws_url = base_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    reconnect_delay = 5

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as ws:
                # Auf alle Agent-Channels subscriben
                for agent_id in agent_ids:
                    await ws.send(json.dumps({
                        "action": "subscribe",
                        "channel": f"agent:{agent_id}",
                    }))
                logger.info("AgentLink WS verbunden, subscribed auf %s", agent_ids)
                reconnect_delay = 5  # Reset bei erfolgreicher Verbindung

                async for raw in ws:
                    try:
                        event = json.loads(raw)
                        await _handle_event(event, agent_ids, orchestrator)
                    except json.JSONDecodeError:
                        pass
                    except Exception as e:
                        logger.error("AgentLink WS Event-Fehler: %s", e)

        except asyncio.CancelledError:
            logger.info("AgentLink WS-Listener gestoppt")
            return
        except Exception as e:
            logger.warning("AgentLink WS-Verbindung unterbrochen: %s — Reconnect in %ds", e, reconnect_delay)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)  # Exponential Backoff bis 60s


async def _handle_event(
    event: dict,
    agent_ids: list[str],
    orchestrator: "AgentOrchestrator",
) -> None:
    """Verarbeitet ein eingehendes AgentLink-Event."""
    event_type = event.get("type") or event.get("event")
    state = event.get("state") or event.get("data") or event

    # Nur Handoff-Events verarbeiten
    if event_type not in ("handoff_received", "state_created", None):
        return

    handoff = state.get("handoff") or {}
    to_agent = handoff.get("to_agent")
    if not to_agent or to_agent not in agent_ids:
        return

    task_info = state.get("task") or {}
    task_desc = task_info.get("description", "")
    from_agent = state.get("agent_id", "unknown")
    state_id = state.get("id", "")
    priority = task_info.get("priority", 3)
    required_skills = handoff.get("required_skills", [])

    logger.info(
        "AgentLink Handoff empfangen: %s → %s (state_id=%s, prio=%s)",
        from_agent, to_agent, state_id, priority,
    )

    if not task_desc:
        logger.warning("AgentLink Handoff ohne task.description — ignoriert")
        return

    # Kontext für den Agenten aufbauen
    knowledge = state.get("knowledge") or {}
    working_mem = state.get("working_memory") or {}
    context_parts = [f"[AgentLink Handoff von {from_agent}]", task_desc]
    if required_skills:
        context_parts.append(f"Benötigte Skills: {', '.join(required_skills)}")
    if knowledge.get("qmd_refs"):
        context_parts.append(f"Relevante A-MEM Skills: {', '.join(knowledge['qmd_refs'])}")
    if working_mem.get("findings"):
        context_parts.append("Kontext:\n" + "\n".join(working_mem["findings"][:3]))

    message_content = "\n\n".join(context_parts)

    # Als neue Message in den Orchestrator injizieren
    from .project_config import ProjectAgents as _PA, ProjectConfig as _PC, ProjectIdentity as _PI

    virtual_cfg = _PC(
        id=to_agent,
        identity=_PI(name=to_agent),
        agents=_PA(boss=to_agent, workers=[]),
    )

    try:
        response_parts: list[str] = []
        async for chunk in orchestrator.handle_message_stream(
            project_id=to_agent,
            project_cfg=virtual_cfg,
            content=message_content,
            sender=f"agentlink:{from_agent}",
            execution_mode="elevated",
        ):
            try:
                data = json.loads(chunk[6:]) if chunk.startswith("data: ") else {}
                if "text" in data:
                    response_parts.append(data["text"])
            except Exception:
                pass

        response = "".join(response_parts).strip()
        logger.info(
            "AgentLink Handoff verarbeitet: %s → %s, Antwort: %d Zeichen",
            from_agent, to_agent, len(response),
        )

        # State als erledigt markieren
        from .agentlink_client import complete_handoff_remote
        if state_id:
            try:
                await complete_handoff_remote(state_id, to_agent,
                                              findings=[response[:500]] if response else None)
            except Exception as e:
                logger.warning("AgentLink complete_handoff fehlgeschlagen: %s", e)

    except Exception as e:
        logger.error("AgentLink Handoff-Verarbeitung fehlgeschlagen für %s: %s", to_agent, e)
