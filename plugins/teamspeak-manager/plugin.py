"""
TeamSpeak Manager Plugin für HydraHive (#252)

Verwaltet einen TeamSpeak 3 Server via ServerQuery (Telnet-basiertes Protokoll).
- Server-Infos abfragen
- Channel-Liste
- Client-Liste
- Clients kicken

TS3 ServerQuery Dokumentation:
https://community.teamspeak.com/t/teamspeak-3-server-query-manual/869
"""
import json
import logging
import socket
import time
from pathlib import Path

logger = logging.getLogger("teamspeak-manager")

PLUGIN_ID = "teamspeak-manager"


def _load_cfg(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/{PLUGIN_ID}.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# TS3 ServerQuery Client
# ---------------------------------------------------------------------------

def _ts3_escape(s: str) -> str:
    """Escapet einen String für das TS3 ServerQuery Protokoll."""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace("/", "\\/")
        .replace(" ", "\\s")
        .replace("|", "\\p")
        .replace("\a", "\\a")
        .replace("\b", "\\b")
        .replace("\f", "\\f")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("\v", "\\v")
    )


def _ts3_unescape(s: str) -> str:
    """Unescapet einen TS3 ServerQuery String."""
    return (
        s.replace("\\\\", "\x00BACKSLASH\x00")
        .replace("\\/", "/")
        .replace("\\s", " ")
        .replace("\\p", "|")
        .replace("\\a", "\a")
        .replace("\\b", "\b")
        .replace("\\f", "\f")
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace("\\v", "\v")
        .replace("\x00BACKSLASH\x00", "\\")
    )


def _ts3_parse_line(line: str) -> list[dict]:
    """Parst eine TS3 ServerQuery Antwortzeile in eine Liste von Dicts."""
    entries = []
    for entry in line.split("|"):
        obj = {}
        for token in entry.strip().split():
            if "=" in token:
                k, _, v = token.partition("=")
                obj[k] = _ts3_unescape(v)
            else:
                obj[token] = True
        entries.append(obj)
    return entries


