from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from .router_core_misc import collect_core_journal_report
from .settings import settings


class NetworkProfileRequest(BaseModel):
    profile: str


class AgentLinkConfigRequest(BaseModel):
    base_url: str = "http://localhost:8000"
    ws_url: str = "ws://localhost:8000"
    enabled: bool = True


class TimezoneRequest(BaseModel):
    timezone: str


class CleanupConfigRequest(BaseModel):
    transcript_days: int | None = None
    backup_keep: int | None = None
    warn_pct_yellow: int | None = None
    warn_pct_red: int | None = None


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
    def _resolve_update_source() -> tuple[str, str, dict]:
        default_url = "https://github.com/hydrahive/hydrahive.git"
        default_source = "github/hydrahive"
        if not settings.use_local_gitea_file.exists():
            return default_url, default_source, {}
        p = Path(gitea_config_file)
        if not p.exists():
            return default_url, default_source, {}
        try:
            cfg = json.loads(p.read_text())
        except Exception as e:
            logger.debug("Failed to parse gitea config: %s", e)
            return default_url, default_source, {}
        base_url = str(cfg.get("url", "")).strip().rstrip("/")
        org = str(cfg.get("org", "hydrahive")).strip() or "hydrahive"
        repo = str(cfg.get("repo", org)).strip() or org
        token = str(cfg.get("token", "")).strip()
        if not base_url:
            return default_url, default_source, {}
        # #1: Token NICHT in URL einbetten — als Extra-Env für Credential-Helper
        env = {}
        if token:
            env["GIT_TOKEN"] = token
            env["GIT_USER"] = org
        return f"{base_url}/{org}/{repo}.git", f"{org}/{repo}", env

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

        remote_url, remote_source, git_env = _resolve_update_source()
        remote_commit = ""
        remote_commit_full = ""
        error = ""
        try:
            import os as _os
            run_env = {**_os.environ}
            # Token via Credential-Helper statt in URL — kein Leak in Prozessliste
            if git_env.get("GIT_TOKEN"):
                cred_helper = f"!f() {{ echo username={git_env['GIT_USER']}; echo password={git_env['GIT_TOKEN']}; }}; f"
                run_env["GIT_ASKPASS"] = "/bin/true"
                run_env["GIT_CONFIG_COUNT"] = "1"
                run_env["GIT_CONFIG_KEY_0"] = "credential.helper"
                run_env["GIT_CONFIG_VALUE_0"] = cred_helper
            proc = subprocess.run(
                ["git", "ls-remote", "--heads", remote_url, "main"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env=run_env,
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
            for sf in ["/var/run/hydrahive-update.json", "/var/run/octopos-update.json"]:
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
            logger.error("Network-Profil Fehler: %s", (proc.stderr or proc.stdout).strip()[:300])
            raise HTTPException(500, "Network-Profil konnte nicht angewendet werden")

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

    _AGENTLINK_CFG = settings.agentlink_config

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
    def set_agentlink_config(req: AgentLinkConfigRequest):
        """AgentLink-Konfiguration speichern."""
        from .agentlink_client import _config_cache
        cfg = {
            "base_url": req.base_url.strip().rstrip("/"),
            "ws_url":   req.ws_url.strip().rstrip("/"),
            "enabled":  req.enabled,
        }
        _AGENTLINK_CFG.write_text(json.dumps(cfg, indent=2))
        _AGENTLINK_CFG.chmod(0o600)
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
    def set_system_timezone(req: TimezoneRequest):
        """Systemzeitzone setzen (erfordert timedatectl-Berechtigung)."""
        tz = req.timezone.strip()
        if not tz or "/" not in tz:
            raise HTTPException(400, "Ungültige Zeitzone (Format: Region/Stadt, z.B. Europe/Berlin)")
        result = subprocess.run(
            ["sudo", "timedatectl", "set-timezone", tz],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            logger.error("timedatectl Fehler: %s", result.stderr.strip())
            raise HTTPException(500, "Zeitzone konnte nicht gesetzt werden")
        return {"updated": True, "timezone": tz}

    # ── OAuth Usage / Rate Limits ─────────────────────────────────

    @auth_router.get("/admin/system/oauth-usage")
    async def get_oauth_usage():
        """OAuth Rate-Limit-Status aus den letzten API-Response-Headers.

        Gibt 5h-Session-Limit, 7d-Weekly-Limit, Overage-Status zurück.
        Daten stammen aus dem letzten Anthropic API-Call (Header-basiert, kein extra Request).
        """
        from .orchestrator_llm import get_oauth_rate_limits

        limits = get_oauth_rate_limits()
        if not limits:
            return {"available": False, "message": "Noch kein OAuth-Call ausgeführt — Daten verfügbar nach dem ersten Chat."}

        # Menschenlesbare Aufbereitung
        result: dict = {"available": True, "raw": limits}

        for window, label in [("5h", "Session (5h)"), ("7d", "Woche (7d)")]:
            util = limits.get(f"{window}_utilization")
            reset = limits.get(f"{window}_reset")
            if util is not None:
                result[window] = {
                    "label": label,
                    "utilization_pct": round(util * 100, 1),
                    "reset": reset,
                }

        # Overage
        overage_util = limits.get("overage_utilization")
        if overage_util is not None:
            result["overage"] = {
                "label": "Zusätzliche Nutzung",
                "utilization_pct": round(overage_util * 100, 1),
                "status": limits.get("overage_status"),
                "reset": limits.get("overage_reset"),
                "disabled_reason": limits.get("overage_disabled_reason"),
            }

        result["status"] = limits.get("status", "unknown")
        result["updated_at"] = limits.get("updated_at")
        return result

    @auth_router.get("/admin/system/oauth-usage/fetch")
    async def fetch_oauth_usage():
        """Frische Usage-Daten direkt von der Anthropic API abrufen.

        Ruft POST /api/oauth/usage auf (wie Claude Web/CLI).
        Benötigt gültigen OAuth-Token.
        """
        import httpx
        from .orchestrator_llm import _load_claude_oauth_token

        token = _load_claude_oauth_token()
        if not token:
            raise HTTPException(404, "Kein OAuth-Token verfügbar")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.anthropic.com/api/oauth/usage",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "anthropic-beta": "oauth-2025-04-20",
                        "user-agent": "claude-cli/2.1.62",
                        "x-app": "cli",
                    },
                )
                if resp.status_code != 200:
                    return {"error": f"API-Fehler: {resp.status_code}", "body": resp.text[:500]}
                return {"available": True, "utilization": resp.json()}
        except Exception as e:
            raise HTTPException(502, f"Anthropic API nicht erreichbar: {e}")

    @auth_router.get("/capabilities")
    def get_capabilities():
        """#309: Feature-Status-Registry — zeigt welche Features installiert/konfiguriert/aktiv sind."""
        import shutil

        def _svc_active(name: str) -> bool:
            try:
                r = subprocess.run(["systemctl", "is-active", "--quiet", name], capture_output=True, timeout=5)
                return r.returncode == 0
            except Exception:
                return False

        def _feature(name: str, *, installed: bool, configured: bool = False, active: bool = False) -> dict:
            if active:
                status = "active"
            elif configured:
                status = "configured"
            elif installed:
                status = "installed"
            else:
                status = "not_installed"
            return {"name": name, "installed": installed, "configured": configured, "active": active, "status": status}

        features = []
        # Core
        features.append(_feature("core", installed=True, configured=True, active=_svc_active("hydrahive-core")))
        # LLM
        llm_cfg = settings.llm_config.exists() or settings.llm_env.exists()
        features.append(_feature("llm", installed=True, configured=llm_cfg, active=llm_cfg))
        # Ollama
        ollama_installed = shutil.which("ollama") is not None
        features.append(_feature("ollama", installed=ollama_installed, configured=ollama_installed, active=_svc_active("ollama")))
        # Gitea
        gitea_installed = Path("/usr/local/bin/gitea").exists()
        gitea_cfg = Path(gitea_config_file).exists()
        features.append(_feature("gitea", installed=gitea_installed, configured=gitea_cfg, active=_svc_active("gitea")))
        # Console
        features.append(_feature("console", installed=(settings.opt_dir / "console" / "index.html").exists(), configured=True, active=True))
        # AgentLink
        al_installed = (settings.opt_dir / "agentlink").exists() or settings.agentlink_config.exists()
        features.append(_feature("agentlink", installed=al_installed, configured=al_installed, active=_svc_active("hydrahive-agentlink")))
        # VPN/Tailscale
        ts_installed = shutil.which("tailscale") is not None
        ts_cfg = settings.tailscale_config.exists()
        features.append(_feature("tailscale", installed=ts_installed, configured=ts_cfg, active=_svc_active("tailscaled")))
        # code-server
        cs_installed = Path("/opt/codeserver/bin/code-server").exists()
        features.append(_feature("code-server", installed=cs_installed, configured=cs_installed, active=_svc_active("code-server")))
        # A-MEM
        amem_cfg = settings.amem_config.exists()
        features.append(_feature("a-mem", installed=amem_cfg, configured=amem_cfg, active=_svc_active("hydrahive-amem")))
        # WhatsApp Bridge
        wa_installed = settings.whatsapp_bridge_dir.exists()
        features.append(_feature("whatsapp-bridge", installed=wa_installed, configured=wa_installed, active=_svc_active("hydrahive-whatsapp-bridge")))
        # Discord — active nur wenn mindestens ein Bot-Token konfiguriert ist
        discord_configured = any(settings.agent_tokens_dir.glob("*_discord.json"))
        features.append(_feature("discord", installed=True, configured=discord_configured, active=discord_configured))
        # Vaultwarden
        vw_installed = Path("/usr/local/bin/vaultwarden").exists()
        features.append(_feature("vaultwarden", installed=vw_installed, configured=vw_installed, active=_svc_active("vaultwarden")))
        # KAS (Mail)
        features.append(_feature("kas", installed=True, configured=settings.kas_config.exists()))
        # Plugins
        plugins_dir = Path("/plugins")
        features.append(_feature("plugins", installed=plugins_dir.exists(), configured=plugins_dir.exists(),
                                 active=any(plugins_dir.iterdir()) if plugins_dir.exists() else False))

        return {"capabilities": features, "count": len(features)}

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
        """Update-Status + Log-Tail für Polling-Clients."""
        log_file = "/var/log/hydrahive-update.log"
        if not Path(log_file).exists():
            log_file = "/var/log/hydrahive-update.log"
        status = _load_update_status()
        try:
            lines = Path(log_file).read_text(errors="replace").splitlines()
            status["log_tail"] = lines[-200:]
            status["log_total"] = len(lines)
        except Exception as e:
            logger.debug("Failed to read update log: %s", e)
            status["log_tail"] = []
            status["log_total"] = 0
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

    @admin_router.post("/admin/agents/{agent_id}/health-check")
    async def agent_health_check(agent_id: str):
        """LLM-Ping: prüft ob Agent antworten kann (#366)."""
        cfg = discovery.get(agent_id)
        if not cfg:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        from .orchestrator_llm import _llm_call as _lc
        try:
            resp = await asyncio.wait_for(
                _lc(cfg, [{"role": "user", "content": "Antworte nur mit OK."}], None),
                timeout=15.0,
            )
            text = resp.choices[0].message.content or ""
            return {"agent_id": agent_id, "healthy": True, "response": text[:50], "model": cfg.llm.model}
        except asyncio.TimeoutError:
            return {"agent_id": agent_id, "healthy": False, "error": "Timeout (15s)"}
        except Exception as e:
            return {"agent_id": agent_id, "healthy": False, "error": str(e)[:100]}

    @admin_router.get("/admin/agents/{agent_id}/debug")
    def get_agent_debug(agent_id: str):
        """Debug-Info für einen Agent: Config, Tools, System-Prompt-Größe, letzte Session (#371)."""
        cfg = discovery.get(agent_id)
        if not cfg:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        from .token_estimation import estimate_tokens
        # System-Prompt Größe schätzen
        sys_prompt_size = 0
        if cfg.agent_dir:
            soul_path = cfg.agent_dir / (cfg.soul or "soul.md")
            if soul_path.exists():
                sys_prompt_size += estimate_tokens(soul_path.read_text(encoding="utf-8"))
            memory_dir = cfg.agent_dir / "memory"
            if memory_dir.exists():
                for f in memory_dir.glob("*.md"):
                    sys_prompt_size += estimate_tokens(f.read_text(encoding="utf-8"))
        # Letzte Session-Stats
        from .orchestrator_context import _context_window_for_model, _history_token_budget, _RESERVE_TOKENS_FLOOR
        ctx_window = _context_window_for_model(cfg.llm.model)
        hist_budget = _history_token_budget(cfg.llm.model, system_prompt_tokens=sys_prompt_size)
        # Aktive Session
        session = sessions.get_active(agent_id) or (hasattr(runtime, '_agent_sessions') and None)
        session_msgs = len(session.messages) if session else 0
        return {
            "agent_id": agent_id,
            "identity": cfg.identity,
            "model": cfg.llm.model,
            "type": cfg.type,
            "execution_mode": cfg.execution_modes.default if cfg.execution_modes else "legacy",
            "tools": cfg.tools,
            "tools_count": len(cfg.tools),
            "token_budget": {
                "context_window": ctx_window,
                "system_prompt_estimate": sys_prompt_size,
                "reserve_floor": _RESERVE_TOKENS_FLOOR,
                "history_budget": hist_budget,
                "available_for_response": ctx_window - sys_prompt_size - hist_budget - _RESERVE_TOKENS_FLOOR,
            },
            "session": {
                "active": session is not None,
                "messages": session_msgs,
            },
            "status": runtime.status_all().get(agent_id, {}),
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

    @admin_router.get("/admin/update/stream")
    async def stream_update_log():
        """SSE-Stream: tailed das Update-Log + Status live."""
        from fastapi.responses import StreamingResponse
        import json as _json

        log_file = Path("/var/log/hydrahive-update.log")

        async def event_stream():
            sent_lines = 0
            done = False
            while not done:
                # Log-Zeilen lesen
                try:
                    if log_file.exists():
                        lines = log_file.read_text(errors="replace").splitlines()
                    else:
                        lines = []
                except Exception:
                    lines = []

                # Neue Zeilen senden
                for line in lines[sent_lines:]:
                    yield f"data: {_json.dumps({'line': line})}\n\n"
                sent_lines = len(lines)

                # Status prüfen
                status = _load_update_status()
                st = status.get("status", "")
                if st in ("ok", "error", ""):
                    # Fertig — finale Statusmeldung senden
                    yield f"data: {_json.dumps({'done': True, 'ok': st == 'ok', 'status': status})}\n\n"
                    done = True
                else:
                    await asyncio.sleep(1)

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
        cred_file = settings.admin_credentials
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
    def update_cleanup_config(req: CleanupConfigRequest):
        from .cleanup_service import _load_config, save_config
        cfg = _load_config()
        for k, v in req.model_dump(exclude_none=True).items():
            cfg[k] = v
        save_config(cfg)
        return {"updated": True, "config": cfg}

    # ── Smart Alerts (#374) ─────────────────────────────────────────

    @admin_router.get("/admin/alerts/config")
    def get_alerts_config():
        from .alert_service import _load_config as _lac
        return _lac()

    class AlertConfigRequest(BaseModel):
        enabled: bool | None = None
        check_interval_seconds: int | None = None
        disk_warn_pct: int | None = None
        disk_crit_pct: int | None = None
        heartbeat_max_age_seconds: int | None = None
        journal_error_threshold: int | None = None
        oauth_warn_days: int | None = None
        cooldown_minutes: int | None = None
        notify_users: list[str] | None = None

    @admin_router.put("/admin/alerts/config")
    def update_alerts_config(req: AlertConfigRequest):
        from .alert_service import _load_config as _lac, save_config as _sac
        cfg = _lac()
        for k, v in req.model_dump(exclude_none=True).items():
            cfg[k] = v
        _sac(cfg)
        return {"updated": True, "config": cfg}

    @admin_router.post("/admin/alerts/check")
    async def trigger_alert_check():
        from .alert_service import alert_service as _as
        result = await _as.run_now()
        return result

    # ── AutoDream ────────────────────────────────────────────────────

    @admin_router.get("/admin/dream/config")
    def get_dream_config():
        from .auto_dream import _load_dream_config
        return _load_dream_config()

    class DreamConfigRequest(BaseModel):
        enabled: bool | None = None
        min_hours: int | None = None
        min_sessions: int | None = None
        check_interval_seconds: int | None = None
        summary_model: str | None = None

    @admin_router.put("/admin/dream/config")
    def update_dream_config(req: DreamConfigRequest):
        from .auto_dream import _load_dream_config, save_dream_config
        cfg = _load_dream_config()
        for k, v in req.model_dump(exclude_none=True).items():
            cfg[k] = v
        save_dream_config(cfg)
        return {"updated": True, "config": cfg}

    @admin_router.post("/admin/dream/run")
    async def trigger_dream(agent_id: str = ""):
        from .auto_dream import auto_dream_service
        result = await auto_dream_service.run_now(agent_id=agent_id or None)
        return result

    @admin_router.get("/admin/dream/status")
    def get_dream_status():
        """Dream-Status aller Agenten."""
        from .auto_dream import _read_dream_state
        agents_dir = Path(agents_dir_str) if 'agents_dir_str' in dir() else settings.agents_dir
        statuses = []
        for agent_dir in sorted(settings.agents_dir.iterdir()):
            if not agent_dir.is_dir() or not (agent_dir / "agent.yaml").exists():
                continue
            state = _read_dream_state(agent_dir)
            transcripts_dir = agent_dir / "transcripts"
            transcript_count = len(list(transcripts_dir.glob("*.md"))) if transcripts_dir.exists() else 0
            last_dream = state.get("last_dream_at", 0)
            hours_since = (time.time() - last_dream) / 3600 if last_dream else None
            statuses.append({
                "agent_id": agent_dir.name,
                "last_dream_at": datetime.fromtimestamp(last_dream, tz=timezone.utc).isoformat() if last_dream else None,
                "hours_since_dream": round(hours_since, 1) if hours_since else None,
                "dream_count": state.get("dream_count", 0),
                "transcript_count": transcript_count,
                "last_sessions_reviewed": state.get("last_sessions_reviewed", 0),
            })
        return {"agents": statuses, "total": len(statuses)}
