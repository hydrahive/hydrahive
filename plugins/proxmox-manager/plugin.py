"""
proxmox-manager Plugin — Proxmox VE via REST API.

Tools:
  - proxmox_nodes:     Cluster-Nodes und deren Status anzeigen
  - proxmox_vms:       VMs und Container auf einem Node listen
  - proxmox_vm_action: VM starten, stoppen oder neustarten
  - proxmox_resources: Cluster-weite Ressourcenübersicht (CPU, RAM, Storage)
"""
import json
import urllib.request
import urllib.error
import ssl


def _load_config(username: str, plugin_id: str = "proxmox-manager") -> dict:
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


def _api_request(base_url: str, api_key: str, path: str, method: str = "GET", body=None) -> dict | list:
    """Proxmox API Token auth: 'PVEAPIToken=user@realm!tokenid=secret'"""
    url = base_url.rstrip("/") + "/api2/json/" + path.lstrip("/")
    data = urllib.parse.urlencode(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"PVEAPIToken={api_key}")
    if body:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=15) as resp:
        result = json.loads(resp.read().decode())
        return result.get("data", result)


import urllib.parse


def _fmt_pct(val) -> str:
    try:
        return f"{float(val) * 100:.1f}%"
    except Exception:
        return str(val)


def _fmt_bytes(val) -> str:
    try:
        val = int(val)
    except Exception:
        return str(val)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024:
            return f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} PB"


def register(api):

    @api.tool(
        tool_id="proxmox_nodes",
        description="Zeigt alle Proxmox Cluster-Nodes mit CPU-, RAM-Auslastung und Status.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "Proxmox URL z.B. https://pve.local:8006 (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "API Token user@realm!tokenid=secret (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def proxmox_nodes(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            nodes = _api_request(base_url, api_key, "nodes")
            if not nodes:
                return "Keine Nodes gefunden"
            lines = [f"**Proxmox Nodes ({len(nodes)}):**", ""]
            for n in nodes:
                name = n.get("node", "?")
                status = n.get("status", "?")
                cpu = _fmt_pct(n.get("cpu", 0))
                mem_used = n.get("mem", 0)
                mem_total = n.get("maxmem", 0)
                mem_pct = (mem_used / mem_total * 100) if mem_total else 0
                uptime_s = n.get("uptime", 0)
                uptime = f"{uptime_s // 86400}d {(uptime_s % 86400) // 3600}h" if uptime_s else "?"
                lines.append(f"Node: **{name}** [{status}]")
                lines.append(f"  CPU: {cpu} | RAM: {_fmt_bytes(mem_used)}/{_fmt_bytes(mem_total)} ({mem_pct:.1f}%)")
                lines.append(f"  Uptime: {uptime}")
                lines.append("")
            return "\n".join(lines).rstrip()
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="proxmox_vms",
        description="Listet alle VMs und LXC Container auf einem Proxmox Node mit Status und Ressourcen.",
        parameters={
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node-Name (leer = erster verfügbarer Node)"},
                "base_url": {"type": "string", "description": "Proxmox URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "API Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def proxmox_vms(node: str = "", base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            # Auto-detect node if not given
            if not node:
                nodes = _api_request(base_url, api_key, "nodes")
                node = nodes[0].get("node", "") if nodes else ""
            if not node:
                return "Fehler: Kein Node gefunden"
            vms = _api_request(base_url, api_key, f"nodes/{node}/qemu")
            lxcs = _api_request(base_url, api_key, f"nodes/{node}/lxc")
            all_guests = [(g, "VM") for g in (vms or [])] + [(g, "LXC") for g in (lxcs or [])]
            if not all_guests:
                return f"Keine VMs/Container auf Node '{node}'"
            all_guests.sort(key=lambda x: x[0].get("vmid", 0))
            lines = [f"**Proxmox Guests auf Node '{node}' ({len(all_guests)}):**", ""]
            for g, gtype in all_guests:
                vmid = g.get("vmid", "?")
                name = g.get("name", "?")
                status = g.get("status", "?")
                cpu = _fmt_pct(g.get("cpu", 0))
                mem = g.get("mem", 0)
                maxmem = g.get("maxmem", 0)
                lines.append(f"  [{gtype} {vmid}] **{name}** — {status}")
                lines.append(f"    CPU: {cpu} | RAM: {_fmt_bytes(mem)}/{_fmt_bytes(maxmem)}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="proxmox_vm_action",
        description="Startet, stoppt oder startet eine Proxmox VM oder einen LXC Container neu.",
        parameters={
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Proxmox Node-Name"},
                "vmid": {"type": "integer", "description": "VM/Container ID"},
                "action": {"type": "string", "description": "Aktion: start, stop, restart, shutdown"},
                "vm_type": {"type": "string", "description": "Typ: qemu (VM, default) oder lxc (Container)"},
                "base_url": {"type": "string", "description": "Proxmox URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "API Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["node", "vmid", "action"],
        },
    )
    def proxmox_vm_action(node: str, vmid: int, action: str, vm_type: str = "qemu",
                          base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        valid_actions = {"start", "stop", "restart", "shutdown"}
        if action not in valid_actions:
            return f"Fehler: Ungültige Aktion '{action}'. Erlaubt: {', '.join(valid_actions)}"
        valid_types = {"qemu", "lxc"}
        if vm_type not in valid_types:
            return f"Fehler: Ungültiger Typ '{vm_type}'. Erlaubt: qemu, lxc"
        try:
            result = _api_request(
                base_url, api_key,
                f"nodes/{node}/{vm_type}/{vmid}/status/{action}",
                method="POST"
            )
            return f"Aktion '{action}' für {vm_type.upper()} {vmid} auf Node '{node}' ausgeführt. Task: {result}"
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="proxmox_resources",
        description="Zeigt eine Cluster-weite Ressourcenübersicht: CPU, RAM und Storage aller Nodes.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "Proxmox URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "API Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def proxmox_resources(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            resources = _api_request(base_url, api_key, "cluster/resources")
            nodes = [r for r in resources if r.get("type") == "node"]
            storages = [r for r in resources if r.get("type") == "storage"]
            vms = [r for r in resources if r.get("type") in ("qemu", "lxc")]
            lines = ["**Proxmox Cluster Ressourcen:**", ""]
            # Nodes summary
            lines.append(f"Nodes: {len(nodes)}")
            total_cpu = sum(r.get("maxcpu", 0) for r in nodes)
            total_mem = sum(r.get("maxmem", 0) for r in nodes)
            used_mem = sum(r.get("mem", 0) for r in nodes)
            lines.append(f"  CPUs gesamt: {total_cpu}")
            lines.append(f"  RAM:         {_fmt_bytes(used_mem)} / {_fmt_bytes(total_mem)}")
            lines.append("")
            # VMs summary
            running = sum(1 for v in vms if v.get("status") == "running")
            lines.append(f"Guests: {len(vms)} total ({running} laufend, {len(vms) - running} gestoppt)")
            lines.append("")
            # Storage
            if storages:
                lines.append("Storage:")
                for s in storages:
                    name = s.get("storage", "?")
                    node = s.get("node", "?")
                    disk = s.get("disk", 0)
                    maxdisk = s.get("maxdisk", 0)
                    pct = (disk / maxdisk * 100) if maxdisk else 0
                    lines.append(f"  {name} ({node}): {_fmt_bytes(disk)}/{_fmt_bytes(maxdisk)} ({pct:.1f}%)")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"
