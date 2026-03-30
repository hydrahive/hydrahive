"""
project_config.py — Projekt-Konfiguration laden und validieren (#7)

Spiegelt project.yaml:
  id, identity, agents (boss + workers), matrix, filesystem, system, chat
Pflichtfelder: id, agents.boss
"""

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class ProjectIdentity(BaseModel):
    model_config = {"extra": "ignore"}
    name:        str
    description: str = ""


class ProjectAgents(BaseModel):
    model_config = {"extra": "ignore"}
    boss:    str
    workers: list[str] = Field(default_factory=list)


class ProjectMatrix(BaseModel):
    model_config = {"extra": "ignore"}
    room: str = ""   # wird beim Erstellen angelegt, kann leer sein


class ProjectFilesystem(BaseModel):
    model_config = {"extra": "ignore"}
    path:  str = ""   # wird aus system.user abgeleitet wenn leer
    samba: bool = True
    nfs:   bool = False


class ProjectSystem(BaseModel):
    model_config = {"extra": "ignore"}
    user:  str = ""   # proj_<id> wenn leer
    group: str = ""   # gleich wie user wenn leer


class ProjectChat(BaseModel):
    model_config = {"extra": "ignore"}
    show_swarm: bool = False   # False = nur Boss-Antworten, True = voller Swarm-Dialog




class ProjectTaskAgents(BaseModel):
    """Konfiguration fuer ephemeral Task-Agenten."""
    model_config = {"extra": "ignore"}
    ttl:          int = 300   # Sekunden bis Task-Agent gestoppt wird
    max_parallel: int = 10    # max gleichzeitige Task-Agenten

class ProjectConfig(BaseModel):
    model_config = {"extra": "ignore"}

    id:         str
    version:    str = "1.0.0"
    identity:   ProjectIdentity
    agents:     ProjectAgents
    matrix:     ProjectMatrix     = Field(default_factory=ProjectMatrix)
    filesystem: ProjectFilesystem = Field(default_factory=ProjectFilesystem)
    system:     ProjectSystem     = Field(default_factory=ProjectSystem)
    chat:       ProjectChat       = Field(default_factory=ProjectChat)
    members:    list[str]         = Field(default_factory=list)  # HydraHive-Usernames die Zugang haben

    # Wird nach dem Laden gesetzt
    project_dir: Path | None = Field(default=None, exclude=True)

    def effective_system_user(self) -> str:
        return self.system.user or f"proj_{self.id}"

    def effective_filesystem_path(self) -> str:
        return self.filesystem.path or f"/projects/{self.id}"

    @property
    def all_agents(self) -> list[str]:
        """Boss + alle Worker als flache Liste."""
        return [self.agents.boss] + self.agents.workers


def load_project_config(project_dir: Path) -> ProjectConfig | None:
    """
    Liest project.yaml aus project_dir, validiert und gibt ProjectConfig zurueck.
    Bei Fehlern: Logging + None.
    """
    yaml_path = project_dir / "project.yaml"
    if not yaml_path.exists():
        logger.debug("Kein project.yaml in %s — uebersprungen", project_dir)
        return None

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        logger.warning("YAML-Fehler in %s: %s", yaml_path, e)
        return None

    if not isinstance(raw, dict):
        logger.warning("project.yaml in %s ist kein Mapping — uebersprungen", project_dir)
        return None

    try:
        config = ProjectConfig.model_validate(raw)
    except ValidationError as e:
        logger.warning("Validierungsfehler in %s:\n%s", yaml_path, e)
        return None

    config.project_dir = project_dir
    return config
