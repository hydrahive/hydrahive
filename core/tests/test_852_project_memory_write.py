"""test_852_project_memory_write.py"""
from __future__ import annotations
import sys
from pathlib import Path
import pytest
import yaml as _yaml
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hydrahive_core.router_projects import register_project_routes, ProjectMemoryWriteRequest, APIRouter

async def _allow_alice():
    return ("alice", "alice")

def _make_app(tmp_path, extra_members=None):
    extra_members = extra_members or []
    app = FastAPI()
    auth_router = APIRouter()
    project_id = "proj1"
    pdir = tmp_path / project_id
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text(_yaml.safe_dump({
        "id": project_id,
        "version": "2.0.0",
        "identity": {"name": "Proj1"},
        "members": extra_members + ["alice"],
    }), encoding="utf-8")
    (pdir / "memory").mkdir(exist_ok=True)
    from hydrahive_core.project_config import load_project_config
    class _Loader:
        def __init__(self):
            self._cache = {}
            self._register(pdir)
        def get(self, pid):
            return self._cache.get(pid)
        def _register(self, pp):
            if pp.is_file():
                pp = pp.parent
            cfg = load_project_config(pp)
            if cfg:
                self._cache[cfg.id] = cfg
    projects = _Loader()
    class _NullDiscovery:
        def get_model(self, p, m):
            return {}
    class _NullRuntime:
        async def stop_agent_task(self, i):
            return False
    class _NullOrchestrator:
        pass
    class _NullSessions:
        pass
    register_project_routes(
        auth_router, APIRouter(), APIRouter(),
        require_auth=_allow_alice,
        projects=projects,
        discovery=_NullDiscovery(),
        runtime=_NullRuntime(),
        sessions=_NullSessions(),
        orchestrator=_NullOrchestrator(),
        projects_dir=str(tmp_path),
        get_provisioner=lambda: None,
        update_project_matrix_room=lambda *a, **kw: None,
        update_project_matrix_space=lambda *a, **kw: None,
        get_user_allowed_projects=lambda u, r: None,
        audit_log=lambda *a, **kw: None,
        check_message_rate=lambda *a, **kw: None,
        logger=__import__("logging").getLogger(__name__))
    app.include_router(auth_router)
    return app, tmp_path, project_id

MEMORY_URL = "/projects/" + "proj1" + "/memory"

def test_write_new_memory(tmp_path):
    app, tmp_path, pid = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.post(MEMORY_URL,
        json={"filename": "notes", "content": "Hello world", "mode": "append"})
    assert resp.status_code == 201, resp.json()
    data = resp.json()
    assert data["ok"] is True
    assert "path" in data
    assert data["bytes"] == 11
    memory_file = tmp_path / pid / "memory" / "notes.md"
    assert memory_file.exists()
    assert memory_file.read_text(encoding="utf-8") == "Hello world"

def test_append_to_existing(tmp_path):
    app, tmp_path, pid = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.post(MEMORY_URL,
        json={"filename": "notes", "content": "AAA\nBBB\n\n", "mode": "append"})
    assert resp.status_code == 201
    content = (tmp_path / pid / "memory" / "notes.md").read_text(encoding="utf-8")
    assert content == "AAA\nBBB"

def test_overwrite_replaces(tmp_path):
    app, tmp_path, pid = _make_app(tmp_path)
    client = TestClient(app)
    client.post(MEMORY_URL,
        json={"filename": "notes", "content": "OLD", "mode": "append"})
    resp = client.post(MEMORY_URL,
        json={"filename": "notes", "content": "NEW", "mode": "overwrite"})
    assert resp.status_code == 201
    content = (tmp_path / pid / "memory" / "notes.md").read_text(encoding="utf-8")
    assert content == "NEW"

@pytest.mark.parametrize("bad", ["..", ""])
def test_invalid_filename_rejected(tmp_path, bad):
    app, tmp_path, pid = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.post(MEMORY_URL,
        json={"filename": bad, "content": "x", "mode": "append"})
    assert resp.status_code == 400

def test_unauthenticated_rejected(tmp_path):
    app, tmp_path, pid = _make_app(tmp_path)
    client = TestClient(app)
    # Override _allow_alice to simulate missing credentials
    from fastapi import HTTPException
    def _raise_auth_fail():
        raise HTTPException(401, "Unauthorized")
    app.dependency_overrides[_allow_alice] = _raise_auth_fail
    resp = client.post(MEMORY_URL,
        json={"filename": "notes", "content": "x", "mode": "append"})
    assert resp.status_code in (401, 403)

def test_cross_project_access_blocked(tmp_path):
    # Test that a user (bob) who is authenticated but NOT in the project's
    # members list gets 403. We achieve this by:
    # 1. Override require_auth to return bob's credentials
    # 2. Override get_user_allowed_projects to return [] (bob has no projects)
    from fastapi import HTTPException
    async def _auth_bob():
        return ("bob", "bob")
    # Build app with custom get_user_allowed_projects that denies bob
    app = FastAPI()
    auth_router = APIRouter()
    project_id = "proj1"
    pdir = tmp_path / project_id
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text(_yaml.safe_dump({
        "id": project_id,
        "version": "2.0.0",
        "identity": {"name": "Proj1"},
        "members": ["alice"],  # bob is NOT a member
    }), encoding="utf-8")
    (pdir / "memory").mkdir(exist_ok=True)
    from hydrahive_core.project_config import load_project_config
    class _Loader:
        def __init__(self):
            self._cache = {}
            self._register(pdir)
        def get(self, pid):
            return self._cache.get(pid)
        def _register(self, pp):
            if pp.is_file():
                pp = pp.parent
            cfg = load_project_config(pp)
            if cfg:
                self._cache[cfg.id] = cfg
    projects = _Loader()
    class _NullDiscovery:
        def get_model(self, p, m):
            return {}
    class _NullRuntime:
        async def stop_agent_task(self, i):
            return False
    class _NullOrchestrator:
        pass
    class _NullSessions:
        pass
    def _get_user_allowed_projects(username, role):
        # bob gets empty list = no project access
        if username == "bob":
            return []
        # alice gets all projects
        return None
    register_project_routes(
        auth_router, APIRouter(), APIRouter(),
        require_auth=_allow_alice,
        projects=projects,
        discovery=_NullDiscovery(),
        runtime=_NullRuntime(),
        sessions=_NullSessions(),
        orchestrator=_NullOrchestrator(),
        projects_dir=str(tmp_path),
        get_provisioner=lambda: None,
        update_project_matrix_room=lambda *a, **kw: None,
        update_project_matrix_space=lambda *a, **kw: None,
        get_user_allowed_projects=_get_user_allowed_projects,
        audit_log=lambda *a, **kw: None,
        check_message_rate=lambda *a, **kw: None,
        logger=__import__("logging").getLogger(__name__))
    app.include_router(auth_router)
    app.dependency_overrides[_allow_alice] = _auth_bob
    client = TestClient(app)
    resp = client.post(MEMORY_URL,
        json={"filename": "notes", "content": "x", "mode": "append"})
    assert resp.status_code == 403

def test_model_fields():
    req = ProjectMemoryWriteRequest(filename="notes", content="Hello")
    assert req.filename == "notes"
    assert req.content == "Hello"
    assert req.mode == "append"
    req2 = ProjectMemoryWriteRequest(filename="x", content="y", mode="overwrite")
    assert req2.mode == "overwrite"
