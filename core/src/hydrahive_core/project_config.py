"""
project_config.py — Projekt-Konfiguration laden und validieren

v2: Projekt = Agent. Kein separater Boss-Agent mehr nötig.
    Projekt-Verzeichnis enthält config.yaml (LLM, Failover) + AGENT.md (Persönlichkeit).
    Abwärtskompatibel: liest auch altes project.yaml Format.

v2 Verzeichnisstruktur:
    /projects/{name}/
    ├── config.yaml       ← LLM, Failover, Provider, Plugins
    ├── AGENT.md          ← Persönlichkeit, Fachgebiet, Regeln
    ├── memory/           ← Lokales Wissen
    ├── messenger.yaml    ← Optional: Discord/Telegram/Matrix/WhatsApp
    ├── project.yaml      ← Legacy (v1), wird gelesen wenn config.yaml fehlt
    └── files/            ← Projektdateien
"""

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


# =========================================================================
# v2: LLM-Konfiguration direkt im Projekt
# =========================================================================

class ProjectLlmConfig(BaseModel):
    """LLM-Konfiguration für das Projekt (v2)."""
    model_config = {"extra": "ignore"}

    provider: str = "anthropic"              # anthropic, openai, google, ollama, etc.
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.7
    max_tokens: int = 4096
    thinking_budget: int = 0                 # Extended Thinking (0 = deaktiviert)
    api_key_env: str = ""                    # Env-Variable für API-Key (z.B. "ANTHROPIC_KEY")
    failover: list[dict] = Field(default_factory=list)  # [{provider, model, api_key_env}, ...]


class ProjectMessengerConfig(BaseModel):
    """Messenger-Konfiguration für das Projekt (v2)."""
    model_config = {"extra": "ignore"}

    discord: dict = Field(default_factory=dict)    # {bot_token_env, channels: [...]}
    telegram: dict = Field(default_factory=dict)   # {bot_token_env, chat_ids: [...]}
    matrix: dict = Field(default_factory=dict)     # {room, space}
    whatsapp: dict = Field(default_factory=dict)   # {bridge_url, chat_ids: [...]}


# =========================================================================
# Legacy (v1) Modelle — abwärtskompatibel
# =========================================================================

class ProjectIdentity(BaseModel):
    model_config = {"extra": "ignore"}
    name:        str
    description: str = ""


class ProjectAgents(BaseModel):
    model_config = {"extra": "ignore"}
    boss:    str = ""               # v2: optional, leer = Projekt-eigener Agent
    workers: list[str] = Field(default_factory=list)


class ProjectMatrix(BaseModel):
    model_config = {"extra": "ignore"}
    room:  str = ""
    space: str = ""


class ProjectFilesystem(BaseModel):
    model_config = {"extra": "ignore"}
    path:  str = ""
    samba: bool = True
    nfs:   bool = False


class ProjectSystem(BaseModel):
    model_config = {"extra": "ignore"}
    user:  str = ""
    group: str = ""


class ProjectChat(BaseModel):
    model_config = {"extra": "ignore"}
    show_swarm: bool = False


class ProjectTaskAgents(BaseModel):
    model_config = {"extra": "ignore"}
    ttl:          int = 300
    max_parallel: int = 10


# =========================================================================
# Haupt-Config: Vereint v1 (project.yaml) und v2 (config.yaml)
# =========================================================================

