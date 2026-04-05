"""
Minecraft Server Plugin für HydraHive (#258)

Überwacht und steuert einen Minecraft Java Edition Server.
- Status via Server List Ping (SLP) Protokoll
- RCON für Serverbefehle (Whitelist, Kick, etc.)

SLP: https://wiki.vg/Server_List_Ping
RCON: https://wiki.vg/RCON
"""
import json
import logging
import socket
import struct
from pathlib import Path

logger = logging.getLogger("minecraft-server")

PLUGIN_ID = "minecraft-server"


def _load_cfg(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/{PLUGIN_ID}.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Server List Ping (SLP) — Minecraft 1.7+
# ---------------------------------------------------------------------------

def _varint_encode(value: int) -> bytes:
    result = b""
    while True:
        part = value & 0x7F
        value >>= 7
        if value:
            part |= 0x80
        result += bytes([part])
        if not value:
            break
    return result


def _varint_decode(sock: socket.socket) -> int:
    result = 0
    shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("Connection closed")
        byte = b[0]
        result |= (byte & 0x7F) << shift
        shift += 7
        if not (byte & 0x80):
            break
        if shift >= 35:
            raise ValueError("VarInt too big")
    return result


def _slp_ping(host: str, port: int, timeout: int = 5) -> dict:
    """Server List Ping → gibt Status-JSON zurück."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        # Handshake packet
        host_bytes = host.encode("utf-8")
        payload = (
            _varint_encode(0x00)           # Packet ID: Handshake
            + _varint_encode(47)           # Protocol version (any)
            + _varint_encode(len(host_bytes))
            + host_bytes
            + struct.pack(">H", port)
            + _varint_encode(1)            # Next state: Status
        )
        packet = _varint_encode(len(payload)) + payload
        s.sendall(packet)

        # Status request
        status_req = _varint_encode(1) + _varint_encode(0x00)
        s.sendall(status_req)

        # Read response
        _varint_decode(s)   # packet length
        _varint_decode(s)   # packet id (0x00)
        str_len = _varint_decode(s)
        raw = b""
        while len(raw) < str_len:
            chunk = s.recv(str_len - len(raw))
            if not chunk:
                break
            raw += chunk
        return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# RCON Protocol
# ---------------------------------------------------------------------------

class RCONClient:
    """Minimaler RCON-Client (Minecraft RCON Protokoll)."""

    AUTH = 3
    COMMAND = 2

    def __init__(self, host: str, port: int, password: str, timeout: int = 10):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._sock = None
        self._req_id = 1

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        # Authenticate
        resp_id, _, _ = self._send(self.AUTH, self.password)
        if resp_id == -1:
            raise PermissionError("RCON authentication failed — wrong password?")

    def _send(self, pkt_type: int, payload: str) -> tuple[int, int, str]:
        req_id = self._req_id
        self._req_id += 1
        data = payload.encode("utf-8") + b"\x00\x00"
        length = 4 + 4 + len(data)
        packet = struct.pack("<iii", length, req_id, pkt_type) + data
        self._sock.sendall(packet)

        # Read response
        header = b""
        while len(header) < 4:
            header += self._sock.recv(4 - len(header))
        resp_len = struct.unpack("<i", header)[0]
        body = b""
        while len(body) < resp_len:
            body += self._sock.recv(resp_len - len(body))
        resp_id, resp_type = struct.unpack("<ii", body[:8])
        resp_payload = body[8:].rstrip(b"\x00").decode("utf-8", errors="replace")
        return resp_id, resp_type, resp_payload

    def command(self, cmd: str) -> str:
        _, _, resp = self._send(self.COMMAND, cmd)
        return resp

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


def _rcon_run(host: str, port: int, password: str, cmd: str) -> str:
    """Führt einen RCON-Befehl aus und gibt die Antwort zurück."""
    client = RCONClient(host, port, password)
    try:
        client.connect()
        result = client.command(cmd)
        return result or "(kein Output)"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Plugin Registration
# ---------------------------------------------------------------------------

def register(api):
    """Plugin beim Core registrieren."""

    @api.tool(
        tool_id="minecraft_status",
        description=(
            "Zeigt den Status des Minecraft-Servers: Version, Beschreibung (MOTD), "
            "Online-Spieler und Server-Infos via Server List Ping."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def minecraft_status(**ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        host = cfg.get("host", "")
        port = int(cfg.get("port", 25565))
        if not host:
            return "Fehler: Kein Server-Host konfiguriert."
        try:
            data = _slp_ping(host, port)
            version = data.get("version", {}).get("name", "?")
            description = data.get("description", {})
            if isinstance(description, dict):
                motd = description.get("text", "")
            else:
                motd = str(description)
            players = data.get("players", {})
            online = players.get("online", 0)
            maximum = players.get("max", 0)
            lines = [
                f"Host:        {host}:{port}",
                f"Status:      Online",
                f"Version:     {version}",
                f"MOTD:        {motd}",
                f"Spieler:     {online}/{maximum}",
            ]
            return "\n".join(lines)
        except ConnectionRefusedError:
            return f"Server {host}:{port} ist nicht erreichbar (Connection refused)."
        except socket.timeout:
            return f"Server {host}:{port} hat nicht innerhalb von 5s geantwortet."
        except Exception as e:
            return f"Fehler beim Abrufen des Server-Status: {e}"

    @api.tool(
        tool_id="minecraft_players",
        description="Listet alle aktuell eingeloggten Spieler auf dem Minecraft-Server.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def minecraft_players(**ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        host = cfg.get("host", "")
        port = int(cfg.get("port", 25565))
        if not host:
            return "Fehler: Kein Server-Host konfiguriert."
        try:
            data = _slp_ping(host, port)
            players = data.get("players", {})
            online = players.get("online", 0)
            maximum = players.get("max", 0)
            sample = players.get("sample", [])
            if online == 0:
                return f"Keine Spieler online (0/{maximum})."
            lines = [f"Spieler online: {online}/{maximum}"]
            for p in sample:
                lines.append(f"  - {p.get('name', '?')} ({p.get('id', '?')})")
            if online > len(sample):
                lines.append(f"  ... und {online - len(sample)} weitere")
            return "\n".join(lines)
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="minecraft_rcon",
        description=(
            "Sendet einen RCON-Befehl an den Minecraft-Server und gibt die Antwort zurück. "
            "Beispiele: 'list', 'say Hallo!', 'time set day', 'op Spielername'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Der Minecraft-Serverbefehl (ohne führenden Slash).",
                },
            },
            "required": ["command"],
        },
    )
    def minecraft_rcon(command: str, **ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        host = cfg.get("host", "")
        rcon_port = int(cfg.get("rcon_port", 25575))
        password = cfg.get("rcon_password", "")
        if not host:
            return "Fehler: Kein Server-Host konfiguriert."
        if not password:
            return "Fehler: Kein RCON-Passwort konfiguriert."
        try:
            result = _rcon_run(host, rcon_port, password, command)
            return f"$ {command}\n{result}"
        except PermissionError as e:
            return f"RCON Authentifizierung fehlgeschlagen: {e}"
        except ConnectionRefusedError:
            return f"RCON-Port {host}:{rcon_port} nicht erreichbar. Ist RCON aktiviert?"
        except Exception as e:
            return f"RCON Fehler: {e}"

    @api.tool(
        tool_id="minecraft_whitelist",
        description=(
            "Verwaltet die Whitelist des Minecraft-Servers via RCON. "
            "Aktionen: list (anzeigen), add (hinzufügen), remove (entfernen), on/off (aktivieren/deaktivieren)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "remove", "on", "off"],
                    "description": "Whitelist-Aktion",
                },
                "player": {
                    "type": "string",
                    "description": "Spielername (nur für add/remove benötigt)",
                },
            },
            "required": ["action"],
        },
    )
    def minecraft_whitelist(action: str, player: str = "", **ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        host = cfg.get("host", "")
        rcon_port = int(cfg.get("rcon_port", 25575))
        password = cfg.get("rcon_password", "")
        if not host:
            return "Fehler: Kein Server-Host konfiguriert."
        if not password:
            return "Fehler: Kein RCON-Passwort konfiguriert."
        if action in ("add", "remove") and not player:
            return f"Fehler: Spielername für 'whitelist {action}' benötigt."
        cmd = f"whitelist {action}" if action in ("list", "on", "off") else f"whitelist {action} {player}"
        try:
            result = _rcon_run(host, rcon_port, password, cmd)
            return f"$ {cmd}\n{result}"
        except Exception as e:
            return f"Whitelist Fehler: {e}"
