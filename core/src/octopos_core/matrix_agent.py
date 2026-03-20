"""
matrix_agent.py — Matrix-nio Basisklasse für Agenten (#26, #27, #28, #29)

Jeder Agent erbt von MatrixAgent. Er:
- loggt sich als @<agent-id>:<server> in conduwuit ein
- joined seine zugewiesenen Rooms beim Start
- lauscht auf eingehende Nachrichten
- leitet User-Nachrichten an den Orchestrator weiter (#28)
- kann selbst Nachrichten in Rooms schicken (#29)

Credentials werden in /etc/octopos/agent_tokens/ gespeichert,
damit der Bot-Account bei Core-Neustart nicht neu registriert werden muss.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

from nio import (
    AsyncClient,
    AsyncClientConfig,
    LoginResponse,
    MatrixRoom,
    RoomMessageText,
    RoomMemberEvent,
    SyncError,
)

from .agent_config import AgentConfig

logger = logging.getLogger(__name__)

CONDUWUIT_URL   = "http://127.0.0.1:6167"
TOKEN_DIR       = Path("/etc/octopos/agent_tokens")
SYNC_TIMEOUT_MS = 30_000    # 30s Long-Poll


class MatrixAgent(ABC):
    """
    Basisklasse für alle Matrix-Bot-Agenten.
    Kapselt Login, Room-Joining und Message-Listening.
    """

    def __init__(
        self,
        config:      AgentConfig,
        server_name: str,
        rooms:       list[str],          # Room-IDs die dieser Agent joined
    ) -> None:
        self.config      = config
        self.server_name = server_name
        self.rooms       = rooms
        self._client:    AsyncClient | None = None
        self._running    = False
        self._mxid       = f"@{config.id}:{server_name}"

    # ------------------------------------------------------------------ public

    async def start(self) -> None:
        """Client initialisieren, einloggen, Rooms joinen, Sync-Loop starten."""
        self._client = AsyncClient(
            homeserver=CONDUWUIT_URL,
            user=self._mxid,
            config=AsyncClientConfig(max_limit_exceeded=0, max_timeouts=3),
        )
        self._client.add_event_callback(self._on_message, RoomMessageText)
        self._client.add_event_callback(self._on_member_event, RoomMemberEvent)

        await self._login()
        await self._join_rooms()
        self._running = True
        logger.info("MatrixAgent %s gestartet, %d Rooms", self._mxid, len(self.rooms))

    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.close()
            self._client = None
        logger.info("MatrixAgent %s gestoppt", self._mxid)

    async def run(self) -> None:
        """Sync-Loop — läuft bis stop() aufgerufen wird."""
        if not self._client:
            raise RuntimeError("start() muss vor run() aufgerufen werden")
        try:
            while self._running:
                resp = await self._client.sync(timeout=SYNC_TIMEOUT_MS)
                if isinstance(resp, SyncError):
                    logger.warning("%s Sync-Fehler: %s", self._mxid, resp.message)
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def send_message(self, room_id: str, text: str) -> None:
        """Text-Nachricht in einen Room schicken (#29)."""
        if not self._client:
            return
        await self._client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": text},
        )

    async def send_markdown(self, room_id: str, markdown: str) -> None:
        """Formatierte Nachricht (Markdown) in einen Room schicken."""
        if not self._client:
            return
        await self._client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype":         "m.text",
                "body":            markdown,
                "format":          "org.matrix.custom.html",
                "formatted_body":  _md_to_html(markdown),
            },
        )

    @property
    def mxid(self) -> str:
        return self._mxid

    # ----------------------------------------------------------------- abstrakt

    @abstractmethod
    async def on_user_message(self, room: MatrixRoom, text: str, sender: str) -> None:
        """
        Wird aufgerufen wenn ein (nicht-Bot) User eine Nachricht schickt.
        Subklassen implementieren hier die Reaktion (#28).
        """

    # ----------------------------------------------------------------- privat

    async def _login(self) -> None:
        """Login mit gespeichertem Token oder neu registrieren/einloggen."""
        token = self._load_token()
        if token:
            self._client.access_token  = token["access_token"]
            self._client.user_id       = token["user_id"]
            self._client.device_id     = token.get("device_id")
            logger.debug("%s: Token aus Disk geladen", self._mxid)
            return

        # Neuen Account registrieren oder einloggen
        resp = await self._client.login(
            password   = self._generate_password(),
            device_name = f"octopos-{self.config.id}",
        )
        if not isinstance(resp, LoginResponse):
            # Login fehlgeschlagen → Registrierung versuchen
            resp = await self._register_and_login()

        self._save_token({
            "access_token": resp.access_token,
            "user_id":      resp.user_id,
            "device_id":    resp.device_id,
        })
        logger.info("%s: Login erfolgreich", self._mxid)

    async def _register_and_login(self) -> LoginResponse:
        """Bot-Account auf dem Homeserver registrieren."""
        import aiohttp
        reg_token = self._read_registration_token()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CONDUWUIT_URL}/_matrix/client/v3/register",
                json={
                    "username":      self.config.id,
                    "password":      self._generate_password(),
                    "auth": {
                        "type":  "m.login.registration_token",
                        "token": reg_token,
                    },
                    "inhibit_login": False,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()

        if "access_token" not in data:
            raise RuntimeError(f"Registrierung von {self._mxid} fehlgeschlagen: {data}")

        # LoginResponse-kompatibles Objekt (Felder die _save_token braucht)
        class _TokenHolder:
            def __init__(self, d):
                self.access_token = d["access_token"]
                self.user_id      = d["user_id"]
                self.device_id    = d["device_id"]

        return _TokenHolder(data)

    async def _join_rooms(self) -> None:
        """Alle zugewiesenen Rooms joinen (#27)."""
        for room_id in self.rooms:
            if not room_id:
                continue
            result = await self._client.join(room_id)
            if hasattr(result, "room_id"):
                logger.debug("%s joined Room %s", self._mxid, room_id)
            else:
                logger.warning("%s konnte Room %s nicht joinen: %s",
                               self._mxid, room_id, result)

    async def _on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        """Eingehende Nachrichten filtern und weiterleiten (#28)."""
        # Eigene Nachrichten ignorieren
        if event.sender == self._mxid:
            return
        # Nur Bot-relevante Rooms
        if room.room_id not in self.rooms:
            return

        text = event.body.strip()
        if not text:
            return

        logger.debug("%s ← %s: %s", self._mxid, event.sender, text[:80])
        try:
            await self.on_user_message(room, text, event.sender)
        except Exception as e:
            logger.error("%s: Unbehandelte Exception in on_user_message: %s", self._mxid, e, exc_info=True)

    async def _on_member_event(self, room: MatrixRoom, event: RoomMemberEvent) -> None:
        """Room-Membership-Änderungen loggen."""
        logger.debug("Member-Event in %s: %s → %s",
                     room.room_id, event.state_key, event.membership)

    def _generate_password(self) -> str:
        """Deterministisches Passwort aus Agent-ID (kein State nötig)."""
        import hashlib
        # Kein Geheimnis — der Account ist ein interner Bot ohne echte Daten
        return hashlib.sha256(f"octopos-bot-{self.config.id}".encode()).hexdigest()[:32]

    def _load_token(self) -> dict | None:
        path = TOKEN_DIR / f"{self.config.id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def _save_token(self, token: dict) -> None:
        TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        path = TOKEN_DIR / f"{self.config.id}.json"
        path.write_text(json.dumps(token), encoding="utf-8")
        path.chmod(0o600)

    @staticmethod
    def _read_registration_token(
        toml_path: str = "/etc/conduwuit/conduwuit.toml",
    ) -> str:
        try:
            for line in Path(toml_path).read_text().splitlines():
                if line.strip().startswith("registration_token"):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return ""


# ======================================================== Boss-Implementierung

class BossMatrixAgent(MatrixAgent):
    """
    Boss-Agent: leitet User-Nachrichten an den Orchestrator weiter,
    postet die Antwort zurück in den Room.
    """

    def __init__(
        self,
        config:       AgentConfig,
        server_name:  str,
        rooms:        list[str],
        orchestrator,            # Orchestrator — kein direkter Import (Zirkel vermeiden)
        project_cfg,             # ProjectConfig
    ) -> None:
        super().__init__(config, server_name, rooms)
        self._orchestrator = orchestrator
        self._project_cfg  = project_cfg

    async def on_user_message(self, room: MatrixRoom, text: str, sender: str) -> None:
        """User schickt Nachricht → Orchestrator → Antwort in Room posten."""
        project_id = self._project_cfg.id
        logger.info("Boss %s empfängt Nachricht in %s von %s", self._mxid, room.room_id, sender)

        try:
            # Typing-Indikator setzen während LLM denkt
            if self._client:
                try:
                    await self._client.room_typing(room.room_id, typing=True, timeout=60_000)
                except Exception:
                    pass

            try:
                response, _workers = await self._orchestrator.handle_message(
                    project_id  = project_id,
                    project_cfg = self._project_cfg,
                    content     = text,
                    sender      = sender,
                )
            finally:
                if self._client:
                    try:
                        await self._client.room_typing(room.room_id, typing=False)
                    except Exception:
                        pass

            await self.send_markdown(room.room_id, response)

        except Exception as e:
            logger.error("Boss %s: Fehler beim Verarbeiten der Nachricht: %s", self._mxid, e, exc_info=True)
            try:
                await self.send_message(room.room_id, f"[Fehler] {e}")
            except Exception:
                pass


class WorkerMatrixAgent(MatrixAgent):
    """
    Worker-Agent: antwortet auf direkte Aufgaben vom Boss im Room.
    In Phase 3 primär als Empfänger für Boss-Delegationen aktiv.
    """

    async def on_user_message(self, room: MatrixRoom, text: str, sender: str) -> None:
        # Worker reagieren auf Nachrichten vom Boss (nicht von normalen Usern)
        logger.debug("Worker %s: Nachricht von %s ignoriert (wird via Orchestrator gespawnt)",
                     self._mxid, sender)


# ======================================================== Hilfsfunktionen

def _md_to_html(text: str) -> str:
    """Minimale Markdown → HTML Konvertierung für Matrix."""
    import re
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Code-Block
    text = re.sub(r"```.*?\n(.*?)```", r"<pre><code>\1</code></pre>", text, flags=re.DOTALL)
    # Inline-Code
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    # Zeilenumbrüche
    text = text.replace("\n", "<br/>")
    return text
