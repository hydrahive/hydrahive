"""
agent_config.py — Agent-Konfiguration laden und validieren (#3)

Pflichtfelder: id, type, identity, llm
Optionale Felder: soul, skills, tools, heartbeat
Unbekannte Felder werden ignoriert.
"""

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class LlmConfig(BaseModel):
    model_config = {"extra": "ignore"}

    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    fallback_models: list[str] = Field(default_factory=list)
    ollama_base_url: str | None = None   # WKS-Ollama: z.B. "http://192.168.1.101:11434"


class HeartbeatRaw(BaseModel):
    """Rohe Heartbeat-Config aus YAML — Parsing in agent_runtime.py."""
    model_config = {"extra": "ignore"}

    interval:   str | int | float | None = None   # z.B. "30s", 30, "2m"
    timeout:    str | int | float | None = None   # z.B. "90s"
    on_failure: str = "restart"                   # restart | stop | alert


class HeartbeatTask(BaseModel):
    """Ein periodischer Task der automatisch an den Agenten geschickt wird."""
    model_config = {"extra": "ignore"}

    id:           str
    message:      str
    schedule:     str | None = None    # Cron-Ausdruck, z.B. "0 8 * * *"
    interval:     int | None = None    # Sekunden-Intervall, z.B. 1800
    project:      str | None = None    # explizites Projekt (sonst: erstes Boss-Projekt)
    active_hours: str | None = None    # z.B. "07:00-22:00"


class ExecutionModeProfile(BaseModel):
    """Permissions-Profil fuer einen technischen Ausfuehrungsmodus."""
    model_config = {"extra": "ignore"}

    permissions: list[str] = Field(default_factory=list)


class ExecutionModesConfig(BaseModel):
    """Technische Tool-Permissions pro Modus.

    Phase 1 haelt die Struktur bewusst klein: globale Tool-Liste aus agent.yaml
    bleibt bestehen, Modi filtern nur ueber Permissions.
    """
    model_config = {"extra": "ignore"}

    default: Literal["safe", "elevated", "root"] = "safe"
    safe: ExecutionModeProfile = Field(default_factory=ExecutionModeProfile)
    elevated: ExecutionModeProfile | None = None
    root: ExecutionModeProfile | None = None


class AgentConfig(BaseModel):
    model_config = {"extra": "ignore"}

    id:       str
    type:     Literal["boss", "worker", "specialist"]
    identity: str
    llm:      LlmConfig
    soul:     str | None = None
    skills:   list[str] = Field(default_factory=list)
    tools:           list[str] = Field(default_factory=list)
    allowed_agents:  list[str] = Field(default_factory=list)
    mcp_servers:     list[str] = Field(default_factory=list)
    max_tool_rounds: int       = 20
    heartbeat: HeartbeatRaw = Field(default_factory=HeartbeatRaw)
    heartbeat_tasks: list[HeartbeatTask] = Field(default_factory=list)
    execution_modes: ExecutionModesConfig | None = None

    # Wird nach dem Laden gesetzt, nicht aus YAML
    agent_dir: Path | None = Field(default=None, exclude=True)

    @property
    def _heartbeat_raw(self) -> dict[str, Any]:
        """Fuer agent_runtime.HeartbeatConfig.from_agent_config()."""
        return self.heartbeat.model_dump(exclude_none=True)

    def effective_execution_mode(
        self,
        execution_mode: Literal["safe", "elevated", "root"] | None = None,
    ) -> Literal["safe", "elevated", "root"] | None:
        """Aktiven Modus bestimmen, ohne Legacy-Agenten zu beeinflussen."""
        if self.execution_modes is None:
            return None
        return execution_mode or self.execution_modes.default

    def effective_permissions(
        self,
        execution_mode: Literal["safe", "elevated", "root"] | None = None,
    ) -> list[str] | None:
        """Permissions fuer den aktiven Modus.

        None bedeutet Legacy-Verhalten: keine technische Permission-Filterung.
        """
        mode = self.effective_execution_mode(execution_mode)
        if mode is None or self.execution_modes is None:
            return None
        profile = getattr(self.execution_modes, mode, None)
        if profile is None:
            return []
        return list(profile.permissions)


def load_agent_config(agent_dir: Path) -> AgentConfig | None:
    """
    Liest agent.yaml aus agent_dir, validiert und gibt AgentConfig zurueck.
    Bei Fehlern: Logging + None.
    """
    yaml_path = agent_dir / "agent.yaml"
    if not yaml_path.exists():
        logger.debug("Kein agent.yaml in %s — uebersprungen", agent_dir)
        return None

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        logger.warning("YAML-Fehler in %s: %s", yaml_path, e)
        return None

    if not isinstance(raw, dict):
        logger.warning("agent.yaml in %s ist kein Mapping — uebersprungen", agent_dir)
        return None

    try:
        config = AgentConfig.model_validate(raw)
    except ValidationError as e:
        logger.warning("Validierungsfehler in %s:\n%s", yaml_path, e)
        return None

    config.agent_dir = agent_dir
    return config
