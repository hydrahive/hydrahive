"""
discord_agent.py — Discord-Bot Integration pro Agent

Jeder persönliche Agent kann einen eigenen Discord Bot haben.
Der Bot lauscht auf Nachrichten in konfigurierten Channels und
leitet sie an den Orchestrator weiter.

Credentials: /etc/hydrahive/agent_tokens/<agent_id>_discord.json
Format: {"bot_token": "...", "guild_id": "...", "channel_ids": ["..."]}
"""

import asyncio
import collections
import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_config import AgentConfig

from .settings import settings

logger = logging.getLogger(__name__)

TOKEN_DIR = settings.agent_tokens_dir


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


def _is_valid_discord_id(s: str) -> bool:
    """Discord-IDs sind rein numerisch (Snowflake, 17–20 Stellen)."""
    return s.isdigit() and 15 <= len(s) <= 20


class DiscordAgentClient(ABC):
    """
    Basisklasse für Discord-Bot-Agenten.
    Kapselt discord.py Client, Message-Handling und Channel-Operationen.
    """

    @staticmethod
    def _sanitize_ids(ids: list[str]) -> set[str]:
        valid = {s for s in ids if _is_valid_discord_id(s)}
        invalid = set(ids) - valid
        if invalid:
            logger.warning("Discord: ungültige IDs ignoriert: %s", invalid)
        return valid

    def __init__(
        self,
        agent_id:       str,
        bot_token:      str,
        guild_id:       str,
        channel_ids:    list[str],
        ignore_bots:    bool = True,
        require_mention: bool = False,
        loop_detection: bool = True,
        loop_bot_threshold: int = 3,
        loop_pingpong_seconds: int = 30,
        loop_cooldown_seconds: int = 300,
        user_whitelist:  list[str] = [],   # noqa: B006
        user_blacklist:  list[str] = [],   # noqa: B006
        role_whitelist:  list[str] = [],   # noqa: B006
        role_blacklist:  list[str] = [],   # noqa: B006
        channel_modes:   dict[str, str] = {},  # noqa: B006
    ) -> None:
        self.agent_id        = agent_id
        self.bot_token       = bot_token
        self.guild_id        = guild_id
        self.channel_ids     = set(channel_ids)
        self.ignore_bots     = ignore_bots
        self.require_mention = require_mention
        self.loop_detection        = loop_detection
        self.loop_bot_threshold    = max(2, loop_bot_threshold)
        self.loop_pingpong_seconds = max(5, loop_pingpong_seconds)
        self.loop_cooldown_seconds = max(10, loop_cooldown_seconds)
        self.user_whitelist  = self._sanitize_ids(user_whitelist)
        self.user_blacklist  = self._sanitize_ids(user_blacklist)
        self.role_whitelist  = self._sanitize_ids(role_whitelist)
        self.role_blacklist  = self._sanitize_ids(role_blacklist)
        self.channel_modes   = dict(channel_modes)
        self._client         = None
        self._running        = False
        # Loop-Detektion: pro Channel eine deque mit (timestamp, is_bot) Einträgen
        self._loop_history: dict[str, collections.deque] = {}
        # Circuit Breaker: channel_id → Zeitpunkt der Öffnung
        self._circuit_open: dict[str, float] = {}

    LOOP_HISTORY_SIZE = 20  # Wie viele Nachrichten zurückschauen
    LOOP_PINGPONG_THRESHOLD = 4  # Fenster-Größe für PingPong-Detektor

    def _check_loop(self, channel_id: str, is_bot: bool) -> bool:
        """
        Prüft ob ein Loop erkannt wurde. Gibt True zurück wenn die Nachricht
        geblockt werden soll (Circuit offen oder Loop erkannt).
        Gibt immer False zurück wenn loop_detection=False.
        """
        if not self.loop_detection:
            return False

        now = time.monotonic()

        # Mensch schreibt: nie blockieren, aber auch nicht zählen
        # Der Circuit Breaker bleibt für Bots offen, auch wenn ein Mensch schreibt
        if not is_bot:
            return False

        # Circuit Breaker: noch offen? (gilt nur für Bots)
        if channel_id in self._circuit_open:
            if now - self._circuit_open[channel_id] < self.loop_cooldown_seconds:
                return True  # Bot geblockt, Circuit noch offen
            else:
                # Circuit schliessen nach Cooldown
                logger.info("Loop-Detektion [%s]: Circuit Breaker schliesst wieder (Channel %s)",
                            self.agent_id, channel_id)
                del self._circuit_open[channel_id]
                self._loop_history.pop(channel_id, None)

        # Nur Bot-Nachrichten zur History hinzufügen
        # → Human-Nachrichten setzen den Zähler NICHT zurück
        if channel_id not in self._loop_history:
            self._loop_history[channel_id] = collections.deque(maxlen=self.LOOP_HISTORY_SIZE)
        history = self._loop_history[channel_id]
        history.append(now)

        # Detektor 1: Zu viele Bot-Nachrichten insgesamt
        if len(history) >= self.loop_bot_threshold:
            logger.warning(
                "Loop-Detektion [%s]: %d Bot-Nachrichten in Channel %s "
                "— Circuit Breaker ausgelöst (%ds Cooldown)",
                self.agent_id, len(history), channel_id, self.loop_cooldown_seconds,
            )
            self._circuit_open[channel_id] = now
            return True

        # Detektor 2: PingPong — viele Bot-Nachrichten in kurzem Zeitfenster
        recent = list(history)
        if len(recent) >= self.LOOP_PINGPONG_THRESHOLD * 2:
            window = recent[-self.LOOP_PINGPONG_THRESHOLD * 2:]
            timespan = window[-1] - window[0]
            if timespan < self.loop_pingpong_seconds:
                logger.warning(
                    "Loop-Detektion [%s]: PingPong erkannt in Channel %s "
                    "(%d Bot-Nachrichten in %.1fs) — Circuit Breaker ausgelöst",
                    self.agent_id, channel_id, len(window), timespan,
                )
                self._circuit_open[channel_id] = now
                return True

        return False

    async def start(self) -> None:
        """Discord-Client initialisieren und verbinden (blockiert bis stop())."""
        import discord

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True  # Für list_members (muss im Developer Portal aktiviert sein)

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
            # @Mention erforderlich wenn konfiguriert
            if self.require_mention and self._client.user not in message.mentions:
                return
            is_bot = message.author.bot
            # Bots hart ignorieren wenn konfiguriert (kein Cross-Agent-Chat)
            if self.ignore_bots and is_bot:
                return
            # Loop-Detektion (Circuit Breaker + PingPong)
            if self._check_loop(str(message.channel.id), is_bot):
                return
            # User-Filter
            author_id = str(message.author.id)
            if author_id in self.user_blacklist:
                return
            if self.user_whitelist and author_id not in self.user_whitelist:
                return
            # Rollen-Filter (nur wenn Member-Objekt mit roles verfügbar)
            if self.role_whitelist or self.role_blacklist:
                try:
                    roles = getattr(message.author, 'roles', None)
                    if roles is not None:
                        author_role_ids = {str(r.id) for r in roles}
                        if self.role_blacklist and author_role_ids & self.role_blacklist:
                            return
                        if self.role_whitelist and not (author_role_ids & self.role_whitelist):
                            return
                    # Kein roles-Attribut (z.B. Webhook/DM) → Whitelist blockiert
                    elif self.role_whitelist:
                        return
                except Exception:
                    logger.debug("Rollen-Filter: roles nicht lesbar für %s", message.author.id)
                    if self.role_whitelist:
                        return
            # Channel-Modus: "ro" = nur lesen, nicht antworten
            channel_mode = self.channel_modes.get(str(message.channel.id), "rw")
            if channel_mode == "ro":
                return
            # @Mention aus Content entfernen bevor weitergegeben
            content = message.content
            if self._client.user in message.mentions:
                content = content.replace(f"<@{self._client.user.id}>", "").replace(f"<@!{self._client.user.id}>", "").strip()
            try:
                await self.on_user_message(
                    channel_id=str(message.channel.id),
                    content=content,
                    author=str(message.author),
                    author_id=str(message.author.id),
                    message_id=str(message.id),
                )
            except Exception as e:
                logger.error("Discord on_message Fehler für Agent %s: %s",
                             self.agent_id, e)

        # ── Butler: Discord-Event-Trigger ────────────────────────────────────

        async def _fire_discord_event(extra: dict) -> None:
            try:
                from .butler_executor import ButlerEvent as _BE, check_flows as _butler, execute_generic_actions as _butler_generic
                import asyncio as _aio
                _owner  = self.agent_id.removeprefix("personal_")
                _bevent = _BE(event_type="discord_event", channel="discord", extra=extra)
                _bacts  = await _butler(_bevent, owner=_owner)
                _aio.create_task(_butler_generic(_bacts, _bevent))
            except Exception as _e:
                logger.debug("Butler discord_event: %s", _e)

        @self._client.event
        async def on_reaction_add(reaction, user):
            if user == self._client.user:
                return
            await _fire_discord_event({
                "event": "reaction_add", "emoji": str(reaction.emoji),
                "message_id": str(reaction.message.id),
                "channel_id": str(reaction.message.channel.id),
                "user_id": str(user.id), "username": str(user),
            })

        @self._client.event
        async def on_reaction_remove(reaction, user):
            if user == self._client.user:
                return
            await _fire_discord_event({
                "event": "reaction_remove", "emoji": str(reaction.emoji),
                "message_id": str(reaction.message.id),
                "channel_id": str(reaction.message.channel.id),
                "user_id": str(user.id), "username": str(user),
            })

        @self._client.event
        async def on_member_join(member):
            await _fire_discord_event({
                "event": "member_join",
                "user_id": str(member.id), "username": str(member),
                "guild_id": str(member.guild.id),
            })

        @self._client.event
        async def on_member_remove(member):
            await _fire_discord_event({
                "event": "member_leave",
                "user_id": str(member.id), "username": str(member),
                "guild_id": str(member.guild.id),
            })

        @self._client.event
        async def on_guild_channel_create(channel):
            await _fire_discord_event({
                "event": "channel_create",
                "channel_id": str(channel.id), "channel_name": str(channel.name),
            })

        @self._client.event
        async def on_guild_channel_delete(channel):
            await _fire_discord_event({
                "event": "channel_delete",
                "channel_id": str(channel.id), "channel_name": str(channel.name),
            })

        logger.info("Discord-Client für Agent '%s' gestartet", self.agent_id)
        # Blockiert bis close() aufgerufen wird
        try:
            await self._client.start(self.bot_token)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "Discord-Client für Agent '%s' Fehler: %s — "
                "Falls 'PrivilegedIntentsRequired': Message Content Intent im "
                "Discord Developer Portal aktivieren (Applications → Bot → Privileged Gateway Intents)",
                self.agent_id, e,
            )
            raise

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

    async def _get_guild(self):
        """Guild-Objekt laden."""
        if not self._client or self._client.is_closed():
            raise RuntimeError("Discord-Client nicht verbunden")
        guild = self._client.get_guild(int(self.guild_id))
        if guild is None:
            guild = await self._client.fetch_guild(int(self.guild_id))
        return guild

    async def list_all_channels(self) -> list[dict]:
        """Alle Channels inkl. Kategorien und Voice-Channels auflisten."""
        guild = await self._get_guild()
        result = []
        for ch in sorted(guild.channels, key=lambda c: (getattr(c, "position", 0))):
            result.append({
                "id":          str(ch.id),
                "name":        ch.name,
                "type":        str(ch.type),
                "category_id": str(ch.category_id) if getattr(ch, "category_id", None) else None,
                "position":    getattr(ch, "position", 0),
            })
        return result

    async def create_category(self, name: str) -> dict:
        """Neue Kategorie erstellen."""
        guild = await self._get_guild()
        cat = await guild.create_category(name)
        return {"id": str(cat.id), "name": cat.name}

    async def create_channel(self, name: str, category_id: str = "", topic: str = "") -> dict:
        """Neuen Text-Channel erstellen, optional in einer Kategorie."""
        guild = await self._get_guild()
        category = None
        if category_id:
            category = guild.get_channel(int(category_id))
        channel = await guild.create_text_channel(name, category=category, topic=topic or None)
        return {"id": str(channel.id), "name": channel.name, "category_id": str(channel.category_id) if channel.category_id else None}

    async def delete_channel(self, channel_id: str) -> dict:
        """Channel oder Kategorie löschen."""
        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(channel_id))
        await channel.delete()
        return {"deleted": True, "channel_id": channel_id}

    async def set_channel_topic(self, channel_id: str, topic: str) -> dict:
        """Channel-Topic setzen."""
        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(channel_id))
        await channel.edit(topic=topic)
        return {"updated": True, "channel_id": channel_id}

    async def rename_channel(self, channel_id: str, name: str) -> dict:
        """Channel umbenennen."""
        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(channel_id))
        await channel.edit(name=name)
        return {"updated": True, "channel_id": channel_id, "new_name": name}

    async def list_roles(self) -> list[dict]:
        """Alle Rollen der Guild auflisten."""
        guild = await self._get_guild()
        return [{"id": str(r.id), "name": r.name, "color": str(r.color)} for r in guild.roles if r.name != "@everyone"]

    async def list_members(self, limit: int = 100) -> list[dict]:
        """Mitglieder der Guild auflisten (benötigt Members Intent)."""
        guild = await self._get_guild()
        members = []
        try:
            async for member in guild.fetch_members(limit=limit):
                members.append({
                    "id":           str(member.id),
                    "username":     str(member.name),
                    "display_name": member.display_name,
                    "roles":        [r.name for r in member.roles if r.name != "@everyone"],
                })
        except Exception:
            # Fallback: gecachte Mitglieder (nur wenn Members Intent nicht aktiviert)
            for member in guild.members:
                members.append({
                    "id":           str(member.id),
                    "username":     str(member.name),
                    "display_name": member.display_name,
                    "roles":        [r.name for r in member.roles if r.name != "@everyone"],
                })
        return members

    async def delete_message(self, channel_id: str, message_id: str) -> dict:
        """Nachricht in einem Channel löschen."""
        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(channel_id))
        msg = await channel.fetch_message(int(message_id))
        await msg.delete()
        return {"deleted": True, "message_id": message_id}

    async def pin_message(self, channel_id: str, message_id: str) -> dict:
        """Nachricht anpinnen."""
        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(channel_id))
        msg = await channel.fetch_message(int(message_id))
        await msg.pin()
        return {"pinned": True, "message_id": message_id}

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
                        return {"ok": False, "invalid_token": resp.status == 401, "error": f"HTTP {resp.status}: {text[:200]}"}
        except Exception as e:
            return {"ok": False, "invalid_token": False, "error": str(e)}

    @property
    def is_connected(self) -> bool:
        return self._client is not None and not self._client.is_closed() and self._running

    @abstractmethod
    async def on_user_message(
        self,
        channel_id: str,
        content:    str,
        author:     str,
        author_id:  str,
        message_id: str,
    ) -> None:
        """Eingehende Nachricht verarbeiten — Subklassen implementieren dies."""


