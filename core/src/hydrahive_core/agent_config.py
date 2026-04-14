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
    api_key_env: str = ""              # v2: Env-Variable für API-Key (z.B. "ANTHROPIC_KEY")


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
    """Profil fuer einen technischen Ausfuehrungsmodus.

    #638: Permissions-Liste entfernt — sie wurde nirgends ausgewertet.
    Profil ist heute leer, bleibt als Pydantic-Anker damit
    `ExecutionModesConfig.{safe,elevated,root,unrestricted}` schemamäßig
    weiter parsbar bleibt; Felder aus Legacy-YAML werden via
    `extra: ignore` stillschweigend verworfen.
    """
    model_config = {"extra": "ignore"}


class ExecutionModesConfig(BaseModel):
    """Aktiver Execution-Mode pro Agent (#638).

    `default` bestimmt den initial gewählten Modus. Modi sind heute keine
    Permission-Filter mehr — siehe `execution_mode_policy.py` (autoritativ
    fuer Shell-Sandbox-Level).
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
    tools:           list[str] = Field(default_factory=list)
    allowed_agents:  list[str] = Field(default_factory=list)  # v1 deprecated — wird ignoriert
    mcp_servers:     list[str] = Field(default_factory=list)
    sources:         list[AgentSource] = Field(default_factory=list)
    max_tool_rounds: int       = 20
    compaction_threshold: int | None = None  # Override für Compaction-Threshold (estimated Tokens). None = global default.
    heartbeat: HeartbeatRaw = Field(default_factory=HeartbeatRaw)
    heartbeat_tasks: list[HeartbeatTask] = Field(default_factory=list)
    execution_modes: ExecutionModesConfig | None = None
    ephemeral: bool = False   # Wenn True: Agent wird beim nächsten Core-Start gelöscht
    hooks: dict | None = None  # #472: Hook-System (before_tool, after_tool)
    # Risiko-Policy: "interactive" = jeder RiskLevel.CONFIRM braucht User-Klick;
    # "trusted" = CONFIRM wird automatisch genehmigt (DENY bleibt blockiert).
    # Bewusste Admin-Entscheidung; Default konservativ.
    risk_policy: Literal["interactive", "trusted"] = "interactive"
    # v2: Plugin-System entfernt — alles über shell_exec

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
        """Aktiven Modus bestimmen, ohne Legacy-Agenten zu beeinflussen.

        #638: einzige autoritative Methode für Execution-Mode. Nur Shell-Sandbox
        wertet das aus — siehe `execution_mode_policy.py` und ShellExecTool.
        """
        if self.execution_modes is None:
            return None
        return execution_mode or self.execution_modes.default


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

    # v2: Rollen-System entfernt — Tools sind fix (9 Core-Tools).
    # execution_modes nur für shell_exec relevant.

    config.agent_dir = agent_dir
    return config


def agent_config_from_project(project_cfg) -> AgentConfig:
    """v2 Bridge: Erzeugt eine AgentConfig aus einer ProjectConfig.

    Ermöglicht v2-Projekten (config.yaml + AGENT.md) den bestehenden
    Orchestrator-Pipeline zu nutzen, ohne dass ein separater Boss-Agent
    in /agents/ existieren muss.

    Die erzeugte AgentConfig hat:
    - id: project_id (statt agent_id)
    - LLM-Config aus config.yaml
    - Soul/AGENT.md als System-Prompt
    - 9 Core-Tools (v2 Standard)
    - agent_dir zeigt auf das Projekt-Verzeichnis
    """
    from .project_config import ProjectConfig

    pcfg: ProjectConfig = project_cfg

    # LLM-Config aus Projekt übernehmen
    llm = LlmConfig(
        model=pcfg.llm.model,
        temperature=pcfg.llm.temperature,
        max_tokens=pcfg.llm.max_tokens,
        thinking_budget=pcfg.llm.thinking_budget,
        fallback_models=[f.get("model", "") for f in pcfg.llm.failover if f.get("model")],
        api_key_env=pcfg.llm.api_key_env,
    )

    # v2 Core-Tools — immer diese 9
    core_tools = [
        "shell_exec", "file_read", "file_write", "file_patch",
        "file_search", "web_search", "read_memory", "write_memory",
        "ask_agent",
    ]

    # #611: execution_mode aus Projekt-Config uebernehmen (war hart auf "safe")
    _default_mode = getattr(pcfg, "execution_mode", "safe") or "safe"
    if _default_mode not in ("safe", "elevated", "unrestricted"):
        _default_mode = "safe"
    # #638: ExecutionModeProfile hat kein permissions-Feld mehr — leeres Profil reicht.
    exec_modes = ExecutionModesConfig(
        default=_default_mode,
        safe=ExecutionModeProfile(),
    )

    return AgentConfig(
        id=pcfg.id,
        type="boss",
        identity=pcfg.identity.name,
        llm=llm,
        soul=None,                       # AGENT.md wird separat injiziert
        tools=core_tools,
        max_tool_rounds=getattr(pcfg, "max_tool_rounds", 50),
        execution_modes=exec_modes,
        agent_dir=pcfg.project_dir,       # Projekt-Verzeichnis = Agent-Verzeichnis
    )
