"""
agent_runtime.py — Agent-Lifecycle: Start / Stop / Restart + Heartbeat (#4, #5, #26)

Verwaltet laufende Agenten-Prozesse:
- Core-Agenten (boss, specialist): permanent, starten beim Boot
- Task-Agenten: ephemeral, on-demand vom Boss gespawnt

Heartbeat-Konfiguration kommt aus agent.yaml:
  heartbeat:
    interval: 30s     # wie oft der Agent pingen soll
    timeout: 90s      # nach wie vielen Sekunden Restart
    on_failure: restart | stop | alert
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum

from .agent_config import AgentConfig

logger = logging.getLogger(__name__)

CORE_TYPES = {"boss", "specialist"}

# Defaults falls agent.yaml keinen heartbeat-Block hat
DEFAULT_INTERVAL = 30.0
DEFAULT_TIMEOUT  = 90.0
DEFAULT_ON_FAILURE = "restart"

TASK_AGENT_TTL         = 300.0  # Max-Laufzeit Task-Agent in Sekunden
WATCHDOG_TICK          = 10.0   # Wie oft der Watchdog prueft
MATRIX_RESTART_DELAY_S = 15.0   # Wartezeit vor Matrix-Client Neustart (#61)


def _parse_duration(value: str | int | float, default: float) -> float:
    """
    Parst Zeitangaben aus YAML:
      "30s" -> 30.0
      "2m"  -> 120.0
      "1h"  -> 3600.0
      42    -> 42.0
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smh]?)", value)
    if not match:
        logger.warning("Unbekanntes Zeitformat: %r — verwende Default %.0fs", value, default)
        return default
    num = float(match.group(1))
    unit = match.group(2)
    return num * {"s": 1, "m": 60, "h": 3600, "": 1}[unit]


@dataclass
class HeartbeatConfig:
    interval: float = DEFAULT_INTERVAL
    timeout: float  = DEFAULT_TIMEOUT
    on_failure: str = DEFAULT_ON_FAILURE   # restart | stop | alert
    enabled: bool   = True

    @classmethod
    def from_agent_config(cls, config: AgentConfig) -> "HeartbeatConfig":
        """Liest heartbeat-Block aus AgentConfig.extra_fields oder raw dict."""
        raw = getattr(config, "_heartbeat_raw", None) or {}
        return cls(
            interval   = _parse_duration(raw.get("interval"),   DEFAULT_INTERVAL),
            timeout    = _parse_duration(raw.get("timeout"),    DEFAULT_TIMEOUT),
            on_failure = raw.get("on_failure", DEFAULT_ON_FAILURE),
            enabled    = bool(raw.get("enabled", True)),
        )


class AgentStatus(str, Enum):
    STARTING   = "starting"
    RUNNING    = "running"
    RESTARTING = "restarting"
    STOPPED    = "stopped"
    ERROR      = "error"


@dataclass
class AgentHandle:
    config:           AgentConfig
    heartbeat_cfg:    HeartbeatConfig
    status:           AgentStatus = AgentStatus.STARTING
    last_heartbeat:   float = field(default_factory=time.monotonic)
    restart_count:    int   = 0
    task:             asyncio.Task | None = field(default=None, repr=False)
    matrix_client:    object | None = field(default=None, repr=False)  # MatrixAgent
    current_activity: str | None = field(default=None, repr=False)  # Live-Aktivität (z.B. "Denkt…", "Tool: shell_exec")
    discord_client: object | None = field(default=None, repr=False)  # AgentDiscordClient
    # #373: Performance Metrics
    total_requests:     int   = 0
    total_errors:       int   = 0
    total_response_ms:  float = 0.0
    last_response_ms:   float = 0.0

    @property
    def avg_response_ms(self) -> float:
        return self.total_response_ms / self.total_requests if self.total_requests else 0.0

    @property
    def error_rate(self) -> float:
        return self.total_errors / self.total_requests * 100 if self.total_requests else 0.0