class AgentDiscordClient(DiscordAgentClient):
    """
    Konkrete Discord-Client-Implementierung für HydraHive-Agenten.
    Leitet eingehende Nachrichten an den Orchestrator weiter.
    """

    def __init__(
        self,
        agent_id:        str,
        bot_token:       str,
        guild_id:        str,
        channel_ids:     list[str],
        orchestrator,
        ignore_bots:          bool = True,
        require_mention:      bool = False,
        loop_detection:       bool = True,
        loop_bot_threshold:   int = 3,
        loop_pingpong_seconds: int = 30,
        loop_cooldown_seconds: int = 300,
        user_whitelist:  list[str] = [],   # noqa: B006
        user_blacklist:  list[str] = [],   # noqa: B006
        role_whitelist:  list[str] = [],   # noqa: B006
        role_blacklist:  list[str] = [],   # noqa: B006
        channel_modes:   dict[str, str] = {},  # noqa: B006
    ) -> None:
        super().__init__(
            agent_id, bot_token, guild_id, channel_ids,
            ignore_bots=ignore_bots,
            require_mention=require_mention,
            loop_detection=loop_detection,
            loop_bot_threshold=loop_bot_threshold,
            loop_pingpong_seconds=loop_pingpong_seconds,
            loop_cooldown_seconds=loop_cooldown_seconds,
            user_whitelist=user_whitelist,
            user_blacklist=user_blacklist,
            role_whitelist=role_whitelist,
            role_blacklist=role_blacklist,
            channel_modes=channel_modes,
        )
        self._orchestrator = orchestrator

    async def on_user_message(
        self,
        channel_id: str,
        content:    str,
        author:     str,
        author_id:  str,
        message_id: str,
    ) -> None:
        """Nachricht an Orchestrator weiterleiten und Antwort zurück in Channel."""
        if not content.strip():
            return

        # Bekannten HydraHive-User anhand Discord-User-ID ermitteln
        sender = author
        try:
            from .main import _load_users
            users = _load_users()
            for uname, udata in users.items():
                if udata.get("discord_user_id") == author_id:
                    sender = uname
                    break
        except Exception:
            pass

        logger.info("Discord [%s] %s (id=%s, sender=%s): %s", self.agent_id, author, author_id, sender, content[:80])

        # Butler: Flows gegen eingehende Nachricht prüfen
        _agent_id = self.agent_id
        try:
            from .butler_executor import ButlerEvent as _BE, check_flows as _butler, execute_generic_actions as _butler_generic
            _owner = self.agent_id.removeprefix("personal_")
            _bevent = _BE(channel="discord", contact_id=author_id, contact_name=author, message_text=content)
            _bactions = await _butler(_bevent, owner=_owner)
            import asyncio as _aio
            _aio.create_task(_butler_generic(_bactions, _bevent))
            for _act in _bactions:
                _sub = _act.get("subtype")
                _p   = _act.get("params", {})
                if _sub == "ignore":
                    return
                if _sub == "reply_fixed":
                    _ft = str(_p.get("text", "")).strip()
                    if _ft:
                        await self.send_message(channel_id, _ft)
                    return
                if _sub == "agent_reply_guided":
                    _instr = str(_p.get("instruction", "")).strip()
                    if _instr:
                        from .butler_executor import get_agent_display_name as _gname
                        _name = _gname(_agent_id)
                        content = f"Dein Name ist {_name}.\n[BUTLER-VORGABE: {_instr}]\n{content}"
                if _sub in ("agent_reply", "agent_reply_guided", "forward"):
                    _aid = str(_p.get("agent_id", "")).strip()
                    if _aid:
                        _agent_id = _aid
        except Exception as _be:
            logger.warning("Butler check Discord fehlgeschlagen: %s", _be)

        # v2: Messenger-Router für Projekt-Lookup
        from .messenger_router import messenger_router as _mr
        _project_id = _mr.resolve_discord(channel_id) or _agent_id

        # v2: Projekt-scoped Butler-Flows prüfen (#566)
        try:
            from .butler_executor import check_flows_for_project as _butler_project
            _proj_actions = await _butler_project(_bevent, _project_id)
            if _proj_actions:
                _aio.create_task(_butler_generic(_proj_actions, _bevent))
                for _pact in _proj_actions:
                    _psub = _pact.get("subtype")
                    if _psub == "ignore":
                        return
                    if _psub == "reply_fixed":
                        _pft = str(_pact.get("params", {}).get("text", "")).strip()
                        if _pft:
                            await self.send_message(channel_id, _pft)
                        return
                    if _psub in ("agent_reply", "agent_reply_guided", "forward"):
                        _paid = str(_pact.get("params", {}).get("agent_id", "")).strip()
                        if _paid:
                            _project_id = _paid
        except Exception as _pbe:
            logger.debug("Projekt-Butler check Discord: %s", _pbe)

        # v2 (#585): echte Projekt-Config laden statt Virtual-Fallback
        _real_cfg = None
        try:
            from .project_loader import get_project_loader as _gpl
            _loader = _gpl()
            if _loader is not None:
                _real_cfg = _loader.get(_project_id)
        except Exception as _cfg_err:
            logger.debug("Discord: Konnte echte Projekt-Config nicht laden (%s) — Fallback Virtual", _cfg_err)
        _resolved_cfg = _real_cfg or _build_virtual_cfg(_project_id)

        # Antwort sammeln und senden
        response_parts: list[str] = []
        try:
            async for chunk in self._orchestrator.handle_message_stream(
                project_id  = _project_id,
                project_cfg = _resolved_cfg,
                content     = content,
                sender      = sender,
                execution_mode = "safe",
            ):
                import json as _json
                try:
                    data = _json.loads(chunk[6:]) if chunk.startswith("data: ") else {}
                    if "text" in data:
                        response_parts.append(data["text"])
                except (ValueError, KeyError):
                    pass  # SSE-Chunks ohne JSON sind normal
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
