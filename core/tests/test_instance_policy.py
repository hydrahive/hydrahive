"""#711: Instance-Policy als optionale Admin-Schicht."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hydrahive_core import orchestrator_context as oc
from hydrahive_core.settings import HydraHiveSettings


@pytest.fixture(autouse=True)
def _clear_prompt_caches():
    oc._STATIC_PROMPT_CACHE.clear()
    oc._SEGMENT_HASHES.clear()
    yield
    oc._STATIC_PROMPT_CACHE.clear()
    oc._SEGMENT_HASHES.clear()


@pytest.fixture
def boss_cfg(tmp_path: Path) -> MagicMock:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    cfg = MagicMock()
    cfg.id = "test-agent"
    cfg.identity = "TestAgent"
    cfg.agent_dir = agent_dir
    cfg.soul = None
    cfg.sources = []
    cfg.project_dir = None
    cfg.llm = None
    return cfg


def _set_test_etc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    etc_dir = tmp_path / "etc"
    etc_dir.mkdir()
    monkeypatch.setattr(oc.settings, "etc_dir", etc_dir)
    return etc_dir


def test_settings_instance_policy_path(tmp_path: Path):
    settings = HydraHiveSettings(etc_dir=tmp_path)
    assert settings.instance_policy == tmp_path / "instance_policy.md"


def test_load_instance_policy_missing_file_returns_empty(monkeypatch, tmp_path):
    _set_test_etc(monkeypatch, tmp_path)
    assert oc._load_instance_policy_text() == ""


def test_load_instance_policy_empty_file_returns_empty(monkeypatch, tmp_path):
    etc_dir = _set_test_etc(monkeypatch, tmp_path)
    (etc_dir / "instance_policy.md").write_text("  \n\n", encoding="utf-8")
    assert oc._load_instance_policy_text() == ""


def test_load_instance_policy_reads_content(monkeypatch, tmp_path):
    etc_dir = _set_test_etc(monkeypatch, tmp_path)
    (etc_dir / "instance_policy.md").write_text("## Instanzregel\n\nKein sudo.", encoding="utf-8")
    assert oc._load_instance_policy_text() == "## Instanzregel\n\nKein sudo."


@pytest.mark.asyncio
async def test_build_system_prompt_appends_instance_policy_after_core(monkeypatch, tmp_path, boss_cfg):
    etc_dir = _set_test_etc(monkeypatch, tmp_path)
    (etc_dir / "instance_policy.md").write_text(
        "## Instance Policy\n\nKeine externen Downloads.",
        encoding="utf-8",
    )

    static, dynamic = await oc.build_system_prompt(boss_cfg, "hallo")

    assert dynamic == "" or "Instance Policy" not in dynamic
    core_idx = static.index("# Core-Policy für HydraHive-Agenten")
    instance_idx = static.index("## Instance Policy")
    assert core_idx < instance_idx
    assert "Keine externen Downloads." in static


@pytest.mark.asyncio
async def test_build_system_prompt_missing_instance_policy_keeps_core(monkeypatch, tmp_path, boss_cfg):
    _set_test_etc(monkeypatch, tmp_path)

    static, _dynamic = await oc.build_system_prompt(boss_cfg, "hallo")

    assert "# Core-Policy für HydraHive-Agenten" in static
    assert "## Instance Policy" not in static


def test_prompt_cache_hash_changes_on_instance_policy_change(monkeypatch, tmp_path, boss_cfg):
    etc_dir = _set_test_etc(monkeypatch, tmp_path)

    h1 = oc._prompt_cache_hash(boss_cfg.agent_dir, "full")
    policy = etc_dir / "instance_policy.md"
    policy.write_text("Regel A", encoding="utf-8")
    h2 = oc._prompt_cache_hash(boss_cfg.agent_dir, "full")

    assert h1 != h2

    policy.write_text("Regel B", encoding="utf-8")
    new_mtime = policy.stat().st_mtime + 5.0
    os.utime(policy, (new_mtime, new_mtime))

    h3 = oc._prompt_cache_hash(boss_cfg.agent_dir, "full")
    assert h2 != h3


@pytest.mark.asyncio
async def test_cache_invalidated_when_instance_policy_changes(monkeypatch, tmp_path, boss_cfg):
    etc_dir = _set_test_etc(monkeypatch, tmp_path)
    policy = etc_dir / "instance_policy.md"
    policy.write_text("Regel v1", encoding="utf-8")

    static1, _ = await oc.build_system_prompt(boss_cfg, "test")
    assert "Regel v1" in static1

    policy.write_text("Regel v2", encoding="utf-8")
    new_mtime = policy.stat().st_mtime + 5.0
    os.utime(policy, (new_mtime, new_mtime))

    static2, _ = await oc.build_system_prompt(boss_cfg, "test")
    assert "Regel v2" in static2
    assert static1 != static2
