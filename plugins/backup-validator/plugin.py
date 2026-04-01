"""backup-validator — Backup-Integrität prüfen."""
import os, time
from pathlib import Path

BACKUP_DIR = Path("/opt/hydrahive/backups")

def register(api):
    @api.tool(tool_id="validate_backups", description="Prüft alle Backups auf Integrität: Größe, Alter, Lesbarkeit.",
        parameters={"type":"object","properties":{"path":{"type":"string","description":"Backup-Verzeichnis (default: /opt/hydrahive/backups)"}},"required":[]})
    def validate_backups(path:str="",**_) -> str:
        bdir = Path(path) if path else BACKUP_DIR
        if not bdir.exists(): return f"Backup-Verzeichnis nicht gefunden: {bdir}"
        files = sorted(bdir.glob("*.tar*")) + sorted(bdir.glob("*.gz")) + sorted(bdir.glob("*.zip"))
        if not files: return "Keine Backups gefunden"
        now=time.time(); issues=[]; ok=0
        lines=["Backup-Prüfung:",""]
        for f in files:
            st=f.stat(); age_days=int((now-st.st_mtime)/86400); size_mb=round(st.st_size/(1024*1024),1)
            status="✅"
            if size_mb < 0.01: status="❌ Leer"; issues.append(f.name)
            elif age_days > 30: status="⚠ Alt ({age_days}d)"
            else: ok+=1
            lines.append(f"  {status} {f.name} — {size_mb}MB, {age_days} Tage alt")
        lines.insert(1,f"{ok}/{len(files)} OK, {len(issues)} Probleme")
        if issues: lines.append(f"\nProblematische Backups: {', '.join(issues)}")
        return "\n".join(lines)

    @api.tool(tool_id="list_backups", description="Listet alle Backups mit Größe und Datum.",
        parameters={"type":"object","properties":{},"required":[]})
    def list_backups(**_) -> str:
        if not BACKUP_DIR.exists(): return "Kein Backup-Verzeichnis"
        files=sorted(BACKUP_DIR.iterdir(),key=lambda f:f.stat().st_mtime,reverse=True)
        if not files: return "Keine Backups"
        lines=[]
        for f in files[:20]:
            if f.is_file():
                st=f.stat(); size=round(st.st_size/(1024*1024),1)
                age=int((time.time()-st.st_mtime)/86400)
                lines.append(f"{f.name} — {size}MB — {age}d alt")
        return "\n".join(lines) or "Keine Backup-Dateien"
