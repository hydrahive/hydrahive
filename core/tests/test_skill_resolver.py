"""Tests für #659: Multi-Layer Skill-Resolver + router layers-Query."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from hydrahive_core.router_agent_skills import register_agent_skill_routes
from hydrahive_core.router_skills_catalog import register_skills_catalog_routes
from hydrahive_core.skill_resolver import (
    SkillOrigin,
    resolve_full_view,
    resolve_prompt_skills,
    validate_username,
)


MD_VALID = """---
skill: {name}
version: "1.0"
scope: on-demand
triggers: []
priority: {prio}
---

Body from {layer}: {name}.
"""


def _write(dirpath: Path, name: str, *, layer: str, prio: int = 50) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / f"{name}.md"
    p.write_text(MD_VALID.format(name=name, prio=prio, layer=layer), encoding="utf-8")
    return p


def _write_broken(dirpath: Path, name: str) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / f"{name}.md"
    p.write_text("kein frontmatter", encoding="utf-8")
    return p


# ── validate_username ────────────────────────────────────────────────────────

@pytest.mark.parametrize("good", ["alice", "bob_1", "x", "a-b-c", "u0123456789"])
def test_username_valid(good):
    assert validate_username(good) == good


@pytest.mark.parametrize("bad", [
    "",
    "../etc",
    "with/slash",
    "back\\slash",
    "UPPER",
    "-dash",
    ".dot",
    "a..b",
    "a.b",
    "x" * 65,
])
def test_username_invalid(bad):
    with pytest.raises(ValueError):
        validate_username(bad)


# ── resolve_prompt_skills: Priorität agent > project > user ─────────────────

def test_prompt_priority_agent_wins(tmp_path):
    agent = tmp_path / "agent/skills"
    project = tmp_path / "project/skills"
    user = tmp_path / "user/skills"
    _write(agent,   "bug-report", layer="agent")
    _write(project, "bug-report", layer="project")
    _write(user,    "bug-report", layer="user")

    resolved, errors = resolve_prompt_skills(
        agent_dir=agent, project_dir=project, user_skills_dir=user,
    )
    assert errors == []
    assert len(resolved) == 1
    r = resolved[0]
    assert r.name == "bug-report"
    assert r.effective.source == "agent"
    assert r.effective.parsed_ok is True
    assert "Body from agent" in r.effective.skill.content
    # shadowed enthält project + user, in Prio-Reihenfolge
    assert [s.source for s in r.shadowed] == ["project", "user"]


def test_prompt_priority_project_when_no_agent(tmp_path):
    project = tmp_path / "project/skills"
    user = tmp_path / "user/skills"
    _write(project, "x", layer="project")
    _write(user,    "x", layer="user")

    resolved, errors = resolve_prompt_skills(
        agent_dir=None, project_dir=project, user_skills_dir=user,
    )
    assert errors == []
    assert resolved[0].effective.source == "project"
    assert [s.source for s in resolved[0].shadowed] == ["user"]


def test_prompt_user_only(tmp_path):
    user = tmp_path / "user/skills"
    _write(user, "y", layer="user")
    resolved, _ = resolve_prompt_skills(
        agent_dir=None, project_dir=None, user_skills_dir=user,
    )
    assert [r.effective.source for r in resolved] == ["user"]


# ── Catalog wird NICHT automatisch prompt-aktiv ──────────────────────────────

def test_catalog_not_prompt_effective(tmp_path):
    """Kern-Garantie V1: resolve_prompt_skills darf system-Layer nicht sehen.
    Ein system-only Skill darf niemals im Prompt landen."""
    catalog = tmp_path / "catalog"
    _write(catalog, "only-system", layer="system")

    # Resolver mit keinem lokalen Skill.
    resolved, errors = resolve_prompt_skills(
        agent_dir=tmp_path / "empty-agent/skills",
        project_dir=None,
        user_skills_dir=None,
    )
    assert resolved == []
    assert errors == []

    # Full-View sieht ihn dagegen.
    resolved_full, _ = resolve_full_view(
        agent_dir=tmp_path / "empty-agent/skills",
        system_catalog_dir=catalog,
    )
    assert [r.effective.source for r in resolved_full] == ["system"]


# ── Dedup agent_dir == project_dir ───────────────────────────────────────────

def test_dedup_when_agent_equals_project(tmp_path):
    shared = tmp_path / "shared/skills"
    _write(shared, "dup", layer="shared")

    resolved, errors = resolve_prompt_skills(
        agent_dir=shared, project_dir=shared, user_skills_dir=None,
    )
    assert errors == []
    assert len(resolved) == 1
    # Effective kommt als agent (erster Layer). Shadowed darf NICHT den
    # Projekt-Eintrag enthalten — wäre Self-Shadowing.
    assert resolved[0].effective.source == "agent"
    assert resolved[0].shadowed == ()


# ── Fallback bei kaputter höherer Datei ──────────────────────────────────────

def test_broken_higher_layer_falls_back_to_lower(tmp_path):
    agent = tmp_path / "agent/skills"
    user = tmp_path / "user/skills"
    _write_broken(agent, "recover")
    _write(user, "recover", layer="user")

    resolved, errors = resolve_prompt_skills(
        agent_dir=agent, project_dir=None, user_skills_dir=user,
    )
    # Effective ist user; agent-Datei landet als shadowed mit parsed_ok=False.
    assert len(resolved) == 1
    r = resolved[0]
    assert r.effective.source == "user"
    assert r.effective.parsed_ok is True

    assert len(r.shadowed) == 1
    assert r.shadowed[0].source == "agent"
    assert r.shadowed[0].parsed_ok is False

    # Fehler wird gemeldet, blockiert aber nicht.
    assert any(e["source"] == "agent" and e["name"] == "recover" for e in errors)


def test_broken_lower_layer_reported_but_higher_wins(tmp_path):
    agent = tmp_path / "agent/skills"
    user = tmp_path / "user/skills"
    _write(agent, "x", layer="agent")
    _write_broken(user, "x")

    resolved, errors = resolve_prompt_skills(
        agent_dir=agent, project_dir=None, user_skills_dir=user,
    )
    assert resolved[0].effective.source == "agent"
    # Shadow-Eintrag für kaputte user-Datei vorhanden, errors gemeldet.
    assert any(s.source == "user" and not s.parsed_ok for s in resolved[0].shadowed)
    assert any(e["source"] == "user" for e in errors)


def test_all_layers_broken_yields_no_effective(tmp_path):
    agent = tmp_path / "agent/skills"
    user = tmp_path / "user/skills"
    _write_broken(agent, "dead")
    _write_broken(user, "dead")
    resolved, errors = resolve_prompt_skills(
        agent_dir=agent, project_dir=None, user_skills_dir=user,
    )
    assert resolved == []
    assert len(errors) == 2


# ── Leere Dirs / None ────────────────────────────────────────────────────────

def test_empty_dirs(tmp_path):
    resolved, errors = resolve_prompt_skills(
        agent_dir=None, project_dir=None, user_skills_dir=None,
    )
    assert resolved == []
    assert errors == []


def test_nonexistent_agent_dir_is_ok(tmp_path):
    resolved, errors = resolve_prompt_skills(
        agent_dir=tmp_path / "does-not-exist",
        project_dir=None,
        user_skills_dir=None,
    )
    assert resolved == []
    assert errors == []


# ── Full-View: Catalog als verfügbare Quelle, aber shadowbar ────────────────

def test_full_view_catalog_shadowed_by_agent(tmp_path):
    agent = tmp_path / "agent/skills"
    catalog = tmp_path / "catalog"
    _write(agent, "bug-report", layer="agent")
    _write(catalog, "bug-report", layer="system")
    _write(catalog, "only-system", layer="system")

    resolved, errors = resolve_full_view(
        agent_dir=agent, system_catalog_dir=catalog,
    )
    assert errors == []
    by_name = {r.name: r for r in resolved}
    assert by_name["bug-report"].effective.source == "agent"
    assert [s.source for s in by_name["bug-report"].shadowed] == ["system"]
    assert by_name["only-system"].effective.source == "system"
    assert by_name["only-system"].shadowed == ()


# ── Router-Integration: layers-Query ─────────────────────────────────────────

@pytest.fixture
def app_and_dirs(tmp_path):
    agents_dir = tmp_path / "agents"
    catalog_dir = tmp_path / "catalog"
    agents_dir.mkdir()
    (agents_dir / "bob").mkdir()
    (agents_dir / "bob" / "skills").mkdir()

    app = FastAPI()
    auth_router = APIRouter()

    def _require_auth():
        return ("bob", "admin")

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


def test_router_layers_installed_backcompat(app_and_dirs):
    """Default `installed` liefert altes #658-Shape."""
    client, agents_dir, _ = app_and_dirs
    _write(agents_dir / "bob/skills", "a", layer="agent")

    r = client.get("/agents/bob/skills")
    assert r.status_code == 200
    data = r.json()
    assert data["agent_id"] == "bob"
    # Altes Shape: flache skills-Liste mit filename/skill/scope.
    assert data["skills"][0]["filename"] == "a"
    assert data["skills"][0]["skill"] == "a"
    # Kein effective/shadows-Feld im installed-Mode.
    assert "effective" not in data["skills"][0]
    assert "errors" not in data
    assert "available" not in data


