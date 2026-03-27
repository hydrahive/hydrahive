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
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

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
    return Path(check).exists()


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
    async for line in proc.stdout:
        text = line.decode("utf-8", errors="replace").rstrip()
        yield f"data: {_j.dumps({'line': text})}\n\n"
    rc = await proc.wait()
    yield f"data: {_j.dumps({'done': True, 'ok': rc == 0})}\n\n"


def register_extension_routes(admin_router: APIRouter, *, require_admin) -> None:

    @admin_router.get("/admin/extensions")
    async def list_extensions(_a=Depends(require_admin)):
        manifests = _load_manifests()
        result = []
        for m in manifests:
            installed = _is_installed(m)
            active    = _service_active(m.get("service")) if installed else False
            http_ok   = _http_ok(m.get("health_url"))     if active   else False
            result.append({
                "id":          m.get("id"),
                "name":        m.get("name"),
                "description": m.get("description"),
                "icon":        m.get("icon"),
                "category":    m.get("category", "tools"),
                "installed":   installed,
                "active":      active,
                "http_ok":     http_ok,
                "open_url":    m.get("open_url"),
                "has_uninstall": bool(m.get("uninstall_script")),
            })
        return result

    @admin_router.post("/admin/extensions/{ext_id}/install")
    async def install_extension(ext_id: str, _a=Depends(require_admin)):
        manifests = {m["id"]: m for m in _load_manifests()}
        if ext_id not in manifests:
            raise HTTPException(404, f"Extension '{ext_id}' nicht gefunden")
        script_rel = manifests[ext_id].get("install_script")
        if not script_rel:
            raise HTTPException(400, "Kein install_script definiert")
        script_path = INSTALLER_DIR / script_rel
        if not script_path.exists():
            raise HTTPException(500, f"Script nicht gefunden: {script_path} — bitte Update durchführen")
        return StreamingResponse(
            _stream_script(script_path),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @admin_router.post("/admin/extensions/{ext_id}/uninstall")
    async def uninstall_extension(ext_id: str, _a=Depends(require_admin)):
        manifests = {m["id"]: m for m in _load_manifests()}
        if ext_id not in manifests:
            raise HTTPException(404, f"Extension '{ext_id}' nicht gefunden")
        script_rel = manifests[ext_id].get("uninstall_script")
        if not script_rel:
            raise HTTPException(400, "Kein uninstall_script definiert")
        script_path = INSTALLER_DIR / script_rel
        if not script_path.exists():
            raise HTTPException(500, f"Script nicht gefunden: {script_path} — bitte Update durchführen")
        return StreamingResponse(
            _stream_script(script_path),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