class TS3Client:
    """Einfacher TS3 ServerQuery Client via raw socket."""

    def __init__(self, host: str, port: int = 10011, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock = None
        self._buf = b""

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        # Warte auf Banner
        banner = self._readline()
        if not banner.startswith("TS3"):
            raise ConnectionError(f"Kein TS3 ServerQuery Banner: {banner!r}")
        # Noch eine Zeile (Welcome)
        self._readline()

    def _readline(self) -> str:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Verbindung getrennt")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode("utf-8", errors="replace").rstrip("\r")

    def _read_response(self) -> tuple[list[str], dict]:
        """Liest Antwortzeilen bis zur 'error' Zeile."""
        lines = []
        error = {}
        while True:
            line = self._readline()
            if line.startswith("error "):
                parts = _ts3_parse_line(line[6:])
                error = parts[0] if parts else {}
                break
            elif line:
                lines.append(line)
        return lines, error

    def send(self, cmd: str) -> tuple[list[str], dict]:
        self._sock.sendall((cmd + "\n").encode("utf-8"))
        return self._read_response()

    def login(self, username: str, password: str):
        _, err = self.send(f"login {_ts3_escape(username)} {_ts3_escape(password)}")
        if err.get("id", "0") != "0":
            raise PermissionError(f"Login fehlgeschlagen: {err.get('msg', '?')}")

    def use(self, server_id: int):
        _, err = self.send(f"use sid={server_id}")
        if err.get("id", "0") != "0":
            raise RuntimeError(f"use sid={server_id} fehlgeschlagen: {err.get('msg', '?')}")

    def quit(self):
        try:
            self.send("quit")
        except Exception:
            pass
        finally:
            if self._sock:
                self._sock.close()
                self._sock = None


def _ts3_connect(cfg: dict) -> TS3Client:
    """Stellt eine authentifizierte TS3 ServerQuery-Verbindung her."""
    host = cfg.get("host", "")
    port = int(cfg.get("port", 10011))
    sq_user = cfg.get("username", "serveradmin")
    sq_pass = cfg.get("password", "")
    server_id = int(cfg.get("server_id", 1))

    if not host:
        raise ValueError("Kein Host konfiguriert.")
    if not sq_pass:
        raise ValueError("Kein ServerQuery-Passwort konfiguriert.")

    client = TS3Client(host, port)
    client.connect()
    client.login(sq_user, sq_pass)
    client.use(server_id)
    return client


# ---------------------------------------------------------------------------
# Plugin Registration
# ---------------------------------------------------------------------------

def register(api):
    """Plugin beim Core registrieren."""

    @api.tool(
        tool_id="teamspeak_info",
        description=(
            "Zeigt allgemeine Informationen über den TeamSpeak 3 Server: "
            "Name, Version, Uptime, Clients, Channels, Max Clients."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def teamspeak_info(**ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        try:
            client = _ts3_connect(cfg)
        except (ValueError, PermissionError, ConnectionError) as e:
            return f"Verbindung fehlgeschlagen: {e}"
        try:
            lines_raw, err = client.send("serverinfo")
            if err.get("id", "0") != "0":
                return f"serverinfo Fehler: {err.get('msg', '?')}"
            if not lines_raw:
                return "Keine Server-Info erhalten."
            info = _ts3_parse_line(lines_raw[0])[0]
            name = info.get("virtualserver_name", "?")
            version = info.get("virtualserver_version", "?")
            platform = info.get("virtualserver_platform", "?")
            uptime_sec = int(info.get("virtualserver_uptime", 0))
            clients_online = info.get("virtualserver_clientsonline", "?")
            channels = info.get("virtualserver_channelsonline", "?")
            max_clients = info.get("virtualserver_maxclients", "?")
            query_clients = info.get("virtualserver_queryclientsonline", "0")

            hours, rem = divmod(uptime_sec, 3600)
            mins = rem // 60
            uptime_str = f"{hours}h {mins}m"

            result_lines = [
                f"Name:         {name}",
                f"Version:      {version} ({platform})",
                f"Uptime:       {uptime_str}",
                f"Clients:      {clients_online} online (davon {query_clients} Query)",
                f"Max Clients:  {max_clients}",
                f"Channels:     {channels}",
            ]
            return "\n".join(result_lines)
        finally:
            client.quit()

    @api.tool(
        tool_id="teamspeak_channels",
        description="Listet alle Channels auf dem TeamSpeak 3 Server mit Clients und Typ.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def teamspeak_channels(**ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        try:
            client = _ts3_connect(cfg)
        except (ValueError, PermissionError, ConnectionError) as e:
            return f"Verbindung fehlgeschlagen: {e}"
        try:
            lines_raw, err = client.send("channellist")
            if err.get("id", "0") != "0":
                return f"channellist Fehler: {err.get('msg', '?')}"
            if not lines_raw:
                return "Keine Channels gefunden."
            channels = _ts3_parse_line(lines_raw[0])
            if not channels:
                return "Keine Channels gefunden."
            result_lines = [f"{'ID':<6} {'Clients':<8} {'Name'}"]
            result_lines.append("-" * 50)
            for ch in channels:
                cid = ch.get("cid", "?")
                name = ch.get("channel_name", "?")
                clients = ch.get("total_clients", "0")
                result_lines.append(f"{cid:<6} {clients:<8} {name}")
            return "\n".join(result_lines)
        finally:
            client.quit()

    @api.tool(
        tool_id="teamspeak_clients",
        description="Listet alle aktuell verbundenen Clients auf dem TeamSpeak 3 Server.",
        parameters={
            "type": "object",
            "properties": {
                "show_details": {
                    "type": "boolean",
                    "description": "Erweiterte Infos anzeigen (IP, Idle-Zeit, Channel). Default: false",
                },
            },
            "required": [],
        },
    )
    def teamspeak_clients(show_details: bool = False, **ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        try:
            client = _ts3_connect(cfg)
        except (ValueError, PermissionError, ConnectionError) as e:
            return f"Verbindung fehlgeschlagen: {e}"
        try:
            cmd = "clientlist"
            if show_details:
                cmd += " -uid -ip -times -away"
            lines_raw, err = client.send(cmd)
            if err.get("id", "0") != "0":
                return f"clientlist Fehler: {err.get('msg', '?')}"
            if not lines_raw:
                return "Keine Clients online."
            clients = _ts3_parse_line(lines_raw[0])
            # Filtere Query-Clients heraus
            real_clients = [c for c in clients if c.get("client_type", "0") == "0"]
            if not real_clients:
                return "Keine Clients online (nur Query-Verbindungen)."
            result_lines = [f"Clients online: {len(real_clients)}"]
            for c in real_clients:
                clid = c.get("clid", "?")
                name = c.get("client_nickname", "?")
                cid = c.get("cid", "?")
                idle_sec = int(c.get("client_idle_time", 0)) // 1000
                away = "(Abwesend)" if c.get("client_away") == "1" else ""
                if show_details:
                    result_lines.append(f"  [{clid}] {name} — Channel: {cid}, Idle: {idle_sec}s {away}")
                else:
                    result_lines.append(f"  [{clid}] {name} {away}")
            return "\n".join(result_lines)
        finally:
            client.quit()

    @api.tool(
        tool_id="teamspeak_kick",
        description=(
            "Kickt einen Client vom TeamSpeak 3 Server oder aus seinem Channel. "
            "Benötigt die Client-ID (clid) — mit teamspeak_clients abrufen."
        ),
        parameters={
            "type": "object",
            "properties": {
                "client_id": {
                    "type": "integer",
                    "description": "Client-ID (clid) des zu kickenden Nutzers",
                },
                "reason": {
                    "type": "string",
                    "description": "Kick-Grund (wird dem Client angezeigt)",
                },
                "from_server": {
                    "type": "boolean",
                    "description": "Vom Server kicken (true) oder nur aus Channel (false). Default: false",
                },
            },
            "required": ["client_id"],
        },
    )
    def teamspeak_kick(
        client_id: int,
        reason: str = "Kicked by HydraHive",
        from_server: bool = False,
        **ctx,
    ) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        try:
            client = _ts3_connect(cfg)
        except (ValueError, PermissionError, ConnectionError) as e:
            return f"Verbindung fehlgeschlagen: {e}"
        try:
            # reasonid: 4 = Channel, 5 = Server
            reason_id = 5 if from_server else 4
            kick_type = "Server" if from_server else "Channel"
            cmd = f"clientkick clid={client_id} reasonid={reason_id} reasonmsg={_ts3_escape(reason)}"
            _, err = client.send(cmd)
            if err.get("id", "0") != "0":
                return f"Kick fehlgeschlagen: {err.get('msg', '?')} (Fehlercode: {err.get('id', '?')})"
            return f"Client {client_id} erfolgreich vom {kick_type} gekickt. Grund: {reason}"
        finally:
            client.quit()
