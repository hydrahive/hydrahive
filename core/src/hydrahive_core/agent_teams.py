"""
agent_teams.py — Multi-Agent Team Koordination (#427)

AgentTeam: Agenten registrieren sich mit Rollen, der Koordinator kann
Broadcasts senden und den Team-Status tracken (idle/running/error).

Verwendung:
    team = AgentTeam("dev-team")
    team.add_member("coder", role="developer")
    team.add_member("reviewer", role="reviewer")
    team.set_status("coder", "running")
    team.broadcast("Neuer Task: Feature X implementieren")
"""
from __future__ import annotations

import logging
import time
import yaml
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TeamMember:
    agent_id: str
    role: str = "worker"
    status: str = "idle"  # idle, running, error, done
    last_activity: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentTeam:
    name: str
    members: dict[str, TeamMember] = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def add_member(self, agent_id: str, role: str = "worker") -> TeamMember:
        member = TeamMember(agent_id=agent_id, role=role, last_activity=time.time())
        self.members[agent_id] = member
        logger.info("Team '%s': +%s [%s]", self.name, agent_id, role)
        return member

    def remove_member(self, agent_id: str) -> bool:
        if agent_id in self.members:
            del self.members[agent_id]
            return True
        return False

    def set_status(self, agent_id: str, status: str) -> None:
        if agent_id in self.members:
            self.members[agent_id].status = status
            self.members[agent_id].last_activity = time.time()

    def broadcast(self, message: str, sender: str = "coordinator") -> None:
        entry = {"sender": sender, "message": message, "timestamp": time.time()}
        self.messages.append(entry)
        if len(self.messages) > 100:
            self.messages = self.messages[-100:]
        logger.info("Team '%s' broadcast von %s: %s", self.name, sender, message[:80])

    def get_idle_members(self, role: str | None = None) -> list[TeamMember]:
        return [m for m in self.members.values()
                if m.status == "idle" and (role is None or m.role == role)]

    def get_by_role(self, role: str) -> list[TeamMember]:
        return [m for m in self.members.values() if m.role == role]

    def summary(self) -> dict:
        return {
            "name": self.name,
            "members": {mid: {"role": m.role, "status": m.status} for mid, m in self.members.items()},
            "total": len(self.members),
            "idle": len([m for m in self.members.values() if m.status == "idle"]),
            "running": len([m for m in self.members.values() if m.status == "running"]),
            "recent_messages": self.messages[-5:],
        }


# ── TeamService: YAML-Persistenz (#789) ───────────────────────────────────────

TEAMS_DIR = Path("/etc/hydrahive/teams")


def _ensure_teams_dir() -> None:
    TEAMS_DIR.mkdir(parents=True, exist_ok=True)


def get_team(team_id: str) -> AgentTeam | None:
    """Lädt Team aus /etc/hydrahive/teams/<team_id>.yaml oder None."""
    path = TEAMS_DIR / f"{team_id}.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        team = AgentTeam(name=data.get("name", team_id))
        for m in data.get("members", []):
            team.add_member(m["agent_id"], role=m.get("role", "worker"))
        return team
    except Exception as e:
        logger.warning("Fehler beim Laden von Team %s: %s", team_id, e)
        return None


def list_teams() -> list[str]:
    """Gibt alle Team-IDs zurück."""
    _ensure_teams_dir()
    return [p.stem for p in TEAMS_DIR.glob("*.yaml")]


def save_team(team_id: str, team: AgentTeam) -> None:
    """Speichert Team nach /etc/hydrahive/teams/<team_id>.yaml."""
    _ensure_teams_dir()
    data = {
        "name": team.name,
        "members": [
            {"agent_id": m.agent_id, "role": m.role}
            for m in team.members.values()
        ],
    }
    path = TEAMS_DIR / f"{team_id}.yaml"
    path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
