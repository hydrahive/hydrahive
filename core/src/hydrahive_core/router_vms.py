"""
router_vms.py — VM-Manager CRUD + ISO-Management (#895/#898)
================================================================
Alle 12 Endpoints für VM-Manager + ISOManager.
admin-only via require_admin dependency.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .disk_import_manager import DiskImportManager
from .iso_manager import ISOManager
from .settings import settings
from .vm_manager import VMManager
from .vnc_proxy import VNCProxy

logger = logging.getLogger(__name__)

# ── Singleton-Manager ──────────────────────────────────────────────────────────
_vm_manager: VMManager | None = None
_iso_manager: ISOManager | None = None
_disk_import_manager: DiskImportManager | None = None


def _get_vm_manager() -> VMManager:
    global _vm_manager
    if _vm_manager is None:
        _vm_manager = VMManager(
            storage_base=Path(settings.vm_storage_base),
            db_path=Path(settings.vm_storage_base) / "vms.db",
        )
    return _vm_manager


def _get_iso_manager() -> ISOManager:
    global _iso_manager
    if _iso_manager is None:
        _iso_manager = ISOManager(
            iso_dir=Path(settings.vm_storage_base) / "isos",
            max_size_gb=settings.vm_iso_max_size_gb,
            max_count=settings.vm_max_isos,
        )
    return _iso_manager


def _get_disk_import_manager() -> DiskImportManager:
    global _disk_import_manager
    if _disk_import_manager is None:
        _disk_import_manager = DiskImportManager(
            import_dir=Path(settings.vm_storage_base) / "disk-imports",
            max_size_gb=getattr(settings, "vm_import_max_size_gb", 500),
        )
    return _disk_import_manager


# ── Request/Response Models ─────────────────────────────────────────────────────
class VMCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    cpu: int = Field(2, ge=1, le=16)
    ram_mb: int = Field(2048, ge=512, le=65536)
    disk_gb: int = Field(20, ge=5, le=500)
    iso_file: str | None = None
    import_job_id: str | None = None
    network_mode: str = Field("user", pattern=r"^(user|bridge)$")
    bridge_iface: str = Field("br0", pattern=r"^[a-zA-Z0-9_\-]{1,15}$")


class VMUpdateRequest(BaseModel):
    cpu: int | None = Field(None, ge=1, le=16)
    ram_mb: int | None = Field(None, ge=512, le=65536)
    disk_gb: int | None = Field(None, ge=5, le=500)
    network_mode: str | None = Field(None, pattern=r"^(user|bridge)$")
    bridge_iface: str | None = Field(None, pattern=r"^[a-zA-Z0-9_\-]{1,15}$")


class VMActionResponse(BaseModel):
    vm_id: str
    status: str
    message: str


class VNCInfoResponse(BaseModel):
    vm_id: str
    websocket_url: str
    token: str
    vnc_port: int
    websockify_ok: bool


# ── Routes ─────────────────────────────────────────────────────────────────────
def register_vm_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    require_auth,
    require_admin,
) -> None:
    _admin = Depends(require_admin)

    # ── VM CRUD ───────────────────────────────────────────────────────────────

    @admin_router.get("/admin/vms")
    async def list_vms(_=_admin):
        """Alle VMs listen."""
        mgr = _get_vm_manager()
        vms = await mgr.list_vms()
        return [v.to_dict() for v in vms]

    @admin_router.post("/admin/vms")
    async def create_vm(req: VMCreateRequest, _=_admin):
        """Neue VM erstellen."""
        if not settings.vm_enabled:
            raise HTTPException(503, "VM-Manager ist deaktiviert")
        mgr = _get_vm_manager()
        vms = await mgr.list_vms()
        if len(vms) >= settings.vm_max_count:
            raise HTTPException(429, f"VM-Limit erreicht ({settings.vm_max_count})")
        if req.cpu > settings.vm_max_cpu:
            raise HTTPException(400, f"CPU-Limit überschritten (max {settings.vm_max_cpu})")
        if req.ram_mb > settings.vm_max_ram_mb:
            raise HTTPException(400, f"RAM-Limit überschritten (max {settings.vm_max_ram_mb} MB)")
        if req.disk_gb > settings.vm_max_disk_gb:
            raise HTTPException(400, f"Disk-Limit überschritten (max {settings.vm_max_disk_gb} GB)")
        try:
            free_bytes = shutil.disk_usage(settings.vm_storage_base).free
            required_bytes = req.disk_gb * 1024**3
            if free_bytes < required_bytes:
                raise HTTPException(400, f"Nicht genug Disk-Space ({free_bytes/1024**3:.1f} GB frei, {req.disk_gb} GB benötigt)")
        except HTTPException:
            raise
        except OSError as e:
            logger.warning("disk_space_check fehlgeschlagen: %s", e)
        # ISO-Pfad validieren: nur bekannte ISO-Dateinamen zulassen
        iso_path: str | None = None
        if req.iso_file:
            iso_mgr = _get_iso_manager()
            try:
                iso_path = str(iso_mgr.get_iso_path(req.iso_file))
            except ValueError as e:
                raise HTTPException(400, str(e))
        # Import-Job validieren wenn angegeben
        import_disk_path: str | None = None
        if req.import_job_id:
            import_mgr = _get_disk_import_manager()
            import_job = import_mgr.get_job(req.import_job_id)
            if not import_job:
                raise HTTPException(404, f"Import-Job nicht gefunden: {req.import_job_id}")
            if import_job.status == "error":
                raise HTTPException(400, f"Konvertierung fehlgeschlagen: {import_job.error}")
            if import_job.status != "done":
                raise HTTPException(409, f"Konvertierung noch nicht abgeschlossen (status={import_job.status})")
            import_disk_path = import_job.output_path
        try:
            vm = await mgr.create_vm(
                name=req.name,
                cpu=req.cpu,
                ram_mb=req.ram_mb,
                disk_gb=req.disk_gb,
                iso_file=iso_path,
                owner="admin",
                import_disk_path=import_disk_path,
                network_mode=req.network_mode,
                bridge_iface=req.bridge_iface,
            )
            return vm.to_dict()
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError:
            raise HTTPException(503, "qemu-img nicht gefunden — bitte QEMU installieren (sudo apt-get install qemu-utils qemu-system-x86_64)")
        except RuntimeError as e:
            raise HTTPException(500, str(e))

    # ── ISO (statisch vor {vm_id}, sonst Konflikt!) ───────────────────────────

    @admin_router.get("/admin/vms/isos")
    async def list_isos(_=_admin):
        """Alle ISOs listen."""
        mgr = _get_iso_manager()
        return mgr.list_isos()

    @admin_router.post("/admin/vms/isos/upload")
    async def upload_iso(file: UploadFile = File(...), _=_admin):
        """ISO-Datei hochladen (Streaming, max size limitiert)."""
        mgr = _get_iso_manager()

        async def _chunks():
            while True:
                data = await file.read(65536)
                if not data:
                    break
                yield data

        try:
            info = await mgr.save_iso(file.filename, _chunks())
            return {"filename": info.filename, "size_human": info.size_human, "path": info.path}
        except ValueError as e:
            msg = str(e)
            if "existiert bereits" in msg:
                raise HTTPException(409, msg)
            elif "ISO-Größe" in msg or "Max-ISO" in msg:
                raise HTTPException(400, msg)
            else:
                raise HTTPException(400, msg)
        except Exception as e:
            logger.error("ISO-Upload fehlgeschlagen: %s", e)
            raise HTTPException(500, f"Upload fehlgeschlagen: {e}")

    @admin_router.delete("/admin/vms/isos/{filename}")
    async def delete_iso(filename: str, _=_admin):
        """ISO-Datei löschen."""
        mgr = _get_iso_manager()
        try:
            mgr.delete_iso(filename)
            return {"deleted": filename}
        except ValueError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            logger.error("ISO-Delete fehlgeschlagen: %s", e)
            raise HTTPException(500, str(e))

    # ── Disk-Import (statisch vor {vm_id}!) ──────────────────────────────────

    @admin_router.post("/admin/vms/import/upload")
    async def import_disk_upload(file: UploadFile = File(...), _=_admin):
        """Disk-Image hochladen und QCOW2-Konvertierung starten."""
        mgr = _get_disk_import_manager()

        async def _chunks():
            while True:
                data = await file.read(65536)
                if not data:
                    break
                yield data

        try:
            job = await mgr.start_import(file.filename, _chunks())
            return {"job_id": job.job_id, "filename": job.filename, "size_bytes": job.size_bytes}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error("Disk-Import fehlgeschlagen: %s", e)
            raise HTTPException(500, str(e))

    class ImportFromPathRequest(BaseModel):
        path: str = Field(..., description="Absoluter Pfad auf dem Server")

    @admin_router.post("/admin/vms/import/from-path")
    async def import_disk_from_path(req: ImportFromPathRequest, _=_admin):
        """Disk-Image das bereits auf dem Server liegt importieren (kein Upload nötig)."""
        mgr = _get_disk_import_manager()
        try:
            job = await mgr.start_import_from_path(req.path)
            return {"job_id": job.job_id, "filename": job.filename, "size_bytes": job.size_bytes}
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error("Disk-Import-from-path fehlgeschlagen: %s", e)
            raise HTTPException(500, str(e))

    @admin_router.get("/admin/vms/import/{job_id}/status")
    async def import_disk_status(job_id: str, _=_admin):
        """Konvertierungs-Status abfragen."""
        mgr = _get_disk_import_manager()
        job = mgr.get_job(job_id)
        if not job:
            raise HTTPException(404, f"Import-Job nicht gefunden: {job_id}")
        return {
            "job_id": job.job_id,
            "filename": job.filename,
            "status": job.status,
            "progress_pct": job.progress_pct,
            "error": job.error,
            "size_bytes": job.size_bytes,
        }

    @admin_router.delete("/admin/vms/import/{job_id}")
    async def import_disk_cancel(job_id: str, _=_admin):
        """Laufenden Import abbrechen und Dateien löschen."""
        mgr = _get_disk_import_manager()
        if not mgr.get_job(job_id):
            raise HTTPException(404, f"Import-Job nicht gefunden: {job_id}")
        mgr.cancel_job(job_id)
        return {"cancelled": job_id}

    # ── VM CRUD (dynamisch, nach statischen Routen) ───────────────────────────

    @admin_router.get("/admin/vms/{vm_id}")
    async def get_vm(vm_id: str, _=_admin):
        """Einzelne VM laden."""
        mgr = _get_vm_manager()
        vm = await mgr.get_vm(vm_id)
        if not vm:
            raise HTTPException(404, f"VM nicht gefunden: {vm_id}")
        return vm.to_dict()

    @admin_router.patch("/admin/vms/{vm_id}")
    async def update_vm(vm_id: str, req: VMUpdateRequest, _=_admin):
        """VM-Konfiguration ändern (nur wenn gestoppt)."""
        mgr = _get_vm_manager()
        try:
            vm = await mgr.update_vm(
                vm_id,
                cpu=req.cpu, ram_mb=req.ram_mb, disk_gb=req.disk_gb,
                network_mode=req.network_mode, bridge_iface=req.bridge_iface,
            )
            return vm.to_dict()
        except FileNotFoundError as e:
            raise HTTPException(503, str(e))
        except ValueError as e:
            status_code = 409 if "gestoppt" in str(e) or "verkleinert" in str(e) else 404
            raise HTTPException(status_code, str(e))
        except RuntimeError as e:
            raise HTTPException(500, str(e))

    @admin_router.delete("/admin/vms/{vm_id}")
    async def delete_vm(vm_id: str, _=_admin):
        """VM löschen (stop + Verzeichnis + DB)."""
        mgr = _get_vm_manager()
        try:
            await mgr.delete_vm(vm_id)
            return VMActionResponse(vm_id=vm_id, status="deleted", message=f"VM {vm_id} gelöscht")
        except ValueError as e:
            raise HTTPException(404, str(e))
        except RuntimeError as e:
            raise HTTPException(500, str(e))

    # ── VM Actions ─────────────────────────────────────────────────────────────

    @admin_router.post("/admin/vms/{vm_id}/start")
    async def start_vm(vm_id: str, _=_admin):
        """VM starten."""
        mgr = _get_vm_manager()
        try:
            vm = await mgr.start_vm(vm_id)
            return VMActionResponse(vm_id=vm_id, status=vm.status, message=f"VM {vm_id} gestartet")
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError:
            raise HTTPException(503, "qemu-system-x86_64 nicht gefunden — bitte QEMU installieren (sudo apt-get install qemu-system-x86)")
        except RuntimeError as e:
            raise HTTPException(500, str(e))

    @admin_router.post("/admin/vms/{vm_id}/stop")
    async def stop_vm(vm_id: str, _=_admin):
        """VM graceful stoppen (SIGTERM)."""
        mgr = _get_vm_manager()
        try:
            vm = await mgr.stop_vm(vm_id, force=False)
            return VMActionResponse(vm_id=vm_id, status=vm.status, message=f"VM {vm_id} gestoppt")
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(500, str(e))

    @admin_router.post("/admin/vms/{vm_id}/poweroff")
    async def poweroff_vm(vm_id: str, _=_admin):
        """VM hart ausschalten (SIGKILL)."""
        mgr = _get_vm_manager()
        try:
            vm = await mgr.stop_vm(vm_id, force=True)
            return VMActionResponse(vm_id=vm_id, status=vm.status, message=f"VM {vm_id} zwangsbeendet")
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(500, str(e))

    @admin_router.get("/admin/vms/{vm_id}/status")
    async def vm_status(vm_id: str, _=_admin):
        """VM-Status refreshed zurückgeben."""
        mgr = _get_vm_manager()
        try:
            vm = await mgr.refresh_status(vm_id)
            return vm.to_dict()
        except ValueError as e:
            raise HTTPException(404, str(e))
        except RuntimeError as e:
            raise HTTPException(500, str(e))

    # ── VNC ───────────────────────────────────────────────────────────────────

    @admin_router.get("/admin/vms/{vm_id}/log")
    async def get_vm_log(vm_id: str, lines: int = 100, _=_admin):
        """Letzte N Zeilen des QEMU-Logs zurückgeben."""
        mgr = _get_vm_manager()
        vm = await mgr.get_vm(vm_id)
        if not vm:
            raise HTTPException(404, f"VM nicht gefunden: {vm_id}")
        log_path = Path(settings.vm_storage_base) / "vms" / vm_id / "qemu.log"
        if not log_path.exists():
            return {"vm_id": vm_id, "lines": [], "size_bytes": 0}
        try:
            text = log_path.read_text(errors="replace")
            all_lines = text.splitlines()
            tail = all_lines[-min(lines, len(all_lines)):]
            return {"vm_id": vm_id, "lines": tail, "size_bytes": log_path.stat().st_size}
        except OSError as e:
            raise HTTPException(500, f"Log nicht lesbar: {e}")

    @admin_router.get("/admin/vms/{vm_id}/vnc")
    async def get_vnc_info(vm_id: str, request: Request, _=_admin):
        """VNC-Verbindungsinfo für VM (nur wenn running)."""
        mgr = _get_vm_manager()
        vm = await mgr.get_vm(vm_id)
        if not vm:
            raise HTTPException(404, f"VM nicht gefunden: {vm_id}")
        if vm.status != "running":
            raise HTTPException(409, f"VM ist nicht running (status={vm.status})")
        # Token kommt aus DB (vm.vnc_token) — kein neues VNCProxy-Objekt nötig,
        # dessen In-Memory-Map leer wäre.
        token_str = vm.vnc_token or ""
        host = request.headers.get("host", "localhost").split(":")[0]
        # nginx setzt kein X-Forwarded-Proto; HydraHive läuft immer mit TLS →
        # default wss, nur wenn Proto explizit "http" → ws.
        proto = request.headers.get("x-forwarded-proto", "https")
        ws_scheme = "wss" if proto != "http" else "ws"
        ws_url = f"{ws_scheme}://{host}/ws/vnc/?token={token_str}"
        ws_ok = VNCProxy.check_websockify()
        return VNCInfoResponse(
            vm_id=vm_id,
            websocket_url=ws_url,
            token=token_str,
            vnc_port=vm.vnc_port or 0,
            websockify_ok=ws_ok,
        )
