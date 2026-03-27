"""
router_searxng.py — SearXNG Admin-Endpoints (#51)

GET  /admin/searxng/status  → Service-Status, Version, konfigurierte Engines
POST /admin/searxng/test    → Test-Suche aus der Console (body: {query, engines?})
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

import json as _json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


async def _curl_get(url: str, timeout: int = 20) -> tuple[int, bytes]:
    """GET via curl-Subprocess — umgeht Python-HTTP-Library-Probleme mit SearXNG."""
    proc = await asyncio.create_subprocess_exec(
        "curl", "-s", "-o", "-", "-w", "\n__STATUS__%{http_code}",
        "--max-time", str(timeout), "--noproxy", "*", "-L", url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    # Letzten Marker extrahieren
    marker = b"\n__STATUS__"
    idx = stdout.rfind(marker)
    if idx >= 0:
        status = int(stdout[idx + len(marker):])
        body = stdout[:idx]
    else:
        status = 0
        body = stdout
    return status, body

logger = logging.getLogger(__name__)

SEARXNG_URL      = "http://127.0.0.1:8888"
SEARXNG_CONF     = Path("/etc/searxng/settings.yml")
SEARXNG_DIR      = Path("/opt/searxng")
INSTALL_SCRIPT   = Path("/opt/hydrahive/installer/modules/14_searxng.sh")


class SearchTestRequest(BaseModel):
    query:   str
    engines: str = ""


def register_searxng_routes(admin_router: APIRouter, *, require_admin) -> None:

    @admin_router.get("/admin/searxng/status")
    async def searxng_status(_a=Depends(require_admin)):
        # 1. systemd-Status
        service_active = False
        service_uptime = ""
        try:
            r = subprocess.run(
                ["systemctl", "show", "searxng",
                 "--property=ActiveState,ActiveEnterTimestamp"],
                capture_output=True, text=True, timeout=5,
            )
            props = {
                k: v for k, v in
                (line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
            }
            service_active = props.get("ActiveState") == "active"
            service_uptime = props.get("ActiveEnterTimestamp", "")
        except Exception as e:
            logger.debug("systemctl show searxng: %s", e)

        # 2. HTTP-Erreichbarkeit + JSON-Format-Check
        http_ok = False
        json_ok = False
        try:
            status, _ = await _curl_get(f"{SEARXNG_URL}/", timeout=3)
            http_ok = status in (200, 302)
        except Exception:
            pass
        if http_ok:
            try:
                status, _ = await _curl_get(f"{SEARXNG_URL}/search?q=ping&format=json", timeout=8)
                json_ok = status == 200
            except Exception:
                pass

        # 3. Version aus git tag
        version = None
        try:
            r2 = subprocess.run(
                ["git", "-C", str(SEARXNG_DIR), "log", "--oneline", "-1"],
                capture_output=True, text=True, timeout=5,
            )
            version = r2.stdout.strip()[:40] or None
        except Exception:
            pass

        # 4. Konfigurierte Engines aus settings.yml
        engines: list[str] = []
        try:
            import yaml as _yaml
            cfg = _yaml.safe_load(SEARXNG_CONF.read_text(encoding="utf-8"))
            engines = [e["name"] for e in (cfg or {}).get("engines", []) if "name" in e]
        except Exception:
            pass

        def _safe_exists(p: Path) -> bool:
            try:
                return p.exists()
            except OSError:
                return False

        return {
            "installed":      _safe_exists(SEARXNG_DIR.joinpath(".git")),
            "service_active": service_active,
            "service_uptime": service_uptime,
            "http_ok":        http_ok,
            "json_ok":        json_ok,
            "url":            SEARXNG_URL,
            "version":        version,
            "engines":        engines,
            "config_exists":  _safe_exists(SEARXNG_CONF),
        }

    @admin_router.post("/admin/searxng/install")
    async def searxng_install(_a=Depends(require_admin)):
        if not INSTALL_SCRIPT.exists():
            from fastapi import HTTPException
            raise HTTPException(500, "Installer-Script nicht gefunden: Bitte erst ein Update durchführen.")

        async def stream():
            import os as _os
            env = {**_os.environ, "DEBIAN_FRONTEND": "noninteractive"}
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "/bin/bash", str(INSTALL_SCRIPT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            import json as _j
            assert proc.stdout
            async for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").rstrip()
                yield f"data: {_j.dumps({'line': text})}\n\n"
            rc = await proc.wait()
            yield f"data: {_j.dumps({'done': True, 'ok': rc == 0})}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @admin_router.post("/admin/searxng/test")
    async def searxng_test(req: SearchTestRequest, _a=Depends(require_admin)):
        from urllib.parse import urlencode

        if not req.query.strip():
            return {"error": "Leere Suchanfrage", "results": []}

        params: dict = {"q": req.query, "format": "json"}
        if req.engines:
            params["engines"] = req.engines

        url = f"{SEARXNG_URL}/search?{urlencode(params)}"
        try:
            status, body = await _curl_get(url, timeout=30)
            if status != 200:
                return {"error": f"HTTP {status}", "detail": body[:200].decode("utf-8", errors="replace"), "results": []}
            data = _json.loads(body)
        except Exception as e:
            detail = str(e) or type(e).__name__
            return {"error": f"SearXNG nicht erreichbar: {detail}", "results": []}

        results = [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "snippet": r.get("content", ""),
                "engine":  r.get("engine", ""),
            }
            for r in data.get("results", [])
        ]
        return {
            "query":       req.query,
            "total":       data.get("number_of_results", len(results)),
            "results":     results[:20],
            "suggestions": data.get("suggestions", []),
        }
