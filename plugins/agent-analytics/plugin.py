"""agent-analytics — Token-Verbrauch und Kosten-Analyse."""
import json, urllib.request

def _api(path):
    try:
        req=urllib.request.Request(f"http://127.0.0.1:8765{path}",headers={"Authorization":"Bearer internal"})
        with urllib.request.urlopen(req,timeout=10) as r:
            return json.loads(r.read().decode())
    except: return None

def register(api):
    @api.tool(tool_id="usage_report", description="Zeigt Token-Verbrauch pro Agent und Modell. Input, Output, Cache-Hits, geschätzte Kosten.",
        parameters={"type":"object","properties":{"agent_id":{"type":"string","description":"Agent-ID (optional, leer=alle)"}},"required":[]})
    def usage_report(agent_id:str="",**_) -> str:
        # Usage-Daten aus /admin/resources lesen
        import subprocess
        try:
            r=subprocess.run(["journalctl","-u","hydrahive-core","--since","24 hours ago","--no-pager","-g","token-budget"],
                capture_output=True,text=True,timeout=15)
            lines=r.stdout.strip().splitlines()
            if agent_id:
                lines=[l for l in lines if agent_id in l]
            # Token-Zahlen extrahieren
            import re
            agents={}
            for l in lines:
                m=re.search(r'proj=(\S+)\s+sys≈(\d+)\s+hist≈(\d+).*total≈(\d+)',l)
                if m:
                    aid=m.group(1)
                    agents.setdefault(aid,{"calls":0,"total_tokens":0})
                    agents[aid]["calls"]+=1
                    agents[aid]["total_tokens"]+=int(m.group(4))
            if not agents: return "Keine Token-Daten in den letzten 24h"
            result=["Token-Verbrauch (letzte 24h):",""]
            for aid,data in sorted(agents.items(),key=lambda x:-x[1]["total_tokens"]):
                cost_est=round(data["total_tokens"]*0.000003,2)  # ~$3/1M tokens Schätzung
                result.append(f"  {aid:30} {data['calls']:>4} Calls  ~{data['total_tokens']:>8} Tokens  ~${cost_est:.2f}")
            return "\n".join(result)
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(tool_id="cost_estimate", description="Schätzt die monatlichen Kosten basierend auf dem aktuellen Verbrauch.",
        parameters={"type":"object","properties":{},"required":[]})
    def cost_estimate(**_) -> str:
        import subprocess,re
        try:
            r=subprocess.run(["journalctl","-u","hydrahive-core","--since","1 hour ago","--no-pager","-g","token-budget"],
                capture_output=True,text=True,timeout=15)
            totals=[]
            for l in r.stdout.strip().splitlines():
                m=re.search(r'total≈(\d+)',l)
                if m: totals.append(int(m.group(1)))
            if not totals: return "Nicht genug Daten für eine Schätzung (min. 1 Stunde)"
            hourly=sum(totals)
            daily=hourly*24; monthly=daily*30
            cost_monthly=round(monthly*0.000003,2)
            return f"Verbrauch letzte Stunde: ~{hourly:,} Tokens\nHochrechnung pro Tag: ~{daily:,} Tokens\nHochrechnung pro Monat: ~{monthly:,} Tokens\n\nGeschätzte Kosten/Monat: ~${cost_monthly:.2f}\n(Basis: ~$3/1M Tokens, Durchschnitt)"
        except Exception as e:
            return f"Fehler: {e}"
