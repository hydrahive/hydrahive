"""Tests für #658: Skill-Catalog + Install-Endpoint."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from hydrahive_core.router_agent_skills import (
    MAX_SKILL_BYTES,
    _sanitize_skill_name,
    register_agent_skill_routes,
)
from hydrahive_core.router_skills_catalog import (
    _is_valid_name,
    get_catalog_entry,
    list_catalog,
    register_skills_catalog_routes,
    safe_catalog_path,
)


VALID_SKILL_MD = """---
skill: code-review
version: "1.0"
scope: on-demand
triggers: [review, audit]
priority: 50
---

Führe einen konzentrierten Code-Review durch. Achte auf Sicherheit.
"""

INVALID_SKILL_MD_NO_FRONTMATTER = "Nur Text, kein Frontmatter.\n"
INVALID_SKILL_MD_NO_SKILL_FIELD = """---
version: "1.0"
scope: on-demand
---

Body.
"""


def _write_skill(d: Path, name: str, content: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return p


# ── Unit: _sanitize_skill_name ───────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("code-review", "code-review"),
    ("code-review.md", "code-review"),
    ("code_review", "code_review"),
    ("my_skill_1", "my_skill_1"),
    ("ab", "ab"),
    ("x" * 64, "x" * 64),
])
def test_sanitize_valid(name, expected):
    assert _sanitize_skill_name(name) == expected


@pytest.mark.parametrize("bad", [
    "",
    "../etc/passwd",
    "foo/bar",
    "foo\\bar",
    "UPPER",
    "-leading-dash",
    ".",
    "..",
    "a..b",
    "a.b",
    ".leading-dot",
    "x" * 65,
    "foo/../bar",
    "with space",
])
def test_sanitize_rejects_invalid(bad):
    with pytest.raises(HTTPException) as exc:
        _sanitize_skill_name(bad)
    assert exc.value.status_code == 400


def test_is_valid_name_edge():
    assert _is_valid_name("ok-name")
    assert _is_valid_name("code_review")
    assert not _is_valid_name("")
    assert not _is_valid_name(".")
    assert not _is_valid_name("..")
    assert not _is_valid_name("a..b")
    assert not _is_valid_name("a.b")
    assert not _is_valid_name("a/b")
    assert not _is_valid_name("Upper")


# ── Unit: safe_catalog_path ──────────────────────────────────────────────────

def test_safe_catalog_path_traversal(tmp_path):
    with pytest.raises(HTTPException) as exc:
        safe_catalog_path(tmp_path, "../outside")
    assert exc.value.status_code == 400


def test_safe_catalog_path_absolute_rejected(tmp_path):
    with pytest.raises(HTTPException) as exc:
        safe_catalog_path(tmp_path, "/abs/path")
    assert exc.value.status_code == 400


# ── Unit: list_catalog ───────────────────────────────────────────────────────

def test_list_catalog_missing_dir(tmp_path):
    items, errors = list_catalog(tmp_path / "nonexistent")
    assert items == []
    assert errors == []


def test_list_catalog_empty(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    items, errors = list_catalog(tmp_path)
    assert items == []
    assert errors == []


def test_list_catalog_valid_and_invalid(tmp_path):
    _write_skill(tmp_path, "ok", VALID_SKILL_MD)
    _write_skill(tmp_path, "broken", INVALID_SKILL_MD_NO_FRONTMATTER)
    _write_skill(tmp_path, "no-skill-field", INVALID_SKILL_MD_NO_SKILL_FIELD)
    items, errors = list_catalog(tmp_path)
    names = {i["name"] for i in items}
    assert "ok" in names
    assert "broken" not in names
    assert "no-skill-field" not in names
    err_names = {e["name"] for e in errors}
    assert err_names == {"broken", "no-skill-field"}
    # Valid entry enthält summary-Felder
    ok = next(i for i in items if i["name"] == "ok")
    assert ok["skill"] == "code-review"
    assert ok["scope"] == "on-demand"
    assert ok["version"] == "1.0"


def test_list_catalog_skips_bad_filename(tmp_path):
    _write_skill(tmp_path, "OKname", VALID_SKILL_MD)  # Uppercase → invalid name
    items, errors = list_catalog(tmp_path)
    assert items == []
    assert len(errors) == 1 and errors[0]["error"] == "invalid_name"


# ── Unit: get_catalog_entry ──────────────────────────────────────────────────

def test_get_catalog_entry_happy(tmp_path):
    _write_skill(tmp_path, "code-review", VALID_SKILL_MD)
    entry = get_catalog_entry(tmp_path, "code-review")
    assert entry["name"] == "code-review"
    assert entry["skill"] == "code-review"
    assert "Code-Review" in entry["content"]


def test_get_catalog_entry_missing(tmp_path):
    with pytest.raises(HTTPException) as exc:
        get_catalog_entry(tmp_path, "nonexistent")
    assert exc.value.status_code == 404


def test_get_catalog_entry_invalid_frontmatter(tmp_path):
    _write_skill(tmp_path, "broken", INVALID_SKILL_MD_NO_FRONTMATTER)
    with pytest.raises(HTTPException) as exc:
        get_catalog_entry(tmp_path, "broken")
    assert exc.value.status_code == 422


def test_get_catalog_entry_traversal_rejected(tmp_path):
    with pytest.raises(HTTPException) as exc:
        get_catalog_entry(tmp_path, "../etc/passwd")
    assert exc.value.status_code == 400


# ── Route-Tests via TestClient ───────────────────────────────────────────────

@pytest.fixture
def app_and_dirs(tmp_path):
    agents_dir = tmp_path / "agents"
    catalog_dir = tmp_path / "catalog"
    agents_dir.mkdir()
    (agents_dir / "personal_alice").mkdir()

    app = FastAPI()
    auth_router = APIRouter()

    def _require_auth():
        return ("alice", "admin")

    def _check_agent_write(agent_id, auth):
        return

    register_agent_skill_routes(
        auth_router,
        require_auth=_require_auth,
        check_agent_write=_check_agent_write,
        agents_dir=str(agents_dir),
        logger=mock.MagicMock(),
        catalog_dir_provider=lambda: catalog_dir,
    )
    register_skills_catalog_routes(
        auth_router,
        require_auth=_require_auth,
        catalog_dir_provider=lambda: catalog_dir,
        logger=mock.MagicMock(),
    )
    app.include_router(auth_router)
    return TestClient(app), agents_dir, catalog_dir


def test_route_list_empty_catalog(app_and_dirs):
    client, _, _ = app_and_dirs
    r = client.get("/skills/catalog")
    assert r.status_code == 200
    data = r.json()
    assert data["skills"] == []
    assert data["errors"] == []


def test_route_list_missing_catalog_dir_ok(app_and_dirs):
    client, _, catalog_dir = app_and_dirs
    # catalog_dir wurde nicht mkdir'd — muss trotzdem 200 liefern
    if catalog_dir.exists():
        import shutil; shutil.rmtree(catalog_dir)
    r = client.get("/skills/catalog")
    assert r.status_code == 200
    assert r.json()["skills"] == []


def test_route_list_with_valid_and_invalid(app_and_dirs):
    client, _, catalog_dir = app_and_dirs
    _write_skill(catalog_dir, "code-review", VALID_SKILL_MD)
    _write_skill(catalog_dir, "broken", INVALID_SKILL_MD_NO_FRONTMATTER)
    r = client.get("/skills/catalog")
    assert r.status_code == 200
    data = r.json()
    assert len(data["skills"]) == 1
    assert data["skills"][0]["name"] == "code-review"
    assert {e["name"] for e in data["errors"]} == {"broken"}


def test_route_detail_happy(app_and_dirs):
    client, _, catalog_dir = app_and_dirs
    _write_skill(catalog_dir, "code-review", VALID_SKILL_MD)
    r = client.get("/skills/catalog/code-review")
    assert r.status_code == 200
    assert r.json()["skill"] == "code-review"


def test_route_detail_broken_returns_422(app_and_dirs):
    client, _, catalog_dir = app_and_dirs
    _write_skill(catalog_dir, "broken", INVALID_SKILL_MD_NO_FRONTMATTER)
    r = client.get("/skills/catalog/broken")
    assert r.status_code == 422


def test_route_detail_not_found(app_and_dirs):
    client, _, _ = app_and_dirs
    r = client.get("/skills/catalog/does-not-exist")
    assert r.status_code == 404


@pytest.mark.parametrize("bad_name", ["../outside", "foo/bar", "UPPER"])
def test_route_detail_invalid_name(app_and_dirs, bad_name):
    client, _, _ = app_and_dirs
    r = client.get(f"/skills/catalog/{bad_name}")
    assert r.status_code in (400, 404)  # FastAPI path-matching kann 404 vor Handler greifen


# ── Install-Endpoint ─────────────────────────────────────────────────────────

def test_install_happy_path(app_and_dirs):
    client, agents_dir, catalog_dir = app_and_dirs
    _write_skill(catalog_dir, "code-review", VALID_SKILL_MD)

    r = client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "catalog", "name": "code-review"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["installed"] is True
    assert data["filename"] == "code-review"
    assert (agents_dir / "personal_alice" / "skills" / "code-review.md").exists()


def test_install_invalid_source(app_and_dirs):
    client, _, _ = app_and_dirs
    r = client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "github", "name": "code-review"},
    )
    assert r.status_code == 400


def test_install_invalid_name(app_and_dirs):
    client, _, _ = app_and_dirs
    r = client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "catalog", "name": "../etc/passwd"},
    )
    assert r.status_code == 400


def test_install_path_traversal_segment(app_and_dirs):
    client, _, _ = app_and_dirs
    r = client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "catalog", "name": "foo/../bar"},
    )
    assert r.status_code == 400


def test_install_unknown_agent(app_and_dirs):
    client, _, catalog_dir = app_and_dirs
    _write_skill(catalog_dir, "code-review", VALID_SKILL_MD)
    r = client.post(
        "/agents/personal_bob/skills/install",
        json={"source": "catalog", "name": "code-review"},
    )
    assert r.status_code == 404


def test_install_missing_catalog_entry(app_and_dirs):
    client, _, _ = app_and_dirs
    r = client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "catalog", "name": "nonexistent"},
    )
    assert r.status_code == 404


def test_install_conflict_without_force(app_and_dirs):
    client, agents_dir, catalog_dir = app_and_dirs
    _write_skill(catalog_dir, "code-review", VALID_SKILL_MD)
    # ersten Install durchführen
    r = client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "catalog", "name": "code-review"},
    )
    assert r.status_code == 201
    # zweiter Install ohne force
    r2 = client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "catalog", "name": "code-review"},
    )
    assert r2.status_code == 409


def test_install_conflict_with_force_overwrites(app_and_dirs):
    client, agents_dir, catalog_dir = app_and_dirs
    _write_skill(catalog_dir, "code-review", VALID_SKILL_MD)
    client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "catalog", "name": "code-review"},
    )
    # neue Catalog-Version
    updated = VALID_SKILL_MD.replace("priority: 50", "priority: 10")
    _write_skill(catalog_dir, "code-review", updated)
    r = client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "catalog", "name": "code-review", "force": True},
    )
    assert r.status_code == 201
    installed = (agents_dir / "personal_alice" / "skills" / "code-review.md").read_text()
    assert "priority: 10" in installed


def test_install_rejects_oversized_file(app_and_dirs):
    client, _, catalog_dir = app_and_dirs
    big = VALID_SKILL_MD + ("x" * (MAX_SKILL_BYTES + 1))
    _write_skill(catalog_dir, "huge", big)
    r = client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "catalog", "name": "huge"},
    )
    assert r.status_code == 413


def test_install_rejects_invalid_frontmatter(app_and_dirs):
    client, _, catalog_dir = app_and_dirs
    _write_skill(catalog_dir, "broken", INVALID_SKILL_MD_NO_FRONTMATTER)
    r = client.post(
        "/agents/personal_alice/skills/install",
        json={"source": "catalog", "name": "broken"},
    )
    assert r.status_code == 422


# ── Bestehende Skill-Route-Härtung: sanitization ─────────────────────────────

def test_create_skill_rejects_traversal(app_and_dirs):
    client, _, _ = app_and_dirs
    r = client.post(
        "/agents/personal_alice/skills",
        json={
            "filename": "../outside",
            "skill": "x", "content": "body",
        },
    )
    assert r.status_code == 400


def test_delete_skill_rejects_traversal(app_and_dirs):
    client, _, _ = app_and_dirs
    r = client.delete("/agents/personal_alice/skills/..%2Fescape")
    # FastAPI normalisiert URL-Path; bei korrekt sanitizendem Handler → 400/404
    assert r.status_code in (400, 404)
