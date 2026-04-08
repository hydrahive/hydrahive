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
    thinking_budget: int = 0          # Extended Thinking Token-Budget (0 = deaktiviert)
    fallback_models: list[str] = Field(default_factory=list)
    ollama_base_url: str | None = None   # WKS-Ollama: z.B. "http://192.168.1.101:11434"


class HeartbeatRaw(BaseModel):
    """Rohe Heartbeat-Config aus YAML — Parsing in agent_runtime.py."""
    model_config = {"extra": "ignore"}

    enabled:    bool = True
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
    # AgentLink-Eskalation: wenn gesetzt wird bei Fund ein Handoff geschrieben
    escalate_to:     str | None = None  # Ziel-Agent-ID bei Fund (z.B. "personal_castiel")
    escalate_type:   str        = "bug_fix"  # AgentLink task.type
    escalate_priority: int      = 3          # AgentLink task.priority 1-5
    escalate_skills: list[str]  = Field(default_factory=list)  # required_skills


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

    default: Literal["safe", "elevated", "root", "unrestricted"] = "safe"
    safe: ExecutionModeProfile = Field(default_factory=ExecutionModeProfile)
    elevated: ExecutionModeProfile | None = None
    root: ExecutionModeProfile | None = None
    unrestricted: ExecutionModeProfile | None = None


class AgentSource(BaseModel):
    """Eine Wissensquelle oder Suchmaschine die dem Agenten zugewiesen ist."""
    model_config = {"extra": "ignore"}

    name:        str
    url:         str
    description: str = ""


class AgentConfig(BaseModel):
    model_config = {"extra": "ignore"}

    id:       str
    type:     Literal["boss", "worker", "specialist"]
    identity: str
    llm:      LlmConfig
    soul:     str | None = None
    skills:   list[str] = Field(default_factory=list)
    role:            str | None = None  # #492: reader/assistant/coder/admin — setzt tools + execution_modes automatisch
    tools:           list[str] = Field(default_factory=list)
    tools_extra:     list[str] = Field(default_factory=list)  # #492: zusätzliche Tools on top of role
    tools_deny:      list[str] = Field(default_factory=list)  # #492: Tools explizit verbieten
    tool_selection:  Literal["auto", "always"] = "auto"  # always = alle Tools immer laden (für Spezialisten)
    allowed_agents:  list[str] = Field(default_factory=list)
    mcp_servers:     list[str] = Field(default_factory=list)
    sources:         list[AgentSource] = Field(default_factory=list)
    max_tool_rounds: int       = 20
    compaction_threshold: int | None = None  # Override für Compaction-Threshold (estimated Tokens). None = global default.
    heartbeat: HeartbeatRaw = Field(default_factory=HeartbeatRaw)
    heartbeat_tasks: list[HeartbeatTask] = Field(default_factory=list)
    execution_modes: ExecutionModesConfig | None = None
    ephemeral: bool = False   # Wenn True: Agent wird beim nächsten Core-Start gelöscht
    hooks: dict | None = None  # #472: Hook-System (before_tool, after_tool)

    # Wird nach dem Laden gesetzt, nicht aus YAML
    agent_dir: Path | None = Field(default=None, exclude=True)

    @property
    def _heartbeat_raw(self) -> dict[str, Any]:
        """Fuer agent_runtime.HeartbeatConfig.from_agent_config()."""
        return self.heartbeat.model_dump(exclude_none=True)

    def effective_execution_mode(
        self,
        execution_mode: Literal["safe", "elevated", "root", "unrestricted"] | None = None,
    ) -> Literal["safe", "elevated", "root", "unrestricted"] | None:
        """Aktiven Modus bestimmen, ohne Legacy-Agenten zu beeinflussen."""
        if self.execution_modes is None:
            return None
        return execution_mode or self.execution_modes.default

    def effective_permissions(
        self,
        execution_mode: Literal["safe", "elevated", "root", "unrestricted"] | None = None,
    ) -> list[str] | None:
        """Permissions fuer den aktiven Modus.

        None bedeutet Legacy-Verhalten: keine technische Permission-Filterung.
        'unrestricted' gibt immer None zurück — kein Tool wird gefiltert.
        """
        mode = self.effective_execution_mode(execution_mode)
        if mode is None or self.execution_modes is None:
            return None
        # unrestricted = alles erlaubt, kein Filter
        if mode == "unrestricted":
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

    # #492: Rolle auflösen → tools + execution_modes setzen
    if config.role:
        from .agent_roles import resolve_role, ROLE_PRESETS
        resolved = resolve_role(
            config.role,
            tools_extra=config.tools_extra or None,
            tools_deny=config.tools_deny or None,
        )
        if resolved:
            role_tools, role_exec_modes = resolved
            # Nur überschreiben wenn nicht explizit in YAML gesetzt
            if not raw.get("tools"):
                config.tools = role_tools
            if not raw.get("execution_modes"):
                config.execution_modes = ExecutionModesConfig.model_validate(role_exec_modes)
            # tool_selection aus Rolle (z.B. admin → always)
            if not raw.get("tool_selection"):
                role_preset = ROLE_PRESETS.get(config.role)  # type: ignore[arg-type]
                if role_preset and "tool_selection" in role_preset:
                    config.tool_selection = role_preset["tool_selection"]

    config.agent_dir = agent_dir
    return config
