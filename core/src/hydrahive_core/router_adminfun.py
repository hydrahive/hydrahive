"""
router_adminfun.py — AdminFun Music-Player (MP3-Upload + Streaming)

Eastery: Till hört beim Coden Vivaldi Techno. AdminFun ist ein Floating
Music-Player im Console-UI der MP3s aus /etc/hydrahive/adminfun/ abspielt
und mit Web Audio API Beat-synchrone CSS-Animations triggert.

Endpoints (alle Admin-only):
  GET    /admin/adminfun/settings          Toggle + aktueller Track
  PUT    /admin/adminfun/settings          Toggle setzen
  GET    /admin/adminfun/tracks            Track-Liste
  POST   /admin/adminfun/upload            MP3 hochladen (multipart/form-data)
  DELETE /admin/adminfun/tracks/{name}     Track löschen
  GET    /admin/adminfun/stream/{name}     MP3 streamen (auth via Header oder Query-Token)
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel


_ADMINFUN_DIR = Path("/etc/hydrahive/adminfun")
_ADMINFUN_STATE = _ADMINFUN_DIR / ".state.json"
_ALLOWED_EXT = {".mp3", ".ogg", ".wav", ".m4a", ".opus"}
_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB pro File
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._\- ]+$")


class AdminFunSettingsRequest(BaseModel):
    enabled: bool | None = None
    volume: float | None = None          # 0.0 – 1.0
    current_track: str | None = None     # Dateiname
    sensitivity: float | None = None     # 0.5 – 2.0 für Beat-Detection


def _load_state() -> dict:
    if not _ADMINFUN_STATE.exists():
        return {"enabled": False, "volume": 0.5, "current_track": "", "sensitivity": 1.0}
    try:
        return json.loads(_ADMINFUN_STATE.read_text())
    except Exception:
        return {"enabled": False, "volume": 0.5, "current_track": "", "sensitivity": 1.0}


def _save_state(state: dict) -> None:
    _ADMINFUN_DIR.mkdir(parents=True, exist_ok=True)
    _ADMINFUN_STATE.write_text(json.dumps(state, indent=2))


def _list_tracks() -> list[dict]:
    _ADMINFUN_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for p in sorted(_ADMINFUN_DIR.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() not in _ALLOWED_EXT:
            continue
        try:
            st = p.stat()
            result.append({
                "name": p.name,
                "size_bytes": st.st_size,
                "modified_at": int(st.st_mtime),
            })
        except Exception:
            continue
    return result


def register_adminfun_routes(
    admin_router: APIRouter,
    *,
    audit_log,
    logger: logging.Logger,
) -> None:

    @admin_router.get("/admin/adminfun/settings")
    def adminfun_settings():
        state = _load_state()
        return {
            "enabled": bool(state.get("enabled", False)),
            "volume": float(state.get("volume", 0.5)),
            "current_track": state.get("current_track", ""),
            "sensitivity": float(state.get("sensitivity", 1.0)),
        }

    @admin_router.put("/admin/adminfun/settings")
    def adminfun_update_settings(req: AdminFunSettingsRequest):
        state = _load_state()
        changed = False
        if req.enabled is not None:
            state["enabled"] = bool(req.enabled)
            changed = True
        if req.volume is not None:
            state["volume"] = max(0.0, min(1.0, float(req.volume)))
            changed = True
        if req.current_track is not None:
            state["current_track"] = req.current_track.strip()
            changed = True
        if req.sensitivity is not None:
            state["sensitivity"] = max(0.5, min(2.0, float(req.sensitivity)))
            changed = True
        if changed:
            _save_state(state)
            audit_log("adminfun.settings", details={k: v for k, v in req.model_dump(exclude_none=True).items()})
        return state

    @admin_router.get("/admin/adminfun/tracks")
    def adminfun_list_tracks():
        return {"tracks": _list_tracks()}

    @admin_router.post("/admin/adminfun/upload")
    async def adminfun_upload(file: UploadFile = File(...)):
        filename = (file.filename or "").strip()
        if not filename:
            raise HTTPException(400, "Dateiname fehlt")

        # Dateiname säubern
        base = Path(filename).name
        if not _SAFE_NAME_RE.match(base):
            raise HTTPException(400, "Dateiname enthält ungültige Zeichen (erlaubt: A-Z a-z 0-9 . _ - Leerzeichen)")

        ext = Path(base).suffix.lower()
        if ext not in _ALLOWED_EXT:
            raise HTTPException(400, f"Dateiformat {ext} nicht erlaubt. Erlaubt: {sorted(_ALLOWED_EXT)}")

        _ADMINFUN_DIR.mkdir(parents=True, exist_ok=True)
        target = _ADMINFUN_DIR / base

        # Streaming-Upload mit Size-Check
        size = 0
        try:
            with target.open("wb") as fout:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _MAX_SIZE_BYTES:
                        fout.close()
                        target.unlink(missing_ok=True)
                        raise HTTPException(413, f"Datei zu groß (max {_MAX_SIZE_BYTES // (1024*1024)} MB)")
                    fout.write(chunk)
            target.chmod(0o644)
        except HTTPException:
            raise
        except Exception as e:
            target.unlink(missing_ok=True)
            logger.exception("AdminFun Upload fehlgeschlagen: %s", e)
            raise HTTPException(500, f"Upload fehlgeschlagen: {e}")

        audit_log("adminfun.upload", details={"filename": base, "size": size})
        logger.info("AdminFun Upload: %s (%d bytes)", base, size)
        return {"uploaded": True, "name": base, "size_bytes": size}

    @admin_router.delete("/admin/adminfun/tracks/{name}")
    def adminfun_delete_track(name: str):
        base = Path(name).name
        if not _SAFE_NAME_RE.match(base):
            raise HTTPException(400, "Ungültiger Dateiname")
        target = _ADMINFUN_DIR / base
        if not target.exists() or not target.is_file():
            raise HTTPException(404, "Track nicht gefunden")
        try:
            target.unlink()
        except Exception as e:
            raise HTTPException(500, f"Löschen fehlgeschlagen: {e}")

        # Falls aktuell spielender Track gelöscht wurde: State aufräumen
        state = _load_state()
        if state.get("current_track") == base:
            state["current_track"] = ""
            _save_state(state)

        audit_log("adminfun.delete", details={"filename": base})
        return {"deleted": True, "name": base}

    @admin_router.get("/admin/adminfun/stream/{name}")
    def adminfun_stream(name: str, request: Request):
        base = Path(name).name
        if not _SAFE_NAME_RE.match(base):
            raise HTTPException(400, "Ungültiger Dateiname")
        target = _ADMINFUN_DIR / base
        if not target.exists() or not target.is_file():
            raise HTTPException(404, "Track nicht gefunden")

        # MIME-Typ aus Extension
        ext = target.suffix.lower()
        mime = {
            ".mp3":  "audio/mpeg",
            ".ogg":  "audio/ogg",
            ".wav":  "audio/wav",
            ".m4a":  "audio/mp4",
            ".opus": "audio/opus",
        }.get(ext, "application/octet-stream")

        return FileResponse(target, media_type=mime, filename=base)
