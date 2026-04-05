"""
heimdall-manager Plugin — Heimdall Application Dashboard via API.

Tools:
  - heimdall_apps:         Alle konfigurierten Apps im Dashboard anzeigen
  - heimdall_add_app:      Eine neue App zum Dashboard hinzufügen
  - heimdall_check_status: HTTP-Erreichbarkeit der Apps prüfen
"""
import json
import urllib.request
import urllib.error
import urllib.parse


def _load_config(username: str, plugin_id: str = "heimdall-manager") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _api_request(base_url: str, api_key: str, path: str, method: str = "GET", body=None):
    url = base_url.rstrip("/") + "/api/" + path.lstrip("/")
    data = None
    if body is not None:
        data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    if data:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _check_url(url: str, timeout: int = 5) -> tuple[bool, int, str]:
    """Prüft ob eine URL erreichbar ist. Gibt (ok, status_code, error) zurück."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "HydraHive-StatusCheck/1.0")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.status, ""
    except urllib.error.HTTPError as e:
        # HTTP errors like 401, 403 still mean the server is reachable
        return True, e.code, ""
    except urllib.error.URLError as e:
        return False, 0, str(e.reason)
    except Exception as e:
        return False, 0, str(e)


def register(api):

    @api.tool(
        tool_id="heimdall_apps",
        description="Zeigt alle konfigurierten Apps im Heimdall Application Dashboard mit URL und Typ.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "Heimdall URL z.B. http://heimdall.local (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Heimdall API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def heimdall_apps(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            data = _api_request(base_url, api_key, "items")
            # Heimdall returns {"data": [...]} or a list directly
            items = data.get("data", data) if isinstance(data, dict) else data
            if not items:
                return "Keine Apps im Heimdall Dashboard gefunden"
            lines = [f"**Heimdall Apps ({len(items)}):**", ""]
            for item in items:
                title = item.get("title", item.get("name", "?"))
                app_url = item.get("url", item.get("link", ""))
                colour = item.get("colour", "")
                app_type = item.get("appid", "")
                pinned = item.get("pinned", False)
                lines.append(f"  **{title}**{' [pinned]' if pinned else ''}")
                if app_url:
                    lines.append(f"    URL: {app_url}")
                if app_type:
                    lines.append(f"    Typ: {app_type}")
                if colour:
                    lines.append(f"    Farbe: {colour}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="heimdall_add_app",
        description="Fügt eine neue App zum Heimdall Application Dashboard hinzu.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "App-Name / Titel"},
                "url": {"type": "string", "description": "URL der App z.B. http://192.168.1.100:8080"},
                "colour": {"type": "string", "description": "Farbe im Hex-Format z.B. #007bff (optional)"},
                "app_type": {"type": "string", "description": "Heimdall App-Typ / Application ID (optional, z.B. 'Portainer')"},
                "pinned": {"type": "boolean", "description": "App anpinnen (default: false)"},
                "base_url": {"type": "string", "description": "Heimdall URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Heimdall API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["title", "url"],
        },
    )
    def heimdall_add_app(title: str, url: str, colour: str = "#007bff", app_type: str = "",
                         pinned: bool = False, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            payload = {
                "title": title,
                "url": url,
                "colour": colour,
                "pinned": "1" if pinned else "0",
            }
            if app_type:
                payload["appid"] = app_type
            result = _api_request(base_url, api_key, "items", method="POST", body=payload)
            item = result.get("data", result) if isinstance(result, dict) else result
            if isinstance(item, dict):
                item_id = item.get("id", "?")
                return f"App '{title}' hinzugefügt (ID: {item_id}) — URL: {url}"
            return f"App '{title}' hinzugefügt — Antwort: {result}"
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()
            except Exception:
                pass
            return f"HTTP Fehler {e.code}: {e.reason}{f' — {body}' if body else ''}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="heimdall_check_status",
        description="Prüft die HTTP-Erreichbarkeit aller Apps im Heimdall Dashboard und zeigt welche online/offline sind.",
        parameters={
            "type": "object",
            "properties": {
                "timeout": {"type": "integer", "description": "Timeout pro App in Sekunden (default: 5)"},
                "base_url": {"type": "string", "description": "Heimdall URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Heimdall API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def heimdall_check_status(timeout: int = 5, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        timeout = min(timeout, 15)
        try:
            data = _api_request(base_url, api_key, "items")
            items = data.get("data", data) if isinstance(data, dict) else data
            if not items:
                return "Keine Apps gefunden"
            online = []
            offline = []
            skipped = []
            for item in items:
                title = item.get("title", item.get("name", "?"))
                app_url = item.get("url", item.get("link", "")).strip()
                if not app_url or not app_url.startswith("http"):
                    skipped.append(title)
                    continue
                ok, status, err = _check_url(app_url, timeout=timeout)
                if ok:
                    online.append((title, app_url, status))
                else:
                    offline.append((title, app_url, err))
            lines = [f"**Heimdall App Status ({len(items)} Apps):**", ""]
            lines.append(f"Online:  {len(online)}")
            lines.append(f"Offline: {len(offline)}")
            if skipped:
                lines.append(f"Ohne URL: {len(skipped)}")
            lines.append("")
            if online:
                lines.append("**Online:**")
                for title, app_url, status in online:
                    lines.append(f"  OK [{status}]  {title} — {app_url}")
            if offline:
                lines.append("")
                lines.append("**Offline / Nicht erreichbar:**")
                for title, app_url, err in offline:
                    lines.append(f"  FEHLER  {title} — {app_url}")
                    if err:
                        lines.append(f"          {err}")
            if skipped:
                lines.append("")
                lines.append(f"**Ohne URL:** {', '.join(skipped)}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"
