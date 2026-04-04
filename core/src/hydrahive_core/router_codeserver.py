"""
router_codeserver.py — Code-Server Admin-Endpoints

GET  /admin/codeserver/status  → Service-Status, Version, URL
POST /admin/codeserver/install → Streaming-Install via modules/15_codeserver.sh
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

CODESERVER_PORT  = 8766
CODESERVER_DIR   = Path("/opt/hydrahive/.config/code-server")
INSTALL_SCRIPT   = Path("/opt/hydrahive/installer/modules/15_codeserver.sh")
CREDENTIALS_FILE = Path("/etc/hydrahive/admin_credentials")


def _safe_exists(p: Path) -> bool:
    try:
        return p.exists()
    except Exception:
        return False


def register_codeserver_routes(admin_router: APIRouter, *, require_admin) -> None:

    @admin_router.get("/admin/codeserver/status")
    async def codeserver_status(_a=Depends(require_admin)):
        # systemd-Status
        service_active = False
        try:
            r = await asyncio.to_thread(lambda: subprocess.run(
                ["systemctl", "show", "hydrahive-codeserver",
                 "--property=ActiveState"],
                capture_output=True, text=True, timeout=5,
            ))
            props = {
                k: v for k, v in
                (line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
            }
            service_active = props.get("ActiveState") == "active"
        except Exception as e:
            logger.debug("systemctl show hydrahive-codeserver: %s", e)

        # Versionslabel aus Binary
        CS_BIN = Path("/opt/codeserver/bin/code-server")
        version = ""
        try:
            r2 = await asyncio.to_thread(lambda: subprocess.run(
                [str(CS_BIN), "--version"],
                capture_output=True, text=True, timeout=5,
            ))
            version = r2.stdout.strip().splitlines()[0] if r2.returncode == 0 else ""
        except Exception:
            pass

        installed = _safe_exists(CS_BIN)

        # Passwort aus admin_credentials lesen
        password = ""
        try:
            for line in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
                if line.startswith("codeserver_password="):
                    password = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass

        return {
            "installed":       installed,
            "service_active":  service_active,
            "version":         version,
            "port":            CODESERVER_PORT,
            "url":             "/code/",
            "password":        password or None,
        }

    @admin_router.post("/admin/codeserver/install")
    async def codeserver_install(_a=Depends(require_admin)):
        if not INSTALL_SCRIPT.exists():
            from fastapi import HTTPException
            raise HTTPException(500, "Installer-Script nicht gefunden: Bitte erst ein Update durchführen.")

        async def stream():
            import os as _os
            import json as _j
            env = {**_os.environ, "DEBIAN_FRONTEND": "noninteractive"}
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "/bin/bash", str(INSTALL_SCRIPT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
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
