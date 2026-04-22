"""
test_821_compaction_threshold_endpoint.py — Admin-Endpoint fuer
Pro-Projekt Compaction-Threshold Override.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
import yaml as _yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app(tmp_path: Path):
    """Mounted nur den Lifecycle-Router (compaction-threshold) gegen ein
    minimales Projects-Verzeichnis. Auth ist via Override entschaerft."""
    from hydrahive_core import router_project_lifecycle as rpl

    app = FastAPI()
    admin_router = rpl.APIRouter()

    # Minimal-Projekt anlegen
    project_id = "demo"
    pdir = tmp_path / project_id
    pdir.mkdir()
    (pdir / "config.yaml").write_text(_yaml.safe_dump({
        "id": project_id,
        "version": "2.0.0",
        "identity": {"name": "Demo"},
        "llm": {"model": "minimax-m2.7"},
    }), encoding="utf-8")

    # ProjectLoader-Stub
    from hydrahive_core.project_config import load_project_config

    class _Loader:
        def __init__(self):
            self._cache: dict[str, object] = {}
            self._register(pdir)
        def get(self, pid: str):
            return self._cache.get(pid)
        def _register(self, project_path: Path):
            # Wenn jemand uns die config.yaml uebergibt: parent verwenden.
            if project_path.is_file():
                project_path = project_path.parent
            cfg = load_project_config(project_path)
            if cfg:
                self._cache[cfg.id] = cfg

    projects = _Loader()

    # require_admin durchlassen
    async def _allow_admin():
        return ("admin", "admin")

    # Stubs fuer die anderen Abhaengigkeiten von register_routes
    class _NullProvisioner:
        def reconcile_all_projects(self, _p):
            return {"reconciled": [], "skipped": [], "errors": []}

    class _NullRuntime:
        async def stop_agent_task(self, _id):
            return False

    rpl.register_project_lifecycle_routes(
        admin_router,
        require_admin=_allow_admin,
        projects=projects,
        runtime=_NullRuntime(),
        discovery=None,
        orchestrator=None,
        projects_dir=str(tmp_path),
        get_provisioner=lambda: _NullProvisioner(),
        read_server_name=lambda: "test",
        audit_log=lambda *a, **kw: None,
        logger=__import__("logging").getLogger(__name__),
    )
    app.include_router(admin_router, prefix="/admin")
    return app, projects, pdir


def test_get_default_no_override(tmp_path: Path):
    app, _projects, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/admin/projects/demo/compaction-threshold")
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"] == "demo"
    assert body["threshold"] is None
    assert body["model"] == "minimax-m2.7"
    assert body["context_window"] == 200_000
    assert body["default_threshold"] == 80_000


def test_put_override_then_get_reflects(tmp_path: Path):
    app, _projects, pdir = _make_app(tmp_path)
    client = TestClient(app)
    r = client.put("/admin/projects/demo/compaction-threshold", json={"threshold": 120_000})
    assert r.status_code == 200, r.text
    assert r.json()["threshold"] == 120_000
    # Datei enthaelt jetzt das Feld
    data = _yaml.safe_load((pdir / "config.yaml").read_text())
    assert data["compaction_threshold"] == 120_000
    # GET liefert den neuen Wert
    r2 = client.get("/admin/projects/demo/compaction-threshold")
    assert r2.json()["threshold"] == 120_000


def test_put_null_removes_override(tmp_path: Path):
    app, _projects, pdir = _make_app(tmp_path)
    client = TestClient(app)
    client.put("/admin/projects/demo/compaction-threshold", json={"threshold": 100_000})
    r = client.put("/admin/projects/demo/compaction-threshold", json={"threshold": None})
    assert r.status_code == 200
    assert r.json()["threshold"] is None
    data = _yaml.safe_load((pdir / "config.yaml").read_text())
    assert "compaction_threshold" not in data


def test_put_rejects_zero_or_negative(tmp_path: Path):
    app, *_ = _make_app(tmp_path)
    client = TestClient(app)
    for bad in (0, -1, -100):
        r = client.put("/admin/projects/demo/compaction-threshold", json={"threshold": bad})
        assert r.status_code == 400, f"value {bad} sollte 400 sein"


def test_put_rejects_too_low(tmp_path: Path):
    app, *_ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.put("/admin/projects/demo/compaction-threshold", json={"threshold": 1_000})
    assert r.status_code == 400


def test_put_unknown_project_404(tmp_path: Path):
    app, *_ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.put("/admin/projects/ghost/compaction-threshold", json={"threshold": 100_000})
    assert r.status_code == 404
