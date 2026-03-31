"""
system-info Plugin — Stellt System-Monitoring als Agent-Tools bereit.

Tools:
  - system_overview: CPU, RAM, Disk, Uptime auf einen Blick
  - disk_usage: Detaillierte Disk-Nutzung pro Mountpoint
  - top_processes: Die ressourcenhungrigsten Prozesse
"""
import subprocess


def register(api):
    """Plugin-Registrierung — wird vom PluginManager aufgerufen."""

    @api.tool(
        tool_id="system_overview",
        description="Gibt eine kompakte System-Übersicht zurück: CPU-Auslastung, RAM, Disk, Uptime, Load Average. Nutze dieses Tool wenn der User nach dem Systemstatus fragt.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def system_overview(**_) -> str:
        import os
        lines = []

        # Uptime
        try:
            with open("/proc/uptime") as f:
                secs = float(f.read().split()[0])
            days, rem = divmod(int(secs), 86400)
            hours, rem = divmod(rem, 3600)
            mins = rem // 60
            lines.append(f"Uptime: {days}d {hours}h {mins}m")
        except Exception:
            pass

        # Load
        try:
            load1, load5, load15 = os.getloadavg()
            lines.append(f"Load: {load1:.2f} / {load5:.2f} / {load15:.2f}")
        except Exception:
            pass

        # CPU
        try:
            with open("/proc/stat") as f:
                cpu = f.readline().split()
            total = sum(int(x) for x in cpu[1:])
            idle = int(cpu[4])
            usage = 100 * (1 - idle / max(total, 1))
            lines.append(f"CPU: {usage:.1f}%")
        except Exception:
            pass

        # RAM
        try:
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem[parts[0].rstrip(":")] = int(parts[1])
            total_mb = mem.get("MemTotal", 0) // 1024
            avail_mb = mem.get("MemAvailable", 0) // 1024
            used_mb = total_mb - avail_mb
            pct = 100 * used_mb / max(total_mb, 1)
            lines.append(f"RAM: {used_mb}MB / {total_mb}MB ({pct:.1f}%)")
        except Exception:
            pass

        # Disk
        try:
            st = os.statvfs("/")
            total_gb = st.f_blocks * st.f_frsize / (1024**3)
            free_gb = st.f_bavail * st.f_frsize / (1024**3)
            used_gb = total_gb - free_gb
            pct = 100 * used_gb / max(total_gb, 1)
            lines.append(f"Disk /: {used_gb:.1f}GB / {total_gb:.1f}GB ({pct:.1f}%)")
        except Exception:
            pass

        return "\n".join(lines) or "Keine Daten verfügbar"

    @api.tool(
        tool_id="disk_usage",
        description="Zeigt detaillierte Disk-Nutzung pro Mountpoint (df -h). Nutze dieses Tool wenn der User nach Speicherplatz fragt.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def disk_usage(**_) -> str:
        try:
            result = subprocess.run(
                ["df", "-h", "--output=target,size,used,avail,pcent", "-x", "tmpfs", "-x", "devtmpfs"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() or "Keine Daten"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="top_processes",
        description="Zeigt die Top-10 Prozesse nach CPU oder RAM. Nutze dieses Tool wenn der User nach ressourcenhungrigen Prozessen fragt.",
        parameters={
            "type": "object",
            "properties": {
                "sort_by": {
                    "type": "string",
                    "enum": ["cpu", "mem"],
                    "description": "Sortierung: cpu oder mem",
                },
                "limit": {
                    "type": "integer",
                    "description": "Anzahl Prozesse (default: 10)",
                },
            },
            "required": [],
        },
    )
    def top_processes(sort_by: str = "cpu", limit: int = 10, **_) -> str:
        sort_key = "-%cpu" if sort_by == "cpu" else "-%mem"
        try:
            result = subprocess.run(
                ["ps", "aux", "--sort", sort_key],
                capture_output=True, text=True, timeout=10,
            )
            lines = result.stdout.strip().splitlines()
            return "\n".join(lines[:limit + 1])  # +1 für Header
        except Exception as e:
            return f"Fehler: {e}"

    @api.hook("message.after")
    async def log_plugin_usage(project_id="", response="", **_):
        """Logging-Hook: zählt Plugin-Nutzung (Beispiel für Hook-System)."""
        # In einem echten Plugin würde hier z.B. ein Counter hochgezählt
        pass
