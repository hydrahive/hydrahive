"""
agent_runtime.py — Agent-Lifecycle: Start / Stop / Restart (#4)

Verwaltet laufende Agenten-Prozesse:
- Core-Agenten (boss, specialist): permanent, starten beim Boot
- Task-Agenten: ephemeral, on-demand vom Boss gespawnt

Jeder Agent läuft als eigener asyncio-Task der eine Endlosschleife
(think → act → heartbeat) ausführt. Bei Heartbeat-Timeout: automatischer Restart.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from .agent_config import AgentConfig

logger = logging.getLogger(__name__)

CORE_TYPES = {"boss", "specialist"}
HEARTBEAT_TIMEOUT = 60.0   # Sekunden bis Restart
TASK_AGENT_TTL    = 300.0  # Max-Laufzeit Task-Agent in Sekunden


class AgentStatus(str, Enum):
    STARTING  = "starting"
    RUNNING   = "running"
    RESTARTING = "restarting"
    STOPPED   = "stopped"
    ERROR     = "error"


@dataclass
class AgentHandle:
    config: AgentConfig
    status: AgentStatus = AgentStatus.STARTING
    last_heartbeat: float = field(default_factory=time.monotonic)
    restart_count: int = 0
    task: asyncio.Task | None = field(default=None, repr=False)


class AgentRuntime:
    """
    Verwaltet alle laufenden Agenten.
    start() und stop() sind die Lifecycle-Methoden für die FastAPI-App.
    """

    def __init__(self) -> None:
        self._handles: dict[str, AgentHandle] = {}
        self._watchdog_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ public

    async def start(self, configs: list[AgentConfig]) -> None:
        """Core-Agenten (boss + specialist) starten."""
        core = [c for c in configs if c.type in CORE_TYPES]
        logger.info("%d Core-Agenten werden gestartet", len(core))
        for config in core:
            await self._spawn(config)
        self._watchdog_task = asyncio.create_task(
            self._watchdog_loop(), name="agent-watchdog"
        )

    async def stop(self) -> None:
        """Alle Agenten sauber stoppen."""
        if self._watchdog_task:
            self._watchdog_task.cancel()
        for handle in list(self._handles.values()):
            await self._cancel_handle(handle)
        self._handles.clear()
        logger.info("AgentRuntime gestoppt")

    async def spawn_task_agent(self, config: AgentConfig) -> str:
        """Task-Agenten on-demand starten (vom Boss aufgerufen)."""
        if config.type not in {"worker"}:
            raise ValueError(f"spawn_task_agent nur für worker, nicht {config.type}")
        await self._spawn(config, ttl=TASK_AGENT_TTL)
        return config.id

    def heartbeat(self, agent_id: str) -> None:
        """Vom Agent-Loop aufgerufen um Liveness zu melden."""
        if handle := self._handles.get(agent_id):
            handle.last_heartbeat = time.monotonic()

    def status_all(self) -> dict[str, dict]:
        return {
            aid: {
                "status": h.status,
                "type": h.config.type,
                "restart_count": h.restart_count,
                "last_heartbeat_age": round(time.monotonic() - h.last_heartbeat, 1),
            }
            for aid, h in self._handles.items()
        }

    # ----------------------------------------------------------------- private

    async def _spawn(self, config: AgentConfig, ttl: float | None = None) -> None:
        # Vorhandenen Task stoppen falls Agent neu gestartet wird
        if existing := self._handles.get(config.id):
            await self._cancel_handle(existing)

        handle = AgentHandle(config=config)
        self._handles[config.id] = handle
        coro = self._run_agent(handle, ttl)
        handle.task = asyncio.create_task(coro, name=f"agent-{config.id}")
        logger.info("Agent gestartet: %s (%s)", config.id, config.type)

    async def _cancel_handle(self, handle: AgentHandle) -> None:
        if handle.task and not handle.task.done():
            handle.task.cancel()
            try:
                await handle.task
            except (asyncio.CancelledError, Exception):
                pass
        handle.status = AgentStatus.STOPPED

    async def _run_agent(self, handle: AgentHandle, ttl: float | None) -> None:
        """
        Haupt-Loop eines Agenten.
        Platzhalter — wird in #5 (Heartbeat) und #8 (Orchestrator) befüllt.
        """
        handle.status = AgentStatus.RUNNING
        handle.last_heartbeat = time.monotonic()
        started_at = time.monotonic()

        try:
            while True:
                # TTL-Check für Task-Agenten
                if ttl and (time.monotonic() - started_at) > ttl:
                    logger.info("Task-Agent %s TTL erreicht — beende", handle.config.id)
                    break

                # Heartbeat aktualisieren (wird in #5 durch echten Heartbeat ersetzt)
                self.heartbeat(handle.config.id)

                # Platzhalter: Agent-Logik kommt in #8
                await asyncio.sleep(10)

        except asyncio.CancelledError:
            pass
        finally:
            handle.status = AgentStatus.STOPPED
            if handle.config.id in self._handles:
                del self._handles[handle.config.id]

    async def _watchdog_loop(self) -> None:
        """Prüft regelmäßig ob Core-Agenten noch leben."""
        try:
            while True:
                await asyncio.sleep(15)
                now = time.monotonic()
                for agent_id, handle in list(self._handles.items()):
                    if handle.config.type not in CORE_TYPES:
                        continue
                    if handle.status != AgentStatus.RUNNING:
                        continue
                    age = now - handle.last_heartbeat
                    if age > HEARTBEAT_TIMEOUT:
                        logger.warning(
                            "Agent %s Heartbeat-Timeout (%.0fs) — starte neu",
                            agent_id, age
                        )
                        handle.status = AgentStatus.RESTARTING
                        handle.restart_count += 1
                        await self._spawn(handle.config)
        except asyncio.CancelledError:
            pass
