"""
Valheim Server Plugin für HydraHive (#259)

Verwaltet und überwacht einen Valheim Dedicated Server.
- Status via Steam A2S Query Protokoll (UDP)
- Spielerliste via A2S_PLAYER
- Backup via Datei-Kopie
- Neustart via systemctl oder Prozess-Management
"""
import json
import logging
import os
import shutil
import socket
import struct
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("valheim-server")

PLUGIN_ID = "valheim-server"


def _load_cfg(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/{PLUGIN_ID}.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Steam A2S Query Protocol (UDP)
# ---------------------------------------------------------------------------

A2S_INFO_REQUEST = b"\xFF\xFF\xFF\xFF\x54Source Engine Query\x00"
A2S_PLAYER_REQUEST_INIT = b"\xFF\xFF\xFF\xFF\x55\xFF\xFF\xFF\xFF"


def _a2s_query(host: str, port: int, request: bytes, timeout: float = 5.0) -> bytes:
    """Sendet eine A2S UDP-Anfrage und gibt die rohe Antwort zurück."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(request, (host, port))
        data, _ = s.recvfrom(4096)
        return data


def _a2s_info(host: str, port: int) -> dict:
    """A2S_INFO — Server-Infos abfragen."""
    data = _a2s_query(host, port, A2S_INFO_REQUEST)
    # Antwort: 4 bytes header (FF FF FF FF) + 1 byte type (0x49) + data
    if len(data) < 6 or data[4:5] != b"\x49":
        raise ValueError(f"Unerwartete A2S_INFO Antwort (Typ: {data[4:5].hex()})")
    offset = 5

    def read_str(d: bytes, pos: int) -> tuple[str, int]:
        end = d.index(b"\x00", pos)
        return d[pos:end].decode("utf-8", errors="replace"), end + 1

    protocol = data[offset]; offset += 1
    name, offset = read_str(data, offset)
    map_name, offset = read_str(data, offset)
    folder, offset = read_str(data, offset)
    game, offset = read_str(data, offset)
    app_id = struct.unpack_from("<H", data, offset)[0]; offset += 2
    players = data[offset]; offset += 1
    max_players = data[offset]; offset += 1
    bots = data[offset]; offset += 1
    server_type = chr(data[offset]); offset += 1
    environment = chr(data[offset]); offset += 1
    visibility = data[offset]; offset += 1

    return {
        "name": name,
        "map": map_name,
        "game": game,
        "app_id": app_id,
        "players": players,
        "max_players": max_players,
        "bots": bots,
        "type": server_type,
        "environment": environment,
        "visibility": "privat" if visibility else "öffentlich",
        "protocol": protocol,
    }


def _a2s_players(host: str, port: int) -> list[dict]:
    """A2S_PLAYER — Spielerliste abfragen (Challenge-basiert)."""
    # Schritt 1: Challenge-Token anfordern
    resp = _a2s_query(host, port, A2S_PLAYER_REQUEST_INIT)
    if resp[4:5] != b"\x41":
        raise ValueError("Kein A2S Challenge Token erhalten")
    challenge = resp[5:9]

    # Schritt 2: Mit Challenge-Token anfragen
    request = b"\xFF\xFF\xFF\xFF\x55" + challenge
    resp = _a2s_query(host, port, request)
    if resp[4:5] != b"\x44":
        raise ValueError("Unerwartete A2S_PLAYER Antwort")

    offset = 5
    count = resp[offset]; offset += 1
    players = []
    for _ in range(count):
        idx = resp[offset]; offset += 1
        end = resp.index(b"\x00", offset)
        name = resp[offset:end].decode("utf-8", errors="replace")
        offset = end + 1
        score = struct.unpack_from("<i", resp, offset)[0]; offset += 4
        duration = struct.unpack_from("<f", resp, offset)[0]; offset += 4
        players.append({"name": name, "score": score, "duration_sec": int(duration)})
    return players


# ---------------------------------------------------------------------------
# Plugin Registration
# ---------------------------------------------------------------------------

def register(api):
    """Plugin beim Core registrieren."""

    @api.tool(
        tool_id="valheim_status",
        description=(
            "Zeigt den Status des Valheim-Servers: Servername, Welt, Spieleranzahl, "
            "Sichtbarkeit. Nutzt Steam A2S Query (UDP)."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def valheim_status(**ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        host = cfg.get("host", "")
        port = int(cfg.get("port", 2457))
        if not host:
            return "Fehler: Kein Server-Host konfiguriert."
        try:
            info = _a2s_info(host, port)
            lines = [
                f"Host:        {host}:{port}",
                f"Status:      Online",
                f"Name:        {info['name']}",
                f"Welt/Map:    {info['map']}",
                f"Spieler:     {info['players']}/{info['max_players']} ({info['bots']} Bots)",
                f"Sichtbarkeit:{info['visibility']}",
                f"Typ:         {info['type']} / {info['environment']}",
            ]
            return "\n".join(lines)
        except socket.timeout:
            return f"Server {host}:{port} antwortet nicht (Timeout). Server offline?"
        except ConnectionRefusedError:
            return f"Server {host}:{port} nicht erreichbar."
        except Exception as e:
            return f"Fehler beim Abrufen des Server-Status: {e}"

    @api.tool(
        tool_id="valheim_players",
        description="Listet die aktuell eingeloggten Spieler auf dem Valheim-Server mit Spielzeit.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def valheim_players(**ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        host = cfg.get("host", "")
        port = int(cfg.get("port", 2457))
        if not host:
            return "Fehler: Kein Server-Host konfiguriert."
        try:
            players = _a2s_players(host, port)
            if not players:
                return "Keine Spieler online."
            lines = [f"Spieler online: {len(players)}"]
            for p in players:
                mins = p["duration_sec"] // 60
                lines.append(f"  - {p['name']} (Score: {p['score']}, seit {mins} Min.)")
            return "\n".join(lines)
        except Exception as e:
            return f"Fehler beim Abrufen der Spielerliste: {e}"

    @api.tool(
        tool_id="valheim_backup",
        description=(
            "Erstellt ein Backup der Valheim-Weltdaten. "
            "Kopiert den Save-Ordner in das konfigurierte Backup-Verzeichnis mit Zeitstempel."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def valheim_backup(**ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        save_dir = cfg.get("save_dir", "")
        backup_dir = cfg.get("backup_dir", "")
        if not save_dir:
            return "Fehler: Kein Save-Verzeichnis konfiguriert."
        if not backup_dir:
            return "Fehler: Kein Backup-Verzeichnis konfiguriert."
        save_path = Path(save_dir)
        if not save_path.exists():
            return f"Fehler: Save-Verzeichnis nicht gefunden: {save_dir}"
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = Path(backup_dir) / f"valheim_backup_{timestamp}"
            shutil.copytree(str(save_path), str(dest))
            # Zähle kopierte Dateien
            file_count = sum(1 for _ in dest.rglob("*") if _.is_file())
            total_mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / (1024 * 1024)
            return (
                f"Backup erstellt: {dest}\n"
                f"Dateien: {file_count}\n"
                f"Größe:   {total_mb:.1f} MB"
            )
        except Exception as e:
            return f"Backup fehlgeschlagen: {e}"

    @api.tool(
        tool_id="valheim_restart",
        description=(
            "Startet den Valheim-Server neu. Versucht zunächst systemctl (wenn Service-Name konfiguriert), "
            "alternativ wird der Prozess per Signal neugestartet."
        ),
        parameters={
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "Muss auf true gesetzt sein um den Neustart zu bestätigen.",
                },
            },
            "required": ["confirm"],
        },
    )
    def valheim_restart(confirm: bool = False, **ctx) -> str:
        if not confirm:
            return "Neustart abgebrochen. Setze confirm=true um den Server neu zu starten."
        cfg = _load_cfg(ctx.get("_username", "admin"))
        service = cfg.get("service_name", "")
        server_name = cfg.get("server_name", "valheim_server")

        if service:
            try:
                result = subprocess.run(
                    ["systemctl", "restart", service],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    return f"Service '{service}' erfolgreich neu gestartet."
                return f"systemctl restart {service} fehlgeschlagen:\n{result.stderr.strip()}"
            except FileNotFoundError:
                pass
            except Exception as e:
                return f"systemctl Fehler: {e}"

        # Fallback: Prozess per Signal neustarten
        try:
            result = subprocess.run(
                ["pkill", "-HUP", "-f", server_name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return f"SIGHUP an Prozess '{server_name}' gesendet."
            return f"Kein Prozess '{server_name}' gefunden. Server läuft möglicherweise nicht."
        except Exception as e:
            return f"Neustart fehlgeschlagen: {e}"
