"""
portainer-manager Plugin — Portainer via REST API.

Tools:
  - portainer_containers:       Container aller Endpoints auflisten
  - portainer_container_action: Container starten, stoppen oder neustarten
  - portainer_stacks:           Docker Compose Stacks anzeigen
  - portainer_container_logs:   Container-Logs abrufen
"""
import json
import urllib.request
import urllib.error
import ssl


def _load_config(username: str, plugin_id: str = "portainer-manager") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _api_request(base_url: str, api_key: str, path: str, method: str = "GET", body=None):
    url = base_url.rstrip("/") + "/api/" + path.lstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", api_key)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=15) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw.strip() else {}


def _get_endpoints(base_url: str, api_key: str) -> list:
    try:
        return _api_request(base_url, api_key, "endpoints") or []
    except Exception:
        return []


def register(api):

    @api.tool(
        tool_id="portainer_containers",
        description="Listet alle Docker Container in Portainer mit Status, Image und Endpoint auf.",
        parameters={
            "type": "object",
            "properties": {
                "endpoint_id": {"type": "integer", "description": "Portainer Endpoint ID (leer = erster Endpoint)"},
                "all_containers": {"type": "boolean", "description": "Auch gestoppte Container anzeigen (default: true)"},
                "base_url": {"type": "string", "description": "Portainer URL z.B. https://portainer.local:9443 (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Portainer API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def portainer_containers(endpoint_id: int = 0, all_containers: bool = True,
                             base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            if not endpoint_id:
                endpoints = _get_endpoints(base_url, api_key)
                endpoint_id = endpoints[0].get("Id", 1) if endpoints else 1
            all_param = "1" if all_containers else "0"
            containers = _api_request(base_url, api_key, f"endpoints/{endpoint_id}/docker/containers/json?all={all_param}")
            if not containers:
                return "Keine Container gefunden"
            lines = [f"**Portainer Container (Endpoint {endpoint_id}, {len(containers)} total):**", ""]
            for c in containers:
                names = ", ".join(n.lstrip("/") for n in c.get("Names", ["?"]))
                image = c.get("Image", "?")
                state = c.get("State", "?")
                status = c.get("Status", "?")
                ports = c.get("Ports", [])
                port_str = ", ".join(
                    f"{p.get('IP', '')}:{p.get('PublicPort', '')}→{p.get('PrivatePort', '')}/{p.get('Type', '')}"
                    for p in ports if p.get("PublicPort")
                ) or "keine"
                lines.append(f"  **{names}** [{state}]")
                lines.append(f"    Image: {image}")
                lines.append(f"    Status: {status} | Ports: {port_str}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="portainer_container_action",
        description="Startet, stoppt oder startet einen Portainer Container neu.",
        parameters={
            "type": "object",
            "properties": {
                "container_id": {"type": "string", "description": "Container ID oder Name"},
                "action": {"type": "string", "description": "Aktion: start, stop, restart, pause, unpause"},
                "endpoint_id": {"type": "integer", "description": "Portainer Endpoint ID (default: 1)"},
                "base_url": {"type": "string", "description": "Portainer URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Portainer API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["container_id", "action"],
        },
    )
    def portainer_container_action(container_id: str, action: str, endpoint_id: int = 1,
                                   base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        valid_actions = {"start", "stop", "restart", "pause", "unpause"}
        if action not in valid_actions:
            return f"Fehler: Ungültige Aktion '{action}'. Erlaubt: {', '.join(valid_actions)}"
        try:
            _api_request(
                base_url, api_key,
                f"endpoints/{endpoint_id}/docker/containers/{container_id}/{action}",
                method="POST"
            )
            return f"Aktion '{action}' für Container '{container_id}' auf Endpoint {endpoint_id} ausgeführt."
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="portainer_stacks",
        description="Listet alle Docker Compose Stacks in Portainer mit Status und Anzahl Services.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "Portainer URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Portainer API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def portainer_stacks(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            stacks = _api_request(base_url, api_key, "stacks")
            if not stacks:
                return "Keine Stacks gefunden"
            lines = [f"**Portainer Stacks ({len(stacks)}):**", ""]
            for s in stacks:
                name = s.get("Name", "?")
                status = s.get("Status", 0)
                status_str = "aktiv" if status == 1 else "gestoppt"
                endpoint = s.get("EndpointId", "?")
                stack_type = "Compose" if s.get("Type", 1) == 1 else "Swarm"
                created = s.get("CreationDate", "?")
                lines.append(f"  **{name}** [{status_str}]")
                lines.append(f"    Typ: {stack_type} | Endpoint: {endpoint} | Erstellt: {created}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="portainer_container_logs",
        description="Ruft die Logs eines Portainer Containers ab.",
        parameters={
            "type": "object",
            "properties": {
                "container_id": {"type": "string", "description": "Container ID oder Name"},
                "lines": {"type": "integer", "description": "Anzahl Zeilen (default: 50, max: 500)"},
                "endpoint_id": {"type": "integer", "description": "Portainer Endpoint ID (default: 1)"},
                "base_url": {"type": "string", "description": "Portainer URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Portainer API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["container_id"],
        },
    )
    def portainer_container_logs(container_id: str, lines: int = 50, endpoint_id: int = 1,
                                 base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        lines = min(lines, 500)
        try:
            url = (f"{base_url.rstrip('/')}/api/endpoints/{endpoint_id}/docker/containers/"
                   f"{container_id}/logs?stdout=1&stderr=1&tail={lines}")
            req = urllib.request.Request(url)
            req.add_header("X-API-Key", api_key)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            # Strip Docker log multiplexing headers (8-byte prefix per line)
            cleaned = []
            for line in raw.splitlines():
                cleaned.append(line[8:] if len(line) > 8 and line[0] in ("\x01", "\x02") else line)
            return "\n".join(cleaned) or "(keine Logs)"
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"
