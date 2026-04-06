"""
router_extensions.py — Extension Manager (#52)

GET  /admin/extensions                      → alle Extensions + Live-Status
POST /admin/extensions/{id}/install         → Streaming SSE Install
POST /admin/extensions/{id}/uninstall       → Streaming SSE Uninstall
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

_EXT_ID_RE = re.compile(r'^[a-z0-9_-]+$')

INSTALLER_DIR   = Path("/opt/hydrahive/installer")
MANIFESTS_DIR   = INSTALLER_DIR / "extensions"
MANIFEST_ORDER  = ["searxng", "codeserver", "gitea", "ollama", "whatsapp", "headscale"]


def _load_manifests() -> list[dict]:
    if not MANIFESTS_DIR.exists():
        return []
    manifests = []
    # Reihenfolge aus MANIFEST_ORDER, dann Rest alphabetisch
    seen: set[str] = set()
    for mid in MANIFEST_ORDER:
        p = MANIFESTS_DIR / f"{mid}.json"
        if p.exists():
            try:
                m = json.loads(p.read_text())
                manifests.append(m)
                seen.add(mid)
            except Exception as e:
                logger.warning("Manifest %s parse error: %s", p, e)
    for p in sorted(MANIFESTS_DIR.glob("*.json")):
        mid = p.stem
        if mid in seen:
            continue
        try:
            manifests.append(json.loads(p.read_text()))
        except Exception as e:
            logger.warning("Manifest %s parse error: %s", p, e)
    return manifests


def _service_active(service: str | None) -> bool:
    if not service:
        return False
    try:
        r = subprocess.run(
            ["systemctl", "show", service, "--property=ActiveState"],
            capture_output=True, text=True, timeout=5,
        )
        return "ActiveState=active" in r.stdout
    except Exception:
        return False


def _http_ok(url: str | None) -> bool:
    if not url:
        return False
    try:
        r = subprocess.run(
            ["curl", "-sf", "--max-time", "3", "--noproxy", "*", url],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _is_installed(manifest: dict) -> bool:
    check = manifest.get("installed_check")
    if not check:
        return False
    try:
        return Path(check).exists()
    except (PermissionError, OSError):
        return False


async def _stream_script(script_path: Path):
    import os as _os
    import json as _j
    env = {**_os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", "/bin/bash", str(script_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    assert proc.stdout
    try:
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip()
            yield f"data: {_j.dumps({'line': text})}\n\n"
        rc = await asyncio.wait_for(proc.wait(), timeout=600)
        yield f"data: {_j.dumps({'done': True, 'ok': rc == 0})}\n\n"
    except asyncio.TimeoutError:
        yield f"data: {_j.dumps({'line': '[TIMEOUT] Script überschritt 10 Minuten — abgebrochen', 'done': True, 'ok': False})}\n\n"
    except Exception as e:
        yield f"data: {_j.dumps({'line': f'[ERROR] {e}', 'done': True, 'ok': False})}\n\n"
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass


def register_extension_routes(admin_router: APIRouter, *, require_admin) -> None:

    @admin_router.get("/admin/extensions")
    async def list_extensions(_a=Depends(require_admin)):
        manifests = _load_manifests()
        result = []
        for m in manifests:
          try:
            installed = _is_installed(m)
            active    = _service_active(m.get("service")) if installed else False
            http_ok   = _http_ok(m.get("health_url"))     if active   else False
            is_external = m.get("external", False)
            result.append({
                "id":          m.get("id"),
                "name":        m.get("name"),
                "description": m.get("description"),
                "icon":        m.get("icon"),
                "category":    m.get("category", "tools"),
                "installed":   installed if not is_external else True,
                "active":      active if not is_external else True,
                "http_ok":     http_ok if not is_external else True,
                "open_url":    m.get("open_url"),
                "has_uninstall": bool(m.get("uninstall_script")),
                "external":    is_external,
                "config_hint": m.get("config_hint", ""),
                "plugin_id":   m.get("plugin_id", ""),
            })
          except Exception as e:
            logger.warning("Extension %s Fehler: %s", m.get("id", "?"), e)
            continue
        return result

    @admin_router.post("/admin/extensions/{ext_id}/install")
    async def install_extension(ext_id: str, _a=Depends(require_admin)):
        if not _EXT_ID_RE.match(ext_id):
            raise HTTPException(400, "Ungültige Extension-ID")
        manifests = {m["id"]: m for m in _load_manifests()}
        if ext_id not in manifests:
            raise HTTPException(404, f"Extension '{ext_id}' nicht gefunden")
        script_rel = manifests[ext_id].get("install_script")
        if not script_rel:
            raise HTTPException(400, "Kein install_script definiert")
        script_path = INSTALLER_DIR / script_rel
        if not script_path.exists():
            logger.error("Install-Script nicht gefunden: %s", script_path)
            raise HTTPException(500, "Installer-Script nicht gefunden — bitte Update durchführen")
        return StreamingResponse(
            _stream_script(script_path),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @admin_router.post("/admin/extensions/{ext_id}/uninstall")
    async def uninstall_extension(ext_id: str, _a=Depends(require_admin)):
        if not _EXT_ID_RE.match(ext_id):
            raise HTTPException(400, "Ungültige Extension-ID")
        manifests = {m["id"]: m for m in _load_manifests()}
        if ext_id not in manifests:
            raise HTTPException(404, f"Extension '{ext_id}' nicht gefunden")
        script_rel = manifests[ext_id].get("uninstall_script")
        if not script_rel:
            raise HTTPException(400, "Kein uninstall_script definiert")
        script_path = INSTALLER_DIR / script_rel
        if not script_path.exists():
            logger.error("Uninstall-Script nicht gefunden: %s", script_path)
            raise HTTPException(500, "Uninstaller-Script nicht gefunden — bitte Update durchführen")
        return StreamingResponse(
            _stream_script(script_path),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
