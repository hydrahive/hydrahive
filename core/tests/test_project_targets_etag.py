"""
test_project_targets_etag.py — #676 ETag/If-Match für Project-Targets.

Zweiteiliger Ansatz:
- TestEtagHelper: reine Unit-Tests für compute_project_targets_etag (laufen
  überall ohne FastAPI-TestClient).
- TestPutIfMatchStrict: Route-Integration via TestClient. Sichert die 200/
  428/409-Semantik — konsistent zu Composer-Etag (#650).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.project_targets import (
    compute_project_targets_etag,
    set_project_targets,
)


@pytest.fixture
def targets_file(tmp_path, monkeypatch):
    f = tmp_path / "project_targets.json"

    class _FakeSettings:
        project_targets_config = f
        users_config = tmp_path / "users.json"
        wks_keys_dir = tmp_path / "wks_keys"

    (tmp_path / "wks_keys").mkdir(exist_ok=True)
    monkeypatch.setattr("hydrahive_core.project_targets.settings", _FakeSettings)
    return f


# ═════════════════════════════════════════════════════ Helper-Unit-Tests


class TestEtagHelper:

    def test_empty_project_returns_stable_etag(self, targets_file):
        """Leeres Projekt muss GET liefern können — sonst kann der erste PUT
        bei strict If-Match gar nicht klappen."""
        etag = compute_project_targets_etag("new-project")
        assert etag
        assert len(etag) == 16  # sha256[:16]

    def test_empty_etag_deterministic(self, targets_file):
        assert compute_project_targets_etag("a") == compute_project_targets_etag("b")

    def test_etag_changes_on_assignment(self, targets_file):
        before = compute_project_targets_etag("proj-a")
        set_project_targets("proj-a", {
            "servers": [{"server_id": "prod-web", "role": "web", "note": ""}],
            "wks": [],
        })
        after = compute_project_targets_etag("proj-a")
        assert before != after

    def test_etag_stable_across_calls(self, targets_file):
        set_project_targets("proj-a", {
            "servers": [{"server_id": "s1", "role": "x", "note": "n"}],
            "wks": [{"username": "till", "role": "dev", "note": ""}],
        })
        e1 = compute_project_targets_etag("proj-a")
        e2 = compute_project_targets_etag("proj-a")
        assert e1 == e2

    def test_etag_independent_between_projects(self, targets_file):
        """Änderung an Projekt B darf ETag von Projekt A NICHT invalidieren."""
        set_project_targets("a", {"servers": [{"server_id": "s1"}], "wks": []})
        etag_a_before = compute_project_targets_etag("a")
        set_project_targets("b", {"servers": [{"server_id": "sx"}], "wks": []})
        etag_a_after = compute_project_targets_etag("a")
        assert etag_a_before == etag_a_after

    def test_etag_changes_on_role_update(self, targets_file):
        set_project_targets("proj-a", {
            "servers": [{"server_id": "s1", "role": "web", "note": ""}],
            "wks": [],
        })
        e1 = compute_project_targets_etag("proj-a")
        set_project_targets("proj-a", {
            "servers": [{"server_id": "s1", "role": "api", "note": ""}],
            "wks": [],
        })
        e2 = compute_project_targets_etag("proj-a")
        assert e1 != e2

    def test_etag_changes_on_wks_add(self, targets_file):
        set_project_targets("proj-a", {"servers": [{"server_id": "s1"}], "wks": []})
        e1 = compute_project_targets_etag("proj-a")
        set_project_targets("proj-a", {
            "servers": [{"server_id": "s1"}],
            "wks": [{"username": "till"}],
        })
        e2 = compute_project_targets_etag("proj-a")
        assert e1 != e2


# ═════════════════════════════════════════════════════ Router-Integration (TestClient)


def _build_app(tmp_path):
    import json as _json
    from fastapi import APIRouter, FastAPI
    from hydrahive_core.router_projects import register_project_routes

    # Stammdaten-Setup
    srv_dir   = tmp_path / "servers"
    srv_keys  = tmp_path / "server_keys"
    wks_keys  = tmp_path / "wks_keys"
    users_cfg = tmp_path / "users.json"
    targets_f = tmp_path / "project_targets.json"
    for p in (srv_dir, srv_keys, wks_keys):
        p.mkdir(parents=True, exist_ok=True)
    (srv_dir / "prod-web.json").write_text(_json.dumps({
        "id": "prod-web", "name": "Web", "ip": "1.2.3.4",
        "ssh_user": "root", "ssh_port": 22,
    }), encoding="utf-8")
    (srv_keys / "prod-web").write_text("KEY", encoding="utf-8")
    users_cfg.write_text(_json.dumps({
        "till": {"wks": {"ip": "10.0.0.1", "ssh_user": "till"}},
    }), encoding="utf-8")
    (wks_keys / "till").write_text("KEY", encoding="utf-8")

    class _S:
        project_targets_config = targets_f
        users_config = users_cfg
        servers_dir = srv_dir
        server_keys_dir = srv_keys
        wks_keys_dir = wks_keys

    # Monkeypatches via direktes Setzen (outside fixture, da _build_app sync ist)
    from hydrahive_core import project_targets as _pt
    from hydrahive_core import router_servers as _rs
    _pt.settings = _S
    _rs.settings = _S
    _rs.SERVERS_DIR = srv_dir
    _rs.SERVERS_KEYS_DIR = srv_keys

    app = FastAPI()
    auth_router = APIRouter()
    admin_router = APIRouter()

    projects = {"proj-a": {"name": "Projekt A", "description": ""}}

    # Viele Pflicht-Kwargs werden von den /targets-Endpoints nicht genutzt,
    # müssen aber für register_project_routes() gesetzt sein.
    register_project_routes(
        auth_router=auth_router,
        admin_router=admin_router,
        require_auth=lambda: ("admin", "admin"),
        projects=projects,
        discovery=mock.MagicMock(),
        runtime=mock.MagicMock(),
        sessions=mock.MagicMock(),
        orchestrator=mock.MagicMock(),
        projects_dir=str(tmp_path / "projects"),
        get_provisioner=lambda: mock.MagicMock(),
        update_project_matrix_room=lambda *a, **kw: None,
        update_project_matrix_space=lambda *a, **kw: None,
        get_user_allowed_projects=lambda u, r: None,  # None = alle Projekte erlaubt
        audit_log=lambda *a, **kw: None,
        check_message_rate=lambda *a, **kw: None,
        logger=mock.MagicMock(),
        invalidate_prompt_cache=lambda _pid: None,
    )
    app.include_router(auth_router)
    app.include_router(admin_router)
    return app


@pytest.fixture
def client(tmp_path, monkeypatch):
    testclient = pytest.importorskip(
        "fastapi.testclient",
        reason="FastAPI TestClient nicht installiert; Router-Integration läuft in CI/venv.",
    )
    app = _build_app(tmp_path)
    return testclient.TestClient(app)


class TestPutIfMatchStrict:

    def test_get_returns_etag(self, client):
        r = client.get("/projects/proj-a/targets")
        assert r.status_code == 200
        body = r.json()
        assert "etag" in body
        assert body["etag"]
        assert len(body["etag"]) == 16

    def test_put_without_if_match_returns_428(self, client):
        r = client.put("/projects/proj-a/targets", json={"servers": [], "wks": []})
        assert r.status_code == 428
        body = r.json()
        assert "current_etag" in body["detail"]
        assert "If-Match" in body["detail"]["message"]

    def test_put_with_stale_if_match_returns_409(self, client):
        # Erst etag holen
        etag = client.get("/projects/proj-a/targets").json()["etag"]
        # Ersten PUT mit gültigem etag — ändert State
        r1 = client.put(
            "/projects/proj-a/targets",
            json={"servers": [{"server_id": "prod-web", "role": "web", "note": ""}], "wks": []},
            headers={"If-Match": etag},
        )
        assert r1.status_code == 200
        # Zweiten PUT mit altem etag — muss 409 sein
        r2 = client.put(
            "/projects/proj-a/targets",
            json={"servers": [], "wks": []},
            headers={"If-Match": etag},
        )
        assert r2.status_code == 409
        assert "current_etag" in r2.json()["detail"]

    def test_put_with_matching_if_match_succeeds_and_rotates_etag(self, client):
        old = client.get("/projects/proj-a/targets").json()["etag"]
        r = client.put(
            "/projects/proj-a/targets",
            json={"servers": [{"server_id": "prod-web", "role": "web", "note": ""}], "wks": []},
            headers={"If-Match": old},
        )
        assert r.status_code == 200
        new = r.json()["etag"]
        assert new
        assert new != old

    def test_empty_project_initial_put_via_get_etag(self, client):
        """Frisches Projekt: GET → etag → PUT mit genau diesem etag → 200."""
        etag = client.get("/projects/proj-a/targets").json()["etag"]
        r = client.put(
            "/projects/proj-a/targets",
            json={"servers": [], "wks": []},
            headers={"If-Match": etag},
        )
        # Body ist leer, aber der etag matcht → strict lässt durch.
        assert r.status_code == 200
        assert r.json()["etag"]

    def test_404_still_fires_before_etag_check(self, client):
        """Unbekanntes Projekt → 404, nicht 428. Reihenfolge sanity."""
        r = client.put("/projects/does-not-exist/targets", json={"servers": [], "wks": []})
        assert r.status_code == 404
