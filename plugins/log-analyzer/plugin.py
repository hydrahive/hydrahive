"""log-analyzer — Journalctl parsen und Fehler-Patterns erkennen."""
import subprocess, re
from collections import Counter

def _journal(unit:str="hydrahive-core",lines:int=500,since:str="",priority:str="") -> list[str]:
    cmd=["journalctl","-u",unit,"--no-pager","-n",str(min(lines,5000))]
    if since: cmd+=["--since",since]
    if priority: cmd+=["-p",priority]
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        return r.stdout.strip().splitlines() if r.returncode==0 else []
    except: return []

def register(api):
    @api.tool(tool_id="analyze_logs", description="Analysiert systemd Journal-Logs eines Services. Findet Fehler, Warnungen und wiederkehrende Muster.",
        parameters={"type":"object","properties":{"unit":{"type":"string","description":"Service-Name (default: hydrahive-core)"},"since":{"type":"string","description":"Zeitraum z.B. '1h', '30m', 'today' (default: letzte 500 Zeilen)"},"lines":{"type":"integer","description":"Max Zeilen (default: 500)"}},"required":[]})
    def analyze_logs(unit:str="hydrahive-core",since:str="",lines:int=500,**_) -> str:
        all_lines=_journal(unit,lines,since)
        if not all_lines: return f"Keine Logs für {unit} gefunden"
        errors=[l for l in all_lines if any(w in l.lower() for w in ["error","fehler","exception","traceback","critical","fatal"])]
        warnings=[l for l in all_lines if any(w in l.lower() for w in ["warn","warning"])]
        # Pattern-Erkennung
        error_patterns=Counter()
        for e in errors:
            clean=re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^ ]*','',e)
            clean=re.sub(r'0x[0-9a-f]+','0x...',clean)
            clean=re.sub(r'\d+','N',clean)[:120]
            error_patterns[clean.strip()]+=1
        result=[f"Service: {unit}",f"Zeilen analysiert: {len(all_lines)}",f"Fehler: {len(errors)}",f"Warnungen: {len(warnings)}",""]
        if error_patterns:
            result.append("Top Fehler-Patterns:")
            for pattern,count in error_patterns.most_common(10):
                result.append(f"  [{count}x] {pattern}")
        if errors:
            result.append(f"\nLetzte {min(5,len(errors))} Fehler:")
            for e in errors[-5:]: result.append(f"  {e[-150:]}")
        return "\n".join(result)

    @api.tool(tool_id="error_summary", description="Kompakte Zusammenfassung aller Fehler der letzten Stunde.",
        parameters={"type":"object","properties":{"unit":{"type":"string","description":"Service-Name (default: hydrahive-core)"}},"required":[]})
    def error_summary(unit:str="hydrahive-core",**_) -> str:
        lines=_journal(unit,1000,"1 hour ago","err")
        if not lines: return f"Keine Fehler in der letzten Stunde für {unit}"
        return f"{len(lines)} Fehler in der letzten Stunde:\n\n"+"\n".join(lines[-20:])
