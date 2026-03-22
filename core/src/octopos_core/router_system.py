from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class NetworkProfileRequest(BaseModel):
    profile: str


class GiteaConfigRequest(BaseModel):
    url: str = "http://127.0.0.1:3001"
    token: str = ""
    org: str = "octopos"
    webhook_secret: str = ""


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
    logger: logging.Logger,
) -> None:
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
                    except Exception:
                        return None

                def _float(v: str):
                    try:
                        return round(float(v), 1)
                    except Exception:
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
        status_file = "/var/run/octopos-update.json"
        log_file = "/var/log/octopos-update.log"
        p = Path(status_file)
        status = {}
        if p.exists():
            try:
                status = json.loads(p.read_text())
            except Exception:
                status = {"status": "unknown"}
        else:
            status = {"status": "never"}
        if status.get("status") == "running" and status.get("started_at"):
            try:
                started = datetime.fromisoformat(status["started_at"])
                if datetime.now(tz=timezone.utc) - started.astimezone(timezone.utc) > timedelta(minutes=10):
                    status["status"] = "ok"
                    status["stale"] = True
            except Exception:
                pass
        try:
            lines = Path(log_file).read_text(errors="replace").splitlines()
            status["log_tail"] = lines[-20:]
        except Exception:
            status["log_tail"] = []
        return status

    @admin_router.post("/admin/update/trigger")
    async def trigger_update():
        asyncio.create_task(run_self_update(pusher="admin-manual", commits=0))
        return {"status": "deploying", "message": "Update gestartet — GET /admin/update/status für Status"}

    @admin_router.get("/gitea/config")
    def get_gitea_config():
        p = Path(gitea_config_file)
        if not p.exists():
            return {"url": "http://127.0.0.1:3001", "org": "octopos", "webhook_secret": "", "has_token": False, "token_masked": ""}
        cfg = json.loads(p.read_text(encoding="utf-8"))
        token = cfg.get("token", "")
        return {
            "url": cfg.get("url", "http://127.0.0.1:3001"),
            "org": cfg.get("org", "octopos"),
            "webhook_secret": cfg.get("webhook_secret", ""),
            "has_token": bool(token),
            "token_masked": token[:8] + "..." + token[-4:] if token else "",
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
