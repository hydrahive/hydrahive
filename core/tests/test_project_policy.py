"""#712: Project-Policy als dritte Policy-Schicht."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hydrahive_core import orchestrator_context as oc


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
    project_dir = tmp_path / "project"
    agent_dir.mkdir()
    project_dir.mkdir()
    cfg = MagicMock()
    cfg.id = "test-agent"
    cfg.identity = "TestAgent"
    cfg.agent_dir = agent_dir
    cfg.project_dir = project_dir
    cfg.soul = None
    cfg.sources = []
    cfg.llm = None
    return cfg


def _set_test_etc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    etc_dir = tmp_path / "etc"
    etc_dir.mkdir()
    monkeypatch.setattr(oc.settings, "etc_dir", etc_dir)
    return etc_dir


def test_load_project_policy_missing_file_returns_empty(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    assert oc._load_project_policy_text(project_dir) == ""


def test_load_project_policy_none_returns_empty():
    assert oc._load_project_policy_text(None) == ""


def test_load_project_policy_empty_file_returns_empty(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "policy.md").write_text("  \n\n", encoding="utf-8")
    assert oc._load_project_policy_text(project_dir) == ""


def test_load_project_policy_reads_content(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "policy.md").write_text("## Projektregel\n\nNur lokal testen.", encoding="utf-8")
    assert oc._load_project_policy_text(project_dir) == "## Projektregel\n\nNur lokal testen."


def test_load_project_policy_unreadable_path_returns_empty(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "policy.md").mkdir()
    assert oc._load_project_policy_text(project_dir) == ""


@pytest.mark.asyncio
async def test_build_system_prompt_missing_project_policy_keeps_core(monkeypatch, tmp_path, boss_cfg):
    _set_test_etc(monkeypatch, tmp_path)

    static, _dynamic = await oc.build_system_prompt(boss_cfg, "hallo")

    assert "# Core-Policy für HydraHive-Agenten" in static
    assert "## Project Policy" not in static


@pytest.mark.asyncio
async def test_build_system_prompt_empty_project_policy_ignored(monkeypatch, tmp_path, boss_cfg):
    _set_test_etc(monkeypatch, tmp_path)
    (boss_cfg.project_dir / "policy.md").write_text("  \n", encoding="utf-8")

    static, _dynamic = await oc.build_system_prompt(boss_cfg, "hallo")

    assert "# Core-Policy für HydraHive-Agenten" in static
    assert "## Project Policy" not in static


@pytest.mark.asyncio
async def test_build_system_prompt_appends_project_policy_after_core_and_instance(
    monkeypatch,
    tmp_path,
    boss_cfg,
):
    etc_dir = _set_test_etc(monkeypatch, tmp_path)
    (etc_dir / "instance_policy.md").write_text(
        "## Instance Policy\n\nInstanzregel.",
        encoding="utf-8",
    )
    (boss_cfg.project_dir / "policy.md").write_text(
        "## Project Policy\n\nProjektregel.",
        encoding="utf-8",
    )

    static, dynamic = await oc.build_system_prompt(boss_cfg, "hallo")

    assert dynamic == "" or "Project Policy" not in dynamic
    core_idx = static.index("# Core-Policy für HydraHive-Agenten")
    instance_idx = static.index("## Instance Policy")
    project_idx = static.index("## Project Policy")
    assert core_idx < instance_idx < project_idx
    assert "Projektregel." in static


def test_prompt_cache_hash_changes_on_project_policy_change(monkeypatch, tmp_path, boss_cfg):
    _set_test_etc(monkeypatch, tmp_path)

    h1 = oc._prompt_cache_hash(boss_cfg.agent_dir, "full", project_dir=boss_cfg.project_dir)
    policy = boss_cfg.project_dir / "policy.md"
    policy.write_text("Regel A", encoding="utf-8")
    h2 = oc._prompt_cache_hash(boss_cfg.agent_dir, "full", project_dir=boss_cfg.project_dir)

    assert h1 != h2

    policy.write_text("Regel B", encoding="utf-8")
    new_mtime = policy.stat().st_mtime + 5.0
    os.utime(policy, (new_mtime, new_mtime))

    h3 = oc._prompt_cache_hash(boss_cfg.agent_dir, "full", project_dir=boss_cfg.project_dir)
    assert h2 != h3


@pytest.mark.asyncio
async def test_cache_invalidated_when_project_policy_changes(monkeypatch, tmp_path, boss_cfg):
    _set_test_etc(monkeypatch, tmp_path)
    policy = boss_cfg.project_dir / "policy.md"
    policy.write_text("Projektregel v1", encoding="utf-8")

    static1, _ = await oc.build_system_prompt(boss_cfg, "test")
    assert "Projektregel v1" in static1

    policy.write_text("Projektregel v2", encoding="utf-8")
    new_mtime = policy.stat().st_mtime + 5.0
    os.utime(policy, (new_mtime, new_mtime))

    static2, _ = await oc.build_system_prompt(boss_cfg, "test")
    assert "Projektregel v2" in static2
    assert static1 != static2
