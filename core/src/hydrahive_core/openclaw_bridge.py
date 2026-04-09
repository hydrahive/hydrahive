"""
openclaw_bridge.py — OpenClaw/Claude Code Bridge (#128)

Adapter damit externe OpenClaw/Claude Code Instanzen als Peers im
HydraHive-Netz auftauchen. Nutzt das bestehende A2A-Protokoll.

Funktionsweise:
1. HydraHive registriert sich bei OpenClaw-Instanzen als A2A-Peer
2. OpenClaw-Agenten können Tasks an HydraHive delegieren
3. HydraHive-Agenten können OpenClaw-Instanzen ansprechen

Protokoll: A2A (Agent-to-Agent) über HTTP POST mit shared secret.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

BRIDGE_CONFIG_PATH = Path("/etc/hydrahive/openclaw_peers.json")


@dataclass
class OpenClawPeer:
    """Eine registrierte OpenClaw/Claude Code Instanz."""
    id: str
    name: str
    url: str  # Base URL der OpenClaw-Instanz
    secret: str = ""
    capabilities: list[str] = field(default_factory=list)
    last_seen: float = 0
    status: str = "unknown"  # "online" | "offline" | "unknown"


class OpenClawBridge:
    """Verwaltet Verbindungen zu OpenClaw/Claude Code Instanzen."""

    def __init__(self):
        self._peers: dict[str, OpenClawPeer] = {}

    def load_config(self) -> None:
        """Peers aus Konfiguration laden."""
        if not BRIDGE_CONFIG_PATH.exists():
            return
        try:
            data = json.loads(BRIDGE_CONFIG_PATH.read_text(encoding="utf-8"))
            for p in data.get("peers", []):
                self._peers[p["id"]] = OpenClawPeer(
                    id=p["id"], name=p.get("name", p["id"]),
                    url=p["url"], secret=p.get("secret", ""),
                    capabilities=p.get("capabilities", []),
                )
            logger.info("OpenClaw Bridge: %d Peers geladen", len(self._peers))
        except Exception as e:
            logger.warning("OpenClaw Bridge Config fehlerhaft: %s", e)

    def save_config(self) -> None:
        """Peers in Konfiguration speichern."""
        data = {
            "peers": [
                {
                    "id": p.id, "name": p.name, "url": p.url,
                    "secret": p.secret, "capabilities": p.capabilities,
                }
                for p in self._peers.values()
            ]
        }
        try:
            BRIDGE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            BRIDGE_CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("OpenClaw Bridge Config speichern fehlgeschlagen: %s", e)

    def add_peer(self, peer_id: str, name: str, url: str, secret: str = "") -> None:
        self._peers[peer_id] = OpenClawPeer(id=peer_id, name=name, url=url, secret=secret)
        self.save_config()
        logger.info("OpenClaw Peer hinzugefügt: %s (%s)", peer_id, url)

    def remove_peer(self, peer_id: str) -> None:
        self._peers.pop(peer_id, None)
        self.save_config()

    def list_peers(self) -> list[dict]:
        return [
            {
                "id": p.id, "name": p.name, "url": p.url,
                "status": p.status, "capabilities": p.capabilities,
                "last_seen": p.last_seen,
            }
            for p in self._peers.values()
        ]

    async def send_task(self, peer_id: str, task: str, context: str = "") -> dict:
        """Task an eine OpenClaw-Instanz senden via A2A-Protokoll."""
        peer = self._peers.get(peer_id)
        if not peer:
            return {"error": f"Peer '{peer_id}' nicht gefunden"}

        import httpx
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # A2A task/send Endpoint
                headers = {"Content-Type": "application/json"}
                if peer.secret:
                    headers["Authorization"] = f"Bearer {peer.secret}"

                body = {
                    "task": {
                        "message": {"role": "user", "parts": [{"text": task}]},
                        "metadata": {"source": "hydrahive", "context": context},
                    }
                }

                r = await client.post(
                    f"{peer.url.rstrip('/')}/a2a/tasks/send",
                    json=body, headers=headers,
                )
                peer.last_seen = time.time()
                if r.status_code < 300:
                    peer.status = "online"
                    return r.json()
                else:
                    return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            peer.status = "offline"
            return {"error": str(e)}

    async def check_health(self, peer_id: str) -> dict:
        """Health-Check einer OpenClaw-Instanz."""
        peer = self._peers.get(peer_id)
        if not peer:
            return {"error": "Peer nicht gefunden"}

        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{peer.url.rstrip('/')}/.well-known/agent.json")
                peer.last_seen = time.time()
                if r.status_code == 200:
                    peer.status = "online"
                    agent_card = r.json()
                    peer.capabilities = agent_card.get("capabilities", [])
                    return {"status": "online", "agent_card": agent_card}
                else:
                    peer.status = "offline"
                    return {"status": "offline", "code": r.status_code}
        except Exception as e:
            peer.status = "offline"
            return {"status": "error", "error": str(e)}


# Globale Singleton-Instanz
openclaw_bridge = OpenClawBridge()
