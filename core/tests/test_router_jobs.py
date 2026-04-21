"""#687: router_jobs — HTTP-Level-Tests + Helper-Integration.

``conftest.py`` mockt fastapi für die meisten Unit-Tests. Dieses Modul
braucht echtes fastapi (``TestClient`` + echtes ``HTTPException`` für die
Helper) — ohne wird die Datei per ``importorskip`` übersprungen und CI/
Deploy übernehmen die Regression-Garantie.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("fastapi.testclient")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from hydrahive_core.jobs_service import JobContext, JobService, _noop_runner
from hydrahive_core.tool_registry import (
    set_workspace_override,
    reset_workspace_override,
)
from hydrahive_core.router_jobs import (
    _load_owned_job_or_403,
    _meta_public,
    _mime_for,
    _username_from_auth,
    register_jobs_routes,
)


# ────────────────────────────────────────────── Fixtures


@pytest.fixture
def svc(tmp_path) -> JobService:
    return JobService(root=tmp_path / "jobs")


@pytest.fixture
def finished_noop(svc: JobService):
    async def run():
        meta = svc.submit(
            type="noop", provider="internal", runner=_noop_runner,
            created_by="alice",
        )
        task = svc._tasks[meta.job_id]
        await asyncio.wait_for(task, timeout=2)
        return meta.job_id

    return asyncio.run(run())


@pytest.fixture
def client(svc):
    """(client, set_user) — set_user(None) simuliert unauthenticated."""
    current = {"user": None}

    def require_auth():
        u = current["user"]
        if u is None:
            raise HTTPException(401, "unauthenticated")
        return (u, "user")

    def require_admin():
        return None

    app = FastAPI()
    auth_router = APIRouter(dependencies=[Depends(require_auth)])
    admin_router = APIRouter(dependencies=[Depends(require_admin)])
    register_jobs_routes(
        auth_router, admin_router,
        require_auth=require_auth, job_service=svc,
    )
    app.include_router(auth_router)
    app.include_router(admin_router)

    def set_user(name):
        current["user"] = name

    return TestClient(app), set_user


def _http_submit_as(svc: JobService, username: str) -> str:
    async def run() -> str:
        meta = svc.submit(
            type="noop", provider="internal", runner=_noop_runner,
            created_by=username,
        )
        task = svc._tasks[meta.job_id]
        await asyncio.wait_for(task, timeout=2)
        return meta.job_id

    return asyncio.run(run())


# ────────────────────────────────────────────── Helper: Ownership


class TestLoadOwnedJobOr403:
    def test_owner_gets_meta(self, svc, finished_noop):
        meta = _load_owned_job_or_403(svc, finished_noop, "alice")
        assert meta.job_id == finished_noop

    def test_foreign_user_is_403(self, svc, finished_noop):
        with pytest.raises(HTTPException) as ei:
            _load_owned_job_or_403(svc, finished_noop, "bob")
        assert ei.value.status_code == 403

    def test_unknown_job_is_404(self, svc):
        with pytest.raises(HTTPException) as ei:
            _load_owned_job_or_403(svc, "job_" + "0" * 16, "alice")
        assert ei.value.status_code == 404

    def test_invalid_id_is_400(self, svc):
        with pytest.raises(HTTPException) as ei:
            _load_owned_job_or_403(svc, "not_a_valid_id", "alice")
        assert ei.value.status_code == 400


# ────────────────────────────────────────────── Helper: Public Shape


class TestMetaPublic:
    def test_expected_fields(self, svc, finished_noop):
        body = _meta_public(svc.get(finished_noop))
        expected = {
            "job_id", "type", "provider", "status", "created_at", "updated_at",
            "started_at", "finished_at", "created_by", "project_id", "agent_id",
            "input_summary", "progress_percent", "progress_message",
            "artifacts", "error",
        }
        assert set(body.keys()) == expected

    def test_no_internal_leak(self, svc, finished_noop):
        body = _meta_public(svc.get(finished_noop))
        assert "_tasks" not in body
        assert "_cancelled" not in body

    def test_artifact_list_is_shallow_copy(self, svc, finished_noop):
        body = _meta_public(svc.get(finished_noop))
        body["artifacts"].append({"hacked": True})
        assert all("hacked" not in e for e in svc.get(finished_noop).artifacts)


# ────────────────────────────────────────────── Helper: MIME / Auth


class TestMimeFor:
    def test_stored_mime(self, svc, finished_noop):
        assert _mime_for(svc.get(finished_noop), "noop.txt") == "text/plain"

    def test_unknown_filename_fallback(self, svc, finished_noop):
        assert _mime_for(svc.get(finished_noop), "other.txt") == "application/octet-stream"


class TestUsernameFromAuth:
    def test_tuple(self):
        assert _username_from_auth(("alice", "user")) == "alice"

    def test_string(self):
        assert _username_from_auth("alice") == "alice"

    def test_none_raises(self):
        with pytest.raises(HTTPException) as ei:
            _username_from_auth(None)
        assert ei.value.status_code == 500


# ────────────────────────────────────────────── HTTP: Admin-Scope


def test_admin_submit_noop(client):
    c, set_user = client
    set_user("admin")
    r = c.post("/admin/jobs", json={"type": "noop", "provider": "internal"})
    assert r.status_code == 201
    assert r.json()["job_id"].startswith("job_")


def test_admin_rejects_unknown_type(client):
    c, set_user = client
    set_user("admin")
    r = c.post("/admin/jobs", json={"type": "image", "provider": "minimax"})
    assert r.status_code == 400


def test_admin_list_all(client, svc):
    _http_submit_as(svc, "alice")
    _http_submit_as(svc, "bob")
    c, set_user = client
    set_user("admin")
    r = c.get("/admin/jobs")
    assert r.status_code == 200
    assert len(r.json()["jobs"]) == 2


def test_admin_list_filter_created_by(client, svc):
    _http_submit_as(svc, "alice")
    _http_submit_as(svc, "bob")
    c, set_user = client
    set_user("admin")
    jobs = c.get("/admin/jobs?created_by=bob").json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["created_by"] == "bob"


def test_admin_get_unknown_404(client):
    c, set_user = client
    set_user("admin")
    assert c.get("/admin/jobs/job_" + "0" * 16).status_code == 404


def test_admin_get_invalid_400(client):
    c, set_user = client
    set_user("admin")
    assert c.get("/admin/jobs/not_a_valid_id").status_code == 400


def test_admin_artifact_download(client, svc):
    c, set_user = client
    set_user("admin")
    jid = _http_submit_as(svc, "alice")
    r = c.get(f"/admin/jobs/{jid}/artifacts/noop.txt")
    assert r.status_code == 200
    assert r.content == b"noop artifact\n"


def test_admin_artifact_missing_404(client, svc):
    c, set_user = client
    set_user("admin")
    jid = _http_submit_as(svc, "alice")
    assert c.get(f"/admin/jobs/{jid}/artifacts/not-written.txt").status_code == 404


# ────────────────────────────────────────────── HTTP: /me-Scope


def test_me_list_filters_self(client, svc):
    _http_submit_as(svc, "alice")
    _http_submit_as(svc, "bob")
    c, set_user = client
    set_user("alice")
    jobs = c.get("/me/jobs").json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["created_by"] == "alice"


def test_me_get_own(client, svc):
    c, set_user = client
    set_user("alice")
    jid = _http_submit_as(svc, "alice")
    assert c.get(f"/me/jobs/{jid}").status_code == 200


def test_me_get_foreign_403(client, svc):
    c, set_user = client
    set_user("bob")
    jid = _http_submit_as(svc, "alice")
    assert c.get(f"/me/jobs/{jid}").status_code == 403


def test_me_cancel_foreign_403(client, svc):
    c, set_user = client
    set_user("bob")
    jid = _http_submit_as(svc, "alice")
    assert c.post(f"/me/jobs/{jid}/cancel").status_code == 403


def test_me_artifact_own(client, svc):
    c, set_user = client
    set_user("alice")
    jid = _http_submit_as(svc, "alice")
    r = c.get(f"/me/jobs/{jid}/artifacts/noop.txt")
    assert r.status_code == 200
    assert r.content == b"noop artifact\n"


def test_me_artifact_foreign_403(client, svc):
    c, set_user = client
    set_user("bob")
    jid = _http_submit_as(svc, "alice")
    assert c.get(f"/me/jobs/{jid}/artifacts/noop.txt").status_code == 403


def test_me_unauthenticated_401(client):
    c, set_user = client
    set_user(None)
    assert c.get("/me/jobs").status_code == 401


# ────────────────────────────────────────────── #704 Sprint C: Degraded Responses


def test_http_admin_submit_degraded_is_503(svc):
    """JobStorageError aus submit → 503 ohne Path im Body."""
    svc._fs_ok = False
    app = FastAPI()
    auth_router = APIRouter()
    admin_router = APIRouter()
    register_jobs_routes(auth_router, admin_router, require_auth=lambda: ("admin", "user"), job_service=svc)
    app.include_router(auth_router)
    app.include_router(admin_router)
    c = TestClient(app)

    r = c.post("/admin/jobs", json={"type": "noop", "provider": "internal"})
    assert r.status_code == 503
    body = r.json()
    assert "unavailable" in body["detail"].lower()
    # Kein Pfad-Leak im Response-Body.
    assert "/var/lib" not in body["detail"]
    assert str(svc._root) not in body["detail"]


def test_http_admin_get_corrupt_meta_is_503(svc):
    """JobStorageError aus get → 503 ohne Path/job_id im Body."""
    jid = "job_" + "c" * 16
    (svc._meta_dir / f"{jid}.json").write_text("{not valid json", encoding="utf-8")

    app = FastAPI()
    auth_router = APIRouter()
    admin_router = APIRouter()
    register_jobs_routes(auth_router, admin_router, require_auth=lambda: ("admin", "user"), job_service=svc)
    app.include_router(auth_router)
    app.include_router(admin_router)
    c = TestClient(app)

    r = c.get(f"/admin/jobs/{jid}")
    assert r.status_code == 503
    body = r.json()
    assert "unavailable" in body["detail"].lower()
    assert jid not in body["detail"]
    assert "/var/lib" not in body["detail"]


# ----------------------------------------------------------------------
# #802 Phase 2 — Workspace-First-Download transparent via /me + /admin
# ----------------------------------------------------------------------


def test_admin_artifact_download_from_workspace(svc, tmp_path, client):
    """Admin GET /admin/jobs/{id}/artifacts/{file} liefert Workspace-Artifacts."""
    ws_root = tmp_path / "projects" / "proj_integration_ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    token = set_workspace_override(ws_root)
    try:
        async def _run() -> str:
            async def runner(ctx: JobContext):
                ctx.record_artifact(
                    b"WORKSPACE_BIN", "artifact.bin", "application/octet-stream",
                )
            meta = svc.submit(
                type="noop", provider="internal", runner=runner,
                created_by="alice", project_id="proj_integration_ws",
            )
            await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
            return meta.job_id
        job_id = asyncio.run(_run())

        c, set_user = client
        set_user("admin")
        r = c.get(f"/admin/jobs/{job_id}/artifacts/artifact.bin")
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        assert r.content == b"WORKSPACE_BIN"
    finally:
        reset_workspace_override(token)


def test_me_artifact_download_from_legacy(svc, client):
    """GET /me/jobs/{id}/artifacts/{file} liefert Legacy-Artifacts (project_id=None)."""
    async def _run() -> str:
        async def runner(ctx: JobContext):
            ctx.record_artifact(
                b"LEGACY_BIN", "legacy.bin", "application/octet-stream",
            )
        meta = svc.submit(
            type="noop", provider="internal", runner=runner,
            created_by="bob", project_id=None,
        )
        await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
        return meta.job_id
    job_id = asyncio.run(_run())

    c, set_user = client
    set_user("bob")
    r = c.get(f"/me/jobs/{job_id}/artifacts/legacy.bin")
    assert r.status_code == 200
    assert r.content == b"LEGACY_BIN"


def test_me_artifact_download_returns_404_when_workspace_file_missing(svc, tmp_path, client):
    """storage=workspace + File gelöscht → 404 (Router prüft .exists())."""
    ws_root = tmp_path / "projects" / "proj_missing_file"
    ws_root.mkdir(parents=True, exist_ok=True)
    token = set_workspace_override(ws_root)
    try:
        async def _run() -> str:
            async def runner(ctx: JobContext):
                ctx.record_artifact(b"DATA", "lost.bin", "application/octet-stream")
            meta = svc.submit(
                type="noop", provider="internal", runner=runner,
                created_by="carol", project_id="proj_missing_file",
            )
            await asyncio.wait_for(svc._tasks[meta.job_id], timeout=2)
            return meta.job_id
        job_id = asyncio.run(_run())

        # Workspace-Datei physisch löschen (simuliert Migrations-Verlust)
        ws_artifact = svc.artifact_path(job_id, "lost.bin")
        ws_artifact.unlink()

        c, set_user = client
        set_user("carol")
        r = c.get(f"/me/jobs/{job_id}/artifacts/lost.bin")
        assert r.status_code == 404
    finally:
        reset_workspace_override(token)
