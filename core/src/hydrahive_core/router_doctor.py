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
        if fix_id == "nginx_a2a":
            return await _fix_nginx_a2a()
        raise _HTTP(400, f"Unbekannter Fix: {fix_id}")

    @admin_router.get("/admin/doctor")
    async def run_doctor(_a=Depends(require_admin)):
        checks = []
        checks += await _check_services()
        checks += _check_configs()
        checks += _check_nginx_a2a()
        checks += _check_ports()
        checks += _check_disk()
        checks += await _check_api()
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
    ]
    optional = [
        (["hydrahive-whatsapp-bridge"], "WhatsApp Bridge"),
        (["tailscaled"],               "Tailscale VPN"),
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
    for units, label in optional:
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
    ports = [
        (8765, "Core API"),
        (80,   "HTTP/nginx"),
        (3002, "Gitea"),
        (6167, "Matrix (conduwuit)"),
    ]
    for port, label in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                results.append(_check(f"Port {port}: {label}", "ok", "erreichbar"))
        except Exception:
            results.append(_check(f"Port {port}: {label}", "error", "nicht erreichbar",
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


async def _fix_nginx_a2a() -> dict:
    """Kopiert hydrahive-console.nginx → sites-enabled und lädt nginx neu."""
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
