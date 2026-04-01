"""dependency-checker — Veraltete Pakete + Security Audit."""
import subprocess, json

def _run(cmd,cwd="",timeout=60):
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,cwd=cwd or None)
        return r.returncode,r.stdout.strip(),r.stderr.strip()
    except FileNotFoundError: return 1,"",f"{cmd[0]} nicht gefunden"
    except subprocess.TimeoutExpired: return 1,"","Timeout"

def register(api):
    @api.tool(tool_id="check_python_deps", description="Prüft veraltete Python-Pakete (pip). Zeigt aktuelle vs. neueste Version.",
        parameters={"type":"object","properties":{"path":{"type":"string","description":"Verzeichnis mit venv (optional)"}},"required":[]})
    def check_python_deps(path:str="",**_) -> str:
        pip="pip3"
        if path:
            from pathlib import Path
            venv_pip=Path(path)/"venv"/"bin"/"pip"
            if venv_pip.exists(): pip=str(venv_pip)
        rc,out,err=_run([pip,"list","--outdated","--format=json"])
        if rc!=0: return f"Fehler: {err}"
        try:
            pkgs=json.loads(out)
            if not pkgs: return "✅ Alle Python-Pakete sind aktuell"
            lines=[f"{len(pkgs)} veraltete Pakete:",""]
            for p in pkgs[:30]:
                lines.append(f"  {p['name']:30} {p['version']:>10} → {p['latest_version']}")
            if len(pkgs)>30: lines.append(f"  ... und {len(pkgs)-30} weitere")
            return "\n".join(lines)
        except: return out or "Keine Daten"

    @api.tool(tool_id="check_npm_deps", description="Prüft veraltete npm-Pakete. Zeigt aktuelle vs. neueste Version.",
        parameters={"type":"object","properties":{"path":{"type":"string","description":"Verzeichnis mit package.json"}},"required":["path"]})
    def check_npm_deps(path:str, **_) -> str:
        rc,out,err=_run(["npm","outdated","--json"],cwd=path)
        try:
            pkgs=json.loads(out) if out else {}
            if not pkgs: return "✅ Alle npm-Pakete sind aktuell"
            lines=[f"{len(pkgs)} veraltete Pakete:",""]
            for name,info in list(pkgs.items())[:30]:
                lines.append(f"  {name:30} {info.get('current','?'):>10} → {info.get('latest','?')}")
            return "\n".join(lines)
        except: return out[:3000] if out else "Keine package.json oder npm Fehler"

    @api.tool(tool_id="security_audit", description="Prüft pip-Pakete auf bekannte Sicherheitslücken mit pip-audit.",
        parameters={"type":"object","properties":{"path":{"type":"string","description":"Verzeichnis mit venv (optional)"}},"required":[]})
    def security_audit(path:str="",**_) -> str:
        cmd=["pip-audit","--format=json"]
        if path:
            from pathlib import Path
            req=Path(path)/"requirements.txt"
            if req.exists(): cmd+=["-r",str(req)]
        rc,out,err=_run(cmd,timeout=120)
        if "nicht gefunden" in err: return "pip-audit nicht installiert. pip install pip-audit"
        try:
            data=json.loads(out)
            vulns=data.get("dependencies",[])
            issues=[v for v in vulns if v.get("vulns")]
            if not issues: return "✅ Keine bekannten Sicherheitslücken gefunden"
            lines=[f"⚠ {len(issues)} Pakete mit Sicherheitslücken:",""]
            for pkg in issues[:20]:
                for v in pkg.get("vulns",[]):
                    lines.append(f"  {pkg['name']} {pkg.get('version','?')}: {v.get('id','')} — {v.get('description','')[:100]}")
            return "\n".join(lines)
        except: return out[:3000] or err[:1000] or "Keine Daten"
