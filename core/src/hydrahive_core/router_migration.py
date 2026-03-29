"""
router_migration.py — Migration API (#78)

POST /admin/migration/export           — Export erstellen + Download
POST /admin/migration/import           — Export-Archiv einspielen (Upload)
POST /admin/migration/transfer         — Direkter Server-zu-Server Transfer (SSE-Stream)
GET  /admin/migration/transfer/status  — Läuft gerade ein Transfer?
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path("/opt/hydrahive/scripts")
EXPORT_SCRIPT  = SCRIPTS_DIR / "hydrahive-export.sh"
IMPORT_SCRIPT  = SCRIPTS_DIR / "hydrahive-import.sh"
TRANSFER_SCRIPT = SCRIPTS_DIR / "hydrahive-transfer.sh"

# Laufender Transfer (nur einer gleichzeitig)
_transfer_lock = threading.Lock()
_transfer_running = False


def register_migration_routes(admin_router: APIRouter, *, require_admin, audit_log) -> None:

    @admin_router.post("/admin/migration/export")
    async def export_instance(
        include_amem: bool = False,
        _a: tuple = Depends(require_admin),
    ):
        """Export als verschlüsseltes Archiv erstellen und zum Download anbieten."""
        if not EXPORT_SCRIPT.exists():
            raise HTTPException(500, f"Export-Skript nicht gefunden: {EXPORT_SCRIPT}")

        with tempfile.NamedTemporaryFile(suffix=".tar.gz.enc", delete=False) as tmp:
            tmp_path = tmp.name

        cmd = ["sudo", "bash", str(EXPORT_SCRIPT), "--output", tmp_path]
        if include_amem:
            cmd.append("--include-amem")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            raise HTTPException(504, "Export-Timeout (>5 min)")
        except Exception as e:
            raise HTTPException(500, f"Export fehlgeschlagen: {e}")

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            Path(tmp_path).unlink(missing_ok=True)
            raise HTTPException(500, f"Export fehlgeschlagen: {err}")

        if not Path(tmp_path).exists():
            raise HTTPException(500, "Export-Archiv wurde nicht erstellt")

        audit_log("migration.export", details={"include_amem": include_amem})
        logger.info("Migration-Export erstellt: %s", tmp_path)

        filename = Path(tmp_path).name
        return FileResponse(
            tmp_path,
            media_type="application/octet-stream",
            filename=f"hydrahive-export.tar.gz.enc",
            background=None,
        )

    @admin_router.post("/admin/migration/import")
    async def import_instance(
        file: UploadFile = File(...),
        _a: tuple = Depends(require_admin),
    ):
        """Verschlüsseltes Export-Archiv hochladen und einspielen."""
        if not IMPORT_SCRIPT.exists():
            raise HTTPException(500, f"Import-Skript nicht gefunden: {IMPORT_SCRIPT}")

        with tempfile.NamedTemporaryFile(suffix=".tar.gz.enc", delete=False) as tmp:
            tmp_path = tmp.name
            content = await file.read()
            tmp.write(content)

        cmd = ["sudo", "bash", str(IMPORT_SCRIPT), "--input", tmp_path, "--force"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            Path(tmp_path).unlink(missing_ok=True)
            raise HTTPException(504, "Import-Timeout (>5 min)")
        except Exception as e:
            Path(tmp_path).unlink(missing_ok=True)
            raise HTTPException(500, f"Import fehlgeschlagen: {e}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()

        if proc.returncode != 0:
            raise HTTPException(500, f"Import fehlgeschlagen: {err or out}")

        audit_log("migration.import", details={"filename": file.filename})
        logger.info("Migration-Import abgeschlossen: %s", file.filename)
        return {"imported": True, "output": out}

    @admin_router.post("/admin/migration/transfer")
    async def transfer_to_server(
        target: str = Form(...),
        ssh_key: str = Form("/root/.ssh/id_ed25519"),
        ssh_port: int = Form(22),
        include_amem: bool = Form(False),
        _a: tuple = Depends(require_admin),
    ):
        """Server-zu-Server Transfer mit Live-Output-Stream."""
        global _transfer_running

        if not TRANSFER_SCRIPT.exists():
            raise HTTPException(500, f"Transfer-Skript nicht gefunden: {TRANSFER_SCRIPT}")

        with _transfer_lock:
            if _transfer_running:
                raise HTTPException(409, "Transfer läuft bereits")
            _transfer_running = True

        cmd = [
            "sudo", "bash", str(TRANSFER_SCRIPT),
            "--target", target,
            "--key", ssh_key,
            "--port", str(ssh_port),
        ]
        if include_amem:
            cmd.append("--include-amem")

        async def stream_output():
            global _transfer_running
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    yield f"data: {line.decode(errors='replace').rstrip()}\n\n"
                await proc.wait()
                rc = proc.returncode
                if rc == 0:
                    audit_log("migration.transfer", details={"target": target})
                    logger.info("Migration-Transfer abgeschlossen: %s", target)
                    yield "data: __DONE__\n\n"
                else:
                    yield f"data: __ERROR__ Transfer fehlgeschlagen (rc={rc})\n\n"
            except Exception as e:
                yield f"data: __ERROR__ {e}\n\n"
            finally:
                _transfer_running = False

        return StreamingResponse(
            stream_output(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @admin_router.get("/admin/migration/transfer/status")
    async def transfer_status(_a: tuple = Depends(require_admin)):
        return {"running": _transfer_running}
