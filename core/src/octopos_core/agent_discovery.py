"""
agent_discovery.py — Filesystem-Discovery + Hot-Reload (#2)

Beim Start: alle /agents/<name>/ mit agent.yaml einlesen.
watchdog überwacht das Verzeichnis — neuer Ordner = sofort registriert,
entfernter Ordner = sofort deregistriert. Kein Neustart nötig.
"""

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .agent_config import AgentConfig, load_agent_config

logger = logging.getLogger(__name__)


class AgentDiscovery:
    """
    Hält ein Dict {agent_id: AgentConfig} aktuell.
    Thread-safe über ein Lock.
    """

    def __init__(self, agents_dir: str | Path = "/agents"):
        self._dir = Path(agents_dir)
        self._agents: dict[str, AgentConfig] = {}
        self._lock = threading.Lock()
        self._observer: Observer | None = None

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Initialen Scan + watchdog starten."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self._scan_all()
        self._start_watcher()
        logger.info("AgentDiscovery gestartet — überwache %s", self._dir)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    @property
    def agents(self) -> dict[str, AgentConfig]:
        with self._lock:
            return dict(self._agents)

    def get(self, agent_id: str) -> AgentConfig | None:
        with self._lock:
            return self._agents.get(agent_id)

    # ----------------------------------------------------------------- private

    def _scan_all(self) -> None:
        found = 0
        for entry in self._dir.iterdir():
            if entry.is_dir():
                self._register(entry)
                found += 1
        logger.info("Initialer Scan: %d Agenten-Verzeichnisse gefunden", found)

    def _register(self, agent_dir: Path) -> None:
        config = load_agent_config(agent_dir)
        if config is None:
            return
        with self._lock:
            self._agents[config.id] = config
        logger.info("Agent registriert: %s (%s)", config.id, config.type)

    def _unregister_dir(self, agent_dir: Path) -> None:
        with self._lock:
            to_remove = [
                aid for aid, cfg in self._agents.items()
                if cfg.agent_dir == agent_dir
            ]
            for aid in to_remove:
                del self._agents[aid]
                logger.info("Agent deregistriert: %s", aid)

    def _start_watcher(self) -> None:
        handler = _DiscoveryEventHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._dir), recursive=False)
        self._observer.daemon = True
        self._observer.start()


class _DiscoveryEventHandler(FileSystemEventHandler):
    def __init__(self, discovery: AgentDiscovery) -> None:
        self._d = discovery

    def on_created(self, event: FileSystemEvent) -> None:
        path = Path(event.src_path)
        if event.is_directory:
            logger.debug("Neues Verzeichnis: %s", path)
            self._d._register(path)
        elif path.name == "agent.yaml":
            self._d._register(path.parent)

    def on_deleted(self, event: FileSystemEvent) -> None:
        path = Path(event.src_path)
        if event.is_directory:
            self._d._unregister_dir(path)
        elif path.name == "agent.yaml":
            self._d._unregister_dir(path.parent)

    def on_modified(self, event: FileSystemEvent) -> None:
        path = Path(event.src_path)
        if not event.is_directory and path.name == "agent.yaml":
            logger.debug("agent.yaml geändert: %s", path)
            self._d._register(path.parent)  # überschreibt vorhandenen Eintrag
