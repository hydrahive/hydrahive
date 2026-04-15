"""Composer Concurrent-Edit-Schutz (#645 Follow-up, ETag/If-Match).

Deckt alle drei Scopes ab (me / admin / project) über eine parametrisierte
Fixture. Backward-Compat: fehlender If-Match = akzeptieren.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest
import yaml as _yaml
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from hydrahive_core.router_composer import (
    _compute_etag,
    register_admin_composer_routes,
    register_composer_routes,
    register_project_composer_routes,
)


# ---------------------------------------------------------------------------
# Fixture: liefert Client + agent_dir für einen gewählten Scope
# ---------------------------------------------------------------------------


def _seed_agent_dir(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent.yaml").write_text(f"id: {name}\ntools: [file_read]\n", encoding="utf-8")
    (d / "soul.md").write_text("legacy soul", encoding="utf-8")
    return d


def _seed_project_dir(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"id": name, "version": "2.0.0", "identity": {"name": name, "description": ""}}
    (d / "config.yaml").write_text(_yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return d


@pytest.fixture(params=["me", "admin", "project"])
def scoped_client(request, tmp_path):
    """Liefert (client, target_dir, save_path, get_path) pro Scope."""
    scope = request.param
    cache_calls: list[str] = []
    audit_calls: list[dict] = []
    logger = mock.MagicMock()

    app = FastAPI()

    if scope == "me":
        agents_dir = tmp_path / "agents"
        target = _seed_agent_dir(agents_dir, "personal_alice")

        def _ensure_personal_agent(u: str):
            return f"personal_{u}", None

        auth_router = APIRouter()
        register_composer_routes(
            auth_router,
            require_auth=lambda: ("alice", "user"),
            agents_dir=str(agents_dir),
            ensure_personal_agent=_ensure_personal_agent,
            invalidate_prompt_cache=lambda aid: cache_calls.append(aid),
            logger=logger,
            audit_log=lambda *a, **kw: audit_calls.append({"args": a, "kwargs": kw}),
        )
        app.include_router(auth_router)
        profile_url = "/me/agent/composer/profile"
        save_url = "/me/agent/composer"

    elif scope == "admin":
        agents_dir = tmp_path / "agents"
        target = _seed_agent_dir(agents_dir, "ops_bot")

        def _require_admin():
            return ("alice_admin", "admin")

        admin_router = APIRouter(dependencies=[Depends(_require_admin)])
        register_admin_composer_routes(
            admin_router,
            agents_dir=str(agents_dir),
            invalidate_prompt_cache=lambda aid: cache_calls.append(aid),
            logger=logger,
            audit_log=lambda action, **kw: audit_calls.append({"action": action, **kw}),
            require_admin=_require_admin,
        )
        app.include_router(admin_router)
        profile_url = "/admin/agents/ops_bot/composer/profile"
        save_url = "/admin/agents/ops_bot/composer"

    else:  # project
        projects_dir = tmp_path / "projects"
        target = _seed_project_dir(projects_dir, "personal_alice")

        def _require_auth():
            return ("alice", "user")

        auth_router = APIRouter(dependencies=[Depends(_require_auth)])
        register_project_composer_routes(
            auth_router,
            require_auth=_require_auth,
            projects_dir=str(projects_dir),
            invalidate_prompt_cache=lambda pid: cache_calls.append(pid),
            logger=logger,
            audit_log=lambda action, **kw: audit_calls.append({"action": action, **kw}),
        )
        app.include_router(auth_router)
        profile_url = "/projects/personal_alice/composer/profile"
        save_url = "/projects/personal_alice/composer"

    return {
        "client": TestClient(app),
        "target": target,
        "profile_url": profile_url,
        "save_url": save_url,
        "cache_calls": cache_calls,
        "audit_calls": audit_calls,
        "scope": scope,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_profile_includes_etag(scoped_client):
    r = scoped_client["client"].get(scoped_client["profile_url"])
    assert r.status_code == 200
    body = r.json()
    assert "etag" in body
    assert isinstance(body["etag"], str)
    assert len(body["etag"]) == 16


def test_save_with_matching_etag_returns_200_and_new_etag(scoped_client):
    c = scoped_client["client"]
    etag = c.get(scoped_client["profile_url"]).json()["etag"]
    r = c.put(
        scoped_client["save_url"],
        json={"selected": ["work_style.precise"]},
        headers={"If-Match": etag},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "etag" in body
    assert body["etag"] != etag  # Datei hat sich geändert → neuer ETag


def test_external_agent_md_edit_causes_409(scoped_client):
    c = scoped_client["client"]
    target = scoped_client["target"]
    old_etag = c.get(scoped_client["profile_url"]).json()["etag"]
    # Extern AGENT.md schreiben — seedet auch einen Legacy-Zustand
    (target / "AGENT.md").write_text("extern handgeschrieben\n", encoding="utf-8")
    time.sleep(0.01)

    r = c.put(
        scoped_client["save_url"],
        json={"selected": ["work_style.precise"]},
        headers={"If-Match": old_etag},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "current_etag" in detail
    assert detail["current_etag"] != old_etag
    # AGENT.md unverändert, kein Composer-Overwrite
    assert (target / "AGENT.md").read_text(encoding="utf-8") == "extern handgeschrieben\n"
    # Kein Backup (Composer hat nicht geschrieben)
    assert not (target / "AGENT.md.backup").exists()
    # Kein profile.yaml erzeugt
    assert not (target / "agent_profile.yaml").exists()


def test_external_profile_yaml_edit_causes_409(scoped_client):
    c = scoped_client["client"]
    target = scoped_client["target"]
    # Erst einen Save, damit profile.yaml existiert
    etag1 = c.get(scoped_client["profile_url"]).json()["etag"]
    r = c.put(
        scoped_client["save_url"],
        json={"selected": ["work_style.precise"]},
        headers={"If-Match": etag1},
    )
    assert r.status_code == 200
    etag2 = r.json()["etag"]

    # profile.yaml extern ändern
    time.sleep(0.01)
    (target / "agent_profile.yaml").write_text(
        "schema_version: 1\nselected: [comm.concise]\npreset: null\n", encoding="utf-8"
    )

    r2 = c.put(
        scoped_client["save_url"],
        json={"selected": ["comm.concise"]},
        headers={"If-Match": etag2},
    )
    assert r2.status_code == 409


def test_save_without_if_match_is_accepted_backward_compat(scoped_client):
    c = scoped_client["client"]
    r = c.put(
        scoped_client["save_url"],
        json={"selected": ["work_style.precise"]},
        # Kein If-Match-Header
    )
    assert r.status_code == 200
    assert "etag" in r.json()


def test_save_with_invalid_if_match_returns_409(scoped_client):
    c = scoped_client["client"]
    target = scoped_client["target"]
    initial_cache = list(scoped_client["cache_calls"])
    initial_audit = list(scoped_client["audit_calls"])

    r = c.put(
        scoped_client["save_url"],
        json={"selected": ["work_style.precise"]},
        headers={"If-Match": "deadbeefdeadbeef"},
    )
    assert r.status_code == 409
    # Keine Seiteneffekte bei 409
    assert scoped_client["cache_calls"] == initial_cache
    assert scoped_client["audit_calls"] == initial_audit
    assert not (target / "AGENT.md").exists()
    assert not (target / "agent_profile.yaml").exists()
    assert not (target / "AGENT.md.backup").exists()


def test_legacy_agent_md_without_profile_yaml(scoped_client):
    c = scoped_client["client"]
    target = scoped_client["target"]
    # Bestehende AGENT.md ohne profile.yaml
    (target / "AGENT.md").write_text("# Legacy\nhandmade\n", encoding="utf-8")

    legacy_etag = c.get(scoped_client["profile_url"]).json()["etag"]
    r = c.put(
        scoped_client["save_url"],
        json={"selected": ["work_style.precise"]},
        headers={"If-Match": legacy_etag},
    )
    assert r.status_code == 200
    assert r.json()["backup_created"] is True
    assert (target / "agent_profile.yaml").exists()


def test_retry_after_409_with_current_etag_succeeds(scoped_client):
    c = scoped_client["client"]
    target = scoped_client["target"]
    old_etag = c.get(scoped_client["profile_url"]).json()["etag"]

    # Extern ändern
    (target / "AGENT.md").write_text("extern\n", encoding="utf-8")
    time.sleep(0.01)

    r1 = c.put(
        scoped_client["save_url"],
        json={"selected": ["work_style.precise"]},
        headers={"If-Match": old_etag},
    )
    assert r1.status_code == 409
    current_etag = r1.json()["detail"]["current_etag"]

    # Retry mit dem neuen ETag
    r2 = c.put(
        scoped_client["save_url"],
        json={"selected": ["work_style.precise"]},
        headers={"If-Match": current_etag},
    )
    assert r2.status_code == 200
    assert (target / "AGENT.md.backup").read_text(encoding="utf-8") == "extern\n"


def test_409_does_not_invalidate_cache_or_audit(scoped_client):
    c = scoped_client["client"]
    assert scoped_client["cache_calls"] == []
    assert scoped_client["audit_calls"] == []
    r = c.put(
        scoped_client["save_url"],
        json={"selected": ["work_style.precise"]},
        headers={"If-Match": "wrongetagwrong00"},
    )
    assert r.status_code == 409
    assert scoped_client["cache_calls"] == []
    assert scoped_client["audit_calls"] == []


def test_new_etag_after_save_differs_from_get_etag(scoped_client):
    c = scoped_client["client"]
    etag_before = c.get(scoped_client["profile_url"]).json()["etag"]
    r = c.put(
        scoped_client["save_url"],
        json={"selected": ["work_style.precise"]},
        headers={"If-Match": etag_before},
    )
    etag_after_save = r.json()["etag"]
    etag_after_get = c.get(scoped_client["profile_url"]).json()["etag"]
    assert etag_after_save == etag_after_get
    assert etag_after_save != etag_before


# ---------------------------------------------------------------------------
# Reine Helper-Tests (scope-unabhängig)
# ---------------------------------------------------------------------------


def test_compute_etag_empty_dir_is_stable(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert _compute_etag(d) == _compute_etag(d)


def test_compute_etag_changes_with_agent_md_mtime(tmp_path):
    d = tmp_path / "e"
    d.mkdir()
    (d / "AGENT.md").write_text("a", encoding="utf-8")
    e1 = _compute_etag(d)
    time.sleep(0.01)
    (d / "AGENT.md").write_text("b", encoding="utf-8")
    e2 = _compute_etag(d)
    assert e1 != e2


def test_compute_etag_differs_for_missing_vs_empty_profile(tmp_path):
    d = tmp_path / "e"
    d.mkdir()
    (d / "AGENT.md").write_text("x", encoding="utf-8")
    e_missing = _compute_etag(d)
    (d / "agent_profile.yaml").write_text("", encoding="utf-8")
    e_present_empty = _compute_etag(d)
    assert e_missing != e_present_empty