class AgentRuntime:
    """
    Verwaltet alle laufenden Agenten.
    start() und stop() sind die Lifecycle-Methoden fuer die FastAPI-App.
    """

    def __init__(self) -> None:
        self._handles: dict[str, AgentHandle] = {}
        self._watchdog_task: asyncio.Task | None = None
        self._discord_tasks: dict[str, asyncio.Task] = {}

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
            raise ValueError(f"spawn_task_agent nur fuer worker, nicht {config.type}")
        await self._spawn(config, ttl=TASK_AGENT_TTL)
        return config.id

    async def reload_agent_config(self, config: AgentConfig, *, start_if_missing: bool = False) -> bool:
        """
        Laufenden Core-Agenten mit frisch geladener AgentConfig aktualisieren.

        Config-Schreibwege aktualisieren Discovery sofort; der Runtime-Handle
        hält aber eine eigene AgentConfig-Instanz. Ohne diesen Reload laufen
        Status/Heartbeat und angehängte Clients bis zum Core-Neustart mit der
        alten Konfiguration weiter.
        """
        if config.type not in CORE_TYPES:
            return False

        handle = self._handles.get(config.id)
        if handle is None:
            if not start_if_missing:
                return False
            await self._spawn(config)
            return True

        await self._cancel_handle(handle)
        handle.config = config
        handle.heartbeat_cfg = HeartbeatConfig.from_agent_config(config)
        handle.current_activity = None
        handle.status = AgentStatus.STARTING
        handle.last_heartbeat = time.monotonic()
        handle.task = asyncio.create_task(
            self._run_agent(handle, ttl=None), name=f"agent-{config.id}"
        )
        self._handles[config.id] = handle
        logger.info("Agent-Konfiguration neu geladen: %s (%s)", config.id, config.type)
        return True

    async def attach_matrix_client(self, agent_id: str, matrix_client: object) -> None:
        """
        Matrix-Client an laufenden Agenten hängen und Task neu starten.
        Wird nach dem Provisioning oder beim Core-Start aufgerufen wenn
        ein Projekt bereits einen konfigurierten Matrix-Room hat.
        """
        handle = self._handles.get(agent_id)
        if not handle:
            logger.warning("attach_matrix_client: Agent '%s' nicht gefunden", agent_id)
            return
        # Laufenden Heartbeat-Task stoppen
        await self._cancel_handle(handle)
        handle.matrix_client = matrix_client
        handle.status = AgentStatus.STARTING
        # Mit Matrix-Client neu starten
        coro = self._run_agent(handle, ttl=None)
        handle.task = asyncio.create_task(coro, name=f"agent-{agent_id}")
        self._handles[agent_id] = handle
        logger.info("Matrix-Client an Agent %s angehängt, Task neu gestartet", agent_id)

    async def attach_discord_client(self, agent_id: str, discord_client: object) -> None:
        """Discord-Client starten (unabhängig vom Agent-Handle — auch für Personal Agents)."""
        # Alten Client stoppen falls vorhanden (im Handle oder in _discord_tasks)
        handle = self._handles.get(agent_id)
        if handle and handle.discord_client is not None:
            try:
                await handle.discord_client.stop()
            except Exception:
                pass
            handle.discord_client = discord_client
        # Alten Task canceln falls vorhanden
        old_task = self._discord_tasks.get(agent_id)
        if old_task and not old_task.done():
            old_task.cancel()
            try:
                await old_task
            except (asyncio.CancelledError, Exception):
                pass
        task = asyncio.create_task(discord_client.start(), name=f"discord-{agent_id}")
        self._discord_tasks[agent_id] = task
        logger.info("Discord-Client für '%s' gestartet", agent_id)

    async def detach_discord_client(self, agent_id: str) -> None:
        """Discord-Client stoppen und entfernen."""
        # Task canceln
        task = self._discord_tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        # Handle-Referenz bereinigen
        handle = self._handles.get(agent_id)
        if handle and handle.discord_client is not None:
            try:
                await handle.discord_client.stop()
            except Exception:
                pass
            handle.discord_client = None
        logger.info("Discord-Client für '%s' getrennt", agent_id)

    def heartbeat(self, agent_id: str) -> None:
        """Vom Agent-Loop oder REST-Endpoint aufgerufen um Liveness zu melden."""
        if handle := self._handles.get(agent_id):
            handle.last_heartbeat = time.monotonic()
            logger.debug("Heartbeat: %s", agent_id)

    def status_all(self) -> dict[str, dict]:
        now = time.monotonic()
        return {
            aid: {
                "status":             h.status,
                "type":               h.config.type,
                "model":              getattr(getattr(h.config, "llm", None), "model", None),
                "identity":           getattr(h.config, "identity", aid),
                "restart_count":      h.restart_count,
                "last_heartbeat_age": round(now - h.last_heartbeat, 1),
                "heartbeat_timeout":  h.heartbeat_cfg.timeout,
                "heartbeat_interval": h.heartbeat_cfg.interval,
                "on_failure":         h.heartbeat_cfg.on_failure,
                "heartbeat_enabled":  h.heartbeat_cfg.enabled,
                "current_activity":   h.current_activity,
                # #373: Performance Metrics
                "total_requests":   h.total_requests,
                "avg_response_ms":  round(h.avg_response_ms, 1),
                "last_response_ms": round(h.last_response_ms, 1),
                "error_rate":       round(h.error_rate, 1),
            }
            for aid, h in self._handles.items()
        }

    def set_activity(self, agent_id: str, activity: str | None) -> None:
        """Setzt die aktuelle Aktivität eines Agenten (für Live-Statusanzeige)."""
        if handle := self._handles.get(agent_id):
            handle.current_activity = activity

    async def stop_agent_task(self, agent_id: str) -> bool:
        """
        Bricht den laufenden Task eines Agenten ab (Notfall-Stop).
        Gibt True zurück wenn ein Task gecancelt wurde, sonst False.
        """
        handle = self._handles.get(agent_id)
        if not handle:
            return False
        if handle.task and not handle.task.done():
            await self._cancel_handle(handle)
            handle.current_activity = None
            logger.warning("Agent '%s' per Notfall-Stop abgebrochen", agent_id)
            return True
        return False

    # ----------------------------------------------------------------- private

    async def _spawn(self, config: AgentConfig, ttl: float | None = None) -> None:
        if existing := self._handles.get(config.id):
            await self._cancel_handle(existing)

        hb_cfg = HeartbeatConfig.from_agent_config(config)
        handle = AgentHandle(config=config, heartbeat_cfg=hb_cfg)
        self._handles[config.id] = handle
        coro = self._run_agent(handle, ttl)
        handle.task = asyncio.create_task(coro, name=f"agent-{config.id}")
        logger.info(
            "Agent gestartet: %s (%s) | HB interval=%.0fs timeout=%.0fs on_failure=%s",
            config.id, config.type,
            hb_cfg.interval, hb_cfg.timeout, hb_cfg.on_failure,
        )

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
        Hat der Agent einen MatrixAgent-Client → run() übernimmt den Loop.
        Sonst: Heartbeat-Ticker als Fallback.
        """
        handle.status = AgentStatus.RUNNING
        handle.last_heartbeat = time.monotonic()
        started_at = time.monotonic()

        # Matrix-Client aus Handle nehmen falls vorhanden
        matrix_client = getattr(handle, "matrix_client", None)

        try:
            if matrix_client is not None:
                # Restart-Loop: bei unerwartetem Verbindungsabbruch neu verbinden (#61)
                while True:
                    try:
                        await matrix_client.start()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(
                            "Matrix-Client %s start() fehlgeschlagen: %s — Neustart in %.0fs",
                            handle.config.id, e, MATRIX_RESTART_DELAY_S,
                        )
                        await asyncio.sleep(MATRIX_RESTART_DELAY_S)
                        continue

                    matrix_task = asyncio.create_task(
                        matrix_client.run(),
                        name=f"matrix-{handle.config.id}",
                    )

                    async def _ticker():
                        while True:
                            if ttl and (time.monotonic() - started_at) > ttl:
                                matrix_task.cancel()
                                break
                            await asyncio.sleep(handle.heartbeat_cfg.interval)
                            self.heartbeat(handle.config.id)

                    ticker_task = asyncio.create_task(_ticker(), name=f"ticker-{handle.config.id}")
                    try:
                        await matrix_task
                        # Normales Ende (_running=False) → Restart-Loop verlassen
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(
                            "Matrix-Client %s unerwartet beendet: %s — Neustart in %.0fs",
                            handle.config.id, e, MATRIX_RESTART_DELAY_S,
                        )
                    finally:
                        ticker_task.cancel()

                    await asyncio.sleep(MATRIX_RESTART_DELAY_S)
            else:
                # Fallback: reiner Heartbeat-Ticker (kein Matrix)
                while True:
                    if ttl and (time.monotonic() - started_at) > ttl:
                        logger.info("Task-Agent %s TTL erreicht — beende", handle.config.id)
                        break
                    await asyncio.sleep(handle.heartbeat_cfg.interval)
                    self.heartbeat(handle.config.id)

        except asyncio.CancelledError:
            if matrix_client is not None:
                await matrix_client.stop()
        finally:
            handle.status = AgentStatus.STOPPED
            if handle.config.id in self._handles:
                del self._handles[handle.config.id]

    async def _watchdog_loop(self) -> None:
        """
        Prueft regelmaessig ob Core-Agenten noch leben.
        Reagiert gemaess on_failure aus der jeweiligen agent.yaml.
        """
        try:
            while True:
                await asyncio.sleep(WATCHDOG_TICK)
                now = time.monotonic()
                for agent_id, handle in list(self._handles.items()):
                    if handle.config.type not in CORE_TYPES:
                        continue
                    if handle.status != AgentStatus.RUNNING:
                        continue

                    if not handle.heartbeat_cfg.enabled:
                        continue

                    age = now - handle.last_heartbeat
                    if age <= handle.heartbeat_cfg.timeout:
                        continue

                    on_failure = handle.heartbeat_cfg.on_failure
                    logger.warning(
                        "Agent %s Heartbeat-Timeout (%.0fs > %.0fs) — on_failure=%s",
                        agent_id, age, handle.heartbeat_cfg.timeout, on_failure,
                    )

                    if on_failure == "restart":
                        handle.status = AgentStatus.RESTARTING
                        handle.restart_count += 1
                        await self._spawn(handle.config)

                    elif on_failure == "stop":
                        handle.status = AgentStatus.ERROR
                        await self._cancel_handle(handle)
                        logger.error("Agent %s gestoppt nach Timeout", agent_id)

                    elif on_failure == "alert":
                        # Platzhalter — wird in #12 (REST API) mit Webhook verbunden
                        handle.status = AgentStatus.ERROR
                        logger.error(
                            "ALERT: Agent %s Heartbeat-Timeout — manuelle Intervention noetig",
                            agent_id,
                        )

        except asyncio.CancelledError:
            pass
