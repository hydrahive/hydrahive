"""
project_loader.py — Filesystem-Discovery fuer Projekte + Hot-Reload (#7)

Analog zu agent_discovery.py:
- Scannt /projects/ beim Start
- watchdog ueberwacht Aenderungen
- Hot-Reload bei project.yaml Aenderungen
"""

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .project_config import ProjectConfig, load_project_config

logger = logging.getLogger(__name__)

# Global Singleton — wird von main.py gesetzt, ist ueber get_project_loader()
# fuer alle Module erreichbar (ohne zyklischen Import via main).
_GLOBAL_LOADER: "ProjectLoader | None" = None


def set_global_loader(loader: "ProjectLoader") -> None:
    global _GLOBAL_LOADER
    _GLOBAL_LOADER = loader


def get_project_loader() -> "ProjectLoader | None":
    """Gibt den global registrierten ProjectLoader zurueck (oder None wenn nicht initialisiert)."""
    return _GLOBAL_LOADER


class ProjectLoader:
    """
    Haelt ein Dict {project_id: ProjectConfig} aktuell.
    Thread-safe ueber ein Lock.
    """

    def __init__(self, projects_dir: str | Path = "/projects") -> None:
        self._dir = Path(projects_dir)
        self._projects: dict[str, ProjectConfig] = {}
        self._lock = threading.Lock()
        self._observer: Observer | None = None

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._scan_all()
        self._start_watcher()
        logger.info("ProjectLoader gestartet — ueberwache %s", self._dir)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    @property
    def projects(self) -> dict[str, ProjectConfig]:
        with self._lock:
            return dict(self._projects)

    def get(self, project_id: str) -> ProjectConfig | None:
        with self._lock:
            return self._projects.get(project_id)

    def register(self, project_dir: Path) -> ProjectConfig | None:
        """Manuell registrieren — z.B. nach Anlage via REST API."""
        return self._register(project_dir)

    # ----------------------------------------------------------------- private

    def _scan_all(self) -> None:
        found = 0
        for entry in self._dir.iterdir():
            if entry.is_dir() and not entry.name.startswith("_deleted_"):
                self._register(entry)
                found += 1
        logger.info("Initialer Scan: %d Projekt-Verzeichnisse gefunden", found)

    def _register(self, project_dir: Path) -> ProjectConfig | None:
        config = load_project_config(project_dir)
        if config is None:
            self._unregister_dir(project_dir)
            return None
        with self._lock:
            existing = self._projects.get(config.id)
            self._projects[config.id] = config
        # Nur loggen wenn neu oder LLM/Boss geändert (Watchdog-Debounce gegen Spam)
        if existing is not None:
            return config
        if getattr(config, "is_v2", False):
            logger.info(
                "Projekt registriert: %s ('%s') | v2 | LLM: %s/%s",
                config.id, config.identity.name,
                config.llm.provider, config.llm.model,
            )
        else:
            logger.info(
                "Projekt registriert: %s ('%s') | Boss: %s | Worker: %s",
                config.id, config.identity.name,
                config.agents.boss,
                ", ".join(config.agents.workers) or "—",
            )
        return config

    def _unregister_dir(self, project_dir: Path) -> None:
        with self._lock:
            to_remove = [
                pid for pid, cfg in self._projects.items()
                if cfg.project_dir == project_dir
            ]
            for pid in to_remove:
                del self._projects[pid]
                logger.info("Projekt deregistriert: %s", pid)

    def _start_watcher(self) -> None:
        handler = _ProjectEventHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._dir), recursive=True)
        self._observer.daemon = True
        self._observer.start()


class _ProjectEventHandler(FileSystemEventHandler):
    # v2 (#598): v2-Dateien sind die kanonische Wahrheit, project.yaml als Legacy
    _CONFIG_FILES = {"config.yaml", "project.yaml", "AGENT.md"}
    _MESSENGER_FILES = {"messenger.yaml"}
    _RELOAD_FILES = _CONFIG_FILES | _MESSENGER_FILES

    def __init__(self, loader: ProjectLoader) -> None:
        self._l = loader

    def _rebuild_messenger_router(self) -> None:
        """Messenger-Router neu aufbauen (messenger.yaml geaendert)."""
        try:
            from .messenger_router import messenger_router as _mr
            _mr.rebuild()
        except Exception as e:
            logger.debug("messenger_router rebuild fehlgeschlagen: %s", e)

    def on_created(self, event: FileSystemEvent) -> None:
        path = Path(event.src_path)
        if event.is_directory:
            self._l._register(path)
        elif path.name in self._CONFIG_FILES:
            self._l._register(path.parent)
        elif path.name in self._MESSENGER_FILES:
            self._rebuild_messenger_router()

    def on_deleted(self, event: FileSystemEvent) -> None:
        path = Path(event.src_path)
        if event.is_directory:
            self._l._unregister_dir(path)
        elif path.name in self._CONFIG_FILES:
            # v2: Nur deregistrieren wenn weder config.yaml noch project.yaml existiert
            parent = path.parent
            if not ((parent / "config.yaml").exists() or (parent / "project.yaml").exists()):
                self._l._unregister_dir(parent)
            else:
                self._l._register(parent)
        elif path.name in self._MESSENGER_FILES:
            self._rebuild_messenger_router()

    def on_moved(self, event: FileSystemEvent) -> None:
        # rename() feuert on_moved, nicht on_deleted — Projekt aus Registry entfernen
        path = Path(event.src_path)
        if event.is_directory:
            self._l._unregister_dir(path)

    def on_modified(self, event: FileSystemEvent) -> None:
        path = Path(event.src_path)
        if event.is_directory:
            return
        if path.name in self._CONFIG_FILES:
            logger.debug("%s geaendert: %s", path.name, path)
            self._l._register(path.parent)
        elif path.name in self._MESSENGER_FILES:
            logger.debug("messenger.yaml geaendert: %s", path)
            self._rebuild_messenger_router()
