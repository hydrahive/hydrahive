"""
peer_discovery.py — Agent-to-Agent Kommunikation (#490)

Agenten können andere laufende Agenten finden und direkt mit ihnen
kommunizieren — ohne Umweg über den User.

Tools:
- list_peers: Alle aktiven Agenten mit Status und Fähigkeiten
- send_peer_message: Nachricht an einen anderen Agenten senden
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PeerInfo:
    """Informationen über einen aktiven Peer-Agenten."""
    agent_id: str
    name: str
    role: str
    status: str  # "idle" | "busy" | "offline"
    tools: list[str]
    last_seen: float

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "status": self.status,
            "tools_count": len(self.tools),
            "last_seen_ago": f"{int(time.time() - self.last_seen)}s",
        }


class PeerRegistry:
    """Registry aller aktiven Peers — wird vom AgentRuntime aktualisiert."""

    def __init__(self):
        self._peers: dict[str, PeerInfo] = {}
        self._messages: dict[str, list[dict]] = {}  # agent_id → [messages]

    def update_peer(self, agent_id: str, name: str, role: str, status: str, tools: list[str]) -> None:
        self._peers[agent_id] = PeerInfo(
            agent_id=agent_id, name=name, role=role,
            status=status, tools=tools, last_seen=time.time(),
        )

    def remove_peer(self, agent_id: str) -> None:
        self._peers.pop(agent_id, None)

    def list_peers(self, exclude: str = "") -> list[dict]:
        """Alle aktiven Peers (optional einen ausschließen)."""
        now = time.time()
        active = []
        for peer in self._peers.values():
            if peer.agent_id == exclude:
                continue
            # Stale nach 5 Minuten
            if now - peer.last_seen > 300:
                peer.status = "offline"
            active.append(peer.to_dict())
        return active

    def send_message(self, from_agent: str, to_agent: str, content: str) -> bool:
        """Nachricht an einen Peer senden."""
        if to_agent not in self._peers:
            return False
        self._messages.setdefault(to_agent, []).append({
            "from": from_agent,
            "content": content,
            "timestamp": time.time(),
        })
        # Max 50 Messages pro Agent
        if len(self._messages[to_agent]) > 50:
            self._messages[to_agent] = self._messages[to_agent][-50:]
        logger.info("Peer message: %s → %s (%d chars)", from_agent, to_agent, len(content))
        return True

    def get_messages(self, agent_id: str, clear: bool = True) -> list[dict]:
        """Ungelesene Messages für einen Agent abholen."""
        msgs = self._messages.get(agent_id, [])
        if clear:
            self._messages[agent_id] = []
        return msgs


# Globale Singleton-Instanz
peer_registry = PeerRegistry()
