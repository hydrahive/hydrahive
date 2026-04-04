from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from .router_core_misc import collect_core_journal_report


class NetworkProfileRequest(BaseModel):
    profile: str


class GiteaConfigRequest(BaseModel):
    url: str = "http://127.0.0.1:3001"
    token: str = ""
    org: str = "hydrahive"
    webhook_secret: str = ""


_restart_lock = asyncio.Lock()
_UPDATE_HEAD_CACHE: dict[str, object] = {
    "checked_at": datetime.fromtimestamp(0, tz=timezone.utc),
    "remote_commit": "",
    "remote_commit_full": "",
    "source": "github/hydrahive",
    "error": "",
}


def register_system_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    get_hb_scheduler,
    discovery,
    projects,
    sessions,
    runtime,
    agents_dir: str,
    projects_dir: str,
    read_network_profile,
    network_profile_status,
    normalize_network_profile,
    write_network_profile,
    network_profile_script: str,
    run_self_update,
    gitea_config_file: str,
    app_version: str,
    logger: logging.Logger,
) -> None:
    def _resolve_update_source() -> tuple[str, str]:
        default_url = "https://github.com/hydrahive/hydrahive.git"
        default_source = "github/hydrahive"
        if not Path("/etc/hydrahive/use_local_gitea").exists():
            return default_url, default_source
        p = Path(gitea_config_file)
        if not p.exists():
            return default_url, default_source
        try:
            cfg = json.loads(p.read_text())
        except Exception as e:
            logger.debug("Failed to parse gitea config: %s", e)
            return default_url, default_source
        base_url = str(cfg.get("url", "")).strip().rstrip("/")
        org = str(cfg.get("org", "hydrahive")).strip() or "hydrahive"
        repo = str(cfg.get("repo", org)).strip() or org
        token = str(cfg.get("token", "")).strip()
        if not base_url:
            return default_url, default_source
        if token:
            if base_url.startswith("http://"):
                base_url = base_url.replace("http://", f"http://{org}:{token}@")
            elif base_url.startswith("https://"):
                base_url = base_url.replace("https://", f"https://{org}:{token}@")
        return f"{base_url}/{org}/{repo}.git", f"{org}/{repo}"

    def _get_remote_head() -> dict[str, str]:
        now = datetime.now(timezone.utc)
        checked_at = _UPDATE_HEAD_CACHE["checked_at"]
        if isinstance(checked_at, datetime) and now - checked_at < timedelta(seconds=30):
            return {
                "remote_commit": str(_UPDATE_HEAD_CACHE["remote_commit"]),
                "remote_commit_full": str(_UPDATE_HEAD_CACHE["remote_commit_full"]),
                "source": str(_UPDATE_HEAD_CACHE["source"]),
                "error": str(_UPDATE_HEAD_CACHE["error"]),
            }

        remote_url, remote_source = _resolve_update_source()
        remote_commit = ""
        remote_commit_full = ""
        error = ""
        try:
            proc = subprocess.run(
                ["git", "ls-remote", "--heads", remote_url, "main"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0:
                line = (proc.stdout or "").strip().splitlines()[0] if (proc.stdout or "").strip() else ""
                remote_commit_full = line.split()[0] if line else ""
                remote_commit = remote_commit_full[:7] if remote_commit_full else ""
            else:
                error = (proc.stderr or proc.stdout or "").strip()[:200]
        except Exception as e:
            error = str(e)[:200]

        _UPDATE_HEAD_CACHE.update(
            checked_at=now,
            remote_commit=remote_commit,
            remote_commit_full=remote_commit_full,
            source=remote_source,
            error=error,
        )
        return {
            "remote_commit": remote_commit,
            "remote_commit_full": remote_commit_full,
            "source": remote_source,
            "error": error,
        }

    def _load_update_status(status_file: str = "") -> dict:
        # Beide Pfade prüfen — hydrahive zuerst, hydrahive als Fallback
        if not status_file:
            for sf in ["/var/run/hydrahive-update.json", "/var/run/hydrahive-update.json"]:
                if Path(sf).exists():
                    status_file = sf
                    break
            else:
                status_file = "/var/run/hydrahive-update.json"
        p = Path(status_file)
        if p.exists():
            try:
                status = json.loads(p.read_text())
            except Exception as e:
                logger.debug("Failed to parse update status file: %s", e)
                status = {"status": "unknown"}
        else:
            status = {"status": "never"}
        if status.get("status") == "running" and status.get("started_at"):
            try:
                started = datetime.fromisoformat(status["started_at"])
                if datetime.now(tz=timezone.utc) - started.astimezone(timezone.utc) > timedelta(minutes=10):
                    status["status"] = "ok"
                    status["stale"] = True
            except Exception as e:
                logger.debug("Failed to parse update started_at timestamp: %s", e)
        status["available"] = False
        if status.get("status") not in {"running", "error"}:
            remote = _get_remote_head()
            if remote["remote_commit_full"]:
                status["source"] = remote["source"]
                status["remote_commit"] = remote["remote_commit"]
                status["remote_commit_full"] = remote["remote_commit_full"]
                if status.get("commit_full"):
                    status["available"] = status.get("commit_full") != remote["remote_commit_full"]
                elif status.get("commit"):
                    status["available"] = status.get("commit") != remote["remote_commit"]
            else:
                status["source"] = remote["source"]
                if remote["error"]:
                    status["remote_error"] = remote["error"]
        return status

    @admin_router.get("/admin/network/profile")
    def get_network_profile_status():
        return network_profile_status()

    @admin_router.put("/admin/network/profile")
    def apply_network_profile(req: NetworkProfileRequest):
        import subprocess as _sub

        profile = normalize_network_profile(req.profile)
        proc = _sub.run(
            ["sudo", network_profile_script, profile],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise HTTPException(500, f"Network-Profil konnte nicht angewendet werden: {(proc.stderr or proc.stdout).strip()[:300]}")

        write_network_profile(profile)
        return {"updated": True, "profile": profile, "status": network_profile_status()}

    @admin_router.get("/system/heartbeat-tasks")
    def heartbeat_tasks_status():
        hb_scheduler = get_hb_scheduler()
        tasks = hb_scheduler.task_summary() if hb_scheduler else []
        return {"tasks": tasks}

    @auth_router.get("/system/gpu")
    def gpu_info():
        import shutil
        import subprocess

        if not shutil.which("nvidia-smi"):
            return {"available": False, "reason": "nvidia-smi nicht gefunden"}
        try:
            fields = "name,temperature.gpu,utilization.gpu,utilization.memory,memory.total,memory.used,memory.free,power.draw,power.limit"
            out = subprocess.run(
                ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode != 0:
                return {"available": False, "reason": out.stderr.strip()}
            gpus = []
            for line in out.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 7:
                    continue

                def _int(v: str):
                    try:
                        return int(v)
                    except (ValueError, TypeError):
                        return None

                def _float(v: str):
                    try:
                        return round(float(v), 1)
                    except (ValueError, TypeError):
                        return None

                gpus.append({
                    "name": parts[0],
                    "temp_c": _int(parts[1]),
                    "util_gpu_pct": _int(parts[2]),
                    "util_mem_pct": _int(parts[3]),
                    "mem_total_mb": _int(parts[4]),
                    "mem_used_mb": _int(parts[5]),
                    "mem_free_mb": _int(parts[6]),
                    "power_draw_w": _float(parts[7]) if len(parts) > 7 else None,
                    "power_limit_w": _float(parts[8]) if len(parts) > 8 else None,
                })
            return {"available": True, "gpus": gpus}
        except subprocess.TimeoutExpired:
            return {"available": False, "reason": "nvidia-smi Timeout"}
        except Exception as e:
            return {"available": False, "reason": str(e)}

    # ── AgentLink Config ─────────────────────────────────────────────

    _AGENTLINK_CFG = Path("/etc/hydrahive/agentlink.json")

    @admin_router.get("/admin/agentlink/config")
    def get_agentlink_config():
        """AgentLink-Konfiguration lesen."""
        try:
            data = json.loads(_AGENTLINK_CFG.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        # Health-Check
        healthy = False
        url = data.get("base_url", "")
        if url and data.get("enabled"):
            try:
                import urllib.request
                with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=3) as resp:
                    h = json.loads(resp.read())
                    healthy = h.get("status") == "healthy"
            except Exception as e:
                logger.debug("AgentLink health check failed: %s", e)
        return {
            "base_url": data.get("base_url", ""),
            "ws_url":   data.get("ws_url", ""),
            "enabled":  data.get("enabled", False),
            "healthy":  healthy,
        }

    @admin_router.put("/admin/agentlink/config")
    def set_agentlink_config(body: dict = Body(...)):
        """AgentLink-Konfiguration speichern."""
        from .agentlink_client import _config_cache
        cfg = {
            "base_url": body.get("base_url", "http://localhost:8000").strip().rstrip("/"),
            "ws_url":   body.get("ws_url", "ws://localhost:8000").strip().rstrip("/"),
            "enabled":  bool(body.get("enabled", True)),
        }
        _AGENTLINK_CFG.write_text(json.dumps(cfg, indent=2))
        # Cache invalidieren
        import hydrahive_core.agentlink_client as _alc
        _alc._config_cache = None
        return {"saved": True, **cfg}

    # ── Systemzeit / Timezone ────────────────────────────────────────

    @auth_router.get("/admin/system/time")
    def get_system_time():
        """Aktuelle Serverzeit + Zeitzone."""
        # timedatectl is authoritative — /etc/timezone may be stale
        tz_full = subprocess.run(
            ["timedatectl", "show", "-p", "Timezone", "--value"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if not tz_full:
            try:
                tz_full = Path("/etc/timezone").read_text().strip()
            except OSError:
                tz_full = "unknown"
        import time as _time
        tz_name = _time.tzname[_time.daylight] if _time.daylight else _time.tzname[0]
        now = datetime.now()
        utc_now = datetime.now(timezone.utc)
        offset_h = round((now - utc_now.replace(tzinfo=None)).total_seconds() / 3600, 1)
        return {
            "server_time": now.isoformat(timespec="seconds"),
            "utc_time": utc_now.isoformat(timespec="seconds"),
            "timezone": tz_full,
            "timezone_abbr": tz_name,
            "utc_offset_hours": offset_h,
        }

    @admin_router.put("/admin/system/timezone")
    def set_system_timezone(body: dict = Body(...)):
        """Systemzeitzone setzen (erfordert timedatectl-Berechtigung)."""
        tz = body.get("timezone", "").strip()
        if not tz or "/" not in tz:
            raise HTTPException(400, "Ungültige Zeitzone (Format: Region/Stadt, z.B. Europe/Berlin)")
        result = subprocess.run(
            ["sudo", "timedatectl", "set-timezone", tz],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise HTTPException(500, f"timedatectl Fehler: {result.stderr.strip()}")
        return {"updated": True, "timezone": tz}

    @auth_router.get("/status")
    def system_status():
        return {
            "discovery": {
                "agents_dir": agents_dir,
                "count": len(discovery.agents),
            },
            "projects": {
                "projects_dir": projects_dir,
                "count": len(projects.projects),
            },
            "sessions": {
                "active_projects": sessions.active_projects(),
            },
            "network": {
                "profile": read_network_profile(),
                "deviations": network_profile_status()["deviations"],
            },
            "runtime": runtime.status_all(),
        }

    @admin_router.get("/admin/update/status")
    def get_update_status():
        log_file = "/var/log/hydrahive-update.log"
        if not Path(log_file).exists():
            log_file = "/var/log/hydrahive-update.log"
        status = _load_update_status()
        try:
            lines = Path(log_file).read_text(errors="replace").splitlines()
            status["log_tail"] = lines[-20:]
        except Exception as e:
            logger.debug("Failed to read update log: %s", e)
            status["log_tail"] = []
        return status

    @admin_router.get("/admin/runtime/status")
    def get_runtime_status():
        update_status = _load_update_status()
        journal_report = collect_core_journal_report(lines=200)
        return {
            "service": {
                "name": "hydrahive-core",
                "version": app_version,
            },
            "deployment": update_status,
            "runtime": runtime.status_all(),
            "audit": {
                "core_journal": journal_report["summary"],
            },
        }

    @admin_router.post("/admin/update/trigger")
    async def trigger_update():
        async def _run_and_notify():
            await run_self_update(pusher="admin-manual", commits=0)
            # Kurz warten bis Status-Datei geschrieben ist
            import asyncio as _aio
            await _aio.sleep(5)
            try:
                status = _load_update_status()
                from .notification_service import notification_service as _ns
                from .tool_registry import _load_users_fn as _luf
                users: list[str] = []
                if _luf:
                    try:
                        all_users = _luf()
                        users = [u for u, d in all_users.items() if d.get("role") == "admin"]
                    except Exception as e:
                        logger.debug("Failed to load admin users for update notification: %s", e)
                users = users or ["admin"]
                if status.get("status") == "ok":
                    commit = status.get("commit", "")
                    msg = status.get("message", "")
                    for u in users:
                        await _ns.push(user=u, type="system",
                                       title=f"Update abgeschlossen ({commit})",
                                       body=msg or "System erfolgreich aktualisiert.",
                                       link="/system")
                elif status.get("status") == "error":
                    for u in users:
                        await _ns.push(user=u, type="system",
                                       title="Update fehlgeschlagen",
                                       body=status.get("error", "Unbekannter Fehler")[:120],
                                       link="/system")
            except Exception as e:
                logger.warning("Failed to send update notification: %s", e)

        asyncio.create_task(_run_and_notify())
        return {"status": "deploying", "message": "Update gestartet — GET /admin/update/status für Status"}

    @admin_router.post("/admin/core/restart")
    async def restart_core():
        if not _restart_lock.acquire_nowait():
            raise HTTPException(409, "Neustart läuft bereits")

        async def _do_restart():
            try:
                await asyncio.sleep(1.5)
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "systemctl", "restart", "hydrahive-core",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(proc.wait(), timeout=30)
                except asyncio.TimeoutError:
                    logger.error("core/restart: systemctl timed out")
                except Exception as e:
                    logger.error("core/restart: %s", e)
            finally:
                _restart_lock.release()

        asyncio.create_task(_do_restart())
        return {"status": "restarting", "message": "Core-Neustart ausgelöst — Seite lädt automatisch neu"}

    @admin_router.get("/gitea/config")
    def get_gitea_config():
        p = Path(gitea_config_file)
        if not p.exists():
            return {"url": "http://127.0.0.1:3001", "org": "hydrahive", "webhook_secret": "", "has_token": False, "token_masked": ""}
        cfg = json.loads(p.read_text(encoding="utf-8"))
        token = cfg.get("token", "")
        return {
            "url": cfg.get("url", "http://127.0.0.1:3001"),
            "org": cfg.get("org", "hydrahive"),
            "webhook_secret": cfg.get("webhook_secret", ""),
            "has_token": bool(token),
            "token_masked": token[:8] + "..." + token[-4:] if token else "",
        }

    @admin_router.get("/gitea/credentials")
    def get_gitea_credentials():
        """Gibt Gitea-Zugangsdaten zurück (URL, Admin-User, Passwort, Token) für die Workspace-Anzeige."""
        p = Path(gitea_config_file)
        cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        internal_url = cfg.get("url", "http://127.0.0.1:3001")
        # Externe URL: intern 127.0.0.1:3001 → auf Port 3002 (nginx-Proxy) umschreiben
        import re as _re
        external_url = _re.sub(r"127\.0\.0\.1:3001", "127.0.0.1:3002", internal_url)
        username = cfg.get("org", "hydrahive")
        token = cfg.get("token", "")
        # Passwort aus /etc/hydrahive/admin_credentials (console_password=...)
        password = ""
        cred_file = Path("/etc/hydrahive/admin_credentials")
        if cred_file.exists():
            for line in cred_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("console_password="):
                    password = line.split("=", 1)[1].strip()
                    break
        return {
            "url": external_url,
            "username": username,
            "password": password,
            "token": token,
        }

    @admin_router.put("/gitea/config")
    def update_gitea_config(req: GiteaConfigRequest):
        from .gitea import reload_gitea_client

        data = req.model_dump()
        cfg_path = Path(gitea_config_file)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        cfg_path.chmod(0o600)
        reload_gitea_client()
        logger.info("Gitea-Config aktualisiert: url=%s org=%s", req.url, req.org)
        return {"updated": True}

    @auth_router.get("/gitea/repos")
    async def list_gitea_repos():
        from .gitea import get_gitea_client
        import aiohttp as _aio

        try:
            client = get_gitea_client()
            try:
                repos = await client._get(f"/orgs/{client.org}/repos?limit=50")
            except _aio.ClientResponseError as e:
                if e.status == 404:
                    repos = await client._get(f"/users/{client.org}/repos?limit=50")
                else:
                    raise
            return {"repos": [
                {
                    "name": r.get("name"),
                    "description": r.get("description"),
                    "html_url": r.get("html_url"),
                    "default_branch": r.get("default_branch"),
                    "updated": r.get("updated"),
                }
                for r in (repos if isinstance(repos, list) else [])
            ]}
        except Exception as e:
            raise HTTPException(503, f"Gitea nicht erreichbar: {e}")

    @auth_router.get("/gitea/repos/{project_id}/prs")
    async def list_project_prs(project_id: str):
        from .gitea import get_gitea_client

        try:
            client = get_gitea_client()
            prs = await client.list_prs(project_id)
            return {"prs": prs, "count": len(prs if isinstance(prs, list) else [])}
        except Exception as e:
            raise HTTPException(503, f"Gitea-Fehler: {e}")

    # ------------------------------------------------------------------ #
    # Disk-Cleanup (#81)                                                   #
    # ------------------------------------------------------------------ #

    @admin_router.get("/admin/cleanup/status")
    def get_cleanup_status():
        from .cleanup_service import cleanup_service as _cs, get_disk_usage, _load_config
        return {
            "last_result": _cs.last_result(),
            "disk": get_disk_usage("/"),
            "config": _load_config(),
        }

    @admin_router.post("/admin/cleanup/run")
    async def trigger_cleanup():
        from .cleanup_service import cleanup_service as _cs
        result = await _cs.run_now()
        return result

    @admin_router.put("/admin/cleanup/config")
    def update_cleanup_config(body: dict = Body(...)):
        from .cleanup_service import _load_config, save_config
        allowed = {"transcript_days", "backup_keep", "warn_pct_yellow", "warn_pct_red"}
        cfg = _load_config()
        for k, v in body.items():
            if k in allowed:
                cfg[k] = v
        save_config(cfg)
        return {"updated": True, "config": cfg}
