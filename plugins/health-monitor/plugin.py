"""health-monitor — System-Überwachung mit Schwellwerten."""
import os, time

_history: list[dict] = []

def register(api):
    @api.tool(tool_id="health_check", description="Prüft CPU, RAM, Disk und Load gegen Schwellwerte. Gibt Warnungen aus wenn Grenzwerte überschritten sind.",
        parameters={"type":"object","properties":{"cpu_warn":{"type":"number","description":"CPU Warnung % (default: 80)"},"ram_warn":{"type":"number","description":"RAM Warnung % (default: 85)"},"disk_warn":{"type":"number","description":"Disk Warnung % (default: 90)"}},"required":[]})
    def health_check(cpu_warn:float=80, ram_warn:float=85, disk_warn:float=90, **_) -> str:
        issues = []; info = {}
        # CPU
        try:
            with open("/proc/stat") as f: cpu = f.readline().split()
            total = sum(int(x) for x in cpu[1:]); idle = int(cpu[4])
            pct = round(100*(1-idle/max(total,1)),1); info["cpu"] = pct
            if pct > cpu_warn: issues.append(f"⚠ CPU: {pct}% (>{cpu_warn}%)")
        except: pass
        # RAM
        try:
            mem = {}
            with open("/proc/meminfo") as f:
                for l in f:
                    p = l.split()
                    if len(p)>=2: mem[p[0].rstrip(":")]=int(p[1])
            total=mem.get("MemTotal",0)//1024; avail=mem.get("MemAvailable",0)//1024; used=total-avail
            pct=round(100*used/max(total,1),1); info["ram"]=pct; info["ram_used_mb"]=used; info["ram_total_mb"]=total
            if pct > ram_warn: issues.append(f"⚠ RAM: {pct}% ({used}MB/{total}MB, >{ram_warn}%)")
        except: pass
        # Disk
        try:
            st=os.statvfs("/"); total_gb=round(st.f_blocks*st.f_frsize/(1024**3),1); free_gb=round(st.f_bavail*st.f_frsize/(1024**3),1)
            pct=round(100*(total_gb-free_gb)/max(total_gb,1),1); info["disk"]=pct
            if pct > disk_warn: issues.append(f"⚠ Disk: {pct}% ({free_gb}GB frei, >{disk_warn}%)")
        except: pass
        # Load
        try:
            l1,l5,l15=os.getloadavg(); info["load"]=f"{l1:.1f}/{l5:.1f}/{l15:.1f}"
            cpus=os.cpu_count() or 1
            if l1 > cpus*1.5: issues.append(f"⚠ Load: {l1:.1f} (>{cpus*1.5:.0f}, {cpus} CPUs)")
        except: pass
        _history.append({"ts":int(time.time()),**info})
        if len(_history)>60: _history.pop(0)
        if issues: return "Probleme erkannt:\n"+"\n".join(issues)+f"\n\nDetails: CPU={info.get('cpu','?')}% RAM={info.get('ram','?')}% Disk={info.get('disk','?')}% Load={info.get('load','?')}"
        return f"Alles OK — CPU={info.get('cpu','?')}% RAM={info.get('ram','?')}% Disk={info.get('disk','?')}% Load={info.get('load','?')}"

    @api.tool(tool_id="health_history", description="Zeigt die letzten Health-Check Ergebnisse als Verlauf.",
        parameters={"type":"object","properties":{},"required":[]})
    def health_history(**_) -> str:
        if not _history: return "Noch keine Messungen. Führe erst health_check aus."
        lines=["Zeitpunkt        CPU%  RAM%  Disk% Load"]
        for h in _history[-20:]:
            ts=time.strftime("%H:%M:%S",time.localtime(h["ts"]))
            lines.append(f"{ts}  {h.get('cpu','?'):>5}  {h.get('ram','?'):>5}  {h.get('disk','?'):>5}  {h.get('load','?')}")
        return "\n".join(lines)
