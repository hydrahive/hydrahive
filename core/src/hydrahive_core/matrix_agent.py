"""
matrix_agent.py — Matrix-nio Basisklasse für Agenten (#26, #27, #28, #29)

Jeder Agent erbt von MatrixAgent. Er:
- loggt sich als @<agent-id>:<server> in conduwuit ein
- joined seine zugewiesenen Rooms beim Start
- lauscht auf eingehende Nachrichten
- leitet User-Nachrichten an den Orchestrator weiter (#28)
- kann selbst Nachrichten in Rooms schicken (#29)

Credentials werden in /etc/hydrahive/agent_tokens/ gespeichert,
damit der Bot-Account bei Core-Neustart nicht neu registriert werden muss.
"""

import asyncio
import collections
import json
import logging
import time
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
def _token_dir_exists(p: Path) -> bool:
    try:
        return p.exists()
    except OSError:
        return False

TOKEN_DIR       = next(
    (p for p in [Path("/etc/hydrahive/agent_tokens"), Path("/etc/octopos/agent_tokens")] if _token_dir_exists(p)),
    Path("/etc/hydrahive/agent_tokens"),
)
SYNC_TIMEOUT_MS = 30_000    # 30s Long-Poll


class MatrixAgent(ABC):
    """
    Basisklasse für alle Matrix-Bot-Agenten.
    Kapselt Login, Room-Joining und Message-Listening.
    """

    LOOP_HISTORY_SIZE      = 20
    LOOP_PINGPONG_THRESHOLD = 4

    def __init__(
        self,
        config:      AgentConfig,
        server_name: str,
        rooms:       list[str],          # Room-IDs die dieser Agent joined
        loop_detection:        bool = True,
        loop_bot_threshold:    int  = 3,
        loop_pingpong_seconds: int  = 30,
        loop_cooldown_seconds: int  = 300,
    ) -> None:
        self.config      = config
        self.server_name = server_name
        self.rooms       = rooms
        self._client:    AsyncClient | None = None
        self._running    = False
        self._mxid       = f"@{config.id}:{server_name}"
        # Loop-Detektion (Circuit Breaker) — analog zu discord_agent.py
        self.loop_detection        = loop_detection
        self.loop_bot_threshold    = max(2, loop_bot_threshold)
        self.loop_pingpong_seconds = max(5, loop_pingpong_seconds)
        self.loop_cooldown_seconds = max(10, loop_cooldown_seconds)
        self._loop_history: dict[str, collections.deque] = {}   # room_id → deque
        self._circuit_open: dict[str, float] = {}               # room_id → open_time

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
        _sync_save_counter = 0
        try:
            while self._running:
                resp = await self._client.sync(timeout=SYNC_TIMEOUT_MS)
                if isinstance(resp, SyncError):
                    logger.warning("%s Sync-Fehler: %s", self._mxid, resp.message)
                    await asyncio.sleep(5)
                    continue
                # next_batch alle 10 Syncs persistieren
                _sync_save_counter += 1
                if _sync_save_counter >= 10 and self._client.next_batch:
                    _sync_save_counter = 0
                    token = self._load_token() or {}
                    token["next_batch"] = self._client.next_batch
                    self._save_token(token)
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

    def _check_loop(self, room_id: str, is_bot: bool) -> bool:
        """
        Circuit Breaker Loop-Detektion — analog zu discord_agent.py.
        Gibt True zurück wenn die Nachricht geblockt werden soll.
        Human-Nachrichten werden nie geblockt, setzen den Zähler aber auch nicht zurück.
        """
        if not self.loop_detection:
            return False

        now = time.monotonic()

        if not is_bot:
            return False  # Menschen nie blocken

        # Circuit noch offen?
        if room_id in self._circuit_open:
            if now - self._circuit_open[room_id] < self.loop_cooldown_seconds:
                return True
            else:
                logger.info("Matrix Loop-Detektion [%s]: Circuit schließt wieder (Room %s)",
                            self._mxid, room_id)
                del self._circuit_open[room_id]
                self._loop_history.pop(room_id, None)

        if room_id not in self._loop_history:
            self._loop_history[room_id] = collections.deque(maxlen=self.LOOP_HISTORY_SIZE)
        history = self._loop_history[room_id]
        history.append(now)

        # Detektor 1: Zu viele Bot-Nachrichten
        if len(history) >= self.loop_bot_threshold:
            logger.warning(
                "Matrix Loop-Detektion [%s]: %d Bot-Nachrichten in Room %s "
                "— Circuit Breaker ausgelöst (%ds Cooldown)",
                self._mxid, len(history), room_id, self.loop_cooldown_seconds,
            )
            self._circuit_open[room_id] = now
            return True

        # Detektor 2: PingPong
        recent = list(history)
        if len(recent) >= self.LOOP_PINGPONG_THRESHOLD * 2:
            window = recent[-self.LOOP_PINGPONG_THRESHOLD * 2:]
            timespan = window[-1] - window[0]
            if timespan < self.loop_pingpong_seconds:
                logger.warning(
                    "Matrix Loop-Detektion [%s]: PingPong in Room %s "
                    "(%d Bot-Nachrichten in %.1fs) — Circuit Breaker ausgelöst",
                    self._mxid, room_id, len(window), timespan,
                )
                self._circuit_open[room_id] = now
                return True

        return False

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
            if token.get("next_batch"):
                self._client.next_batch = token["next_batch"]
                logger.debug("%s: next_batch aus Disk geladen (%s)", self._mxid, token["next_batch"])
            logger.debug("%s: Token aus Disk geladen", self._mxid)
            return

        # Neuen Account registrieren oder einloggen
        resp = await self._client.login(
            password   = self._generate_password(),
            device_name = f"hydrahive-{self.config.id}",
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
        # Eigene Nachrichten ignorieren (MXID-Vergleich inkl. lokalem Teil)
        sender_local = event.sender.split(":")[0] if ":" in event.sender else event.sender
        own_local    = self._mxid.split(":")[0]   if ":" in self._mxid   else self._mxid
        if event.sender == self._mxid or sender_local == own_local:
            return
        # Nur Bot-relevante Rooms
        if room.room_id not in self.rooms:
            return

        text = event.body.strip()
        if not text:
            return

        # Bot-Detection: Sender-MXID endet typischerweise auf einen bekannten Bot-Pattern
        # Heuristik: kein @-Präfix im lokalen Teil → lokaler User; Bot-Accounts haben oft
        # deterministischen lokalen Namen ohne menschliche Muster (z.B. "claude_boss")
        is_bot = (
            sender_local.startswith("@") is False  # immer False hier, nur zur Klarheit
            and any(pat in event.sender.lower() for pat in ("bot", "agent", "claude", "boss", "worker"))
        )

        if self._check_loop(room.room_id, is_bot):
            logger.info("%s: Loop-Detektion geblockt (Room %s, Sender %s)",
                        self._mxid, room.room_id, event.sender)
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
        return hashlib.sha256(f"hydrahive-bot-{self.config.id}".encode()).hexdigest()[:32]

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
                    execution_mode = "safe",
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
