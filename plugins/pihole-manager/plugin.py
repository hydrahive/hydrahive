"""
pihole-manager Plugin — Pi-hole DNS-Filter via Admin API.

Tools:
  - pihole_stats:       Statistiken: geblockte Domains, Queries, Clients
  - pihole_top_blocked: Top geblockte Domains anzeigen
  - pihole_toggle:      Pi-hole aktivieren oder deaktivieren
  - pihole_query_log:   Letzte DNS-Queries anzeigen
"""
import json
import urllib.request
import urllib.error
import urllib.parse


def _load_config(username: str, plugin_id: str = "pihole-manager") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _api_get(base_url: str, params: dict) -> dict:
    url = base_url.rstrip("/") + "/admin/api.php?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


def register(api):

    @api.tool(
        tool_id="pihole_stats",
        description="Zeigt Pi-hole Statistiken: geblockte Domains, Query-Zahlen, aktive Clients und Status.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "Pi-hole URL z.B. http://pi.hole (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Pi-hole API Password/Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def pihole_stats(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url:
            return "Fehler: base_url benötigt"
        try:
            params = {"summaryRaw": ""}
            if api_key:
                params["auth"] = api_key
            data = _api_get(base_url, params)
            status = data.get("status", "?")
            total = data.get("dns_queries_today", 0)
            blocked = data.get("ads_blocked_today", 0)
            pct = data.get("ads_percentage_today", 0.0)
            clients = data.get("unique_clients", 0)
            domains_blocked = data.get("domains_being_blocked", 0)
            gravity_update = data.get("gravity_last_updated", {})
            gravity_str = ""
            if isinstance(gravity_update, dict):
                rel = gravity_update.get("relative", {})
                if rel:
                    gravity_str = f"{rel.get('days', 0)}d {rel.get('hours', 0)}h ago"
            lines = [
                "**Pi-hole Status:**",
                "",
                f"Status:          **{status.upper()}**",
                f"Queries heute:   {total:,}",
                f"Geblockt:        {blocked:,} ({pct:.1f}%)",
                f"Aktive Clients:  {clients}",
                f"Blocklist-Eintr: {domains_blocked:,}",
            ]
            if gravity_str:
                lines.append(f"Gravity-Update:  {gravity_str}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="pihole_top_blocked",
        description="Zeigt die am häufigsten geblockten Domains und die aktivsten anfragenden Clients.",
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Anzahl Einträge pro Liste (default: 10)"},
                "base_url": {"type": "string", "description": "Pi-hole URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Pi-hole API Token (benötigt für diese Abfrage)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def pihole_top_blocked(count: int = 10, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url:
            return "Fehler: base_url benötigt"
        if not api_key:
            return "Fehler: api_key benötigt (Top-Blocked-Daten erfordern Authentifizierung)"
        try:
            params = {"topItems": str(min(count, 100)), "auth": api_key}
            data = _api_get(base_url, params)
            top_ads = data.get("top_ads", {})
            top_sources = data.get("top_sources", {})
            lines = [f"**Pi-hole Top {count} Blocked Domains:**", ""]
            if top_ads:
                for domain, hits in list(top_ads.items())[:count]:
                    lines.append(f"  {hits:>6}x  {domain}")
            else:
                lines.append("  Keine Daten verfügbar")
            lines.append("")
            lines.append(f"**Top {count} Anfragende Clients:**")
            lines.append("")
            if top_sources:
                for client, queries in list(top_sources.items())[:count]:
                    lines.append(f"  {queries:>6}x  {client}")
            else:
                lines.append("  Keine Daten verfügbar")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="pihole_toggle",
        description="Aktiviert oder deaktiviert Pi-hole DNS-Blocking. Kann auch temporär für N Sekunden deaktiviert werden.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Aktion: enable oder disable"},
                "duration": {"type": "integer", "description": "Deaktivierungsdauer in Sekunden (nur bei disable, 0 = permanent)"},
                "base_url": {"type": "string", "description": "Pi-hole URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Pi-hole API Token (benötigt)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["action"],
        },
    )
    def pihole_toggle(action: str, duration: int = 0, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url:
            return "Fehler: base_url benötigt"
        if not api_key:
            return "Fehler: api_key benötigt"
        if action not in ("enable", "disable"):
            return "Fehler: Aktion muss 'enable' oder 'disable' sein"
        try:
            params = {action: "", "auth": api_key}
            if action == "disable" and duration > 0:
                params = {f"disable={duration}": "", "auth": api_key}
                params = {"disable": str(duration), "auth": api_key}
            data = _api_get(base_url, params)
            new_status = data.get("status", "?")
            if action == "disable" and duration > 0:
                return f"Pi-hole deaktiviert für {duration} Sekunden. Status: {new_status}"
            return f"Pi-hole {action}d. Neuer Status: **{new_status.upper()}**"
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="pihole_query_log",
        description="Zeigt die letzten DNS-Queries im Pi-hole Query Log mit Status (geblockt/erlaubt).",
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Anzahl Queries (default: 20, max: 100)"},
                "base_url": {"type": "string", "description": "Pi-hole URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Pi-hole API Token (benötigt)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def pihole_query_log(count: int = 20, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url:
            return "Fehler: base_url benötigt"
        if not api_key:
            return "Fehler: api_key benötigt (Query Log erfordert Authentifizierung)"
        count = min(count, 100)
        # Query type names
        query_types = {1: "A", 2: "AAAA", 3: "ANY", 4: "SRV", 5: "SOA", 6: "PTR", 7: "TXT", 8: "NAPTR", 9: "MX"}
        # Status codes
        status_map = {
            1: "CACHE", 2: "FORWARDED", 3: "BLOCKED(gravity)", 4: "BLOCKED(regex)",
            5: "BLOCKED(blacklist)", 6: "BLOCKED(NXDOMAIN)", 7: "BLOCKED(CNAME gravity)",
            8: "BLOCKED(CNAME regex)", 9: "BLOCKED(CNAME blacklist)", 10: "BLOCKED(rate-limit)",
            11: "ALLOWED(special)", 12: "RETRIED", 13: "RETRIED(ignored)",
        }
        try:
            params = {"getAllQueries": str(count), "auth": api_key}
            data = _api_get(base_url, params)
            queries = data.get("data", [])
            if not queries:
                return "Keine Queries im Log"
            lines = [f"**Pi-hole Query Log (letzte {len(queries)} Einträge):**", ""]
            import datetime
            for q in queries[-count:]:
                # Format: [timestamp, type, domain, client, status, ...]
                if len(q) < 5:
                    continue
                ts = datetime.datetime.fromtimestamp(int(q[0])).strftime("%H:%M:%S") if q[0] else "?"
                qtype = query_types.get(int(q[1]), str(q[1])) if q[1] else "?"
                domain = q[2] or "?"
                client = q[3] or "?"
                status = status_map.get(int(q[4]), f"STATUS({q[4]})") if q[4] else "?"
                lines.append(f"  {ts}  {qtype:5}  {domain[:40]:40}  {client:15}  {status}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"