class ProjectConfig(BaseModel):
    model_config = {"extra": "ignore"}

    id:         str
    version:    str = "2.0.0"
    identity:   ProjectIdentity

    # v2: LLM direkt im Projekt (statt über Boss-Agent)
    llm:        ProjectLlmConfig = Field(default_factory=ProjectLlmConfig)

    # v2: Messenger direkt im Projekt
    messenger:  ProjectMessengerConfig = Field(default_factory=ProjectMessengerConfig)

    # v2: Plugins die für dieses Projekt aktiv sind (leer = keine)
    plugins:    list[str] = Field(default_factory=list)

    # v2: Git-Repos und Informationsquellen
    repos:      list[dict] = Field(default_factory=list)    # [{url, branch, local_path}, ...]
    sources:    list[dict] = Field(default_factory=list)     # [{name, url, type}, ...]

    # Legacy (v1) — abwärtskompatibel
    agents:     ProjectAgents     = Field(default_factory=ProjectAgents)
    matrix:     ProjectMatrix     = Field(default_factory=ProjectMatrix)
    filesystem: ProjectFilesystem = Field(default_factory=ProjectFilesystem)
    system:     ProjectSystem     = Field(default_factory=ProjectSystem)
    chat:       ProjectChat       = Field(default_factory=ProjectChat)

    # Shared Sessions (v2): User die Zugriff haben
    members:    list[str]         = Field(default_factory=list)
    github_repo: str              = ""

    # v2: Default Execution-Mode für dieses Projekt (#568)
    execution_mode: str = "safe"  # safe | elevated | unrestricted

    # Risiko-Policy für den Projekt-Boss: "interactive" verlangt CONFIRM-Klicks,
    # "trusted" lässt CONFIRM automatisch durch (DENY bleibt blockiert).
    # Wird über agent_config_from_project an AgentConfig.risk_policy weitergereicht.
    risk_policy: Literal["interactive", "trusted"] = "interactive"

    # v2: Max Tool-Runden pro Chat-Nachricht (#613)
    max_tool_rounds: int = 50

    # Wird nach dem Laden gesetzt
    project_dir: Path | None = Field(default=None, exclude=True)

    # v2: AGENT.md Inhalt (geladen aus Datei, nicht aus YAML)
    agent_md: str = Field(default="", exclude=True)

    def effective_system_user(self) -> str:
        return self.system.user or f"proj_{self.id}"

    def effective_filesystem_path(self) -> str:
        return self.filesystem.path or f"/projects/{self.id}"

    @property
    def all_agents(self) -> list[str]:
        """Boss + alle Worker als flache Liste. v2: kann leer sein."""
        agents = []
        if self.agents.boss:
            agents.append(self.agents.boss)
        agents.extend(self.agents.workers)
        return agents

    @property
    def is_v2(self) -> bool:
        """True wenn Projekt v2-Format nutzt (config.yaml statt project.yaml)."""
        return self.version.startswith("2.")


# =========================================================================
# Laden: unterstützt v2 (config.yaml) und v1 (project.yaml)
# =========================================================================

def load_project_config(project_dir: Path) -> ProjectConfig | None:
    """
    Lädt Projekt-Konfiguration. Prüft in dieser Reihenfolge:
      1. config.yaml (v2-Format)
      2. project.yaml (v1-Format, abwärtskompatibel)

    Lädt zusätzlich AGENT.md wenn vorhanden.
    """
    config = _load_v2_config(project_dir) or _load_v1_config(project_dir)
    if config is None:
        return None

    config.project_dir = project_dir

    # AGENT.md laden (v2: Persönlichkeit/Fachgebiet)
    agent_md_path = project_dir / "AGENT.md"
    if agent_md_path.exists():
        try:
            config.agent_md = agent_md_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("AGENT.md nicht lesbar in %s: %s", project_dir, e)

    return config


def _load_v2_config(project_dir: Path) -> ProjectConfig | None:
    """Lädt config.yaml (v2-Format)."""
    yaml_path = project_dir / "config.yaml"
    if not yaml_path.exists():
        return None

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        logger.warning("YAML-Fehler in %s: %s", yaml_path, e)
        return None

    if not isinstance(raw, dict):
        logger.warning("config.yaml in %s ist kein Mapping", project_dir)
        return None

    # id aus Verzeichnisname wenn nicht in YAML
    raw.setdefault("id", project_dir.name)
    raw.setdefault("version", "2.0.0")
    raw.setdefault("identity", {"name": project_dir.name})

    try:
        return ProjectConfig.model_validate(raw)
    except ValidationError as e:
        logger.warning("Validierungsfehler in %s:\n%s", yaml_path, e)
        return None


def _load_v1_config(project_dir: Path) -> ProjectConfig | None:
    """Lädt project.yaml (v1-Format, abwärtskompatibel)."""
    yaml_path = project_dir / "project.yaml"
    if not yaml_path.exists():
        return None

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        logger.warning("YAML-Fehler in %s: %s", yaml_path, e)
        return None

    if not isinstance(raw, dict):
        logger.warning("project.yaml in %s ist kein Mapping", project_dir)
        return None

    try:
        return ProjectConfig.model_validate(raw)
    except ValidationError as e:
        logger.warning("Validierungsfehler in %s:\n%s", yaml_path, e)
        return None


def load_messenger_config(project_dir: Path) -> ProjectMessengerConfig | None:
    """Lädt messenger.yaml wenn vorhanden."""
    yaml_path = project_dir / "messenger.yaml"
    if not yaml_path.exists():
        return None

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        logger.warning("YAML-Fehler in %s: %s", yaml_path, e)
        return None

    if not isinstance(raw, dict):
        return None

    try:
        return ProjectMessengerConfig.model_validate(raw)
    except ValidationError as e:
        logger.warning("Validierungsfehler in messenger.yaml %s:\n%s", yaml_path, e)
        return None
