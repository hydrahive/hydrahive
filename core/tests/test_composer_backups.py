"""Composer Backup Listing / Preview / Restore (#647).

Deckt ab:
- Listing: Sortierung (versioned newest→oldest, `AGENT.md.backup` am Ende),
  leer, 500-Cap via `truncated=True`.
- Preview: valid name, invalid name, fehlende Datei, Oversize-Cap.
- Restore: strict If-Match (428 wenn fehlt, 409 bei Mismatch), happy path,
  Path-Traversal, Cache + Audit Side-Effects, Sibling-Files unberührt.
- Alle drei Scopes (personal/admin/project) via geteilter Helper-Logik.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest import mock

import pytest
import yaml as _yaml
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from hydrahive_core.router_composer import (
    AGENT_MD_BACKUP,
    AGENT_MD_FILENAME,
    register_admin_composer_routes,
    register_composer_routes,
    register_project_composer_routes,
)


# ────────────────────────────────────────────────────────────────── Fixtures

def _seed_agent_dir(d: Path, *, agent_md: str = "# v1", profile_yaml: str | None = None) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / AGENT_MD_FILENAME).write_text(agent_md, encoding="utf-8")
    (d / "agent.yaml").write_text("id: test\n", encoding="utf-8")
    (d / "soul.md").write_text("soul-original", encoding="utf-8")
    (d / "skills").mkdir(exist_ok=True)
    (d / "skills" / "foo.md").write_text("foo-skill", encoding="utf-8")
    (d / "memory").mkdir(exist_ok=True)
    (d / "memory" / "bar.md").write_text("bar-memory", encoding="utf-8")
    if profile_yaml is not None:
        (d / "agent_profile.yaml").write_text(profile_yaml, encoding="utf-8")


def _write_backup(d: Path, name: str, content: str, *, mtime: float | None = None) -> Path:
    p = d / name
    p.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def personal_client(tmp_path):
    username = "alice"
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    personal_dir = agents_dir / f"personal_{username}"
    _seed_agent_dir(personal_dir)

    cache_calls: list[str] = []
    audit_calls: list[dict] = []

    def _ensure_personal_agent(u: str):
        return f"personal_{u}", None

    app = FastAPI()
    auth_router = APIRouter()
    register_composer_routes(
        auth_router,
        require_auth=lambda: (username, "user"),
        agents_dir=str(agents_dir),
        ensure_personal_agent=_ensure_personal_agent,
        invalidate_prompt_cache=lambda aid: cache_calls.append(aid),
        logger=mock.MagicMock(),
        audit_log=lambda action, **kw: audit_calls.append({"action": action, **kw}),
    )
    app.include_router(auth_router)
    return TestClient(app), personal_dir, cache_calls, audit_calls


@pytest.fixture
def admin_client(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent_dir = agents_dir / "ops_bot"
    _seed_agent_dir(agent_dir)

    cache_calls: list[str] = []
    audit_calls: list[dict] = []

    def _require_admin():
        return ("admin_user", "admin")

    app = FastAPI()
    admin_router = APIRouter(dependencies=[Depends(_require_admin)])
    register_admin_composer_routes(
        admin_router,
        agents_dir=str(agents_dir),
        invalidate_prompt_cache=lambda aid: cache_calls.append(aid),
        logger=mock.MagicMock(),
        audit_log=lambda action, **kw: audit_calls.append({"action": action, **kw}),
        require_admin=_require_admin,
    )
    app.include_router(admin_router)
    return TestClient(app), agent_dir, cache_calls, audit_calls


@pytest.fixture
def project_client(tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    # Personal-Projekt (entspricht _require_project_composer_access für Non-Admin)
    pdir = projects_dir / "personal_alice"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "config.yaml").write_text(
        _yaml.safe_dump({"id": "personal_alice", "version": "2.0.0",
                         "identity": {"name": "personal_alice"}}),
        encoding="utf-8",
    )
    _seed_agent_dir(pdir)

    cache_calls: list[str] = []
    audit_calls: list[dict] = []

    def _require_auth():
        return ("alice", "user")

    app = FastAPI()
    auth_router = APIRouter(dependencies=[Depends(_require_auth)])
    register_project_composer_routes(
        auth_router,
        require_auth=_require_auth,
        projects_dir=str(projects_dir),
        invalidate_prompt_cache=lambda aid: cache_calls.append(aid),
        logger=mock.MagicMock(),
        audit_log=lambda action, **kw: audit_calls.append({"action": action, **kw}),
    )
    app.include_router(auth_router)
    return TestClient(app), pdir, cache_calls, audit_calls


# ─────────────────────────────────────────────────────────────── Listing

def test_list_empty_returns_zero(personal_client):
    client, _, _, _ = personal_client
    r = client.get("/me/agent/composer/backups")
    assert r.status_code == 200
    data = r.json()
    assert data["backups"] == []
    assert data["count"] == 0
    assert data["truncated"] is False


def test_list_sorts_versioned_newest_first_then_latest(personal_client):
    client, d, _, _ = personal_client
    base = time.time()
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "old",  mtime=base - 300)
    _write_backup(d, "AGENT.md.20260416T100500Z.backup", "mid",  mtime=base - 200)
    _write_backup(d, "AGENT.md.20260416T101000Z.backup", "new",  mtime=base - 100)
    _write_backup(d, AGENT_MD_BACKUP,                    "latest", mtime=base - 10)

    r = client.get("/me/agent/composer/backups")
    assert r.status_code == 200
    data = r.json()
    names = [b["name"] for b in data["backups"]]
    assert names == [
        "AGENT.md.20260416T101000Z.backup",
        "AGENT.md.20260416T100500Z.backup",
        "AGENT.md.20260416T100000Z.backup",
        AGENT_MD_BACKUP,
    ]
    kinds = [b["kind"] for b in data["backups"]]
    assert kinds[:3] == ["versioned"] * 3
    assert kinds[-1] == "latest"
    assert data["count"] == 4
    assert data["truncated"] is False


def test_list_ignores_foreign_files(personal_client):
    client, d, _, _ = personal_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "ok")
    _write_backup(d, "AGENT.md.evil.backup", "bad-shape")
    _write_backup(d, "AGENT.md", "current")  # kein Backup
    _write_backup(d, "random.txt", "foreign")

    r = client.get("/me/agent/composer/backups")
    assert r.status_code == 200
    names = [b["name"] for b in r.json()["backups"]]
    assert names == ["AGENT.md.20260416T100000Z.backup"]


def test_list_truncated_at_500(personal_client):
    client, d, _, _ = personal_client
    # 501 eindeutige valid-named Backups via `-N`-Suffix am gleichen Zeitstempel.
    base = time.time()
    for i in range(501):
        _write_backup(
            d, f"AGENT.md.20260416T100000Z-{i + 2}.backup",
            str(i),
            mtime=base - (501 - i),
        )
    r = client.get("/me/agent/composer/backups")
    data = r.json()
    assert data["truncated"] is True
    assert data["count"] == 500


# ─────────────────────────────────────────────────────────────── Preview

def test_preview_valid_backup(personal_client):
    client, d, _, _ = personal_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "# historical content")
    r = client.get("/me/agent/composer/backups/AGENT.md.20260416T100000Z.backup")
    assert r.status_code == 200
    data = r.json()
    assert data["content"] == "# historical content"
    assert data["size_bytes"] == len("# historical content")
    assert data["name"] == "AGENT.md.20260416T100000Z.backup"
    assert "T" in data["mtime"]  # ISO-UTC


def test_preview_latest_backup_also_ok(personal_client):
    client, d, _, _ = personal_client
    _write_backup(d, AGENT_MD_BACKUP, "last-active")
    r = client.get(f"/me/agent/composer/backups/{AGENT_MD_BACKUP}")
    assert r.status_code == 200
    assert r.json()["content"] == "last-active"


@pytest.mark.parametrize("bad", [
    "../etc/passwd",
    "AGENT.md",             # ist keine Backup-Datei, sondern das Original
    "random.md",
    "AGENT.md.evil",
    "AGENT.md.backup.bak",
    "AGENT.md..backup",
    "AGENT.md.20260416T100000Z.backup/../evil",
])
def test_preview_invalid_name_rejected_400(personal_client, bad):
    client, _, _, _ = personal_client
    r = client.get(f"/me/agent/composer/backups/{bad}")
    # FastAPI kann manche "leere" oder slash-haltige Pfade als 404 routen
    assert r.status_code in (400, 404, 405)


def test_preview_missing_file_404(personal_client):
    client, _, _, _ = personal_client
    r = client.get("/me/agent/composer/backups/AGENT.md.20260416T999999Z.backup")
    assert r.status_code == 400 or r.status_code == 404
    # Regex ist strict; 999999 ist keine gültige Zeit → 400 oder 404.
    # Gültigen, aber fehlenden Namen nochmal testen:
    r2 = client.get("/me/agent/composer/backups/AGENT.md.20260416T235959Z.backup")
    assert r2.status_code == 404


def test_preview_oversize_rejected_413(personal_client):
    client, d, _, _ = personal_client
    big = "x" * (1 * 1024 * 1024 + 10)
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", big)
    r = client.get("/me/agent/composer/backups/AGENT.md.20260416T100000Z.backup")
    assert r.status_code == 413


# ─────────────────────────────────────────────────────────────── Restore

def _current_etag(client, profile_path: str = "/me/agent/composer/profile") -> str:
    r = client.get(profile_path)
    assert r.status_code == 200
    return r.json()["etag"]


def test_restore_missing_if_match_returns_428(personal_client):
    client, d, _, audit_calls = personal_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "historical")
    r = client.post("/me/agent/composer/backups/AGENT.md.20260416T100000Z.backup/restore")
    assert r.status_code == 428
    detail = r.json()["detail"]
    assert "current_etag" in detail
    assert isinstance(detail["current_etag"], str) and detail["current_etag"]
    # Nichts wurde geschrieben.
    assert (d / AGENT_MD_FILENAME).read_text(encoding="utf-8") == "# v1"
    assert audit_calls == []


def test_restore_mismatched_if_match_returns_409(personal_client):
    client, d, _, audit_calls = personal_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "historical")
    r = client.post(
        "/me/agent/composer/backups/AGENT.md.20260416T100000Z.backup/restore",
        headers={"If-Match": "not-the-current-etag"},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["current_etag"]
    assert (d / AGENT_MD_FILENAME).read_text(encoding="utf-8") == "# v1"
    assert audit_calls == []


def test_restore_happy_path(personal_client):
    client, d, cache_calls, audit_calls = personal_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "# restored content")
    etag = _current_etag(client)
    # AGENT.md.backup existiert vorher nicht
    assert not (d / AGENT_MD_BACKUP).exists()

    r = client.post(
        "/me/agent/composer/backups/AGENT.md.20260416T100000Z.backup/restore",
        headers={"If-Match": etag},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"] is True
    assert body["from_backup"] == "AGENT.md.20260416T100000Z.backup"
    assert body["pre_restore_snapshot"] is not None
    assert body["pre_restore_snapshot"].endswith(".backup")
    assert body["etag"] and body["etag"] != etag

    # AGENT.md = restored content (byte-treu)
    assert (d / AGENT_MD_FILENAME).read_text(encoding="utf-8") == "# restored content"
    # AGENT.md.backup = Pre-Restore-Content (rolling latest-Semantik)
    assert (d / AGENT_MD_BACKUP).read_text(encoding="utf-8") == "# v1"
    # Pre-Restore-Snapshot existiert und enthält den alten AGENT.md-Inhalt
    snap = d / body["pre_restore_snapshot"]
    assert snap.exists()
    assert snap.read_text(encoding="utf-8") == "# v1"

    # Side-Effects
    assert cache_calls == ["personal_alice"]
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "personal_agent.composer_restore"
    assert audit_calls[0]["target"] == "personal_alice"
    assert audit_calls[0]["details"]["from_backup"] == "AGENT.md.20260416T100000Z.backup"


def test_restore_preserves_siblings(personal_client):
    client, d, _, _ = personal_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "# restore")
    etag = _current_etag(client)

    # Hash aller Siblings vor dem Restore
    siblings = {
        "agent.yaml":        (d / "agent.yaml").read_bytes(),
        "soul.md":           (d / "soul.md").read_bytes(),
        "skills/foo.md":     (d / "skills" / "foo.md").read_bytes(),
        "memory/bar.md":     (d / "memory" / "bar.md").read_bytes(),
    }

    r = client.post(
        "/me/agent/composer/backups/AGENT.md.20260416T100000Z.backup/restore",
        headers={"If-Match": etag},
    )
    assert r.status_code == 200

    # Alle Siblings unverändert.
    assert (d / "agent.yaml").read_bytes() == siblings["agent.yaml"]
    assert (d / "soul.md").read_bytes()    == siblings["soul.md"]
    assert (d / "skills" / "foo.md").read_bytes() == siblings["skills/foo.md"]
    assert (d / "memory" / "bar.md").read_bytes() == siblings["memory/bar.md"]


@pytest.mark.parametrize("bad", [
    "../etc/passwd",
    "AGENT.md",
    "random.md",
    "AGENT.md.evil",
])
def test_restore_invalid_name_rejected_400(personal_client, bad):
    client, d, cache_calls, audit_calls = personal_client
    etag = _current_etag(client)
    r = client.post(
        f"/me/agent/composer/backups/{bad}/restore",
        headers={"If-Match": etag},
    )
    assert r.status_code in (400, 404, 405)
    assert (d / AGENT_MD_FILENAME).read_text(encoding="utf-8") == "# v1"
    assert cache_calls == []
    assert audit_calls == []


def test_restore_missing_backup_404(personal_client):
    client, _, _, _ = personal_client
    etag = _current_etag(client)
    r = client.post(
        "/me/agent/composer/backups/AGENT.md.20260416T235959Z.backup/restore",
        headers={"If-Match": etag},
    )
    assert r.status_code == 404


def test_restore_etag_changes_after_success(personal_client):
    client, d, _, _ = personal_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "# changed")
    etag_before = _current_etag(client)
    r = client.post(
        "/me/agent/composer/backups/AGENT.md.20260416T100000Z.backup/restore",
        headers={"If-Match": etag_before},
    )
    assert r.status_code == 200
    etag_after = r.json()["etag"]
    assert etag_after != etag_before
    # Profile-Endpoint liefert denselben neuen ETag.
    assert _current_etag(client) == etag_after


# ─────────────────────────────────────────────────────────────── Scope-Matrix

def test_admin_scope_list_and_restore(admin_client):
    client, d, cache_calls, audit_calls = admin_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "# admin-restore")

    r = client.get("/admin/agents/ops_bot/composer/backups")
    assert r.status_code == 200
    assert r.json()["count"] == 1

    etag = client.get("/admin/agents/ops_bot/composer/profile").json()["etag"]
    r = client.post(
        "/admin/agents/ops_bot/composer/backups/AGENT.md.20260416T100000Z.backup/restore",
        headers={"If-Match": etag},
    )
    assert r.status_code == 200
    assert (d / AGENT_MD_FILENAME).read_text(encoding="utf-8") == "# admin-restore"
    assert cache_calls == ["ops_bot"]
    assert audit_calls[-1]["action"] == "admin.agent.composer_restore"
    assert audit_calls[-1]["target"] == "ops_bot"


def test_admin_restore_missing_if_match_428(admin_client):
    client, d, _, _ = admin_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "x")
    r = client.post(
        "/admin/agents/ops_bot/composer/backups/AGENT.md.20260416T100000Z.backup/restore",
    )
    assert r.status_code == 428


def test_project_scope_list_and_restore(project_client):
    client, d, cache_calls, audit_calls = project_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "# project-restore")

    r = client.get("/projects/personal_alice/composer/backups")
    assert r.status_code == 200
    assert r.json()["count"] == 1

    etag = client.get("/projects/personal_alice/composer/profile").json()["etag"]
    r = client.post(
        "/projects/personal_alice/composer/backups/AGENT.md.20260416T100000Z.backup/restore",
        headers={"If-Match": etag},
    )
    assert r.status_code == 200
    assert (d / AGENT_MD_FILENAME).read_text(encoding="utf-8") == "# project-restore"
    assert cache_calls == ["personal_alice"]
    assert audit_calls[-1]["action"] == "project.composer_restore"


def test_project_restore_missing_if_match_428(project_client):
    client, d, _, _ = project_client
    _write_backup(d, "AGENT.md.20260416T100000Z.backup", "x")
    r = client.post(
        "/projects/personal_alice/composer/backups/AGENT.md.20260416T100000Z.backup/restore",
    )
    assert r.status_code == 428
