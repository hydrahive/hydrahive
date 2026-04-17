"""router_jobs.py — HTTP-API für das Job-Fundament (#687).

Scopes (Phase 1):

  /admin/jobs           Admin sieht alle Jobs.
  /me/jobs              User sieht nur eigene (created_by == username).

Projekt-Scope ``/projects/{id}/jobs`` wird bewusst ausgespart und kommt mit
den ersten echten Provider-Integrationen (#679 Image etc.).

POST ``/admin/jobs`` akzeptiert in Phase 1 ausschließlich ``type="noop"``
als Smoke-Entry. Produktive Jobs (Image/Video/Music) werden von Tools über
``job_service.submit()`` direkt registriert, nicht über HTTP-POST.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .jobs_service import (
    JobError,
    JobMeta,
    JobNotFoundError,
    JobService,
    _noop_runner,
)

logger = logging.getLogger(__name__)

# In Phase 1 nur der Builtin-Runner. Provider-Runner registrieren sich später
# direkt via job_service.submit() aus Tools — dieser Map dient nur dem
# Admin-POST-Smoke-Endpoint.
_ADMIN_SUBMITTABLE_TYPES: dict[str, Any] = {
    "noop": _noop_runner,
}


class JobSubmitRequest(BaseModel):
    type: str = Field(..., description="Job-Typ; Phase 1 nur 'noop'.")
    provider: str = Field("internal", description="Provider-Kennung.")
    input_summary: dict = Field(default_factory=dict, description="Sanitisierte Meta-Infos.")
    project_id: str | None = None
    agent_id: str | None = None


def _meta_public(meta: JobMeta) -> dict:
    """JSON-Response-Shape. Explizit keine internen Felder leaken."""
    return {
        "job_id":            meta.job_id,
        "type":              meta.type,
        "provider":          meta.provider,
        "status":            meta.status,
        "created_at":        meta.created_at,
        "updated_at":        meta.updated_at,
        "started_at":        meta.started_at,
        "finished_at":       meta.finished_at,
        "created_by":        meta.created_by,
        "project_id":        meta.project_id,
        "agent_id":          meta.agent_id,
        "input_summary":     meta.input_summary,
        "progress_percent":  meta.progress_percent,
        "progress_message":  meta.progress_message,
        "artifacts":         list(meta.artifacts),
        "error":             meta.error,
    }


def _username_from_auth(auth: Any) -> str:
    """Extract username from the (username, role) auth tuple.

    require_auth returns a tuple of (username, role) across the codebase.
    """
    if isinstance(auth, tuple) and auth:
        return auth[0]
    if isinstance(auth, str):
        return auth
    raise HTTPException(500, "auth payload ungültig")


def register_jobs_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    require_auth,
    job_service: JobService,
) -> None:
    """Hängt Jobs-Routen an auth_router + admin_router. Admin-Router bringt
    require_admin via Gruppendekorator bereits mit (siehe main.py Pattern),
    daher hier kein separater require_admin nötig."""

    # ── Admin-Scope ──────────────────────────────────────────────────────

    @admin_router.post("/admin/jobs", status_code=201)
    def admin_submit_job(req: JobSubmitRequest):
        """Phase-1-Smoke: nur ``type='noop'`` erlaubt."""
        runner = _ADMIN_SUBMITTABLE_TYPES.get(req.type)
        if runner is None:
            raise HTTPException(
                400,
                f"type '{req.type}' nicht submittable; erlaubt: "
                f"{sorted(_ADMIN_SUBMITTABLE_TYPES)}",
            )
        meta = job_service.submit(
            type=req.type,
            provider=req.provider or "internal",
            runner=runner,
            input_summary=dict(req.input_summary or {}),
            created_by=None,          # Admin-Submit ist unspezifisch
            project_id=req.project_id,
            agent_id=req.agent_id,
        )
        return _meta_public(meta)

    @admin_router.get("/admin/jobs")
    def admin_list_jobs(
        status: str | None = None,
        type: str | None = None,
        created_by: str | None = None,
        project_id: str | None = None,
    ):
        items = job_service.list(
            status=status, type=type,
            created_by=created_by, project_id=project_id,
        )
        return {"jobs": [_meta_public(m) for m in items]}

    @admin_router.get("/admin/jobs/{job_id}")
    def admin_get_job(job_id: str):
        try:
            return _meta_public(job_service.get(job_id))
        except JobNotFoundError:
            raise HTTPException(404, "job nicht gefunden")
        except JobError as exc:
            raise HTTPException(400, str(exc))

    @admin_router.post("/admin/jobs/{job_id}/cancel")
    def admin_cancel_job(job_id: str):
        try:
            meta = job_service.cancel(job_id)
        except JobNotFoundError:
            raise HTTPException(404, "job nicht gefunden")
        except JobError as exc:
            raise HTTPException(400, str(exc))
        return _meta_public(meta)

    @admin_router.get("/admin/jobs/{job_id}/artifacts/{filename}")
    def admin_download_artifact(job_id: str, filename: str):
        try:
            path = job_service.artifact_path(job_id, filename)
        except JobError as exc:
            raise HTTPException(400, str(exc))
        if not path.exists() or not path.is_file():
            raise HTTPException(404, "artifact nicht gefunden")
        try:
            meta = job_service.get(job_id)
        except JobNotFoundError:
            raise HTTPException(404, "job nicht gefunden")
        mime = _mime_for(meta, filename)
        return FileResponse(path, media_type=mime, filename=filename)

    # ── User-Scope ───────────────────────────────────────────────────────

    @auth_router.get("/me/jobs")
    def me_list_jobs(
        auth: tuple[str, str] = Depends(require_auth),
        status: str | None = None,
        type: str | None = None,
    ):
        username = _username_from_auth(auth)
        items = job_service.list(created_by=username, status=status, type=type)
        return {"jobs": [_meta_public(m) for m in items]}

    @auth_router.get("/me/jobs/{job_id}")
    def me_get_job(job_id: str, auth: tuple[str, str] = Depends(require_auth)):
        username = _username_from_auth(auth)
        meta = _load_owned_job_or_403(job_service, job_id, username)
        return _meta_public(meta)

    @auth_router.post("/me/jobs/{job_id}/cancel")
    def me_cancel_job(job_id: str, auth: tuple[str, str] = Depends(require_auth)):
        username = _username_from_auth(auth)
        # Ownership-Check bevor Cancel-Flag gesetzt wird.
        _load_owned_job_or_403(job_service, job_id, username)
        try:
            meta = job_service.cancel(job_id)
        except JobNotFoundError:
            raise HTTPException(404, "job nicht gefunden")
        except JobError as exc:
            raise HTTPException(400, str(exc))
        return _meta_public(meta)

    @auth_router.get("/me/jobs/{job_id}/artifacts/{filename}")
    def me_download_artifact(
        job_id: str, filename: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        username = _username_from_auth(auth)
        meta = _load_owned_job_or_403(job_service, job_id, username)
        try:
            path = job_service.artifact_path(job_id, filename)
        except JobError as exc:
            raise HTTPException(400, str(exc))
        if not path.exists() or not path.is_file():
            raise HTTPException(404, "artifact nicht gefunden")
        mime = _mime_for(meta, filename)
        return FileResponse(path, media_type=mime, filename=filename)


# ────────────────────────────────────────────── Helpers (modul-level für Tests)


def _load_owned_job_or_403(
    job_service: JobService,
    job_id: str,
    username: str,
) -> JobMeta:
    """Lädt einen Job und blockt fremde Owner mit 403.

    Wir geben bei Fremd-Access bewusst 403 statt 404 zurück, sobald bekannt
    ist, dass die Ressource existiert — analog #685-Policy (strict blockt
    mit klarer Message statt silent-404). Bei wirklich fehlender Ressource
    kommt 404.
    """
    try:
        meta = job_service.get(job_id)
    except JobNotFoundError:
        raise HTTPException(404, "job nicht gefunden")
    except JobError as exc:
        raise HTTPException(400, str(exc))
    if meta.created_by != username:
        raise HTTPException(403, "zugriff verweigert")
    return meta


def _mime_for(meta: JobMeta, filename: str) -> str:
    """Prefer den bei record_artifact gespeicherten MIME-Wert."""
    for entry in meta.artifacts or []:
        if entry.get("filename") == filename and entry.get("mime"):
            return str(entry["mime"])
    return "application/octet-stream"
