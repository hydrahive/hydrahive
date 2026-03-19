"""
agent_config.py — Agent-Konfiguration laden und validieren (#3)

Pflichtfelder: id, type, identity, llm
Unbekannte Felder werden ignoriert (model_config extra="ignore").
Fehlerhafte Configs werden geloggt und übersprungen.
"""

import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class LlmConfig(BaseModel):
    model_config = {"extra": "ignore"}

    model: str
    temperature: float = 0.7
    max_tokens: int = 4096


class AgentConfig(BaseModel):
    model_config = {"extra": "ignore"}

    id: str
    type: Literal["boss", "worker", "specialist"]
    identity: str                      # Name/Persona des Agenten
    llm: LlmConfig
    soul: str | None = None            # Pfad zur soul.md relativ zum Agent-Dir
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    # Pfad zum Agent-Verzeichnis — wird nach dem Laden gesetzt, nicht aus YAML
    agent_dir: Path | None = Field(default=None, exclude=True)


def load_agent_config(agent_dir: Path) -> AgentConfig | None:
    """
    Liest agent.yaml aus agent_dir, validiert und gibt AgentConfig zurück.
    Bei Fehlern: Logging + None (kein Absturz des Callers).
    """
    yaml_path = agent_dir / "agent.yaml"
    if not yaml_path.exists():
        logger.debug("Kein agent.yaml in %s — übersprungen", agent_dir)
        return None

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        logger.warning("YAML-Fehler in %s: %s", yaml_path, e)
        return None

    if not isinstance(raw, dict):
        logger.warning("agent.yaml in %s ist kein Mapping — übersprungen", agent_dir)
        return None

    try:
        config = AgentConfig.model_validate(raw)
    except ValidationError as e:
        logger.warning("Validierungsfehler in %s:\n%s", yaml_path, e)
        return None

    config.agent_dir = agent_dir
    return config
