"""Admin-Composer-Tests (#645 Phase 1d).

Deckt `/admin/agents/{agent_id}/composer/*` ab — inkl. Rechte-Gate,
Path-Traversal-Schutz, personal_*-Ablehnung, und dass soul.md/agent.yaml
unverändert bleiben.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
import yaml as _yaml
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from hydrahive_core.router_composer import register_admin_composer_routes


def _make_app(username: str, role: str, tmp_path: Path, *, agent_ids: list[str]):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for aid in agent_ids:
        d = agents_dir / aid
        d.mkdir(parents=True, exist_ok=True)
        (d / "agent.yaml").write_text(
            f"id: {aid}\ntools: [file_read]\nexecution_modes: [chat]\n", encoding="utf-8"
        )
        (d / "soul.md").write_text("legacy soul content", encoding="utf-8")

    cache_calls: list[str] = []
    audit_calls: list[dict] = []

    def _require_admin():
        if role != "admin":
            raise HTTPException(403, "Keine Berechtigung")
        return (username, role)

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
    return TestClient(app), agents_dir, cache_calls, audit_calls


@pytest.fixture
def admin_client(tmp_path):
    return _make_app("alice_admin", "admin", tmp_path, agent_ids=["ops_bot"])


@pytest.fixture
def user_client(tmp_path):
    return _make_app("bob", "user", tmp_path, agent_ids=["ops_bot"])


def test_non_admin_gets_403_on_blocks(user_client):
    client, _, _, _ = user_client
    r = client.get("/admin/agents/ops_bot/composer/blocks")
    assert r.status_code == 403


def test_non_admin_gets_403_on_save(user_client):
    client, agents_dir, _, _ = user_client
    r = client.put("/admin/agents/ops_bot/composer", json={"selected": ["work_style.precise"]})
    assert r.status_code == 403
    assert not (agents_dir / "ops_bot" / "AGENT.md").exists()


def test_unknown_agent_id_returns_404(admin_client):
    client, _, _, _ = admin_client
    r = client.get("/admin/agents/does_not_exist/composer/profile")
    assert r.status_code == 404


def test_personal_agent_rejected_with_403(admin_client, tmp_path):
    # Lege personal_alice an, dann versuche Admin-Composer-Save
    client, agents_dir, _, _ = admin_client
    personal_dir = agents_dir / "personal_alice"
    personal_dir.mkdir()
    (personal_dir / "agent.yaml").write_text("id: personal_alice\n", encoding="utf-8")

    r = client.get("/admin/agents/personal_alice/composer/profile")
    assert r.status_code == 403

    r2 = client.put(
        "/admin/agents/personal_alice/composer",
        json={"selected": ["work_style.precise"]},
    )
    assert r2.status_code == 403
    assert not (personal_dir / "AGENT.md").exists()


@pytest.mark.parametrize("bad", ["../etc", "ops/bot", "ops\\bot", "", ".", "..", "x/../y"])
def test_path_traversal_rejected(admin_client, bad):
    client, agents_dir, _, _ = admin_client
    r = client.get(f"/admin/agents/{bad}/composer/profile")
    # FastAPI normalisiert `/` ggf. — in jedem Fall darf kein Save passieren,
    # und der Status muss ≥ 400 sein.
    assert r.status_code >= 400
    # Keine Datei außerhalb agents_dir erzeugt
    assert not any(agents_dir.parent.glob("AGENT.md"))


def test_admin_save_writes_agent_md_and_profile_yaml(admin_client):
    client, agents_dir, cache_calls, audit_calls = admin_client
    r = client.put(
        "/admin/agents/ops_bot/composer",
        json={"selected": ["work_style.precise", "comm.concise"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] is True
    assert body["agent_id"] == "ops_bot"
    assert body["backup_created"] is False

    agent_dir = agents_dir / "ops_bot"
    assert (agent_dir / "AGENT.md").exists()
    assert "kleinen, fokussierten Schritten" in (agent_dir / "AGENT.md").read_text(encoding="utf-8")

    profile = _yaml.safe_load((agent_dir / "agent_profile.yaml").read_text(encoding="utf-8"))
    assert profile["schema_version"] == 1
    assert set(profile["selected"]) == {"work_style.precise", "comm.concise"}

    assert cache_calls == ["ops_bot"]
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "admin.agent.composer_save"
    assert audit_calls[0]["target"] == "ops_bot"
    assert audit_calls[0]["user"] == "alice_admin"


def test_admin_save_creates_backup_on_overwrite(admin_client):
    client, agents_dir, _, _ = admin_client
    existing = agents_dir / "ops_bot" / "AGENT.md"
    existing.write_text("handgeschrieben\n", encoding="utf-8")

    r = client.put(
        "/admin/agents/ops_bot/composer",
        json={"selected": ["work_style.precise"]},
    )
    assert r.status_code == 200
    assert r.json()["backup_created"] is True
    backup = agents_dir / "ops_bot" / "AGENT.md.backup"
    assert backup.read_text(encoding="utf-8") == "handgeschrieben\n"


def test_admin_save_does_not_touch_agent_yaml_or_soul(admin_client):
    client, agents_dir, _, _ = admin_client
    agent_dir = agents_dir / "ops_bot"
    agent_yaml_before = (agent_dir / "agent.yaml").read_text(encoding="utf-8")
    soul_before = (agent_dir / "soul.md").read_text(encoding="utf-8")
    yaml_mtime = (agent_dir / "agent.yaml").stat().st_mtime_ns
    soul_mtime = (agent_dir / "soul.md").stat().st_mtime_ns

    r = client.put(
        "/admin/agents/ops_bot/composer",
        json={"selected": ["work_style.precise"]},
    )
    assert r.status_code == 200

    assert (agent_dir / "agent.yaml").read_text(encoding="utf-8") == agent_yaml_before
    assert (agent_dir / "soul.md").read_text(encoding="utf-8") == soul_before
    assert (agent_dir / "agent.yaml").stat().st_mtime_ns == yaml_mtime
    assert (agent_dir / "soul.md").stat().st_mtime_ns == soul_mtime
    # execution_modes + tools[] noch drin
    raw = (agent_dir / "agent.yaml").read_text(encoding="utf-8")
    assert "file_read" in raw
    assert "execution_modes" in raw


def test_admin_profile_roundtrip(admin_client):
    client, _, _, _ = admin_client
    sel = ["work_style.precise", "safety.prod_hands_off", "safety.read_only_default"]
    r = client.put("/admin/agents/ops_bot/composer", json={"selected": sel})
    assert r.status_code == 200
    r2 = client.get("/admin/agents/ops_bot/composer/profile")
    assert r2.status_code == 200
    body = r2.json()
    assert set(body["selected"]) == set(sel)
    assert body["agent_md_exists"] is True


def test_admin_preview_does_not_write(admin_client):
    client, agents_dir, _, _ = admin_client
    r = client.post(
        "/admin/agents/ops_bot/composer/preview",
        json={"selected": ["work_style.precise"]},
    )
    assert r.status_code == 200
    assert "kleinen, fokussierten Schritten" in r.json()["markdown"]
    assert not (agents_dir / "ops_bot" / "AGENT.md").exists()


def test_admin_save_rejects_unknown_block(admin_client):
    client, agents_dir, _, _ = admin_client
    r = client.put(
        "/admin/agents/ops_bot/composer",
        json={"selected": ["work_style.precise", "nope.xxx"]},
    )
    assert r.status_code == 400
    assert not (agents_dir / "ops_bot" / "AGENT.md").exists()


def test_admin_save_blocked_on_error_severity(admin_client):
    client, agents_dir, _, _ = admin_client
    r = client.put(
        "/admin/agents/ops_bot/composer",
        json={"selected": [], "preset": "read_only_auditor"},
    )
    assert r.status_code == 422
    assert not (agents_dir / "ops_bot" / "AGENT.md").exists()


def test_admin_presets_and_blocks_endpoints(admin_client):
    client, _, _, _ = admin_client
    r = client.get("/admin/agents/ops_bot/composer/presets")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()["presets"]}
    # #649: Katalog erweitert — Phase-1c-Presets bleiben Teil der Liste,
    # exakte Gleichheit ist nicht mehr gefordert.
    assert {"read_only_auditor", "trusted_admin"}.issubset(ids)

    r2 = client.get("/admin/agents/ops_bot/composer/blocks")
    assert r2.status_code == 200
    assert len(r2.json()["categories"]) >= 5
