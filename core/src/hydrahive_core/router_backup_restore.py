from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException


_BACKUP_NAME_RE = r"^hydrahive-backup-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.tar\.gz$"


def _list_backups(backup_dir: Path) -> list[dict]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for path in sorted(backup_dir.glob("hydrahive-backup-*.tar.gz"), reverse=True):
        stat = path.stat()
        result.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )
    return result


def register_backup_restore_routes(
    admin_router: APIRouter,
    *,
    require_admin,
    backup_dir: Path,
    backup_sources: list[tuple[str, str]],
    audit_log,
    logger: logging.Logger,
) -> None:
    @admin_router.get("/admin/backups")
    def list_backups():
        return {"backups": _list_backups(backup_dir)}

    @admin_router.post("/admin/backup", status_code=201)
    def create_backup():
        import tarfile as _tar

        backup_dir.mkdir(parents=True, exist_ok=True)
        name = f"hydrahive-backup-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.tar.gz"
        dest = backup_dir / name
        _SKIP_PARTS = {"tls", "files", ".sessions", "whatsapp-sessions"}

        def _backup_filter(ti: _tar.TarInfo) -> _tar.TarInfo | None:
            parts = set(Path(ti.name).parts)
            if parts & _SKIP_PARTS:
                return None
            if any(p.startswith("proj_") for p in parts):
                return None
            return ti

        with _tar.open(dest, "w:gz") as tf:
            for src, arcname in backup_sources:
                src_path = Path(src)
                if not src_path.exists():
                    continue
                try:
                    tf.add(src_path, arcname=arcname, filter=_backup_filter)
                except PermissionError as e:
                    logger.warning("Backup: Datei übersprungen (keine Berechtigung): %s", e)
        size = dest.stat().st_size
        audit_log("backup.create", target=name, details={"size": size})
        logger.info("Backup erstellt: %s (%d bytes)", name, size)
        return {
            "created": True,
            "name": name,
            "size": size,
            "created_at": datetime.now().isoformat(),
        }

    @admin_router.get("/admin/backups/{name}/download")
    def download_backup(name: str):
        import re as _re
        from fastapi.responses import FileResponse

        if not _re.match(_BACKUP_NAME_RE, name):
            raise HTTPException(400, "Ungültiger Backup-Name")
        path = (backup_dir / name).resolve()
        if not str(path).startswith(str(backup_dir.resolve())):
            raise HTTPException(403, "Zugriff verweigert")
        if not path.exists():
            raise HTTPException(404, "Backup nicht gefunden")
        return FileResponse(path, media_type="application/gzip", filename=name)

    @admin_router.delete("/admin/backups/{name}")
    def delete_backup(name: str):
        import re as _re

        if not _re.match(_BACKUP_NAME_RE, name):
            raise HTTPException(400, "Ungültiger Backup-Name")
        path = backup_dir / name
        if not path.exists():
            raise HTTPException(404, "Backup nicht gefunden")
        path.unlink()
        audit_log("backup.delete", target=name)
        return {"deleted": True, "name": name}

    @admin_router.post("/admin/restore/{name}")
    def restore_backup(name: str, _a: tuple = Depends(require_admin)):
        import re as _re
        import shutil as _sh
        import subprocess as _sub
        import tarfile as _tar
        import tempfile as _tmp
        import threading as _thr

        if not _re.match(_BACKUP_NAME_RE, name):
            raise HTTPException(400, "Ungültiger Backup-Name")
        path = backup_dir / name
        if not path.exists():
            raise HTTPException(404, "Backup nicht gefunden")

        with _tmp.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp).resolve()
            with _tar.open(path, "r:gz") as tf:
                # Sicherheitsprüfung: kein Path-Traversal aus dem Archiv heraus
                for member in tf.getmembers():
                    member_path = (tmp_path / member.name).resolve()
                    if not str(member_path).startswith(str(tmp_path)):
                        raise HTTPException(400, f"Unsicheres Archiv: verdächtiger Pfad '{member.name}'")
                tf.extractall(tmp_path)

            src_etc = tmp_path / "etc-hydrahive"
            if src_etc.exists():
                for file_path in src_etc.iterdir():
                    _sh.copy2(file_path, Path("/etc/hydrahive") / file_path.name)

            src_agents = tmp_path / "agents"
            if src_agents.exists():
                _sh.copytree(src_agents, Path("/agents"), dirs_exist_ok=True)

            src_projects = tmp_path / "projects"
            if src_projects.exists():
                for project_dir in src_projects.iterdir():
                    if project_dir.is_dir():
                        dest_proj = Path("/projects") / project_dir.name
                        dest_proj.mkdir(exist_ok=True)
                        for item in project_dir.iterdir():
                            if item.name != "files":
                                dst = dest_proj / item.name
                                if item.is_dir():
                                    _sh.copytree(item, dst, dirs_exist_ok=True)
                                else:
                                    _sh.copy2(item, dst)

        audit_log("backup.restore", target=name)
        logger.info("Restore abgeschlossen: %s — starte Service neu", name)

        def _restart():
            import time as _time

            _time.sleep(1)
            _sub.run(["systemctl", "restart", "hydrahive-core"], check=False)

        _thr.Thread(target=_restart, daemon=True).start()
        return {"restored": True, "name": name, "restarting": True}
