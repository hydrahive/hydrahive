"""
Pterodactyl Manager Plugin für HydraHive (#260)

Verwaltet Spielserver über das Pterodactyl Panel via Client API v1.
- Server auflisten
- Server starten/stoppen/neustarten
- Konsolenbefehle senden
- Ressourcen-Nutzung abfragen

API: https://dashflo.net/docs/api/pterodactyl/v1/
"""
import json
import logging
import urllib.request
import urllib.error
from pathlib import Path

logger = logging.getLogger("pterodactyl-manager")

PLUGIN_ID = "pterodactyl-manager"


def _load_cfg(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/{PLUGIN_ID}.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _api_request(
    base_url: str,
    api_key: str,
    path: str,
    method: str = "GET",
    body: dict | None = None,
) -> dict | list | None:
    """Generische Pterodactyl API-Anfrage."""
    url = f"{base_url.rstrip('/')}/api/client{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body_text}") from e


def _fmt_status(status: str) -> str:
    icons = {
        "running": "Running",
        "starting": "Starting",
        "stopping": "Stopping",
        "offline": "Offline",
    }
    return icons.get(status, status)


# ---------------------------------------------------------------------------
# Plugin Registration
# ---------------------------------------------------------------------------

def register(api):
    """Plugin beim Core registrieren."""

    @api.tool(
        tool_id="pterodactyl_servers",
        description=(
            "Listet alle Server im Pterodactyl Panel auf: Name, ID, Status, Node, Spieltyp."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def pterodactyl_servers(**ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        base_url = cfg.get("base_url", "")
        api_key = cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: Pterodactyl Base URL und API Key müssen konfiguriert sein."
        try:
            result = _api_request(base_url, api_key, "/")
            servers = result.get("data", [])
            if not servers:
                return "Keine Server gefunden."
            lines = [f"{'Name':<30} {'ID':<12} {'Status':<10} {'Node':<20} {'Spiel'}"]
            lines.append("-" * 90)
            for srv in servers:
                attrs = srv.get("attributes", {})
                name = attrs.get("name", "?")[:28]
                identifier = attrs.get("identifier", "?")
                node = attrs.get("node", "?")[:18]
                egg = attrs.get("relationships", {}).get("egg", {}).get("attributes", {}).get("name", "")
                # Status aus relationships/resources wenn vorhanden
                status = "?"
                try:
                    res = _api_request(base_url, api_key, f"/servers/{identifier}/resources")
                    status = _fmt_status(res.get("attributes", {}).get("current_state", "?"))
                except Exception:
                    pass
                lines.append(f"{name:<30} {identifier:<12} {status:<10} {node:<20} {egg}")
            return "\n".join(lines)
        except RuntimeError as e:
            return f"API Fehler: {e}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="pterodactyl_server_action",
        description=(
            "Startet, stoppt oder startet einen Pterodactyl-Server neu. "
            "Aktionen: start, stop, restart, kill."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_id": {
                    "type": "string",
                    "description": "Server-Identifier (kurze ID, z.B. 'a1b2c3d4')",
                },
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "restart", "kill"],
                    "description": "Aktion die ausgeführt werden soll",
                },
            },
            "required": ["server_id", "action"],
        },
    )
    def pterodactyl_server_action(server_id: str, action: str, **ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        base_url = cfg.get("base_url", "")
        api_key = cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: Pterodactyl Base URL und API Key müssen konfiguriert sein."
        try:
            _api_request(
                base_url, api_key,
                f"/servers/{server_id}/power",
                method="POST",
                body={"signal": action},
            )
            action_labels = {
                "start": "gestartet",
                "stop": "gestoppt",
                "restart": "neu gestartet",
                "kill": "beendet (kill)",
            }
            return f"Server {server_id} wurde {action_labels.get(action, action)}."
        except RuntimeError as e:
            return f"API Fehler: {e}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="pterodactyl_console",
        description=(
            "Sendet einen Befehl an die Konsole eines Pterodactyl-Servers. "
            "Beispiel: 'list', 'say Hallo', 'stop'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_id": {
                    "type": "string",
                    "description": "Server-Identifier",
                },
                "command": {
                    "type": "string",
                    "description": "Befehl der an die Server-Konsole gesendet wird",
                },
            },
            "required": ["server_id", "command"],
        },
    )
    def pterodactyl_console(server_id: str, command: str, **ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        base_url = cfg.get("base_url", "")
        api_key = cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: Pterodactyl Base URL und API Key müssen konfiguriert sein."
        try:
            _api_request(
                base_url, api_key,
                f"/servers/{server_id}/command",
                method="POST",
                body={"command": command},
            )
            return f"Befehl an Server {server_id} gesendet: {command}"
        except RuntimeError as e:
            return f"API Fehler: {e}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="pterodactyl_resources",
        description=(
            "Zeigt Ressourcen-Nutzung eines Pterodactyl-Servers: CPU, RAM, Disk, "
            "Netzwerk, Status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_id": {
                    "type": "string",
                    "description": "Server-Identifier",
                },
            },
            "required": ["server_id"],
        },
    )
    def pterodactyl_resources(server_id: str, **ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        base_url = cfg.get("base_url", "")
        api_key = cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: Pterodactyl Base URL und API Key müssen konfiguriert sein."
        try:
            result = _api_request(base_url, api_key, f"/servers/{server_id}/resources")
            attrs = result.get("attributes", {})
            state = _fmt_status(attrs.get("current_state", "?"))
            res = attrs.get("resources", {})
            cpu = res.get("cpu_absolute", 0)
            mem_bytes = res.get("memory_bytes", 0)
            disk_bytes = res.get("disk_bytes", 0)
            net_rx = res.get("network_rx_bytes", 0)
            net_tx = res.get("network_tx_bytes", 0)
            uptime_ms = res.get("uptime", 0)
            uptime_min = uptime_ms // 60000 if uptime_ms else 0

            def fmt_mb(b: int) -> str:
                return f"{b / (1024**2):.1f} MB"

            lines = [
                f"Server:    {server_id}",
                f"Status:    {state}",
                f"CPU:       {cpu:.1f}%",
                f"RAM:       {fmt_mb(mem_bytes)}",
                f"Disk:      {fmt_mb(disk_bytes)}",
                f"Netz RX:   {fmt_mb(net_rx)}",
                f"Netz TX:   {fmt_mb(net_tx)}",
                f"Uptime:    {uptime_min} Min.",
            ]
            return "\n".join(lines)
        except RuntimeError as e:
            return f"API Fehler: {e}"
        except Exception as e:
            return f"Fehler: {e}"
