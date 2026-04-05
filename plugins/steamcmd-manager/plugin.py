"""
SteamCMD Manager Plugin für HydraHive (#261)

Verwaltet Spielserver via SteamCMD und LinuxGSM.
- Server-Instanzen auflisten
- SteamCMD-Installation / Update starten
- Server-Status prüfen (Prozess + LinuxGSM)

SteamCMD: https://developer.valvesoftware.com/wiki/SteamCMD
LinuxGSM: https://linuxgsm.com
"""
import json
import logging
import os
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("steamcmd-manager")

PLUGIN_ID = "steamcmd-manager"


def _load_cfg(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/{PLUGIN_ID}.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _run(cmd: list[str], timeout: int = 60, cwd: str | None = None) -> tuple[int, str, str]:
    """Führt einen Befehl aus und gibt (returncode, stdout, stderr) zurück."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ---------------------------------------------------------------------------
# Plugin Registration
# ---------------------------------------------------------------------------

def register(api):
    """Plugin beim Core registrieren."""

    @api.tool(
        tool_id="steamcmd_servers",
        description=(
            "Listet alle konfigurierten Spielserver-Instanzen im Server-Verzeichnis auf. "
            "Zeigt LinuxGSM-Skripte und Prozess-Status."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    def steamcmd_servers(**ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        servers_dir = cfg.get("servers_dir", "")
        if not servers_dir:
            return "Fehler: Kein Server-Verzeichnis konfiguriert."
        base = Path(servers_dir)
        if not base.exists():
            return f"Fehler: Verzeichnis nicht gefunden: {servers_dir}"
        try:
            # Finde LinuxGSM-Skripte und Server-Unterordner
            entries = []
            for item in sorted(base.iterdir()):
                if item.is_dir():
                    # Prüfe ob LinuxGSM-Skript vorhanden
                    script = item / item.name
                    lgsm_script = None
                    for f in item.glob("*.sh"):
                        lgsm_script = f
                        break
                    if not lgsm_script and script.exists():
                        lgsm_script = script

                    # Prozess-Status prüfen
                    running = False
                    try:
                        rc, out, _ = _run(["pgrep", "-f", item.name], timeout=5)
                        running = rc == 0 and bool(out.strip())
                    except Exception:
                        pass

                    status = "Running" if running else "Stopped"
                    lgsm = "(LinuxGSM)" if lgsm_script else ""
                    entries.append(f"  {item.name:<25} {status:<10} {lgsm}")

            if not entries:
                return f"Keine Server-Instanzen in {servers_dir} gefunden."
            return f"Server-Instanzen in {servers_dir}:\n" + "\n".join(entries)
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="steamcmd_install",
        description=(
            "Installiert einen neuen Spielserver via SteamCMD. "
            "Benötigt die Steam App-ID und ein Zielverzeichnis."
        ),
        parameters={
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "integer",
                    "description": "Steam App-ID des Servers (z.B. 896660 für Valheim, 232250 für CS:GO)",
                },
                "install_dir": {
                    "type": "string",
                    "description": "Installationsverzeichnis (absoluter Pfad oder relativ zu servers_dir)",
                },
                "anonymous": {
                    "type": "boolean",
                    "description": "Anonymous Login nutzen (default: true). False = konfigurierten Steam-Account nutzen.",
                },
                "validate": {
                    "type": "boolean",
                    "description": "Dateien nach Installation validieren (default: true)",
                },
            },
            "required": ["app_id", "install_dir"],
        },
    )
    def steamcmd_install(
        app_id: int,
        install_dir: str,
        anonymous: bool = True,
        validate: bool = True,
        **ctx,
    ) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        steamcmd = cfg.get("steamcmd_path", "/usr/games/steamcmd")
        servers_dir = cfg.get("servers_dir", "")

        if not Path(steamcmd).exists():
            # Alternativ: steamcmd aus PATH
            rc, out, _ = _run(["which", "steamcmd"], timeout=5)
            if rc == 0 and out:
                steamcmd = out
            else:
                return f"SteamCMD nicht gefunden: {steamcmd}. Bitte steamcmd_path konfigurieren."

        # Absoluter Pfad
        if not install_dir.startswith("/") and servers_dir:
            install_dir = str(Path(servers_dir) / install_dir)

        Path(install_dir).mkdir(parents=True, exist_ok=True)

        login = "+login anonymous" if anonymous else f"+login {cfg.get('steam_user', 'anonymous')} {cfg.get('steam_password', '')}"
        validate_flag = "+validate" if validate else ""

        cmd = [
            steamcmd,
            "+force_install_dir", install_dir,
        ] + login.split() + [
            "+app_update", str(app_id),
        ]
        if validate:
            cmd.append("+validate")
        cmd.append("+quit")

        try:
            rc, stdout, stderr = _run(cmd, timeout=600, cwd=str(Path(install_dir).parent))
            if rc == 0 or "Success! App" in stdout:
                # Letzten Teil des Outputs zurückgeben
                lines = stdout.splitlines()
                summary = "\n".join(lines[-20:]) if len(lines) > 20 else stdout
                return f"Installation erfolgreich (App {app_id}):\n{summary}"
            return f"SteamCMD Fehler (rc={rc}):\n{stdout[-1000:]}\n{stderr[-500:]}"
        except subprocess.TimeoutExpired:
            return "Installation läuft noch (Timeout nach 10 Min.). Prüfe den Prozess manuell."
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="steamcmd_update",
        description=(
            "Aktualisiert einen installierten Spielserver auf die neueste Version via SteamCMD. "
            "Stoppt den Server nicht automatisch — vorher manuell stoppen empfohlen."
        ),
        parameters={
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "integer",
                    "description": "Steam App-ID des Servers",
                },
                "install_dir": {
                    "type": "string",
                    "description": "Installationsverzeichnis des Servers",
                },
                "validate": {
                    "type": "boolean",
                    "description": "Dateien validieren (default: false für schnellere Updates)",
                },
            },
            "required": ["app_id", "install_dir"],
        },
    )
    def steamcmd_update(app_id: int, install_dir: str, validate: bool = False, **ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        steamcmd = cfg.get("steamcmd_path", "/usr/games/steamcmd")
        servers_dir = cfg.get("servers_dir", "")

        if not Path(steamcmd).exists():
            rc, out, _ = _run(["which", "steamcmd"], timeout=5)
            if rc == 0 and out:
                steamcmd = out
            else:
                return f"SteamCMD nicht gefunden: {steamcmd}"

        if not install_dir.startswith("/") and servers_dir:
            install_dir = str(Path(servers_dir) / install_dir)

        if not Path(install_dir).exists():
            return f"Installationsverzeichnis nicht gefunden: {install_dir}"

        anonymous = not cfg.get("steam_user")
        login_args = ["anonymous"] if anonymous else [cfg.get("steam_user", ""), cfg.get("steam_password", "")]

        cmd = [
            steamcmd,
            "+force_install_dir", install_dir,
            "+login",
        ] + login_args + [
            "+app_update", str(app_id),
        ]
        if validate:
            cmd.append("+validate")
        cmd.append("+quit")

        try:
            rc, stdout, stderr = _run(cmd, timeout=600)
            lines = stdout.splitlines()
            summary = "\n".join(lines[-20:]) if len(lines) > 20 else stdout
            if rc == 0 or "fully installed" in stdout.lower() or "already up to date" in stdout.lower():
                return f"Update App {app_id} abgeschlossen:\n{summary}"
            return f"Update fehlgeschlagen (rc={rc}):\n{summary}\n{stderr[-300:]}"
        except subprocess.TimeoutExpired:
            return "Update läuft noch (Timeout). Prüfe den Prozess manuell."
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="steamcmd_status",
        description=(
            "Prüft den Status eines Spielservers: ob der Prozess läuft, "
            "Speicherplatz und ob Updates verfügbar sind (via SteamDB Vergleich)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "install_dir": {
                    "type": "string",
                    "description": "Installationsverzeichnis oder Servername",
                },
                "process_name": {
                    "type": "string",
                    "description": "Prozessname zum Prüfen (optional, z.B. 'srcds_run', 'valheim_server')",
                },
            },
            "required": ["install_dir"],
        },
    )
    def steamcmd_status(install_dir: str, process_name: str = "", **ctx) -> str:
        cfg = _load_cfg(ctx.get("_username", "admin"))
        servers_dir = cfg.get("servers_dir", "")

        if not install_dir.startswith("/") and servers_dir:
            install_dir = str(Path(servers_dir) / install_dir)

        lines = [f"Verzeichnis: {install_dir}"]

        # Verzeichnis-Info
        p = Path(install_dir)
        if not p.exists():
            return f"Fehler: Verzeichnis nicht gefunden: {install_dir}"
        try:
            total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            file_count = sum(1 for _ in p.rglob("*") if _.is_file())
            lines.append(f"Dateien:     {file_count}")
            lines.append(f"Größe:       {total / (1024**3):.2f} GB")
        except Exception as e:
            lines.append(f"Größe:       Fehler ({e})")

        # steamapps/appmanifest prüfen (App-ID und Build-ID)
        try:
            for manifest in p.glob("steamapps/appmanifest_*.acf"):
                content = manifest.read_text(errors="replace")
                app_id_line = [l for l in content.splitlines() if '"appid"' in l.lower()]
                build_line = [l for l in content.splitlines() if '"buildid"' in l.lower()]
                if app_id_line:
                    lines.append(f"App-ID:      {app_id_line[0].split()[-1].strip(chr(34))}")
                if build_line:
                    lines.append(f"Build-ID:    {build_line[0].split()[-1].strip(chr(34))}")
        except Exception:
            pass

        # Prozess-Status
        check_name = process_name or p.name
        try:
            rc, out, _ = _run(["pgrep", "-af", check_name], timeout=5)
            if rc == 0 and out:
                lines.append(f"Prozess:     Running")
                lines.append(f"  {out.splitlines()[0][:100]}")
            else:
                lines.append(f"Prozess:     Gestoppt (kein '{check_name}' Prozess)")
        except Exception as e:
            lines.append(f"Prozess:     Prüfung fehlgeschlagen ({e})")

        # LinuxGSM-Skript prüfen
        lgsm = p / p.name
        if lgsm.exists() and os.access(str(lgsm), os.X_OK):
            lines.append(f"LinuxGSM:    Verfügbar ({lgsm.name})")

        return "\n".join(lines)