def test_router_layers_effective_splits_available(app_and_dirs):
    """System-only Skills landen NIE in `skills[]`, sondern in `available[]`.
    Nur agent/project/user dürfen als effective auftauchen."""
    client, agents_dir, catalog_dir = app_and_dirs
    _write(agents_dir / "bob/skills", "local", layer="agent")
    _write(catalog_dir, "from-catalog", layer="system")
    _write(catalog_dir, "local", layer="system")  # wird shadowed

    r = client.get("/agents/bob/skills?layers=effective")
    assert r.status_code == 200
    data = r.json()

    eff_names = {s["name"]: s for s in data["skills"]}
    avail_names = {a["name"]: a for a in data["available"]}

    # Installierter Skill ist effective.
    assert "local" in eff_names
    assert eff_names["local"]["effective"]["source"] == "agent"
    # System-only-Skill ist NICHT effective, sondern available.
    assert "from-catalog" not in eff_names
    assert "from-catalog" in avail_names
    assert avail_names["from-catalog"]["source"] == "system"
    # Kein system-Eintrag schmuggelt sich in effective.
    assert all(s["effective"]["source"] != "system" for s in data["skills"])
    # effective-Mode zeigt KEINE shadows.
    assert "shadows" not in eff_names["local"]


def test_router_layers_all_reports_shadows_and_errors(app_and_dirs):
    client, agents_dir, catalog_dir = app_and_dirs
    _write(agents_dir / "bob/skills", "common", layer="agent")
    _write(catalog_dir, "common", layer="system")
    _write_broken(catalog_dir, "broken")
    _write(catalog_dir, "catalog-only", layer="system")

    r = client.get("/agents/bob/skills?layers=all")
    assert r.status_code == 200
    data = r.json()

    eff_names = {s["name"]: s for s in data["skills"]}
    avail_names = {a["name"]: a for a in data["available"]}

    # Agent-Skill ist effective, wird nicht durch system-dup verdrängt.
    assert eff_names["common"]["effective"]["source"] == "agent"
    assert [s["source"] for s in eff_names["common"]["shadows"]] == ["system"]
    # Inhalt ist bei layers=all eingebettet für /skill run.
    assert "content" in eff_names["common"]
    # Catalog-only Skill: nur in available, NICHT in effective.
    assert "catalog-only" in avail_names
    assert "catalog-only" not in eff_names
    # errors enthält kaputtes catalog-file.
    assert any(e["name"] == "broken" and e["source"] == "system"
               for e in data.get("errors", []))


