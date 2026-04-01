"""test-runner — Tests ausführen als Agent-Tool."""
import subprocess

def _run(cmd,cwd="",timeout=120):
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,cwd=cwd or None)
        return r.returncode,r.stdout.strip(),r.stderr.strip()
    except FileNotFoundError: return 1,"",f"{cmd[0]} nicht gefunden"
    except subprocess.TimeoutExpired: return 1,"","Timeout"

def register(api):
    @api.tool(tool_id="run_tests", description="Führt Tests aus — erkennt automatisch pytest, npm test oder unittest. Gibt strukturierte Ergebnisse zurück.",
        parameters={"type":"object","properties":{"path":{"type":"string","description":"Projektverzeichnis"},"framework":{"type":"string","enum":["auto","pytest","npm","unittest"],"description":"Test-Framework (default: auto)"},"verbose":{"type":"boolean","description":"Ausführliche Ausgabe (default: false)"}},"required":["path"]})
    def run_tests(path:str, framework:str="auto", verbose:bool=False, **_) -> str:
        from pathlib import Path
        p=Path(path)
        if not p.exists(): return f"Nicht gefunden: {path}"
        results=[]
        if framework in ("auto","pytest"):
            cmd=["python3","-m","pytest",str(p),"-v" if verbose else "-q","--tb=short","--no-header"]
            rc,out,err=_run(cmd,cwd=str(p))
            if rc!=1 or "no tests ran" not in (out+err).lower():
                passed=out.count(" PASSED"); failed=out.count(" FAILED"); errors=out.count(" ERROR")
                return f"pytest: {passed} passed, {failed} failed, {errors} errors\n\n{out[-3000:]}"
        if framework in ("auto","npm"):
            if (p/"package.json").exists():
                rc,out,err=_run(["npm","test","--","--passWithNoTests"],cwd=str(p),timeout=180)
                return f"npm test (exit {rc}):\n\n{out[-3000:]}\n{err[-1000:]}"
        if framework in ("auto","unittest"):
            rc,out,err=_run(["python3","-m","unittest","discover","-s",str(p),"-v" if verbose else "-q"])
            if "Ran " in (out+err):
                return f"unittest:\n\n{out}\n{err}"
        return "Keine Tests gefunden. Unterstützt: pytest, npm test, unittest"

    @api.tool(tool_id="test_file", description="Führt Tests für eine einzelne Datei aus.",
        parameters={"type":"object","properties":{"path":{"type":"string","description":"Test-Datei (z.B. tests/test_main.py)"}},"required":["path"]})
    def test_file(path:str, **_) -> str:
        from pathlib import Path
        p=Path(path)
        if not p.exists(): return f"Nicht gefunden: {path}"
        if p.suffix==".py":
            rc,out,err=_run(["python3","-m","pytest",str(p),"-v","--tb=short"])
            return f"pytest {p.name} (exit {rc}):\n\n{out[-3000:]}"
        return "Nur .py Test-Dateien unterstützt"
