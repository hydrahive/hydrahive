"""
adguard-home Plugin — AdGuard Home API.

Tools:
  - adguard_status:    Status und Konfiguration anzeigen
  - adguard_stats:     DNS-Statistiken anzeigen
  - adguard_toggle:    DNS-Blocking ein-/ausschalten
  - adguard_query_log: Letzte DNS-Queries anzeigen
"""
import json
import urllib.request
import urllib.error
import urllib.parse
import base64


def _load_config(username: str, plugin_id: str = "adguard-home") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _api(base_url: str, username: str, password: str, path: str, method: str = "GET", body: dict = None) -> dict | list:
    url = base_url.rstrip("/") + "/control" + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        if not raw:
            return {}
        return json.loads(raw.decode())


def register(api):

    @api.tool(
        tool_id="adguard_status",
        description="Zeigt AdGuard Home Status: Blocking-Status, DNS-Upstream, Filterlisten-Anzahl und Versionsinformationen.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "AdGuard Home URL (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "AdGuard-Benutzername (optional, sonst aus Config)"},
                "password": {"type": "string", "description": "AdGuard-Passwort (optional, sonst aus Config)"},
                "hh_user": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def adguard_status(base_url: str = "", username: str = "", password: str = "", hh_user: str = "", **_) -> str:
        cfg = _load_config(hh_user) if hh_user else {}
        base_url = base_url or cfg.get("base_url", "")
        username = username or cfg.get("username", "")
        password = password or cfg.get("password", "")
        if not base_url:
            return "Fehler: base_url benötigt"
        try:
            status = _api(base_url, username, password, "/status")
            filters = _api(base_url, username, password, "/filtering/status")
            blocking = status.get("protection_enabled", False)
            running = status.get("running", False)
            version = status.get("version", "?")
            dns_addrs = status.get("dns_addresses", [])
            dns_port = status.get("dns_port", 53)
            upstreams = status.get("upstream_dns", [])
            filter_count = len(filters.get("filters", []))
            rules_count = filters.get("rules_count", 0)
            lines = [
                "**AdGuard Home Status:**",
                "",
                f"Dienst:          {'Läuft' if running else 'Gestoppt'}",
                f"Blocking:        {'AKTIV' if blocking else 'INAKTIV'}",
                f"Version:         {version}",
                f"DNS-Adressen:    {', '.join(dns_addrs)} :{dns_port}",
                f"Filterlisten:    {filter_count}",
                f"Filterregeln:    {rules_count:,}",
                "",
                "**Upstream DNS:**",
            ]
            for u in upstreams[:5]:
                lines.append(f"  {u}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="adguard_stats",
        description="Zeigt AdGuard Home DNS-Statistiken: Queries, geblockt, Top-Domains und Top-Clients.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "AdGuard Home URL (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "AdGuard-Benutzername (optional, sonst aus Config)"},
                "password": {"type": "string", "description": "AdGuard-Passwort (optional, sonst aus Config)"},
                "hh_user": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def adguard_stats(base_url: str = "", username: str = "", password: str = "", hh_user: str = "", **_) -> str:
        cfg = _load_config(hh_user) if hh_user else {}
        base_url = base_url or cfg.get("base_url", "")
        username = username or cfg.get("username", "")
        password = password or cfg.get("password", "")
        if not base_url:
            return "Fehler: base_url benötigt"
        try:
            stats = _api(base_url, username, password, "/stats")
            total = stats.get("num_dns_queries", 0)
            blocked = stats.get("num_blocked_filtering", 0)
            replaced_safebrowsing = stats.get("num_replaced_safebrowsing", 0)
            replaced_parental = stats.get("num_replaced_parental", 0)
            avg_time = stats.get("avg_processing_time", 0.0)
            top_domains = stats.get("top_queried_domains", [])[:5]
            top_blocked = stats.get("top_blocked_domains", [])[:5]
            top_clients = stats.get("top_clients", [])[:5]
            pct = (blocked / total * 100) if total > 0 else 0
            lines = [
                "**AdGuard Home Statistiken (24h):**",
                "",
                f"Queries gesamt:   {total:,}",
                f"Geblockt:         {blocked:,} ({pct:.1f}%)",
                f"Safe Browsing:    {replaced_safebrowsing:,}",
                f"Parental Control: {replaced_parental:,}",
                f"Ø Antwortzeit:    {avg_time * 1000:.1f} ms",
                "",
                "**Top Domains:**",
            ]
            for d in top_domains:
                if isinstance(d, dict):
                    for k, v in d.items():
                        lines.append(f"  {v:>6}x  {k}")
            lines.append("")
            lines.append("**Top Geblockte Domains:**")
            for d in top_blocked:
                if isinstance(d, dict):
                    for k, v in d.items():
                        lines.append(f"  {v:>6}x  {k}")
            lines.append("")
            lines.append("**Top Clients:**")
            for c in top_clients:
                if isinstance(c, dict):
                    for k, v in c.items():
                        lines.append(f"  {v:>6}x  {k}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="adguard_toggle",
        description="Aktiviert oder deaktiviert AdGuard Home DNS-Blocking.",
        parameters={
            "type": "object",
            "properties": {
                "enable": {"type": "boolean", "description": "true = Blocking aktivieren, false = deaktivieren"},
                "base_url": {"type": "string", "description": "AdGuard Home URL (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "AdGuard-Benutzername (optional, sonst aus Config)"},
                "password": {"type": "string", "description": "AdGuard-Passwort (optional, sonst aus Config)"},
                "hh_user": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["enable"],
        },
    )
    def adguard_toggle(enable: bool, base_url: str = "", username: str = "", password: str = "", hh_user: str = "", **_) -> str:
        cfg = _load_config(hh_user) if hh_user else {}
        base_url = base_url or cfg.get("base_url", "")
        username = username or cfg.get("username", "")
        password = password or cfg.get("password", "")
        if not base_url:
            return "Fehler: base_url benötigt"
        try:
            _api(base_url, username, password, "/protection", method="POST", body={"enabled": enable})
            state = "aktiviert" if enable else "deaktiviert"
            return f"AdGuard Home DNS-Blocking {state}."
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="adguard_query_log",
        description="Zeigt die letzten DNS-Queries im AdGuard Home Query Log mit Blocking-Status und Client.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Anzahl Einträge (default: 20, max: 100)"},
                "filter": {"type": "string", "description": "Nur Queries die diesen Begriff enthalten"},
                "blocked_only": {"type": "boolean", "description": "Nur geblockte Queries anzeigen"},
                "base_url": {"type": "string", "description": "AdGuard Home URL (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "AdGuard-Benutzername (optional, sonst aus Config)"},
                "password": {"type": "string", "description": "AdGuard-Passwort (optional, sonst aus Config)"},
                "hh_user": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def adguard_query_log(limit: int = 20, filter: str = "", blocked_only: bool = False, base_url: str = "", username: str = "", password: str = "", hh_user: str = "", **_) -> str:
        cfg = _load_config(hh_user) if hh_user else {}
        base_url = base_url or cfg.get("base_url", "")
        username = username or cfg.get("username", "")
        password = password or cfg.get("password", "")
        if not base_url:
            return "Fehler: base_url benötigt"
        limit = min(limit, 100)
        try:
            params = {"limit": str(limit)}
            if filter:
                params["search"] = filter
            if blocked_only:
                params["response_status"] = "filtered"
            qs = urllib.parse.urlencode(params)
            data = _api(base_url, username, password, f"/querylog?{qs}")
            entries = data.get("data", [])
            lines = [f"**AdGuard Query Log ({len(entries)} Einträge):**", ""]
            for entry in entries:
                time_str = entry.get("time", "")[:19].replace("T", " ")
                question = entry.get("question", {})
                domain = question.get("name", "?")
                qtype = question.get("type", "?")
                client = entry.get("client", "?")
                reason = entry.get("reason", "")
                answer_dnssec = entry.get("answer_dnssec", False)
                elapsed = entry.get("elapsedMs", 0)
                # Determine blocked status
                filtered = entry.get("filtered", False)
                if filtered:
                    status = f"BLOCKED ({reason})"
                else:
                    status = "OK"
                lines.append(f"  {time_str}  {qtype:5}  {domain[:45]:45}  {client:15}")
                lines.append(f"    {status} | {elapsed:.1f}ms")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"