def test_router_system_only_never_effective(app_and_dirs):
    """Explizite Garantie: System-only Skill ohne agent/project/user-Pendant
    wird niemals als effective ausgeliefert, egal ob layers=effective|all."""
    client, _, catalog_dir = app_and_dirs
    _write(catalog_dir, "system-solo", layer="system")

    for mode in ("effective", "all"):
        r = client.get(f"/agents/bob/skills?layers={mode}")
        assert r.status_code == 200, mode
        data = r.json()
        eff_names = [s["name"] for s in data["skills"]]
        avail_names = [a["name"] for a in data["available"]]
        assert "system-solo" not in eff_names, mode
        assert "system-solo" in avail_names, mode


def test_router_layers_invalid_query(app_and_dirs):
    client, _, _ = app_and_dirs
    r = client.get("/agents/bob/skills?layers=bogus")
    assert r.status_code == 422  # FastAPI Query-Pattern-Reject


# ── #668: Router User-Layer bei layers=effective|all ─────────────────────────

@pytest.fixture
def app_with_users(tmp_path, monkeypatch):
    """Wie app_and_dirs, aber `settings.user_skills_dir` zeigt auf tmp_path
    und die Auth-Fixture kann den Username pro Test variieren."""
    from hydrahive_core.settings import settings as _settings
    from hydrahive_core.skill_resolver import validate_username

    users_base = tmp_path / "users"

    def _fake_user_skills_dir(self, username: str) -> Path:
        validate_username(username)  # Raise wie echtes settings
        return users_base / username / "skills"

    # Pydantic-Settings erlaubt kein Instance-setattr — Class-Method patchen.
    monkeypatch.setattr(type(_settings), "user_skills_dir", _fake_user_skills_dir)

    agents_dir = tmp_path / "agents"
    catalog_dir = tmp_path / "catalog"
    agents_dir.mkdir()
    (agents_dir / "bob").mkdir()
    (agents_dir / "bob" / "skills").mkdir()

    app = FastAPI()
    auth_router = APIRouter()
    current_auth = {"username": "alice", "role": "user"}

    def _require_auth():
        return (current_auth["username"], current_auth["role"])

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
    app.include_router(auth_router)
    return TestClient(app), agents_dir, users_base, current_auth


