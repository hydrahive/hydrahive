"""
router_doctor.py — HydraHive Doctor: Systemdiagnose (#new)

GET /admin/doctor  → führt alle Checks durch und gibt strukturierten Report zurück
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends

logger = logging.getLogger(__name__)


def _find_install_dir() -> Path:
    for candidate in [Path("/opt/hydrahive"), Path("/opt/hydrahive")]:
        if candidate.exists():
            return candidate
    return Path("/opt/hydrahive")


def register_doctor_routes(admin_router: APIRouter, *, require_admin) -> None:

    @admin_router.get("/admin/tests")
    async def run_tests(_a=Depends(require_admin)):
        install_dir = _find_install_dir()
        pytest_bin  = install_dir / "venv" / "bin" / "pytest"
        tests_dir   = install_dir / "core" / "tests"

        if not tests_dir.exists():
            return {"status": "error", "passed": 0, "failed": 0, "total": 0,
                    "duration": 0.0, "output": f"Tests-Verzeichnis nicht gefunden: {tests_dir}"}

        cmd = [str(pytest_bin), str(tests_dir), "-q", "--tb=short", "--no-header"]
        try:
            r = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=120),
            )
        except subprocess.TimeoutExpired:
            return {"status": "error", "passed": 0, "failed": 0, "total": 0,
                    "duration": 0.0, "output": "Timeout — Tests liefen länger als 120s"}
        except Exception as e:
            return {"status": "error", "passed": 0, "failed": 0, "total": 0,
                    "duration": 0.0, "output": str(e)}

        output = (r.stdout or "") + (r.stderr or "")

        # Letzte Zeile parsen: "22 passed in 0.14s" oder "1 failed, 21 passed in 0.15s"
        import re
        passed = failed = total = 0
        duration = 0.0
        for line in reversed(output.splitlines()):
            m = re.search(
                r"(?:(\d+) failed,?\s*)?(\d+) passed(?:,\s*\d+ \w+)* in ([\d.]+)s",
                line,
            )
            if m:
                failed   = int(m.group(1) or 0)
                passed   = int(m.group(2))
                duration = float(m.group(3))
                total    = passed + failed
                break

        status = "ok" if r.returncode == 0 else "error"
        return {"status": status, "passed": passed, "failed": failed,
                "total": total, "duration": duration, "output": output}

    @admin_router.post("/admin/doctor/fix/{fix_id}")
    async def run_fix(fix_id: str, _a=Depends(require_admin)):
        from fastapi import HTTPException as _HTTP
        if fix_id in ("nginx_a2a", "nginx_projects", "nginx_upload"):
            return await _fix_nginx()
        if fix_id == "install_chromium":
            return await _fix_install_chromium()
        if fix_id == "repair_whatsapp":
            return await _fix_repair_whatsapp()
        if fix_id == "repair_agentlink":
            return await _fix_repair_agentlink()
        if fix_id == "repair_samba":
            return await _fix_samba_permissions()
        raise _HTTP(400, f"Unbekannter Fix: {fix_id}")

    @admin_router.get("/admin/doctor")
    async def run_doctor(_a=Depends(require_admin)):
        checks = []
        checks += await _check_services()
        checks += _check_configs()
        checks += _check_nginx_a2a()
        checks += _check_nginx_projects()
        checks += _check_ports()
        checks += await _check_agentlink()
        checks += _check_disk()
        checks += await _check_api()
        checks += _check_llm_config()
        checks += _check_nginx_upload_limit()
        checks += _check_samba()
        checks += _check_vpn()

        total = len(checks)
        errors = sum(1 for c in checks if c["status"] == "error")
        warnings = sum(1 for c in checks if c["status"] == "warn")

        return {
            "status": "error" if errors else ("warn" if warnings else "ok"),
            "summary": {"total": total, "ok": total - errors - warnings, "warn": warnings, "error": errors},
            "checks": checks,
        }


def _check(name: str, status: str, detail: str, hint: str = "", fix: str = "") -> dict:
    result: dict = {"name": name, "status": status, "detail": detail, "hint": hint}
    if fix:
        result["fix"] = fix
    return result


async def _check_services() -> list[dict]:
    """Prüft systemd-Services."""
    results = []
    required = [
        (["hydrahive-core"],         "HydraHive Core"),
        (["nginx"],                  "Nginx Reverse Proxy"),
        (["gitea"],                  "Gitea"),
        (["hydrahive-conduwuit"],    "Matrix (conduwuit)"),
        (["hydrahive-amem"],         "A-MEM MCP"),
        (["hydrahive-agentlink"],    "AgentLink Hub"),
        (["redis-server", "redis"],  "Redis"),
        (["postgresql"],             "PostgreSQL"),
    ]
    optional = [
        (["hydrahive-whatsapp-bridge"], "WhatsApp Bridge", "repair_whatsapp"),
        (["tailscaled"],               "Tailscale VPN",    ""),
        (["hydrahive-codeserver"],     "Code Editor (code-server)", ""),
    ]
    for units, label in required:
        found_active = False
        for unit in units:
            try:
                r = subprocess.run(
                    ["systemctl", "is-active", unit],
                    capture_output=True, text=True, timeout=5,
                )
                if r.stdout.strip() == "active":
                    results.append(_check(f"Service: {label}", "ok", f"aktiv ({unit})"))
                    found_active = True
                    break
            except Exception:
                continue
        if not found_active:
            results.append(_check(
                f"Service: {label}", "error", "nicht aktiv",
                f"sudo systemctl start {units[0]}",
            ))
    for units, label, fix_id in optional:
        found_active = False
        for unit in units:
            try:
                r = subprocess.run(
                    ["systemctl", "is-active", unit],
                    capture_output=True, text=True, timeout=5,
                )
                if r.stdout.strip() == "active":
                    results.append(_check(f"Service: {label}", "ok", f"aktiv ({unit})"))
                    found_active = True
                    break
            except Exception:
                continue
        if not found_active:
            results.append(_check(
                f"Service: {label}", "warn", "nicht aktiv",
                f"sudo systemctl start {units[0]}",
                fix=fix_id,
            ))
    return results


def _check_configs() -> list[dict]:
    """Prüft wichtige Konfigurationsdateien."""
    results = []
    configs = [
        ([Path("/etc/hydrahive/llm_config.json"), Path("/etc/hydrahive/llm_config.json")],                       "LLM-Konfiguration"),
        ([Path("/etc/hydrahive/users.json"), Path("/etc/hydrahive/users.json")],                                 "Benutzer-Datenbank"),
        ([Path("/etc/nginx/sites-enabled/hydrahive-console"), Path("/etc/nginx/sites-enabled/hydrahive-console")], "Nginx-Konfiguration"),
        ([Path("/opt/hydrahive/venv"), Path("/opt/hydrahive/venv")],                                             "Python-Virtualenv"),
    ]
    for paths, label in configs:
        existing = next((p for p in paths if p.exists()), None)
        results.append(_check(
            f"Config: {label}",
            "ok" if existing else "error",
            str(existing) if existing else f"Nicht gefunden: {paths[0]}",
            "" if existing else "Installer erneut ausführen",
        ))

    # VPN-Konfig optional
    vpn = Path("/etc/hydrahive/vpn.json")
    if vpn.exists():
        try:
            data = json.loads(vpn.read_text())
            results.append(_check("Config: VPN", "ok", f"Modus: {data.get('mode','unbekannt')}"))
        except Exception:
            results.append(_check("Config: VPN", "warn", "vpn.json nicht parsebar"))

    return results


def _check_ports() -> list[dict]:
    """Prüft ob wichtige Ports erreichbar sind."""
    import socket
    results = []
    # (port, label, required)
    ports = [
        (8765, "Core API",              True),
        (80,   "HTTP/nginx",            True),
        (3002, "Gitea",                 True),
        (6167, "Matrix (conduwuit)",    True),
        (8766, "Code Editor (code-server)", False),
    ]
    for port, label, required in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                results.append(_check(f"Port {port}: {label}", "ok", "erreichbar"))
        except Exception:
            severity = "error" if required else "warn"
            results.append(_check(f"Port {port}: {label}", severity, "nicht erreichbar",
                                  f"Prüfe: sudo ss -tlnp | grep {port}"))
    return results


def _check_disk() -> list[dict]:
    """Prüft Festplattenplatz."""
    results = []
    try:
        import shutil
        # Prüfe hydrahive zuerst, dann hydrahive als Fallback
        check_path = "/opt/hydrahive"
        for candidate in ["/opt/hydrahive", "/opt/hydrahive"]:
            if Path(candidate).exists():
                check_path = candidate
                break
        total, used, free = shutil.disk_usage(check_path)
        free_gb = free / (1024 ** 3)
        used_pct = int(used / total * 100)
        status = "ok" if free_gb > 2 else ("warn" if free_gb > 0.5 else "error")
        results.append(_check(
            f"Festplatte: {check_path}",
            status,
            f"{free_gb:.1f} GB frei ({used_pct}% belegt)",
            "" if status == "ok" else "Speicherplatz freigeben",
        ))
    except Exception as e:
        results.append(_check("Festplatte", "warn", str(e)))
    return results


async def _check_api() -> list[dict]:
    """Prüft Deployment-Status (kein Self-Ping um Deadlock zu vermeiden)."""
    results = []
    # Hinweis: kein HTTP-Self-Call — API antwortet ja gerade auf diese Anfrage
    results.append(_check("API: /health", "ok", "läuft (antwortet auf diese Anfrage)"))

    # Update-Status — beide Pfade prüfen
    status_file = None
    for sf in [Path("/var/run/hydrahive-update.json"), Path("/var/run/hydrahive-update.json")]:
        if sf.exists():
            status_file = sf
            break
    if status_file is not None:
        try:
            data = json.loads(status_file.read_text())
            commit = data.get("commit", "?")
            results.append(_check("Deployment: Commit-Stand", "ok", f"Letzter Commit: {commit}"))
        except Exception:
            results.append(_check("Deployment: Commit-Stand", "warn", "Status nicht lesbar"))
    else:
        results.append(_check("Deployment: Commit-Stand", "warn", "Noch nie deployed"))

    return results


def _check_nginx_a2a() -> list[dict]:
    """Prüft ob nginx die A2A-Proxy-Regeln (/.well-known/ und /a2a/) enthält."""
    nginx_site = Path("/etc/nginx/sites-enabled/hydrahive-console")
    if not nginx_site.exists():
        return []
    try:
        content = nginx_site.read_text(encoding="utf-8")
        has_well_known = "/.well-known/" in content
        has_a2a        = "/a2a/" in content
        if has_well_known and has_a2a:
            return [_check("Nginx: A2A-Proxy", "ok", "/.well-known/ und /a2a/ konfiguriert")]
        missing = ([".well-known"] if not has_well_known else []) + (["/a2a/"] if not has_a2a else [])
        return [_check(
            "Nginx: A2A-Proxy", "warn",
            f"Proxy-Regeln fehlen: {', '.join(missing)}",
            hint="Fix-Button klicken um nginx automatisch zu aktualisieren",
            fix="nginx_a2a",
        )]
    except Exception as e:
        return [_check("Nginx: A2A-Proxy", "warn", f"Konfig nicht lesbar: {e}")]


async def _fix_nginx() -> dict:
    """Führt 16_nginx_update.sh aus — injiziert fehlende nginx-Regeln idempotent."""
    script = Path("/opt/hydrahive/installer/modules/16_nginx_update.sh")
    if not script.exists():
        return {"ok": False, "error": "Fix-Script nicht gefunden — bitte zuerst ein Update ausführen"}

    def _run():
        return subprocess.run(
            ["sudo", "-n", "/bin/bash", str(script)],
            capture_output=True, text=True, timeout=30,
        )

    r = await asyncio.to_thread(_run)
    if r.returncode == 0:
        return {"ok": True, "output": (r.stdout or "").strip()}
    return {"ok": False, "error": (r.stderr or r.stdout or "unbekannter Fehler").strip()}


async def _fix_install_chromium() -> dict:
    """Installiert Chrome-Laufzeitbibliotheken via apt (benötigt für Puppeteer-Chrome)."""
    _chrome_libs = [
        "libnspr4", "libnss3", "libatk1.0-0", "libatk-bridge2.0-0",
        "libcups2", "libdrm2", "libxkbcommon0", "libxcomposite1", "libxdamage1",
        "libxfixes3", "libxrandr2", "libgbm1", "libpango-1.0-0", "libcairo2",
        "libdbus-1-3", "libx11-6", "libxcb1", "libxext6", "libxshmfence1",
    ]

    def _run_apt(packages: list[str]) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            ["sudo", "-n", "apt-get", "install", "-y", "--no-install-recommends"] + packages,
            capture_output=True, text=True, timeout=180,
        )

    # libasound2 heißt auf Debian 12+ libasound2t64
    r = await asyncio.to_thread(_run_apt, _chrome_libs + ["libasound2"])
    if r.returncode != 0:
        r = await asyncio.to_thread(_run_apt, _chrome_libs + ["libasound2t64"])

    if r.returncode == 0:
        return {"ok": True, "output": "Chrome-Bibliotheken installiert"}
    return {"ok": False, "error": (r.stderr or r.stdout or "apt-get fehlgeschlagen").strip()[:500]}


async def _fix_repair_whatsapp() -> dict:
    """Reinstalliert WhatsApp Bridge ohne Datenverlust (Sessions + Config bleiben)."""
    import shlex
    script = Path("/opt/hydrahive/installer/modules/13_whatsapp_bridge.sh")
    if not script.exists():
        return {"ok": False, "error": "Installer-Modul nicht gefunden — bitte Update durchführen"}

    def _run():
        return subprocess.run(
            ["sudo", "-n", "/bin/bash", str(script)],
            capture_output=True, text=True, timeout=300,
            env={**__import__("os").environ, "DEBIAN_FRONTEND": "noninteractive",
                 "HYDRAHIVE_DIR": "/opt/hydrahive"},
        )

    r = await asyncio.to_thread(_run)
    if r.returncode == 0:
        return {"ok": True, "output": (r.stdout or "").strip()[-1000:]}
    return {"ok": False, "error": (r.stderr or r.stdout or "Installer fehlgeschlagen").strip()[-500:]}


def _agentlink_url() -> str:
    """Liest AgentLink base_url aus /etc/hydrahive/agentlink.json (Default: http://localhost:8000)."""
    try:
        cfg = json.loads(Path("/etc/hydrahive/agentlink.json").read_text())
        return cfg.get("base_url", "http://localhost:8000").rstrip("/")
    except (OSError, json.JSONDecodeError):
        return "http://localhost:8000"


async def _check_agentlink() -> list[dict]:
    """Prüft AgentLink Health-Endpoint."""
    results = []
    url = _agentlink_url()

    try:
        import urllib.request
        with urllib.request.urlopen(f"{url}/health", timeout=5) as resp:
            data = json.loads(resp.read())
        redis_ok = data.get("redis") == "connected"
        results.append(_check(
            "AgentLink: Health",
            "ok" if redis_ok else "warn",
            f"status={data.get('status','?')} redis={data.get('redis','?')}",
            "" if redis_ok else "Redis prüfen: systemctl status redis-server",
        ))
    except Exception as e:
        results.append(_check("AgentLink: Health", "error", f"nicht erreichbar: {e}",
                              fix="repair_agentlink"))

    return results


async def _fix_repair_agentlink() -> dict:
    """Repariert AgentLink: DB-User-Reset + Service-Restart."""
    output_lines = []
    try:
        # 1. PostgreSQL Passwort Reset
        r = subprocess.run(
            ["sudo", "-u", "postgres", "psql", "-c", "ALTER USER agentlink PASSWORD 'changeme';"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            output_lines.append("PostgreSQL: Passwort zurückgesetzt")
        else:
            output_lines.append(f"PostgreSQL: {r.stderr.strip() or 'Fehler'}")

        # 2. Git safe.directory
        subprocess.run(
            ["sudo", "git", "config", "--global", "--add", "safe.directory", "/agentlink"],
            capture_output=True, timeout=5,
        )

        # 3. Service Restart
        r = subprocess.run(
            ["sudo", "systemctl", "restart", "agentlink"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            output_lines.append("AgentLink Service neu gestartet")
        else:
            output_lines.append(f"Service-Restart fehlgeschlagen: {r.stderr.strip()}")

        # 4. Health-Check nach kurzer Wartezeit
        import time
        time.sleep(3)
        import urllib.request
        url = _agentlink_url()
        with urllib.request.urlopen(f"{url}/health", timeout=5) as resp:
            data = json.loads(resp.read())
        output_lines.append(f"Health OK: status={data.get('status')} redis={data.get('redis')}")
        return {"ok": True, "output": "\n".join(output_lines)}
    except Exception as e:
        output_lines.append(f"Health-Check nach Repair fehlgeschlagen: {e}")
        return {"ok": False, "error": "\n".join(output_lines)}


def _check_nginx_projects() -> list[dict]:
    """Prüft ob nginx /projects/ konfiguriert und www-data in hydrahive-Gruppe ist."""
    results = []
    nginx_site = Path("/etc/nginx/sites-enabled/hydrahive-console")
    if nginx_site.exists():
        content = nginx_site.read_text(encoding="utf-8")
        if "location /projects/" in content:
            results.append(_check("Nginx: /projects/", "ok", "Location-Block vorhanden"))
        else:
            results.append(_check("Nginx: /projects/", "error",
                                  "Location-Block fehlt — Agenten-Dateien nicht erreichbar",
                                  "Fix-Button klicken um nginx automatisch zu aktualisieren",
                                  fix="nginx_projects"))

    # www-data in hydrahive-Gruppe?
    try:
        import grp, pwd
        hive_gid = grp.getgrnam("hydrahive").gr_gid
        www_groups = [g.gr_gid for g in grp.getgrall() if "www-data" in g.gr_mem]
        in_group = hive_gid in www_groups
        results.append(_check(
            "Nginx: www-data in hydrahive-Gruppe",
            "ok" if in_group else "error",
            "ja" if in_group else "nein — /projects/ gibt 403",
            "" if in_group else "sudo usermod -aG hydrahive www-data && sudo systemctl reload nginx",
        ))
    except KeyError:
        results.append(_check("Nginx: www-data in hydrahive-Gruppe", "warn", "Gruppe hydrahive nicht gefunden"))
    except Exception as e:
        results.append(_check("Nginx: www-data in hydrahive-Gruppe", "warn", str(e)))

    return results


def _check_nginx_upload_limit() -> list[dict]:
    """Prüft ob client_max_body_size in der nginx-Config gesetzt ist."""
    nginx_site = Path("/etc/nginx/sites-enabled/hydrahive-console")
    if not nginx_site.exists():
        return []
    try:
        content = nginx_site.read_text(encoding="utf-8")
        if "client_max_body_size" in content:
            return [_check("Nginx: Upload-Limit", "ok", "client_max_body_size konfiguriert")]
        return [_check(
            "Nginx: Upload-Limit", "warn",
            "client_max_body_size fehlt — Agent-Import > 1MB schlägt fehl",
            "Fix-Button klicken um nginx automatisch zu aktualisieren",
            fix="nginx_upload",
        )]
    except Exception as e:
        return [_check("Nginx: Upload-Limit", "warn", str(e))]


def _check_llm_config() -> list[dict]:
    """Prüft ob mindestens ein LLM-Provider konfiguriert ist (OAuth-Token oder API-Key)."""
    active = []

    # Claude Max OAuth Token
    claude_token = Path("/etc/hydrahive/claude_oauth_token")
    if claude_token.exists() and claude_token.read_text(encoding="utf-8").strip():
        active.append("Claude Max (OAuth)")

    # OpenAI Codex OAuth Token
    codex_token = Path("/etc/hydrahive/openai_codex_token.json")
    if codex_token.exists():
        try:
            data = json.loads(codex_token.read_text(encoding="utf-8"))
            if data.get("access_token") and data.get("account_id"):
                active.append("OpenAI Codex (OAuth)")
        except Exception:
            pass

    # llm_config.json: Provider mit API-Key
    config_path = Path("/etc/hydrahive/llm_config.json")
    if config_path.exists():
        try:
            raw = config_path.read_text(encoding="utf-8").strip()
            if raw:
                providers = json.loads(raw).get("providers", {})
                for name, cfg in providers.items():
                    if cfg.get("enabled") and cfg.get("api_key", "").strip():
                        active.append(f"{name} (API-Key)")
        except Exception:
            pass

    if active:
        return [_check("LLM: Provider-Config", "ok", ", ".join(active))]
    return [_check("LLM: Provider-Config", "warn",
                   "Kein LLM-Provider konfiguriert",
                   "Einstellungen → LLM-Provider")]


def _check_vpn() -> list[dict]:
    """Prüft Tailscale-Status falls installiert."""
    results = []
    try:
        r = subprocess.run(["tailscale", "status", "--json"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            data = json.loads(r.stdout)
            ip = data.get("TailscaleIPs", ["?"])[0] if data.get("TailscaleIPs") else "?"
            peer_data = data.get("Peer") or {}
            peers = len(peer_data)
            results.append(_check("VPN: Tailscale", "ok", f"IP: {ip}, Peers: {peers}"))
        else:
            results.append(_check("VPN: Tailscale", "warn", "Nicht verbunden"))
    except FileNotFoundError:
        pass  # tailscale nicht installiert → kein Check
    except Exception as e:
        results.append(_check("VPN: Tailscale", "warn", str(e)))
    return results


def _check_samba() -> list[dict]:
    """Prüft Samba-Config: force group = hydrahive + Dateiberechtigungen."""
    results = []
    shares_file = Path("/etc/samba/hydrahive-shares.conf")

    if not shares_file.exists():
        return []  # Kein Samba konfiguriert

    content = shares_file.read_text(encoding="utf-8")

    # Check force group
    if "force group = hydrahive" in content:
        results.append(_check("Samba: force group", "ok", "hydrahive-Gruppe gesetzt"))
    else:
        results.append(_check("Samba: force group", "error",
                              "force group = hydrahive fehlt — Agent kann Samba-Uploads nicht lesen",
                              fix="repair_samba"))

    # Check Dateiberechtigungen in /projects/
    bad_perms = []
    for proj in Path("/projects").iterdir():
        files_dir = proj / "files"
        if not files_dir.is_dir():
            continue
        try:
            for f in list(files_dir.rglob("*"))[:50]:  # Max 50 Dateien samplen
                if f.is_file():
                    import grp
                    try:
                        fg = grp.getgrgid(f.stat().st_gid).gr_name
                        if fg != "hydrahive":
                            bad_perms.append(str(f.relative_to(files_dir)))
                            break
                    except (KeyError, OSError):
                        pass
        except Exception:
            pass

    if bad_perms:
        results.append(_check("Samba: Dateiberechtigungen", "warn",
                              f"{len(bad_perms)} Projekt(e) mit falscher Gruppe",
                              fix="repair_samba"))
    else:
        results.append(_check("Samba: Dateiberechtigungen", "ok", "Alle Dateien in hydrahive-Gruppe"))

    return results


async def _fix_samba_permissions() -> dict:
    """Repariert Samba: force group + Dateiberechtigungen."""
    output = []
    try:
        shares_file = Path("/etc/samba/hydrahive-shares.conf")
        if shares_file.exists():
            content = subprocess.run(["sudo", "cat", str(shares_file)],
                                     capture_output=True, text=True, timeout=5).stdout
            if "force group" not in content:
                content = content.replace("create mask", "force group = hydrahive\n   create mask")
                tmp = Path("/tmp/hydrahive-samba-fix.conf")
                tmp.write_text(content, encoding="utf-8")
                subprocess.run(["sudo", "cp", str(tmp), str(shares_file)],
                               capture_output=True, check=True, timeout=5)
                tmp.unlink(missing_ok=True)
                output.append("force group = hydrahive in Samba-Config eingefügt")

                subprocess.run(["sudo", "smbcontrol", "smbd", "reload-config"],
                               capture_output=True, timeout=10)
                output.append("Samba-Config reloaded")
            else:
                output.append("force group bereits gesetzt")

        # Dateiberechtigungen fixen — sowohl files/ als auch Projektroot
        for proj in Path("/projects").iterdir():
            if proj.is_dir():
                subprocess.run(["sudo", "chgrp", "-R", "hydrahive", str(proj)],
                               capture_output=True, timeout=60)
                subprocess.run(["sudo", "chmod", "-R", "g+rw", str(proj)],
                               capture_output=True, timeout=60)
        output.append("Dateiberechtigungen für alle Projekte korrigiert")

        return {"ok": True, "output": "\n".join(output)}
    except Exception as e:
        output.append(f"Fehler: {e}")
        return {"ok": False, "error": "\n".join(output)}
