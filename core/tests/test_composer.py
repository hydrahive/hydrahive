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


# ===========================================================================
# Phase 1c — agent_profile.yaml, Presets, Warnings
# ===========================================================================


import yaml as _yaml
from hydrahive_core.composer_engine import (
    AgentProfile,
    evaluate_warnings,
    list_presets,
    preset_selection,
    render_from_profile,
    save_blocked,
)


def test_list_presets_contains_both_phase1c_presets():
    presets = list_presets()
    ids = {p["id"] for p in presets}
    assert ids == {"read_only_auditor", "trusted_admin"}
    for p in presets:
        assert p["selected"], f"Preset {p['id']} hat leere Selection"


def test_preset_selection_is_subset_of_known_blocks():
    from hydrahive_core.composer_engine import known_block_ids
    known = known_block_ids()
    for pid in ("read_only_auditor", "trusted_admin"):
        for bid in preset_selection(pid):
            assert bid in known, f"Preset {pid} referenziert unbekannten Block {bid}"


def test_evaluate_warnings_clean_on_full_preset():
    sel = preset_selection("read_only_auditor")
    warnings = evaluate_warnings(sel, "read_only_auditor")
    assert not any(w["severity"] == "error" for w in warnings)


def test_profile_yaml_is_written_and_readable(client_factory):
    client, personal_dir, _, _ = client_factory()
    r = client.put("/me/agent/composer", json={"selected": ["work_style.precise"]})
    assert r.status_code == 200
    profile_path = personal_dir / "agent_profile.yaml"
    assert profile_path.exists()
    data = _yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["selected"] == ["work_style.precise"]
    assert data["updated_at"]  # not None/empty
    assert data["preset"] is None


def test_agent_md_rendered_from_profile_matches_direct_render(client_factory):
    client, personal_dir, _, _ = client_factory()
    sel = ["work_style.precise", "comm.concise"]
    r = client.put("/me/agent/composer", json={"selected": sel})
    assert r.status_code == 200
    md_written = (personal_dir / "AGENT.md").read_text(encoding="utf-8")
    profile_data = _yaml.safe_load((personal_dir / "agent_profile.yaml").read_text(encoding="utf-8"))
    prof = AgentProfile(**profile_data)
    assert render_from_profile(prof) == md_written


def test_backup_still_created(client_factory):
    client, personal_dir, _, _ = client_factory()
    (personal_dir / "AGENT.md").write_text("old\n", encoding="utf-8")
    r = client.put("/me/agent/composer", json={"selected": ["work_style.precise"]})
    assert r.status_code == 200
    assert (personal_dir / "AGENT.md.backup").read_text(encoding="utf-8") == "old\n"


def test_preset_applies_block_set(client_factory):
    client, personal_dir, _, _ = client_factory()
    r = client.put("/me/agent/composer", json={
        "selected": preset_selection("trusted_admin"),
        "preset": "trusted_admin",
    })
    assert r.status_code == 200
    data = _yaml.safe_load((personal_dir / "agent_profile.yaml").read_text(encoding="utf-8"))
    assert data["preset"] == "trusted_admin"
    assert set(data["selected"]) == set(preset_selection("trusted_admin"))


def test_preset_drift_warning_triggered(client_factory):
    client, _, _, _ = client_factory()
    base = list(preset_selection("trusted_admin"))
    base.pop()  # entferne einen Block → drift
    r = client.post("/me/agent/composer/preview", json={
        "selected": base,
        "preset": "trusted_admin",
    })
    assert r.status_code == 200
    warnings = r.json()["warnings"]
    rule_ids = [w["rule"] for w in warnings]
    assert "preset_drift" in rule_ids


def test_read_only_incomplete_warning(client_factory):
    client, _, _, _ = client_factory()
    # Nur einer der beiden Safety-Blocks
    r = client.post("/me/agent/composer/preview", json={
        "selected": ["safety.read_only_default"],
    })
    assert r.status_code == 200
    rules = [w["rule"] for w in r.json()["warnings"]]
    assert "read_only_incomplete" in rules


def test_deploy_discipline_partial_warning(client_factory):
    client, _, _, _ = client_factory()
    r = client.post("/me/agent/composer/preview", json={
        "selected": ["git.verify_before_done"],
    })
    rules = [w["rule"] for w in r.json()["warnings"]]
    assert "deploy_discipline_partial" in rules


def test_load_profile_after_save_round_trip(client_factory):
    client, _, _, _ = client_factory()
    sel = ["work_style.precise", "comm.concise"]
    r = client.put("/me/agent/composer", json={"selected": sel, "preset": None})
    assert r.status_code == 200
    r2 = client.get("/me/agent/composer/profile")
    assert r2.status_code == 200
    body = r2.json()
    assert body["selected"] == sel
    assert body["preset"] is None
    assert body["schema_version"] == 1
    assert body["agent_md_exists"] is True
    assert body["agent_md_mtime_matches"] is True


def test_missing_profile_yaml_returns_empty_selection(client_factory):
    client, personal_dir, _, _ = client_factory()
    # Kein Save — profile.yaml existiert nicht
    assert not (personal_dir / "agent_profile.yaml").exists()
    r = client.get("/me/agent/composer/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["selected"] == []
    assert body["preset"] is None
    assert body["agent_md_exists"] is False


def test_corrupt_profile_yaml_returns_empty_profile_with_warning(client_factory):
    client, personal_dir, _, _ = client_factory()
    (personal_dir / "agent_profile.yaml").write_text(":\n  bogus: - [not yaml", encoding="utf-8")
    r = client.get("/me/agent/composer/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["selected"] == []
    rule_ids = [w["rule"] for w in body["warnings"]]
    assert "profile_yaml_corrupt" in rule_ids


def test_unknown_preset_rejected(client_factory):
    client, personal_dir, _, _ = client_factory()
    r = client.put("/me/agent/composer", json={
        "selected": ["work_style.precise"],
        "preset": "nope_preset",
    })
    assert r.status_code == 400
    assert not (personal_dir / "agent_profile.yaml").exists()


def test_save_blocked_on_error_severity(client_factory):
    client, personal_dir, _, _ = client_factory()
    # preset gesetzt, selected leer → empty_selection_with_preset = error
    r = client.put("/me/agent/composer", json={
        "selected": [],
        "preset": "read_only_auditor",
    })
    assert r.status_code in (400, 422)
    assert not (personal_dir / "AGENT.md").exists()
    assert not (personal_dir / "agent_profile.yaml").exists()


def test_save_blocked_is_422_when_conflict_not_400(client_factory):
    client, _, _, _ = client_factory()
    r = client.put("/me/agent/composer", json={
        "selected": [],
        "preset": "read_only_auditor",
    })
    assert r.status_code == 422
    body = r.json()
    assert "warnings" in body["detail"]


def test_agent_md_mtime_matches_false_on_external_edit(client_factory):
    import time
    client, personal_dir, _, _ = client_factory()
    client.put("/me/agent/composer", json={"selected": ["work_style.precise"]})
    time.sleep(0.1)
    # Extern AGENT.md ändern
    agent_md = personal_dir / "AGENT.md"
    import os
    future = time.time() + 60
    os.utime(agent_md, (future, future))
    r = client.get("/me/agent/composer/profile")
    assert r.status_code == 200
    assert r.json()["agent_md_mtime_matches"] is False