def test_router_user_layer_effective_for_authenticated_user(app_with_users):
    """User-Skill ohne agent/project-Override wird als effective geliefert."""
    client, _, users_base, _auth = app_with_users
    _write(users_base / "alice" / "skills", "alice-only", layer="user")

    r = client.get("/agents/bob/skills?layers=all")
    assert r.status_code == 200
    data = r.json()
    names = {s["name"]: s for s in data["skills"]}
    assert "alice-only" in names
    assert names["alice-only"]["effective"]["source"] == "user"


def test_router_user_layer_shadowed_by_agent(app_with_users):
    """Agent-Skill überschreibt User-Skill, User taucht als shadow."""
    client, agents_dir, users_base, _auth = app_with_users
    _write(agents_dir / "bob/skills", "common", layer="agent")
    _write(users_base / "alice" / "skills", "common", layer="user")

    r = client.get("/agents/bob/skills?layers=all")
    data = r.json()
    names = {s["name"]: s for s in data["skills"]}
    assert names["common"]["effective"]["source"] == "agent"
    assert any(s["source"] == "user"
               for s in names["common"].get("shadows", []))


def test_router_internal_request_skips_user_layer(app_with_users):
    """Internal-signed sub-calls tragen `internal` → kein User-Layer."""
    client, _, users_base, auth = app_with_users
    auth["username"] = "internal"
    auth["role"] = "admin"
    _write(users_base / "internal" / "skills", "leak", layer="user")

    r = client.get("/agents/bob/skills?layers=all")
    data = r.json()
    names = [s["name"] for s in data["skills"]]
    avails = [a["name"] for a in data["available"]]
    assert "leak" not in names
    assert "leak" not in avails


def test_router_invalid_username_does_not_500(app_with_users, monkeypatch):
    """Falls `user_skills_dir` ValueError wirft, liefert die Route trotzdem
    200 — der User-Layer wird still übersprungen."""
    client, agents_dir, _, auth = app_with_users
    _write(agents_dir / "bob/skills", "agent-skill", layer="agent")

    from hydrahive_core.settings import settings as _settings

    def _raises(self, username: str) -> Path:
        raise ValueError("böse")

    monkeypatch.setattr(type(_settings), "user_skills_dir", _raises)
    auth["username"] = "whatever"

    r = client.get("/agents/bob/skills?layers=all")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["skills"]]
    assert "agent-skill" in names


def test_router_installed_mode_unchanged_by_user_layer(app_with_users):
    """layers=installed bleibt strikt agent-lokal, auch wenn User-Skills
    auf der Platte liegen."""
    client, agents_dir, users_base, _auth = app_with_users
    _write(agents_dir / "bob/skills", "agent-one", layer="agent")
    _write(users_base / "alice" / "skills", "user-one", layer="user")

    r = client.get("/agents/bob/skills")  # default = installed
    data = r.json()
    stems = [s.get("skill") or s.get("filename") for s in data["skills"]]
    assert "agent-one" in stems
    assert "user-one" not in stems
    assert "available" not in data
