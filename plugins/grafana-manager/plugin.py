"""
grafana-manager Plugin — Grafana via HTTP API.

Tools:
  - grafana_dashboards:  Alle Dashboards auflisten
  - grafana_alerts:      Alert-Status abrufen
  - grafana_query:       Datasource-Query ausführen (PromQL / Loki etc.)
  - grafana_annotations: Annotations anzeigen oder erstellen
"""
import json
import urllib.request
import urllib.error
import urllib.parse
import base64


def _load_config(username: str, plugin_id: str = "grafana-manager") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _api_request(base_url: str, api_key: str, path: str, method: str = "GET", body=None):
    url = base_url.rstrip("/") + "/api/" + path.lstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    # Support both Bearer token and basic auth (user:password)
    if ":" in api_key and not api_key.startswith("glsa_") and not api_key.startswith("eyJ"):
        encoded = base64.b64encode(api_key.encode()).decode()
        req.add_header("Authorization", f"Basic {encoded}")
    else:
        req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def register(api):

    @api.tool(
        tool_id="grafana_dashboards",
        description="Listet alle Grafana Dashboards mit Titel, Ordner und UID auf.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriff für Dashboard-Namen (optional)"},
                "base_url": {"type": "string", "description": "Grafana URL z.B. http://grafana.local:3000 (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Grafana Service Account Token oder user:password (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def grafana_dashboards(query: str = "", base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            params = "search/dashboards?type=dash-db&limit=100"
            if query:
                params += f"&query={urllib.parse.quote(query)}"
            dashboards = _api_request(base_url, api_key, params)
            if not dashboards:
                return "Keine Dashboards gefunden"
            lines = [f"**Grafana Dashboards ({len(dashboards)}):**", ""]
            # Group by folder
            folders: dict[str, list] = {}
            for d in dashboards:
                folder = d.get("folderTitle", "General")
                folders.setdefault(folder, []).append(d)
            for folder, items in sorted(folders.items()):
                lines.append(f"**{folder}/**")
                for d in items:
                    uid = d.get("uid", "?")
                    title = d.get("title", "?")
                    url_path = d.get("url", "")
                    lines.append(f"  [{uid}] {title}")
                    if url_path:
                        lines.append(f"    URL: {base_url.rstrip('/')}{url_path}")
                lines.append("")
            return "\n".join(lines).rstrip()
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="grafana_alerts",
        description="Zeigt den aktuellen Alert-Status aller Grafana Alerting-Regeln.",
        parameters={
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "Filter nach Status: all, alerting, pending, ok, paused (default: all)"},
                "base_url": {"type": "string", "description": "Grafana URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Grafana API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def grafana_alerts(state: str = "all", base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            # Try Grafana Alerting API (v9+)
            try:
                rules = _api_request(base_url, api_key, "prometheus/grafana/api/v1/alerts")
                alerts = rules.get("data", {}).get("alerts", [])
                if state != "all":
                    alerts = [a for a in alerts if a.get("state", "").lower() == state.lower()]
                if not alerts:
                    return f"Keine Alerts{f' mit Status {state}' if state != 'all' else ''}"
                lines = [f"**Grafana Alerts ({len(alerts)}):**", ""]
                for a in alerts:
                    name = a.get("labels", {}).get("alertname", "?")
                    astate = a.get("state", "?")
                    severity = a.get("labels", {}).get("severity", "")
                    summary = a.get("annotations", {}).get("summary", "")
                    lines.append(f"  [{astate.upper()}] **{name}**{f' ({severity})' if severity else ''}")
                    if summary:
                        lines.append(f"    {summary}")
                return "\n".join(lines)
            except urllib.error.HTTPError:
                pass
            # Fallback: legacy alert API
            endpoint = "alerts"
            if state != "all":
                endpoint += f"?state={state}"
            alerts = _api_request(base_url, api_key, endpoint)
            if not alerts:
                return "Keine Alerts gefunden"
            lines = [f"**Grafana Alerts ({len(alerts)}):**", ""]
            for a in alerts:
                name = a.get("name", "?")
                astate = a.get("state", "?")
                dashboard = a.get("dashboardSlug", "?")
                lines.append(f"  [{astate.upper()}] **{name}** (Dashboard: {dashboard})")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="grafana_query",
        description="Führt eine Query gegen eine Grafana Datasource aus (z.B. PromQL für Prometheus, LogQL für Loki).",
        parameters={
            "type": "object",
            "properties": {
                "datasource_uid": {"type": "string", "description": "UID der Datasource (z.B. 'prometheus', 'loki' oder die tatsächliche UID)"},
                "expr": {"type": "string", "description": "Query-Ausdruck (PromQL, LogQL etc.)"},
                "from_time": {"type": "string", "description": "Startzeitpunkt (default: 'now-1h')"},
                "to_time": {"type": "string", "description": "Endzeitpunkt (default: 'now')"},
                "base_url": {"type": "string", "description": "Grafana URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Grafana API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["datasource_uid", "expr"],
        },
    )
    def grafana_query(datasource_uid: str, expr: str, from_time: str = "now-1h", to_time: str = "now",
                      base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            payload = {
                "queries": [
                    {
                        "refId": "A",
                        "datasource": {"uid": datasource_uid},
                        "expr": expr,
                    }
                ],
                "from": from_time,
                "to": to_time,
            }
            result = _api_request(base_url, api_key, "ds/query", method="POST", body=payload)
            results = result.get("results", {})
            a_result = results.get("A", {})
            frames = a_result.get("frames", [])
            if not frames:
                return f"Keine Daten für Query: {expr}"
            lines = [f"**Grafana Query Result:**", f"Datasource: {datasource_uid}", f"Expr: {expr}", ""]
            for frame in frames[:5]:
                schema = frame.get("schema", {})
                data = frame.get("data", {})
                fields = schema.get("fields", [])
                values = data.get("values", [])
                if fields and values:
                    # Show last value for each series
                    name = schema.get("name", "") or (fields[1].get("name") if len(fields) > 1 else "value")
                    labels = fields[1].get("labels", {}) if len(fields) > 1 else {}
                    label_str = ", ".join(f"{k}={v}" for k, v in labels.items()) if labels else ""
                    last_val = values[-1][-1] if values else "?"
                    lines.append(f"  {name}{f' {{{label_str}}}' if label_str else ''}: {last_val}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="grafana_annotations",
        description="Zeigt bestehende Grafana Annotations oder erstellt eine neue Annotation.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list (default) oder create"},
                "text": {"type": "string", "description": "Annotations-Text (nur bei action=create)"},
                "tags": {"type": "string", "description": "Komma-getrennte Tags (optional, z.B. 'deploy,v2.0')"},
                "limit": {"type": "integer", "description": "Max. Anzahl Annotations bei list (default: 20)"},
                "base_url": {"type": "string", "description": "Grafana URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Grafana API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def grafana_annotations(action: str = "list", text: str = "", tags: str = "",
                            limit: int = 20, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            if action == "create":
                if not text:
                    return "Fehler: text benötigt für action=create"
                import time
                payload = {
                    "text": text,
                    "time": int(time.time() * 1000),
                }
                if tags:
                    payload["tags"] = [t.strip() for t in tags.split(",")]
                result = _api_request(base_url, api_key, "annotations", method="POST", body=payload)
                return f"Annotation erstellt: ID {result.get('id', '?')} — {result.get('message', 'OK')}"
            else:
                params = f"annotations?limit={min(limit, 100)}"
                if tags:
                    for tag in tags.split(","):
                        params += f"&tags={urllib.parse.quote(tag.strip())}"
                annotations = _api_request(base_url, api_key, params)
                if not annotations:
                    return "Keine Annotations gefunden"
                import datetime
                lines = [f"**Grafana Annotations ({len(annotations)}):**", ""]
                for a in annotations:
                    ts = a.get("time", 0)
                    ts_str = datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M") if ts else "?"
                    atags = ", ".join(a.get("tags", []))
                    atext = a.get("text", "?")[:80]
                    lines.append(f"  {ts_str}  {atext}")
                    if atags:
                        lines.append(f"    Tags: {atags}")
                return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"
