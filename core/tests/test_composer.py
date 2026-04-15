"""Composer-Engine + Endpoint-Tests (#645 Phase 1b)."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from hydrahive_core.composer_engine import (
    BLOCK_CATALOG,
    known_block_ids,
    list_blocks,
    render_agent_md,
)
from hydrahive_core.router_composer import register_composer_routes


# ===========================================================================
# Engine-Tests
# ===========================================================================


def test_catalog_not_empty_and_ids_unique():
    ids = [b.id for cat in BLOCK_CATALOG for b in cat.blocks]
    assert len(ids) >= 10, "Katalog zu klein"
    assert len(ids) == len(set(ids)), f"Doppelte Block-IDs: {ids}"
    assert all("." in bid for bid in ids), "IDs folgen dem Muster <cat>.<slug>"


def test_list_blocks_matches_catalog():
    api = list_blocks()
    assert len(api) == len(BLOCK_CATALOG)
    for cat_api, cat_def in zip(api, BLOCK_CATALOG):
        assert cat_api["id"] == cat_def.id
        assert len(cat_api["blocks"]) == len(cat_def.blocks)


def test_render_empty_selection_returns_empty_string():
    assert render_agent_md([]) == ""
    assert render_agent_md(["unknown.block", "also.unknown"]) == ""


def test_render_known_selection_contains_block_markdown():
    md = render_agent_md(["work_style.precise", "comm.concise"])
    assert "# Persönliches Agent-Profil" in md
    assert "## Arbeitsstil" in md
    assert "## Kommunikation" in md
    assert "kleinen, fokussierten Schritten" in md
    assert "auf den Punkt" in md


def test_render_order_is_catalog_order_not_input_order():
    # comm kommt im Katalog nach work_style — Output muss trotzdem work_style zuerst haben
    md_a = render_agent_md(["comm.concise", "work_style.precise"])
    md_b = render_agent_md(["work_style.precise", "comm.concise"])
    assert md_a == md_b
    assert md_a.index("## Arbeitsstil") < md_a.index("## Kommunikation")


def test_render_unknown_ids_silently_ignored():
    md = render_agent_md(["work_style.precise", "bogus.block"])
    assert "kleinen, fokussierten Schritten" in md
    assert "bogus" not in md


# ===========================================================================
# Endpoint-Tests
# ===========================================================================


@pytest.fixture
def client_factory(tmp_path):
    def _make(username: str = "alice"):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        personal_dir = agents_dir / f"personal_{username}"
        personal_dir.mkdir(parents=True, exist_ok=True)
        (personal_dir / "agent.yaml").write_text("id: personal_alice\ntools: [file_read]\n", encoding="utf-8")
        (personal_dir / "soul.md").write_text("old soul", encoding="utf-8")

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
            audit_log=lambda *a, **kw: audit_calls.append({"args": a, "kwargs": kw}),
        )
        app.include_router(auth_router)
        return TestClient(app), personal_dir, cache_calls, audit_calls

    return _make


def test_get_blocks_returns_catalog(client_factory):
    client, _, _, _ = client_factory()
    r = client.get("/me/agent/composer/blocks")
    assert r.status_code == 200
    data = r.json()
    assert "categories" in data
    assert len(data["categories"]) >= 5
    assert any(c["id"] == "work_style" for c in data["categories"])


def test_preview_returns_markdown(client_factory):
    client, _, _, _ = client_factory()
    r = client.post("/me/agent/composer/preview", json={"selected": ["work_style.precise"]})
    assert r.status_code == 200
    assert "kleinen, fokussierten Schritten" in r.json()["markdown"]


def test_preview_does_not_write_files(client_factory):
    client, personal_dir, _, _ = client_factory()
    r = client.post("/me/agent/composer/preview", json={"selected": ["work_style.precise"]})
    assert r.status_code == 200
    assert not (personal_dir / "AGENT.md").exists()


def test_save_writes_agent_md_and_invalidates_cache(client_factory):
    client, personal_dir, cache_calls, audit_calls = client_factory()
    r = client.put("/me/agent/composer", json={"selected": ["work_style.precise", "safety.prod_hands_off"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] is True
    assert body["agent_id"] == "personal_alice"
    assert body["backup_created"] is False  # Erstes Schreiben — kein Vorgänger
    agent_md = personal_dir / "AGENT.md"
    assert agent_md.exists()
    text = agent_md.read_text(encoding="utf-8")
    assert "# Persönliches Agent-Profil" in text
    assert "Produktivsysteme" in text
    assert cache_calls == ["personal_alice"]
    assert len(audit_calls) == 1


def test_save_creates_backup_on_overwrite(client_factory):
    client, personal_dir, _, _ = client_factory()
    (personal_dir / "AGENT.md").write_text("handgeschrieben\n", encoding="utf-8")

    r = client.put("/me/agent/composer", json={"selected": ["work_style.precise"]})
    assert r.status_code == 200
    assert r.json()["backup_created"] is True
    assert (personal_dir / "AGENT.md.backup").read_text(encoding="utf-8") == "handgeschrieben\n"
    assert "kleinen, fokussierten Schritten" in (personal_dir / "AGENT.md").read_text(encoding="utf-8")


def test_save_does_not_touch_soul_or_yaml_or_tools(client_factory):
    client, personal_dir, _, _ = client_factory()
    soul_before = (personal_dir / "soul.md").read_text(encoding="utf-8")
    yaml_before = (personal_dir / "agent.yaml").read_text(encoding="utf-8")
    soul_mtime = (personal_dir / "soul.md").stat().st_mtime_ns
    yaml_mtime = (personal_dir / "agent.yaml").stat().st_mtime_ns

    r = client.put("/me/agent/composer", json={"selected": ["work_style.precise"]})
    assert r.status_code == 200

    assert (personal_dir / "soul.md").read_text(encoding="utf-8") == soul_before
    assert (personal_dir / "agent.yaml").read_text(encoding="utf-8") == yaml_before
    assert (personal_dir / "soul.md").stat().st_mtime_ns == soul_mtime
    assert (personal_dir / "agent.yaml").stat().st_mtime_ns == yaml_mtime
    # tools[] explizit noch enthalten
    assert "file_read" in (personal_dir / "agent.yaml").read_text(encoding="utf-8")


def test_save_rejects_unknown_block_ids(client_factory):
    client, personal_dir, _, _ = client_factory()
    r = client.put("/me/agent/composer", json={"selected": ["work_style.precise", "nope.xxx"]})
    assert r.status_code == 400
    assert "Unbekannte" in r.json()["detail"]
    assert not (personal_dir / "AGENT.md").exists()


def test_save_rejects_empty_selection(client_factory):
    client, personal_dir, _, _ = client_factory()
    r = client.put("/me/agent/composer", json={"selected": []})
    assert r.status_code == 400
    assert not (personal_dir / "AGENT.md").exists()
