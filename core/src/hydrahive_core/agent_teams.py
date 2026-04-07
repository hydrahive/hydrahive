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


# ── Team Registry ──────────────────────────────────────────────────────────────

_teams: dict[str, AgentTeam] = {}


def get_or_create_team(name: str) -> AgentTeam:
    if name not in _teams:
        _teams[name] = AgentTeam(name=name)
    return _teams[name]


def get_team(name: str) -> AgentTeam | None:
    return _teams.get(name)


def list_teams() -> list[dict]:
    return [t.summary() for t in _teams.values()]


def delete_team(name: str) -> bool:
    return _teams.pop(name, None) is not None
