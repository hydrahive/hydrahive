"""code-linter — Deterministisches Linting als Agent-Tool."""
import subprocess
from pathlib import Path

def _run(cmd,cwd="",timeout=30):
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,cwd=cwd or None)
        return r.returncode,r.stdout.strip(),r.stderr.strip()
    except FileNotFoundError: return 1,"",f"{cmd[0]} nicht installiert"
    except subprocess.TimeoutExpired: return 1,"","Timeout"

def register(api):
    @api.tool(tool_id="lint_python", description="Python-Code linten mit ruff oder flake8. Findet Style-Probleme, unused imports, Syntax-Fehler.",
        parameters={"type":"object","properties":{"path":{"type":"string","description":"Datei oder Verzeichnis"},"fix":{"type":"boolean","description":"Auto-Fix versuchen (default: false)"}},"required":["path"]})
    def lint_python(path:str, fix:bool=False, **_) -> str:
        p=Path(path)
        if not p.exists(): return f"Nicht gefunden: {path}"
        # ruff bevorzugt
        cmd=["ruff","check",str(p)]
        if fix: cmd.append("--fix")
        rc,out,err=_run(cmd)
        if "nicht installiert" in err:
            cmd=["python3","-m","flake8","--max-line-length=120",str(p)]
            rc,out,err=_run(cmd)
            if "nicht installiert" in err:
                # Fallback: py_compile
                if p.is_file() and p.suffix==".py":
                    rc2,out2,err2=_run(["python3","-m","py_compile",str(p)])
                    return f"Syntax-Check: {'OK' if rc2==0 else err2}"
                return "Weder ruff noch flake8 installiert. pip install ruff"
        if rc==0: return f"✅ Keine Probleme gefunden in {path}"
        return out or err

    @api.tool(tool_id="lint_js", description="JavaScript/TypeScript linten mit eslint.",
        parameters={"type":"object","properties":{"path":{"type":"string","description":"Datei oder Verzeichnis"},"fix":{"type":"boolean","description":"Auto-Fix (default: false)"}},"required":["path"]})
    def lint_js(path:str, fix:bool=False, **_) -> str:
        p=Path(path)
        if not p.exists(): return f"Nicht gefunden: {path}"
        cmd=["npx","eslint",str(p)]
        if fix: cmd.append("--fix")
        rc,out,err=_run(cmd,timeout=60)
        if rc==0: return f"✅ Keine Probleme in {path}"
        return out or err or "eslint nicht verfügbar"

    @api.tool(tool_id="syntax_check", description="Schneller Syntax-Check für Python-Dateien (py_compile).",
        parameters={"type":"object","properties":{"path":{"type":"string","description":"Python-Datei"}},"required":["path"]})
    def syntax_check(path:str, **_) -> str:
        p=Path(path)
        if not p.exists(): return f"Nicht gefunden: {path}"
        if not p.suffix==".py": return "Nur .py Dateien"
        rc,out,err=_run(["python3","-m","py_compile",str(p)])
        return f"✅ Syntax OK: {path}" if rc==0 else f"❌ Syntax-Fehler:\n{err}"
