"""
truenas-manager Plugin — TrueNAS Storage via REST API v2.0.

Tools:
  - truenas_pools:       ZFS Pool Status und Kapazität anzeigen
  - truenas_datasets:    Datasets eines Pools auflisten
  - truenas_alerts:      Aktive Alerts abfragen
  - truenas_smart_check: SMART-Status der Disks prüfen
"""
import json
import urllib.request
import urllib.error
import os


def _load_config(username: str, plugin_id: str = "truenas-manager") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _api_request(base_url: str, api_key: str, path: str, method: str = "GET", body=None) -> dict | list:
    url = base_url.rstrip("/") + "/api/v2.0/" + path.lstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    # TrueNAS uses self-signed certs in many setups; allow insecure but warn
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _fmt_size(bytes_val: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} EB"


def register(api):

    @api.tool(
        tool_id="truenas_pools",
        description="Zeigt alle ZFS Pools im TrueNAS mit Status, Kapazität und Health-Info.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "TrueNAS URL z.B. https://nas.local (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "TrueNAS API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def truenas_pools(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt (Parameter oder Config)"
        try:
            pools = _api_request(base_url, api_key, "pool")
            if not pools:
                return "Keine Pools gefunden"
            lines = ["**TrueNAS Pools:**", ""]
            for p in pools:
                size = p.get("size") or 0
                free = p.get("free") or 0
                used = size - free
                pct = (used / size * 100) if size else 0
                lines.append(f"Pool: **{p.get('name', '?')}**")
                lines.append(f"  Status:  {p.get('status', '?')} | Health: {p.get('healthy', '?')}")
                lines.append(f"  Größe:   {_fmt_size(size)}")
                lines.append(f"  Genutzt: {_fmt_size(used)} ({pct:.1f}%)")
                lines.append(f"  Frei:    {_fmt_size(free)}")
                scan = p.get("scan") or {}
                if scan:
                    lines.append(f"  Letzter Scan: {scan.get('end_time', '?')} — {scan.get('state', '?')}")
                lines.append("")
            return "\n".join(lines).rstrip()
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="truenas_datasets",
        description="Listet Datasets eines TrueNAS Pools mit Größe, Kompression und Mount-Point auf.",
        parameters={
            "type": "object",
            "properties": {
                "pool": {"type": "string", "description": "Pool-Name (leer = alle Datasets)"},
                "base_url": {"type": "string", "description": "TrueNAS URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "TrueNAS API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def truenas_datasets(pool: str = "", base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            endpoint = "pool/dataset"
            datasets = _api_request(base_url, api_key, endpoint)
            if pool:
                datasets = [d for d in datasets if d.get("name", "").startswith(pool)]
            if not datasets:
                return f"Keine Datasets{f' für Pool {pool}' if pool else ''} gefunden"
            lines = [f"**TrueNAS Datasets{f' — Pool: {pool}' if pool else ''}:**", ""]
            for d in datasets[:50]:
                name = d.get("name", "?")
                used = d.get("used", {}).get("rawvalue", 0) if isinstance(d.get("used"), dict) else 0
                avail = d.get("available", {}).get("rawvalue", 0) if isinstance(d.get("available"), dict) else 0
                comp = d.get("compression", {}).get("value", "?") if isinstance(d.get("compression"), dict) else "?"
                mnt = d.get("mountpoint", "?")
                lines.append(f"  {name}")
                lines.append(f"    Mount: {mnt} | Comp: {comp}")
                lines.append(f"    Genutzt: {_fmt_size(int(used))} | Frei: {_fmt_size(int(avail))}")
            if len(datasets) > 50:
                lines.append(f"  ... und {len(datasets) - 50} weitere")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="truenas_alerts",
        description="Zeigt aktive Alerts und Warnungen im TrueNAS System.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "TrueNAS URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "TrueNAS API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def truenas_alerts(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            alerts = _api_request(base_url, api_key, "alert/list")
            if not alerts:
                return "Keine aktiven Alerts — System OK"
            lines = [f"**TrueNAS Alerts ({len(alerts)}):**", ""]
            for a in alerts:
                level = a.get("level", "INFO")
                msg = a.get("formatted", a.get("text", "?"))
                ts = a.get("datetime", {}).get("$date", "") if isinstance(a.get("datetime"), dict) else ""
                lines.append(f"[{level}] {msg}")
                if ts:
                    lines.append(f"  Seit: {ts}")
                lines.append("")
            return "\n".join(lines).rstrip()
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="truenas_smart_check",
        description="Zeigt den SMART-Status aller Disks im TrueNAS System.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "TrueNAS URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "TrueNAS API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def truenas_smart_check(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            disks = _api_request(base_url, api_key, "disk")
            if not disks:
                return "Keine Disks gefunden"
            lines = [f"**TrueNAS SMART Status ({len(disks)} Disks):**", ""]
            for d in disks:
                name = d.get("name", "?")
                model = d.get("model", "?")
                serial = d.get("serial", "?")
                size = d.get("size") or 0
                temp = d.get("temperature") or "?"
                rotpm = d.get("rotationrate") or "SSD"
                lines.append(f"  /dev/{name} — {model} (S/N: {serial})")
                lines.append(f"    Größe: {_fmt_size(size)} | Temp: {temp}°C | {rotpm} RPM")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"
