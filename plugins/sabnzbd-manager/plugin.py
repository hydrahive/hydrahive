"""
sabnzbd-manager Plugin — SABnzbd API.

Tools:
  - sabnzbd_queue:   Download-Queue anzeigen
  - sabnzbd_history: Abgeschlossene Downloads anzeigen
  - sabnzbd_pause:   Queue pausieren
  - sabnzbd_resume:  Queue fortsetzen
"""
import json
import urllib.request
import urllib.error
import urllib.parse


def _load_config(username: str, plugin_id: str = "sabnzbd-manager") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _api(base_url: str, api_key: str, params: dict) -> dict:
    params["apikey"] = api_key
    params["output"] = "json"
    url = base_url.rstrip("/") + "/api?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _fmt_size(mb: float) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.0f} MB"


def register(api):

    @api.tool(
        tool_id="sabnzbd_queue",
        description="Zeigt die SABnzbd Download-Queue mit Fortschritt, Geschwindigkeit und Restzeit.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "SABnzbd URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "SABnzbd API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def sabnzbd_queue(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            data = _api(base_url, api_key, {"mode": "queue"})
            q = data.get("queue", {})
            status = q.get("status", "?")
            speed = q.get("speed", "0")
            timeleft = q.get("timeleft", "0:00:00")
            size_left = q.get("mbleft", 0)
            size_total = q.get("mb", 0)
            slots = q.get("slots", [])
            lines = [
                "**SABnzbd Queue:**",
                "",
                f"Status:     {status}",
                f"Speed:      {speed}",
                f"Remaining:  {_fmt_size(float(size_left))} / {_fmt_size(float(size_total))}",
                f"ETA:        {timeleft}",
                f"Items:      {len(slots)}",
                "",
            ]
            for slot in slots[:20]:
                name = slot.get("filename", "?")[:60]
                pct = slot.get("percentage", 0)
                mb_left = slot.get("mbleft", 0)
                cat = slot.get("cat", "")
                prio = slot.get("priority", "")
                cat_str = f" [{cat}]" if cat else ""
                lines.append(f"  {pct:3}%  {name}{cat_str}")
                lines.append(f"        noch {_fmt_size(float(mb_left))} | Prio: {prio}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="sabnzbd_history",
        description="Zeigt die SABnzbd Download-History mit Status, Größe und Kategorie.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Anzahl Einträge (default: 20, max: 100)"},
                "category": {"type": "string", "description": "Nur Einträge dieser Kategorie anzeigen"},
                "base_url": {"type": "string", "description": "SABnzbd URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "SABnzbd API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def sabnzbd_history(limit: int = 20, category: str = "", base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        limit = min(limit, 100)
        try:
            params = {"mode": "history", "limit": str(limit)}
            if category:
                params["category"] = category
            data = _api(base_url, api_key, params)
            history = data.get("history", {})
            slots = history.get("slots", [])
            total_size = history.get("total_size", "?")
            month_size = history.get("month_size", "?")
            lines = [
                f"**SABnzbd History ({len(slots)} Einträge):**",
                f"Gesamt: {total_size} | Diesen Monat: {month_size}",
                "",
            ]
            status_icons = {"Completed": "✓", "Failed": "✗", "Extracting": "⟳", "Verifying": "⟳"}
            for slot in slots:
                name = slot.get("name", "?")[:60]
                status = slot.get("status", "?")
                size = slot.get("size", "?")
                cat = slot.get("category", "")
                stage = slot.get("stage_log", [{}])
                fail_msg = slot.get("fail_message", "")
                icon = status_icons.get(status, "?")
                cat_str = f" [{cat}]" if cat else ""
                lines.append(f"  {icon} {name}{cat_str}")
                lines.append(f"    {status} | {size}")
                if fail_msg:
                    lines.append(f"    ⚠ {fail_msg}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="sabnzbd_pause",
        description="Pausiert die SABnzbd Download-Queue. Optional für eine bestimmte Dauer in Minuten.",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "integer", "description": "Pausendauer in Minuten (0 = permanent, default: 0)"},
                "base_url": {"type": "string", "description": "SABnzbd URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "SABnzbd API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def sabnzbd_pause(duration: int = 0, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            if duration > 0:
                params = {"mode": "config", "name": "set_pause", "value": str(duration)}
            else:
                params = {"mode": "pause"}
            data = _api(base_url, api_key, params)
            if data.get("status") is True or data.get("result") == "ok":
                if duration > 0:
                    return f"SABnzbd Queue pausiert für {duration} Minuten."
                return "SABnzbd Queue pausiert."
            return f"Antwort: {json.dumps(data)}"
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="sabnzbd_resume",
        description="Setzt die SABnzbd Download-Queue fort (nach Pause).",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "SABnzbd URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "SABnzbd API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def sabnzbd_resume(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            data = _api(base_url, api_key, {"mode": "resume"})
            if data.get("status") is True or data.get("result") == "ok":
                return "SABnzbd Queue fortgesetzt."
            return f"Antwort: {json.dumps(data)}"
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"
