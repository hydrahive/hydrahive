"""
discord_agent.py — Discord-Bot Integration pro Agent

Jeder persönliche Agent kann einen eigenen Discord Bot haben.
Der Bot lauscht auf Nachrichten in konfigurierten Channels und
leitet sie an den Orchestrator weiter.

Credentials: /etc/octopos/agent_tokens/<agent_id>_discord.json
Format: {"bot_token": "...", "guild_id": "...", "channel_ids": ["..."]}
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_config import AgentConfig

logger = logging.getLogger(__name__)

TOKEN_DIR = Path("/etc/octopos/agent_tokens")


def load_discord_config(agent_id: str) -> dict | None:
    """Discord-Config für einen Agenten laden. None wenn nicht konfiguriert."""
    token_file = TOKEN_DIR / f"{agent_id}_discord.json"
    if not token_file.exists():
        return None
    try:
        data = json.loads(token_file.read_text())
        if not data.get("bot_token"):
            return None
        return data
    except Exception as e:
        logger.warning("Discord-Config für %s nicht lesbar: %s", agent_id, e)
        return None


def save_discord_config(agent_id: str, config: dict) -> None:
    """Discord-Config speichern."""
    token_file = TOKEN_DIR / f"{agent_id}_discord.json"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(json.dumps(config, indent=2))
    token_file.chmod(0o600)


def delete_discord_config(agent_id: str) -> None:
    """Discord-Config löschen."""
    token_file = TOKEN_DIR / f"{agent_id}_discord.json"
    if token_file.exists():
        token_file.unlink()


class DiscordAgentClient(ABC):
    """
    Basisklasse für Discord-Bot-Agenten.
    Kapselt discord.py Client, Message-Handling und Channel-Operationen.
    """

    def __init__(
        self,
        agent_id:    str,
        bot_token:   str,
        guild_id:    str,
        channel_ids: list[str],
    ) -> None:
        self.agent_id    = agent_id
        self.bot_token   = bot_token
        self.guild_id    = guild_id
        self.channel_ids = set(channel_ids)
        self._client     = None
        self._running    = False

    async def start(self) -> None:
        """Discord-Client initialisieren und verbinden (blockiert bis stop())."""
        import discord

        intents = discord.Intents.default()
        intents.message_content = True

        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready():
            logger.info("Discord-Bot für Agent '%s' online als %s",
                        self.agent_id, self._client.user)
            self._running = True

        @self._client.event
        async def on_message(message):
            # Eigene Nachrichten ignorieren
            if message.author == self._client.user:
                return
            # Nur in konfigurierten Channels
            if self.channel_ids and str(message.channel.id) not in self.channel_ids:
                return
            try:
                await self.on_user_message(
                    channel_id=str(message.channel.id),
                    content=message.content,
                    author=str(message.author),
                    message_id=str(message.id),
                )
            except Exception as e:
                logger.error("Discord on_message Fehler für Agent %s: %s",
                             self.agent_id, e)

        logger.info("Discord-Client für Agent '%s' gestartet", self.agent_id)
        # Blockiert bis close() aufgerufen wird
        await self._client.start(self.bot_token)

    async def stop(self) -> None:
        """Discord-Client sauber beenden."""
        self._running = False
        if self._client and not self._client.is_closed():
            await self._client.close()
        logger.info("Discord-Client für Agent '%s' gestoppt", self.agent_id)

    async def send_message(self, channel_id: str, text: str) -> None:
        """Nachricht in einen Channel senden."""
        if not self._client or self._client.is_closed():
            raise RuntimeError("Discord-Client nicht verbunden")
        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(channel_id))
        await channel.send(text)

    async def read_messages(self, channel_id: str, limit: int = 20) -> list[dict]:
        """Letzte Nachrichten aus einem Channel lesen."""
        if not self._client or self._client.is_closed():
            raise RuntimeError("Discord-Client nicht verbunden")
        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(channel_id))
        messages = []
        async for msg in channel.history(limit=limit):
            messages.append({
                "id":        str(msg.id),
                "author":    str(msg.author),
                "content":   msg.content,
                "timestamp": msg.created_at.isoformat(),
            })
        return messages

    async def list_channels(self) -> list[dict]:
        """Alle Text-Channels der konfigurierten Guild auflisten."""
        if not self._client or self._client.is_closed():
            raise RuntimeError("Discord-Client nicht verbunden")
        import discord as _discord
        if not self.guild_id:
            return []
        guild = self._client.get_guild(int(self.guild_id))
        if guild is None:
            guild = await self._client.fetch_guild(int(self.guild_id))
        return [
            {"id": str(ch.id), "name": ch.name, "type": str(ch.type)}
            for ch in guild.channels
            if ch.type == _discord.ChannelType.text
        ]

    async def test_connection(self) -> dict:
        """Bot-Token testen — gibt Bot-Name und ID zurück."""
        import aiohttp
        headers = {"Authorization": f"Bot {self.bot_token}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://discord.com/api/v10/users/@me",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "ok":       True,
                            "bot_name": data.get("username", ""),
                            "bot_id":   data.get("id", ""),
                        }
                    else:
                        text = await resp.text()
                        return {"ok": False, "error": f"HTTP {resp.status}: {text[:200]}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @property
    def is_connected(self) -> bool:
        return self._client is not None and not self._client.is_closed() and self._running

    @abstractmethod
    async def on_user_message(
        self,
        channel_id: str,
        content:    str,
        author:     str,
        message_id: str,
    ) -> None:
        """Eingehende Nachricht verarbeiten — Subklassen implementieren dies."""


class AgentDiscordClient(DiscordAgentClient):
    """
    Konkrete Discord-Client-Implementierung für OctopOS-Agenten.
    Leitet eingehende Nachrichten an den Orchestrator weiter.
    """

    def __init__(
        self,
        agent_id:    str,
        bot_token:   str,
        guild_id:    str,
        channel_ids: list[str],
        orchestrator,          # Orchestrator-Instanz
    ) -> None:
        super().__init__(agent_id, bot_token, guild_id, channel_ids)
        self._orchestrator = orchestrator

    async def on_user_message(
        self,
        channel_id: str,
        content:    str,
        author:     str,
        message_id: str,
    ) -> None:
        """Nachricht an Orchestrator weiterleiten und Antwort zurück in Channel."""
        if not content.strip():
            return

        logger.info("Discord [%s] %s: %s", self.agent_id, author, content[:80])

        # Antwort sammeln und senden
        response_parts: list[str] = []
        try:
            async for chunk in self._orchestrator.handle_message_stream(
                project_id  = self.agent_id,
                project_cfg = _build_virtual_cfg(self.agent_id),
                content     = content,
                sender      = author,
            ):
                import json as _json
                try:
                    data = _json.loads(chunk[6:]) if chunk.startswith("data: ") else {}
                    if "text" in data:
                        response_parts.append(data["text"])
                except Exception:
                    pass
        except Exception as e:
            logger.error("Orchestrator-Fehler für Discord-Agent %s: %s", self.agent_id, e)
            response_parts = [f"Fehler: {e}"]

        response_text = "".join(response_parts).strip()
        if response_text:
            # Discord-Limit: 2000 Zeichen pro Nachricht
            for i in range(0, len(response_text), 1990):
                await self.send_message(channel_id, response_text[i:i+1990])


def _build_virtual_cfg(agent_id: str):
    """Minimale ProjectConfig für den Personal Agent — analog zu main.py."""
    from .project_config import ProjectConfig as _PC, ProjectAgents as _PA, ProjectIdentity as _PI
    return _PC(
        id=agent_id,
        identity=_PI(name=agent_id),
        agents=_PA(boss=agent_id, workers=[]),
    )
